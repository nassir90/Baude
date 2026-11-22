#!/usr/bin/env python3
"""What the render rigs and the benches share: a throwaway environment (fake claude, temp config dirs, no real host,
launcher, ssh, menu bar or GitHub), set up on import, then a real BaudeApp and the helpers that drive its windows by
pumping the GLib main loop. tests/screenshots.py, tests/parity/desktop.py and tests/bench_*.py import it first."""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

TMP = os.path.realpath(tempfile.mkdtemp(prefix="baude-test-"))  # (macOS: /var is a link to /private/var)
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg-data")  # never touch the real launcher
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "xdg-config")  # nor the real settings
# nor your clusters, nor your containers: Signal and its name catalogue read kubeconfig contexts, what runs in local
# clusters and the databases in Docker (a scene that wants them sets up its own kubeconfig and fakes)
os.environ["KUBECONFIG"] = os.path.join(TMP, "no-kubeconfig")
os.environ["DOCKER_HOST"] = "unix://" + os.path.join(TMP, "no-docker.sock")
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(TMP, "claude-config")  # nor real sessions
os.environ["XDG_CACHE_HOME"] = os.path.join(TMP, "xdg-cache")
os.environ["BAUDE_GH"] = ""  # (no issue filed on GitHub for the feedback saved here)
os.environ["BAUDE_KEEP_FILING"] = "0"  # (nor a note going up by itself, to a stand-in gh, mid-run)
os.environ["BAUDE_UPDATE"] = "0"  # (no git fetched, no restart onto a new version)
# nor this machine's host process, nor ssh's shared connections: a folder of the run's own (short: a socket's path is)
os.environ["BAUDE_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="bd-", dir="/tmp")
os.environ["BAUDE_SSH"] = "false"  # (nor anywhere over ssh: a host added by hand is never reached)
os.environ["BAUDE_MENU_BAR"] = "0"  # (nor an item in the real menu bar, from a host process a run starts)
# this machine's host process, which runs Baude's chats, in the run's own process (its claudes the fake's, as before)
os.environ["BAUDE_HOST_IN_PROCESS"] = "1"
os.environ["FAKE_CLAUDE_SESSION_IDS"] = "1"  # (the fake's id the one it's given, as claude's is: the host's chats' ids)
# the stand-in claude, run by this Python: its #!/usr/bin/env python3 can find an older one first (macOS's 3.9)
FAKE_CLAUDE = os.path.join(TMP, "fake-claude")
with open(FAKE_CLAUDE, "w") as f:
    f.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(os.path.join(HERE, "fake_claude.py"))} "$@"\n')
os.chmod(FAKE_CLAUDE, 0o755)
os.environ["BAUDE_CLAUDE"] = FAKE_CLAUDE
os.environ["FAKE_CLAUDE_LOG"] = os.path.join(TMP, "fake.log")

import baude as _baude  # noqa: E402
import baudelib.claude  # noqa: E402
import baudelib.transcripts  # noqa: E402
import baudelib.models  # noqa: E402
import codeview  # noqa: E402
import feedback  # noqa: E402
import fixes  # noqa: E402
import gitview  # noqa: E402
import kubeview  # noqa: E402
import lookup  # noqa: E402
import schemaview  # noqa: E402
import widgets  # noqa: E402
from baude import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402,F401

_MOVED = (widgets, codeview, kubeview, schemaview, gitview, feedback,  # (what was in baude.py: the viewers, what they
          baudelib.claude, baudelib.transcripts, baudelib.models)        # share, and Claude Code's process and transcripts)


class _Baude:
    """baude, as the rigs use it: a name a scene replaces is replaced wherever it's held: in baude, in lookup (⌘K's
    engine, whose own functions call it), and in the modules code moved to out of baude.py (widgets and the viewers'),
    whose code saw baude's when it was there. A name baude doesn't hold is read from those (SIGNAL is widgets')."""

    def __getattr__(self, name):
        for m in (_baude, *_MOVED, lookup):
            if name in vars(m):
                return getattr(m, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        for m in [m for m in (_baude, *_MOVED, lookup) if name in vars(m)] or [_baude]:
            setattr(m, name, value)


baude = _Baude()
widgets.SLIDE_MS = 0  # (a side bar opens and closes at once here)
widgets.Lightbox.FLY_MS = 0  # (the large view opens and closes at once here)

if sys.platform == "darwin":  # never touch the real launcher, nor register a stand-in one with LaunchServices
    baude.LAUNCHER = baude.MacLauncher(os.path.join(TMP, "Applications"))
    baude.LAUNCHER.refresh = lambda: None

OPTS = argparse.Namespace(cwd=TMP, model=None, resume=None)


def pump(seconds=0.05):
    end = time.monotonic() + seconds
    ctx = GLib.MainContext.default()
    while time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.002)


