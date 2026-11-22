"""metaagent — Meta's session and its tools: the prompts it's given (SYSTEM), the tools it has (SHARED_TOOLS,
SETUP_TOOLS) and the reports some of them answer with, the Unix socket they come in on (ToolSocket) through the
relay Claude Code runs as Meta's MCP server (RELAY), and the session itself (Agent, on MetaProc or MetaProxiedProc).
"""
import json
import os
import socket
import sys
import tempfile
import threading
import uuid

from gi.repository import GLib  # noqa: E402

import widgets  # noqa: E402
from baudelib import agentproxy, codex, modelfiles, wording  # noqa: E402
from baudelib import models as modelslib  # noqa: E402
from baudelib import providers as providerslib  # noqa: E402
from baudelib.claude import ClaudeProc  # noqa: E402
from meta import (  # noqa: E402
    AGENT_ENV, CHARS_PER_TOKEN, COMMAND_TOOLS, EDIT_TOOLS, MODEL, NAME, NO_AUTO, OVERFLOW_RE, READ_TOOLS,
    codex_catalog, listed, sync_rows, takes_commands, weight, window,
)


# ---------------------------------------------------------------- its tools

SYSTEM = """You are {name}, the helper inside {app}, {what} You operate the app for the user: you read its state, \
change its settings, and organise and change what's in it, with your tools. Do what's asked; when it's ambiguous which \
things are meant, say which you'd take, or highlight them, before changing anything.

What you know of the app comes with the user's first message: a short index, a line or two per part, with short ids. \
app_index opens a part in full (its rows, a filter to narrow them): look rather than guess, and don't ask the user for \
what the index has.

Be useful: find out what you can with your tools before asking. Take what the user tells you as settled and act on it: \
never ask again for what they've said, or ask them to check what you could. When you don't know a value and the \
likeliest is clear (a file beside one they named, a path in the checkout they gave), use it and say so in a line; ask \
only when you can't narrow it down, and then ask once, for the one thing you need.

Everything you do is recorded as an action, with an id, and can be undone: under your reply the user sees each one \
with undo and redo, and a change to {listed} is marked in the left bar (green added, red removed, blue moved) till they \
keep or undo it; all you change of {listed} in one turn is one change, undone and done again as one. \
undo and redo take one back or put it again. You're told when the user undoes something. Never do \
what can't be undone: say so plainly, and show its button (show button:<action>) for the user to press themselves.

Forms: asked to help the user add or make something the app has a form for (app_index forms), bring it up with \
open_form, fill in what you know (fill_form), and leave the rest and the submitting to them.

Settings: set_setting changes one at once, and its row shows under your reply, where the user can go on adjusting it; \
you're told the value they settle on. Asked about a setting without being asked to change it, show its row (show \
setting:<name>). show also lists chats{and_pages} as rows the user can click, and shows the changelog.

Models: local_models says what runs models here besides Claude's (Ollama, MLX, Apple's adapter, OpenAI-compatible \
servers). check_models finds Ollama's unhealthy ones; prune_models removes them (the user's asked first: it can't be \
undone). add_mlx_model and add_endpoint add one; build_apple_adapter builds Apple's (asked first). fetch_model \
copies a model another host in the mesh has (asked first); model_files says where a model's files are, here or there, \
for copying them some other way. Nothing is installed or downloaded that the user didn't ask for: say what they'd run.

Be brief and plain: a sentence or two, no headings. Don't describe what your tools did: the user sees it."""

COMMANDS_SYSTEM = """

Commands: you run shell commands (Bash), edit files (Edit, Write) and look things up on the web (WebFetch, \
WebSearch), in Claude Code's Auto mode: what looks risky is put to the user first. Check things yourself rather than \
asking the user to: where a file is, what's installed, what a host has (ssh -o BatchMode=yes -o ConnectTimeout=10 \
<host> '<command>' for one of the app's hosts or one the user names: there's no terminal for a password). Read and look \
as you like. Commands and file edits aren't your actions: they have no undo, and what's said above of undo is of the \
app's tools. So change nothing outside the app that the user didn't ask for, install, delete or download nothing \
unasked, and before a command that changes or removes something, say in a line what it will do."""

CODE_SYSTEM = """

Code mode: run_python is your one tool for the app. It runs up to {lines} lines of Python with app, whose functions \
are the app's tools (below), and returns what's printed and the last expression's value. app.app_index gives its \
rows as dicts; the others give their answer as text, and raise RuntimeError for an error. Look things up and act in \
one go: filter, then act on what's found. No imports, no names starting with _; plain builtins only. The tools that \
set models up (local_models, check_models, prune_models, add_mlx_model, add_endpoint, model_files, fetch_model, \
build_apple_adapter) are yours \
to call as they are, not through run_python: they take longer than its code has.
{doc}"""

