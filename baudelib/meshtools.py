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

SCHEMAS = {
    "hosts": ("The mesh's hosts (this one first): each one's id, name, whether it's reachable now, and the models it "
              "can run (their values are what start_subagent's model takes).", {}, []),
    "start_subagent": ("Start a new chat (a subagent) on a host of the mesh, with one of its models, give it a task "
                       "and, unless wait is false, wait for its answer. It runs as a chat there (listed in Baude), in "
                       "cwd on that host (its home if none's given), in Auto mode unless another's asked for (Auto at "
                       "most); it can't ask you for permissions, so what it does ask is refused after a minute. Its "
                       "id and its host's come back, to message it again (message_chat).",
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
        return out + found

    def where(self, host):
        """Where a host is (wire.reach's), by its id or name; this one for none."""
        if not host:
            return self.local
        here = self.here()
        if host in (here.get("id"), here.get("name")):
            return self.local
        for m in (here.get("mesh") or {}).get("members") or []:
            if host in (m.get("id"), m.get("name")):
                return {"via": "mesh", "host": m["id"], **self.relay}
        raise ValueError(f"no host {host!r} in the mesh: hosts lists them")

    def named(self, where):
        """The host where is, as a reply names it: {host: its id, host_name}."""
        here = self.here()
        if where.get("host") is None:
            return {"host": here.get("id"), "host_name": here.get("name")}
        m = next((m for m in (here.get("mesh") or {}).get("members") or [] if m.get("id") == where["host"]), {})
        return {"host": where["host"], "host_name": m.get("name")}

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
            ch.send({"op": "input", "session": session, "req": 2, "msg": {
                "type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}})
            return self._answer(ch, session, wait, timeout_s, skip=1 if busy else 0, req=2)
        finally:
            ch.close()

    def _answer(self, ch, session, wait, timeout_s, skip=0, req=None):
        """The turn's answer, read off ch (attached, live), or as far as it got (skip: the answers of turns before it,
        to pass by; req: what was sent's, its error the answer at once)."""
        if not wait and req is None:
            return {"session": session, "sent": True}
        end = time.monotonic() + max(5, min(int(timeout_s or WAIT_S), WAIT_MAX_S))
        ch.sock.settimeout(wire.SILENT_S)
        said = []
        for msg in ch:
            if time.monotonic() > end:
                return {"session": session, "answer": None, "still_working": True, "said_so_far": cut(said)}
            if msg.get("op") == "exited" and msg.get("session") == session:
                return {"session": session, "answer": None, "exited": msg.get("code"), "said_so_far": cut(said)}
            if req is not None and msg.get("req") == req:  # (what was sent: taken, or not)
                if msg.get("op") == "error":
                    raise ValueError(msg.get("error") or "the host didn't take it")
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
        return {"session": session, "answer": None, "lost": True, "said_so_far": cut(said)}

    def start_subagent(self, a):
        """A chat started on a host with the task, attached to as it starts (nothing it says missed), its answer
        waited for."""
        params = {"op": "start", "prompt": a["prompt"], "title": a.get("title") or " ".join(a["prompt"].split())[:60],
                  "mode": a.get("mode") or "auto", "ask_timeout": ASK_S, "ask_default": "deny",
                  "attach": True, "answers": False, "req": 1}
        for k in ("model", "cwd"):
            if a.get(k):
                params[k] = a[k]
        if self.me:
            params["spawned_by"] = self.me
        where = self.where(a.get("host"))
        ch, _info = _channel(where)
        try:
            ch.sock.settimeout(60)
            ch.send(params)
            while (r := ch.reply()) is not None and r.get("req") != 1:
                pass
            if r is None or r.get("op") == "error":
                raise ValueError((r or {}).get("error") or "the host went before it answered")
            ch.keep_alive()
            got = self._answer(ch, r["session"]["id"], a.get("wait", True), a.get("timeout_s") or WAIT_S)
        finally:
            ch.close()
        return {**got, **self.named(where)}

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
        size = self.request(where, {"op": "transcript", "session": a["session"], "offset": 0, "limit": 1})["size"]
        start = max(0, size - (2 << 20))
        got = self.request(where, {"op": "transcript", "session": a["session"], "offset": start, "limit": 4 << 20})
        lines = base64.b64decode(got.get("data") or "").decode("utf-8", "replace").splitlines()
        said = []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") not in ("user", "assistant"):
                continue
            content = (rec.get("message") or {}).get("content")
            text = content if isinstance(content, str) else "\n".join(
                b.get("text") for b in content or [] if isinstance(b, dict) and b.get("type") == "text" and b.get("text"))
            if not text.strip():
                continue
            if said and said[-1]["who"] == rec["type"]:  # (a reply in pieces, as claude writes one: one message)
                said[-1]["text"] += "\n" + text
            else:
                said.append({"who": rec["type"], "text": text})
        return {"session": a["session"], "messages": [{**m, "text": cut([m["text"]])} for m in said[-max(1, int(
            a.get("last") or 6)):]]}

    def message_chat(self, a):
        if a["session"] == self.me:
            raise ValueError("that's this chat")
        where, _s = self.find(a["session"], a.get("host"))
        return {**self.ask(where, a["session"], a["text"], a.get("wait", True), a.get("timeout_s") or WAIT_S),
                **self.named(where)}


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