def wait_for(cond, what, timeout=10):
    end = time.monotonic() + timeout
    ctx = GLib.MainContext.default()
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what() if callable(what) else what}")


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def find(root, cls, pred=lambda w: True):
    return [w for w in walk(root) if isinstance(w, cls) and pred(w)]


def button(root, label):
    found = find(root, Gtk.Button, lambda b: b.get_label() == label)
    return found[0] if found else None


def labels_text(root):
    return "\n".join(w.get_text() for w in find(root, Gtk.Label))


def type_and_send(win, text):
    win.input.get_buffer().set_text(text)
    win.on_send()


MINI_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj
trailer<</Root 1 0 R>>
"""


class App:
    """One application for the whole run; a fresh window per scene."""

    def __init__(self):
        self.app = baude.BaudeApp(OPTS)
        self.app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)  # don't hand off to a running baude
        self.app.register(None)
        baude_css = Gtk.CssProvider()
        baude_css.load_from_string(baude.CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), baude_css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.app.hold()

    def window(self, opts=OPTS):
        open(os.environ["FAKE_CLAUDE_LOG"], "w").close()
        win = baude.BaudeWindow(self.app, opts)
        if getattr(self, "next_size", None):  # screenshots: size it before it is shown
            win.set_default_size(*self.next_size)
        win.present()
        # (a new chat: in this machine's host process, a draft there, its claude the fake, ready: initialize answered)
        wait_for(lambda: win.session_id and win.proc is not None and win.mode_dd.values, "the chat's claude to start")
        return win


def close(win):
    if win.proc:
        win.proc.kill()
    win.destroy()
    pump(0.05)


def baude_png(w, h):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    pb.fill(0x123456FF)
    return pb.save_to_bufferv("png", [], [])[1]


class _ImageServer:
    """A tiny local HTTP server for web images (nothing leaves the machine)."""

    def __enter__(self):
        import http.server, threading as th  # noqa: E401
        png = baude_png(300, 150)

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/pic.png":
                    body, kind, code = png, "image/png", 200
                elif self.path == "/page.html":
                    body, kind, code = b"<html></html>", "text/html", 200
                else:
                    body, kind, code = b"nope", "text/plain", 404
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        th.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __exit__(self, *exc):
        self.httpd.shutdown()


def write_chain(folder, sid, title, msg_ids, mtime, born=None, entrypoint=None):
    """A transcript whose records carry the given uuids (continued copies share them);
    born: the first record's timestamp."""
    d = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", re.sub(r"[^A-Za-z0-9]", "-", folder))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{sid}.jsonl")
    with open(path, "w") as f:
        first = {"type": "user", "cwd": folder, "uuid": f"00000000-0000-4000-8000-{msg_ids[0]:012d}",
                 "message": {"content": "start"}}
        if born:
            first["timestamp"] = born
        if entrypoint:
            first["entrypoint"] = entrypoint
        f.write(json.dumps(first) + "\n")
        for i in msg_ids[1:]:
            f.write(json.dumps({"type": "assistant", "uuid": f"00000000-0000-4000-8000-{i:012d}",
                                "message": {"content": [{"type": "text", "text": f"m{i}"}]}}) + "\n")
        f.write(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": sid}) + "\n")
    os.utime(path, (mtime, mtime))
    return path


def write_bg_session(folder, sid, live=True):
    """A transcript with a background command and a background subagent (and its own file)."""
    d = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", re.sub(r"[^A-Za-z0-9]", "-", folder))
    os.makedirs(os.path.join(d, sid, "subagents"), exist_ok=True)
    path = os.path.join(d, f"{sid}.jsonl")
    out = os.path.join(TMP, "bgx1.output")
    with open(out, "w") as f:
        f.write("compiling…\nBUILD OK\n")
    recs = [
        {"type": "user", "cwd": folder, "message": {"content": "build it and survey the tests"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tb1", "name": "Bash", "input": {
            "command": "make", "description": "Build", "run_in_background": True}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tb1", "content":
            f"Command running in background with ID: bgx1. Output is being written to: {out}"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "ta1", "name": "Agent", "input": {
            "description": "Survey the tests", "subagent_type": "Explore", "prompt": "Count the tests"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "ta1", "content": [
            {"type": "text", "text": "Async agent launched successfully.\nagentId: a123 (internal ID)"}]}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Both are running."}]}},
    ]
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(r) for r in recs) + "\n")
    with open(os.path.join(d, sid, "subagents", "agent-a123.jsonl"), "w") as f:
        for r in [{"type": "user", "isSidechain": True, "message": {"content": "Count the tests"}},
                  {"type": "assistant", "isSidechain": True, "message": {"content": [
                      {"type": "text", "text": "Looking at the test files."},
                      {"type": "tool_use", "id": "sg1", "name": "Grep", "input": {"pattern": "^def test_"}}]}},
                  {"type": "user", "isSidechain": True, "message": {"content": [
                      {"type": "tool_result", "tool_use_id": "sg1", "content": "28"}]}}]:
            f.write(json.dumps(r) + "\n")
    if not live:
        old = time.time() - 3600
        os.utime(path, (old, old))
    return path, os.path.join(d, sid, "subagents", "agent-a123.jsonl")