SHARED_TOOLS = [
    {"name": "app_index", "description": "A part of the app's index in full, a row a line (its parts: the index's last "
     "line). filter: only rows containing it (any case).",
     "inputSchema": {"type": "object", "required": ["section"], "properties": {
         "section": {"type": "string"}, "filter": {"type": "string"}}}},
    {"name": "set_setting", "description": "Change a setting at once (app_index settings: their names and values). "
     "Its row shows under your reply for the user to adjust.",
     "inputSchema": {"type": "object", "required": ["name", "value"], "properties": {
         "name": {"type": "string"}, "value": {"type": ["string", "number", "boolean"]}}}},
    {"name": "show", "description": "Show UI under your reply: setting:<name> (its row), chats or pages (rows to "
     "click: their ids or names in items), changelog, or button:<action> (a control for the user to press, for "
     "what you don't do yourself; its actions: app_index buttons).",
     "inputSchema": {"type": "object", "required": ["what"], "properties": {
         "what": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "read_chat", "description": "A chat's conversation as text (its end, if it's long).",
     "inputSchema": {"type": "object", "required": ["chat"], "properties": {"chat": {"type": "string"}}}},
    {"name": "rename_chat", "description": "Rename a chat.",
     "inputSchema": {"type": "object", "required": ["chat", "title"], "properties": {
         "chat": {"type": "string"}, "title": {"type": "string"}}}},
    {"name": "undo", "description": "Undo one of your actions: its id, or last. turn: true undoes all of the "
     "turn it was made in.",
     "inputSchema": {"type": "object", "required": ["action"], "properties": {
         "action": {"type": "string"}, "turn": {"type": "boolean"}}}},
    {"name": "redo", "description": "Do again an action of yours that was undone: its id.",
     "inputSchema": {"type": "object", "required": ["action"], "properties": {"action": {"type": "string"}}}},
]

SHARED_TOOLS += [
    {"name": "open_form", "description": "Bring up one of the app's forms in your pane (app_index forms: their names), "
     "filled with values if you know them ({field: value}), for the user to finish and submit; you never submit one. "
     "Returns its id, its fields (with their choices) and what's still wrong.",
     "inputSchema": {"type": "object", "required": ["form"], "properties": {
         "form": {"type": "string"}, "values": {"type": "object"}}}},
    {"name": "fill_form", "description": "Fill fields of a form you brought up (its id): values {field: value}. "
     "Returns what's still wrong, if anything.",
     "inputSchema": {"type": "object", "required": ["id", "values"], "properties": {
         "id": {"type": "string"}, "values": {"type": "object"}}}},
]

# setting models up: what runs them here, Ollama's models checked and pruned, an MLX model or a server added, Apple's
# adapter built (MetaChat's handlers). Slow ones answer later (Later), and are Meta's own tools in code mode too:
# run_python's code has seconds
SETUP_TOOLS = [
    {"name": "local_models", "description": "What runs models here besides Claude's: Ollama (installed, running, its "
     "models: window, tools), MLX (pi's models, and MLX models downloaded but not added), Apple's adapter, and the "
     "OpenAI-compatible servers.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "check_models", "description": "Load Ollama's models (all, if none are named) for a one-token reply each: "
     "which are unhealthy, and why. Slow: one at a time.",
     "inputSchema": {"type": "object", "properties": {"models": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "prune_models", "description": "Remove Ollama models, by name. The user's asked first; it can't be "
     "undone (they'd be downloaded again).",
     "inputSchema": {"type": "object", "required": ["models"], "properties": {
         "models": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "add_mlx_model", "description": "Name a downloaded MLX model (local_models: its repo, or its folder's path) among MLX's models: "
     "every one downloaded is listed already, as its folder's named. thinking: it thinks (Qwen3's do); name: as it's listed.",
     "inputSchema": {"type": "object", "required": ["repo"], "properties": {
         "repo": {"type": "string"}, "name": {"type": "string"}, "thinking": {"type": "boolean"}}}},
    {"name": "add_endpoint", "description": "Add an OpenAI-compatible server (LM Studio, llama.cpp, vLLM), once it "
     "answers: name, url (to /v1); key_env: the environment variable its key is in; window: its context window.",
     "inputSchema": {"type": "object", "required": ["name", "url"], "properties": {
         "name": {"type": "string"}, "url": {"type": "string"}, "key_env": {"type": "string"},
         "window": {"type": "integer"}}}},
    {"name": "model_files", "description": "Where a model's files are: an Ollama model's (its manifest and blobs, in "
     "Ollama's models folder) or an MLX model's (its Hugging Face cache snapshot), with their sizes; here, or on host "
     "(another host in the mesh, by name). For copying them some other way (rsync, scp) when fetch_model can't.",
     "inputSchema": {"type": "object", "required": ["model"], "properties": {
         "model": {"type": "string", "description": "Ollama's name (qwen3:4b), or a Hugging Face repo (org/name)"},
         "host": {"type": "string"}, "kind": {"type": "string", "enum": ["ollama", "mlx"]}}}},
    {"name": "fetch_model", "description": "Copy a model from another host in the mesh to this one, over the mesh "
     "(no download from the internet): an Ollama model (one of Ollama's library's: qwen3:4b), which this machine's "
     "Ollama pulls from that host's and checks; or an MLX model (its Hugging Face repo), into the Hugging Face cache "
     "here. The user's asked first; it copies in the background (local_models says how it's going).",
     "inputSchema": {"type": "object", "required": ["model", "host"], "properties": {
         "model": {"type": "string"}, "host": {"type": "string"}, "kind": {"type": "string", "enum": ["ollama", "mlx"]}}}},
    {"name": "build_apple_adapter", "description": "Build apple-claude, which runs Apple's on-device model (macOS 26.4 "
     "or later, with swift). The user's asked first; it builds in the background (local_models: how it went).",
     "inputSchema": {"type": "object", "properties": {}}},
]
SETUP_NAMES = {t["name"] for t in SETUP_TOOLS}
CONFIRM_WAIT = 600  # (s a question to the user waits for an answer, then it's a no: well inside pi's MCP timeout)


def models_report():
    """local_models' answer: this machine's providers, a line or a few each (off the main loop: it asks servers)."""
    found = {p["id"]: p for p in providerslib.found(wait=providerslib.TIMEOUT * 3 + 2, max_age=0)}
    lines = []
    o, where = found.get("ollama") or {}, providerslib.ollama_install()
    installed = ", ".join(x for x in (where["exe"], where["app"]) if x)
    if o and not o.get("error"):
        lines.append(f"Ollama {o.get('version') or ''}: running at {o['url']}" + (f" (installed: {installed})"
                                                                                  if installed else "") +
                     f"; {len(o['models'])} models" + (":" if o["models"] else ""))
        for m in o["models"]:
            notes = [f"window {m['window']:,}" + (" (too small for pi's tools: raise Ollama's context length)"
                                                   if m["window"] < 8192 else ""),
                     "tools" if m.get("tools") else "no tools", *(["thinks"] if m.get("thinking") else []),
                     *(["sees images"] if "image" in (m.get("input") or ()) else [])]
            size = f"{m['size'] / 1e9:.1f} GB, " if m.get("size") else ""
            lines.append(f"  {m['resolvedModel']}: {size}{', '.join(notes)}")
    elif installed:
        lines.append(f"Ollama: installed ({installed}) but {(o or {}).get('error') or 'not running'}: the user opens "
                     "it (or runs ollama serve)")
    else:
        lines.append("Ollama: not installed (ollama.com/download: the user installs it)")
    mlx = found.get("mlx") or {}
    have = {m["value"] for m in mlx.get("models") or []}
    lines.append("MLX (pi-claude): " + (f"can't run yet: {mlx['error']}; " if mlx.get("error") else "") +
                 (", ".join(f"{m['displayName']} ({'downloaded' if m['value'] in have else 'not downloaded'})"
                            for m in modelslib.MLX_OWN + modelslib.mlx_added()) or "no models named"))
    extra = [m for m in providerslib.mlx_downloaded() if not m["added"]]
    if extra:
        lines.append("  also downloaded, listed as found (add_mlx_model names one, or says it thinks): " + ", ".join(
            f"{m['repo']} ({m['where']}, {m['size'] / 1e9:.1f} GB{', sees images' if m['vision'] else ''})"
            for m in extra))
    lines.append("  looked for in: the Hugging Face cache (" + modelslib.hf_hub() + ")" +
                 "".join(f", {d}" for d in modelslib.mlx_dirs()) + " (settings.json's mlx_dirs adds folders)")
    if sys.platform == "darwin":
        st = providerslib.apple_status()
        state = "built" if st["built"] else {"building": "building now", "failed": "its build failed"}.get(
            st["build"], "not built")
        need = ".".join(map(str, providerslib.APPLE_NEEDS))
        lines.append(f"Apple's on-device model (apple-claude): {state}; macOS {st['macos']}" +
                     ("" if st["fits"] else f", it needs {need}") +
                     ("" if st["swift"] else "; no swift (Xcode's command line tools)") +
                     ("" if st["built"] or st["build"] else " (build_apple_adapter)"))
        if st["build"] == "failed" and st["log"]:
            lines.append("  its build's last lines: " + " | ".join(st["log"][-6:]))
    for f in list(FETCHES.values()):
        gb = lambda n: f"{n / 1e9:.1f} GB"  # noqa: E731
        lines.append(f"Fetching {f['model']} from {f['host']}: " + (
            f"{gb(f['done'])} of {gb(f['total'])}" if f["state"] == "copying" and f["total"] else f["state"]) +
            (f" ({f['error']})" if f.get("error") else ""))
    if hosts := modelfiles.hosts():
        lines.append("Other hosts with models (fetch_model copies one here; model_files says where its files are): " +
                     ", ".join(sorted(hosts)))
    for pid, p in found.items():
        if pid.startswith("openai/"):
            lines.append(f"{p['name']} ({p['url']}): " + (p["error"] or f"{len(p['models'])} models: " +
                                                           ", ".join(m["resolvedModel"] for m in p["models"][:10])))
    if not any(pid.startswith("openai/") for pid in found):
        lines.append("OpenAI-compatible servers: none added (add_endpoint)")
    return "\n".join(lines)


FETCHES = {}  # (fetch_model's, by (host, model): {"host", "model", "kind", "state", "done", "total", "error"})


def files_report(where, kind, host):
    """model_files' answer: where a model's files are."""
    gb = f"{where['size'] / 1e9:.1f} GB" if where["size"] >= 1e8 else f"{where['size'] / 1e6:.1f} MB"
    if kind == "ollama":
        lines = [f"Ollama model {where['name']} on {host}, {gb}: Ollama's models folder {where['dir']}",
                 f"  manifest: {where['manifest']}"]
        lines += [f"  blob: {b['path']}" + (f" ({b['size']:,} bytes)" if b["size"] is not None else " (missing)")
                  for b in where["blobs"]]
        lines.append("  (copied to the same places under another Ollama's models folder, that Ollama lists it)")
    else:
        lines = [f"MLX model {where['repo']} on {host}, {gb}: snapshot {where['dir']}"]
        lines += [f"  {f['name']} ({f['size']:,} bytes)" for f in where["files"]]
        lines.append("  (copied to models--<org>--<name>/snapshots/<revision>/ in another Hugging Face cache, with "
                     "refs/main naming the revision, it's found there)")
    return "\n".join(lines)


class Later:
    """A tool's answer that takes a while (Ollama loading a model, the user answering, a build starting): work() run
    on the socket's thread (ToolSocket), the main loop free meanwhile; then done(answer) on the main loop, its
    answer what Meta's told."""

    def __init__(self, work, done=None):
        self.work, self.done = work, done

    def run(self):
        try:
            out = self.work()
        except Exception as e:  # noqa: BLE001 (Meta's told; the app goes on)
            out = {"error": str(e) if isinstance(e, (ValueError, OSError)) else f"{type(e).__name__}: {e}"}
        if self.done is not None:
            out = ToolSocket.main(self.done, out) or out
        return out


RUN_PYTHON = {"name": "run_python", "description": "Run up to 10 lines of Python with app (its functions: your "
              "instructions). Returns what's printed and the last expression's value.",
              "inputSchema": {"type": "object", "required": ["code"], "properties": {"code": {"type": "string"}}}}

# the relay Claude Code starts as Meta's MCP server (python -c, nothing written): stdio JSON-RPC in, the app's socket
# out (ToolSocket), a call a line, with the agent it's for (its token: the app's windows each have one)
RELAY = r'''import json, socket, sys

sock, token = sys.argv[1], sys.argv[2]


def call(tool, args):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock)
    s.sendall((json.dumps({"tool": tool, "args": args, "agent": token}) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        chunk = s.recv(1 << 16)
        if not chunk:
            break
        data += chunk
    s.close()
    return json.loads(data or b"{}")


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        continue
    try:
        if method == "initialize":
            result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or "2025-06-18",
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "meta", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": call("_tools", {}).get("tools", [])}
        elif method == "tools/call":
            p = msg.get("params") or {}
            res = call(p.get("name"), p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": res.get("error") or res.get("text") or json.dumps(res)}],
                      "isError": bool(res.get("error"))}
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}})
            continue
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    except Exception as e:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "Meta: " + str(e)}})
'''


class ToolSocket:
    """Where Meta's tools come in: a Unix socket of the app's, its owner's only, that the relay Claude Code runs as
    Meta's MCP server (RELAY) talks to, a call a line, each with its agent's token. A call's answered on the main loop
    (the agent's handler(tool, args) -> {"text", "data"?} or {"error"}), its thread waiting for that."""

    _one = []

    @classmethod
    def get(cls):
        if not cls._one:
            cls._one.append(cls())
        return cls._one[0]

    def __init__(self):
        self.agents = {}  # (token: (handler, tools))
        self.path = os.path.join(tempfile.gettempdir(), f"meta-tools-{os.getpid()}.sock")
        for old in glob_sockets():  # (left by an app gone, or restarted: no atexit then)
            try:
                pid = int(os.path.basename(old)[len("meta-tools-"):-len(".sock")])
                if pid != os.getpid():
                    os.kill(pid, 0)
                    continue
            except ProcessLookupError:
                pass
            except (ValueError, OSError):
                continue
            try:
                os.unlink(old)
            except OSError:
                pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        mask = os.umask(0o177)  # (made its owner's only)
        try:
            srv.bind(self.path)
        finally:
            os.umask(mask)
        srv.listen(8)
        threading.Thread(target=self._serve, args=(srv,), daemon=True, name="meta-tools").start()
        import atexit
        atexit.register(lambda: os.path.exists(self.path) and os.unlink(self.path))

    def add(self, token, handler, tools, python=None):
        """An agent's tools: handler(tool, args) on the main loop; python(args) (run_python's) on the socket's thread,
        the main loop left free for the calls its code makes."""
        self.agents[token] = (handler, tools, python)

    def drop(self, token):
        self.agents.pop(token, None)

    def _serve(self, srv):
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve_one, args=(conn,), daemon=True).start()

    def _serve_one(self, conn):
        try:
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(1 << 16)
                if not chunk:
                    break
                data += chunk
            req = json.loads(data or b"{}")
            handler, tools, python = self.agents.get(req.get("agent"), (None, [], None))
            if req.get("tool") == "_tools":
                res = {"tools": tools}
            elif req.get("tool") == "run_python" and python is not None and not req.get("nested"):
                res = python(req.get("args") or {})
            else:
                res = self.main(handler, req.get("tool"), req.get("args") or {}) if handler is not None else None
                if isinstance(res, Later):  # (a slow one: worked out here, off the main loop)
                    res = res.run()
                res = res or {"error": f"{NAME} didn't answer: try again"}
            if not req.get("nested"):
                res = {k: v for k, v in res.items() if k != "data"}  # (data is run_python's code's: the text says it)
            conn.sendall((json.dumps(res, ensure_ascii=False, default=str) + "\n").encode())
        except (OSError, ValueError) as e:
            try:
                conn.sendall((json.dumps({"error": str(e)}) + "\n").encode())
            except OSError:
                pass
        finally:
            conn.close()

    @classmethod
    def main(cls, fn, *args):
        """fn(*args) run on the main loop, waited for (from a thread of the socket's): its answer, or None."""
        box, done = {}, threading.Event()
        GLib.idle_add(cls._call, fn, args, box, done)
        return box.get("result") if done.wait(120) else None

    @staticmethod
    def _call(fn, args, box, done):
        try:
            box["result"] = fn(*args)
        except Exception as e:  # noqa: BLE001 (Meta's told; the app goes on)
            box["result"] = {"error": f"{type(e).__name__}: {e}"}
        done.set()
        return False


def glob_sockets():
    import glob
    return glob.glob(os.path.join(tempfile.gettempdir(), "meta-tools-*.sock"))


class MetaProc(ClaudeProc):
    """baudelib's ClaudeProc, on GTK's main loop, in the app's environment for claude (env_of, if it has one)."""
    dispatch = staticmethod(GLib.idle_add)

    def __init__(self, *args, env_of=None, **kw):
        if env_of is not None:
            self.env_of = env_of
        super().__init__(*args, **kw)


class MetaProxiedProc(agentproxy.ProxiedProc):
    """A model of another host's run there as Meta's agent (agentproxy), its tools here: on GTK's main loop."""
    dispatch = staticmethod(GLib.idle_add)


class Agent:
    """Meta's session: a Claude Code session for a conversation (one turn at a time: ask), on the model chosen. Its
    tools read (READ_TOOLS): what's under roots it reads as it likes; anything else it asks for, and is let read what
    the user referenced, else the user's asked (MetaChat.ask_permission). With commands (runs_commands: Claude's
    models) it also acts (COMMAND_TOOLS), in Claude Code's Auto mode, which lets by what's safe (reads anywhere, edits
    in its folders, as it judges) and asks for the rest: the user's asked, as for a read. Nothing's written under
    protect (the app's own files: Record's pages), whatever the mode. Without commands, it's in Claude Code's default
    permission mode, and anything but its tools is refused. Its own tools come in on the ToolSocket. Its callbacks reach chat on the main loop: _said_more,
    _tool_started, _tool_ended, _done, agent_gone."""

    STOP_WAIT = 3000  # ms for an interrupted turn to end, before the session's stopped
    EXIT_WAIT = 200  # ms for what it said as it ended (stderr, read on its own thread) to come in

    def __init__(self, chat, key, system, tools, cwd, roots=(), model=MODEL, exe=None, env_of=None, effort=None,
                 commands=False, protect=()):
        # (what runs the model, as Baude's chats start it: claude (exe), or apple-claude or pi-claude for a model this
        # Mac runs besides, each given Meta's tools through the same MCP relay, and its effort only if it takes one)
        commands = commands and takes_commands(model)  # (only Claude's models act)
        run = modelslib.launch(model, "auto" if commands else "default", effort)
        if run["missing"]:
            raise OSError(wording.say("meta.apple_not_built") if run["program"] == "apple" else
                          wording.say("meta.runner_missing", program=run["program"]))
        self.program = run["program"]
        self.runs = self.program if self.program in ("claude", "codex") else f"{self.program}-claude"  # (what runs it: runtime info)
        exe, effort = (exe, effort) if self.program == "claude" else (run["exe"], run["effort"])
        self.chat, self.key, self.model, self.effort, self.commands = chat, key, model, effort, commands
        self.window = window(model)  # (tokens its session holds: what it says, once it's asked: first)
        self.overhead = (len(system) + len(json.dumps(tools))) // CHARS_PER_TOKEN  # (its instructions' and tools', at most)
        self.roots = [os.path.realpath(r) for r in roots if r]
        self.protect = [os.path.realpath(r) for r in protect if r] if commands else []
        self.started_mode = None  # (the mode claude says the session's in: its init's)
        self.turn, self.fresh, self.stderr, self.closed = None, True, [], False
        self.answers = {}  # (the control requests it's made, by id: what to do with their answers)
        self.allowed = set()  # (paths the user referenced: read when it asks)
        self.token = uuid.uuid4().hex
        self.cwd = cwd
        sock = ToolSocket.get()
        sock.add(self.token, lambda tool, args: chat._tool(self, tool, args), tools,
                 python=lambda args: chat._python(self, args))
        relay = {"mcpServers": {"meta": {"command": sys.executable, "args": ["-I", "-c", RELAY, sock.path, self.token]}}}
        args = ["--system-prompt", system, "--tools", ",".join(READ_TOOLS + (COMMAND_TOOLS if commands else ())),
                "--mcp-config", json.dumps(relay), "--strict-mcp-config",
                "--allowedTools", ",".join(f"mcp__meta__{t['name']}" for t in tools),
                "--no-session-persistence",  # (no transcript: it would be listed with your chats)
                "--disable-slash-commands", "--setting-sources", ""]
        for root in self.roots:
            args += ["--add-dir", root]
        if self.protect:  # (Claude Code's own rule, so an edit Auto mode would let by unasked is refused too: //, from /)
            args += ["--settings", json.dumps({"permissions": {"deny": [
                f"{t}(/{r}/**)" for r in self.protect for t in EDIT_TOOLS]}})]
        if self.program == "codex":
            try:
                self.proc = codex.CodexProc(
                    self.on_msg, self.on_exit, cwd, model, effort=effort, exe=exe, ephemeral=True,
                    catalog=codex_catalog(), dispatch=GLib.idle_add, meta_system=system,
                    meta_relay=relay["mcpServers"]["meta"],
                )
            except OSError:
                sock.drop(self.token)
                raise
        elif modelslib.proxied(model) is not None:  # (another host's: run there, Meta's tools here, relayed)
            self.proc = MetaProxiedProc(self.on_msg, self.on_exit, cwd, model, "default", effort=effort, extra=args)
        else:
            self.proc = MetaProc(self.on_msg, self.on_exit, cwd, model, run["mode"], effort=effort, extra=args,
                                 env=AGENT_ENV, exe=exe, env_of=env_of)

    def ask(self, text, turn):
        """A message (turn: what the reply's made of, as it comes: its text, actions, what it shows). A session's
        first is sent once it's said how big its window is (first)."""
        self.turn = turn
        if self.fresh:
            self.usage(lambda usage: self.first(text, usage, turn))
        else:
            self.proc.user(text)

    def first(self, text, usage, turn):
        """A session's first message, sent if its window holds it: its instructions' and tools' tokens (usage;
        pi-claude's says none till it's started: overhead, then) and the message's, at most (weight). If not, it isn't
        sent, and the chat's exhausted with what it would take (MetaChat._done). A conversation that's empty (cleared,
        or new: nothing earlier it carries) starts from empty: what the user brought (turn's brought: what they wrote,
        referenced, quoted) is counted, with the instructions and tools only as the session counts them, not Meta's
        own words (the app's index, the notebook) nor overhead's guess, as Clear's the way out of Context Exhausted,
        which those would leave it in on a small window. Where the session's own count of its instructions and tools
        is more than the window alone, it's too small for Meta (small): no clearing helps."""
        if self.closed or self.turn is not turn or turn.get("stopped"):
            return
        self.window = usage.get("maxTokens") or self.window
        counted = usage.get("totalTokens") or 0  # (none: pi-claude's, till it's started)
        carried = turn.get("carried", True)
        need = (counted or (self.overhead if carried else 0)) + \
            -(-weight(text if carried else turn.get("brought", text)) // CHARS_PER_TOKEN)
        if counted > self.window or need > self.window:
            self.turn = None
            self.chat._done(self, "", None, turn, need=need, small=counted > self.window)
            return
        self.fresh = False
        self.proc.user(text)

    def usage(self, then):
        """then(usage): how much of its context window the session's used (Claude Code's get_context_usage); {} if
        it didn't say."""
        def said(usage):
            modelslib.remember_window(self.model, usage.get("maxTokens"))  # (its next session's, before it says)
            then(usage)
        self.answers[self.proc.control({"subtype": "get_context_usage"})] = said

    def stop(self):
        """The turn interrupted (Esc, the stop button): what's been said kept; the session too, unless it won't stop."""
        if self.turn is None or self.turn.get("stopped"):
            return
        self.turn["stopped"] = True
        self.chat._drop_asking(deny=True)
        if self.fresh:  # (its first message not sent yet, its window asked for: nothing to interrupt, the session kept)
            turn, self.turn = self.turn, None
            self.chat._done(self, "", None, turn)
            return
        self.proc.control({"subtype": "interrupt"})
        turn = self.turn
        GLib.timeout_add(self.STOP_WAIT, lambda: self.turn is turn and self.close() and False)

    def close(self):
        """Done with: its turn ended where it is, the session stopped."""
        if self.closed:
            return False
        self.closed = True
        ToolSocket.get().drop(self.token)
        turn, self.turn = self.turn, None
        self.proc.kill()
        if turn is not None:
            self.chat._done(self, "".join(turn["text"]).strip(), None, turn)
        self.chat.agent_gone(self)
        return False

    def under(self, path, roots):
        """Whether path (as the session gives it: from its folder) is one of roots or under one."""
        path = os.path.realpath(os.path.join(self.cwd, os.path.expanduser(str(path))))
        return any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)

    def may_read(self, path):
        """Whether a path is one it reads without asking: under a root, or referenced."""
        if not path:
            return False
        path = os.path.realpath(os.path.join(self.cwd, os.path.expanduser(str(path))))
        roots = [*self.roots, *(p for p in self.allowed if os.path.isdir(p))]
        return path in self.allowed or any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)

    def permission(self, msg):
        rid, req = msg["request_id"], msg.get("request") or {}
        name, inp = req.get("tool_name") or "", req.get("input") or {}
        if name.startswith("mcp__meta__") or name in READ_TOOLS and self.may_read(inp.get("file_path") or inp.get("path")):
            self.proc.respond(rid, {"behavior": "allow", "updatedInput": inp})
        elif name in EDIT_TOOLS and self.under(inp.get("file_path") or "", self.protect):
            self.proc.respond(rid, {"behavior": "deny", "message": "Those are the app's own files: change them with "
                                    "your tools."})
        elif name in READ_TOOLS or self.commands and name in COMMAND_TOOLS:
            self.chat.ask_permission(self, rid, name, inp)
        elif name == "AskUserQuestion":  # (Meta's a conversation: a question is asked in its reply)
            self.proc.respond(rid, {"behavior": "deny", "message": "Ask the user in your reply instead; they answer "
                                    "there."})
        elif name in COMMAND_TOOLS:
            self.proc.respond(rid, {"behavior": "deny", "message": f"{NAME} doesn't run commands or edit files here "
                                    "(Settings → Meta → Run commands, on Claude's models): say what the user would run."})
        else:
            self.proc.respond(rid, {"behavior": "deny", "message": f"{NAME} reads, and acts with its own tools: it "
                                    f"doesn't use {name}."})

    def answer(self, rid, allow, name, inp):
        """The user's answer to a read or command it asked for (MetaChat.ask_permission)."""
        if self.closed:
            return
        if allow:
            if name in READ_TOOLS and (path := inp.get("file_path") or inp.get("path")):
                self.allowed.add(os.path.realpath(os.path.join(self.cwd, os.path.expanduser(str(path)))))
            self.proc.respond(rid, {"behavior": "allow", "updatedInput": inp})
        else:
            self.proc.respond(rid, {"behavior": "deny", "message": f"The user didn't let you {name} that."})

    def on_msg(self, proc, msg):
        if proc is not self.proc or self.closed:
            return False
        t, turn = msg.get("type"), self.turn
        if t == "system" and msg.get("permissionMode"):  # (the mode it's in: Auto, if claude gave it for this model)
            self.started_mode = msg["permissionMode"]
            if self.commands and (self.started_mode != "auto") != (self.model in NO_AUTO):
                (NO_AUTO.add if self.started_mode != "auto" else NO_AUTO.discard)(self.model)
                sync_rows()
        if t == "system" and msg.get("subtype") == "compact_boundary":  # (summarised: less of its window used now)
            self.chat.context_soon(self)
        elif t == "system" and msg.get("subtype") == "baude_context" and self is self.chat.agent:  # (pi-claude's: the
            self.chat.show_usage(msg.get("usage") or {})  # tokens of the request it's sent, as MLX's server says)
        said = widgets.compact_said(msg) if turn is not None else None  # (compacting: said in the reply)
        if said is not None and said[0] != "summary":
            self.chat._compaction(self, turn, *said)
        if t == "_stderr":
            self.stderr = (self.stderr + [msg.get("text", "")])[-20:]
            if msg.get("warn") and turn is not None:  # (its tools didn't reach the model's host: over the reply)
                turn.setdefault("warned", []).append(msg.get("text", ""))
        elif t == "control_request":
            if (msg.get("request") or {}).get("subtype") == "can_use_tool":
                self.permission(msg)
            else:
                proc.send({"type": "control_response", "response": {
                    "subtype": "error", "request_id": msg.get("request_id"),
                    "error": f"meta: unsupported {(msg.get('request') or {}).get('subtype')}"}})
        elif t == "control_response":
            r = msg.get("response") or {}
            if r.get("request_id") == proc.init_rid and r.get("subtype") == "success":
                listed((r.get("response") or {}).get("models"))  # (what this machine's claude runs: Intelligence)
            then = self.answers.pop(r.get("request_id"), None)
            if then is not None:
                then((r.get("response") or {}) if r.get("subtype") == "success" else {})
        elif turn is None or msg.get("parent_tool_use_id"):
            pass
        elif t == "stream_event":
            ev = msg.get("event") or {}
            if ev.get("type") == "message_start":  # (a message of the turn's, after a tool's: a paragraph of its own)
                turn["streamed"] = False
                if "".join(turn["text"]).strip() and not "".join(turn["text"]).endswith("\n\n"):
                    turn["text"].append("\n\n")
            delta = ev.get("delta") or {}
            if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                turn["text"].append(delta["text"])
                turn["streamed"] = True
                self.chat._said_more(self, turn, delta["text"])
        elif t == "assistant":
            self.chat.context_soon(self)  # (its request counted: what the window holds now, its tools' calls asked)
            for c in (msg.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    self.chat._tool_started(self, turn, c)
                elif c.get("type") == "text" and not turn.get("streamed") and c.get("text"):
                    turn["text"].append(c["text"])
                    self.chat._said_more(self, turn, c["text"])
        elif said is not None and said[0] == "summary" and turn["items"] and \
                turn["items"][-1]["kind"] == "compact" and turn["items"][-1]["state"] == "done" and \
                not turn["items"][-1].get("summary"):
            self.chat._compaction(self, turn, *said)  # (what it compacted the conversation to: under its marker)
        elif t == "user":  # (what its tools gave back)
            for c in (msg.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    self.chat._tool_ended(self, turn, c)
        elif t == "result":
            self.turn = None
            text = "".join(turn["text"]).strip() or ("" if msg.get("is_error") else (msg.get("result") or "").strip())
            error, outgrown = None, False
            if msg.get("is_error") and not turn.get("stopped"):
                error = (msg.get("result") or "; ".join(map(str, msg.get("errors") or [])) or
                         wording.say("meta.claude_error"))[-600:]
                if outgrown := bool(OVERFLOW_RE.search(error)):
                    error = None  # (the conversation's more than the window holds: the chat's exhausted, _done)
                elif "log" in error.lower() and "in" in error.lower():
                    error += "\n\n" + wording.say("meta.login_hint")
            self.chat._done(self, text, error, turn, outgrown)
        return False

    def on_exit(self, code):
        if not self.closed:
            GLib.timeout_add(self.EXIT_WAIT, self._exited, code)
        return False

    def _exited(self, code):
        if self.closed:
            return False
        self.closed = True
        ToolSocket.get().drop(self.token)
        turn, self.turn = self.turn, None
        if turn is not None:
            error = None if turn.get("stopped") else \
                ("\n".join(self.stderr[-6:]).strip() or wording.say("meta.exited", program=self.runs, code=code))[-600:]
            self.chat._done(self, "".join(turn["text"]).strip(), error, turn)
        self.chat.agent_gone(self)
        return False
