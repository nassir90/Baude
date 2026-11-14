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

from baudelib import hosttools, models, wire
from baudelib.claude import ClaudeProc, claude_env

PREFIX = "agent/"
HEADLESS = os.path.join(models.REPO, "baude_headless.py")
NAMED = ("--system-prompt", "--tools", "--mcp-config", "--session-id")  # (the flags of a caller's taken, with a value)


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


def pack(root, ids, seen=None, cwd=None):
    """A session's files under root (its entries: names with one of ids in them), {path in root: base64}, those that
    have changed since seen ({path: stamp}, updated), each said to be cwd's (retarget) if it's given; at most
    SESSIONS_MAX bytes of them."""
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
                try:
                    st = os.lstat(path)
                except OSError:
                    continue
                rel = os.path.relpath(path, root)
                stamp = (st.st_mtime_ns, st.st_size)
                if not os.path.isfile(path) or os.path.islink(path) or (seen or {}).get(rel) == stamp:
                    continue
                size += st.st_size
                if size > SESSIONS_MAX:
                    return out
                try:
                    with open(path, "rb") as fh:
                        out[rel] = base64.b64encode(retarget(fh.read(), cwd)).decode()
                except OSError:
                    continue
                if seen is not None:
                    seen[rel] = stamp
    return out


