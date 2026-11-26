"""The host process's sessions (Server's ServerSessions): starting and adopting one, its claude (ensure_proc), what's
said to it and what it says, who its messages are from, renaming it, watching for changes, and reading its
transcript, its subagents', its files and its folder's catalogue.
"""
import base64
import json
import os
import sys
import threading
import time
import uuid

from baudelib import (
    accounts, agentproxy, autofix, chattools, claude, codex, emissiontools, meshtools, models, multiplextools, notes, transcripts, wire, wording,
    deps, desktopchats, hostlogin, worktree,
)
from baudelib.serverstate import (
    AGENT_TOOLS, CATALOGUE_LIMIT, CATALOGUE_WAIT_S, CHECK_SOCKET_S, CLAUDE_OPTIONS, COMMAND_TOOLS, CONTEXT_EVERY_S,
    FILE_LIMIT, GRACE_MOST, RENAME_S, SETTINGS, Session, TAKEN, TRANSCRIPT_CHUNK, _said, _what, _write_json, bot_system,
    clean_env, context_kept, context_listing, turn_acting,
)

CARRY_TRIES = 5  # (copies of a chat's record made, each written to as it was copied, before its account waits again)
FOLLOW_UP_WAIT = 30  # (s a claude's given to begin the turn a follow-up sent at the last one starts, before an account
# picked at that turn's taken: begun by then, it's taken after that turn instead)


