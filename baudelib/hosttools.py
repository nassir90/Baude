"""The tools of an agent that runs on another host (baudelib.agentproxy): Read, Write, Edit, Bash, Grep and Glob, as an
MCP server on stdin and stdout (a JSON-RPC message a line), run on the chat's host, in the chat's folder. Each is
called by the agent as mcp__host__<tool>, asked for first as any tool of its is."""
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys

NAME = "host"  # (the server's name: its tools are mcp__host__Read, ...)
TOOLS = ("Read", "Write", "Edit", "Bash", "Grep", "Glob")
ALIASES = {"MultiEdit": "Edit", "LS": "Glob", "Exec": "Bash"}  # (another name for one of them, as --tools may give it)
READ_LINES = 2000
BASH_S, BASH_MAX_S = 120, 600
OUT_MAX = 30000  # (characters of a command's output, or a search's, given back)


def wanted(tools):
    """The tools of TOOLS a --tools list names (None: all of them), in TOOLS' order."""
    if tools is None:
        return list(TOOLS)
    names = {ALIASES.get(t.strip(), t.strip()) for t in tools}
    return [t for t in TOOLS if t in names]


SCHEMAS = {
    "Read": ("Read a text file: its lines, numbered (from offset, limit of them; 2000 at most).",
             {"file_path": {"type": "string", "description": "The file: absolute, or from the working folder"},
              "offset": {"type": "integer", "description": "The line to start at (1 is the first)"},
              "limit": {"type": "integer", "description": "How many lines"}}, ["file_path"]),
    "Write": ("Write a file, all of it (made, or replaced).",
              {"file_path": {"type": "string"}, "content": {"type": "string"}}, ["file_path", "content"]),
    "Edit": ("Replace text in a file: old_string, which must be there once (or every time, with replace_all), by "
             "new_string.",
             {"file_path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
              "replace_all": {"type": "boolean"}}, ["file_path", "old_string", "new_string"]),
    "Bash": ("Run a shell command in the working folder: its output and exit status.",
             {"command": {"type": "string"},
              "timeout": {"type": "integer", "description": "Seconds (default 120, 600 at most)"}}, ["command"]),
    "Grep": ("Search files for a regular expression: the matching lines, as path:line:text.",
             {"pattern": {"type": "string"}, "path": {"type": "string", "description": "A folder or file (default: "
                                                                                       "the working folder)"},
              "glob": {"type": "string", "description": "Only files whose names match, as *.py"}}, ["pattern"]),
    "Glob": ("Files whose paths match a pattern (**/*.py, say), newest first.",
             {"pattern": {"type": "string"}, "path": {"type": "string"}}, ["pattern"]),
}