def unpack(root, files, ids, seen=None, cwd=None):
    """files (pack's) written under root, those of a session's (ids) only, each said to be cwd's (retarget) if it's
    given; seen: their stamps, as written."""
    for rel, data in (files or {}).items() if isinstance(files, dict) else ():
        if not _within(rel, ids) or not isinstance(data, str):
            continue
        path = os.path.join(root, rel)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".part", "wb") as fh:
                fh.write(retarget(base64.b64decode(data), cwd))
            os.replace(path + ".part", path)
            if seen is not None:
                st = os.stat(path)
                seen[rel] = (st.st_mtime_ns, st.st_size)
        except (OSError, ValueError):
            continue


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
        # (pi's session of it lives here, on the chat's host: carried to the model's host with it, and back as it's
        # written there, each turn and as it ends)
        self.ids = [sid] if persist and sid and models.model_program(inner) == "pi" else []
        self.ask_for = {"op": "agent", "req": 1, "model": inner, "mode": mode, "effort": effort, "resume": sid,
                        "system_prompt": got.get("--system-prompt"), "persist": persist,
                        "servers": sorted(self.servers), "cwd": self.cwd,
                        "sessions": pack(pi_sessions(), self.ids) if self.ids else {}}
        self.ch, self.waiting, self.lock = None, [], threading.Lock()
        self.relays = {}  # (conn: the MCP server started here for it)
        self.p, self.ended = _Ended(self, ["agent", self.member, inner]), False
        threading.Thread(target=self._run, args=({"via": "mesh", "host": self.member, "path": path},),
                         daemon=True, name="agent-proxy").start()
        self.init_rid = self.control({"subtype": "initialize", "hooks": None})

    def _say(self, text):
        self.dispatch(lambda: self.on_msg(self, {"type": "_stderr", "text": text}))

    def _run(self, where):
        try:
            ch, info = wire.reach(where, timeout=10)
        except OSError as e:
            self._say(f"couldn't reach the host that runs this model ({self.member[:8]}): {e}")
            return self._end(1)
        ch.send(self.ask_for)
        reply = ch.reply()
        if not reply or reply.get("op") != "agent_started":
            self._say(f"{info.get('name') or self.member[:8]} didn't run it: "
                      f"{(reply or {}).get('error') or 'no answer'}")
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
                if isinstance(said, dict):
                    self.dispatch(lambda m=bare(said): self.on_msg(self, m))
            elif op == "err":
                self._say(str(msg.get("line") or ""))
            elif op == "mcp_open":
                self._open(msg.get("conn"), msg.get("name"))
            elif op == "mcp":
                self._to_server(msg.get("conn"), msg.get("line"))
            elif op == "mcp_close":
                self._close(msg.get("conn"))
            elif op == "sessions" and self.ids:  # (pi's session, as it's written there: kept here)
                unpack(pi_sessions(), msg.get("files"), self.ids)
            elif op == "exit":
                code = msg.get("code") if isinstance(msg.get("code"), int) else 1
                break
        else:
            self._say("the connection to the host that runs this model is gone")
        self._end(code)

    def _open(self, conn, name):
        server = self.servers.get(name)
        if server is None or not isinstance(conn, int) or self.ended:
            return self._send({"op": "mcp_close", "conn": conn})
        try:
            p = subprocess.Popen([server["command"], *[str(a) for a in server.get("args") or []]], cwd=self.cwd,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                 bufsize=1, env=self.env | {k: str(v) for k, v in (server.get("env") or {}).items()})
        except OSError as e:
            self._say(f"couldn't start the {name} tools here: {e}")
            return self._send({"op": "mcp_close", "conn": conn})
        with self.lock:
            ended = self.ended
            if not ended:
                self.relays[conn] = p
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
            raise ValueError(f"{server.me['name']} can't run {model}: it, or its program, isn't here")
        run = models.launch(model, msg.get("mode") or "default", msg.get("effort") or None)
        resume = msg.get("resume") if wire.is_id(msg.get("resume")) else None
        names = [n for n in msg.get("servers") or [] if wire.is_id(n)][:16]
        self.server, self.conn, self.id, self.token = server, conn, secrets.token_hex(8), secrets.token_hex(16)
        self.relays, self.numbers, self.lock, self.gone = {}, itertools.count(1), threading.Lock(), False
        self.folder = tempfile.mkdtemp(prefix="baude-agent-")
        # (pi's session, the caller's: here only while it runs, what's written sent back as it is)
        self.ids = [resume] if resume and msg.get("persist") is not False and models.model_program(model) == "pi" \
            else []
        self.sessions, self.stamps = os.path.join(self.folder, "sessions"), {}
        self.told = msg.get("cwd") if isinstance(msg.get("cwd"), str) else ""
        if self.ids:  # (said to be this run's folder's, as pi-claude's run in it; the chat's folder's going back)
            unpack(self.sessions, msg.get("sessions"), self.ids, self.stamps, os.path.realpath(self.folder))
        cfg = {"mcpServers": {n: {"command": sys.executable,
                                  "args": [HEADLESS, "mcp-relay", "--socket", server.socket_path, self.id, n,
                                           self.token]} for n in names}}
        args = [run["exe"], "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--permission-prompt-tool", "stdio", "--permission-mode", "manual",
                "--model", model, *(["--resume", resume] if resume else []),
                *(["--effort", run["effort"]] if run["effort"] else []), "--tools", "", "--mcp-config", json.dumps(cfg)]
        if isinstance(msg.get("system_prompt"), str):
            args += ["--system-prompt", msg["system_prompt"]]
        if msg.get("persist") is False:
            args += ["--no-session-persistence"]
        try:
            self.p = subprocess.Popen(args, cwd=self.folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, bufsize=1,
                                      env=claude_env() | {"BAUDE_TOOLS_CWD": self.told[:4096]} |
                                      ({"BAUDE_PI_SESSIONS": self.sessions} if self.ids else {}))
        except OSError:
            shutil.rmtree(self.folder, ignore_errors=True)
            raise
        threading.Thread(target=self._read, args=(self.p.stdout, "out"), daemon=True, name="agent-out").start()
        threading.Thread(target=self._read, args=(self.p.stderr, "err"), daemon=True, name="agent-err").start()

    def _read(self, stream, op):
        for line in stream:
            if line.strip():
                if op == "out" and self.ids and '"result"' in line:  # (a turn's end: its session as it is, first)
                    self.sync()
                self.conn.send({"op": op, "line": line.rstrip("\n")})
        if op == "out":
            code = self.p.wait()
            self.sync()
            self.conn.send({"op": "exit", "code": code})
            self.end()

    def sync(self):
        """pi's session, what's been written of it since it was last sent: sent to the caller, whose it is."""
        if self.ids and (files := pack(self.sessions, self.ids, self.stamps, self.told)):
            self.conn.send({"op": "sessions", "files": files})

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
            n = next(self.numbers)
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


def close_conn(conn):
    """A host process's connection (server.Conn) let go of, its socket too: what's at the other end hears it's gone."""
    conn.close()
    conn.channel.close()


def relay(socket_path, run, name, token):
    """(baude_headless.py mcp-relay) An MCP server for a program run as an agent here (Run): its stdin and stdout
    carried to and from the one its caller runs, through this host process."""
    ch, _info = wire.connect_local(socket_path, timeout=10)
    ch.send({"op": "mcp_relay", "req": 1, "run": run, "name": name, "token": token})
    reply = ch.reply()
    if not reply or reply.get("op") != "relaying":
        sys.exit(f"mcp-relay: {(reply or {}).get('error') or 'no answer'}")
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
