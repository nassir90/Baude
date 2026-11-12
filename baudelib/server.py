"""The headless server: Claude Code sessions started and steered over the network, each run headless (ClaudeProc) the
way Baude runs a chat: Claude's models in claude, Apple's in apple-claude, those on this machine in pi-claude
(baudelib.models says how each is started), whichever of them this machine has. A session is started with a prompt, a
folder, a model, an effort, a permission mode and a group, and gets a UUID that is its id from then on: claude's
--session-id,
and what it's resumed by. Its claude keeps going between turns, so whoever attaches next carries on with it, and stops
once nobody's used it for a while (idle_s): the next message resumes it. Clients (Baude) list the sessions, read their
transcripts and their subagents', rename them, attach to one to see what it does and to talk to it, and answer what it
asks. wire.py says how.

What it keeps (state_dir: the server's id, each session's settings) is its own; a claude session's transcript is Claude
Code's, and the server keeps one in the same form for a session whose program keeps none (apple-claude, pi-claude)."""
import base64
import fcntl
import getpass
import itertools
import json
import os
import queue
import select
import socket
import subprocess
import sys
import threading
import time
import uuid

from baudelib import claude, models, transcripts, wire
from baudelib.discovery import Advertiser

IDLE_S = 30 * 60
EXIT_S = 4 * 3600  # (nothing running and nobody attached this long: the host process goes; a head starts it again)
CHECK_SOCKET_S = 600  # (nobody attached: its socket looked at this often, in case its folder's been taken away)
TRANSCRIPT_CHUNK = 1 << 20
FILE_LIMIT = 4 << 20
RENAME_S = 15  # (what a claude asked to rename its session is given to answer: then it's done without it)
SUBAGENT_FILES = (".jsonl", ".meta.json")  # (in a subagents folder: each subagent's transcript, and what it's for)
# (what a client may set on a session whose claude isn't running: kept for when it runs, said to be done)
SETTINGS = {"set_model": ("model", "model"), "set_permission_mode": ("mode", "mode")}
# what start takes for claude's own flags, each run: (param, flag); and how much of an ask's input a listing shows
CLAUDE_OPTIONS = (("system", "--append-system-prompt"), ("add_dirs", "--add-dir"), ("allowed_tools", "--allowedTools"),
                  ("disallowed_tools", "--disallowedTools"))
ASK_SHOWN = 400


def _shown(value, depth=0):
    """An ask's input as a listing shows it: long strings cut short, deep or long things summed up."""
    if isinstance(value, str):
        return value if len(value) <= ASK_SHOWN else value[:ASK_SHOWN] + "…"
    if isinstance(value, dict) and depth < 3:
        return {k: _shown(v, depth + 1) for k, v in list(value.items())[:20]}
    if isinstance(value, list) and depth < 3:
        return [_shown(v, depth + 1) for v in value[:20]]
    return value if isinstance(value, (int, float, bool)) or value is None else f"({type(value).__name__})"


def state_home():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "server")


def lock_path(state_dir=None):
    """The lock a host process holds while it runs (one each): in its state folder, named for this machine."""
    return os.path.join(state_dir or state_home(), f"host-{wire.safe_name(socket.gethostname().split('.')[0])}.lock")


