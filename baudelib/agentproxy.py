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


class _Ended:
    """Its Popen, as callers wait on it: poll, wait, terminate."""

    def __init__(self, proc):
        self.proc, self.code, self.done = proc, None, threading.Event()

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
        got = flags(extra)
        tools = got["--tools"].split(",") if "--tools" in got else None
        self.servers = mcp_servers(got.get("--mcp-config"))
        self.host_tools = hosttools.wanted(tools)
        if self.host_tools:
            self.servers[hosttools.NAME] = {"command": sys.executable,
                                            "args": [HEADLESS, "tools", "--cwd", self.cwd, "--only",
                                                     ",".join(self.host_tools)]}
        self.ask_for = {"op": "agent", "req": 1, "model": inner, "mode": mode, "effort": effort,
                        "resume": resume or got.get("--session-id"), "system_prompt": got.get("--system-prompt"),
                        "persist": "--no-session-persistence" not in got, "servers": sorted(self.servers),
                        "cwd": self.cwd}
        self.ch, self.waiting, self.lock = None, [], threading.Lock()
        self.relays = {}  # (conn: the MCP server started here for it)
        self.p, self.ended = _Ended(self), False
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
            self.ch = ch
            waiting, self.waiting = self.waiting, []
        for msg in waiting:
            ch.send(msg)
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
            elif op == "exit":
                code = msg.get("code") if isinstance(msg.get("code"), int) else 1
                break
        else:
            self._say("the connection to the host that runs this model is gone")
        self._end(code)

    def _open(self, conn, name):
        server = self.servers.get(name)
        if server is None or not isinstance(conn, int):
            return self._send({"op": "mcp_close", "conn": conn})
        try:
            p = subprocess.Popen([server["command"], *[str(a) for a in server.get("args") or []]], cwd=self.cwd,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                 bufsize=1, env=os.environ | {k: str(v) for k, v in (server.get("env") or {}).items()})
        except OSError as e:
            self._say(f"couldn't start the {name} tools here: {e}")
            return self._send({"op": "mcp_close", "conn": conn})
        self.relays[conn] = p

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
            threading.Timer(2, lambda: p.poll() is None and p.kill()).start()

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
        if ch is None:  # (not there yet: given up on)
            return self._end(-1)
        ch.send({"op": "kill"})
        threading.Timer(5, lambda: self._end(-1)).start()  # (no word back: gone anyway)


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
        self.relays, self.ids, self.lock, self.gone = {}, itertools.count(1), threading.Lock(), False
        self.folder = tempfile.mkdtemp(prefix="baude-agent-")
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
        told = msg.get("cwd") if isinstance(msg.get("cwd"), str) else ""
        self.p = subprocess.Popen(args, cwd=self.folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, bufsize=1,
                                  env=claude_env() | {"BAUDE_TOOLS_CWD": told[:4096]})
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
                relay.close()

    def relay(self, conn, msg):
        """A relay of the program's (mcp-relay) connected, for its server name: the caller starts that server, and
        what each says is carried to the other. Its number, or None if it isn't this run's."""
        if msg.get("token") != self.token or not wire.is_id(msg.get("name")) or self.gone:
            return None
        with self.lock:
            n = next(self.ids)
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
            threading.Timer(5, lambda: self.p.poll() is None and self.p.kill()).start()
        for r in relays:
            r.close()
        shutil.rmtree(self.folder, ignore_errors=True)
        self.server.agent_runs.pop(self.id, None)


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
