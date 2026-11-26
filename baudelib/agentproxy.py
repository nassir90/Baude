"""An agent run on another host of the mesh: a model this side can't reach as an API (Apple's on-device model, MLX's),
run by its program (apple-claude, pi-claude) on the host that has it, with its tools on the chat's host. The program
there has none of its own: its tools are an MCP server's here (baudelib.hosttools: Read, Write, Edit, Bash, Grep and
Glob, in the chat's folder), and any other MCP server the caller gives it (Meta's) runs here too, each relayed to it
through the mesh. What it says is claude's stream-json, as if it ran here.

A model of another host's is "agent/<member id>/<its value there>" (value, split). This side (ProxiedProc, a ClaudeProc
for the caller: the chat host's host process, or Meta in an app) reaches the member through this machine's host
process (wire.reach, via mesh) and asks it to run the program (op agent: Run, on that side). Then:
- in {line}: a line for the program's stdin; close: its stdin closed; kill: it stopped;
- out {line}, err {line}: what it says, on stdout and stderr; exit {code}: it's gone;
- mcp_open {conn, name}: the program's started an MCP server (a relay, baude_headless.py mcp-relay, on that side): it's
  started here; mcp {conn, line}: a line for the one or the other; mcp_close {conn}: it's gone, on either side.

A pi model's session is the chat's host's: a copy of its file goes with op agent (sessions) for pi to take it up from,
and what pi adds to it comes back as it's added, as pi-claude's baude_pi_session lines (session.ts, a pi extension),
kept here by PiSession, not passed on.
"""
import base64
import itertools
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading

from baudelib import hosttools, models, wire, wording
from baudelib.claude import ClaudeProc, claude_env

PREFIX = "agent/"
HEADLESS = os.path.join(models.REPO, "baude_headless.py")
NAMED = ("--system-prompt", "--append-system-prompt", "--tools", "--mcp-config", "--session-id")  # (the flags of a
# caller's taken, with a value)


def value(member, inner):
    """The model of member's that's inner there, run as an agent of its (inner: "apple", an MLX model's value)."""
    return f"{PREFIX}{member}/{inner}"


def split(model):
    """(member, its value there) of an agent's model, None for any other."""
    if not isinstance(model, str) or not model.startswith(PREFIX):
        return None
    member, _, inner = model[len(PREFIX):].partition("/")
    return (member, inner) if wire.is_id(member) and inner else None


def flags(extra):
    """A caller's extra flags as {flag: its value, or True}, of those taken (NAMED, --no-session-persistence)."""
    out, extra = {}, list(extra or ())
    for i, a in enumerate(extra):
        if a in NAMED and i + 1 < len(extra):
            out[a] = extra[i + 1]
        elif a == "--no-session-persistence":
            out[a] = True
    return out


def mcp_servers(raw):
    """--mcp-config's stdio servers ({name: {command, args, env}}): its JSON, or the file it names."""
    if not raw:
        return {}
    try:
        cfg = json.loads(raw) if raw.lstrip().startswith("{") else json.load(open(raw))
    except (OSError, ValueError):
        return {}
    return {n: s for n, s in (cfg.get("mcpServers") or {}).items()
            if isinstance(s, dict) and s.get("command") and wire.is_id(n) and n != hosttools.NAME}


HOST_TOOL = f"mcp__{hosttools.NAME}__"


def bare(msg):
    """What the program says, its tools on the chat's host named as they'd be if they ran here (mcp__host__Read: Read),
    as the caller allows, asks and shows them."""
    if isinstance(msg, dict):
        return {k: (v[len(HOST_TOOL):] if k in ("name", "tool_name") and isinstance(v, str) and
                    v.startswith(HOST_TOOL) else bare(v)) for k, v in msg.items()}
    if isinstance(msg, list):
        return [v[len(HOST_TOOL):] if isinstance(v, str) and v.startswith(HOST_TOOL) else bare(v) for v in msg]
    return msg


SESSIONS_MAX = 64 << 20  # (bytes of pi's session carried with an agent, at most)


def pi_sessions():
    """Where pi-claude keeps pi's sessions on this machine, a chat's those whose names have its id in them (pi's
    <when>_<id>.jsonl)."""
    return os.path.expanduser("~/.local/share/baude/pi/sessions")