class ServerSessions:
    """Server's sessions: starting one, its claude, what's said to it and by it, who from, and reading it."""

    # ---- sessions
    def start(self, params, conn=None, req=None):
        """A new session, as start asks (wire.py): conn attached live to it first if it asks to be (attach), and told
        started once it's begun (its claude started, its prompt sent): before claude says anything of it, which waits
        on the lock this holds. Anything wrong, and it's not kept."""
        model = params.get("model") or None
        if params.get("adopt") is not None:
            return self.adopt(params, conn, req)
        with self.lock:
            src = self.sessions.get(params["fork_from"]) if params.get("fork_from") else None
        origin = src  # (the chat forked, or asked a side question of: its folder made again if it's gone)
        if params.get("fork_from") and params.get("ephemeral") and src is not None and src.transcript() is None:
            params = {**params, "cwd": params.get("cwd") or src.meta["cwd"]}  # (a side question in a chat with
            src = None                                                         # nothing written yet: a fresh one, there)
        elif params.get("fork_from") and (src is None or src.kept() or src.transcript() is None):
            raise ValueError(wording.say("host_errors.no_fork_source", session=params["fork_from"]))
        cwd = os.path.realpath(os.path.expanduser(params.get("cwd") or (src.meta["cwd"] if src else "~")))
        if origin is not None and not os.path.isdir(cwd) and cwd == os.path.realpath(origin.meta["cwd"]):
            worktree.restore(cwd, origin.transcript())  # (not under the lock: git; Gone if it can't be)
        if not os.path.isdir(cwd):
            raise ValueError(wording.say("host_errors.not_a_folder", path=cwd))
        if src and cwd != os.path.realpath(src.meta["cwd"]):  # (claude finds what it resumes by its folder)
            raise ValueError(wording.say("host_errors.fork_folder", path=src.meta["cwd"]))
        model = model or (src.meta.get("model") if src else None)
        mode = params.get("mode") or "default"
        if (params.get("spawned_by") or params.get("as_meta")) and mode not in meshtools.SUBAGENT_MODES:
            mode = "auto"  # (a subagent of a chat's or Meta's: Auto at most; a program without Auto's modes,
            # launch's next best: manual)
        if params.get("ephemeral") and models.model_program(model) == "codex":
            mode = "default"
        run = models.launch(model, mode, params.get("effort") or None)
        if not self.can_run(model):
            raise ValueError(wording.say("host_errors.cant_run", host=self.me["name"], model=model))
        options = {k: params[k] for k, _flag in CLAUDE_OPTIONS if params.get(k)}
        if not isinstance(options.get("system", ""), str):
            raise ValueError("system is text")
        for k in ("add_dirs", "allowed_tools", "disallowed_tools"):
            if k in options:
                values = options[k] if isinstance(options[k], list) else [options[k]]
                if not all(isinstance(v, str) and v.strip() and not v.startswith("-") for v in values):
                    raise ValueError(f"{k} is a list of words, none starting with -")
                options[k] = values
        if (options or src) and run["program"] != "claude":
            raise ValueError(f"{', '.join([*options, *(['fork_from'] if src else [])])}: claude's, not {model}'s")
        if params.get("ask_default") not in (None, "deny", "allow"):
            raise ValueError("ask_default is deny or allow")
        tools = params.get("app_tools") or []  # (MCP servers the head runs itself: relayed back to it, AppTools)
        if not isinstance(tools, list) or not all(wire.is_id(t) for t in tools) or len(tools) > 4:
            raise ValueError("app_tools is a list of up to 4 names")
        if tools and (conn is None or run["program"] != "claude"):
            raise ValueError("app_tools are a head's, for a claude here")
        meta = {"id": str(uuid.uuid4()), "cwd": cwd, "model": model, "effort": run["effort"], "mode": run["mode"],
                "group": self.group_of(params), "title": params.get("title") or None,
                "prompt": params.get("prompt") or "", "created": time.time(), "options": options,
                "fork_from": (src.meta.get("cli_id") or src.id) if src else None,
                "ask_timeout": float(params["ask_timeout"]) if params.get("ask_timeout") else None,
                "ask_default": params.get("ask_default") or "deny", "ephemeral": bool(params.get("ephemeral")),
                "grace": min(float(params["grace"]), GRACE_MOST) if params.get("ephemeral") and
                isinstance(params.get("grace"), (int, float)) and params["grace"] > 0 else None,
                "spawned_by": params.get("spawned_by") if wire.is_id(params.get("spawned_by") or "") else None,
                "start_key": params.get("start_key") if wire.is_id(params.get("start_key") or "") else None,
                "draft": bool(params.get("draft")) and not (params.get("prompt") or "").strip(),
                **({"started_by": {"id": conn.member, "verified": conn.verified}}  # (a member's: its tools judged as it)
                   if getattr(conn, "member", None) is not None else {})}
        with self.lock:
            if self.stopped.is_set():
                raise OSError("the host process is stopping")
            # (its accounts: the chat's it's forked from or asked of, or that spawned it here; else the defaults)
            parent = origin or (self.sessions.get(meta["spawned_by"]) if meta["spawned_by"] else None)
            meta["profiles"] = dict(parent.meta.get("profiles", {})) if parent else accounts.defaults()
            s = Session(self, meta)
            if tools:
                s.app_tools = agentproxy.AppTools(self, conn, s.id, tools)
            if isinstance(params.get("env"), dict):
                s.env = clean_env(params["env"])
            self.sessions[s.id] = s
            self.save(s)
            if conn is not None and params.get("attach"):
                if params.get("answers", True) is False:
                    conn.mute.add(s.id)
                s.attached_once = True
                s.viewers.add(conn)
                s.live.add(conn)
            try:
                if src is not None:  # (who its messages were from goes with the copy of them)
                    self.copy_senders(src, s)
                if meta["prompt"].strip():  # (from a chat whose mesh tools started it, if they did: input's as)
                    self.ensure_proc(s)
                    self.send_user(s, meta["prompt"], self.sent_by(conn, params) if conn is not None else None)
            except Exception:  # (its claude couldn't start: no session, and the error's the answer)
                if s.app_tools is not None:
                    s.app_tools.end()
                del self.sessions[s.id]
                if s.proc is not None:
                    s.proc.kill()
                for kept in (os.path.join(self.state_dir, "sessions", f"{s.id}.json"), self.senders_path(s.id)):
                    try:  # (nor who its prompt was from)
                        os.remove(kept)
                    except OSError:
                        pass
                raise
            if conn is not None:
                conn.send({"op": "started", "req": req, "session": s.listing(), "forked": bool(src)})
            self.log(f"started {s.id} in {cwd} ({meta['group']})")
            self.changed()
        return s

    def group_of(self, params):
        """A new session's group: as asked; the host's own if none's asked; none if asked for none ("": Baude's own
        chats, which are in no group till they're put in one)."""
        group = params.get("group")
        return group if isinstance(group, str) and (group or "group" in params) else self.me["group"]

    def adopt(self, params, conn=None, req=None):
        """start {adopt: id}: a conversation claude has here already (a chat Baude ran itself, or a terminal's), made
        one of this host's, by its own id, and resumed by it from then on (its transcript's where claude keeps it).
        One that's this host's already: as it is. Its claude starts with the first message (input)."""
        sid = params["adopt"]
        if not wire.is_id(sid):
            raise ValueError(f"not a session's id: {sid!r}")
        path = transcripts.session_file(sid)
        with self.lock:
            if self.stopped.is_set():
                raise OSError("the host process is stopping")
            s = self.sessions.get(sid)
            if s is not None and s.deleting:
                raise ValueError(wording.say("host_errors.being_deleted", session=sid))
            if s is None:
                if not path:
                    raise ValueError(wording.say("host_errors.no_adopt_source", session=sid))
                cwd = os.path.realpath(os.path.expanduser(params.get("cwd") or "~"))
                if not os.path.isdir(cwd) and not worktree.restorable(cwd):  # (a worktree that's gone: made again
                    raise ValueError(wording.say("host_errors.not_a_folder", path=cwd))  # with its first message)
                model = params.get("model") or None
                if not self.can_run(model):
                    raise ValueError(wording.say("host_errors.cant_run", host=self.me["name"], model=model))
                run = models.launch(model, params.get("mode") or "default", params.get("effort") or None)
                s = Session(self, {"id": sid, "cwd": cwd, "model": model,
                                   "profiles": {"claude": accounts.claude_session_profile(sid)},
                                   "effort": run["effort"], "mode": run["mode"],
                                   "group": self.group_of(params), "title": params.get("title") or None,
                                   "prompt": "", "created": time.time(), "options": {}, "path": path,
                                   "said": os.path.getmtime(path), "ask_default": "deny", "adopted": True})
                if sid in desktopchats.desktop_index():  # (Claude Desktop's: listed in Desktop still, wherever it runs)
                    s.meta["desktop"] = True
                self.sessions[sid] = s
                self.save(s)
                self.log(f"took up {sid} in {cwd}")
            if isinstance(params.get("env"), dict):
                s.env = clean_env(params["env"])
            if conn is not None:
                conn.send({"op": "started", "req": req, "session": s.listing()})
            self.changed()
        return s

    def codex_empty_thread(self, s):
        """Older Baude builds stored an ID even when Codex had no rollout yet."""
        if s.meta.get("codex_started") or s.meta.get("context_usage"):
            return False
        path = s.transcript()
        if path is None:
            return True
        try:
            with open(path, encoding="utf-8") as transcript:
                return not any(line.strip() and json.loads(line).get("type") == "assistant" for line in transcript)
        except (OSError, ValueError):
            return False

    def ensure_proc(self, s):
        """(lock held) The session's claude, started if it isn't running: with its id until it's written a transcript,
        resumed after. One being stopped is let go of first (what's sent now is the next one's)."""
        if s.proc is not None and getattr(s.proc, "stopping", False):
            old, s.proc, s.busy = s.proc, None, False
            s.forget_commands()
            old.kill()
            try:
                old.p.wait(5)
            except Exception:  # (still going: a claude of its own, about to go)
                pass
            self.drop_routes(s)
        if s.proc is not None:
            return s.proc
        if not os.path.isdir(s.meta["cwd"]):  # (gone, and not made again before this: restore_folder's)
            raise worktree.missing(s.meta["cwd"])
        resumed = s.transcript() is not None
        run = models.launch(s.meta.get("model"), s.meta.get("mode"), s.meta.get("effort"),
                            s.meta.get("cli_id") or s.id, resumed=resumed)
        program = models.model_program(s.meta.get("model"))
        if program in ("claude", "codex") and (missing := deps.missing(program)) is not None:  # (its program isn't
            raise deps.Missing(missing, "report.claude" if program == "claude" else "programs.codex")  # here at all)
        if not self.can_run(s.meta.get("model")):
            raise OSError(wording.say("host_errors.cant_run", host=self.me["name"], model=s.meta.get("model")))
        extra = list(run["extra"])
        if run["program"] == "claude":
            if s.meta.get("fork_from") and not resumed:  # (its first run: a copy of the other's conversation, which
                run["resume"], extra = s.meta["fork_from"], ["--fork-session"]  # claude names itself: cli_id)
            for key, flag in CLAUDE_OPTIONS:
                value = (s.meta.get("options") or {}).get(key)
                if value:
                    extra += [flag, *(value if isinstance(value, list) else [value])]
            if s.meta.get("ephemeral"):  # (a side question: sees the conversation, writes nothing of its own)
                extra += ["--no-session-persistence"]
        if s.meta.get("bot") and run["program"] in ("claude", "pi"):  # (who it is, and its memory, each start)
            mem = self.bot_memory(s)
            told = bot_system(s.meta["bot"]["name"], mem["path"], mem["text"])
            if run["program"] == "claude" and "--append-system-prompt" in extra:  # (one: its own system with it)
                at = extra.index("--append-system-prompt") + 1
                extra[at] = extra[at] + "\n\n" + told
            else:
                extra += ["--append-system-prompt", told]
            if run["program"] == "claude":  # (its memory's folder its to edit, as the chat's own is, without asking)
                folder = os.path.dirname(mem["path"])
                extra += ["--add-dir", folder]
                rules = [f"{tool}(/{folder}/**)" for tool in ("Edit", "Write")]  # (//: a path from the root)
                if "--allowedTools" in extra:  # (one: the chat's own allowed_tools with them)
                    at = extra.index("--allowedTools") + 1
                    extra[at:at] = rules
                else:
                    extra += ["--allowedTools", *rules]
        if run["program"] in ("claude", "pi") and not s.meta.get("ephemeral"):  # (its own tools: chattools, every
            servers = {chattools.NAME: chattools.server(s.id, self.socket_path, sys.executable,  # chat's)
                                                        agentproxy.HEADLESS)}
            rules = [f"mcp__{chattools.NAME}__{t}" for t in chattools.SCHEMAS]
        else:
            servers, rules = {}, []
        if s.mesh_tools() and run["program"] in ("claude", "pi"):  # (the mesh's tools: baudelib.meshtools, here)
            servers |= {meshtools.NAME: {"command": sys.executable, "args": [agentproxy.HEADLESS, "mesh-tools",
                                                                            "--socket",
                                                                            os.path.abspath(self.socket_path)],
                                        "env": {"BAUDE_SESSION": s.id}}}
            if s.meta.get("bot"):  # (a Bot's browser too: its own Emission profile, on any host)
                servers[emissiontools.NAME] = {"command": sys.executable,
                                               "args": [agentproxy.HEADLESS, "emission-tools", "--socket",
                                                        os.path.abspath(self.socket_path)],
                                               "env": {"BAUDE_SESSION": s.id}}
                servers[multiplextools.NAME] = {"command": sys.executable,  # (and its terminals: Multiplex's)
                                                "args": [agentproxy.HEADLESS, "multiplex-tools", "--socket",
                                                         os.path.abspath(self.socket_path)],
                                                "env": {"BAUDE_SESSION": s.id}}
            rules += [f"mcp__{meshtools.NAME}__{t}" for t in meshtools.SCHEMAS  # (never asked about, a Bot's or Meta's;
                      if t not in meshtools.ASKED]                          # but those that run code here, as Bash is)
            if s.meta.get("bot"):
                rules += [f"mcp__{emissiontools.NAME}__{t}" for t in emissiontools.SCHEMAS]
                rules += [f"mcp__{multiplextools.NAME}__{t}" for t in multiplextools.SCHEMAS]
        if servers:
            extra += ["--mcp-config", json.dumps({"mcpServers": servers})]
            if run["program"] == "claude":  # (its tools never asked about)
                if "--allowedTools" in extra:
                    at = extra.index("--allowedTools") + 1
                    extra[at:at] = rules
                else:
                    extra += ["--allowedTools", *rules]
        if s.app_tools is not None and run["program"] == "claude":  # (a head's own tools: relays back to it, never
            tools = s.app_tools.config()                              # asked about: the head runs them itself)
            if "--mcp-config" in extra:  # (the mesh's with them, in one)
                at = extra.index("--mcp-config") + 1
                extra[at] = json.dumps({"mcpServers": {**json.loads(extra[at])["mcpServers"], **tools}})
            else:
                extra += ["--mcp-config", json.dumps({"mcpServers": tools})]
            rules = [f"mcp__{n}__*" for n in tools]
            if "--allowedTools" in extra:
                at = extra.index("--allowedTools") + 1
                extra[at:at] = rules
            else:
                extra += ["--allowedTools", *rules]
        made = []  # (the claude its exit is about: claude_exited)
        kind = agentproxy.ProxiedProc if models.proxied(s.meta.get("model")) else claude.ClaudeProc
        if kind is claude.ClaudeProc and run["program"] == "pi":  # (pi here writes its session itself: a run of it
            agentproxy.PiSession.release(agentproxy.pi_sessions(), s.meta.get("cli_id") or s.id)  # elsewhere no more)
        if run["program"] == "codex":
            profile = s.meta.get("profiles", {}).get("codex", "")
            # The server keeps the display history. Codex resumes its own thread using its native ID.
            s.proc = codex.CodexProc(
                lambda p, m: self.from_claude(s, p, m), lambda code: self.claude_exited(s, made, code),
                s.meta["cwd"], s.meta["model"],
                resume=None if s.meta.get("ephemeral") else s.meta.get("cli_id"),
                effort=run["effort"], exe=run["exe"], ephemeral=s.meta.get("ephemeral", False),
                profile=profile,
                env={**s.env, **(self.env or {})}, catalog=self.codex_models(profile), mode=run["mode"],
                recover_empty=bool(s.meta.get("cli_id")) and self.codex_empty_thread(s))
        else:
            s.proc = kind(
                lambda p, m: self.from_claude(s, p, m), lambda code: self.claude_exited(s, made, code), s.meta["cwd"],
                s.meta.get("model"), run["mode"], run["resume"], run["effort"], extra=extra,
                env={**s.env, **(self.env or {})} or None,  # (a head's, as it gave it; the host's own extras over it)
                exe=run["exe"] if run["program"] != "claude" else self.exe,
                **({"profile": s.meta.get("profiles", {}).get("claude", "")} if run["program"] == "claude" else {}),
                **({"path": self.socket_path} if kind is agentproxy.ProxiedProc else {}))
        made.append(s.proc)
        s.used, s.streamed, s.acting = time.time(), False, False  # (a new agent, its model's perhaps: how it says
        # what it writes learned again)
        self.changed()  # (running: listed so, and the watch waits for it to go idle)
        return s.proc

    def send_user(self, s, content, sender=None):
        """(lock held) A session's first message (start's prompt), from sender if it isn't its user's (take_input)."""
        said = str(uuid.uuid4())  # (its program keeps it: who it's from kept by it, stamp)
        if wire.clean_sender(sender) is not None:
            content = wire.said_by(sender, content)
            self.stamp(s, said, sender)
        s.proc.user(content, uuid=said)
        self.keep(s, "user", {"role": "user", "content": content}, said)
        s.busy, s.used, s.turn_began = True, time.time(), time.time()
        s.meta["said"] = time.time()
        self.save(s)

    def take_input(self, conn, s, msg, sender=None, shown=None, again=False):
        """(lock held) What a client sends its claude. A user message is given a uuid if it has none, which its
        program keeps; sender: who it's from, if not the session's user (sent_by): said so in it, for its agent
        (wire.said_by), and kept by that uuid (stamp), with what heads show of it if not its text (shown: a held
        answer's). One whose uuid it's taken already (a head sending it again, its connection gone as it first sent
        it) taken once: the last TAKEN kept in its meta. Those attached live but conn are told it as it's given its agent
        and kept (tell_said). again: one taken already, held for the turn going on (codex_queue)."""
        kind = msg.get("type")
        if kind == "user" and not again:
            desktopchats.refuse_or_resync(s)  # (a Desktop chat someone else is writing: Elsewhere, said)
            if wire.is_id(msg.get("uuid") or ""):
                taken = s.meta.setdefault("taken", [])
                if msg["uuid"] in taken:
                    return
                taken.append(msg["uuid"])
                del taken[:-TAKEN]
            msg = {**msg, "uuid": msg["uuid"] if wire.is_id(msg.get("uuid") or "") else str(uuid.uuid4())}
        if kind == "user" and wire.clean_sender(sender) is not None:
            message = msg.get("message") or {}
            msg = {**msg, "message": {**message, "content": wire.said_by(sender, message.get("content"))}}
            self.stamp(s, msg["uuid"], sender, shown)
        if kind == "user":
            is_codex = models.model_program(s.meta.get("model")) == "codex"
            if is_codex:
                codex.user_input((msg.get("message") or {}).get("content", ""))
            if is_codex and s.busy:
                s.codex_queue.append((msg, conn))
                return
            s.meta.pop("cut", None)  # (carried on, or something else said: not cut any more)
            if s.meta.pop("draft", None) and not s.meta.get("prompt"):  # (written in: a session of its own from now on,
                s.meta["prompt"] = _said(msg)                           # listed and kept, titled by what was said)
                if s.proc is None and not s.meta.get("fork_from") and not s.meta.get("spawned_by") and \
                        not s.meta.get("account_picked"):  # (its account: the default as it's first written in, picked since it was made too; one picked for it kept)
                    s.meta["profiles"] = accounts.defaults()
            at_turn = s.busy
            self.ensure_proc(s).send(msg)
            if at_turn and s.kept():  # (taken after the turn it's sent in: kept after it too)
                s.queued.append((msg.get("message") or {}, msg.get("uuid"), conn))
            elif at_turn:  # (claude's own to take after the turn it's sent in: an account picked waits for it)
                s.sent_at_turn = True
            else:
                self.keep(s, "user", msg.get("message") or {}, msg.get("uuid"))
                self.tell_said(s, msg.get("message") or {}, msg["uuid"], conn)
            if not at_turn:
                s.turn_began = time.time()
            s.busy, s.used, s.meta["said"] = True, time.time(), time.time()
            self.save(s)
            self.changed()
            return
        if kind == "control_response":  # (an answer to what claude asked)
            s.asks.pop((msg.get("response") or {}).get("request_id"), None)
            if s.proc is not None:
                s.proc.send(msg)
            s.used = time.time()
            self.changed()
            return
        if kind != "control_request":
            if s.proc is not None:
                s.proc.send(msg)
            return
        rid, request = msg.get("request_id"), msg.get("request") or {}
        sub, undo = request.get("subtype"), None
        answer = lambda response: conn.send({"op": "claude", "session": s.id, "msg": {  # noqa: E731
            "type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": response}}})
        if sub == "get_context_usage" and models.model_program(s.meta.get("model")) == "codex":
            return answer(s.meta.get("context_usage") or {"showWithoutLimit": True})
        if sub == "initialize":
            if s.proc is None and not os.path.isdir(s.meta["cwd"]):  # (its folder gone: nothing started for this, a
                return answer(self.init({**(s.meta.get("init") or {}),  # message makes it again: restore_folder)
                                         "current_permission_mode": s.meta.get("mode") or "default"}))
            if s.meta.get("init") is not None and (s.proc is None or s.proc.init_rid is None):
                return answer(self.init({**s.meta["init"], "current_permission_mode": s.meta.get("mode") or "default"}))
            s.waiting_init.append((conn, rid))
            try:
                self.ensure_proc(s)
            except deps.Missing:  # (its program isn't here: said once, by the message sent after, not here above it)
                pass
            return
        if sub == "set_model" and request.get("model") and \
                models.model_program(request["model"]) != models.model_program(s.meta.get("model")):
            return conn.send({"op": "claude", "session": s.id, "msg": {"type": "control_response", "response": {
                "subtype": "error", "request_id": rid,
                "error": wording.say("host_errors.other_model")}}})
        if sub == "set_model" and s.proc is not None and request.get("model") != s.meta.get("model") and \
                (models.model_program(request.get("model")) == "pi" or
                 models.runs_on(request.get("model")) != models.runs_on(s.meta.get("model"))):
            s.proc.stopping = s.proc.restarting = True  # (pi-claude runs one model, and one on another host runs
            s.proc.kill()  # there: the next message starts it on this one, pi's session going on: nothing's gone)
        if sub == "set_permission_mode" and models.model_program(s.meta.get("model")) == "codex" and (
            models.codex_mode(request.get("mode")) != request.get("mode")
            or (s.meta.get("ephemeral") and request.get("mode") != "default")
        ):
            return conn.send({"op": "claude", "session": s.id, "msg": {"type": "control_response", "response": {
                "subtype": "error", "request_id": rid, "error": "Unsupported Codex permission mode"}}})
        if sub in SETTINGS:
            key, field = SETTINGS[sub]
            undo = (key, s.meta.get(key), request.get(field) or s.meta.get(key))  # (put back if claude says no)
            s.meta[key] = undo[2]
        elif sub == "apply_flag_settings" and "effortLevel" in (request.get("settings") or {}):
            s.meta["effort"] = request["settings"]["effortLevel"]
        if models.model_program(s.meta.get("model")) == "codex":
            s.meta["effort"] = models.codex_effort(self.codex_models(s.meta.get("profiles", {}).get("codex", "")), s.meta["model"], s.meta.get("effort"))
        self.save(s)
        if (s.proc is None or getattr(s.proc, "stopping", False)) and sub != "rewind_conversation":
            if sub == "set_model":
                self.rewindow(s)
            self.changed()  # (stopped: answered from what's kept, for when it runs; only a message starts it, and a
            # revert, which its claude makes)
            if sub == "get_settings":
                return answer({"applied": {"model": s.meta.get("model"), "effort": s.meta.get("effort")}})
            if sub == "interrupt":  # (nothing here to stop: no result's coming, so a head waiting on one ends its turn)
                return answer({"running": False})
            if sub == "get_context_usage":  # (what it last said)
                return answer(s.meta.get("context_usage") or {})
            return answer({})
        if sub == "get_context_usage" and s.meta.get("context_usage"):  # (what it last said, categories too: the
            return answer(s.meta["context_usage"])  # listing has its count only)
        try:
            proc = self.ensure_proc(s)
        except OSError as e:  # (its claude couldn't be started: a revert's said so, not left waiting)
            return conn.send({"op": "claude", "session": s.id, "msg": {"type": "control_response", "response": {
                "subtype": "error", "request_id": rid, "error": str(e)}}})
        on = f"srv_{next(self.rids)}"
        if sub == "rewind_conversation" and s.kept():  # (pi's and Apple's: the model's cut, by its program; the
            # transcript's, here, once it's made)
            path = os.path.join(self.state_dir, "transcripts", f"{s.id}.jsonl")
            found = transcripts.rewind_target(path, request.get("target_message_uuid"))
            if found is None:
                return conn.send({"op": "claude", "session": s.id, "msg": {"type": "control_response", "response": {
                    "subtype": "success", "request_id": rid,
                    "response": {"rewound": False, "error": wording.say("host_errors.rewind_missing")}}}})
            s.rewinds[on], index, count, before = found
            msg = {**msg, "request": {**request, "baude_prompt_index": index, "baude_prompt_count": count,
                                      "baude_kept": before}}  # (Apple's, its session made again from them)
        if sub == "rewind_conversation":  # (once it's made, who what it drops was from goes too: unstamp)
            s.reverting[on] = request.get("target_message_uuid")
        s.routes[on] = (conn, rid, undo)
        proc.send({**msg, "request_id": on})
        self.changed()

    def from_claude(self, s, proc, msg):
        """What a session's claude says (on its reading thread)."""
        with self.lock:
            if proc is not s.proc:
                return
            kind = msg.get("type")
            if kind == "control_response":
                r = msg.get("response") or {}
                rid = r.get("request_id")
                if rid == proc.init_rid:
                    proc.init_rid = None  # (answered: what it said is kept for whoever asks next)
                    if r.get("subtype") == "success":
                        s.meta["init"] = r.get("response") or {}
                        self.save(s)
                        if models.model_program(s.meta.get("model")) == "claude":  # (the latest: info's, for a head's
                            self.me["init"] = {k: v for k, v in s.meta["init"].items()  # pickers before its own)
                                               if k in ("commands", "models", "output_style", "available_output_styles")}
                            _write_json(os.path.join(self.state_dir, "server.json"), self.me)
                        r = {**r, "response": self.init(s.meta["init"], fresh=True)}
                        self.ask_context(s)
                    for conn, theirs in s.waiting_init:
                        conn.send({"op": "claude", "session": s.id,
                                   "msg": {**msg, "response": {**r, "request_id": theirs}}})
                    s.waiting_init = []
                elif rid in s.routes:
                    conn, theirs, undo = s.routes.pop(rid)
                    rewound = r.get("subtype") == "success" and (r.get("response") or {}).get("rewound")
                    if rid in s.rewinds:  # (a revert its program made: marked in the transcript kept here)
                        last = s.rewinds.pop(rid)
                        if rewound:
                            self.keep_rewind(s, last)
                            s.queued = []  # (sent after it, at the turn it stopped: gone with it)
                    if (target := s.reverting.pop(rid, None)) and rewound:
                        self.unstamp(s, target)
                        self.ask_context(s)  # (less of the window used)
                    applied = (r.get("response") or {}).get("applied") if r.get("subtype") == "success" else None
                    if isinstance(applied, dict):  # (get_settings: what claude runs with, its own defaults too: kept,
                        for key in ("model", "effort"):  # for when it's asked again and isn't running)
                            if applied.get(key) and not s.meta.get(key):
                                s.meta[key] = applied[key]
                                self.save(s)
                    if undo and r.get("subtype") == "error" and s.meta.get(undo[0]) == undo[2]:
                        s.meta[undo[0]] = undo[1]  # (refused, a mode it can't switch to say: kept as it still is)
                        self.save(s)
                        self.changed()
                    if undo and undo[0] == "model" and r.get("subtype") == "success":
                        self.ask_context(s)  # (another model: another window)
                    if callable(theirs):  # (the server's own request: rename, ask_context)
                        theirs(r.get("subtype") == "success", r.get("response") or {})
                    else:
                        conn.send({"op": "claude", "session": s.id,
                                   "msg": {**msg, "response": {**r, "request_id": theirs}}})
                return
            if kind == "control_request":
                request = msg.get("request") or {}
                if request.get("subtype") != "can_use_tool":
                    proc.send({"type": "control_response", "response": {
                        "subtype": "error", "request_id": msg.get("request_id"),
                        "error": f"baude-server: unsupported {request.get('subtype')}"}})
                    return
                if request.get("tool_name") in s.always_allow and models.model_program(s.meta.get("model")) == "claude":
                    proc.respond(msg.get("request_id"), {"behavior": "allow",  # (always allowed, a head said:
                                                         "updatedInput": request.get("input") or {}})  # whoever's here)
                    return
                if self.edits_memory(s, request):  # (a Bot's memory is its own to keep: pi's and others' too)
                    proc.respond(msg.get("request_id"), {"behavior": "allow",
                                                         "updatedInput": request.get("input") or {}})
                    return
                s.asks[msg.get("request_id")] = msg
                self.cutting.discard(s.id)  # (asking as it stops: nobody can answer, so it stays cut, however it ends)
                if s.meta.get("ask_timeout"):
                    self.ask_timer(s, proc, msg.get("request_id"))
                for conn in s.viewers:
                    conn.send({"op": "claude", "session": s.id, "msg": msg})
                self.changed()
                return
            if kind == "system" and msg.get("subtype") in ("codex_context", "baude_context"):
                self.keep_context(s, msg.get("usage"))  # (pi-claude's, MLX's count of what it's sent)
            if kind == "system" and msg.get("subtype") == "compact_boundary":
                self.ask_context(s)
            if kind == "system" and msg.get("subtype") == "status" and msg.get("permissionMode") \
                    and msg["permissionMode"] != s.meta.get("mode") \
                    and not any(u and u[0] == "mode" for _c, _r, u in s.routes.values()):
                # (claude's own: out of plan, say: kept so; not a change a head asked for before the one it's making)
                s.meta["mode"] = msg["permissionMode"]
                self.save(s)
                self.changed()
            subtype = msg.get("subtype")
            if kind == "system" and subtype == "codex_discard_empty_thread":
                s.meta.pop("cli_id", None)
                self.save(s)
                return
            if kind == "system" and subtype == "codex_turn_started":
                if not s.meta.get("ephemeral") and s.meta.get("cli_id") != msg["session_id"]:
                    s.meta["cli_id"] = msg["session_id"]
                    s.meta["codex_started"] = True
                    self.save(s)
            if kind == "system" and subtype == "init" and not isinstance(proc, codex.CodexProc) \
                    and msg.get("session_id") and msg["session_id"] != (s.meta.get("cli_id") or s.id):
                s.meta["cli_id"] = msg["session_id"]  # (claude went on in a transcript of another id)
                s.meta.pop("path", None)
                self.save(s)
            if kind in ("stream_event", "assistant") and not msg.get("parent_tool_use_id") and not s.busy:
                s.busy, s.turn_began = True, time.time()  # (a turn claude started itself: a background task's end, say)
                if self.stopped.is_set():  # (one more, started as it's stopped: cut short)
                    self.cut(s)
                    self.cutting.add(s.id)
                self.changed()
            if kind in ("assistant", "user") and not msg.get("parent_tool_use_id"):
                self.keep(s, kind, msg.get("message") or {})
                self.ask_context(s, soon=True)  # (a reply, a tool's result: the window filling through a turn)
            if kind == "stream_event" and not msg.get("parent_tool_use_id"):
                s.streamed = True  # (its text said as it's written: the same text come whole after is late)
            if kind in ("stream_event", "assistant") and not msg.get("parent_tool_use_id") and \
                    (acting := turn_acting(msg, s.acting, s.streamed)) != s.acting:
                s.acting = acting
                self.changed()
            if kind in ("assistant", "user", "system", "result") and self.track(s, msg):
                self.changed()
            if models.model_program(s.meta.get("model")) == "claude":  # (its sign-in: needed, or put right by a turn)
                hostlogin.heard(self, s, msg)
            if kind == "result" and s.id in self.cutting and self.stopped.is_set() and msg.get("subtype") == "success":
                self.cutting.discard(s.id)  # (it ended as it was stopped: not cut after all)
                s.meta.pop("cut", None)
                self.save(s)
            if kind == "result" and s.renew and proc is s.proc and not s.queued:  # (a Bot changed at its turn)
                self.restart_agent(s)
            if kind == "result" and (not self.stopped.is_set() or msg.get("subtype") == "success"):
                self.drop_waits(s)
            told = []
            if kind == "result":
                for message, said, _by in s.queued:
                    self.keep(s, "user", message, said)
                told, s.queued = s.queued, []
                s.busy, s.used, s.acting = False, time.time(), False
                autofix.turn_over(s, s.turn_began, time.time())  # (its own comments on the pull request: not sent it)
                if (s.meta.get("autofix") or {}).get("waiting"):
                    self.woken.set()  # (what Auto-fix found meanwhile: sent now)
                s.note_written()  # (its own turn's lines: not another writer's, desktopchats.elsewhere)
                self.tasks.turn_over(s, msg)  # (its tasks' asks it took: answered)
                s.asks.clear()
                if s.meta.get("context_usage") is not None:
                    self.save(s)
                self.ask_context(s)
                self.changed()
                self.telemetry.turn(s.id, msg, s.meta.get("model"), s.meta.get("effort"), s.turn_began)
                ended = {"op": "turn_ended", "session": s.id, "result": msg.get("result"),
                         "is_error": bool(msg.get("is_error")), "cost": msg.get("total_cost_usd"),
                         "turns": msg.get("num_turns"), "duration_ms": msg.get("duration_ms")}
                for conn in self.conns:  # (as it happens: a turn too short for a sessions listing to see isn't missed;
                    # an archived session's to those who see archived ones; to those it's listed for: access)
                    if conn.subscribed and (conn.archived or not s.meta.get("archived")) and \
                            self.access.allowed(conn, "manage_read", s):
                        conn.send(ended)
            for conn in s.live:
                conn.send({"op": "claude", "session": s.id, "msg": msg})
            for message, said, by in told:
                self.tell_said(s, message, said, by)
            if kind == "result" and s.codex_queue:
                if self.stopped.is_set() or s.deleting or getattr(proc, "stopping", False):
                    s.codex_queue.clear()
                else:
                    held, by = s.codex_queue.pop(0)
                    self.take_input(by, s, held, again=True)
            if kind == "result":
                followed, s.sent_at_turn = bool(told) or s.sent_at_turn, False
                if s.account_after_turn and proc is s.proc:  # (another account picked at the turn: moved onto it,
                    self.account_after(s, FOLLOW_UP_WAIT if followed else 0)  # follow-ups sent at it answered first)

    def tell_said(self, s, message, said, conn=None):
        """(lock held) A message given a session's agent, as it's kept (take_input; one taken at a turn, as the turn
        ends: queued, codex_queue), told those attached live but conn, who sent it ({type: "_said"}), as its claude
        never says it back; with who it's from (sender: with what's shown of it) if its sender store says."""
        told = {"type": "_said", "uuid": said, "message": message}
        if (sender := (self.senders(s) or {}).get(said)) is not None:
            told["sender"] = sender
        for c in s.live - {conn}:
            c.send({"op": "claude", "session": s.id, "msg": told})

    def track(self, s, msg):
        """(lock held) What a session's claude runs (Session.commands), from what it says: a Bash or PowerShell call
        (its subagents' too) from its tool_use to its tool_result; a background task (not a subagent's) from
        task_started to task_notification. A turn's result ends any call still unanswered, but one in a subagent
        running in the background (is_backgrounded, run_in_background, or in one), which goes on past it. True if
        that's changed."""
        kind, changed = msg.get("type"), False
        content = (msg.get("message") or {}).get("content")
        blocks = [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []
        if kind == "assistant":
            for c in blocks:
                if c.get("type") != "tool_use" or not isinstance(c.get("id"), str):
                    continue
                inp = c.get("input") if isinstance(c.get("input"), dict) else {}
                if c.get("name") in AGENT_TOOLS:
                    s.agent_calls[c["id"]] = (msg.get("parent_tool_use_id"), inp.get("run_in_background") is True)
                elif c.get("name") in COMMAND_TOOLS and c["id"] not in s.calls:
                    s.calls[c["id"]] = {"id": c["id"], "what": _what(inp.get("description"), inp.get("command")),
                                        "since": time.time(), "background": False,
                                        "parent": msg.get("parent_tool_use_id")}
                    changed = True
        elif kind == "user":
            for c in blocks:
                if c.get("type") == "tool_result" and s.calls.pop(c.get("tool_use_id"), None) is not None:
                    changed = True
        elif kind == "system" and isinstance(tid := msg.get("task_id"), str):
            if msg.get("subtype") == "task_started" and msg.get("task_type") == "local_agent":
                tool = msg.get("tool_use_id")
                s.agent_tasks[tid] = tool
                parent, background = s.agent_calls.get(tool) or (None, False)
                if tool is not None and (msg.get("is_backgrounded") or background or s.in_background_agent(parent)):
                    s.background_agents.add(tool)  # (its calls go on past the turn's result)
            elif msg.get("subtype") == "task_started" and tid not in s.tasks:
                call = s.calls.get(msg.get("tool_use_id")) or {}  # (a Bash run in the background: its call's)
                s.tasks[tid] = {"id": tid, "what": _what(msg.get("description")) or call.get("what") or "a task",
                                "since": call.get("since") or time.time(), "background": True}
                changed = True
            elif msg.get("subtype") == "task_notification":
                changed = s.tasks.pop(tid, None) is not None
                if (agent := s.agent_tasks.pop(tid, None)) is not None:  # (a subagent's done: nothing in it runs)
                    s.background_agents.discard(agent)
                    inside = [k for k, c in s.calls.items() if c["parent"] == agent]
                    for k in inside:
                        del s.calls[k]
                    changed |= bool(inside)
        elif kind == "result":  # (a subagent in the background goes on: what it runs, too)
            left = [k for k, c in s.calls.items() if not s.in_background_agent(c["parent"])]
            for k in left:
                del s.calls[k]
            changed = bool(left)
        return changed

    def ask_timer(self, s, proc, rid):
        """(lock held) A permission asked, answered as the session says (ask_default) once it's waited ask_timeout with
        nobody attached to answer it (someone's there: they're given as long again)."""
        def due():
            with self.lock:
                if rid not in s.asks or proc is not s.proc:
                    return
                if any(s.id not in v.mute for v in s.viewers):  # (someone's there who answers: given as long again)
                    return self.ask_timer(s, proc, rid)
                ask = s.asks.pop(rid)
                allow = s.meta.get("ask_default") == "allow"
                proc.respond(rid, {"behavior": "allow", "updatedInput": (ask.get("request") or {}).get("input") or {}}
                             if allow else {"behavior": "deny",
                                            "message": f"Nobody answered within {s.meta['ask_timeout']:g} s."})
                self.log(f"{s.id}: {'allowed' if allow else 'denied'} {(ask.get('request') or {}).get('tool_name')}, "
                         "nobody there to answer")
                self.changed()
        timer = threading.Timer(s.meta["ask_timeout"], due)
        timer.daemon = True
        timer.start()

    def keep(self, s, kind, message, said=None):
        """(lock held) A message of a session whose program keeps no transcript, kept in the server's, as Claude Code
        keeps one (what's listed, and what clients mirror, come from it). said: a message of yours's uuid, as the head
        named it (what a revert names)."""
        if not s.kept() or s.deleted or s.deleting:
            return
        path = os.path.join(self.state_dir, "transcripts", f"{s.id}.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec = {"parentUuid": None, "isSidechain": False, "type": kind, "uuid": said or str(uuid.uuid4()),
               "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()), "sessionId": s.id,
               "cwd": s.meta.get("cwd"), "entrypoint": "baude-server", "message": message}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")

    # ---- who a session's messages are from, if not its user (wire's Senders): kept by their uuids beside it
    def chat_sender(self, sid, meta=False, agent=None):
        """(lock held) One of this host's chats as a message from it is from: a Bot by its name, else by its title, on
        this host; None if there's no such chat here. meta: a head's Meta's, which is no chat here: a chat named Meta,
        as a phone's Meta is; agent: an agent outside the apps (baude-mcp's), by its name, said as Meta is."""
        if meta or (isinstance(agent, str) and agent.strip() and not sid):
            return wire.clean_sender({"kind": "chat", "name": meshtools.META if meta else agent, "host": self.me["id"],
                                      "host_name": self.me["name"]})
        s = self.sessions.get(sid) if wire.is_id(sid or "") else None
        if s is None:
            return None
        listed = s.listing()
        bot = (listed.get("bot") or {}).get("name")
        return wire.clean_sender({"kind": "bot" if bot else "chat", "name": bot or listed.get("title"),
                                  "host": self.me["id"], "host_name": self.me["name"], "session": sid})

    def sent_by(self, conn, msg):
        """(lock held) Who a request passing a message in (input, start) is from, as this host knows it: through a
        tunnel, the chat its relay named (else the one its as names) on the member it came from; on this host, the
        chat of this host's its as names; None for the session's user."""
        if conn.origin is not None:
            said = conn.sender or ({"kind": "chat", "session": msg["as"]} if wire.is_id(msg.get("as") or "") else None)
            if said is None:
                return None
            name = self.mesh.name_of(conn.origin)
            return wire.clean_sender({**said, "host": conn.origin,
                                      "host_name": said.get("host_name") if name == conn.origin[:8] else name})
        return self.chat_sender(msg.get("as"), msg.get("as_meta"), msg.get("as_agent"))

    def senders_path(self, sid):
        return os.path.join(self.state_dir, "senders", f"{sid}.json")

    def senders(self, s):
        """(lock held) Its sender store, {uuid: sender} in the order kept (read the first time it's wanted); None if
        it's there and can't be read (said in the log; read again the next time, never written over meanwhile)."""
        if s.senders is None:
            try:
                with open(self.senders_path(s.id), encoding="utf-8") as f:
                    got = json.load(f)
            except FileNotFoundError:
                got = {}
            except (OSError, ValueError) as e:
                self.log(f"{s.id}: its sender store couldn't be read: {e}")
                return None
            if not isinstance(got, dict):
                self.log(f"{s.id}: its sender store isn't one")
                return None
            s.senders = {k: v for k, v in got.items() if wire.is_id(k) and isinstance(v, dict)}
        return s.senders

    def write_senders(self, s, senders):
        """(lock held) Its sender store as it is now, written whole, and listed as changed (its revision)."""
        s.senders = senders
        if s.meta.get("ephemeral") or s.deleted or s.deleting:
            return
        if senders or os.path.exists(self.senders_path(s.id)):
            _write_json(self.senders_path(s.id), senders)
        s.meta["senders"] = (s.meta.get("senders") or 0) + 1
        self.save(s)
        self.changed()

    def stamp(self, s, said, sender, shown=None):
        """(lock held) A message passed to a session from another than its user: who it's from kept by its uuid, and
        what heads show of it in place of its text, if that's given (shown: a held answer's, its agent's told)."""
        if (sender := wire.clean_sender(sender)) is not None and wire.is_id(said or "") and \
                (have := self.senders(s)) is not None:
            self.write_senders(s, {**have, said: {**sender, **({"shown": shown} if isinstance(shown, str) else {})}})

    def unstamp(self, s, target):
        """(lock held) A revert made: the message it took back and those after it gone from its sender store. Its own
        order says which, if it's one of the store's (they're kept as they're sent); else its transcript does, read
        on a thread of its own, not under the lock (what's stamped meanwhile, after the revert, stays)."""
        if not (have := self.senders(s)):
            return
        if target in have:
            keys = list(have)
            self.write_senders(s, {k: have[k] for k in keys[:keys.index(target)]})
            return
        path, among = s.transcript(), set(have)
        if not path:
            return

        def read():
            gone = transcripts.from_record(path, target, among)
            with self.lock:
                if gone and not s.deleted and self.sessions.get(s.id) is s and (now := self.senders(s)) is not None:
                    self.write_senders(s, {k: v for k, v in now.items() if k not in gone})
        threading.Thread(target=read, daemon=True, name="unstamp").start()

    def copy_senders(self, src, s):
        """(lock held) A fork's sender store: its source's, as the copy of its conversation has the same messages."""
        if (have := self.senders(src)):  # (None: unreadable, nothing copied)
            self.write_senders(s, dict(have))

    def keep_rewind(self, s, last):
        """(lock held) A revert its program made, in a session whose transcript is the server's: marked there as claude
        marks its own (rewind_conversation), so what comes after the record it keeps last is read as gone."""
        path = os.path.join(self.state_dir, "transcripts", f"{s.id}.jsonl")
        if not s.kept() or s.deleted or s.deleting or not os.path.exists(path):
            return
        rec = {"type": "last-prompt", "leafUuid": last, "rewound": True, "sessionId": s.id,
               "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")

    def claude_exited(self, s, made, code):
        with self.lock:
            if not made or s.proc is not made[0]:
                return  # (one let go of already: ensure_proc)
            s.proc, s.busy, s.acting, s.streamed = None, False, False, False
            s.codex_queue.clear()
            s.asks.clear()
            s.rewinds.clear()  # (asked of the program gone: never answered)
            s.forget_commands()
            s.agents = None  # (what its subagents wrote as it went: looked at again)
            s.note_written()  # (exited: all it's written is there)
            self.save(s)
            for conn, theirs in s.waiting_init:
                conn.send({"op": "claude", "session": s.id, "msg": {"type": "control_response", "response": {
                    "subtype": "error", "request_id": theirs,
                    "error": wording.say("host_errors.claude_exited", code=code)}}})
            s.waiting_init = []
            self.drop_routes(s)
            if not self.stopped.is_set():
                self.drop_waits(s)
                self.account_after(s)  # (an account picked at a turn it ended with: taken now)
            if not getattr(made[0], "restarting", False) and not self.stopped.is_set():  # (the host stopping: said
                # as that, said_quitting)
                stopped = {"stopped": True} if getattr(made[0], "stopping", False) else {}  # (told to: op stop)
                for conn in s.live:
                    conn.send({"op": "exited", "session": s.id, "code": code, **stopped})
            self.changed()

    def ask_context(self, s, soon=False):
        """(lock held) How much of its window the session's agent has used, asked of it (get_context_usage) and kept
        for every head (keep_context): as it starts, as a turn ends, after a compaction or another model; soon: through
        a turn, a moment from now, at most once a second (asked only as a turn ended, a small model's 32k filled through
        a turn of tool calls with the meter standing still). Codex's says it itself."""
        proc = s.proc
        if proc is None or getattr(proc, "stopping", False) or models.model_program(s.meta.get("model")) == "codex":
            return
        if soon:
            if not s.viewers:  # (nobody has it open: asked as turns end, not through them)
                return
            wait = s.context_asked + CONTEXT_EVERY_S - time.time()
            if s.context_soon or wait > 0:
                if not s.context_soon:
                    s.context_soon = True
                    timer = threading.Timer(max(wait, 0), self._context_due, (s, proc))
                    timer.daemon = True
                    timer.start()
                return
        s.context_asked = time.time()
        on = f"srv_{next(self.rids)}"
        s.routes[on] = (None, lambda ok, said=None: ok and self.keep_context(s, said), None)
        proc.send({"type": "control_request", "request_id": on, "request": {"subtype": "get_context_usage"}})

    def _context_due(self, s, proc):
        with self.lock:
            s.context_soon = False
            if s.proc is proc and s.busy and s.viewers:
                self.ask_context(s)

    def rewindow(self, s):
        """(lock held) Another model picked while its agent can't be asked: the count kept is of the window it
        had, so it's given the new model's window if that's known, else none (the listing then shows the model's own,
        as for a session that's never said)."""
        kept = s.meta.get("context_usage")
        if not isinstance(kept, dict) or not kept.get("maxTokens"):
            return
        window = models.window_of(s.meta.get("model"))
        if window:
            s.meta["context_usage"] = {**kept, "maxTokens": window}
        else:
            s.meta["context_usage"] = {k: v for k, v in kept.items() if k != "maxTokens"}
        self.save(s)

    def keep_context(self, s, usage):
        """(lock held) What its agent said of its context, kept as every head's told it (context_listing): one that
        says neither a window nor to be shown without one changes nothing. Saved between turns (with what the turn
        kept, as it ends)."""
        kept = context_kept(usage)
        if kept is None:
            return
        listed = context_listing(kept) != context_listing(s.meta.get("context_usage"))
        s.meta["context_usage"] = kept  # (its categories too: said to the chat that's open, on get_context_usage)
        if not s.busy:
            self.save(s)
        if listed:
            self.changed()

    def drop_routes(self, s):
        """(lock held) What was asked of a session's claude that's gone: no answer's coming (the server's own requests
        are done without one)."""
        routes, s.routes = s.routes, {}
        s.reverting = {}
        for _conn, theirs, _undo in routes.values():
            if callable(theirs):
                theirs(False)

    def rename(self, s, title, done):
        """(lock held) A session's title, as Claude Code keeps it: its claude's to write in its transcript if it's
        running (rename_session); else the record claude writes for a rename, put in its transcript here; the server's
        own for a transcript it keeps (or before there's any). done() once it is."""
        title = " ".join(str(title or "").split())[:200]
        if not title:
            raise ValueError(wording.say("host_errors.empty_title"))

        def write(by_claude, _said=None):
            path = None if s.kept() else s.transcript()
            if path and not by_claude:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"type": "custom-title", "customTitle": title,
                                        "sessionId": s.meta.get("cli_id") or s.id},
                                       separators=(",", ":"), ensure_ascii=False) + "\n")
                if s.proc is None:
                    s.note_written()  # (this host's own line, not another writer's)
            if path:
                s.meta.pop("title", None)  # (the transcript says it now, and whatever a /rename in it says next)
            else:
                s.meta["title"] = title
            self.save(s)
            self.rescan(s)
            self.changed()
            done()
        if not s.kept() and s.proc is not None and not getattr(s.proc, "stopping", False):
            on = f"srv_{next(self.rids)}"
            s.routes[on] = (None, write, None)  # (one it can't do, or it's gone before it answers: written here)
            s.proc.send({"type": "control_request", "request_id": on,
                         "request": {"subtype": "rename_session", "title": title}})

            def unanswered():
                with self.lock:
                    if s.routes.pop(on, None) is not None:
                        write(False)
            threading.Timer(RENAME_S, unanswered).start()
            return
        write(False)

    def use_account(self, sid, profile):
        """A chat goes on under another of its host's accounts for its model's provider (profile; "": the CLI's own
        sign-in): its agent started again under it, resuming the chat, now or, mid-turn or with follow-ups waiting,
        once they're answered (switch_account). ValueError for a chat that isn't here, or an account that isn't one for
        its model; OSError if its record can't be carried to the account (nothing's stopped then)."""
        with self.lock:
            s = self.sessions.get(sid) if isinstance(sid, str) else None
            program = models.model_program(s.meta.get("model")) if s is not None else None
            if program not in accounts.PROVIDERS:
                raise ValueError(wording.say("subscriptions.no_account_here"))
            if profile and (accounts.account(profile) or {}).get("provider") != program:
                raise ValueError(wording.say("subscriptions.gone"))
            if profile == (s.meta.get("profiles") or {}).get(program, ""):  # (its own picked back: none waiting)
                s.account_to, s.account_after_turn = None, False
                self.changed()
                return
            s.account_to = profile
        self.switch_account(s)

    def switch_account(self, s):
        """(lock not held) A chat moved onto the account picked for it (account_to), once nothing's running in it nor
        waiting for it (a turn, follow-ups sent at it: queued, codex_queue; till then account_after_turn, and this again
        as the turn's over): its record carried to the account (accounts.carry_chat), the lock not held as it's copied,
        then, if nothing's written it meanwhile, the account kept as its own and its agent started again under it with
        what's sent it next. OSError if the copy fails (the chat left as it was)."""
        for tries in range(CARRY_TRIES):
            with self.lock:
                to = s.account_to
                if to is None or s.deleting or s.deleted:
                    return
                if s.busy or s.queued or s.codex_queue or tries == CARRY_TRIES - 1:  # (written to all along: taken
                    s.account_after_turn = True  # after its next turn)
                    return
                program = models.model_program(s.meta.get("model"))
                was = (s.meta.get("profiles") or {}).get(program, "")
                thread = s.meta.get("cli_id")
                transcript = None if s.kept() or program != "claude" else s.transcript()
            try:
                path, seen = accounts.carry_chat(program, thread, transcript, was, to)
            except (OSError, ValueError) as e:
                with self.lock:
                    if s.account_to == to:
                        s.account_to, s.account_after_turn = None, False
                        self.changed()
                self.log(f"{s.id}: not moved to another account: {e}")
                raise OSError(str(e)) from e
            with self.lock:
                if s.account_to != to or s.busy or s.queued or s.codex_queue or \
                        accounts.carry_seen(seen) != seen:
                    continue  # (picked again, or written or sent to as it was copied: looked at again)
                s.meta.setdefault("profiles", {})[program] = to
                s.meta["account_picked"] = True  # (not the default's as it's first written in: default_changed)
                s.account_to, s.account_after_turn = None, False
                if program == "claude":
                    accounts.use_claude_session(s.id, to)
                    if path:
                        s.meta["path"] = path  # (where its claude under the account resumes it, and writes it on)
                if s.proc is not None and not getattr(s.proc, "stopping", False):
                    self.restart_agent(s)
                self.save(s)
                self.changed()
                return

    def account_after(self, s, wait=0):
        """(lock held) A chat's turn over, or its agent gone, with an account picked for it meanwhile: moved onto it on
        a thread of its own (switch_account; the copy can take a while), if nothing waits for it still. wait: s given
        its claude first to begin the turn a follow-up sent at the last one starts (one begun: moved after it)."""
        if s.account_to is None or s.busy or s.queued or s.codex_queue:
            return

        def run():
            time.sleep(wait)
            try:
                self.switch_account(s)
            except OSError:
                pass  # (logged: the chat stays on the account it's on)
        threading.Thread(target=run, daemon=True, name="account").start()

    def default_changed(self, provider, profile):
        """A provider's default account picked: the new chats not written in yet, with no agent running, take it, as
        each would as it's first written in (take_input)."""
        with self.lock:
            for s in self.sessions.values():
                if s.meta.get("draft") and s.proc is None and not s.meta.get("fork_from") and \
                        not s.meta.get("spawned_by") and not s.meta.get("account_picked"):
                    s.meta.setdefault("profiles", {})[provider] = profile
                    self.save(s)
        self.changed()

    def changed(self):
        """(lock held) Every subscriber gets the sessions as they are now (soon: a burst of changes is one), and
        on_change hears, whether anyone's attached or not."""
        self.dirty = True
        self.woken.set()

    def _watch(self):
        """While someone's attached, twice a second: transcripts that grew are read (what's listed comes from them),
        their subagents' files looked at, and subscribers hear what changed, or a ping. Idle claudes are stopped. While
        nobody is, it waits, and wakes only when someone comes, a claude starts or stops, the next idle claude's due to
        stop, (nothing running, for exit_s) the host process is to go, or every CHECK_SOCKET_S, to see its socket's
        still there (still_bound)."""
        pinged, quiet, checked = 0.0, time.monotonic(), time.monotonic()
        while not self.stopped.is_set():
            with self.lock:
                attached, running = bool(self.conns), [s for s in self.sessions.values() if s.proc is not None]
            quiet = max(quiet, self.accepted)  # (a connection, however short, since: not quiet)
            if attached:
                quiet = time.monotonic()
                self.stopped.wait(0.5)
            else:
                waits = [s.used + self.idle_s - time.time() for s in running if self.stoppable(s)]
                waits.append(checked + CHECK_SOCKET_S - time.monotonic())
                if (soon := self.tasks.due()) is not None:
                    waits.append(min(soon, 60))
                if (soon := self.autofix_due()) is not None:
                    waits.append(min(soon, 60))  # (looked at again each minute: a machine asleep meanwhile isn't
                    # counted by the clock it waits on)
                if not running and self.exit_s:
                    waits.append(quiet + self.exit_s - time.monotonic())
                elif running:
                    quiet = time.monotonic()  # (something's running: not quiet yet)
                self.woken.wait(max(1.0, min(waits)))
                self.woken.clear()
                quiet = max(quiet, self.accepted)
                with self.lock:
                    now_running = any(s.proc is not None for s in self.sessions.values())
                if not now_running and self.exit_s and time.monotonic() - quiet >= self.exit_s and not self.conns \
                        and not self.mesh.links and not self.mesh.outs and self.tasks.due() is None \
                        and self.autofix_due() is None:  # (linked,
                    # or making links: the mesh's members may want it; a task to run or a branch to watch: it's to be here)
                    self.log(f"nothing running and nobody here for {self.exit_s / 3600:g} h: going")
                    threading.Thread(target=self.shutdown, daemon=True, name="exit").start()
                    return
            if self.stopped.is_set():
                return
            if attached:
                self.tasks.due()
            if time.monotonic() - checked > (10 if attached else CHECK_SOCKET_S - 1):
                checked = time.monotonic()
                self.still_bound()
            try:
                if attached:
                    autofix.due(self)
                pinged = self._look(pinged)
            except Exception as e:  # (whatever it was, it's looked at again next time)
                self.log(f"watching: {type(e).__name__}: {e}")

    def autofix_due(self):
        """autofix.due, for the watch while nobody's attached: whatever goes wrong with it said, not the watch's end."""
        try:
            return autofix.due(self)
        except Exception as e:  # noqa: BLE001 (looked at again next time)
            self.log(f"auto-fix: {type(e).__name__}: {e}")
            return 60

    def stoppable(self, s):
        """(lock held) Would an idle look stop a session's claude, once it's idle long enough (_look)? Not one at a
        turn, asking, watched live, stopping already, or whose program forgets what it's said (apple-claude, and an
        ephemeral session's, kept nowhere)."""
        return s.proc is not None and not s.busy and not s.asks and not s.live and not s.meta.get("ephemeral") and \
            not getattr(s.proc, "stopping", False) and models.program(s.meta.get("model"))["resumes"]

    def _notes(self):
        """The notes ops, one at a time in the order they came (a change after another applied after it, a listing
        after the changes asked for before it)."""
        while True:
            msg, reply = self.notes_ops.get()
            try:
                if msg.get("op") in notes.EDIT_OPS:  # (an edit to a page here)
                    kind, out = notes.edit_op(msg)
                else:  # (a write's moved pages: their edits moved too)
                    kind, out = "notes", (notes.write(msg.get("changes"), msg.get("moved"))
                                          if msg.get("action") == "write" else notes.listing(msg.get("have")))
                if (seen := notes.edits_stamp()) != self.edits_seen:  # (the info's edits said anew, if they changed)
                    with self.lock:
                        self.edits_seen, self.dirty = seen, True
                reply(kind, **out)
            except Exception as e:  # noqa: BLE001 (said to the Record that asked)
                reply("error", error=f"{type(e).__name__}: {e}")

    def _look(self, pinged):
        """One look (_watch): (when subscribers were last pinged). Record's notebooks here written since the last:
        subscribers told too (info's notes), so a Record elsewhere showing them lists them again."""
        if (written := notes.file_key()) != self.notes_file:  # (read again only once it's written)
            self.notes_file = written
            if (seen := notes.stamp()) != self.notes_seen:
                with self.lock:
                    self.notes_seen, self.dirty = seen, True
        with self.lock:
            for s in list(self.sessions.values()):
                grew = self.rescan(s)
                self.dirty |= grew
                if s.agents is None or s.proc is not None or grew:  # (only a running claude's subagents work)
                    if (agents := s.subagent_files(s.meta.get("path"))) != s.agents:
                        s.agents, self.dirty = agents, True
                if self.stoppable(s) and time.time() - s.used > self.idle_s:  # (one that'd forget it all, not)
                    self.log(f"{s.id} idle: its claude stopped")
                    s.proc.stopping = True
                    s.proc.close_stdin()
                    threading.Timer(10, s.proc.kill).start()
                    s.used = time.time()
            if self.conns:  # (Claude Desktop's list read again, for its heads: every desktopchats.LOOK_S)
                self.dirty |= self.desktop.look()
            send, self.dirty = self.dirty, False
            conns = list(self.conns)
        if send:
            every, info = self.heads_listing(archived=True), self.info()
            shown = [x for x in every if not x["archived"]]
            with self.lock:  # (each told only the chats it may manage: access)
                told = [(c, self.shown_to(c, every if c.archived else shown)) for c in conns if c.subscribed]
            for c, listing in told:
                c.send({"op": "sessions", "sessions": listing, "server": info})
            self.on_change()
        if time.monotonic() - pinged >= wire.PING_S:  # (every connection: a client takes silence for gone)
            pinged = time.monotonic()
            for c in conns:
                c.send({"op": "ping"})
        return pinged

    def rescan(self, s):
        """(lock held) A session's transcript read on, if it's changed since it was last read (what's listed comes from
        it): True if it had."""
        path = s.transcript()
        try:
            st = os.stat(path) if path else None
        except OSError:
            st = None
        stat = (st.st_size, st.st_mtime) if st else None
        if stat == s.stat:
            return False
        if st and s.stat and st.st_size < s.stat[0]:
            s.scan = None  # (rewritten: read again)
        s.stat = stat
        if path and st:
            s.scan, s.info = transcripts.scan_session(path, s.scan)
        return True

    # ---- reading
    def read_transcript(self, s, offset, limit=None):
        """Whole lines of the transcript from offset: {offset, next, size, data}."""
        return self.read_lines(s.transcript(), offset, limit)

    def read_subagents(self, s, have, limit=None):
        """Its subagents' files, each from where a client's copy of it is (have: {name: [size, mtime_ns]}): a
        transcript's whole lines after that (read_lines), a meta.json whole if it's not as the client has it; limit,
        all of them together, at most: {files: [{name, size, mtime, offset, next, data}], more}. more: there's more to
        fetch (those limit didn't reach aren't listed)."""
        have = have if isinstance(have, dict) else {}
        room = max(4096, min(int(limit or TRANSCRIPT_CHUNK), 16 << 20))
        files, folder, more = [], s.subagents_dir(), False
        if (found := s.subagent_files()) != s.agents:  # (listed as they're served: a client's checked against it)
            s.agents = found
            self.changed()
        for name, (size, mtime) in sorted(found.items()):
            had = have.get(name)
            at, when = had if isinstance(had, list) and len(had) == 2 and isinstance(had[0], int) else (0, None)
            whole = name.endswith(".jsonl")
            new = (at != size) if whole else (at, when) != (size, mtime)
            if new and room <= 0:
                more = True
                break
            f = {"name": name, "size": size, "mtime": mtime, "offset": at, "next": at, "data": ""}
            if new and whole:
                got = self.read_lines(os.path.join(folder, name), at if at < size else 0, room)
                f.update(offset=got["offset"], next=got["next"], size=max(size, got["size"]), data=got["data"])
                room -= got["next"] - got["offset"]
                more |= bool(got["data"]) and got["next"] < got["size"]  # (cut short by limit: the rest next time)
            elif new:
                try:
                    with open(os.path.join(folder, name), "rb") as fh:
                        data = fh.read(FILE_LIMIT)
                except OSError:
                    continue  # (gone meanwhile)
                f.update(offset=0, next=len(data), data=base64.b64encode(data).decode("ascii"))
                room -= len(data)
            files.append(f)
        return {"files": files, "more": more}

    def read_lines(self, path, offset, limit=None):
        """Whole lines of a file from offset: {offset, next, size, data}."""
        limit = max(4096, min(int(limit or TRANSCRIPT_CHUNK), 16 << 20))
        try:
            with open(path, "rb") as f:
                size = f.seek(0, os.SEEK_END)
                offset = 0 if offset > size else int(offset)
                f.seek(offset)
                data = more = f.read(limit)
                while more and b"\n" not in more and len(data) < (64 << 20):
                    more = f.read(limit)  # (a line longer than a chunk: all of it)
                    data += more
        except (OSError, TypeError):
            return {"offset": 0, "next": 0, "size": 0, "data": ""}
        whole = data[:data.rfind(b"\n") + 1]
        return {"offset": offset, "next": offset + len(whole), "size": size,
                "data": base64.b64encode(whole).decode("ascii")}  # (as it is, byte for byte: the mirror's size is where it's up to)

    def read_file(self, path, limit=None):
        """A file in one of its sessions' folders (what a session's links name), not anywhere: there's no sign-in."""
        path = os.path.expanduser(path or "")
        if not os.path.isabs(path):
            return {"path": path, "error": wording.say("host_errors.not_absolute")}
        if not self.in_sessions(os.path.realpath(path)):
            return {"path": path, "error": wording.say("host_errors.not_in_session_folder")}
        try:
            with open(path, "rb") as f:
                data = f.read(min(int(limit or FILE_LIMIT), FILE_LIMIT) + 1)
            size = os.path.getsize(path)
        except OSError as e:
            return {"path": path, "error": e.strerror or str(e)}
        return {"path": path, "size": size, "truncated": len(data) > FILE_LIMIT,
                "data": base64.b64encode(data[:FILE_LIMIT]).decode("ascii")}

    def task_output(self, sid, path, limit=None):
        """The last part of a background task's output file: one its session's transcript names, not any file."""
        with self.lock:
            s = self.sessions.get(sid)
        if s is None or path not in s.output_files():
            return {"path": path, "error": wording.say("host_errors.not_task_output")}
        try:
            n = min(int(limit or 4000), FILE_LIMIT)
            with open(path, "rb") as f:
                size = f.seek(0, os.SEEK_END)
                f.seek(max(0, size - n))
                data = f.read()
        except (OSError, ValueError) as e:
            return {"path": path, "error": getattr(e, "strerror", None) or str(e)}
        return {"path": path, "size": size, "truncated": size > n, "data": base64.b64encode(data).decode("ascii")}

    def in_sessions(self, path):
        """Is path (real) one of its sessions' folders, or in one?"""
        with self.lock:
            folders = {os.path.realpath(s.meta["cwd"]) for s in self.sessions.values() if s.meta.get("cwd")}
        return any(path == f or path.startswith(f.rstrip(os.sep) + os.sep) for f in folders)

    def catalogue(self, cwd, wait=False):
        """What @ lists for a session's folder, as Baude lists its own chat's (lookup.items: its files, branches,
        Kubernetes objects, what's running on this machine, its pull requests and issues), made here. What there is is
        sent at once; with nothing made yet, or wait, it's waited for while it's made (and GitHub's first read of it),
        up to CATALOGUE_WAIT_S. busy: there's more to come. At most CATALOGUE_LIMIT files, and every other name."""
        if not isinstance(cwd, str) or not os.path.isabs(os.path.expanduser(cwd)):
            return {"cwd": cwd, "items": [], "error": wording.say("host_errors.not_absolute")}
        real = os.path.realpath(os.path.expanduser(cwd))
        if not self.in_sessions(real) or not os.path.isdir(real):
            return {"cwd": cwd, "items": [], "error": wording.say("host_errors.not_session_folder")}
        root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        import lookup
        import lookupgit
        with self.catalogue_lock:  # (two heads asking at once: one Folder, made once)
            folder = lookup.Folder.for_folder(real)
            folder.catalogue_soon()
            first = self.catalogue_asked.setdefault(folder.root, time.monotonic())
        lookupgit.GITHUB.look([folder.root])

        def busy():
            github = lookupgit.gh_path() is not None and \
                lookupgit.GITHUB.listed(folder.root) is lookupgit.GITHUB.NOTHING and \
                time.monotonic() - first < CATALOGUE_WAIT_S  # (its first read: a folder that's on GitHub's, soon)
            return folder.catalogue_busy or github
        if wait or not folder.catalogue:
            until = time.monotonic() + CATALOGUE_WAIT_S
            while busy() and time.monotonic() < until:
                time.sleep(0.1)
        items, files = [], 0
        for it in lookup.items(folder):
            if it["kind"] == "file":
                files += 1
                if files > CATALOGUE_LIMIT:
                    continue
            entry = it.get("entry")
            if entry is not None:  # (what a list shows of it: its first source, and GitHub's)
                entry = {k: entry[k] for k in ("kind", "hint", "github", "at") if k in entry} | \
                    {"sources": list(entry.get("sources") or ())[:1]}
            item = {**it, "entry": entry}
            try:
                wire.encode(item)
            except (TypeError, ValueError):  # (a file name that isn't UTF-8, say: that one left out)
                continue
            items.append(item)
        return {"cwd": cwd, "items": items, "busy": busy()}