def write_session(folder_cwd, sid, title=None, relocated=None):
    d = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", re.sub(r"[^A-Za-z0-9]", "-", folder_cwd))
    os.makedirs(d, exist_ok=True)
    recs = [
        {"type": "user", "cwd": folder_cwd, "sessionId": sid, "isSidechain": False,
         "message": {"role": "user", "content": [
             {"type": "text", "text": "<system-reminder>ignore me</system-reminder>"},
             {"type": "text", "text": "Fix the **login** bug"}]}},
        {"type": "assistant", "cwd": folder_cwd, "sessionId": sid, "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "Look at auth.py"},
            {"type": "text", "text": "Let me look at `auth.py`."},
            {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "auth.py"}}]}},
        {"type": "user", "cwd": folder_cwd, "sessionId": sid, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "def login(): ..."}]}},
        {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "SIDECHAIN"}]}},
        {"type": "assistant", "cwd": folder_cwd, "sessionId": sid, "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Fixed it."}]}},
    ]
    if title:
        recs.append({"type": "custom-title", "customTitle": title, "sessionId": sid})
    if relocated:
        recs.append({"type": "relocated", "relocatedCwd": relocated, "sessionId": sid})
    path = os.path.join(d, f"{sid}.jsonl")
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(r) for r in recs) + "\n")
    return path


