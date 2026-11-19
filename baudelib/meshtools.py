"""The mesh's tools, for a Bot's agent (or a chat given them): an MCP server on stdin and stdout (a JSON-RPC message a
line), run on the chat's host. Through that host's process it reaches every member of the mesh, as a head does
(wire.reach: this machine's socket, or through the mesh), so its agent can see the hosts and their models, start a
subagent on any of them with any of their models, and read and write to the other chats there, Bots too. Each is
called by the agent as mcp__mesh__<tool>."""
import base64
import json
import os
import sys
import threading
import time

from baudelib import wire

NAME = "mesh"  # (its tools are mcp__mesh__hosts, ...)
SUBAGENT_MODES = ("auto", "acceptEdits", "default", "plan")  # (a subagent's: Auto at most; its host holds it to these)
READ_ONLY = ("hosts", "chats", "read_chat")  # (looking, never asked about; starting or saying: as the chat's mode is)
WAIT_S, WAIT_MAX_S = 600, 3600  # (a subagent's or a chat's reply waited for this long, by default and at most)
ASK_S = 60  # (a subagent asking a permission no one's there to give: refused after this long)
TEXT_MAX = 20000  # (characters of a reply, or of what a chat said, given back)
BACK_S = 120  # (a host gone from the mesh, restarting say, as a subagent's started or its answer's waited for: waited
# for this long to come back, and tried again)
AGAIN_S = 2  # (tried again this often meanwhile)

SCHEMAS = {
    "hosts": ("The mesh's hosts (this one first): each one's id, name, whether it's reachable now, and the models it "
              "can run (their values are what start_subagent's model takes).", {}, []),
    "start_subagent": ("Start a new chat (a subagent) on a host of the mesh, with one of its models, give it a task "
                       "and, unless wait is false, wait for its answer. It runs as a chat there (listed in Baude), in "
                       "cwd on that host (its home if none's given), in Auto mode unless another's asked for (Auto at "
                       "most); it can't ask you for permissions, so what it does ask is refused after a minute. Its id comes back, to message it again (message_chat).",
                       {"prompt": {"type": "string", "description": "The task, said in full: it doesn't see this chat"},
                        "host": {"type": "string", "description": "A host's id or name (hosts); this one if none"},
                        "model": {"type": "string", "description": "A model's value on that host (hosts)"},
                        "cwd": {"type": "string", "description": "The folder on that host it works in"},
                        "title": {"type": "string", "description": "Its title in the list"},
                        "mode": {"type": "string", "enum": list(SUBAGENT_MODES),
                                 "description": "Its permission mode (default: auto; a model with no modes runs in "
                                                "default)"},
                        "wait": {"type": "boolean", "description": "Wait for its answer (default true)"},
                        "timeout_s": {"type": "integer", "description": f"How long to wait (default {WAIT_S})"}},
                       ["prompt"]),
    "chats": ("The chats on a host (this one if none's given), newest first: each one's id, title, state, and its "
              "Bot's name if it's a Bot.",
              {"host": {"type": "string", "description": "A host's id or name"},
               "bots": {"type": "boolean", "description": "Only Bots"}}, []),
    "read_chat": ("What a chat on a host said last: its last messages, yours and its.",
                  {"session": {"type": "string", "description": "The chat's id (chats, start_subagent)"},
                   "host": {"type": "string", "description": "Its host's id or name; looked for on every host if none"},
                   "last": {"type": "integer", "description": "How many messages (default 6)"}}, ["session"]),
    "message_chat": ("Send a message to another chat or Bot on a host, as its user would, and unless wait is false wait "
                     "for its answer. Only when the user asked for this.",
                     {"session": {"type": "string"},
                      "text": {"type": "string", "description": "What to say, in full: it doesn't see this chat"},
                      "host": {"type": "string", "description": "Its host's id or name; looked for if none"},
                      "wait": {"type": "boolean", "description": "Wait for its answer (default true)"},
                      "timeout_s": {"type": "integer"}}, ["session", "text"]),
}


def _channel(where, timeout=10):
    ch, info = wire.reach(where, timeout=timeout)
    return ch, info or {}