def running_pid(state_dir=None):
    """The pid of the host process running here, by its lock (held: one is), else None."""
    try:
        fd = os.open(lock_path(state_dir), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return None  # (not held: none's running)
        except OSError:
            pass
        text = os.read(fd, 32).decode(errors="replace").strip()
    finally:
        os.close(fd)
    if not text.isdigit():
        return None
    try:  # (its pid, not another's since: the lock's written as it's taken, and a pid's used again)
        ran = subprocess.run(["ps", "-o", "command=", "-p", text], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int(text) if "baude_headless.py" in ran else None


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
        self.subscribed, self.answers = False, True  # (answers: what its sessions ask, if it's attached to them)
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
        self.routes = {}          # request id sent on -> (conn, its own request id or the server's callback, undo)
        self.scan = self.info = self.stat = None  # the transcript: scan_session's state and info, (size, mtime)
        self.agents = None        # its subagents' files, as last looked at: {name: (size, mtime_ns)}
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

    def found_transcript(self):
        """Its transcript's path as it's been found (transcript, which the watch calls, finds it), without looking."""
        if self.kept():
            path = os.path.join(self.server.state_dir, "transcripts", f"{self.id}.jsonl")
        else:
            path = self.meta.get("path")
        return path if path and os.path.isfile(path) else None

    def subagents_dir(self, path=None):
        """The folder claude keeps its subagents' transcripts in, beside its own (path: that, if it's known); None for
        one the server keeps (its program has no subagents)."""
        path = None if self.kept() else path or self.transcript()
        return path[:-len(".jsonl")] + os.sep + "subagents" if path and path.endswith(".jsonl") else None

    def subagent_files(self, path=None):
        """Its subagents' files as they are now: {name: (size, mtime_ns)}."""
        out, folder = {}, self.subagents_dir(path)
        if folder is None:
            return out  # (not scandir(None): that's the folder the server's in)
        try:
            with os.scandir(folder) as found:
                for e in found:
                    if e.name.endswith(SUBAGENT_FILES) and not e.name.startswith(".") and e.is_file():
                        st = e.stat()
                        out[e.name] = (st.st_size, st.st_mtime_ns)
        except (OSError, TypeError):
            pass
        return out

    def listing(self):
        info = self.info or {}
        size, mtime = self.stat or (0, None)
        prompt = " ".join((self.meta.get("prompt") or "").split())
        return {"id": self.id, "title": self.meta.get("title") or info.get("title") or prompt[:90] or None,
                "cwd": self.meta.get("cwd"), "model": self.meta.get("model"), "effort": self.meta.get("effort"),
                "mode": self.meta.get("mode"), "group": self.meta.get("group"), "created": self.meta.get("created"),
                "said": max(info.get("said") or 0, self.meta.get("said") or 0) or self.meta.get("created"),
                "updated": mtime or self.meta.get("created"), "size": size, "state": self.state(),
                "prompts": info.get("prompts") or 0, "cli_id": self.meta.get("cli_id"),
                "transcript": self.found_transcript(),
                "subagents": max((m for _size, m in (self.agents or {}).values()), default=0),
                "asks": [{"request_id": rid, "tool": (m.get("request") or {}).get("tool_name"),
                          "input": _shown((m.get("request") or {}).get("input"))} for rid, m in self.asks.items()]}


class AlreadyRunning(OSError):
    """This machine's user has a host process already (its socket's taken): there's one each."""


class Server:
    """Serve sessions on a Unix socket (socket_path: wire.socket_path(), this user's one on this machine, unless
    another's given). advertise: say on the network that Baude is on this host, and the ssh user who'd reach it
    (True, False, or None: as it was last told, else not; remembered in its state)."""

    def __init__(self, state_dir=None, group=None, name=None, socket_path=None, exe=None, advertise=None,
                 idle_s=IDLE_S, log=None, env=None, exit_s=EXIT_S, on_change=None):
        self.state_dir = state_dir or state_home()
        self.exe, self.idle_s, self.env, self.exit_s = exe, idle_s, env, exit_s  # (env: more for its claudes; exit_s 0: never)
        self.on_change = on_change or (lambda: None)  # (what it lists changed: a menu bar's item, say; on its own thread)
        self.woken = threading.Event()  # (_watch: someone's attached, a claude started or stopped)
        self.log = log or (lambda text: print(f"baude-server: {text}", file=sys.stderr, flush=True))
        # one each: a lock in its state folder held while it runs, taken before anything's read or written there
        # (one beside the socket would be lost with the socket's folder, which a session's end can take: logind's)
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        host = socket.gethostname().split(".")[0]
        self.lock_file = os.open(lock_path(self.state_dir), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        # (a head looking at it, running_pid, holds it for a moment; one stopping, for some seconds: waited for)
        end = time.monotonic() + (10 if os.path.exists(self.state_dir + "/.stopping") else 0.5)
        while True:
            try:
                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > end:
                    os.close(self.lock_file)
                    raise AlreadyRunning(f"a host process is running here already ({self.state_dir})") from None
                time.sleep(0.05)
        try:
            os.unlink(self.state_dir + "/.stopping")
        except OSError:
            pass
        os.ftruncate(self.lock_file, 0)  # (who's running: running_pid's, for a head to tell it to listen again)
        os.write(self.lock_file, str(os.getpid()).encode())
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
        self.me["name"] = name or self.me.get("name") or host
        self.me["group"] = group or self.me.get("group") or self.me["name"]
        if advertise is not None:
            self.me["advertise"] = bool(advertise)
        self.advertise = bool(self.me.get("advertise"))
        _write_json(me, self.me)
        self.sessions = {}
        folder = os.path.join(self.state_dir, "sessions")
        for entry in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            try:
                with open(os.path.join(folder, entry), encoding="utf-8") as f:
                    meta = json.load(f)
                if not wire.is_id(meta.get("id")):
                    continue
                self.sessions[meta["id"]] = Session(self, meta)
                meta.pop("tags", None)  # (an older server kept tags: not read, and gone at its next save)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        self.conns = set()
        self.dirty = False  # (subscribers have yet to hear of a change: _watch)
        self.socket_path = socket_path or wire.socket_path()
        if len(self.socket_path.encode()) > 100:
            raise OSError(f"{self.socket_path}: too long a path for a socket")
        self.listener, self.accepted, self.binding = None, 0.0, threading.Lock()
        self.wake_r, self.wake_w = os.pipe()  # (the accept's waits woken: a new socket, or shut down)
        self.bind()
        self.advertiser = None
        self.stopped, self.closed = threading.Event(), threading.Event()  # (closed: shutdown's done, socket and all)

    def bind(self):
        """The socket listened on: made (again, if it's gone: its folder taken away, say), in a folder of its user's
        alone, where one left by a host process that's gone is taken over (the lock says none's running). One at a
        time (the watch, and a head's SIGUSR1, may both want it)."""
        with self.binding:
            self._bind()

    def _bind(self):
        wire.own_dir(os.path.dirname(self.socket_path))
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.socket_path)
        os.chmod(self.socket_path, 0o600)  # (its folder's its user's alone already: the socket's theirs too)
        listener.listen(16)
        self.bound = os.stat(self.socket_path).st_ino
        listener.setblocking(False)  # (accepted when select says there's one: never waited on there)
        old, self.listener = self.listener, listener
        if old is not None:
            os.write(self.wake_w, b"x")  # (the accept loop takes up the new one)
            old.close()

    def still_bound(self):
        """Is its socket still there (not taken away with its folder)? Bound again if it isn't (and if that fails,
        said, and tried again next time)."""
        with self.binding:
            try:
                if os.stat(self.socket_path).st_ino == self.bound:
                    return True
            except OSError:
                pass
            if not self.stopped.is_set():
                self.log(f"{self.socket_path} went: listening there again")
                try:
                    self._bind()
                except OSError as e:
                    self.log(f"couldn't listen there again: {e}")
        return False

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
        """Take connections till it's shut down."""
        threading.Thread(target=self._watch, daemon=True, name="watch").start()
        if self.advertise:  # (its port's ssh's: what's said is who'd reach it; no command, which no one should take
            # from the network)
            self.advertiser = Advertiser(self.me["name"], 22, {
                "id": self.me["id"], "group": self.me["group"], "v": wire.VERSION, "user": getpass.getuser()}).start()
        self.log(f"serving {len(self.sessions)} sessions on {self.socket_path}, group {self.me['group']!r}"
                 + ("" if self.advertise else ", not said on the network"))
        while not self.stopped.is_set():
            listener = self.listener
            try:
                ready, _, _ = select.select([listener, self.wake_r], [], [])  # (nothing done till there's something)
                if self.wake_r in ready:
                    os.read(self.wake_r, 64)
                    continue
                sock, _ = listener.accept()
            except BlockingIOError:
                continue  # (gone before it was taken)
            except (OSError, ValueError):
                if not self.stopped.is_set() and listener is self.listener:
                    time.sleep(0.1)  # (out of file descriptors, say: not a loop that spins)
                continue
            if self.stopped.is_set():
                sock.close()
                break
            sock.setblocking(True)  # (macOS's accept hands on the listener's non-blocking)
            self.accepted = time.monotonic()
            threading.Thread(target=self.serve, args=(sock,), daemon=True, name="client").start()
        self.listener.close()

    def shutdown(self):
        """Goodbye on the network, every claude stopped (asked to end, then made to: what it's written is kept, and
        resumed next time), the socket let go. Once."""
        with self.lock:
            if self.stopped.is_set():
                return
            self.stopped.set()
        self.woken.set()
        try:  # (one starting meanwhile waits for this one to go: Server)
            open(self.state_dir + "/.stopping", "w").close()
        except OSError:
            pass
        if self.advertiser is not None:
            self.advertiser.stop()
        with self.lock:
            procs = [s.proc for s in self.sessions.values() if s.proc is not None]
            for c in list(self.conns):
                c.close()
        for p in procs:
            p.stopping = True
            p.close_stdin()  # (claude, its stdin closed, ends as it would at the end of a pipe)
        end = time.monotonic() + 5
        for p in procs:
            try:
                p.p.wait(timeout=max(0.1, end - time.monotonic()))
            except Exception:  # (one that doesn't, in time: made to)
                p.kill()
        while time.monotonic() < end + 2:  # (what each said last, read and kept: its exit's handled once it's all read)
            with self.lock:
                if not any(s.proc in procs for s in self.sessions.values()):
                    break
            time.sleep(0.05)
        os.write(self.wake_w, b"x")  # (the accept loop sees it's stopped)
        try:
            if os.stat(self.socket_path).st_ino == self.bound:
                os.unlink(self.socket_path)
        except OSError:
            pass
        os.close(self.lock_file)
        self.closed.set()

    def serve(self, sock):
        sock.settimeout(wire.SILENT_S)  # (a client that's said nothing for this long is gone: they ping)
        ch = wire.Channel.over(sock)
        conn = Conn(ch)
        with self.lock:
            self.conns.add(conn)
        self.woken.set()  # (someone's here: watched for them)
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
        if self.stopped.is_set():  # (going: nothing more started, nothing more sent to a claude it's stopping)
            return reply("error", error="the host process is stopping")
        if op == "hello":
            return reply("hello", server=self.info())
        if op == "subscribe":
            conn.subscribed = True
            with self.lock:  # (as they are now: nothing's been read while nobody was attached)
                for s in self.sessions.values():
                    self.rescan(s)
            return reply("sessions", sessions=self.listing(), server=self.info())
        if op == "quit":  # (the host process itself: its claudes stopped, its sessions kept for next time)
            reply("quitting")
            threading.Thread(target=self.shutdown, daemon=True, name="quit").start()
            return None
        if op == "start":
            return self.start(msg, conn, req)
        if op == "read_file":
            return reply("file", **self.read_file(msg.get("path"), msg.get("limit")))
        with self.lock:
            s = self.sessions.get(msg.get("session"))
            if s is None:
                return reply("error", error=f"no session {msg.get('session')}")
            if op == "attach":
                conn.answers = msg.get("answers", True) is not False  # (false: it won't answer what's asked)
                s.viewers.add(conn)
                if msg.get("live"):
                    s.live.add(conn)
                return reply("attached", session=s.listing(), asks=list(s.asks.values()))
            if op == "detach":
                s.viewers.discard(conn)
                s.live.discard(conn)
                return None
            if op == "input":
                self.take_input(conn, s, msg.get("msg") or {})
                return reply("accepted", session=s.id) if req is not None else None
            if op == "stop":
                if s.proc is not None:
                    s.proc.stopping = True
                    s.proc.kill()
                return reply("stopped", session=s.id)
            if op == "transcript":
                return reply("transcript", session=s.id, **self.read_transcript(s, msg.get("offset") or 0,
                                                                               msg.get("limit")))
            if op == "subagents":
                return reply("subagents", session=s.id, **self.read_subagents(s, msg.get("have"), msg.get("limit")))
            if op == "rename":
                return self.rename(s, msg.get("title"), lambda: reply("renamed", session=s.listing()))
        return reply("error", error=f"unknown op {op!r}")

    # ---- sessions
    def start(self, params, conn=None, req=None):
        """A new session, as start asks (wire.py): conn attached live to it first if it asks to be (attach), and told
        started once it's begun (its claude started, its prompt sent): before claude says anything of it, which waits
        on the lock this holds. Anything wrong, and it's not kept."""
        model = params.get("model") or None
        with self.lock:
            src = self.sessions.get(params["fork_from"]) if params.get("fork_from") else None
        if params.get("fork_from") and (src is None or src.kept() or src.transcript() is None):
            raise ValueError(f"no conversation here to fork: {params['fork_from']}")
        cwd = os.path.realpath(os.path.expanduser(params.get("cwd") or (src.meta["cwd"] if src else "~")))
        if not os.path.isdir(cwd):
            raise ValueError(f"not a folder here: {cwd}")
        if src and cwd != os.path.realpath(src.meta["cwd"]):  # (claude finds what it resumes by its folder)
            raise ValueError(f"a fork runs in its session's folder: {src.meta['cwd']}")
        model = model or (src.meta.get("model") if src else None)
        run = models.launch(model, params.get("mode") or "default", params.get("effort") or None)
        if not self.can_run(model):
            raise ValueError(f"{self.me['name']} can't run {model}: it, or its program, isn't here")
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
        meta = {"id": str(uuid.uuid4()), "cwd": cwd, "model": model, "effort": run["effort"], "mode": run["mode"],
                "group": params.get("group") or self.me["group"], "title": params.get("title") or None,
                "prompt": params.get("prompt") or "", "created": time.time(), "options": options,
                "fork_from": (src.meta.get("cli_id") or src.id) if src else None,
                "ask_timeout": float(params["ask_timeout"]) if params.get("ask_timeout") else None,
                "ask_default": params.get("ask_default") or "deny"}
        with self.lock:
            s = Session(self, meta)
            self.sessions[s.id] = s
            self.save(s)
            if conn is not None and params.get("attach"):
                conn.answers = params.get("answers", True) is not False
                s.viewers.add(conn)
                s.live.add(conn)
            try:
                if meta["prompt"].strip():
                    self.ensure_proc(s)
                    self.send_user(s, meta["prompt"])
            except Exception:  # (its claude couldn't start: no session, and the error's the answer)
                del self.sessions[s.id]
                if s.proc is not None:
                    s.proc.kill()
                try:
                    os.remove(os.path.join(self.state_dir, "sessions", f"{s.id}.json"))
                except OSError:
                    pass
                raise
            if conn is not None:
                conn.send({"op": "started", "req": req, "session": s.listing()})
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
            self.drop_routes(s)
        if s.proc is not None:
            return s.proc
        resumed = s.transcript() is not None
        run = models.launch(s.meta.get("model"), s.meta.get("mode"), s.meta.get("effort"),
                            s.meta.get("cli_id") or s.id, resumed=resumed)
        if not self.can_run(s.meta.get("model")):
            raise OSError(f"{self.me['name']} can't run {s.meta.get('model')}: it, or its program, isn't here")
        extra = list(run["extra"])
        if run["program"] == "claude":
            if s.meta.get("fork_from") and not resumed:  # (its first run: a copy of the other's conversation, which
                run["resume"], extra = s.meta["fork_from"], ["--fork-session"]  # claude names itself: cli_id)
            for key, flag in CLAUDE_OPTIONS:
                value = (s.meta.get("options") or {}).get(key)
                if value:
                    extra += [flag, *(value if isinstance(value, list) else [value])]
        made = []  # (the claude its exit is about: claude_exited)
        s.proc = claude.ClaudeProc(
            lambda p, m: self.from_claude(s, p, m), lambda code: self.claude_exited(s, made, code), s.meta["cwd"],
            s.meta.get("model"), run["mode"], run["resume"], run["effort"], extra=extra, env=self.env,
            exe=run["exe"] if run["program"] != "claude" else self.exe)
        made.append(s.proc)
        s.used = time.time()
        self.changed()  # (running: listed so, and the watch waits for it to go idle)
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
        sub, undo = request.get("subtype"), None
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
            undo = (key, s.meta.get(key), request.get(field) or s.meta.get(key))  # (put back if claude says no)
            s.meta[key] = undo[2]
        elif sub == "apply_flag_settings" and "effortLevel" in (request.get("settings") or {}):
            s.meta["effort"] = request["settings"]["effortLevel"]
        self.save(s)
        if s.proc is None or getattr(s.proc, "stopping", False):  # (stopped: answered from what's kept, for when it
            self.changed()                                          # runs; only a message starts it)
            if sub == "get_settings":
                return answer({"applied": {"model": s.meta.get("model"), "effort": s.meta.get("effort")}})
            return answer({})
        on = f"srv_{next(self.rids)}"
        s.routes[on] = (conn, rid, undo)
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
                    conn, theirs, undo = s.routes.pop(rid)
                    if undo and r.get("subtype") == "error" and s.meta.get(undo[0]) == undo[2]:
                        s.meta[undo[0]] = undo[1]  # (refused, a mode it can't switch to say: kept as it still is)
                        self.save(s)
                        self.changed()
                    if callable(theirs):  # (the server's own request: rename)
                        theirs(r.get("subtype") == "success")
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
                s.asks[msg.get("request_id")] = msg
                if s.meta.get("ask_timeout"):
                    self.ask_timer(s, proc, msg.get("request_id"))
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
                ended = {"op": "turn_ended", "session": s.id, "result": msg.get("result"),
                         "is_error": bool(msg.get("is_error")), "cost": msg.get("total_cost_usd"),
                         "turns": msg.get("num_turns"), "duration_ms": msg.get("duration_ms")}
                for conn in self.conns:
                    if conn.subscribed:  # (as it happens: a turn too short for a sessions listing to see isn't missed)
                        conn.send(ended)
            for conn in s.live:
                conn.send({"op": "claude", "session": s.id, "msg": msg})

    def ask_timer(self, s, proc, rid):
        """(lock held) A permission asked, answered as the session says (ask_default) once it's waited ask_timeout with
        nobody attached to answer it (someone's there: they're given as long again)."""
        def due():
            with self.lock:
                if rid not in s.asks or proc is not s.proc:
                    return
                if any(v.answers for v in s.viewers):  # (someone's there who answers: given as long again)
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
            s.agents = None  # (what its subagents wrote as it went: looked at again)
            for conn, theirs in s.waiting_init:
                conn.send({"op": "claude", "session": s.id, "msg": {"type": "control_response", "response": {
                    "subtype": "error", "request_id": theirs, "error": f"claude exited ({code})"}}})
            s.waiting_init = []
            self.drop_routes(s)
            if not getattr(made[0], "restarting", False):
                for conn in s.live:
                    conn.send({"op": "exited", "session": s.id, "code": code})
            self.changed()

    def drop_routes(self, s):
        """(lock held) What was asked of a session's claude that's gone: no answer's coming (the server's own requests
        are done without one)."""
        routes, s.routes = s.routes, {}
        for _conn, theirs, _undo in routes.values():
            if callable(theirs):
                theirs(False)

    def rename(self, s, title, done):
        """(lock held) A session's title, as Claude Code keeps it: its claude's to write in its transcript if it's
        running (rename_session); else the record claude writes for a rename, put in its transcript here; the server's
        own for a transcript it keeps (or before there's any). done() once it is."""
        title = " ".join(str(title or "").split())[:200]
        if not title:
            raise ValueError("a title can't be empty")

        def write(by_claude):
            path = None if s.kept() else s.transcript()
            if path and not by_claude:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"type": "custom-title", "customTitle": title,
                                        "sessionId": s.meta.get("cli_id") or s.id},
                                       separators=(",", ":"), ensure_ascii=False) + "\n")
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
                if not running and self.exit_s:
                    waits.append(quiet + self.exit_s - time.monotonic())
                elif running:
                    quiet = time.monotonic()  # (something's running: not quiet yet)
                self.woken.wait(max(1.0, min(waits)))
                self.woken.clear()
                quiet = max(quiet, self.accepted)
                with self.lock:
                    now_running = any(s.proc is not None for s in self.sessions.values())
                if not now_running and self.exit_s and time.monotonic() - quiet >= self.exit_s and not self.conns:
                    self.log(f"nothing running and nobody here for {self.exit_s / 3600:g} h: going")
                    threading.Thread(target=self.shutdown, daemon=True, name="exit").start()
                    return
            if self.stopped.is_set():
                return
            if time.monotonic() - checked > (10 if attached else CHECK_SOCKET_S - 1):
                checked = time.monotonic()
                self.still_bound()
            try:
                pinged = self._look(pinged)
            except Exception as e:  # (whatever it was, it's looked at again next time)
                self.log(f"watching: {type(e).__name__}: {e}")

    def stoppable(self, s):
        """(lock held) Would an idle look stop a session's claude, once it's idle long enough (_look)? Not one at a
        turn, asking, watched live, stopping already, or whose program forgets what it's said (apple-claude)."""
        return s.proc is not None and not s.busy and not s.asks and not s.live and \
            not getattr(s.proc, "stopping", False) and models.program(s.meta.get("model"))["resumes"]

    def _look(self, pinged):
        """One look (_watch): (when subscribers were last pinged)."""
        with self.lock:
            for s in list(self.sessions.values()):
                grew = self.rescan(s)
                self.dirty |= grew
                if s.agents is None or s.proc is not None or grew:  # (only a running claude's subagents work)
                    if (agents := s.subagent_files(s.meta.get("path"))) != s.agents:
                        s.agents, self.dirty = agents, True
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