def _within(rel, ids):
    """Is rel a path under one of a session's entries (its name with one of ids in it), going nowhere else?"""
    parts = rel.replace("\\", "/").split("/") if isinstance(rel, str) else []
    return bool(parts) and not os.path.isabs(rel) and all(p not in ("", ".", "..") for p in parts) and \
        any(i in parts[0] for i in ids)


def retarget(data, cwd):
    """A pi session file's bytes, said to be cwd's: pi takes up a session by its id only where it's run in the folder
    its first line says (with --session-dir), so it's said to be the folder it's run in wherever it's carried to."""
    first, nl, rest = data.partition(b"\n")
    try:
        head = json.loads(first)
    except ValueError:
        return data
    if not isinstance(head, dict) or head.get("type") != "session" or not cwd:
        return data
    return json.dumps({**head, "cwd": cwd}, separators=(",", ":"), ensure_ascii=False).encode() + nl + rest


def pack(root, ids):
    """A session's files under root (its entries: names with one of ids in them), {path in root: base64}. None if
    they're more than SESSIONS_MAX bytes."""
    out, size = {}, 0
    try:
        names = [n for n in os.listdir(root) if any(i in n for i in ids)]
    except OSError:
        return out
    for name in names:
        top = os.path.join(root, name)
        walked = [(top, [], [""])] if os.path.isfile(top) else \
            [(d, [], fs) for d, _ds, fs in os.walk(top) if not os.path.islink(d)]
        for folder, _dirs, files in walked:
            for f in files:
                path = os.path.join(folder, f) if f else folder
                if not os.path.isfile(path) or os.path.islink(path):
                    continue
                rel = os.path.relpath(path, root)
                try:
                    size += os.stat(path).st_size
                    if size > SESSIONS_MAX:
                        return None
                    with open(path, "rb") as fh:
                        out[rel] = base64.b64encode(fh.read()).decode()
                except OSError:
                    continue
    return out


def unpack(root, files, ids, cwd):
    """files (pack's) written under root, those of a session's (ids) only, each said to be cwd's (retarget). False if
    one couldn't be."""
    ok = True
    for rel, data in (files or {}).items() if isinstance(files, dict) else ():
        if not _within(rel, ids) or not isinstance(data, str):
            continue
        path = os.path.join(root, rel)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".part", "wb") as fh:
                fh.write(retarget(base64.b64decode(data), cwd))
            os.replace(path + ".part", path)
        except (OSError, ValueError):
            ok = False
    return ok


def line(entry):
    """A pi session file's line for an entry (JSON.stringify's, but ASCII: a lone surrogate pi's escaped writes)."""
    return json.dumps(entry, separators=(",", ":"))


