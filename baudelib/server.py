"""The headless server: Claude Code sessions started and steered over the network, each run headless (ClaudeProc) the
way Baude runs a chat: Claude's models in claude, Apple's in apple-claude, those on this machine in pi-claude
(baudelib.models says how each is started), whichever of them this machine has. A session is started with a prompt, a folder, a model,
an effort, a permission mode, tags and a group, and gets a UUID that is its id from then on: claude's --session-id,
and what it's resumed by. Its claude keeps going between turns, so whoever attaches next carries on with it, and stops
once nobody's used it for a while (idle_s): the next message resumes it. Clients (Baude) list the sessions, read their
transcripts, attach to one to see what it does and to talk to it, and answer what it asks. wire.py says how.

What it keeps (state_dir: the server's id, each session's settings) is its own; a claude session's transcript is Claude
Code's, and the server keeps one in the same form for a session whose program keeps none (apple-claude, pi-claude)."""
import base64
import itertools
import json
import os
import queue
import socket
import socketserver
import sys
import threading
import time
import uuid

from baudelib import claude, models, transcripts, wire
from baudelib.discovery import Advertiser

IDLE_S = 30 * 60
TRANSCRIPT_CHUNK = 1 << 20
FILE_LIMIT = 4 << 20
# (what a client may set on a session whose claude isn't running: kept for when it runs, said to be done)
SETTINGS = {"set_model": ("model", "model"), "set_permission_mode": ("mode", "mode")}


def state_home():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "server")


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


class Conn:
    """A client's connection: what it's sent goes out on a thread of its own, so a slow one holds nobody up."""

    def __init__(self, channel):
        self.channel, self.out = channel, queue.Queue()
        self.subscribed = False
        threading.Thread(target=self._write, daemon=True, name="client-out").start()

    def send(self, msg):
        self.out.put(msg)

    def _write(self):
        while (msg := self.out.get()) is not None:
            if not self.channel.send(msg):
                break

    def close(self):
        self.out.put(None)


class Session:
    def __init__(self, server, meta):
        self.server, self.meta = server, meta
        self.proc = None
        self.viewers, self.live = set(), set()  # attached Conns; those that see what claude says as it says it
        self.asks = {}            # claude's request id -> a can_use_tool it sent, not answered yet
        self.busy = False         # at a turn
        self.waiting_init = []    # (conn, its request id): initialize asked before claude answered its own
        self.queued = []          # messages sent at a turn, kept once it's over (Server.keep)
        self.routes = {}          # request id sent on -> (conn, its own request id)
        self.scan = self.info = self.stat = None  # the transcript: scan_session's state and info, (size, mtime)
        self.used = time.time()

    @property
    def id(self):
        return self.meta["id"]

    def state(self):
        if self.proc is None:
            return "stopped"
        return "waiting" if self.asks else "working" if self.busy else "idle"

    def kept(self):
        """Is its transcript the server's (its program keeps none)?"""
        return not models.program(self.meta.get("model"))["transcript"]

    def transcript(self):
        """Its transcript, where it is (None before there's one): Claude Code's, or the server's (kept)."""
        if self.kept():
            path = os.path.join(self.server.state_dir, "transcripts", f"{self.id}.jsonl")
            return path if os.path.exists(path) else None
        path = self.meta.get("path")
        if path and os.path.exists(path):
            return path
        path = transcripts.session_file(self.meta.get("cli_id") or self.id)
        if path and path != self.meta.get("path"):
            self.meta["path"] = path
            self.server.save(self)
        return path

    def listing(self):
        info = self.info or {}
        size, mtime = self.stat or (0, None)
        prompt = " ".join((self.meta.get("prompt") or "").split())
        return {"id": self.id, "title": self.meta.get("title") or info.get("title") or prompt[:90] or None,
                "cwd": self.meta.get("cwd"), "model": self.meta.get("model"), "effort": self.meta.get("effort"),
                "mode": self.meta.get("mode"), "tags": list(self.meta.get("tags") or []),
                "group": self.meta.get("group"), "created": self.meta.get("created"),
                "said": max(info.get("said") or 0, self.meta.get("said") or 0) or self.meta.get("created"),
                "updated": mtime or self.meta.get("created"), "size": size, "state": self.state(),
                "prompts": info.get("prompts") or 0}


