"""Claude Code's process: the claude binary found, and run headless (`claude -p`) speaking the stream-json protocol on
stdin and stdout, the one the Agent SDK speaks. No GTK: Baude delivers what it says on its main loop (its ClaudeProc's
dispatch), the headless server (baudelib.server) on its own threads."""
import glob
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import threading

# what a claude started from inside another one inherits (from a Claude Desktop or terminal session) and must not:
# claude_env leaves it out (fixes.py's claude runs too)
HOST_SESSION_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID",
                    "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                    "CLAUDE_PID", "CLAUDE_CODE_DESKTOP_APP_VERSION", "CLAUDE_CODE_SESSION_ATTENDED")


# (value, label). MODELS is only a fallback: the CLI's initialize response lists the real
# models, and reports the permission mode it actually started in.
MODELS = [("opus", "Opus"), ("fable", "Fable"), ("sonnet", "Sonnet"), ("haiku", "Haiku")]


MODES = [("default", "Manual"), ("acceptEdits", "Accept edits"), ("plan", "Plan"),
         ("auto", "Auto"), ("dontAsk", "Don't ask"), ("bypassPermissions", "Bypass permissions")]


EFFORT_LABELS = {"off": "No thinking", "low": "Low effort", "medium": "Medium effort", "high": "High effort",
                 "xhigh": "Extra-high effort", "max": "Max effort"}


MACOS_DESKTOP_CLAUDE = "~/Library/Application Support/Claude/claude-code"


def claude_exe():
    """The claude binary: BAUDE_CLAUDE, else claude on PATH, else (macOS) the newest one the Claude desktop app
    keeps (claude-code/<version>/<build>/claude.app), as the desktop app puts none on PATH."""
    exe = os.environ.get("BAUDE_CLAUDE") or shutil.which("claude")
    if exe or sys.platform != "darwin":
        return exe or "claude"
    found = []
    for path in glob.glob(os.path.join(os.path.expanduser(MACOS_DESKTOP_CLAUDE),
                                       "*", "*", "claude.app", "Contents", "MacOS", "claude")):
        version = os.path.relpath(path, os.path.expanduser(MACOS_DESKTOP_CLAUDE)).split(os.sep)[0]
        if os.access(path, os.X_OK) and re.fullmatch(r"\d+(\.\d+)*", version):
            found.append((tuple(int(n) for n in version.split(".")), os.path.getmtime(path), path))
    return max(found)[2] if found else "claude"


_CLAUDE_VERSION = []


def claude_version():
    """The installed Claude Code's version (2.1.300), or None."""
    if not _CLAUDE_VERSION:
        exe = claude_exe()
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15,
                                 stdin=subprocess.DEVNULL).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        m = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", out)
        _CLAUDE_VERSION.append(m.group(0) if m else None)
    return _CLAUDE_VERSION[0]


# what a claude started from inside another one inherits (baude run from a Claude Desktop or terminal
# session): it would label its transcript as that host's ("Claude Desktop"), claim its session id and
# talk to its messaging socket. baude's claude is its own session.
def claude_env():
    return {k: v for k, v in os.environ.items() if k not in HOST_SESSION_ENV}


def spent_since(proc, msg):
    """What a result adds to what's been spent. Its total_cost_usd is the session's so far, not the turn's: every turn's
    again, added up, came to six times what was spent in seven turns. On --resume claude counts on from the session's
    last cost-state (written as it exits; not if it's killed), so proc.cost_seen starts there (h_result) and is the
    last total it gave; one lower than that is a count started again."""
    spent = float(msg.get("total_cost_usd") or 0)
    seen = getattr(proc, "cost_seen", 0.0)
    if proc is not None:
        proc.cost_seen = spent
    return spent - seen if spent >= seen else spent


class ClaudeProc:
    """Owns the claude subprocess. Callbacks are delivered through dispatch (a callable that runs what it's given): as
    they're read, on the reading threads, unless a subclass says otherwise (Baude's, on the GTK main loop)."""

    dispatch = staticmethod(lambda fn: fn())

    def __init__(self, on_msg, on_exit, cwd, model, mode, resume=None, effort=None, extra=(), env=None, exe=None):
        self.on_msg, self.on_exit = on_msg, on_exit
        self.req_ids = itertools.count(1)
        self.asks = {}  # request id -> a permission it asked for and hasn't been answered (BaudeWindow.detach_proc)
        exe = exe or claude_exe()  # (apple-claude for Apple's model: start_proc)
        args = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages", "--permission-prompt-tool", "stdio",
                "--forward-subagent-text"]  # subagents' own messages, for the side panel
        if mode:  # the flag spells the protocol's "default" mode as "manual"
            args += ["--permission-mode", "manual" if mode == "default" else mode]
        if model:
            args += ["--model", model]
        if resume:
            args += ["--resume", resume]
        if effort:
            args += ["--effort", effort]
        args += list(extra)
        self.p = subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, bufsize=1, env=claude_env() | (env or {}))
        threading.Thread(target=self._read, args=(self.p.stdout, False), daemon=True).start()
        threading.Thread(target=self._read, args=(self.p.stderr, True), daemon=True).start()
        self.init_rid = self.control({"subtype": "initialize", "hooks": None})

    def _read(self, stream, is_err):
        for line in stream:
            line = line.strip()
            if not line:
                continue
            if is_err:
                self.dispatch(lambda m={"type": "_stderr", "text": line}: self.on_msg(self, m))
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                msg = {"type": "_stderr", "text": line}
            # (its handler looked up as it's delivered, not as it's read: a chat left mid-turn, or opened again, in between
            # gets what's after from its new one, BaudeWindow.detach_proc and attach_proc)
            self.dispatch(lambda m=msg: self.on_msg(self, m))
        if not is_err:
            code = self.p.wait()
            self.close_stdin()
            self.dispatch(lambda: self.on_exit(code))

    def send(self, obj):
        try:
            self.p.stdin.write(json.dumps(obj) + "\n")
            self.p.stdin.flush()
        except (BrokenPipeError, ValueError):
            self.close_stdin()

    def close_stdin(self):
        """Close our end of the pipe now: left to the garbage collector, unsent data would be
        flushed into a dead process and print a BrokenPipeError traceback."""
        try:
            self.p.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass  # close() still closes the file when its final flush fails

    def user(self, content):
        """content: a string, or a list of content blocks (text / image / document)."""
        self.send({"type": "user", "message": {"role": "user", "content": content},
                   "parent_tool_use_id": None, "session_id": ""})

    def control(self, request):
        rid = f"baude_{next(self.req_ids)}"
        self.send({"type": "control_request", "request_id": rid, "request": request})
        return rid

    def respond(self, request_id, response):
        self.send({"type": "control_response",
                   "response": {"subtype": "success", "request_id": request_id, "response": response}})

    def kill(self):
        if self.p.poll() is None:
            self.p.terminate()
        self.close_stdin()