def _git_repo(folder):
    """A small repository with an env var, a constant, a FastAPI route, a branch and a rewritten commit."""
    files = {
        "svc/app/settings.py": "class Settings(BaseSettings):\n"
                               "    model_config = SettingsConfigDict(env_prefix=\"SVC_\")\n"
                               "    runner_key: str = \"\"\n",
        "svc/app/routes.py": "router = APIRouter(prefix=\"/v1/private/jobs\")\n\n\n"
                             "@router.post(\"/claim\")\nasync def claim():\n    return {}\n",
        "svc/app/main.py": "app.include_router(routes.router)\n",
        "svc/app/loop.py": "".join(f"# filler {i}\n" for i in range(1, 11)) + "STALE_AFTER = 600\n"
                           + "def tick():\n    return STALE_AFTER\n" + "".join(f"# more {i}\n" for i in range(20)),
        "deploy/compose.yaml": "services:\n  svc:\n    environment:\n      SVC_RUNNER_KEY: ${SVC_RUNNER_KEY}\n",
        "svc/app/migrations/0001_jobs.py": "def upgrade():\n    op.create_table(\n        \"svc_jobs\",\n"
                                           "        sa.Column(\"runner_id\", sa.String()),\n    )\n",
        "deploy/.env.example": "SVC_RUNNER_KEY=examplesecretvalue\n",
        ".gitignore": ".env\n",
        ".env": "SVC_RUNNER_KEY=supersecretvalue\n",
    }
    for rel, text in files.items():
        os.makedirs(os.path.dirname(os.path.join(folder, rel)) or folder, exist_ok=True)
        with open(os.path.join(folder, rel), "w") as f:
            f.write(text)
    dates = {"GIT_AUTHOR_DATE": "2026-01-02T03:04:05Z", "GIT_COMMITTER_DATE": "2026-01-02T03:04:05Z"}
    git = lambda *a: subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", *a], cwd=folder,  # noqa: E731
                                    check=True, capture_output=True, text=True, env={**os.environ, **dates}).stdout.strip()
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "Start the service")
    git("checkout", "-q", "-b", "feature/x")
    with open(os.path.join(folder, "svc/app/extra.py"), "w") as f:
        f.write("EXTRA = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "An extra module")
    git("checkout", "-q", "main")
    with open(os.path.join(folder, "notes.md"), "w") as f:
        f.write("one\n")
    git("add", "-A")
    git("commit", "-q", "-m", "Add notes")
    old = git("rev-parse", "HEAD")
    with open(os.path.join(folder, "notes.md"), "w") as f:
        f.write("two\n")
    git("commit", "-q", "-a", "--amend", "-m", "Add notes")  # the first one is now on no branch
    return git, old, git("rev-parse", "HEAD")


def _git_view_repo(name):
    """A repository for the Git page: main with a merged side branch, pushed to a bare origin; feature, made from
    main in a worktree, with one commit pushed and one not, while main moved on by one. Times are fixed (local)."""
    base = os.path.join(TMP, name)
    repo, origin, wt = os.path.join(base, "repo"), os.path.join(base, "origin.git"), os.path.join(base, "feature-wt")
    os.makedirs(repo)

    def git(*a, cwd=repo, when=None):
        env = {**os.environ, **({"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else {})}
        return subprocess.run(["git", "-c", "user.name=Ada", "-c", "user.email=ada@example.com", *a], cwd=cwd, check=True,
                              capture_output=True, text=True, env=env).stdout.strip()

    def commit(path, text, msg, when, cwd=repo):
        with open(os.path.join(cwd, path), "a") as f:
            f.write(text)
        git("add", "-A", cwd=cwd)
        git("commit", "-q", "-m", msg, cwd=cwd, when=when)
        return git("rev-parse", "HEAD", cwd=cwd)
    git("init", "-q", "-b", "main")
    commit("app.py", "a = 1\n", "Start the app", "2026-10-20T10:00:00")
    git("checkout", "-q", "-b", "side")
    commit("side.py", "s = 1\n", "Side work", "2026-10-20T11:00:00")
    git("checkout", "-q", "main")
    commit("app.py", "b = 2\n", "Main work", "2026-10-20T12:00:00")
    git("merge", "-q", "--no-ff", "side", "-m", "Merge side", when="2026-10-20T13:00:00")
    merge = git("rev-parse", "HEAD")
    git("init", "-q", "--bare", origin, cwd=base)
    git("remote", "add", "origin", origin)
    git("push", "-q", "-u", "origin", "main")
    git("worktree", "add", "-q", "-b", "feature", wt, "main")
    f1 = commit("feature.py", "f = 1\nf = 2\nf = 3\n", "Feature: first", "2026-10-21T22:30:00", cwd=wt)
    git("push", "-q", "-u", "origin", "feature", cwd=wt)
    f2 = commit("feature.py", "g = 2\n", "Feature: second", "2026-10-21T23:15:00", cwd=wt)  # (not pushed)
    m2 = commit("app.py", "c = 3\n", "Main moves on", "2026-10-22T09:00:00")
    git("push", "-q", "origin", "main")
    return {"repo": repo, "wt": wt, "merge": merge, "f1": f1, "f2": f2, "m2": m2, "git": git}


TOOL_SDK_SAMPLE = """/* eslint-disable */
/**
 * This file was automatically generated by json-schema-to-typescript.
 */

/**
 * JSON Schema definitions for Claude CLI tool inputs
 */
export type ToolInputSchemas =
  | AgentInput
  | FileReadInput
  | TodoWriteInput;

export interface AgentInput {
  /**
   * A short (3-5 word) description of the task
   */
  description: string;
  /**
   * The task for the agent to perform
   */
  prompt: string;
  /**
   * The type of specialized agent to use for this task
   */
  subagent_type?: string;
  model?: "sonnet" | "opus" | "haiku";
}
export interface FileReadInput {
  /**
   * The absolute path to the file to read
   */
  file_path: string;
  /**
   * The line number to start reading from. Only provide if the file is too large to read at once
   */
  offset?: number;
}
export interface TodoWriteInput {
  /**
   * The updated todo list
   */
  todos: {
    content: string;
    status: "pending" | "in_progress" | "completed";
    activeForm: string;
  }[];
}
export interface EnterWorktreeInput {}
/**
 * The todo list after the update
 */
export interface TodoWriteOutput {
  /**
   * The todo list before the update
   */
  oldTodos: {
    content: string;
  }[];
}
"""
TOOL_DOCS_SAMPLE = """# Tools reference

| Tool | Description | Permission required |
| :--- | :--- | :--- |
| `Agent` | Spawns a [subagent](/docs/en/sub-agents) with its own context window to handle a task. See [Agent tool behavior](#agent-tool-behavior) | No |
| `Bash` | Executes shell commands in your environment. See [Bash tool behavior](#bash-tool-behavior) | Yes |
| `Read` | Reads the contents of files | No |
| `TodoWrite` | Manages the session task checklist | No |
"""