class Server:
    """Serve sessions on host:port (0: any free port), and say so on the network (advertise)."""

    def __init__(self, state_dir=None, group=None, name=None, host="0.0.0.0", port=0, exe=None, advertise=True,
                 idle_s=IDLE_S, log=None, env=None):
        self.state_dir = state_dir or state_home()
        self.exe, self.idle_s, self.advertise, self.env = exe, idle_s, advertise, env  # (env: more for its claudes)
        self.log = log or (lambda text: print(f"baude-server: {text}", file=sys.stderr, flush=True))
        self.lock = threading.RLock()
        self.rids = itertools.count(1)
        me = os.path.join(self.state_dir, "server.json")
        try:
            with open(me, encoding="utf-8") as f:
                self.me = json.load(f)
        except (OSError, ValueError):
            self.me = {}
        if not self.me.get("id"):
            self.me["id"] = str(uuid.uuid4())
        self.me["name"] = name or self.me.get("name") or socket.gethostname().split(".")[0]
        self.me["group"] = group or self.me.get("group") or self.me["name"]
        _write_json(me, self.me)
        self.sessions = {}
        folder = os.path.join(self.state_dir, "sessions")
        for entry in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            try:
                with open(os.path.join(folder, entry), encoding="utf-8") as f:
                    meta = json.load(f)
                self.sessions[meta["id"]] = Session(self, meta)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        self.conns = set()
        self.dirty = False  # (subscribers have yet to hear of a change: _watch)
        server = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                server.serve(self.request)

        class TCP(socketserver.ThreadingTCPServer):
            allow_reuse_address, daemon_threads = True, True

        self.tcp = TCP((host, port), Handler)
        self.port = self.tcp.server_address[1]
        self.advertiser = None
        self.stopped = threading.Event()

    # ---- what it is
    def info(self):
        with self.lock:
            return self._info()

    def models(self, said=None, fresh=False):
        """Every model it can run, as initialize lists them: Claude's (as claude last said; said, if it's just said it:
        fresh), then Apple's and those on this machine that it has."""
        claudes = [m for m in said or [] if models.model_program(m.get("value")) == "claude"] if fresh else []
        if claudes and claudes != self.me.get("claude_models"):
            self.me["claude_models"] = claudes
            _write_json(os.path.join(self.state_dir, "server.json"), self.me)
        return (claudes or self.me.get("claude_models") or []) + models.runnable_models()

    def init(self, said, fresh=False):
        """What a session's program said to initialize (fresh: just now, not kept from before), with every model this
        machine can run in it."""
        return {**said, "models": self.models(said.get("models"), fresh)}

    def can_run(self, model):
        """Can this machine run a model: Claude's (claude's own say), or one of runnable_models (its program and, for
        pi's, the model itself here)?"""
        if models.model_program(model) == "claude":
            return not models.launch(model, None, None)["missing"]
        return any(m["value"] == model for m in models.runnable_models())

    def _info(self):
        return {"id": self.me["id"], "name": self.me["name"], "group": self.me["group"], "version": wire.VERSION,
                "models": [{"value": m["value"], "displayName": m.get("displayName") or m["value"]}
                           for m in self.models()],
                "host": socket.gethostname(), "home": os.path.expanduser("~"), "pid": os.getpid(),
                "groups": sorted({s.meta.get("group") or self.me["group"] for s in self.sessions.values()}
                                 | {self.me["group"]})}

    def save(self, s):
        _write_json(os.path.join(self.state_dir, "sessions", f"{s.id}.json"), s.meta)

    def listing(self):
        with self.lock:
            return sorted((s.listing() for s in self.sessions.values()), key=lambda i: -(i["said"] or 0))

    # ---- running
    def serve_forever(self):
        threading.Thread(target=self._watch, daemon=True, name="watch").start()
        if self.advertise:
            self.advertiser = Advertiser(self.me["name"], self.port, {
                "id": self.me["id"], "group": self.me["group"], "v": wire.VERSION}).start()
        self.log(f"serving {len(self.sessions)} sessions on port {self.port}, group {self.me['group']!r}")
        self.tcp.serve_forever(poll_interval=0.25)

    def shutdown(self):
        """Goodbye on the network, every claude stopped, the port let go."""
        self.stopped.set()
        if self.advertiser is not None:
            self.advertiser.stop()
        with self.lock:
            procs = [s.proc for s in self.sessions.values() if s.proc is not None]
            for c in list(self.conns):
                c.close()
        for p in procs:
            p.kill()
        self.tcp.shutdown()
        self.tcp.server_close()

    def serve(self, sock):
        sock.settimeout(wire.SILENT_S)  # (a client that's said nothing for this long is gone: they ping)
        ch = wire.Channel.over(sock)
        conn = Conn(ch)
        with self.lock:
            self.conns.add(conn)
        try:
            for msg in ch:
                try:
                    self.handle(conn, msg)
                except Exception as e:  # (one bad request doesn't end the connection)
                    conn.send({"op": "error", "req": msg.get("req"), "error": f"{type(e).__name__}: {e}"})
        finally:
            with self.lock:
                self.conns.discard(conn)
                for s in self.sessions.values():
                    s.viewers.discard(conn)
                    s.live.discard(conn)
                    s.waiting_init = [w for w in s.waiting_init if w[0] is not conn]
                    s.routes = {k: v for k, v in s.routes.items() if v[0] is not conn}
            conn.close()
            ch.close()

    def handle(self, conn, msg):
        op, req = msg.get("op"), msg.get("req")
        reply = lambda kind, **kw: conn.send({"op": kind, "req": req, **kw})  # noqa: E731
        if op == "ping":
            return None
        if op == "hello":
            return reply("hello", server=self.info())
        if op == "subscribe":
            conn.subscribed = True
            return reply("sessions", sessions=self.listing(), server=self.info())
        if op == "start":
            return reply("started", session=self.start(msg).listing())
        if op == "read_file":
            return reply("file", **self.read_file(msg.get("path"), msg.get("limit")))
        with self.lock:
            s = self.sessions.get(msg.get("session"))
            if s is None:
                return reply("error", error=f"no session {msg.get('session')}")
            if op == "attach":
                s.viewers.add(conn)
                if msg.get("live"):
                    s.live.add(conn)
                return reply("attached", session=s.listing(), asks=list(s.asks.values()))
            if op == "detach":
                s.viewers.discard(conn)
                s.live.discard(conn)
                return None
            if op == "input":
                return self.take_input(conn, s, msg.get("msg") or {})
            if op == "stop":
                if s.proc is not None:
                    s.proc.stopping = True
                    s.proc.kill()
                return reply("stopped", session=s.id)
            if op == "transcript":
                return reply("transcript", session=s.id, **self.read_transcript(s, msg.get("offset") or 0,
                                                                               msg.get("limit")))
        return reply("error", error=f"unknown op {op!r}")

    # ---- sessions
    def start(self, params):
        cwd = os.path.realpath(os.path.expanduser(params.get("cwd") or "~"))
        if not os.path.isdir(cwd):
            raise ValueError(f"not a folder here: {cwd}")
        tags = params.get("tags") or []
        run = models.launch(params.get("model") or None, params.get("mode") or "default", params.get("effort") or None)
        if not self.can_run(params.get("model") or None):
            raise ValueError(f"{self.me['name']} can't run {params.get('model')}: it, or its program, isn't here")
        meta = {"id": str(uuid.uuid4()), "cwd": cwd, "model": params.get("model") or None,
                "effort": run["effort"], "mode": run["mode"],
                "tags": [str(t) for t in (tags if isinstance(tags, list) else [tags]) if str(t).strip()],
                "group": params.get("group") or self.me["group"], "title": params.get("title") or None,
                "prompt": params.get("prompt") or "", "created": time.time()}
        with self.lock:
            s = Session(self, meta)
            self.sessions[s.id] = s
            self.save(s)
            if meta["prompt"].strip():
                self.ensure_proc(s)
                self.send_user(s, meta["prompt"])
            self.log(f"started {s.id} in {cwd} ({meta['group']})")
            self.changed()
        return s

    def ensure_proc(self, s):
        """(lock held) The session's claude, started if it isn't running: with its id until it's written a transcript,
        resumed after. One being stopped is let go of first (what's sent now is the next one's)."""
        if s.proc is not None and getattr(s.proc, "stopping", False):
            old, s.proc, s.busy = s.proc, None, False
            old.kill()
            try:
                old.p.wait(5)
            except Exception:  # (still going: a claude of its own, about to go)
                pass
        if s.proc is not None:
            return s.proc
        run = models.launch(s.meta.get("model"), s.meta.get("mode"), s.meta.get("effort"),
                            s.meta.get("cli_id") or s.id, resumed=s.transcript() is not None)
        if not self.can_run(s.meta.get("model")):
            raise OSError(f"{self.me['name']} can't run {s.meta.get('model')}: it, or its program, isn't here")
        made = []  # (the claude its exit is about: claude_exited)
        s.proc = claude.ClaudeProc(
            lambda p, m: self.from_claude(s, p, m), lambda code: self.claude_exited(s, made, code), s.meta["cwd"],
            s.meta.get("model"), run["mode"], run["resume"], run["effort"], extra=run["extra"], env=self.env,
            exe=run["exe"] if run["program"] != "claude" else self.exe)
        made.append(s.proc)
        s.used = time.time()
        return s.proc

    def send_user(self, s, content):
        s.proc.user(content)
        self.keep(s, "user", {"role": "user", "content": content})
        s.busy, s.used = True, time.time()
        s.meta["said"] = time.time()
        self.save(s)

    def take_input(self, conn, s, msg):
        """(lock held) What a client sends its claude."""
        kind = msg.get("type")
        if kind == "user":
            at_turn = s.busy
            self.ensure_proc(s).send(msg)
            if at_turn and s.kept():  # (taken after the turn it's sent in: kept after it too)
                s.queued.append(msg.get("message") or {})
            else:
                self.keep(s, "user", msg.get("message") or {})
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
        sub = request.get("subtype")
        answer = lambda response: conn.send({"op": "claude", "session": s.id, "msg": {  # noqa: E731
            "type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": response}}})
        if sub == "initialize":
            if s.meta.get("init") is not None and (s.proc is None or s.proc.init_rid is None):
                return answer(self.init({**s.meta["init"], "current_permission_mode": s.meta.get("mode") or "default"}))
            s.waiting_init.append((conn, rid))
            self.ensure_proc(s)
            return
        if sub == "set_model" and request.get("model") and \
                models.model_program(request["model"]) != models.model_program(s.meta.get("model")):
            return conn.send({"op": "claude", "session": s.id, "msg": {"type": "control_response", "response": {
                "subtype": "error", "request_id": rid,
                "error": "another kind of model can't carry this conversation on: start a session with it"}}})
        if sub == "set_model" and models.model_program(request.get("model")) == "pi" and s.proc is not None \
                and request.get("model") != s.meta.get("model"):
            s.proc.stopping = s.proc.restarting = True  # (pi-claude runs one model: the next message starts it on
            s.proc.kill()                               # this one, pi's session going on: nothing's gone)
        if sub in SETTINGS:
            key, field = SETTINGS[sub]
            s.meta[key] = request.get(field) or s.meta.get(key)
        elif sub == "apply_flag_settings" and "effortLevel" in (request.get("settings") or {}):
            s.meta["effort"] = request["settings"]["effortLevel"]
        self.save(s)
        if s.proc is None or getattr(s.proc, "stopping", False):  # (stopped: answered from what's kept, for when it
            self.changed()                                          # runs; only a message starts it)
            if sub == "get_settings":
                return answer({"applied": {"model": s.meta.get("model"), "effort": s.meta.get("effort")}})
            return answer({})
        on = f"srv_{next(self.rids)}"
        s.routes[on] = (conn, rid)
        self.ensure_proc(s).send({**msg, "request_id": on})
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
                        r = {**r, "response": self.init(s.meta["init"], fresh=True)}
                    for conn, theirs in s.waiting_init:
                        conn.send({"op": "claude", "session": s.id,
                                   "msg": {**msg, "response": {**r, "request_id": theirs}}})
                    s.waiting_init = []
                elif rid in s.routes:
                    conn, theirs = s.routes.pop(rid)
                    conn.send({"op": "claude", "session": s.id, "msg": {**msg, "response": {**r, "request_id": theirs}}})
                return
            if kind == "control_request":
                request = msg.get("request") or {}
                if request.get("subtype") != "can_use_tool":
                    proc.send({"type": "control_response", "response": {
                        "subtype": "error", "request_id": msg.get("request_id"),
                        "error": f"baude-server: unsupported {request.get('subtype')}"}})
                    return
                s.asks[msg.get("request_id")] = msg
                for conn in s.viewers:
                    conn.send({"op": "claude", "session": s.id, "msg": msg})
                self.changed()
                return
            if kind == "system" and msg.get("subtype") == "init" and msg.get("session_id") \
                    and msg["session_id"] != (s.meta.get("cli_id") or s.id):
                s.meta["cli_id"] = msg["session_id"]  # (claude went on in a transcript of another id)
                s.meta.pop("path", None)
                self.save(s)
            if kind in ("stream_event", "assistant") and not msg.get("parent_tool_use_id") and not s.busy:
                s.busy = True  # (a turn claude started itself: a background task's end, say)
                self.changed()
            if kind in ("assistant", "user") and not msg.get("parent_tool_use_id"):
                self.keep(s, kind, msg.get("message") or {})
            if kind == "result":
                for message in s.queued:
                    self.keep(s, "user", message)
                s.queued = []
                s.busy, s.used = False, time.time()
                s.asks.clear()
                self.changed()
            for conn in s.live:
                conn.send({"op": "claude", "session": s.id, "msg": msg})

    def keep(self, s, kind, message):
        """(lock held) A message of a session whose program keeps no transcript, kept in the server's, as Claude Code
        keeps one (what's listed, and what clients mirror, come from it)."""
        if not s.kept():
            return
        path = os.path.join(self.state_dir, "transcripts", f"{s.id}.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec = {"parentUuid": None, "isSidechain": False, "type": kind, "uuid": str(uuid.uuid4()),
               "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()), "sessionId": s.id,
               "cwd": s.meta.get("cwd"), "entrypoint": "baude-server", "message": message}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")

    def claude_exited(self, s, made, code):
        with self.lock:
            if not made or s.proc is not made[0]:
                return  # (one let go of already: ensure_proc)
            s.proc, s.busy = None, False
            s.asks.clear()
            for conn, theirs in s.waiting_init:
                conn.send({"op": "claude", "session": s.id, "msg": {"type": "control_response", "response": {
                    "subtype": "error", "request_id": theirs, "error": f"claude exited ({code})"}}})
            s.waiting_init, s.routes = [], {}
            if not getattr(made[0], "restarting", False):
                for conn in s.live:
                    conn.send({"op": "exited", "session": s.id, "code": code})
            self.changed()

    def changed(self):
        """(lock held) Every subscriber gets the sessions as they are now (soon: a burst of changes is one)."""
        self.dirty = True

    def _watch(self):
        """Once a second: transcripts that grew are read (what's listed comes from them), idle claudes are stopped, and
        subscribers hear what changed, or a ping."""
        pinged = 0.0
        while not self.stopped.wait(0.5):
            try:
                pinged = self._look(pinged)
            except Exception as e:  # (whatever it was, it's looked at again next time)
                self.log(f"watching: {type(e).__name__}: {e}")

    def _look(self, pinged):
        """One look (_watch): (when subscribers were last pinged)."""
        with self.lock:
            for s in list(self.sessions.values()):
                path = s.transcript()
                try:
                    st = os.stat(path) if path else None
                except OSError:
                    st = None
                stat = (st.st_size, st.st_mtime) if st else None
                if stat != s.stat:
                    if st and s.stat and st.st_size < s.stat[0]:
                        s.scan = None  # (rewritten: read again)
                    s.stat = stat
                    if path and st:
                        s.scan, s.info = transcripts.scan_session(path, s.scan)
                    self.dirty = True
                if s.proc is not None and not s.busy and not s.asks and not s.live \
                        and time.time() - s.used > self.idle_s and not getattr(s.proc, "stopping", False) \
                        and models.program(s.meta.get("model"))["resumes"]:  # (apple-claude would forget it all)
                    self.log(f"{s.id} idle: its claude stopped")
                    s.proc.stopping = True
                    s.proc.close_stdin()
                    threading.Timer(10, s.proc.kill).start()
                    s.used = time.time()
            send, self.dirty = self.dirty, False
            conns = list(self.conns)
        if send:
            msg = {"op": "sessions", "sessions": self.listing(), "server": self.info()}
            for c in conns:
                if c.subscribed:
                    c.send(msg)
        if time.monotonic() - pinged >= wire.PING_S:  # (every connection: a client takes silence for gone)
            pinged = time.monotonic()
            for c in conns:
                c.send({"op": "ping"})
        return pinged

    # ---- reading
    def read_transcript(self, s, offset, limit=None):
        """Whole lines of the transcript from offset: {offset, next, size, data}."""
        limit = max(4096, min(int(limit or TRANSCRIPT_CHUNK), 16 << 20))
        path = s.transcript()
        try:
            with open(path, "rb") as f:
                size = f.seek(0, os.SEEK_END)
                offset = 0 if offset > size else int(offset)
                f.seek(offset)
                data = f.read(limit)
                while data and not data.endswith(b"\n") and len(data) < (64 << 20):
                    more = f.read(limit)  # (a line longer than a chunk: all of it)
                    if not more:
                        break
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
            return {"path": path, "error": "not an absolute path"}
        real = os.path.realpath(path)
        with self.lock:
            folders = {os.path.realpath(s.meta["cwd"]) for s in self.sessions.values() if s.meta.get("cwd")}
        if not any(real == f or real.startswith(f.rstrip(os.sep) + os.sep) for f in folders):
            return {"path": path, "error": "not in one of its sessions' folders"}
        try:
            with open(path, "rb") as f:
                data = f.read(min(int(limit or FILE_LIMIT), FILE_LIMIT) + 1)
            size = os.path.getsize(path)
        except OSError as e:
            return {"path": path, "error": e.strerror or str(e)}
        return {"path": path, "size": size, "truncated": len(data) > FILE_LIMIT,
                "data": base64.b64encode(data[:FILE_LIMIT]).decode("ascii")}