def _head(path):
    """A pi session file's first line, if it's a session's header."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = json.loads(f.readline() or "{}")
    except (OSError, ValueError):
        return None
    return head if isinstance(head, dict) and head.get("type") == "session" else None


class PiSession:
    """(the chat's host) A chat's pi session file, kept as pi run on another host adds to it: each notice of
    session.ts's (pi-claude's baude_pi_session) puts the entries it has after the first from, appended where that's
    the end of it, else the file written again; its first line said to be the chat's folder's (cwd), where pi run here
    takes it up. One for each session (get), whatever runs it: its owner, the run whose notices it takes; a run's that
    went before, or a local pi's run, isn't (release)."""

    _all, _lock = {}, threading.Lock()

    @classmethod
    def get(cls, root, sid, cwd):
        with cls._lock:
            one = cls._all.get((root, sid))
            if one is None:
                one = cls._all[(root, sid)] = cls(root, sid, cwd)
            one.cwd = cwd
            return one

    @classmethod
    def release(cls, root, sid):
        """(pi run here, on this machine, as it's started) No run elsewhere writes to it from now on."""
        with cls._lock:
            one = cls._all.get((root, sid))
        if one is not None:
            with one.lock:
                one.owner = None

    def __init__(self, root, sid, cwd):
        self.root, self.sid, self.cwd = root, sid, cwd
        self.path, self.lines, self.stamp, self.owner, self.whole = None, [], None, None, False
        self.lock = threading.Lock()

    def load(self):
        """(lock held) Its file as it is now: the one pi here would take up (pi's findById: the first by name that's
        its id's and the chat's folder's), else the newest of its id's."""
        self.path, self.lines, self.stamp, self.whole = None, [], None, False
        try:
            names = [n for n in os.listdir(self.root) if self.sid in n and n.endswith(".jsonl")]
        except OSError:
            return
        here = os.path.realpath(self.cwd) if self.cwd else None
        heads = [(n, h) for n in names if (h := _head(os.path.join(self.root, n))) and h.get("id") == self.sid]

        def when(n):
            try:
                return os.path.getmtime(os.path.join(self.root, n))
            except OSError:
                return 0
        found = next((n for n, h in heads if here and os.path.realpath(str(h.get("cwd") or "")) == here), None) or \
            max((n for n, _h in heads), key=when, default=None)
        if found is None:
            return
        try:
            with open(os.path.join(self.root, found), encoding="utf-8", errors="replace") as f:
                rows = f.read().splitlines()
        except OSError:
            return
        self.path, self.lines = os.path.join(self.root, found), [r for r in rows[1:] if r.strip()]
        self.stamp = _head(self.path).get("timestamp")

    def claim(self, run):
        """A run's from now on (its notices taken, those of one before it not), its file as it is now."""
        with self.lock:
            self.owner = run
            self.load()
            return self.path

    def take(self, said, run):
        """A notice of run's: False if it's of no use (not its owner's now, another session's, entries missing before
        it, or not written). One that starts the session again (pi there took it up as a new one: its header's
        another's) is written beside it, not over it."""
        header, start, entries = said.get("header"), said.get("from"), said.get("entries")
        with self.lock:
            if run is not self.owner or not isinstance(header, dict) or header.get("id") != self.sid or \
                    not isinstance(start, int) or not isinstance(entries, list) or not 0 <= start <= len(self.lines):
                return False
            if start == 0 and self.path is not None and header.get("timestamp") != self.stamp:
                self.path, self.lines = None, []  # (a session begun again there: the one here kept as it is)
            new = [line(e) for e in entries if isinstance(e, dict)]
            appending = self.path is not None and start == len(self.lines) and not self.whole
            lines = self.lines[:start] + new
            if self.path is None:
                when = str(header.get("timestamp") or "").replace(":", "-").replace(".", "-") or "session"
                self.path, self.stamp = os.path.join(self.root, f"{when}_{self.sid}.jsonl"), header.get("timestamp")
            try:
                os.makedirs(self.root, exist_ok=True)
                if appending:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write("".join(n + "\n" for n in new))
                else:
                    with open(self.path + ".part", "w", encoding="utf-8") as f:
                        f.write("".join(n + "\n" for n in [line({**header, "cwd": self.cwd}), *lines]))
                    os.replace(self.path + ".part", self.path)
            except (OSError, ValueError):
                self.lines, self.whole = lines, True  # (what it has, written whole next time)
                return False
            self.lines, self.whole = lines, False
            return True


def later(seconds, fn):
    """fn in seconds, on a thread that doesn't keep the process up."""
    t = threading.Timer(seconds, fn)
    t.daemon = True
    t.start()


class _Ended:
    """Its Popen, as callers wait on it and say what it is: poll, wait, terminate; pid (none here), args."""

    pid = None

    def __init__(self, proc, args):
        self.proc, self.args, self.code, self.done = proc, args, None, threading.Event()

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise subprocess.TimeoutExpired("agent", timeout)
        return self.code

    def terminate(self):
        self.proc.kill()


class ProxiedProc(ClaudeProc):
    """What runs a model of another host's (model: value's), for a chat in cwd here: as ClaudeProc is, to its caller."""

    def __init__(self, on_msg, on_exit, cwd, model, mode, resume=None, effort=None, extra=(), env=None, exe=None,
                 path=None):
        self.on_msg, self.on_exit = on_msg, on_exit
        self.req_ids = itertools.count(1)
        self.asks = {}
        self.member, inner = split(model)
        self.inner = inner
        self.cwd = cwd if cwd and os.path.isdir(cwd) else os.path.expanduser("~")
        self.env = os.environ | (env or {})  # (the caller's, as its claude would have it: its tools' here)
        got = flags(extra)
        tools = got["--tools"].split(",") if "--tools" in got else None
        self.servers = mcp_servers(got.get("--mcp-config"))
        self.host_tools = hosttools.wanted(tools)
        if self.host_tools:
            self.servers[hosttools.NAME] = {"command": sys.executable,
                                            "args": [HEADLESS, "tools", "--cwd", self.cwd, "--only",
                                                     ",".join(self.host_tools)]}
        sid = resume or got.get("--session-id")
        persist = "--no-session-persistence" not in got
        # (pi's session of it lives here, on the chat's host: a copy carried to the model's host for pi to take it up
        # there; what pi adds to it sent back as it's added, session.ts's notices, and kept here: PiSession)
        self.ids = [sid] if persist and sid and models.model_program(inner) == "pi" else []
        self.pi_session = PiSession.get(pi_sessions(), sid, self.cwd) if self.ids else None
        self.ask_for = {"op": "agent", "req": 1, "model": inner, "mode": mode, "effort": effort, "resume": sid,
                        "system_prompt": got.get("--system-prompt"), "persist": persist,
                        "append_system_prompt": got.get("--append-system-prompt"),  # (a Bot's: who it is, its memory)
                        "servers": sorted(self.servers), "cwd": self.cwd}
        self.ch, self.waiting, self.lock = None, [], threading.Lock()
        self.relays = {}  # (conn: the MCP server started here for it)
        self.opened, self.checked = set(), False  # (the servers it's started here; said if any never came: _check)
        self.p, self.ended = _Ended(self, ["agent", self.member, inner]), False
        self.finished = threading.Event()  # (its end handled: what it said of pi's session, all taken)
        threading.Thread(target=self._run, args=({"via": "mesh", "host": self.member, "path": path},),
                         daemon=True, name="agent-proxy").start()
        self.init_rid = self.control({"subtype": "initialize", "hooks": None})

    def _say(self, text, warn=False):
        """A note in the chat; warn: one Meta shows over its reply too (it shows the others only if the model fails)."""
        self.dispatch(lambda: self.on_msg(self, {"type": "_stderr", "text": text} | ({"warn": True} if warn else {})))

    def _run(self, where):
        if self.pi_session is not None:  # (a run of this session's before it: its last notices taken first)
            before = self.pi_session.owner
            if isinstance(before, ProxiedProc) and before is not self:
                before.finished.wait(6)
            path = self.pi_session.claim(self)
            shipped = pack(pi_sessions(), [os.path.basename(path)]) if path else {}  # (the file pi takes up here)
            if shipped is None or path and not shipped:
                self._say(wording.say("agent_proxy.pi_too_big"))
                return self._end(1)
            self.ask_for["sessions"] = shipped
        try:
            ch, info = wire.reach(where, timeout=10)
        except OSError as e:
            self._say(wording.say("agent_proxy.unreachable", host=self.member[:8], why=e))
            return self._end(1)
        ch.send(self.ask_for)
        reply = ch.reply()
        if not reply or reply.get("op") != "agent_started":
            self._say(wording.say("agent_proxy.didnt_run", host=info.get("name") or self.member[:8],
                                  why=wire.said(reply) or wording.say("agent_proxy.no_answer")))
            ch.close()
            return self._end(1)
        with self.lock:
            if self.ended:  # (given up on, or killed, while it was being started there: stopped there too)
                ch.send({"op": "kill"})
                ch.close()
                return None
            for msg in self.waiting:  # (what was said meanwhile, in its order, before anything said from now on)
                ch.send(msg)
            self.ch, self.waiting = ch, []
        ch.keep_alive()
        code = 1
        for msg in ch:
            op = msg.get("op")
            if op == "out":
                try:
                    said = json.loads(msg.get("line") or "")
                except ValueError:
                    said = {"type": "_stderr", "text": str(msg.get("line"))}
                if isinstance(said, dict) and said.get("type") == "system" and \
                        said.get("subtype") == "baude_pi_session":  # (pi's session as it's added to: kept here)
                    try:
                        kept = self.pi_session is not None and self.pi_session.take(said, self)
                    except Exception:  # noqa: BLE001 (said, and the chat goes on)
                        kept = False
                    if not kept:
                        self._say(wording.say("agent_proxy.pi_not_kept"))
                elif isinstance(said, dict):
                    if said.get("type") in ("stream_event", "control_request"):  # (what it says once it runs: not
                        # the error a program that didn't start ends its turn with, whose servers never started)
                        self._check(info.get("name") or self.member[:8])
                    self.dispatch(lambda m=bare(said): self.on_msg(self, m))
            elif op == "err":
                self._say(str(msg.get("line") or ""))
            elif op == "mcp_open":
                self._open(msg.get("conn"), msg.get("name"))
            elif op == "mcp":
                self._to_server(msg.get("conn"), msg.get("line"))
            elif op == "mcp_close":
                self._close(msg.get("conn"))
            elif op == "exit":
                code = msg.get("code") if isinstance(msg.get("code"), int) else 1
                break
        else:
            self._say(wording.say("agent_proxy.connection_gone"))
        self._end(code)

    def _check(self, host):
        """Once, as the model first says anything: the chat told if any of its MCP servers never reached the program
        on host (it's started them all by then), as the model would otherwise make up what they'd have said."""
        with self.lock:
            if self.checked:
                return
            self.checked = True
            missing = sorted(set(self.servers) - self.opened)
        if not missing or not self._calls_tools():
            return
        which = [wording.say("agent_proxy.chat_tools", names=", ".join(self.host_tools)) if n == hosttools.NAME else
                 wording.say("agent_proxy.server_tools", name=f"{n[:1].upper()}{n[1:]}") for n in missing]
        said = " and ".join(which)
        self._say(wording.say("agent_proxy.tools_missing", tools=f"{said[0].upper()}{said[1:]}", host=host), warn=True)

    def _calls_tools(self):
        """Does the model call tools, as its host says (one that doesn't only talks: its program starts no servers)?"""
        try:
            from baudelib import mesh
            return next((m.get("tools", True) is not False for a in mesh.agents() if a.get("host") == self.member
                         for m in a.get("models") or [] if isinstance(m, dict) and m.get("value") == self.inner), True)
        except Exception:  # noqa: BLE001 (not known here: it's taken to)
            return True

    def _open(self, conn, name):
        server = self.servers.get(name)
        if server is None or not isinstance(conn, int) or self.ended:
            return self._send({"op": "mcp_close", "conn": conn})
        try:
            p = subprocess.Popen([server["command"], *[str(a) for a in server.get("args") or []]], cwd=self.cwd,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                 bufsize=1, env=self.env | {k: str(v) for k, v in (server.get("env") or {}).items()})
        except OSError as e:
            self._say(wording.say("agent_proxy.tools_not_started", name=name, why=e))
            return self._send({"op": "mcp_close", "conn": conn})
        with self.lock:
            ended = self.ended
            if not ended:
                self.relays[conn] = p
                self.opened.add(name)
        if ended:  # (it ended as this was started: not kept)
            p.kill()
            return None

        def read():
            for line in p.stdout:
                if line.strip():
                    self._send({"op": "mcp", "conn": conn, "line": line.rstrip("\n")})
            if self.relays.pop(conn, None) is not None:
                self._send({"op": "mcp_close", "conn": conn})
        threading.Thread(target=read, daemon=True, name=f"agent-mcp-{name}").start()

    def _to_server(self, conn, line):
        p = self.relays.get(conn)
        if p is None or not isinstance(line, str):
            return
        try:
            p.stdin.write(line + "\n")
            p.stdin.flush()
        except (OSError, ValueError):
            self._close(conn)

    def _close(self, conn):
        p = self.relays.pop(conn, None)
        if p is not None:
            try:
                p.stdin.close()
            except (OSError, ValueError):
                pass
            later(2, lambda: p.poll() is None and p.kill())

    def _send(self, msg):
        with self.lock:
            if self.ch is None:
                if not self.ended:
                    self.waiting.append(msg)
                return
            ch = self.ch
        ch.send(msg)

    def _end(self, code):
        with self.lock:
            if self.ended:
                return
            self.ended, ch = True, self.ch
        for conn in list(self.relays):
            self._close(conn)
        if ch is not None:
            ch.close()
        if not self.p.done.is_set():
            self.p.code = code
            self.p.done.set()
        self.finished.set()
        self.dispatch(lambda: self.on_exit(code))

    def send(self, obj):
        self._send({"op": "in", "line": json.dumps(obj)})

    def close_stdin(self):
        self._send({"op": "close"})

    def kill(self):
        if self.ended:
            return
        with self.lock:
            ch = self.ch
        if ch is None:  # (not there yet: given up on, and stopped there if it's started yet: _run)
            return self._end(-1)
        if not self.p.done.is_set():  # (gone, to who waits on it: told to go, nothing of it holds anything here)
            self.p.code = -1
            self.p.done.set()
        ch.send({"op": "kill"})
        later(5, lambda: self._end(-1))  # (no word back: gone anyway)


class Run:
    """(on the host that has the model) A program run for a caller elsewhere (conn: its connection, through the
    mesh): started on msg (op agent), in an empty folder of its own; what it says sent back; its MCP servers relays
    (mcp-relay) that connect to this host process, their lines carried to and from the caller."""

    def __init__(self, server, conn, msg):
        model = msg.get("model")
        if split(model) is not None or models.model_program(model) == "claude":
            raise ValueError(f"not a model run as an agent here: {model}")
        if not server.can_run(model):
            raise ValueError(wording.say("host_errors.cant_run", host=server.me["name"], model=model))
        run = models.launch(model, msg.get("mode") or "default", msg.get("effort") or None)
        resume = msg.get("resume") if wire.is_id(msg.get("resume")) else None
        names = [n for n in msg.get("servers") or [] if wire.is_id(n)][:16]
        self.server, self.conn, self.id, self.token = server, conn, secrets.token_hex(8), secrets.token_hex(16)
        self.relays, self.lock, self.gone = {}, threading.Lock(), False
        self.model, self.chat = model, resume  # (what a pending restart says it waits for: Server.blockers)
        self.folder = tempfile.mkdtemp(prefix="baude-agent-")
        # (pi's session, the caller's: a copy here for pi to take it up from, said to be this run's folder's, where
        # pi-claude's run; what pi adds to it is sent to the caller as it's added, session.ts)
        self.ids = [resume] if resume and msg.get("persist") is not False and models.model_program(model) == "pi" \
            else []
        self.sessions = os.path.join(self.folder, "sessions")
        self.told = msg.get("cwd") if isinstance(msg.get("cwd"), str) else ""
        if self.ids and not unpack(self.sessions, msg.get("sessions"), self.ids, os.path.realpath(self.folder)):
            shutil.rmtree(self.folder, ignore_errors=True)
            raise ValueError(wording.say("agent_proxy.pi_not_written"))
        cfg = {"mcpServers": {n: {"command": sys.executable,
                                  "args": [HEADLESS, "mcp-relay", "--socket", server.socket_path, self.id, n,
                                           self.token]} for n in names}}
        args = [run["exe"], "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--permission-prompt-tool", "stdio", "--permission-mode", "manual",
                "--model", model, *(["--resume", resume] if resume else []),
                *(["--effort", run["effort"]] if run["effort"] else []), "--tools", "", "--mcp-config", json.dumps(cfg)]
        if isinstance(msg.get("system_prompt"), str):
            args += ["--system-prompt", msg["system_prompt"]]
        if isinstance(msg.get("append_system_prompt"), str) and models.model_program(model) == "pi":
            args += ["--append-system-prompt", msg["append_system_prompt"]]  # (after pi's own: pi-claude's)
        if msg.get("persist") is False:
            args += ["--no-session-persistence"]
        try:
            self.p = subprocess.Popen(args, cwd=self.folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, bufsize=1,
                                      env=claude_env() | {"BAUDE_TOOLS_CWD": self.told[:4096]} |
                                      ({"BAUDE_PI_SESSIONS": self.sessions} if self.ids else {}) |
                                      ({"BAUDE_PI_RELAY": "1"} if self.ids else {}))
        except OSError:
            shutil.rmtree(self.folder, ignore_errors=True)
            raise
        threading.Thread(target=self._read, args=(self.p.stdout, "out"), daemon=True, name="agent-out").start()
        threading.Thread(target=self._read, args=(self.p.stderr, "err"), daemon=True, name="agent-err").start()

    def _read(self, stream, op):
        for line in stream:
            if line.strip():
                self.conn.send({"op": op, "line": line.rstrip("\n")})
        if op == "out":
            code = self.p.wait()
            self.conn.send({"op": "exit", "code": code})
            self.end()

    def take(self, msg):
        """A message from the caller."""
        op = msg.get("op")
        if op == "in" and isinstance(msg.get("line"), str):
            try:
                self.p.stdin.write(msg["line"] + "\n")
                self.p.stdin.flush()
            except (OSError, ValueError):
                pass
        elif op == "close":
            try:
                self.p.stdin.close()
            except (OSError, ValueError):
                pass
        elif op == "kill":
            self.end()
        elif op == "mcp":
            relay = self.relays.get(msg.get("conn"))
            if relay is not None and isinstance(msg.get("line"), str):
                relay.send({"op": "mcp_line", "line": msg["line"]})
        elif op == "mcp_close":
            relay = self.relays.pop(msg.get("conn"), None)
            if relay is not None:
                close_conn(relay)

    def relay(self, conn, msg):
        """A relay of the program's (mcp-relay) connected, for its server name: the caller starts that server, and
        what each says is carried to the other. Its number, or None if it isn't this run's."""
        if msg.get("token") != self.token or not wire.is_id(msg.get("name")) or self.gone:
            return None
        with self.lock:
            n = next(RELAY_NUMBERS)
            self.relays[n] = conn
        conn.agent_relay = (self, n)
        self.conn.send({"op": "mcp_open", "conn": n, "name": msg["name"]})
        return n

    def relay_line(self, n, line):
        if isinstance(line, str) and n in self.relays:
            self.conn.send({"op": "mcp", "conn": n, "line": line})

    def relay_gone(self, n):
        if self.relays.pop(n, None) is not None:
            self.conn.send({"op": "mcp_close", "conn": n})

    def end(self):
        """It's stopped, and its folder and relays let go of (the caller gone, or told to)."""
        with self.lock:
            if self.gone:
                return
            self.gone = True
            relays, self.relays = list(self.relays.values()), {}
        if self.p.poll() is None:
            self.p.terminate()
            later(5, lambda: self.p.poll() is None and self.p.kill())
        for r in relays:
            close_conn(r)
        shutil.rmtree(self.folder, ignore_errors=True)
        self.server.agent_runs.pop(self.id, None)


RELAY_NUMBERS = itertools.count(1)  # (every relay's number, an agent's (Run) and an app's tools' (AppTools): none
# the same as another's, whichever connection they're on)


class AppTools:
    """A chat's MCP servers that are a head's own (start's app_tools: a phone's Meta's tools, which act on the phone):
    each run by its claude here as a relay (mcp-relay) whose lines go to and from the head over its connection, as an
    agent's MCP servers go to and from its caller (Run): mcp_open {session, conn, name}, then mcp {conn, line} either
    way, mcp_close {conn} from either side. The head's connection gone, its calls are answered as failed till it
    attaches again (attach's app_tools: bind); they're let go of with the chat (end)."""

    def __init__(self, server, conn, session, names):
        self.server, self.session, self.names = server, session, names
        self.id, self.token = secrets.token_hex(8), secrets.token_hex(16)
        self.relays, self.lock, self.gone, self.conn = {}, threading.Lock(), False, None
        self.asked = {}  # (calls a relay's carried to the head, not answered yet: (relay, its id as JSON) -> its id)
        server.agent_runs[self.id] = self  # (found by mcp_relay, as an agent's run is)
        self.bind(conn)

    def config(self):
        """--mcp-config's servers for them: a relay each, to this host process."""
        return {n: {"command": sys.executable, "args": [HEADLESS, "mcp-relay", "--socket",
                                                        os.path.abspath(self.server.socket_path), self.id, n,
                                                        self.token]} for n in self.names}

    def bind(self, conn):
        """Carried to conn from now (the head's, attached again): each relay open said to it again."""
        with self.lock:
            if self.gone:
                return
            old, self.conn = self.conn, conn
            if old is not None and old is not conn:
                getattr(old, "app_tools", set()).discard(self)
            if not hasattr(conn, "app_tools"):
                conn.app_tools, conn.tool_relays = set(), {}
            conn.app_tools.add(self)
            relays = list(self.relays.items())
            for n, _relay in relays:
                conn.tool_relays[n] = self
        for n, relay in relays:
            conn.send({"op": "mcp_open", "session": self.session, "conn": n, "name": relay.app_tool})

    def unbind(self, conn):
        """The head's connection gone: what it was asked, answered as failed (by each call's own id); asked nothing more
        till it's back."""
        with self.lock:
            if self.conn is not conn:
                return
            self.conn = None
            asked, self.asked = list(self.asked.items()), {}
        for (n, _key), rid in asked:
            self._fail(n, rid)

    def _fail(self, n, rid):
        relay = self.relays.get(n)
        if relay is not None:
            relay.send({"op": "mcp_line", "line": json.dumps({"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32000, "message": "the app these tools act on isn't connected to this host now"}})})

    def relay(self, conn, msg):
        with self.lock:
            if msg.get("token") != self.token or msg.get("name") not in self.names or self.gone:
                return None
            n = next(RELAY_NUMBERS)
            self.relays[n] = conn
            conn.app_tool = msg["name"]
            to = self.conn
            if to is not None:
                to.tool_relays[n] = self
        conn.agent_relay = (self, n)
        if to is not None:
            to.send({"op": "mcp_open", "session": self.session, "conn": n, "name": msg["name"]})
        return n

    def relay_line(self, n, line):
        """A line from claude's side: to the head, or, the head away, a call answered as failed at once."""
        if not isinstance(line, str) or n not in self.relays:
            return
        try:
            msg = json.loads(line)
        except ValueError:
            msg = {}
        call = isinstance(msg, dict) and "id" in msg and "method" in msg
        with self.lock:
            to = self.conn
            if to is not None and call:
                self.asked[(n, json.dumps(msg["id"]))] = msg["id"]
        if to is not None:
            to.send({"op": "mcp", "conn": n, "line": line})
        elif call:
            self._fail(n, msg["id"])

    def relay_gone(self, n):
        with self.lock:
            to = self.conn
            if to is not None:
                to.tool_relays.pop(n, None)
            had = self.relays.pop(n, None) is not None
            self.asked = {k: v for k, v in self.asked.items() if k[0] != n}
        if had and to is not None:
            to.send({"op": "mcp_close", "conn": n})

    def take(self, msg):
        """The head's: a line for one of its relays, or one closed."""
        relay = self.relays.get(msg.get("conn"))
        if relay is None:
            return
        if msg.get("op") == "mcp" and isinstance(msg.get("line"), str):
            try:
                rid = json.loads(msg["line"]).get("id")
            except (ValueError, AttributeError):
                rid = None
            with self.lock:
                late = rid is not None and self.asked.pop((msg["conn"], json.dumps(rid)), self) is self
                to = self.conn
            if late:  # (answered as failed already, its head away then: it did run, so its head's told, for its
                if to is not None:  # agent to hear of it rather than do it again)
                    to.send({"op": "mcp_late", "conn": msg["conn"], "line": msg["line"]})
                return
            relay.send({"op": "mcp_line", "line": msg["line"]})
        elif msg.get("op") == "mcp_close":
            with self.lock:
                self.relays.pop(msg["conn"], None)
                if self.conn is not None:
                    self.conn.tool_relays.pop(msg["conn"], None)
            close_conn(relay)

    def end(self):
        """Its chat gone (dropped, deleted, or the host stopping): its relays let go of, its claude's tools with them."""
        with self.lock:
            if self.gone:
                return
            self.gone = True
            relays, self.relays = list(self.relays.values()), {}
            if self.conn is not None:
                getattr(self.conn, "app_tools", set()).discard(self)
        for r in relays:
            close_conn(r)
        self.server.agent_runs.pop(self.id, None)


def close_conn(conn):
    """A host process's connection (serverstate.Conn) let go of, its socket too: what's at the other end hears it's
    gone."""
    conn.close()
    conn.channel.close()


def relay(socket_path, run, name, token):
    """(baude_headless.py mcp-relay) An MCP server for a program run as an agent here (Run): its stdin and stdout
    carried to and from the one its caller runs, through this host process."""
    ch, _info = wire.connect_local(socket_path, timeout=10)
    ch.send({"op": "mcp_relay", "req": 1, "run": run, "name": name, "token": token})
    reply = ch.reply()
    if not reply or reply.get("op") != "relaying":
        sys.exit(f"mcp-relay: {wire.said(reply) or 'no answer'}")
    ch.keep_alive()

    def up():
        for line in sys.stdin:
            if line.strip() and not ch.send({"op": "mcp_line", "line": line.rstrip("\n")}):
                break
        ch.close()
    threading.Thread(target=up, daemon=True, name="relay-up").start()
    for msg in ch:
        if msg.get("op") == "mcp_line" and isinstance(msg.get("line"), str):
            sys.stdout.write(msg["line"] + "\n")
            sys.stdout.flush()