class Mesh:
    """The mesh as this host's process has it (this chat's: me, its session id; socket: that host process's, if it
    isn't this user's usual one)."""

    def __init__(self, me=None, socket=None):
        self.me = me
        self.local = {"via": "unix", "path": socket} if socket else {"via": "local"}
        self.relay = {"path": socket} if socket else {}  # (a member reached through that host process)

    def here(self):
        ch, info = _channel(self.local)
        ch.close()
        return info

    def hosts(self):
        here = self.here()
        out = [{"id": here.get("id"), "name": here.get("name"), "here": True, "reachable": True,
                "models": [m.get("value") for m in here.get("models") or []]}]
        members = (here.get("mesh") or {}).get("members") or []
        found = [{"id": m.get("id"), "name": m.get("name"), "here": False, "reachable": False, "models": []}
                 for m in members]
        gone = [{"id": m.get("id"), "name": m.get("name"), "here": False, "reachable": False, "models": [],
                 "error": "out of reach now (restarting, say)"} for m in (here.get("mesh") or {}).get("gone") or []]

        def look(entry):  # (each at once: one that's down takes its time without holding up the rest)
            try:
                ch, info = _channel({"via": "mesh", "host": entry["id"], **self.relay})
                ch.close()
                entry.update(reachable=True, models=[x.get("value") for x in info.get("models") or []])
            except (OSError, KeyError, ValueError) as e:
                entry["error"] = str(e)
        threads = [threading.Thread(target=look, args=(e,), daemon=True) for e in found if e["id"]]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        return out + found + gone

    def where(self, host):
        """Where a host is (wire.reach's), by its id or name; this one for none."""
        if not host:
            return self.local
        here = self.here()
        if host in (here.get("id"), here.get("name")):
            return self.local
        view = here.get("mesh") or {}
        for m in (view.get("members") or []) + (view.get("gone") or []) + (view.get("links") or []):
            if host in (m.get("id"), m.get("name")) and m.get("id"):  # (one out of reach now: reached once it's back)
                return {"via": "mesh", "host": m["id"], **self.relay}
        raise ValueError(f"no host {host!r} in the mesh: hosts lists them")

    def again(self, where, end):
        """(channel, info) for where, tried every AGAIN_S till end (monotonic): a host restarting is back in a while.
        OSError, the last one's, once it's not been reached by then."""
        while True:
            try:
                return _channel(where)
            except (OSError, ValueError) as e:
                if time.monotonic() + AGAIN_S > end:
                    raise OSError(str(e)) from None
            time.sleep(AGAIN_S)

    def request(self, where, msg, timeout=30):
        ch, _info = _channel(where)
        try:
            ch.sock.settimeout(timeout)
            ch.send({**msg, "req": 1})
            while (r := ch.reply()) is not None:
                if r.get("req") == 1:
                    if r.get("op") == "error":
                        raise ValueError(r.get("error"))
                    return r
            raise OSError("the host went before it answered")
        finally:
            ch.close()

    def sessions(self, where):
        """A host's chats, archived ones too."""
        return self.request(where, {"op": "subscribe", "archived": True})["sessions"]

    def find(self, session, host=None):
        """(where, its listing) for a session, on host or wherever in the mesh it is."""
        places = [self.where(host)] if host else [self.local] + [
            {"via": "mesh", "host": h["id"], **self.relay} for h in self.hosts() if not h["here"] and h["reachable"]]
        for where in places:
            try:
                listed = self.sessions(where)
            except (OSError, ValueError):  # (one that's gone meanwhile: the others looked in still)
                if host:
                    raise
                continue
            for s in listed:
                if s["id"] == session:
                    return where, s
        raise ValueError(f"no chat {session} {'on ' + host if host else 'in the mesh'}: chats lists them")

    def ask(self, where, session, text, wait=True, timeout_s=WAIT_S):
        """text sent to a session as its user's; its answer (the turn's result) waited for, unless not wait."""
        ch, _info = _channel(where)
        try:
            ch.send({"op": "attach", "session": session, "live": True, "answers": False, "req": 1})
            got = ch.reply()
            if not got or got.get("op") != "attached":
                raise ValueError((got or {}).get("error") or "the host didn't answer")
            ch.keep_alive()
            busy = (got.get("session") or {}).get("state") == "working"  # (it's at a turn: this one's answer's next)
            sent = said_to(session, text)
            ch.send(sent)
        except BaseException:
            ch.close()
            raise
        return self._answer(ch, where, session, wait, timeout_s, skip=1 if busy else 0, req=sent, text=text,
                            kind="chat")

    def _answer(self, ch, where, session, wait, timeout_s, skip=0, req=None, text=None, kind="subagent"):
        """The turn's answer, read off ch (attached, live; closed by the end), or as far as it got. skip: the answers
        of turns before it, to pass by; req: the input sent, its error the answer at once; text: what the turn was
        asked, what its answer comes after in the chat. Its host gone meanwhile (restarting, say; ch None: from the
        start): reached again (BACK_S) and the session looked at. Still at it: followed again. What it was asked never
        got to its claude: sent again. Done meanwhile: its answer read from the chat. Cut short at the turn (its claude
        stopped by that restart): said so, interrupted. This chat's host is told what it waits on meanwhile
        (waiting)."""
        if not wait and req is None:
            if ch is not None:
                ch.close()
            return {"session": session, "sent": True}
        end = time.monotonic() + max(5, min(int(timeout_s or WAIT_S), WAIT_MAX_S))
        host = where.get("host") if where.get("via") == "mesh" else None
        said, told = [], wait and self.waiting(host, session, kind, text, end)
        try:
            while True:
                if ch is None:
                    ch, got = self.reattach(where, session)
                    if ch is None:
                        return {"session": session, "answer": None, "lost": True, "said_so_far": cut(said),
                                "error": got}
                    s = got.get("session") or {}
                    working = s.get("state") in ("working", "waiting") and not s.get("cut")
                    if not working or req is not None:
                        chat = self._chat(where, session, text)
                        if text is not None and not chat["asked"] and (s.get("cut") or req is not None):
                            # (it never got to its claude, as its host stopped: sent again)
                            req, skip, said = said_to(session, text), 1 if working else 0, []
                            ch.send(req)
                        elif s.get("cut"):
                            return {"session": session, "host": host, "answer": None,
                                    "said_so_far": cut([chat["said"]]),
                                    "interrupted": "Its host process restarted as it worked, which stopped it at this "
                                                   "turn. To carry it on, message_chat it (this session); read_chat "
                                                   "shows how far it got."}
                        elif not working:  # (its turn ended while its host was out of reach)
                            return {"session": session, "answer": cut([chat["answer"]]), "error": False}
                        else:  # (taken: its answer's next)
                            req = None
                ch.sock.settimeout(wire.SILENT_S)
                for msg in ch:
                    if time.monotonic() > end:
                        return {"session": session, "answer": None, "still_working": True, "said_so_far": cut(said)}
                    if msg.get("op") == "exited" and msg.get("session") == session:
                        return {"session": session, "answer": None, "exited": msg.get("code"), "said_so_far": cut(said)}
                    if req is not None and msg.get("req") == req["req"]:  # (what was sent: taken, or not)
                        if msg.get("op") == "error":
                            raise ValueError(msg.get("error") or "the host didn't take it")
                        req = None
                        if not wait:
                            return {"session": session, "sent": True}
                        continue
                    if msg.get("op") != "claude" or msg.get("session") != session:
                        continue
                    m = msg.get("msg") or {}
                    if m.get("type") == "assistant":
                        said += [b.get("text") for b in (m.get("message") or {}).get("content") or []
                                 if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
                    elif m.get("type") == "result" and skip:  # (the turn it was at when this was sent: not its answer)
                        skip, said = skip - 1, []
                    elif m.get("type") == "result":
                        answer = m.get("result") if isinstance(m.get("result"), str) else "\n".join(said)
                        return {"session": session, "answer": cut([answer]), "error": bool(m.get("is_error"))}
                ch.close()
                ch = None
        finally:
            if ch is not None:
                ch.close()
            if told:
                self.waiting(host, session, kind, text, end, done=True)

    def reattach(self, where, session):
        """(channel, attached) for a session whose host went, once it's back (BACK_S; one stopping, waited for
        too): attached live again. (None, what's wrong) if it isn't, or the session's not there."""
        end = time.monotonic() + BACK_S
        while True:
            try:
                ch, _info = self.again(where, end)
                ch.sock.settimeout(30)
                ch.send({"op": "attach", "session": session, "live": True, "answers": False, "req": 1})
                got = ch.reply()
            except OSError as e:
                return None, f"its host went, and wasn't back within {BACK_S} s: {e}"
            if got and got.get("op") == "attached":
                ch.keep_alive()
                return ch, got
            ch.close()
            error = (got or {}).get("error")
            if error and "host process is stopping" not in error:
                return None, error
            if time.monotonic() + AGAIN_S > end:
                return None, f"its host went, and wasn't back within {BACK_S} s"
            time.sleep(AGAIN_S)

    def waiting(self, host, session, kind, text, end, done=False):
        """This chat's host told what its agent waits on (waits_on: session, on host, None for that one), or that it's
        done: kept there, so if it restarts meanwhile, which stops this, the answer's sent to this chat once there's
        one. Told: True."""
        if not self.me:
            return False
        try:
            self.request(self.local, {"op": "waits_on", "session": self.me, "done": done, "on": {
                "host": host, "session": session, "kind": kind, "text": text,
                "until": time.time() + end - time.monotonic()}}, timeout=10)
            return True
        except (OSError, ValueError):  # (a host before waits_on: a restart of it ends the wait, as it did)
            return False

    def follow(self, wait):
        """What a wait (waits_on's) that its chat's host restarted in would have given, once there's an answer: for
        that host, started again."""
        where = {"via": "mesh", "host": wait["host"], **self.relay} if wait.get("host") else self.local
        return self._answer(None, where, wait["session"], True, max(0, wait.get("until", 0) - time.time()),
                            text=wait.get("text"), kind=wait.get("kind"))

    def start_subagent(self, a):
        """A chat started on a host with the task, attached to as it starts (nothing it says missed), its answer
        waited for. Its host out of reach, or gone as it's started (restarting, say): reached again and started again
        (BACK_S), unless it took it before it went (_started)."""
        params = {"op": "start", "prompt": a["prompt"], "title": a.get("title") or " ".join(a["prompt"].split())[:60],
                  "mode": a.get("mode") or "auto", "ask_timeout": ASK_S, "ask_default": "deny",
                  "attach": True, "answers": False, "req": 1}
        for k in ("model", "cwd"):
            if a.get(k):
                params[k] = a[k]
        if self.me:
            params["spawned_by"] = self.me
        where, wait, timeout_s = self.where(a.get("host")), a.get("wait", True), a.get("timeout_s") or WAIT_S
        end, since, dropped = time.monotonic() + BACK_S, time.time(), False
        while True:
            try:
                ch, _info = self.again(where, end)
            except OSError as e:
                raise OSError(f"{a.get('host') or 'this host'} wasn't reached within {BACK_S} s: {e}") from None
            if dropped and (sid := self._started(where, params, since)):  # (taken before it went)
                ch.close()
                return self._answer(None, where, sid, wait, timeout_s, text=a["prompt"])
            try:
                ch.sock.settimeout(60)
                ch.send(params)
                while (r := ch.reply()) is not None and r.get("req") != 1:
                    pass
            except OSError:
                r = None
            if r is not None and r.get("op") != "error":
                ch.keep_alive()
                return self._answer(ch, where, r["session"]["id"], wait, timeout_s, text=a["prompt"])
            ch.close()
            if r is not None and "host process is stopping" not in (r.get("error") or ""):
                raise ValueError(r.get("error") or "the host didn't take it")
            dropped |= r is None  # (gone before it answered: started there, or not)
            if time.monotonic() + AGAIN_S > end:
                raise OSError(f"its host went as the subagent was started, and wasn't back within {BACK_S} s")
            time.sleep(AGAIN_S)

    def _started(self, where, params, since):
        """The id of a session a start (params) sent at since made before its host went, if it did: spawned by this
        chat, with its title, made since then (a minute either way: another machine's clock)."""
        try:
            listed = self.sessions(where)
        except (OSError, ValueError):
            return None
        return next((s["id"] for s in listed if s.get("spawned_by") == params.get("spawned_by") and
                     s.get("title") == params["title"] and (s.get("created") or 0) >= since - 60), None)

    def chats(self, a):
        out = []
        for s in self.sessions(self.where(a.get("host"))):
            if a.get("bots") and not s.get("bot"):
                continue
            out.append({"id": s["id"], "title": s.get("title"), "state": s.get("state"),
                        "bot": (s.get("bot") or {}).get("name"), "cwd": s.get("cwd"), "model": s.get("model")})
        return sorted(out, key=lambda s: s["id"] != self.me)[:200]

    def read_chat(self, a):
        where, _s = self.find(a["session"], a.get("host"))
        said = []
        for who, text, _tool in self._records(where, a["session"]):
            if not text.strip():
                continue
            if said and said[-1]["who"] == who:  # (a reply in pieces, as claude writes one: one message)
                said[-1]["text"] += "\n" + text
            else:
                said.append({"who": who, "text": text})
        return {"session": a["session"], "messages": [{**m, "text": cut([m["text"]])} for m in said[-max(1, int(
            a.get("last") or 6)):]]}

    def _records(self, where, session):
        """A chat's messages, from the last 2 MiB of its transcript, in order: [(who, text, tool)] (tool: a call of a
        tool's, or its result, is in it)."""
        size = self.request(where, {"op": "transcript", "session": session, "offset": 0, "limit": 1})["size"]
        start = max(0, size - (2 << 20))
        got = self.request(where, {"op": "transcript", "session": session, "offset": start, "limit": 4 << 20})
        out = []
        for line in base64.b64decode(got.get("data") or "").decode("utf-8", "replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("type") not in ("user", "assistant"):
                continue
            content = (rec.get("message") or {}).get("content")
            blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
            text = content if isinstance(content, str) else "\n".join(
                b.get("text") for b in blocks if b.get("type") == "text" and b.get("text"))
            out.append((rec["type"], text, any(b.get("type") in ("tool_use", "tool_result") for b in blocks)))
        return out

    def _chat(self, where, session, text=None):
        """How far a chat's got with what it was last asked (text; its last message of yours, if None): {asked: it's
        there, answer: what it said last after it (after its last tool's), said: all it said after it}."""
        try:
            records = self._records(where, session)
        except (OSError, ValueError, KeyError):
            records = []
        at = None
        for i, (who, said, tool) in enumerate(records):
            if who == "user" and not tool and said.strip() and (text is None or said.strip() == text.strip()):
                at = i
        if at is None:
            return {"asked": False, "answer": None, "said": ""}
        said, last = [], []
        for who, words, tool in records[at + 1:]:
            if who == "user" and not tool and words.strip():  # (another message of yours: another turn)
                break
            if who == "assistant":
                said += [words] if words.strip() else []
                last = [] if tool else last + ([words] if words.strip() else [])
        return {"asked": True, "answer": "\n".join(last), "said": "\n".join(said)}

    def message_chat(self, a):
        if a["session"] == self.me:
            raise ValueError("that's this chat")
        where, _s = self.find(a["session"], a.get("host"))
        return self.ask(where, a["session"], a["text"], a.get("wait", True), a.get("timeout_s") or WAIT_S)


def said_to(session, text):
    """The input that says text to a session, as its user would."""
    return {"op": "input", "session": session, "req": 2, "msg": {
        "type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}}


def told(wait, got):
    """What a chat's told once a wait of its agent's (waits_on's), cut short by its host's restart, is over: what the
    call would have given."""
    call = "message_chat" if wait.get("kind") == "chat" else "start_subagent"
    return (f"(Your mcp__{NAME}__{call} call, waiting on chat {wait.get('session')}, was cut short as this chat's host "
            f"process restarted. It was followed up once the host was back: this is what the call would have "
            f"given.)\n\n"
            + json.dumps(got, indent=1, ensure_ascii=False))


def cut(parts):
    text = "\n".join(p for p in parts if p)
    return text if len(text) <= TEXT_MAX else text[:TEXT_MAX] + f"\n(… {len(text) - TEXT_MAX} more characters)"


def listed():
    return [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": props, "required": req}}
            for n, (d, props, req) in SCHEMAS.items()]


def serve(me=None, rfile=None, wfile=None, socket=None):
    """The MCP server: a message a line on rfile (stdin), its answers on wfile (stdout), till rfile ends; each call on
    a thread of its own (a subagent's answer can take a while: another call meanwhile is answered)."""
    rfile, wfile = rfile or sys.stdin, wfile or sys.stdout
    mesh, lock = Mesh(me, socket), threading.Lock()

    def send(msg):
        with lock:
            wfile.write(json.dumps(msg) + "\n")
            wfile.flush()

    def call(mid, name, args):
        try:
            if name not in SCHEMAS:
                raise ValueError(f"no tool {name}")
            got = mesh.hosts() if name == "hosts" else getattr(mesh, name)(args)
            result = {"content": [{"type": "text", "text": json.dumps(got, indent=1, ensure_ascii=False)}]}
        except Exception as e:  # (said to the model, which may try again)
            result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    for line in rfile:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            continue
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": (msg.get("params") or {}).get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {}}, "serverInfo": {"name": NAME, "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": listed()}})
        elif method == "tools/call":
            p = msg.get("params") or {}
            threading.Thread(target=call, args=(mid, p.get("name"), p.get("arguments") or {}), daemon=True).start()
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}})


if __name__ == "__main__":
    serve(os.environ.get("BAUDE_SESSION"))
