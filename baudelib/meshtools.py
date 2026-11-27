"""The mesh's tools, for a Bot's agent and Meta's: an MCP server on stdin and stdout (a JSON-RPC message a
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
import uuid

from baudelib import toolnames, wire, wording

NAME = "mesh"  # (its tools are mcp__mesh__hosts, ...)
META = "Meta"  # (a head's Meta, which is no chat on a host: what its messages are from, as a phone's Meta chat is)
SUBAGENT_MODES = ("auto", "acceptEdits", "default", "plan")  # (a subagent's: Auto at most; its host holds it to these)
WAIT_S, WAIT_MAX_S = 600, 3600  # (a subagent's or a chat's reply waited for this long, by default and at most)
ASK_S = wire.CONSTANTS["ASK_S"]  # (a subagent asking a permission no one's there to give: refused after this long)
TEXT_MAX = 20000  # (characters of a reply, or of what a chat said, given back)
BACK_S = 120  # (a host gone from the mesh, restarting say, as a subagent's started or its answer's waited for: waited
# for this long to come back, and tried again)
AGAIN_S = 2  # (tried again this often meanwhile)

BOT = {"type": "string", "description": "A Bot's session id (yours if none's given)"}
SCHEMAS = {
    "hosts": ("The mesh's hosts (this one first): each one's id, name, whether it's reachable now, and the models it "
              "can run (their values are what start_subagent's model takes).", {}, []),
    "subscriptions": ("The Claude and Codex subscriptions a host's chats run on (every reachable host's if none's "
                      "given): each one's id (what start_subagent's account takes; \"\" for its CLI's own sign-in), "
                      "provider, name, kind, whether it's the one a new chat on that provider's models takes "
                      "(default), whether it's disabled (a subagent on it is refused), and the models of that host "
                      "it runs. A model in no subscription's models runs on none (Ollama's, Apple's, an endpoint's).",
                      {"host": {"type": "string", "description": "A host's id or name (hosts)"}}, []),
    "start_subagent": ("Start a new chat (a subagent) on a host of the mesh, with one of its models, give it a task "
                       "and, unless wait is false, wait for its answer. It runs as a chat there (listed in Baude), in "
                       "cwd on that host (its home if none's given), in Auto mode unless another's asked for (Auto at "
                       "most); it can't ask you for permissions, so what it does ask is refused after a minute. Its "
                       "id and its host's come back, to message it again (message_chat).",
                       {"prompt": {"type": "string", "description": "The task, said in full: it doesn't see this chat"},
                        "host": {"type": "string", "description": "A host's id or name (hosts); this one if none"},
                        "model": {"type": "string", "description": "A model's value on that host (hosts)"},
                        "account": {"type": "string",
                                    "description": "The subscription it runs on, of those that host has for its "
                                                   "model's provider (subscriptions: an id or a name; \"\" for the "
                                                   "CLI's own sign-in), in place of the one it'd take (yours, on "
                                                   "this host; else that host's default)"},
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
    "tasks": ("Your tasks (a Bot's; another Bot's, by its session): scripts your host runs, by hand or as a trigger "
              "fires (a schedule; a git push to a branch of a repo on the host), each one's triggers, form, last run "
              "and version. Their scripts call you mid-run (import baudetask: ask, fallback) when they can't decide "
              "or fail: see task for one's script.", {"session": BOT}, []),
    "task": ("One of your tasks: its script, triggers, form and variables (what each's bound to, and a warning for "
             "one bound to what isn't there now), and its latest runs (when, what started each, how long, how it "
             "ended, the version it ran).", {"session": BOT, "id": {"type": "string"}}, ["id"]),
    "set_task": ("Make one of your tasks, or change one (by its id; a field left out kept as it was, but triggers and "
                 "form, given, are given whole; variables and bindings too). A Python script imports baudetask: "
                 "baudetask.inputs (its form, filled), baudetask.payload (its trigger's event), baudetask.vars (its "
                 "variables: vars[\"Translation Model\"] or vars.translation_model, typed, None if unbound, .warning "
                 "if what it's bound to isn't there now; a model one passed straight to agent(model=)), say(text) "
                 "(a line in its log), ask(text, form=None) (asks you: your answer, or the form's values), fallback(error), and @baudetask.main (an "
                 "exception handed to you). A shell script gets its inputs as $BAUDE_IN_<FIELD>. A trigger's payload "
                 "fills a field of the same name, or the one fills maps it to. Saving a new script makes a new "
                 "version.",
                 {"session": BOT, "id": {"type": "string", "description": "A task's id, to change it"},
                  "name": {"type": "string"},
                  "runtime": {"type": "string", "enum": ["python", "shell", "bot"],
                              "description": "bot: the script is a prompt you're given as a turn"},
                  "script": {"type": "string"},
                  "form": {"type": "array", "description": "Fields: {name, label?, type: text|number|bool|choice, "
                                                           "choices?, default?, required?}", "items": {"type": "object"}},
                  "triggers": {"type": "array", "description": "{kind: schedule, every (minutes) or at (HH:MM) and "
                                                               "days (0 Monday … 6 Sunday)} or {kind: git_push, repo (a "
                                                               "folder on the host), branch, poll_s}; each may have "
                                                               "fills: {field: payload key}. A git push's payload: repo, "
                                                               "branch, before, after, commit, message, author",
                               "items": {"type": "object"}},
                  "variables": {"type": "array", "description": "Variables, each bound once for the task (not per run, "
                                                               "as the form is): {name, type: model|decider|host|"
                                                               "browser_host|terminal_host|kube_context|pod|container|"
                                                               "database|text, description?}",
                                "items": {"type": "object"}},
                  "bindings": {"type": "object", "description": "A variable's name: what it's bound to, {value, "
                                                                "effort? (a model's), host? (the host serving a model), "
                                                                "model? (a decider's), context?, namespace? (a pod's), "
                                                                "container? (a database's)}, or just its value as text; "
                                                                "given, given whole. A model's value is as hosts lists "
                                                                "it; anything else is taken too. One bound to what "
                                                                "isn't there is warned of, and the run starts anyway"},
                  "notify_failure": {"type": "boolean", "description": "A failed run is sent to you (default true)"},
                  "notify_exit": {"type": "boolean", "description": "A run that finishes with exit 0 is told to you, for your information (default false)"},
                  "timeout_s": {"type": "integer"}, "paused": {"type": "boolean"},
                  "delete": {"type": "boolean", "description": "Delete the task (id)"}}, []),
    "run_task": ("Run one of your tasks now, its form filled with inputs (the rest by default). Your own task's run is started and given at once (wait true: waited for until it ends or asks you, "
                 "as you can only answer once this turn's over); another Bot's is waited for until it ends (wait "
                 "false: at once). Gives its record and log; task_run reads it again.",
                 {"session": BOT, "id": {"type": "string"}, "inputs": {"type": "object"},
                  "wait": {"type": "boolean"}}, ["id"]),
    "task_run": ("One run of one of your tasks, and its log.", {"session": BOT, "run": {"type": "string"}}, ["run"]),
    "answer_task": ("Answer an ask of one of your tasks' runs whose message says to answer with this tool "
                    "(mcp__mesh__answer_task); the run's id is in its table. With a form, answer is an object of its "
                    "fields (the ask lists them); with none, text. Its run goes on as soon as you've answered: what "
                    "you say besides is only commentary. A wrong answer is refused with what's wrong: call it again. "
                    "A message from a task that doesn't say to use this tool is answered in your reply, not with it.",
                    {"session": BOT, "run": {"type": "string", "description": "The asking run's id (its table's run)"},
                     "answer": {"type": "object", "description": "The form's fields, by name: each a text, number, "
                                                                 "true or false, or one of a choice's choices"},
                     "text": {"type": "string", "description": "The answer to an ask with no form"}}, ["run"]),
    "answer_ask": ("Answer an ask in a person's Inbox whose message says to answer with this tool "
                   "(mcp__mesh__answer_ask): press one of its buttons, by its index, with its form's values. It takes "
                   "effect at once: its task is told, and the ask leaves every Inbox. A wrong answer is refused with "
                   "what's wrong: call it again.",
                   {"ask": {"type": "string", "description": "The ask's id (its message says it)"},
                    "button": {"type": "integer", "description": "The button's index (its message lists them)"},
                    "values": {"type": "object", "description": "The form's fields, by name: each a text, number, "
                                                                "true or false, or one of a choice's choices"}},
                   ["ask", "button"]),
    "revise_ask": ("Put a new version of an ask your task raised in a person's Inbox (its message says its id): what "
                   "it says, its buttons, its form. A field left out stays as it was. The person sees it at once.",
                   {"ask": {"type": "string", "description": "The ask's id"},
                    "title": {"type": "string", "description": "What it's called in the Inbox's list"},
                    "body": {"type": "string", "description": "What it says, in Markdown; ![alt](name) shows one of "
                                                              "its pictures"},
                    "buttons": {"type": "array", "description": "Its buttons, each {label, style?: primary|secondary|"
                                                                "blue|teal|green|yellow|orange|red|pink|purple|slate, "
                                                                "picture?: a picture's name}",
                                "items": {"type": "object"}},
                    "form": {"type": "array", "description": "Its form's fields, each {name, type: text|number|bool|"
                                                             "choice, label?, choices?, default?, required?}",
                             "items": {"type": "object"}}}, ["ask"]),
}
BOT_ANSWERS = ("answer_task", "answer_ask", "revise_ask")  # (a Bot's answers to its tasks and the Inbox: a Codex
# Bot's only mesh tools)
ASKED = ("set_task", "run_task")  # (run code on the host: asked about as Bash is, by the chat's permission mode)


def _channel(where, timeout=10, as_=None, meta=False, agent=None):
    ch, info = wire.reach(where, timeout=timeout, as_=as_, meta=meta, agent=agent)
    return ch, info or {}


class Mesh:
    """The mesh as this host's process has it (this chat's: me, its session id, or meta: a head's Meta's, in no chat;
    socket: that host process's, if it isn't this user's usual one; agent: an agent outside the apps', by its name:
    baude-mcp's)."""

    def __init__(self, me=None, socket=None, meta=False, agent=None):
        self.me, self.meta, self.agent = me, meta, agent
        if socket is None and not me and os.environ.get("BAUDE_TASK_RUN"):  # (inside a task's run: its host's)
            socket = os.environ.get("BAUDE_SOCKET") or None
        self.local = {"via": "unix", "path": socket} if socket else {"via": "local"}
        self.relay = {"path": socket} if socket else {}  # (a member reached through that host process)

    def here(self):
        ch, info = self.channel(self.local)
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
                ch, info = self.channel({"via": "mesh", "host": entry["id"], **self.relay})
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

    def subscriptions(self, a):
        """Each host's subscriptions (a host's, by its id or name, if a says one; else every reachable host's), with
        the models of the host each runs: [{host, host_name, subscriptions: [{id, provider, name, kind, default,
        disabled, models}]}], a host that couldn't be asked with error instead."""
        places = [h for h in self.hosts() if h["reachable"]] if not a.get("host") else \
            [{"id": (w := self.where(a["host"])).get("host"), "name": a["host"], "where": w}]
        out = [{"host": h["id"], "host_name": h["name"]} for h in places]

        def look(entry, h):
            where = h.get("where") or (self.local if h.get("here") else {"via": "mesh", "host": h["id"], **self.relay})
            try:
                ch, info = self.channel(where)
                ch.close()
                entry.update(host=info.get("id"), host_name=info.get("name"),
                             subscriptions=subscriptions_of(info, self.request(where, {"op": "accounts",
                                                                                       "what": "list"})))
            except (OSError, ValueError, KeyError) as e:
                entry["error"] = str(e)
        threads = [threading.Thread(target=look, args=(e, h), daemon=True) for e, h in zip(out, places)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        return out

    def where(self, host):
        """Where a host is (wire.reach's), by its id or name; this one for none."""
        if not host:
            return self.local
        here = self.here()
        if host in (here.get("id"), here.get("name")):
            return self.local
        view = here.get("mesh") or {}
        for m in (view.get("members") or []) + (view.get("gone") or []) + (view.get("links") or []):
            if host in (m.get("id"), m.get("name")) and m.get("id") and m["id"] not in (view.get("inactive") or []):
                # (one out of reach now: reached once it's back; one deactivated here: not at all)
                return {"via": "mesh", "host": m["id"], **self.relay}
        raise ValueError(f"no host {host!r} in the mesh: hosts lists them")

    def again(self, where, end):
        """(channel, info) for where, tried every AGAIN_S till end (monotonic): a host restarting is back in a while.
        OSError, the last one's, once it's not been reached by then."""
        while True:
            try:
                return self.channel(where)
            except (OSError, ValueError) as e:
                if time.monotonic() + AGAIN_S > end:
                    raise OSError(str(e)) from None
            time.sleep(AGAIN_S)

    def named(self, where):
        """The host where is, as a reply names it: {host: its id, host_name}; as much as is known, never an error
        (asked before the call, not after: a reply's never lost to this host process restarting meanwhile)."""
        try:
            here = self.here()
        except (OSError, ValueError):
            return {"host": where.get("host"), "host_name": None}
        if where.get("host") is None:
            return {"host": here.get("id"), "host_name": here.get("name")}
        view = here.get("mesh") or {}
        m = next((m for m in (view.get("members") or []) + (view.get("gone") or []) if m.get("id") == where["host"]),
                 {})
        return {"host": where["host"], "host_name": m.get("name")}

    def request(self, where, msg, timeout=30):
        ch, _info = self.channel(where)
        try:
            ch.sock.settimeout(timeout)
            ch.send({**msg, "req": 1})
            while (r := ch.reply()) is not None:
                if r.get("req") == 1:
                    if r.get("op") == "error":
                        raise ValueError(wire.said(r))
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

    def channel(self, where, timeout=10):
        """(channel, info) for where, as from this chat (relay's as, or on its host's socket hello's): what's sent on
        it is from this chat, and its host judges it as the member that started the chat, if one did (access)."""
        return _channel(where, timeout, self.me, self.meta, self.agent)

    def ask(self, where, session, text, wait=True, timeout_s=WAIT_S, title=None):
        """text sent to a session, from this chat (input's as); its answer (the turn's result) waited for, unless not
        wait."""
        ch, _info = self.channel(where)
        try:
            ch.send({"op": "attach", "session": session, "live": True, "answers": False, "req": 1})
            got = ch.reply()
            if not got or got.get("op") != "attached":
                raise ValueError(wire.said(got) or "the host didn't answer")
            ch.keep_alive()
            busy = (got.get("session") or {}).get("state") == "working"  # (it's at a turn: this one's answer's next)
            offset = self.request(where, {"op": "transcript", "session": session, "offset": 0, "limit": 1})["size"] \
                if wait else 0  # (its chat as far as it's written before this: what's said to it is after)
            sent = said_to(session, text, self.me, self.meta, self.agent)
            ch.send(sent)
        except BaseException:
            ch.close()
            raise
        return self._answer(ch, where, session, wait, timeout_s, skip=1 if busy else 0, req=sent, text=text,
                            kind="chat", offset=offset, title=title)

    def _answer(self, ch, where, session, wait, timeout_s, skip=0, req=None, text=None, kind="subagent", offset=0,
                end=None, title=None):
        """The turn's answer, read off ch (attached, live; closed by the end), or as far as it got. skip: the answers
        of turns before it, to pass by; req: the input sent, its error the answer at once; text: what the turn was
        asked, what its answer comes after in the chat, past offset (its transcript's size as it was asked); end:
        when the wait's over (monotonic), if not timeout_s from now. Its host gone meanwhile (restarting, say; ch
        None: from the start): reached again (BACK_S) and the session looked at (_chat). Its turn going on: followed
        again, the turns before it passed by. Done meanwhile: its answer read from the chat. Cut short at the turn
        (its claude stopped by that restart): said so, interrupted. Cut before its claude had what it was asked:
        sent again (req, or text, from this chat). This chat's host is told what it waits on meanwhile (waiting;
        title: the chat's, for what it's held as)."""
        if not wait and req is None:
            if ch is not None:
                ch.close()
            return {"session": session, "sent": True}
        if end is None:
            end = time.monotonic() + max(5, min(int(timeout_s or WAIT_S), WAIT_MAX_S))
        host = where.get("host") if where.get("via") == "mesh" else None
        said, told = [], wait and self.waiting(host, session, kind, text, offset, end, title=title)
        again = req  # (sent again as it was; else what it was asked, from this chat)
        try:
            while True:
                if ch is None:
                    if not wait:  # (sent and not waited for: not followed, nor matched to what's said in it)
                        return {"session": session, "sent": True, "lost": True}
                    ch, got = self.reattach(where, session, min(end, time.monotonic() + BACK_S))
                    if ch is None:
                        return {"session": session, "answer": None, "lost": True, "said_so_far": cut(said),
                                "error": got}
                    s = got.get("session") or {}
                    stopped = bool(s.get("cut"))
                    working = s.get("state") in ("working", "waiting") and not stopped
                    chat = self._chat(where, session, text, offset)
                    if chat["asked"]:  # (taken: its turn's going on, or over)
                        req = None
                        if stopped and not chat["next"]:
                            return {"session": session, "host": host, "answer": None,
                                    "said_so_far": cut([chat["said"]]),
                                    "interrupted": "Its host process restarted as it worked, which stopped it at this "
                                                   "turn. To carry it on, message_chat it (this session); read_chat "
                                                   "shows how far it got."}
                        if chat["next"] or not working:  # (its turn ended while its host was out of reach)
                            return {"session": session, "answer": cut([chat["answer"]]), "error": False}
                        skip, said = 0, []
                    elif stopped and text is not None:  # (its host stopped before its claude had it: sent again)
                        req, skip, said = again or said_to(session, text, self.me, self.meta, self.agent), 0, []
                        ch.send(req)
                    else:  # (not written yet: the turn it's at is another's, or it's next)
                        skip, said = (1 if working else 0), []
                ch.sock.settimeout(wire.SILENT_S)
                for msg in ch:
                    if time.monotonic() > end:
                        return {"session": session, "answer": None, "still_working": True, "said_so_far": cut(said)}
                    if msg.get("op") == "exited" and msg.get("session") == session:
                        return {"session": session, "answer": None, "exited": msg.get("code"), "said_so_far": cut(said)}
                    if req is not None and msg.get("req") == req["req"]:  # (what was sent: taken, or not)
                        if msg.get("op") == "error":
                            raise ValueError(wire.said(msg) or "the host didn't take it")
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
                self.waiting(host, session, kind, text, offset, end, done=True, title=title)

    def reattach(self, where, session, end):
        """(channel, attached) for a session whose host went, once it's back (till end, monotonic; one stopping,
        waited for too): attached live again (the channel's info, its host's hello's: its version or ops). (None, what's wrong) if it isn't, or
        the session's not there."""
        while True:
            try:
                ch, info = self.again(where, end)
                ch.info = info
                ch.sock.settimeout(30)
                ch.send({"op": "attach", "session": session, "live": True, "answers": False, "req": 1})
                got = ch.reply()
            except OSError as e:
                return None, f"its host went, and wasn't back in time: {e}"
            if got and got.get("op") == "attached":
                ch.keep_alive()
                return ch, got
            ch.close()
            error = wire.said(got)
            if error and "host process is stopping" not in error:
                return None, error
            if time.monotonic() + AGAIN_S > end:
                return None, "its host went, and wasn't back in time"
            time.sleep(AGAIN_S)

    def waiting(self, host, session, kind, text, offset, end, done=False, title=None):
        """This chat's host told what its agent waits on (waits_on: session, on host, None for that one), or that it's
        done: kept there, so if it restarts meanwhile, which stops this, the answer's sent to this chat once there's
        one. Told: True."""
        if not self.me:
            return False
        try:
            self.request(self.local, {"op": "waits_on", "session": self.me, "done": done, "on": {
                "host": host, "session": session, "kind": kind, "text": text, "offset": offset,
                **({"title": title} if title is not None else {}), "until": time.time() + end - time.monotonic()}},
                timeout=10)
            return True
        except (OSError, ValueError):  # (a host before waits_on keeps none: a restart of it ends the wait)
            return False

    def follow(self, wait):
        """What a wait (waits_on's) that its chat's host restarted in would have given, once there's an answer (by
        its end, until): for that host, started again."""
        where = {"via": "mesh", "host": wait["host"], **self.relay} if wait.get("host") else self.local
        named, left = self.named(where), float(wait.get("until") or 0) - time.time()
        if left <= 0:
            return {"session": wait["session"], "answer": None, **named,
                    "expired": "The wait ran out as this chat's host restarted: read_chat shows whether it's answered "
                               "since."}
        return {**self._answer(None, where, wait["session"], True, left, text=wait.get("text"), kind=wait.get("kind"),
                               offset=int(wait.get("offset") or 0), end=time.monotonic() + left,
                               title=wait.get("title")), **named}

    def start_subagent(self, a):
        """A chat started on a host with the task, attached to as it starts (nothing it says missed), its answer
        waited for. Its host out of reach, or gone as it's started (restarting, say): reached again and started again
        (BACK_S), unless it took it before it went (_started: the start's own start_key)."""
        params = {"op": "start", "prompt": a["prompt"], "title": a.get("title") or " ".join(a["prompt"].split())[:60],
                  "mode": a.get("mode") or "auto", "ask_timeout": ASK_S, "ask_default": "deny",
                  "attach": True, "answers": False, "start_key": str(uuid.uuid4()), "req": 1}
        for k in ("model", "cwd"):
            if a.get(k):
                params[k] = a[k]
        if a.get("account") is not None:
            params["account"] = a["account"]
        where, wait, timeout_s = self.where(a.get("host")), a.get("wait", True), a.get("timeout_s") or WAIT_S
        if self.me:
            params["spawned_by"] = params["as"] = self.me  # (its task's from this chat: its host says so)
        elif self.meta:
            params["as_meta"] = True  # (from Meta: held to Auto at most too)
        else:  # (baude-mcp's, inside a Bot's task's run: under that Bot, and the run's on its own host)
            params.update(wire.from_task(here=where == self.local))
        named = self.named(where)
        end, dropped = time.monotonic() + BACK_S, False
        while True:
            try:
                ch, _info = self.again(where, end)
            except OSError as e:
                raise OSError(f"{a.get('host') or 'this host'} wasn't reached within {BACK_S} s: {e}") from None
            if dropped:
                if (sid := self._started(where, params["start_key"])) is not None:  # (taken before it went)
                    ch.close()
                    return {**self._answer(None, where, sid, wait, timeout_s, text=a["prompt"], title=params["title"]),
                            **named}
            try:
                ch.sock.settimeout(60)
                ch.send(params)
                while (r := ch.reply()) is not None and r.get("req") != 1:
                    pass
            except OSError:
                r = None
            if r is not None and r.get("op") != "error":
                ch.keep_alive()
                return {**self._answer(ch, where, r["session"]["id"], wait, timeout_s, text=a["prompt"],
                                       title=params["title"]), **named}
            ch.close()
            if r is not None and "host process is stopping" not in (r.get("error") or ""):
                raise ValueError(wire.said(r) or "the host didn't take it")
            dropped |= r is None  # (gone before it answered: started there, or not)
            if time.monotonic() + AGAIN_S > end:
                raise OSError(f"its host went as the subagent was started, and wasn't back within {BACK_S} s")
            time.sleep(AGAIN_S)

    def _started(self, where, key):
        """The id of the session a start made before its host went, if it did: the one with its start_key."""
        try:
            listed = self.sessions(where)
        except (OSError, ValueError):
            return None
        return next((s["id"] for s in listed if s.get("start_key") == key), None)

    def _bot(self, a):
        sid = a.get("session") or self.me
        if not sid:
            raise ValueError("which Bot's? (session)")
        return sid

    def tasks(self, a):
        return self.request(self.local, {"op": "tasks", "session": self._bot(a)})["tasks"]

    def task(self, a):
        got = self.request(self.local, {"op": "task", "session": self._bot(a), "id": a["id"],
                                       "script": True})
        return {"task": got["task"], "runs": got.get("runs") or []}

    def set_task(self, a):
        sid = self._bot(a)
        if a.get("delete"):
            self.request(self.local, {"op": "tasks", "session": sid, "delete": a.get("id") or ""})
            return {"deleted": a.get("id")}
        task = {k: a[k] for k in ("id", "name", "runtime", "script", "form", "triggers", "variables", "bindings",
                                  "notify_failure", "notify_exit", "timeout_s", "paused") if k in a}
        if a.get("id"):  # (fields left out: as they were)
            old = self.request(self.local, {"op": "task", "session": sid, "id": a["id"]})["task"]
            task = {**{k: old.get(k) for k in ("name", "runtime", "form", "triggers", "variables", "bindings",
                                               "notify_failure", "notify_exit", "timeout_s", "paused")}, **task}
        got = self.request(self.local, {"op": "tasks", "session": sid, "set": task})
        return next(t for t in got["tasks"] if t["id"] == got.get("saved"))

    def run_task(self, a):
        sid = self._bot(a)
        own = sid == self.me  # (its asks are this turn's to answer, once it's over: never waited on in it)
        run = self.request(self.local, {"op": "run_task", "session": sid, "id": a["id"],
                                        "inputs": a.get("inputs") or {},
                                        "waited": own and a.get("wait", False) is True})["run"]
        end = time.monotonic() + WAIT_MAX_S
        while a.get("wait", not own) is True and time.monotonic() < end:
            got = self.task_run({"session": sid, "run": run["id"]})
            if got["run"]["status"] not in ("running", "asking") or own and got["run"]["status"] == "asking":
                return got
            time.sleep(1)
        return self.task_run({"session": sid, "run": run["id"]})

    def task_run(self, a):
        got = self.request(self.local, {"op": "task_run", "session": self._bot(a), "run": a["run"]})
        return {"run": got["run"], "log": got.get("log") or []}

    def answer_task(self, a):
        msg = {"op": "answer_task", "session": self._bot(a), "run": a["run"],
               **{k: a[k] for k in ("answer", "text") if k in a}}
        got = self.request(self.local, msg)
        return {k: got[k] for k in ("task", "run", "values", "text") if k in got}

    def answer_ask(self, a):
        self.request(self.local, {"op": "inbox_press", "ask": a["ask"], "button": a["button"],
                                  "values": a.get("values") or {}, "as": self.me})
        return {"ask": a["ask"], "answered": True}

    def revise_ask(self, a):
        got = self.request(self.local, {"op": "inbox_revise", "ask": a["ask"],
                                        **{k: a[k] for k in ("title", "body", "buttons", "form") if k in a}})
        return {"ask": got["ask"], "version": got["version"]}

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

    def _records(self, where, session, offset=None):
        """A chat's messages, in order: [(who, text, tool)] (tool: a call of a tool's, or its result, is in it). From
        offset in its transcript to its end, if it's given; else from the last 2 MiB."""
        size = self.request(where, {"op": "transcript", "session": session, "offset": 0, "limit": 1})["size"]
        at = max(0, size - (2 << 20)) if offset is None else min(int(offset), size)
        data = b""
        while True:
            got = self.request(where, {"op": "transcript", "session": session, "offset": at, "limit": 4 << 20})
            data += base64.b64decode(got.get("data") or "")
            if got.get("next", at) <= at or got["next"] >= got.get("size", 0) or len(data) > 64 << 20:
                break
            at = got["next"]
        out = []
        for line in data.decode("utf-8", "replace").splitlines():
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

    def _chat(self, where, session, text=None, offset=0):
        """How far a chat's got with what it was asked (text, the first message of yours that's it past offset; its
        last message of yours, if None): {asked: it's there, answer: what it said last after it (after its last
        tool's), said: all it said after it, next: another message of yours came after it (its turn's over)}."""
        try:
            records = self._records(where, session, offset)
        except (OSError, ValueError, KeyError):
            records = []
        at = None
        for i, (who, said, tool) in enumerate(records):
            if who == "user" and not tool and said.strip() and \
                    (text is None or wire.unsaid(said).strip() == text.strip()):  # (as from this chat, too)
                at = i
                if text is not None:
                    break
        if at is None:
            return {"asked": False, "answer": None, "said": "", "next": False}
        said, last, after = [], [], False
        for who, words, tool in records[at + 1:]:
            if who == "user" and not tool and words.strip():  # (another message of yours: another turn)
                after = True
                break
            if who == "assistant":
                said += [words] if words.strip() else []
                last = [] if tool else last + ([words] if words.strip() else [])
        return {"asked": True, "answer": "\n".join(last), "said": "\n".join(said), "next": after}

    def message_chat(self, a):
        if a["session"] == self.me:
            raise ValueError("that's this chat")
        where, s = self.find(a["session"], a.get("host"))
        named = self.named(where)
        return {**self.ask(where, a["session"], a["text"], a.get("wait", True), a.get("timeout_s") or WAIT_S,
                           title=s.get("title")), **named}


PROVIDERS = ("claude", "codex")  # (accounts.PROVIDERS: the providers a subscription is of)
CODEX = "codex/"  # (a Codex model's value's start: models.model_program's)


def subscriptions_of(info, got):
    """A host's subscriptions (its hello's info; got: its accounts list's answer), each with the models of the host's
    it runs: its CLI's own sign-in (id "") for each provider whose program the host has, then its accounts. An error
    the answer says, ValueError."""
    if got.get("error"):
        raise ValueError(got["error"])
    other = {m.get("value") for p in info.get("providers") or [] for m in p.get("models") or []}
    runs = {p: [] for p in PROVIDERS}
    for m in info.get("models") or []:
        v = m.get("value")
        if isinstance(v, str) and v not in other and not v.startswith("agent/"):  # (a provider's, another member's)
            runs["codex" if v.startswith(CODEX) else "claude"].append(v)
    defaults, off = got.get("defaults") or {}, set(got.get("disabled") or ())
    has = {"claude": bool(got.get("claude_cli")) or bool(runs["claude"]),
           "codex": "codex" not in (got.get("missing") or {})}
    cli = [{"id": "", "provider": p, "name": wording.say("subscriptions.cli_default"), "kind": "cli"}
           for p in PROVIDERS if has[p]]
    return [{**{k: a.get(k) for k in ("id", "provider", "name", "kind")},
             "default": defaults.get(a["provider"], "") == a["id"],
             "disabled": (a["id"] or ("codex" if a["provider"] == "codex" else "")) in off,
             "models": runs.get(a["provider"]) or []}
            for a in cli + [a for a in got.get("accounts") or [] if a.get("provider") in PROVIDERS]]


def said_to(session, text, me=None, meta=False, agent=None):
    """The input that says text to a session: from chat me (input's as), if it's a chat's, from Meta (as_meta), or from
    an agent outside the apps, by its name (as_agent)."""
    return {"op": "input", "session": session, "req": 2, **({"as": me} if me else {}),
            **({"as_meta": True} if meta else {}), **({"as_agent": agent} if agent and not me and not meta else {}),
            "msg": {
        "type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}}


def told(wait, got):
    """What a chat's told once a wait of its agent's (waits_on's), cut short by its host's restart, is over: what the
    call would have given."""
    call = "message_chat" if wait.get("kind") == "chat" else "start_subagent"
    return (f"(Your mcp__{NAME}__{call} call, waiting on chat {wait.get('session')}, was cut short as this chat's host "
            f"process restarted. It was followed up once the host was back: this is what the call would have "
            f"given.)\n\n"
            + json.dumps(got, indent=1, ensure_ascii=False))


def shown(got):
    """What a person's shown of a wait cut short as told says it (a held answer's, beside what its agent's sent): the
    answer (an error result's said so), or what became of it in a line, as Baude's card for the call says it."""
    if isinstance(got.get("answer"), str) and got["answer"].strip():
        return wording.say("held_answer.error", answer=got["answer"]) if got.get("error") is True else got["answer"]
    if got.get("interrupted"):
        return wording.say("held_answer.interrupted")
    if got.get("expired"):
        return wording.say("held_answer.expired")
    if got.get("still_working"):
        return wording.say("held_answer.still_working")
    if "exited" in got:
        return wording.say("held_answer.exited", code=got["exited"]) if got["exited"] is not None else \
            wording.say("held_answer.exited_plain")
    if isinstance(got.get("error"), str) and got["error"].strip():  # (a lost one's too: why)
        return got["error"]
    if got.get("lost"):
        return wording.say("held_answer.lost")
    return wording.say("held_answer.nothing")


def shown_of_told(text):
    """shown for what told said (a held answer kept before heads were shown anything else: its result, after the
    note), or None if it isn't that."""
    try:
        got = json.loads(text.split("\n\n", 1)[1])
    except (IndexError, ValueError):
        return None
    return shown(got) if isinstance(got, dict) else None


def cut(parts):
    text = "\n".join(p for p in parts if p)
    return text if len(text) <= TEXT_MAX else text[:TEXT_MAX] + f"\n(… {len(text) - TEXT_MAX} more characters)"


def listed(schemas=None, server=NAME):
    return [{"name": n, **({"title": t} if (t := toolnames.TITLES.get(server, {}).get(n)) else {}), "description": d,
             "inputSchema": {"type": "object", "properties": props, "required": req}}
            for n, (d, props, req) in (schemas or SCHEMAS).items()]


def serve(me=None, rfile=None, wfile=None, socket=None, meta=False, only=None):
    """The MCP server: a message a line on rfile (stdin), its answers on wfile (stdout), till rfile ends; each call on
    a thread of its own (a subagent's answer can take a while: another call meanwhile is answered). only: the tools
    it has, by name (None: all)."""
    mesh = Mesh(me, socket, meta)
    serve_tools(NAME, {n: v for n, v in SCHEMAS.items() if only is None or n in only}, lambda name, args: mesh.hosts() if name == "hosts" else getattr(mesh, name)(args),
                rfile, wfile)


def serve_tools(name, schemas, call, rfile=None, wfile=None, started=None):
    """An MCP server of tools (schemas: {name: (description, properties, required)}, each called as call(name, args),
    its answer given as JSON): a message a line on rfile (stdin), its answers on wfile (stdout), till rfile ends; each
    call on a thread of its own. started(params): initialize's params (the client's clientInfo), as it's asked."""
    rfile, wfile, lock = rfile or sys.stdin, wfile or sys.stdout, threading.Lock()

    def send(msg):
        with lock:
            wfile.write(json.dumps(msg) + "\n")
            wfile.flush()

    def run(mid, tool, args):
        try:
            if tool not in schemas:
                raise ValueError(f"no tool {tool}")
            got = call(tool, args)
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
            if started is not None:
                started(msg.get("params") or {})
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": (msg.get("params") or {}).get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": name, "title": toolnames.SERVERS.get(name, name), "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": listed(schemas, name)}})
        elif method == "tools/call":
            p = msg.get("params") or {}
            threading.Thread(target=run, args=(mid, p.get("name"), p.get("arguments") or {}), daemon=True).start()
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}})


if __name__ == "__main__":
    serve(os.environ.get("BAUDE_SESSION"))