class Tools:
    def __init__(self, cwd, only=None):
        self.cwd, self.only = os.path.realpath(os.path.expanduser(cwd)), wanted(only)

    def path(self, p):
        if not isinstance(p, str) or not p:
            raise ValueError("no path given")
        return os.path.normpath(os.path.join(self.cwd, os.path.expanduser(p)))

    def listed(self):
        return [{"name": n, "description": SCHEMAS[n][0],
                 "inputSchema": {"type": "object", "properties": SCHEMAS[n][1], "required": SCHEMAS[n][2]}}
                for n in self.only]

    def call(self, name, a):
        if name not in self.only:
            raise ValueError(f"no tool {name}")
        return getattr(self, name.lower())(a)

    def read(self, a):
        path, start = self.path(a.get("file_path")), max(1, int(a.get("offset") or 1))
        count = min(READ_LINES, max(1, int(a.get("limit") or READ_LINES)))
        out = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f, 1):
                if n >= start + count:
                    break
                if n >= start:
                    out.append(f"{n:6}\t{line.rstrip(chr(10))}")
        return "\n".join(out) if out else "(no lines there)"

    def write(self, a):
        path = self.path(a.get("file_path"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(a.get("content") or ""))
        return f"wrote {path}"

    def edit(self, a):
        path = self.path(a.get("file_path"))
        old, new = str(a.get("old_string") or ""), str(a.get("new_string") or "")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        found = text.count(old) if old else 0
        if not found:
            raise ValueError(f"old_string isn't in {path}")
        if found > 1 and not a.get("replace_all"):
            raise ValueError(f"old_string is in {path} {found} times: give more of it, or replace_all")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new) if a.get("replace_all") else text.replace(old, new, 1))
        return f"edited {path} ({found if a.get('replace_all') else 1} replaced)"

    def bash(self, a):
        timeout = min(BASH_MAX_S, max(1, int(a.get("timeout") or BASH_S)))
        try:
            p = subprocess.run(["/bin/bash", "-c", str(a.get("command") or "")], cwd=self.cwd, capture_output=True,
                               text=True, errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or "") + (e.stderr or "") if isinstance(e.stdout, str) else ""
            return cut(out) + f"\n(stopped after {timeout} s)"
        out = p.stdout + (("\n" if p.stdout and p.stderr else "") + p.stderr if p.stderr else "")
        return cut(out) + (f"\n(exit status {p.returncode})" if p.returncode else "")

    def grep(self, a):
        where = self.path(a.get("path") or ".")
        if rg := shutil.which("rg"):
            args = [rg, "-n", "--no-heading", "--color", "never", *(["-g", a["glob"]] if a.get("glob") else []),
                    "-e", str(a.get("pattern") or ""), where]
            p = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=60,
                               stdin=subprocess.DEVNULL)
            return cut(p.stdout) or ("(no matches)" if p.returncode == 1 else p.stderr.strip() or "(no matches)")
        pattern, out = re.compile(str(a.get("pattern") or "")), []
        for path in self.files(where, a.get("glob")):
            try:
                with open(path, encoding="utf-8", errors="strict") as f:
                    out += [f"{path}:{n}:{line.rstrip()}" for n, line in enumerate(f, 1) if pattern.search(line)]
            except (OSError, UnicodeDecodeError):
                continue
            if sum(map(len, out)) > OUT_MAX:
                break
        return cut("\n".join(out)) or "(no matches)"

    def glob(self, a):
        where = self.path(a.get("path") or ".")
        pattern = str(a.get("pattern") or "*")
        tops = {pattern, pattern[3:]} if pattern.startswith("**/") else {pattern}  # (**/*.py: the top folder's too)
        found = [p for p in self.files(where) if any(fnmatch.fnmatch(os.path.relpath(p, where), t) for t in tops)]
        found.sort(key=lambda p: -os.path.getmtime(p))
        return cut("\n".join(found)) or "(no files)"

    @staticmethod
    def files(where, glob=None):
        if os.path.isfile(where):
            yield where
            return
        for root, dirs, names in os.walk(where):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
            for n in names:
                if glob is None or fnmatch.fnmatch(n, glob):
                    yield os.path.join(root, n)


def cut(text):
    return text if len(text) <= OUT_MAX else text[:OUT_MAX] + f"\n(… {len(text) - OUT_MAX} more characters)"


def serve(cwd, only=None, rfile=None, wfile=None):
    """The MCP server: a message a line on rfile (stdin), its answers on wfile (stdout), till rfile ends."""
    rfile, wfile = rfile or sys.stdin, wfile or sys.stdout
    tools = Tools(cwd, only)

    def send(msg):
        wfile.write(json.dumps(msg) + "\n")
        wfile.flush()
    for line in rfile:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            continue
        if method == "initialize":
            result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or "2025-06-18",
                      "capabilities": {"tools": {}}, "serverInfo": {"name": NAME, "version": "1"}}
        elif method == "tools/list":
            result = {"tools": tools.listed()}
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                result = {"content": [{"type": "text", "text": tools.call(p.get("name"), p.get("arguments") or {})}]}
            except Exception as e:  # (said to the model, which may try again)
                result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}})
            continue
        send({"jsonrpc": "2.0", "id": mid, "result": result})
