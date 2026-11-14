#!/usr/bin/env python3
"""Screenshot matrix: every screen and render baude has, in light and dark.

Run through tests/screenshots.sh (headless, inside Xvfb):

    tests/screenshots.sh                 # all scenes -> screenshots/
    tests/screenshots.sh -k permission   # scenes whose name contains "permission"
    tests/screenshots.sh --out DIR

Writes <scene>-light.png / <scene>-dark.png, an index.html gallery (one theme at a time,
with a Light / Dark switch) and matrix-light.png / matrix-dark.png contact sheets. Scenes drive a real BaudeWindow against tests/fake_claude.py, the same
way the UI tests do, so the pictures always match the current code.
"""
import argparse
import base64
import html
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_baude as T  # noqa: E402  (sets up the throwaway env: fake claude, temp config dirs)
from test_baude import Adw, Gdk, GdkPixbuf, GLib, Gtk, baude  # noqa: E402
from gi.repository import Graphene  # noqa: E402

from PIL import Image, ImageDraw, ImageGrab  # noqa: E402

SIDEBAR = 250  # the sessions sidebar is on in every scene; scenes are this much wider for it
W, H = 1000 + SIDEBAR, 760
SCENES = []


def scene(name, description, size=(W, H)):
    def register(fn):
        SCENES.append((name, description, size, fn))
        return fn
    return register


def opts(cwd=None, model=None):
    return argparse.Namespace(cwd=cwd or T.TMP, model=model, resume=None)


def age(path, seconds=3600):
    """A past session nobody is writing (a fresh file would look active elsewhere)."""
    old = time.time() - seconds
    os.utime(path, (old, old))


def png_bytes(w, h, rgba):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    pb.fill(rgba)
    return pb.save_to_bufferv("png", [], [])[1]


def ask_permission(win, name, inp, tool_id):
    """What manual mode shows: the tool card, asking whether to allow it."""
    win.render_assistant([{"type": "tool_use", "id": tool_id, "name": name, "input": inp}])
    win.on_msg(win.proc, {"type": "control_request", "request_id": f"perm-{tool_id}",
                          "request": {"subtype": "can_use_tool", "tool_name": name, "input": inp,
                                      "tool_use_id": tool_id}})


def tool_with_output(win, name, inp, tool_id, content, is_error=False, open_input=True):
    win.render_assistant([{"type": "tool_use", "id": tool_id, "name": name, "input": inp}])
    win.render_tool_results([{"type": "tool_result", "tool_use_id": tool_id, "content": content,
                              "is_error": is_error}])
    card = win.tool_cards[tool_id]
    card.input_toggle.set_expanded(open_input)
    card.output_toggle.set_expanded(True)
    return card


# --------------------------------------------------------------- scenes --

@scene("01-empty", "A fresh window: header controls, empty chat, input box, status bar")
def _(app):
    return app.window(opts())


@scene("02-turn-complete", "A finished turn: thinking, markdown reply, a tool call (collapsed), cost/context")
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "list the files")
    T.wait_for(lambda: T.button(win, "Allow"), "permission")
    T.button(win, "Allow").emit("clicked")
    T.wait_for(lambda: not win.busy, "turn end")
    return win


@scene("03-working", "Mid-turn: spinner on the tool card, red stop button, 'working…' status")
def _(app):
    win = app.window(opts())
    win.render_assistant([{"type": "text", "text": "Checking the tests…"}])
    win.render_assistant([{"type": "tool_use", "id": "w1", "name": "Bash",
                           "input": {"command": "pytest -q", "description": "Run the test suite"}}])
    win.set_busy(True)
    return win


@scene("10-permission-bash", "Manual mode: a Bash call asking, in its own card (description + command, no raw JSON); no spinner")
def _(app):
    win = app.window(opts())
    ask_permission(win, "Bash", {
        "command": 'for d in /sys/class/backlight/*; do echo "$d: $(cat $d/brightness)/$(cat $d/max_brightness)"; '
                   'done 2>&1; command -v brightnessctl && brightnessctl 2>&1',
        "description": "Read screen backlight brightness"}, "p1")
    return win


@scene("11-permission-edit", "Manual mode: an Edit asking (path + red/green diff)")
def _(app):
    win = app.window(opts())
    ask_permission(win, "Edit", {"file_path": "/home/naza/projects/Baude/baude.py",
                                 "old_string": "ZOOM_MIN, ZOOM_MAX = 0.6, 2.5\nZOOM_STEP = 0.1",
                                 "new_string": "ZOOM_MIN, ZOOM_MAX = 0.5, 3.0\nZOOM_STEP = 0.1"}, "p2")
    return win


@scene("12-permission-write", "Manual mode: a Write asking (path, line count, contents)")
def _(app):
    win = app.window(opts())
    ask_permission(win, "Write", {"file_path": "/home/naza/projects/Baude/hello.py",
                                  "content": "def main():\n    print('hello')\n\n\nif __name__ == '__main__':\n    main()\n"},
                   "p3")
    return win


@scene("13-permission-web", "Manual mode: a WebFetch asking (link + prompt)")
def _(app):
    win = app.window(opts())
    ask_permission(win, "WebFetch", {"url": "https://docs.gtk.org/gtk4/class.TextView.html",
                                     "prompt": "Find how paragraph-level tag properties are resolved."}, "p4")
    return win


@scene("14-permission-mcp", "Manual mode: an MCP tool with arbitrary fields (labelled rows)")
def _(app):
    win = app.window(opts())
    ask_permission(win, "mcp__github__create_issue", {
        "owner": "naza", "repo": "baude", "title": "Jitter when typing in a code block",
        "labels": ["bug", "ui"], "draft": False, "assignees": [{"login": "naza"}]}, "p5")
    return win


@scene("20-tool-views", "Readable tool inputs: Read, Grep, Glob, TodoWrite, Task (expanded)")
def _(app):
    win = app.window(opts())
    for i, (name, inp) in enumerate([
            ("Read", {"file_path": "/home/naza/projects/Baude/baude.py", "offset": 120, "limit": 40}),
            ("Grep", {"pattern": r"def (on|h)_\w+", "path": "baude.py", "output_mode": "content", "-n": True}),
            ("Glob", {"pattern": "tests/**/*.py"}),
            ("TodoWrite", {"todos": [{"content": "Fix fence jitter", "status": "completed"},
                                     {"content": "Split command / output", "status": "in_progress"},
                                     {"content": "Screenshot matrix", "status": "pending"}]}),
            ("Task", {"description": "Survey session files", "subagent_type": "Explore",
                      "prompt": "List the record types in `~/.claude/projects/*.jsonl` and **count** them."})]):
        win.render_assistant([{"type": "tool_use", "id": f"v{i}", "name": name, "input": inp}])
        win.tool_cards[f"v{i}"].input_toggle.set_expanded(True)
    return win


@scene("21-output-text", "Command output: separate 'command' and 'output' sections, both open")
def _(app):
    win = app.window(opts())
    tool_with_output(win, "Bash", {"command": "ls -la tests", "description": "List the tests folder"},
                     "o1", "total 48\ndrwxrwxr-x 2 naza naza  4096 .\n-rwxrwxr-x 1 naza naza  5831 fake_claude.py\n"
                           "-rwxrwxr-x 1 naza naza   625 run.sh\n-rw-rw-r-- 1 naza naza 33834 test_baude.py")
    return win


@scene("21b-output-long", "Long command output: a sub-pane at most 2/3 of the chat's height that scrolls by "
                         "itself, instead of pages of chat")
def _(app):
    win = app.window(opts())
    out = "\n".join(f"tests/test_baude.py::test_{i:03d} PASSED" for i in range(1, 400))
    tool_with_output(win, "Bash", {"command": "pytest -v", "description": "Run the whole test suite"}, "ol", out)
    return win


@scene("21c-write-long", "A long file (Write): the whole file in a scrolling sub-pane")
def _(app):
    win = app.window(opts())
    body = "\n".join(f"def handler_{i}(request):\n    return respond(request, {i})\n" for i in range(120))
    win.render_assistant([{"type": "tool_use", "id": "wl", "name": "Write",
                           "input": {"file_path": "/home/naza/projects/demo/handlers.py", "content": body}}])
    win.render_tool_results([{"type": "tool_result", "tool_use_id": "wl", "content": "File created"}])
    win.tool_cards["wl"].input_toggle.set_expanded(True)
    return win


@scene("21d-command-no-description", "A command run without a description: nothing after the tool name (a "
                                     "description there is always Claude's); the command is in its own section, and "
                                     "the copy button's corner covers the code it sits on")
def _(app):
    win = app.window(opts())
    cmd = ("D=/home/naza/projects/demo-app/.local/bench/v7-smoke; mkdir -p $D; cp ~/.claude/projects/demo/*.jsonl $D/; "
           "cd $D && uv sync -q 2>&1 | tail -2")
    card = tool_with_output(win, "Bash", {"command": cmd, "timeout": 540000}, "nd", "Resolved 42 packages in 120ms")
    return win


@scene("22-output-error", "Command output: a failing command (✗, 'error' section)")
def _(app):
    win = app.window(opts())
    tool_with_output(win, "Bash", {"command": "python3 -c 'import nope'", "description": "Import a missing module"},
                     "o2", "Traceback (most recent call last):\n  File \"<string>\", line 1, in <module>\n"
                           "ModuleNotFoundError: No module named 'nope'", is_error=True)
    return win


@scene("23-output-image", "Tool output with an image (e.g. Read of a PNG) — opens automatically")
def _(app):
    win = app.window(opts())
    tool_with_output(win, "Read", {"file_path": "/tmp/screenshot.png"}, "o3",
                     [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                   "data": base64.b64encode(png_bytes(640, 360, 0x3584E4FF)).decode()}}],
                     open_input=False)
    return win


def read_image_card(win, tool_id="ri"):
    path = os.path.join(T.TMP, "mockup.png")
    with open(path, "wb") as f:
        f.write(png_bytes(1280, 720, 0x26A269FF))
    with open(path, "rb") as f:
        data = f.read()
    card = tool_with_output(win, "Read", {"file_path": path}, tool_id,
                            [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                          "data": base64.b64encode(data).decode()}}],
                            open_input=False)
    T.pump(0.3)
    return T.find(card.media_box, Gtk.Picture)[0]


@scene("25-image-read", "Read of an image file: the card is just the image (click to view, right-click for more)")
def _(app):
    win = app.window(opts())
    read_image_card(win)
    return win


@scene("26-image-viewer", "Clicking an image opens it over the whole window: ←/→ step through the chat's images, "
                           "a click outside the image (or Esc) closes it")
def _(app):
    win = app.window(opts())
    first = os.path.join(T.TMP, "before.png")
    with open(first, "wb") as f:
        f.write(png_bytes(1280, 720, 0x9141ACFF))
    win.render_assistant([{"type": "text", "text": f"Before:\n\n![before]({first})"}])
    pic = read_image_card(win)
    win.render_assistant([{"type": "text", "text": "After the fix, see the second screenshot."}])
    pic.image_actions.view()
    return win


@scene("27-image-menu", "Right-clicking an image: View, Copy image, Open with default app, Open in folder")
def _(app):
    win = app.window(opts())
    pic = read_image_card(win)
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = 120, 80, 1, 1
    pic.image_menu.set_pointing_to(rect)
    pic.image_menu.popup()
    return win


@scene("24-output-collapsed", "Several finished tool calls, a line each: the name, its Command and Output buttons, a failed one red")
def _(app):
    win = app.window(opts())
    for i, (cmd, out, err) in enumerate([("git status --short", " M baude.py", False),
                                         ("make lint", "baude.py:12: E501 line too long", True),
                                         ("tests/run.sh", "26/26 passed", False)]):
        tool_with_output(win, "Bash", {"command": cmd}, f"c{i}", out, err)
        win.tool_cards[f"c{i}"].input_toggle.set_expanded(False)
        win.tool_cards[f"c{i}"].output_toggle.set_expanded(False)
    T.find(win.transcript, baude.ToolGroup)[0].toggle.set_expanded(True)  # (three in a row: their group, opened)
    return win


MARKDOWN = """# Heading 1
## Heading 2
### Heading 3

Body text with **bold**, *italic*, ~~struck~~, `inline code` and a [link](https://example.com) (links are never fetched).

- A bullet
- Another bullet
  - Nested bullet
1. Numbered
2. Items

> A quoted line
> spanning two lines

```python
def greet(name: str) -> str:
    return f"hello {name}"  # long lines scroll sideways instead of wrapping ………………………………
```

| Feature | Status |
|---|---|
| Tables | ✓ |
| Images | ✓ |

---
"""


@scene("28-tool-group", "Consecutive tool calls folded into one line, N tool calls; Claude's narration between "
                       "steps (summarized thinking) stays visible, dimmed, above it")
def _(app):
    win = app.window(opts())
    tool_group(win)
    return win


@scene("29-tool-group-open", "The same group opened: each call one line, its Command and Output buttons after the name; no indent")
def _(app):
    win = app.window(opts())
    tool_group(win).toggle.set_expanded(True)
    return win


def tool_group(win):
    win.render_assistant([{"type": "text", "text": "Let me fix the failing test."},
                          {"type": "thinking", "thinking": "Read the test first, then patch and re-run."},
                          {"type": "tool_use", "id": "q1", "name": "Read", "input": {"file_path": "tests/test_baude.py"}},
                          {"type": "tool_use", "id": "q2", "name": "Edit", "input": {
                              "file_path": "baude.py", "old_string": "x = 1", "new_string": "x = 2"}},
                          {"type": "tool_use", "id": "q3", "name": "Bash", "input": {
                              "command": "tests/run.sh -k composer", "description": "Run the composer tests"}},
                          {"type": "tool_use", "id": "q4", "name": "Grep", "input": {"pattern": "def test_"}},
                          {"type": "tool_use", "id": "q5", "name": "Bash", "input": {"command": "git diff --stat"}}])
    for i, out in enumerate(["…", "ok", "PASS  test_composer_markdown", "35", " baude.py | 2 +-"], 1):
        win.render_tool_results([{"type": "tool_result", "tool_use_id": f"q{i}", "content": out}])
    win.render_assistant([{"type": "text", "text": "Fixed: the composer test passes again."}])
    return T.find(win.transcript, baude.ToolGroup)[0]


@scene("30-markdown-gallery", "Assistant markdown: headings, emphasis, lists, quote, code, table, rule")
def _(app):
    win = app.window(opts())
    win.render_assistant([{"type": "text", "text": MARKDOWN}])
    return win


def table_md(rows, cols, cell=lambda r, c: f"r{r}c{c}", header=None):
    head = header or [f"Column {c + 1}" for c in range(cols)]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * cols]
    lines += ["| " + " | ".join(cell(r, c) for c in range(cols)) + " |" for r in range(rows)]
    return "\n".join(lines)


@scene("33-table-small", "A small table inline with the text")
def _(app):
    win = app.window(opts())
    win.render_assistant([{"type": "text", "text": "The context windows:\n\n| Model | Window |\n|---|---|\n| Opus 5.5 | 1M |\n| Haiku 5.5 | 200k |\n\nBoth support effort."}])
    return win


@scene("34-table-tall", "A table taller than the window: every row is there; the chat scrolls")
def _(app):
    win = app.window(opts())
    md = table_md(60, 4, lambda r, c: [f"tests/test_{r:02}.py", "PASS" if r % 7 else "FAIL", f"{r * 13 % 97}ms",
                                       "flaky" if r % 11 == 0 else ""][c],
                  ["File", "Result", "Time", "Notes"])
    win.render_assistant([{"type": "text", "text": "All 60 test files:\n\n" + md + "\n\nThat's the lot."}])
    win.scroller.get_vadjustment().set_value(0)
    return win


@scene("35-table-wide", "A table wider than the window: it scrolls sideways instead of squashing the cells")
def _(app):
    win = app.window(opts())
    md = table_md(6, 14, lambda r, c: f"value {r}.{c} with some words", [f"Metric {c + 1} (units)" for c in range(14)])
    win.render_assistant([{"type": "text", "text": "Every metric, side by side:\n\n" + md + "\n\nScroll right for more."}])
    return win


@scene("36-auto-links", "Links: http(s) addresses, artifacts (by title), and paths that exist — "
                        "clicking a path opens it in your editor (at the line, if given)")
def _(app):
    proj = os.path.join(T.TMP, "links-proj")
    os.makedirs(os.path.join(proj, "src"), exist_ok=True)
    for name in ("src/app.py", "README.md"):
        with open(os.path.join(proj, name), "w") as f:
            f.write("x\n")
    win = app.window(opts(cwd=proj))
    win.register_artifact({"url": "https://claude.ai/artifact/LINKSdemo", "title": "Release Notes"})
    win.render_assistant([{"type": "text", "text":
        "I changed `src/app.py:42` and src/app.py:88, updated README.md, and left the folder "
        f"{proj}/src/ alone.\n\nDocs: https://docs.gtk.org/gtk4/ and http://localhost:8765/ · "
        "summary: https://claude.ai/artifact/LINKSdemo\n\nNot links: missing.py, version 2.1.296, e.g."}])
    return win


def signal_window(app, name):
    """A small git repository with Signal on and a reply naming a file, an env var, a constant, a
    route, a commit, a branch, things that aren't there, and a line that has since gone (stale)."""
    proj = os.path.join(T.TMP, name)
    shutil.rmtree(proj, ignore_errors=True)  # a fresh repository for each theme
    os.makedirs(proj)
    git, rewritten, _ = T._git_repo(proj)
    start = git("rev-parse", "--short", "HEAD~1")
    win = app.window(opts(cwd=proj))
    win.signal_run.set_active(True)
    getattr(win.toasts, "dismiss_all", lambda: None)()  # (its "on" toast)
    win.render_assistant([{"type": "text", "text":
        f"The runner loop is in `loop.py:11`: `tick()` waits `STALE_AFTER` seconds, then calls "
        f"`POST /v1/private/jobs/claim` with the key from `SVC_RUNNER_KEY`.\n\n"
        f"- It started in `{start}` and moved to `feature/x`; the old `{rewritten[:9]}` was amended away.\n"
        f"- The settings live in `settings.py:3`, and the compose file sets SVC_RUNNER_KEY for local runs.\n"
        f"- Not here (yet): `SVC_PREVIEW_PORT` and `preview_routes.py`.\n\n"
        f"The tick used to be at `loop.py:13`."}])
    names = ["loop.py:11", "tick()", "STALE_AFTER", "POST /v1/private/jobs/claim", "SVC_RUNNER_KEY", start, "feature/x",
             rewritten[:9], "settings.py:3", "SVC_PREVIEW_PORT", "preview_routes.py", "loop.py:13"]
    refs = win.signal.refs
    T.wait_for(lambda: all(n in refs and refs[n].status != "pending" for n in names), "names looked up", 40)
    win.signal.mark_stale("loop.py:13")
    T.wait_for(lambda: refs["loop.py:13"].status == "stale", "stale")
    return win, names, start


@scene("37-signal", "Signal: the files, env vars, constants, routes, commits and branches a reply names "
                        "become links (grey: not found, amber: the code moved away); References lists them",
       size=(1250 + SIDEBAR, H))
def _(app):
    win, _names, _start = signal_window(app, "ref-proj")
    win.show_panel(True, "refs")
    win.panel.set_size_request(430, -1)
    win.panel.signal_page.rebuild()
    win.connect("destroy", lambda *_: baude.save_settings(signal=False))
    return win


LINK_KINDS_REPLY = """Every kind of thing Signal links, each with its logo:

- **File**: the retry lives in `services/runner/queue.py`, next to the rest of `services/runner/`.
- **Commit**, **branch** and **pull request**: it's commit `a1b2c3d` on `feature/runner-retry`, up for review as PR #482.
- **Code**: `JobQueue.claim()` now gives up after `STALE_AFTER` seconds.
- **Env var** and **config key**: `RUNNER_TIMEOUT` overrides it, and the chart sets `runner.timeout`.
- **HTTP route** and **command**: runners call `POST /v1/runner/claim`; rebuild with `npm run build`.
- **Package** and **service**: the UI comes from `@acme/ui`; the compose `worker` service runs the queue.
- **Kubernetes**: the `orders-api` Deployment runs in the `k3d-dev` context.
- **Databases**: it writes the `invoice_line` table (Postgres) and the `e_Customer` collection (ArangoDB).
- **Claude Code**: I used the `Read` tool, an `Explore` subagent and the `pdf` skill.
- **Outside tools**: `terraform` isn't in the code.

A name still being looked up (`retry_budget`) or not there (`RUNNER_RETRIES`) has no logo: it claims no kind yet."""


class StandInSignal:
    """Signal's answers, fixed (no lookups), for a picture of every kind of link."""
    phrase_re, catalogue, _by_surface = None, {}, {}

    def __init__(self):
        R = baude.Ref
        loc = lambda role, path, line=None: {"role": role, "path": path, "line": line}  # noqa: E731
        self.refs = {r.surface: r for r in (
            R("services/runner/queue.py", "file", "resolved", locations=[loc("file", "services/runner/queue.py")]),
            R("services/runner/", "file", "resolved", locations=[loc("folder", "services/runner")]),
            R("a1b2c3d", "commit", "resolved"), R("feature/runner-retry", "branch", "resolved"),
            R("PR #482", "pr", "resolved"),
            R("JobQueue.claim()", "symbol", "resolved", locations=[loc("method", "services/runner/queue.py", 21)]),
            R("STALE_AFTER", "constant", "resolved", locations=[loc("constant", "services/runner/queue.py", 17)]),
            R("RUNNER_TIMEOUT", "env", "resolved", locations=[loc("read", "services/runner/settings.py", 8)]),
            R("runner.timeout", "config", "resolved", locations=[loc("key", "charts/runner/values.yaml", 12)]),
            R("POST /v1/runner/claim", "route", "resolved", locations=[loc("handler", "services/runner/api.py", 30)]),
            R("npm run build", "cli", "resolved", locations=[loc("script", "package.json", 6)]),
            R("@acme/ui", "package", "resolved", locations=[loc("package", "packages/ui/package.json", 2)]),
            R("worker", "service", "resolved", locations=[loc("compose service", "compose.yaml", 14)]),
            R("orders-api", "k8s", "resolved", data={"kind": "Deployment", "name": "orders-api"},
              locations=[loc("Deployment", "charts/orders/templates/deployment.yaml", 4)]),
            R("k3d-dev", "k8s", "resolved", data={"context": "k3d-dev"}),
            R("invoice_line", "symbol", "resolved", data={"db": {"table": "invoice_line", "engine": "Postgres"}}),
            R("e_Customer", "symbol", "resolved", data={"db": {"table": "e_Customer", "engine": "ArangoDB"}}),
            R("Read", "tool", "resolved"), R("Explore", "subagent", "resolved"), R("pdf", "skill", "resolved"),
            R("terraform", "external", "resolved"), R("retry_budget", "symbol", "pending"),
            R("RUNNER_RETRIES", "env", "not_found", reason="no file reads it"))}
        self.strong = set(self.refs)

    def decision(self, *_a):
        return None

    def message_phrases(self, *_a):
        return None

    def reading(self):
        return False


@scene("37b-signal-link-kinds", "Signal: every kind of link, in a reply, each with its logo (git, Kubernetes, the "
                                "databases, files, code, env vars, routes, commands, packages, Claude Code's tools)")
def _(app):
    win = app.window()
    saved, baude.SIGNAL = baude.SIGNAL, StandInSignal()
    try:
        view = baude.MarkdownView(LINK_KINDS_REPLY)  # (drawn now, with these answers: no Signal runs)
    finally:
        baude.SIGNAL = saved
    win.transcript.append(view)
    return win


@scene("38-signal-popover","Signal: an env var's popover lists where it's set and read (here through "
                                "a settings class's env_prefix); a commit's card shows its branches and size",
       size=(1250 + SIDEBAR, H))
def _(app):
    win, _names, start = signal_window(app, "ref-pop-proj")
    win.connect("destroy", lambda *_: baude.save_settings(signal=False))
    win.show_ref(start)
    win.panel.set_size_request(430, -1)
    label = [l for l in T.find(win.transcript, Gtk.Label) if "baude-ref:SVC_RUNNER_KEY" in l.get_label()][0]
    T.pump(0.3)
    text = label.get_text()
    index = len(text[:text.index("SVC_RUNNER_KEY") + 7].encode())
    pos = label.get_layout().index_to_pos(index)
    ox, oy = label.get_layout_offsets()
    from gi.repository import Graphene
    ok, pt = label.compute_point(win, Graphene.Point().init(ox + pos.x / 1024, oy + (pos.y + pos.height) / 1024))
    win.pointer = (pt.x, pt.y)
    win.open_ref("SVC_RUNNER_KEY", label)
    return win


CODE_SCENE_SOURCE = '''"""The build queue: jobs a runner claims, works on and finishes."""
import dataclasses
import time


@dataclasses.dataclass
class Job:
    job_id: str
    org_id: str
    status: str = "queued"
    runner_id: str | None = None


class JobQueue:
    """Jobs waiting for a runner. A runner claims one at a time and reports progress
    until it finishes; a job without progress for STALE_AFTER seconds fails."""

    STALE_AFTER = 600

    def __init__(self, db):
        self.db = db
        self.claimed = {}

    def claim(self, runner_id: str) -> Job | None:
        """The oldest queued job, now this runner's (or None)."""
        job = self.db.oldest("queued")
        if job is None:
            return None
        job.status, job.runner_id = "running", runner_id
        self.claimed[job.job_id] = time.monotonic()
        return job

    def progress(self, job_id: str) -> None:
        self.claimed[job_id] = time.monotonic()

    def expire_stale(self) -> list[str]:
        now = time.monotonic()
        stale = [j for j, t in self.claimed.items() if now - t > self.STALE_AFTER]
        for job_id in stale:
            self.db.fail(job_id, "no progress")
        return stale
'''


@scene("38b-code-view", "Code in Baude: a function opens beside the chat, highlighted, with the class it belongs to "
                        "(its members jump to their lines) and the line marked", size=(1300 + SIDEBAR, H))
def _(app):
    import subprocess
    proj = os.path.join(T.TMP, "code-proj")
    shutil.rmtree(proj, ignore_errors=True)
    os.makedirs(os.path.join(proj, "svc"))
    with open(os.path.join(proj, "svc", "queue.py"), "w") as f:
        f.write(CODE_SCENE_SOURCE)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=proj, check=True)
    win = app.window(opts(cwd=proj))
    win.signal_run.set_active(True)
    getattr(win.toasts, "dismiss_all", lambda: None)()  # (its "on" toast)
    win.connect("destroy", lambda *_: baude.save_settings(signal=False))
    win.render_assistant([{"type": "text", "text":
        "Runners take work through `queue.claim()`: it hands out the oldest queued job and stamps the time, "
        "and `JobQueue.expire_stale` fails any job that stops reporting `progress()`."}])
    refs = win.signal.refs
    T.wait_for(lambda: "queue.claim()" in refs and refs["queue.claim()"].status == "resolved", "looked up", 30)
    win.open_ref("queue.claim()", T.find(win.transcript, Gtk.Label)[0])
    T.wait_for(lambda: win.panel.code_page.code.highlighted > 0, "highlighted", 10)
    return win


def schema_sample():
    """A small app's schema (the shape PG_SCHEMA_SQL returns), for the diagram scene: no database needed."""
    tables = {"orgs": [("id", "uuid", False), ("name", "text", False), ("plan", "text", False),
                       ("created_at", "timestamptz", False)],
              "users": [("id", "uuid", False), ("org_id", "uuid", False), ("email", "text", False),
                        ("password_hash", "text", True), ("last_seen", "timestamptz", True)],
              "projects": [("id", "uuid", False), ("org_id", "uuid", False), ("name", "text", False),
                           ("repo_url", "text", True)],
              "runners": [("id", "text", False), ("org_id", "uuid", False), ("label", "text", True),
                          ("last_seen", "timestamptz", True)],
              "jobs": [("id", "uuid", False), ("project_id", "uuid", False), ("requested_by", "uuid", False),
                       ("runner_id", "text", True), ("status", "text", False), ("created_at", "timestamptz", False)],
              "job_events": [("id", "bigint", False), ("job_id", "uuid", False), ("kind", "text", False),
                             ("payload", "jsonb", True), ("at", "timestamptz", False)],
              "artefacts": [("id", "uuid", False), ("job_id", "uuid", False), ("path", "text", False),
                            ("bytes", "bigint", False)],
              "api_keys": [("id", "uuid", False), ("user_id", "uuid", False), ("prefix", "text", False),
                           ("key_hash", "text", False), ("revoked_at", "timestamptz", True)],
              "settings": [("key", "text", False), ("value", "jsonb", True)]}
    rows = {"orgs": 12, "users": 340, "projects": 58, "runners": 6, "jobs": 18_204, "job_events": 412_877,
            "artefacts": 9_310, "api_keys": 77, "settings": 14}
    fks = [("users", "org_id", "orgs"), ("projects", "org_id", "orgs"), ("runners", "org_id", "orgs"),
           ("jobs", "project_id", "projects"), ("jobs", "requested_by", "users"), ("jobs", "runner_id", "runners"),
           ("job_events", "job_id", "jobs"), ("artefacts", "job_id", "jobs"), ("api_keys", "user_id", "users")]
    return baude.pg_normalize({
        "tables": [{"s": "public", "t": t, "rows": rows[t], "kind": "r"} for t in tables],
        "columns": [{"s": "public", "t": t, "c": c, "type": ty, "null": n, "def": None}
                    for t, cols in tables.items() for c, ty, n in cols],
        "pks": [{"s": "public", "t": t, "c": cols[0][0]} for t, cols in tables.items()],
        "fks": [{"s": "public", "t": t, "c": c, "rs": "public", "rt": rt, "rc": "id"} for t, c, rt in fks]})


@scene("38c-schema-diagram", "Database schemas (☰ → Database Schemas…, or a table name in a reply): every reachable "
                             "Postgres, its tables and foreign keys, like Supabase's visualizer; the selected table's "
                             "links light up", size=(1300 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    sv = win.schema
    sv.set_visible(True)
    sv.schema, sv.database, sv.focus_column = schema_sample(), "builds", None
    sv.servers, sv.dbs = [{"container": "builds-postgres-1", "user": "postgres", "version": "18"}], \
        {"builds-postgres-1": ["builds"]}
    sv.syncing = True  # (drawn from the sample, not read from a server)
    sv.server_dd.set_model(Gtk.StringList.new(["builds-postgres-1 (Postgres 18)"]))
    sv.db_dd.set_model(Gtk.StringList.new(["builds"]))
    sv.syncing = False
    sv.show_namespace("public")
    T.wait_for(lambda: sv.canvas.get_width() > 0 and sv.canvas.cards, "laid out")
    T.pump(0.3)
    sv.canvas.fit()
    sv.canvas.select(next(i for i, c in enumerate(sv.canvas.cards) if c["table"]["name"] == "jobs"))
    return win


def kube_window(app):
    """The Kubernetes view on tests/fake_kubectl.py's k3d-dev, its pods read, by CPU."""
    import kube_fixture as kf
    os.environ.update(KUBECONFIG=kf.make_kubeconfig(os.path.join(T.TMP, "shot-kubeconfig")),
                      BAUDE_KUBECTL=os.path.join(HERE, "fake_kubectl.py"), BAUDE_HELM=os.path.join(HERE, "fake_kubectl.py"))
    for k in ("FAKE_KUBECTL_PG", "FAKE_KUBECTL_FOLLOW", "FAKE_KUBECTL_STATE"):
        os.environ.pop(k, None)
    baude.save_settings(kube_context=None, kube_log_wrap=None)
    win = app.window(opts())
    kv = win.kube
    kv.open()
    T.wait_for(lambda: kv.store is not None and kv.store.get_n_items() == 7 and not kv.loading, "the pods")
    kv.table.sort_by_column(next(c for c in kv.columns if c.get_title() == "CPU"), Gtk.SortType.DESCENDING)
    return win, kv


@scene("38e-kubernetes", "Kubernetes (☰ → Kubernetes…): a cluster laid out like Freelens: its categories, pods "
                         "with their CPU and memory (the metrics API), a square per container (green ready, orange "
                         "getting ready, outlined restarted lately, red failed, grey done), restarts, status and age, "
                         "sorted by any column; any set of namespaces at once; at a pod row's end, its logs and ⋮ "
                         "(Delete, Force delete, each asked first)",
       size=(1200 + SIDEBAR, H))  # (it fits a 13" MacBook Air's 1470-point screen, where it's made too)
def _(app):
    win, kv = kube_window(app)
    kv.show_namespaces(["shop", "jobs"])
    T.pump(0.3)
    kv.ns_btn.popup()
    return win


@scene("38e-kubernetes-logs", "Kubernetes: a pod's logs under the table, followed (kubectl logs -f), its containers "
                              "to pick from (init containers marked), its previous run's, wrapped, Ctrl+F to find; "
                              "kubectl stops when they close", size=(1200 + SIDEBAR, H))
def _(app):
    win, kv = kube_window(app)
    T.pump(0.3)
    kv.show_logs("shop/api-6f7c-mnopq")
    T.wait_for(lambda: "GET /healthz 200 0.9ms" in kv.logs.buffer.get_property("text"), "its log")
    win.connect("destroy", lambda *_: kv.hide_logs())
    return win


@scene("38d-tool-card", "Tool schemas: a tool a reply talks about (the Agent tool, TodoWrite, an MCP server's tool) "
                       "links to its card: what it does, whether it asks permission, its inputs from Anthropic's "
                       "Agent SDK types, and the subagent types defined here", size=(1250 + SIDEBAR, H))
def _(app):
    proj = os.path.join(T.TMP, "tool-proj")
    shutil.rmtree(proj, ignore_errors=True)
    os.makedirs(os.path.join(proj, ".claude", "agents"))
    with open(os.path.join(proj, ".claude", "agents", "widget-builder.md"), "w") as f:
        f.write("---\nname: widget-builder\ndescription: Builds a Widget from a spec\nmodel: sonnet\n---\n")
    cache = os.path.join(baude.tool_dir(), "2.1.300")  # (a cached fetch, and fetching off: no network)
    os.makedirs(cache, exist_ok=True)
    for name, text in (("sdk-tools.d.ts", T.TOOL_SDK_SAMPLE), ("tools-reference.md", T.TOOL_DOCS_SAMPLE),
                       ("sdk-version", "0.3.300")):
        with open(os.path.join(cache, name), "w") as f:
            f.write(text)
    baude._CLAUDE_VERSION[:] = ["2.1.300"]
    baude.TOOLS.__init__()
    baude.save_settings(signal_tool_schemas=False, signal_mcp_schemas=False)
    win = app.window(opts(cwd=proj))
    win.signal_run.set_active(True)
    getattr(win.toasts, "dismiss_all", lambda: None)()  # (its "on" toast)
    win.connect("destroy", lambda *_: baude.save_settings(signal=False, signal_tool_schemas=None,
                                                          signal_mcp_schemas=None))
    win.render_assistant([{"type": "tool_use", "id": "toolu_scene1", "name": "TodoWrite", "input": {"todos": [
        {"content": "Write the Widget", "status": "in_progress", "activeForm": "Writing the Widget"}]}}])
    win.render_assistant([{"type": "text", "text":
        "I've put the steps in `TodoWrite`. The Widget itself goes to the Agent tool with `widget-builder` as its "
        "subagent type, so it runs on Sonnet in its own context and only its summary comes back here."}])
    refs = win.signal.refs
    T.wait_for(lambda: all(n in refs and refs[n].status == "resolved" for n in ("TodoWrite", "Agent tool", "widget-builder")),
               "looked up", 30)
    win.panel.set_size_request(430, -1)
    win.open_ref("Agent tool", T.find(win.transcript, Gtk.Label)[0])
    T.pump(0.5)
    return win


def git_window(app, name):
    """A chat working in a worktree's branch (test_baude's repository for the Git page), with the commit it made, and
    the Git page open on the side."""
    import kube_fixture as kf
    shutil.rmtree(os.path.join(T.TMP, name), ignore_errors=True)  # (made again for each theme)
    r = T._git_view_repo(name)
    sid = f"6a7e0000-0000-4000-8000-0000000000{len(name):02d}"
    projects = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", "".join(c if c.isalnum() else "-" for c in r["wt"]))
    kf.write_session(os.path.join(projects, f"{sid}.jsonl"), r["wt"], sid, [
        ("you", "Commit the second part of the feature."),
        ("bash", "git commit -am 'Feature: second'", f"[feature {r['f2'][:7]}] Feature: second\n 1 file changed",
         "Commit the second part"),
        ("reply", "Committed it: `feature.py` now sets `g` too.")])
    age(os.path.join(projects, f"{sid}.jsonl"))
    win = app.window(opts(cwd=r["wt"]))
    T.wait_for(lambda: win.sidebar.find_info(sid), "listed")
    win.resume_session(win.sidebar.find_info(sid))
    T.wait_for(lambda: "Committed it" in T.labels_text(win.transcript), "the chat")
    win.panel.set_size_request(470, -1)
    win.show_panel(True, "git")
    T.wait_for(lambda: win.panel.git_page.commits, "the log", 20)
    return win, r


@scene("38f-git", "Git (side panel): the branch the chat works in (a worktree's, picked first), what it tracks and "
                  "came from, commits per hour (off-peak in amber), and the log with its lanes, clock times and lines "
                  "changed; the commit this chat made is a violet dot", size=(1150 + SIDEBAR, H))
def _(app):
    win, _r = git_window(app, "git-scene")
    T.pump(0.4)
    return win


@scene("38g-git-commit", "Git: a commit's card: when it was made, the lines it changed in all and per file (each "
                         "opens its diff), the branches that have it, and the command of this chat's that made it",
       size=(1150 + SIDEBAR, H))
def _(app):
    win, r = git_window(app, "git-commit-scene")
    win.panel.git_page.show_commit(r["f2"])
    T.wait_for(lambda: "feature.py" in T.labels_text(win.panel.git_page.detail_body), "the card")
    return win


def git_github(app, name, signed_in):
    """The Git page on a GitHub repository, with a faked gh: signed in (the branch's pull request shows) or signed
    out (nothing about GitHub shows)."""
    orig = baude.gh_state, baude._gh_json
    pr = {"number": 7, "title": "The feature", "state": "OPEN", "url": "https://github.com/acme/widgets/pull/7"}
    baude.gh_state = lambda refresh=False: ("signed_in", "ada") if signed_in else ("signed_out", None)
    baude._gh_json = lambda *a, timeout=20: ([pr] if a[:2] == ("pr", "list") else [
        {"number": 7, "title": "The feature", "state": "open", "html_url": pr["url"]}]
        if a[0] == "api" and a[1].endswith("/pulls") else None) if signed_in else None
    win, r = git_window(app, name)
    win.connect("destroy", lambda *_: (setattr(baude, "gh_state", orig[0]), setattr(baude, "_gh_json", orig[1])))
    r["git"]("remote", "set-url", "origin", "https://github.com/acme/widgets.git")
    page = win.panel.git_page
    page.load()
    T.wait_for(lambda: page.state.get("repo") == "acme/widgets", "GitHub")
    T.wait_for(lambda: page.state.get("gh", ("",))[0] == ("signed_in" if signed_in else "signed_out") and
               (not signed_in or "Pull request #7" in page.info.get_text()), "the log")
    return win


@scene("38h-git-signed-in", "Git with gh signed in: the branch's pull request under where the branch stands",
       size=(1150 + SIDEBAR, H))
def _(app):
    return git_github(app, "git-signed-in", True)


@scene("38i-git-signed-out", "Git with gh signed out: no pull request, and nothing said about signing in",
       size=(1150 + SIDEBAR, H))
def _(app):
    return git_github(app, "git-signed-out", False)


@scene("31-markdown-image", "Markdown images: a local file (file button) and a web image (globe button); "
                             "both open on click, right-click for more")
def _(app):
    path = os.path.join(T.TMP, "diagram.png")
    with open(path, "wb") as f:
        f.write(png_bytes(420, 200, 0xE66100FF))
    server = T._ImageServer()
    base = server.__enter__()  # a local server stands in for the web: screenshots never use the network
    win = app.window(opts())
    win.render_assistant([{"type": "text", "text": f"Here is the diagram:\n\n![diagram]({path})\n\n"
                                                    f"And the chart from the web:\n\n![chart]({base}/pic.png)"}])
    T.wait_for(lambda: len(T.find(win.transcript, Gtk.Picture)) == 2, "web image")
    win.connect("destroy", lambda *_: server.__exit__())
    return win


@scene("32-user-bubble", "A sent message with markdown and attachments")
def _(app):
    win = app.window(opts())
    atts = [baude.Attachment("shot.png", "image", "image/png", png_bytes(300, 200, 0x2EC27EFF)),
            baude.Attachment("spec.pdf", "pdf", "application/pdf", b"%PDF-1.4"),
            baude.Attachment("notes.txt", "file", "text/plain", None, "/tmp/notes.txt")]
    win.add(win.user_bubble("## Please review\n- the screenshot\n- the **spec**\n\n`make test` fails", atts))
    return win


@scene("40-composer-markdown", "Input box live preview: headings, bullets, emphasis, code block")
def _(app):
    win = app.window(opts())
    buf = win.input.get_buffer()
    buf.set_text("# Plan\n## Steps\nUse **bold**, *italic* and `code`\n- first\n  - nested\n1. numbered\n\n"
                 "```bash\nls -la\n```\nlast line")
    buf.place_cursor(buf.get_end_iter())
    return win


@scene("41-composer-code-fence", "Input box right after ``` + Enter: closing fence added, cursor inside")
def _(app):
    win = app.window(opts())
    buf = win.input.get_buffer()
    buf.set_text("```")
    buf.place_cursor(buf.get_end_iter())
    win.on_key(None, Gdk.KEY_Return, 0, Gdk.ModifierType(0))
    return win


@scene("42-composer-attachments", "Attachments waiting to be sent (images, PDF, other file)")
def _(app):
    win = app.window(opts())
    folder = os.path.join(T.TMP, "attach")
    os.makedirs(folder, exist_ok=True)
    paths = []
    for name, data in (("photo.png", png_bytes(400, 300, 0x9141ACFF)), ("chart.png", png_bytes(300, 300, 0x1C71D8FF)),
                       ("report.pdf", T.MINI_PDF), ("data.csv", b"a,b\n1,2\n")):
        paths.append(os.path.join(folder, name))
        with open(paths[-1], "wb") as f:
            f.write(data)
    win.attach_paths(paths)
    win.input.get_buffer().set_text("What do these show?")
    return win


@scene("43-slash-completer", "Typing / opens the command and skill dropdown")
def _(app):
    win = app.window(opts())
    T.wait_for(lambda: len(win.completer.commands) > len(baude.LOCAL_COMMANDS), "commands")
    win.input.get_buffer().set_text("/d")
    win.completer.handle_key(Gdk.KEY_Down)
    return win


@scene("44-completer-filter", "The / dropdown narrowing as you type: '/diff' finds /code-review by its description")
def _(app):
    win = app.window(opts())
    T.wait_for(lambda: len(win.completer.commands) > len(baude.LOCAL_COMMANDS), "commands")
    win.input.get_buffer().set_text("/diff")
    return win


@scene("45-completer-picked", "After Tab / Enter in the dropdown: the command is filled in, ready for arguments")
def _(app):
    win = app.window(opts())
    T.wait_for(lambda: len(win.completer.commands) > len(baude.LOCAL_COMMANDS), "commands")
    win.input.get_buffer().set_text("/dee")
    win.completer.handle_key(Gdk.KEY_Tab)
    win.input.get_buffer().insert_at_cursor("GTK4 text view performance")
    return win


def sidebar_sessions(proj):
    """Sessions in this folder, another project, and Claude Desktop's scratch chats."""
    other = os.path.join(T.TMP, "acme-app")
    scratch = os.path.join(T.TMP, ".config", "Claude", "scratch-workspaces", "a", "b", "scratch-2026-10-14-0aa1ab")
    for d in (proj, other, scratch):
        os.makedirs(d, exist_ok=True)
    home = os.path.expanduser("~")  # (only the recorded folder: nothing is written there)
    for i, (folder, title) in enumerate([(home, "Restart the touchscreen"), (other, "App creation test"),
                                         (scratch, "Install uv and node"),
                                         (other, "Acme code questions"), (proj, "Refactor settings"),
                                         (scratch, "Recipe box web app"), (proj, "Login bug")]):
        path = T.write_session(folder, f"{i}{i}{i}{i}{i}{i}{i}{i}-0000-4000-8000-00000000000{i}", title=title)
        when = time.time() - 3600 + i * 60  # written a while ago: idle, so no dot (and still in this order)
        os.utime(path, (when, when))


@scene("50-sessions-sidebar", "The sessions sidebar: new session / open folder, search, sessions grouped by "
                              "folder (this one first, with the open session highlighted); + starts one in a folder")
def _(app):
    proj = os.path.join(T.TMP, "sessions-proj")
    sidebar_sessions(proj)
    win = app.window(opts(cwd=proj))
    T.wait_for(lambda: win.sidebar.list.get_row_at_index(6) is not None, "rows")
    return win


@scene("50b-sidebar-search", "Searching the sidebar filters every folder's sessions by title and folder")
def _(app):
    proj = os.path.join(T.TMP, "sessions-proj")
    sidebar_sessions(proj)
    win = app.window(opts(cwd=proj))
    T.wait_for(lambda: win.sidebar.list.get_row_at_index(6) is not None, "rows")
    win.sidebar.search.set_text("app")
    return win


@scene("50c-dot-working", "Sidebar dot, working: grey and pulsing while Claude works (or background tasks run)")
def _(app):
    proj = os.path.join(T.TMP, "sessions-proj")
    sidebar_sessions(proj)
    win = app.window(opts(cwd=proj))
    T.type_and_send(win, "BACKGROUND HOLD")
    T.wait_for(lambda: not win.busy and win.running_tasks(), "task running")
    return win


@scene("50d-dot-waiting", "Sidebar dot, waiting: yellow while a prompt (here: allow Bash?) needs your answer")
def _(app):
    proj = os.path.join(T.TMP, "sessions-proj")
    sidebar_sessions(proj)
    win = app.window(opts(cwd=proj))
    T.type_and_send(win, "list the files")
    T.wait_for(lambda: T.button(win, "Allow"), "permission")
    return win


@scene("50e-dot-done", "Sidebar dot, done: blue when the turn has finished and Claude waits for your next message")
def _(app):
    proj = os.path.join(T.TMP, "sessions-proj")
    sidebar_sessions(proj)
    win = app.window(opts(cwd=proj))
    T.type_and_send(win, "list the files")
    T.wait_for(lambda: T.button(win, "Allow"), "permission")
    T.button(win, "Allow").emit("clicked")
    T.wait_for(lambda: not win.busy, "turn end")
    return win


@scene("50f-fork-tree", "Forks sit under the session they came from, like files in a folder: just what the "
                      "fork's name adds ('fork', 'Slug collision'), or its own name if renamed; one joined line per "
                      "parent, down to its last fork; inactive chats have no dot")
def _(app):
    proj = os.path.join(T.TMP, "forks-proj")
    os.makedirs(proj, exist_ok=True)
    now, base = time.time(), list(range(0, 40))
    T.write_chain(proj, "f0000000-0000-4000-8000-000000000000", "Refactor settings", list(range(700, 720)), now - 900)
    T.write_chain(proj, "f1000000-0000-4000-8000-000000000001", "Recipe box web app", base, now - 600)
    T.write_chain(proj, "f2000000-0000-4000-8000-000000000002", "Recipe box web app (fork)",
                  base[:30] + list(range(500, 520)), now - 300, born="2026-10-14T08:29:10Z")
    T.write_chain(proj, "f3000000-0000-4000-8000-000000000003", "Recipe box web app (Slug collision)",
                  base[:25] + list(range(600, 640)), now - 200)
    T.write_chain(proj, "f4000000-0000-4000-8000-000000000004", "Search tuning",
                  base[:30] + list(range(500, 515)) + list(range(800, 820)), now - 100, born="2026-10-14T17:05:54Z")
    win = app.window(opts(cwd=proj))
    T.wait_for(lambda: win.sidebar.list.get_row_at_index(5) is not None, "rows")
    return win


@scene("51-resumed-session", "A past session, opened: history redrawn, title in the header; no banner and no "
                              "'resumed' mark until you write")
def _(app):
    proj = os.path.join(T.TMP, "resume-proj")
    os.makedirs(proj, exist_ok=True)
    path = T.write_session(proj, "99999999-0000-4000-8000-000000000009", title="Login bug")
    old = time.time() - 3600
    os.utime(path, (old, old))  # nobody else is writing it
    win = app.window(opts(cwd=proj))
    win.resume_session(baude.read_session_info(path))
    T.wait_for(lambda: win.session_id and win.proc, "resume")
    return win


@scene("52-resumed-image-read", "A resumed session whose history has an image Read: shown as the image "
                                "(this used to come back as an empty 'output')")
def _(app):
    proj = os.path.join(T.TMP, "resume-image-proj")
    os.makedirs(proj, exist_ok=True)
    shot = os.path.join(proj, "header.png")
    with open(shot, "wb") as f:
        f.write(png_bytes(888, 790, 0x3584E4FF))
    with open(shot, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    sid = "88888888-0000-4000-8000-000000000008"
    d = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", proj.replace("/", "-"))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{sid}.jsonl")
    recs = [
        {"type": "user", "cwd": proj, "message": {"content": "Check the header screenshot"}},
        {"type": "assistant", "cwd": proj, "message": {"content": [
            {"type": "text", "text": "Let me look at it."},
            {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": shot}}]}},
        {"type": "user", "cwd": proj, "message": {"content": [{"type": "tool_result", "tool_use_id": "r1", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}]}]}},
        {"type": "assistant", "cwd": proj, "message": {"content": [
            {"type": "text", "text": "The header shows **Opus 5.5 · Manual**."}]}},
        {"type": "custom-title", "customTitle": "Header screenshot", "sessionId": sid},
    ]
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(r) for r in recs) + "\n")
    age(path)
    win = app.window(opts(cwd=proj))
    win.resume_session(baude.read_session_info(path))
    T.wait_for(lambda: win.session_id and win.proc, "resume")
    return win


def sidebar_row(win, title):
    for i in range(200):
        row = win.sidebar.list.get_row_at_index(i)
        if row is None:
            return None
        if getattr(row, "info", {}).get("title") == title:
            return row


@scene("53-rename-session", "Rename: F2 on a session in the sidebar (or right-click → Rename…)")
def _(app):
    proj = os.path.join(T.TMP, "sessions-proj")
    sidebar_sessions(proj)
    win = app.window(opts(cwd=proj))
    T.wait_for(lambda: sidebar_row(win, "Login bug") is not None, "rows")
    win.sidebar.ask_rename(sidebar_row(win, "Login bug").info)
    return win


@scene("53b-sidebar-menu", "Right-click a session: Rename…, Archive, Move to Trash…")
def _(app):
    proj = os.path.join(T.TMP, "sessions-proj")
    sidebar_sessions(proj)
    win = app.window(opts(cwd=proj))
    T.wait_for(lambda: sidebar_row(win, "Login bug") is not None, "rows")
    win.sidebar.show_menu(sidebar_row(win, "Login bug"), 120, 14)
    return win


# ---------------------------------------------------------------- network --

NET = {}


def network_fixture():
    """Servers as Baude finds them on the network (baudelib.server, here on loopback, running the fake claude, each
    saying it's on a machine of its own): lab-mac, there, with sessions in the Lab and Planning groups (one asking
    whether it may run a command); and build-box, with sessions in Lab, gone once Baude has seen them (dead). Made once,
    for every scene that wants it."""
    if NET:
        return NET
    import threading
    from baudelib.server import Server

    class Server(Server):  # (another machine's: its own name, not this one's, so not listed as This Mac's)
        def _info(self):
            return {**super()._info(), "host": f"{self.me['name']}.lan"}
    net = baude.network()
    env = {"FAKE_CLAUDE_SESSION_IDS": "1", "FAKE_CLAUDE_TRANSCRIPTS": "1"}  # (its sessions' ids and transcripts)
    parser, site = (os.path.join(T.TMP, "net", name) for name in ("parser", "site"))
    for d in (parser, site):
        os.makedirs(d, exist_ok=True)
    servers = {}
    for name in ("lab-mac", "build-box"):  # (each on a socket of its own: reached as another host's is)
        s = Server(state_dir=os.path.join(T.TMP, "servers", name), group="Lab", name=name,
                   socket_path=os.path.join(os.environ["BAUDE_RUNTIME_DIR"], f"{name}.sock"), log=lambda _text: None,
                   env=env)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        servers[name] = (s, net.add({"via": "unix", "path": s.socket_path}))
    started = {}
    for name, params, answer in [
            ("build-box", {"prompt": "Run the nightly benchmarks", "cwd": parser}, True),
            ("build-box", {"prompt": "Bisect the flaky login test", "cwd": site}, True),
            ("lab-mac", {"prompt": "Plan the Q1 roadmap", "cwd": site, "group": "Planning"}, True),
            ("lab-mac", {"prompt": "Write the release notes for 2.4", "cwd": site}, True),
            ("lab-mac", {"prompt": "Speed up the tokenizer's hot loop", "cwd": parser, "model": "opus",
                         "effort": "high"}, True),
            ("lab-mac", {"prompt": "List the files in the build folder", "cwd": parser}, False)]:
        server = servers[name][0]
        s = server.start(params)
        started[params["prompt"]] = s.id
        T.wait_for(lambda: s.asks, f"{params['prompt']}: asking")
        if answer:
            with server.lock:
                rid, ask = next(iter(s.asks.items()))
                server.take_input(None, s, {"type": "control_response", "response": {
                    "subtype": "success", "request_id": rid,
                    "response": {"behavior": "allow", "updatedInput": ask["request"]["input"]}}})
            T.wait_for(lambda: s.state() == "idle", f"{params['prompt']}: done")
        time.sleep(0.05)  # (in order: each said a little after the one before)
    box, box_r = servers["build-box"]
    T.wait_for(lambda: len(net.sessions()) == 6 and all(
        x.get("size") for x in net.sessions() if x["server"] is box_r), "the network's sessions")
    for sid in box_r.sessions:
        box_r.sync(sid)  # (what Baude had of them, from before it went)
    box.shutdown()
    T.wait_for(lambda: box_r.state == "dead", "build-box gone", 20)
    NET.update(net=net, lab=servers["lab-mac"], box=servers["build-box"], ids=started, folders=(parser, site))
    return NET


def network_window(app, group="Lab", sid=None, host="lab"):
    """A window listing a group on the network (⋮ → Groups; None: every group), on a host (New session's: lab, lab-mac;
    None: this machine), in Planning, sid: the session waited for."""
    network_fixture()
    baude.save_settings(new_session_host=baude.host_key(NET[host][1]) if host else None,
                        sidebar_groups=[group] if group else None)
    win = app.window(opts(cwd=NET["folders"][0]))
    if sid is None and host == "lab":
        T.wait_for(lambda: sidebar_row(win, "Write the release notes for 2.4") is not None, "the network's sessions")
    elif sid is None:
        T.pump(0.5)
    else:
        T.wait_for(lambda: session_row(win, sid) is not None, "the session")
    return win


def session_row(win, sid):
    for i in range(200):
        row = win.sidebar.list.get_row_at_index(i)
        if row is None:
            return None
        if getattr(row, "info", {}).get("id") == sid:
            return row


def subagent_session():
    """A session on lab-mac, in Planning, that had a subagent count the tests in the background: the subagent's own
    transcript kept beside the session's, as claude keeps it."""
    if "agent" in NET:
        return NET["agent"]
    network_fixture()
    server = NET["lab"][0]
    s = server.start({"prompt": "Have a SUBAGENT count the test functions", "title": "Count the test functions",
                      "cwd": NET["folders"][0], "group": "Planning"})
    T.wait_for(lambda: s.transcript() and "subagent counted" in open(s.transcript()).read(), "its subagent done", 15)
    NET["agent"] = s.id
    return s.id


def open_network_session(win, prompt):
    win.sidebar.on_activate(win.sidebar.list, sidebar_row(win, prompt))
    T.wait_for(lambda: win.remote is not None and win.session_id == NET["ids"][prompt] and win.proc is not None,
               f"{prompt}: opened")
    T.pump(0.3)


def models_server():
    """A server that can run more than Claude's models, as this machine can't: gpu-mac, in a process of its own with
    stand-ins for apple-claude and pi-claude (the fake claude) and two of pi's models said to be downloaded; given to
    Baude by address. Its session: "Sketch the release checklist", on Claude's model."""
    if "gpu" in NET:
        return NET["gpu"]
    import subprocess
    network_fixture()
    d = os.path.join(T.TMP, "gpu-mac")
    hub = os.path.join(d, "hf")
    for repo in ("mlx-community--Qwen3-8B-4bit", "mlx-community--Qwen3-4B-Instruct-2507-4bit"):
        os.makedirs(os.path.join(hub, "models--" + repo), exist_ok=True)
    fake = os.path.join(T.HERE, "fake_claude.py")
    for name, body in (("apple", f"exec env -u FAKE_CLAUDE_TRANSCRIPTS {sys.executable} {fake} \"$@\""),
                       ("pi", f"exec env -u FAKE_CLAUDE_TRANSCRIPTS {sys.executable} {fake} \"$@\""), ("agent", "exit 0")):
        with open(os.path.join(d, name), "w") as f:
            f.write(f"#!/bin/sh\n{body}\n")
        os.chmod(os.path.join(d, name), 0o755)
    env = {**os.environ, "BAUDE_APPLE_CLAUDE": os.path.join(d, "apple"), "BAUDE_PI_CLAUDE": os.path.join(d, "pi"),
           "BAUDE_PI": os.path.join(d, "agent"), "HF_HUB_CACHE": hub, "FAKE_CLAUDE_SESSION_IDS": "1",
           "FAKE_CLAUDE_TRANSCRIPTS": "1"}
    proc = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(T.HERE), "baude_headless.py"), "serve",
                             "--group", "Lab", "--name", "gpu-mac", "--state", os.path.join(d, "state"),
                             "--socket", os.path.join(os.environ["BAUDE_RUNTIME_DIR"], "gpu-mac.sock")],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True)
    import atexit
    atexit.register(proc.terminate)  # (it goes with this run, its claudes with it)
    r = baude.network().add({"via": "unix", "path": json.loads(proc.stdout.readline())["socket"]})
    T.wait_for(lambda: r.state == "alive", "gpu-mac there")
    got = []
    r.request({"op": "start", "prompt": "Sketch the release checklist", "cwd": NET["folders"][1], "model": "opus"},
              got.append)
    T.wait_for(lambda: got and (r.sessions.get(got[0]["session"]["id"]) or {}).get("state") == "waiting", "its session")
    NET["gpu"] = (proc, r, got[0]["session"]["id"])
    return NET["gpu"]


def popup_choices(dropdown):
    """A ChoiceDropDown's list, open (a picture of what it offers)."""
    next(w for w in T.walk(dropdown) if isinstance(w, Gtk.Popover)).popup()


@scene("56-host-older", "This Mac's host process, which runs Baude's chats, older than Baude after an update: said, with "
                       "Restart Daemon (once nothing's at a turn there: its sessions resume as they're next written to)")
def _(app):
    win = app.window()
    r = baude.local_host()
    r.info = {**r.info, "version": baude.baudelib.wire.VERSION - 1}  # (as one started before the update says)
    baude._HOST_SAID.clear()
    win.check_host_version()
    T.pump(0.5)
    return win


@scene("55-network-group-menu", "⋮ beside the search: Group by, Add Host…, then which groups are listed: each "
                                "group the sessions are in. Here lab-mac's (New session's host), in Lab")
def _(app):
    win = network_window(app)
    win.sidebar.view_btn.popup()
    return win


@scene("55b-network-group", "A group on lab-mac, New session's host: its sessions there, by folder")
def _(app):
    return network_window(app)


@scene("55c-network-session-asking", "A session on lab-mac, opened: its transcript, fetched; what it's asking, to "
                                     "answer here; written in at once, though it's at a turn there (a host's session "
                                     "takes several heads)")
def _(app):
    win = network_window(app)
    open_network_session(win, "List the files in the build folder")
    T.wait_for(lambda: T.button(win, "Allow"), "its question")
    return win


@scene("55d-network-session-steered", "Allowed, and written to: Baude takes it up on lab-mac, and what it does comes "
                                      "as it's done")
def _(app):
    win = network_window(app)
    open_network_session(win, "List the files in the build folder")
    if T.button(win, "Allow"):
        T.button(win, "Allow").emit("clicked")
    T.wait_for(lambda: (NET["lab"][1].sessions.get(win.session_id) or {}).get("state") == "idle", "its turn over")
    T.wait_for(lambda: not win.live_elsewhere(), "the banner gone")
    T.type_and_send(win, "Now count them")
    T.wait_for(lambda: T.button(win, "Allow"), "asked again")
    T.button(win, "Allow").emit("clicked")
    T.wait_for(lambda: not win.busy, "turn end")
    return win


@scene("55f-network-new-session", "New session, while a new chat here isn't listed (only Planning): a prompt, a "
                                  "folder and a group, through this machine's host process, with the top bar's model, "
                                  "effort and mode")
def _(app):
    win = network_window(app, None, host=None)
    win.sidebar.filtered(sidebar_groups=["Planning"])  # (a new chat here, in no group, not listed)
    win.sidebar.new_chat()
    T.wait_for(lambda: getattr(win, "remote_fields", None), "the dialog")
    f = win.remote_fields
    f["folder"].set_text(NET["folders"][1])
    f["prompt"].get_buffer().set_text("Check every link in the docs still resolves, and fix the ones that don't.")
    return win


@scene("55h-network-models", "A network session's models are its server's: gpu-mac runs Apple's model and two of "
                             "its own, which this machine doesn't have")
def _(app):
    models_server()
    win = network_window(app)
    T.wait_for(lambda: sidebar_row(win, "Sketch the release checklist") is not None, "gpu-mac's session")
    win.sidebar.on_activate(win.sidebar.list, sidebar_row(win, "Sketch the release checklist"))
    T.wait_for(lambda: win.remote is not None and win.proc is not None and "apple" in win.model_dd.values, "its models")
    popup_choices(win.model_dd)
    return win


@scene("55k-network-add-server", "⋮ → Hosts → Add Host…: another machine's host, by where ssh goes (an "
                                 "alias, or user@host), and its host process's command there if it isn't on PATH; "
                                 "kept from then on, reached or not")
def _(app):
    win = network_window(app)
    dest, command = win.ask_add_host()
    dest.set_text("lab@build-box.lan")
    command.set_text("~/src/Baude/baude_headless.py")
    return win


@scene("55l-network-hosts", "New session's host, lab-mac, picked: lab-mac's sessions listed, in every group; ⋮ "
                            "has Add Host… and the groups")
def _(app):
    win = network_window(app, None)
    win.sidebar.view_btn.popup()
    return win


@scene("55l2-network-host-picker", "New session's hosts: the one picked is the one whose chats are listed")
def _(app):
    win = network_window(app, None)
    popup_choices(win.sidebar.host_dd)
    return win


@scene("55m-network-all", "Every group, on this machine (New session's host, as at first): its own chats, with its "
                           "host process's sessions")
def _(app):
    network_fixture()
    for n, title in enumerate(("Tidy the changelog", "Fix the login redirect")):  # (this machine's own, in site)
        T.write_session(NET["folders"][1], f"5555555{n}-0000-4000-8000-000000000055", title=title)
    win = network_window(app, None, host=None)
    T.wait_for(lambda: sidebar_row(win, "Tidy the changelog") is not None, "this machine's chats")
    return win


def studio(win):
    """A host added by hand, over ssh (never reached: the tests' ssh is false): studio."""
    r = baude.network().add({"via": "ssh", "dest": "lab@studio.lan"}, by_hand=True)
    T.wait_for(lambda: r.state == "dead", "studio tried")
    win.sidebar.fill_filters()
    return r


@scene("55q-network-host-command", "Host Command…: the command ssh runs on the host, with connect after it")
def _(app):
    win = network_window(app, None)
    r = studio(win)
    win.ask_host_command(r.key).set_text("~/src/Baude/baude_headless.py")
    return win


@scene("55g-network-session-menu", "Right-click a network session: Rename…, Archive and Move to Trash…, done on its "
                                   "host; one at a turn: Stop; one whose host is gone: Delete (it comes back when it's "
                                   "used again)")
def _(app):
    win = network_window(app)
    win.sidebar.show_menu(sidebar_row(win, "Write the release notes for 2.4"), 120, 14)
    return win


@scene("55i-network-subagent", "A network session's subagent in the side panel: its own transcript, fetched from its "
                               "server with the session's", size=(1200 + SIDEBAR, H))
def _(app):
    sid = subagent_session()
    win = network_window(app, "Planning", sid)
    win.sidebar.on_activate(win.sidebar.list, session_row(win, sid))
    T.wait_for(lambda: win.remote is not None and win.session_id == sid and win.proc is not None, "opened")
    T.wait_for(lambda: "toolu_ag1" in win.panel.agent_rows, "its subagent")
    win.show_panel(True, "agents")
    win.panel.open_agent("toolu_ag1")
    end = time.monotonic() + 5  # (its details: what's mirrored of its transcript, read as it's there)
    while "28" not in T.labels_text(win.panel.agent_view("toolu_ag1")) and time.monotonic() < end:
        T.pump(0.1)
    return win


@scene("55j-network-renamed", "A network session renamed (Rename…, or F2): its server renames it, and it's listed "
                              "and shown by its new name")
def _(app):
    network_fixture()
    sid = NET["ids"]["Plan the Q1 roadmap"]
    win = network_window(app, "Planning", sid)
    win.sidebar.on_activate(win.sidebar.list, session_row(win, sid))
    T.wait_for(lambda: win.remote is not None and win.session_id == sid and win.proc is not None, "opened")
    win.sidebar.rename(session_row(win, sid).info, "Q1 roadmap: first draft")
    T.wait_for(lambda: (session_row(win, sid).info.get("title") or "").startswith("Q1 roadmap"), "renamed")
    T.pump(0.3)
    return win


@scene("53c-folder-collapse", "Folders collapse: the arrow appears beside the name when the pointer is near "
                              "(and stays while collapsed); archived sessions sit in a collapsed group at the end")
def _(app):
    proj = os.path.join(T.TMP, "sessions-proj")
    sidebar_sessions(proj)
    win = app.window(opts(cwd=proj))
    T.wait_for(lambda: sidebar_row(win, "App creation test") is not None, "rows")
    win.sidebar.archive(sidebar_row(win, "Refactor settings").info)
    win.sidebar.toggle_folder(os.path.join(T.TMP, "acme-app"))
    for i in range(60):  # the pointer near "No folder (Claude Desktop)"
        row = win.sidebar.list.get_row_at_index(i)
        if row is not None and getattr(row, "key", None) == "scratch":
            row.get_child().add_css_class("hover")
            break
    return win


@scene("53d-live-tail", "A session active elsewhere: new messages appear as they're written, a spinner while "
                        "it works, and the input is off until it goes quiet")
def _(app):
    proj = os.path.join(T.TMP, "tail-proj")
    os.makedirs(proj, exist_ok=True)
    path = T.write_session(proj, "44444444-0000-4000-8000-000000000004", title="Running in Claude Desktop")
    win = app.window(opts(cwd=proj))
    T.wait_for(lambda: sidebar_row(win, "Running in Claude Desktop") is not None, "rows")
    win.resume_session(sidebar_row(win, "Running in Claude Desktop").info)
    with open(path, "a") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "now add a test for it"}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Adding a regression test for the login fix."},
            {"type": "tool_use", "id": "live1", "name": "Bash",
             "input": {"command": "pytest -q tests/test_auth.py", "description": "Run the auth tests"}}]}}) + "\n")
    T.wait_for(lambda: "Adding a regression test" in T.labels_text(win.transcript), "tailed")
    win.sidebar.set_state(win.session_state())
    return win


@scene("51b-resumed-after-writing", "Writing to a past session takes it up: '— resumed —' marks where, above "
                                    "your message")
def _(app):
    proj = os.path.join(T.TMP, "resume-write-proj")
    os.makedirs(proj, exist_ok=True)
    path = T.write_session(proj, "99999999-0000-4000-8000-00000000000b", title="Login bug")
    old = time.time() - 3600
    os.utime(path, (old, old))
    win = app.window(opts(cwd=proj))
    win.resume_session(baude.read_session_info(path))
    T.pump(0.3)
    T.type_and_send(win, "and the logout bug?")
    T.wait_for(lambda: T.button(win, "Allow"), "the reply's permission prompt")
    return win


@scene("54-task-notifications", "Background tasks that ended while Claude worked: one quiet line each, "
                                "✓ or ✗, the tool and its description (a link back to its card), not the raw "
                                "command or the XML they arrive in")
def _(app):
    proj = os.path.join(T.TMP, "tasknote-proj")
    os.makedirs(proj, exist_ok=True)
    path = T.write_session(proj, "99999999-0000-4000-8000-00000000000c", title="Build and test")

    def started(tool_id, cmd, description):
        return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tool_id, "name": "Bash",
             "input": {"command": cmd, "description": description, "run_in_background": True}}]}}) + "\n" + \
            json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content":
                 f"Command running in background with ID: {tool_id}. Output is being written to: /tmp/{tool_id}"}]}}) + "\n"

    def note(status, summary, tool_id):
        return json.dumps({"type": "user", "message": {"role": "user", "content": (
            f"<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>{tool_id}</tool-use-id>\n"
            f"<output-file>/tmp/claude-501/tasks/b1.output</output-file>\n<status>{status}</status>\n"
            f"<summary>{summary}</summary>\n</task-notification>")}}) + "\n"
    with open(path, "a") as f:
        f.write(started("bg_dev", "cd web && npm run dev -- --port 5173 > /tmp/dev.log 2>&1", "Launch the dev server"))
        f.write(started("bg_e2e", "cd web && npx playwright test --reporter=line 2>&1 | tail -40", "Run the e2e suite"))
        f.write(note("completed", 'Background command "cd web &amp;&amp; npm run dev -- --port 5173 &gt; /tmp/dev.log '
                                  '2&gt;&amp;1" completed (exit code 0)', "bg_dev"))
        f.write(json.dumps({"type": "assistant", "message": {"stop_reason": "end_turn", "content": [
            {"type": "text", "text": "The dev server is up on port 5173."}]}}) + "\n")
        f.write(note("failed", 'Background command "Run the e2e suite" failed with exit code 1', "bg_e2e"))
        f.write(json.dumps({"type": "assistant", "message": {"stop_reason": "end_turn", "content": [
            {"type": "text", "text": "Two e2e tests failed; both time out waiting for the login form."}]}}) + "\n")
    old = time.time() - 3600
    os.utime(path, (old, old))
    win = app.window(opts(cwd=proj))
    win.resume_session(baude.read_session_info(path))
    return win


@scene("70-background-running", "A background command: card keeps spinning, status bar shows it running")
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "BACKGROUND HOLD")
    T.wait_for(lambda: "toolu_bg1" in win.tool_cards and win.tool_cards["toolu_bg1"].state == "background", "bg")
    return win


@scene("71-background-finished", "It finished: ✓ with its output, then the turn the CLI started by itself")
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "BACKGROUND")
    T.wait_for(lambda: "The build finished" in T.labels_text(win.transcript) and not win.busy, "follow-up", 10)
    win.tool_cards["toolu_bg1"].output_toggle.set_expanded(True)
    return win


@scene("72-panel-processes", "Side panel, Processes: live output tail and a stop button", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "BACKGROUND HOLD")
    T.wait_for(lambda: "bg1" in win.panel.proc_rows, "row")
    win.show_panel(True, "processes")
    win.panel.proc_rows["bg1"].set_expanded(True)
    T.pump(1.2)  # one output poll
    return win


@scene("72b-panel-processes-long", "Side panel, Processes: commands without a description are one line of code "
                                   "(the whole command when opened, highlighted)", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    cmds = [
        ("J=gen_79fd3c530feb4fd29bbb9a; D=/home/naza/projects/demo-app/.local/widget-bench/v7-smoke-2/$J; while docker "
         "exec demo-app-runner-1 test -d /var/lib/runner/$J 2>/dev/null; do docker exec demo-app-runner-1 sh -c "
         "\"cd /var/lib/runner/$J && tar cf - --exclude=node_modules workspace\" > $D/snap.tar.tmp; sleep 30; done"),
        "until [ $(docker exec demo-postgres psql -tAc 'select count(*) from installs') -ge 8 ]; do sleep 10; done",
    ]
    for n, cmd in enumerate(cmds):
        tid = f"lp{n}"
        win.render_assistant([{"type": "tool_use", "id": f"toolu_{tid}", "name": "Bash",
                               "input": {"command": cmd, "run_in_background": True}}])
        win.on_msg(win.proc, {"type": "system", "subtype": "task_started", "task_id": tid, "tool_use_id": f"toolu_{tid}",
                              "description": cmd, "task_type": "local_bash"})
    win.render_assistant([{"type": "tool_use", "id": "toolu_lp9", "name": "Bash",
                           "input": {"command": "npm run dev", "description": "Start the dev server",
                                     "run_in_background": True}}])
    win.on_msg(win.proc, {"type": "system", "subtype": "task_started", "task_id": "lp9", "tool_use_id": "toolu_lp9",
                          "description": "Start the dev server", "task_type": "local_bash"})
    win.show_panel(True, "processes")
    win.panel.proc_rows["lp0"].set_expanded(True)
    return win


@scene("73-panel-subagents", "Side panel, Subagents: each run with status, tool uses, tokens, time", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "SUBAGENT")
    T.wait_for(lambda: "subagent counted" in T.labels_text(win.transcript) and not win.busy, "done", 10)
    win.show_panel(True, "agents")
    return win


@scene("74-panel-subagent-detail", "A subagent's own transcript: its prompt, tool calls and answer", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "SUBAGENT")
    T.wait_for(lambda: "subagent counted" in T.labels_text(win.transcript) and not win.busy, "done", 10)
    win.show_panel(True, "agents")
    win.panel.open_agent("toolu_ag1")
    return win


@scene("74b-panel-nested-subagents", "Nested subagents: an agent's own agents sit under it in the list "
                                    "(here the outer one is still running)", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "SUBAGENT")
    T.wait_for(lambda: "subagent counted" in T.labels_text(win.transcript) and not win.busy, "done", 10)
    T.type_and_send(win, "NESTED HOLD")
    T.wait_for(lambda: "toolu_ag2_inner" in win.panel.agent_rows
               and win.panel.agent_rows["toolu_ag2_inner"].status.state == "done", "inner done", 10)
    win.show_panel(True, "agents")
    return win


@scene("74c-panel-nested-detail", "The outer agent's transcript: the inner agent is one of its tool calls, "
                                  "linked to its own transcript", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "NESTED HOLD")
    T.wait_for(lambda: "toolu_ag1_inner" in win.panel.agent_rows
               and win.panel.agent_rows["toolu_ag1_inner"].status.state == "done", "inner done", 10)
    T.wait_for(lambda: "helper counted" in T.labels_text(win.panel.agent_view("toolu_ag1")), "outer reply")
    win.show_panel(True, "agents")
    win.panel.open_agent("toolu_ag1")
    for group in T.find(win.panel.agent_view("toolu_ag1"), baude.ToolGroup):
        group.toggle.set_expanded(True)
    win.tool_cards["toolu_ag1_inner"].output_toggle.set_expanded(True)
    return win


ARTIFACT_URL = "https://claude.ai/artifact/EXAMPLEart1fact2demo3x"


def publish_artifact(win, tool_id="art", title="Acme Fleet Ops", icon="chart", url=ARTIFACT_URL):
    page = os.path.join(T.TMP, f"{tool_id}.html")
    with open(page, "w") as f:
        f.write(f"<!doctype html><title>{title}</title><body>dashboard</body>")
    win.render_assistant([{"type": "tool_use", "id": tool_id, "name": "Artifact",
                           "input": {"file_path": page, "icon": icon, "description": "Day's jobs, runs and numbers"}}])
    win.render_tool_results([{"type": "tool_result", "tool_use_id": tool_id, "content":
                              f'Published {page} at {url} (Version 2, version id 1-a) Icon: "{icon}".'}])


@scene("75-side-questions", "/btw side questions: each its own forked, unsaved claude; answers here, the chat "
                             "carries on (two threads, one with a follow-up)", size=(1200 + SIDEBAR, H))
def _(app):
    proj = os.path.join(T.TMP, "btw-proj")
    os.makedirs(proj, exist_ok=True)
    win = app.window(opts(cwd=proj))
    # (its transcript, as claude would have it, so the side questions fork it: the host process's chat's id)
    T.write_session(proj, win.session_id, title="Main chat")
    T.type_and_send(win, "list the files")
    T.wait_for(lambda: T.button(win, "Allow"), "main turn")
    first = win.ask_side_question("what does make do here?")
    win.ask_side_question("is the build cached?")
    T.wait_for(lambda: all(not q.busy for q in win.panel.side_questions()), "answers")
    first.ask("and how long does it take?")
    T.wait_for(lambda: not first.busy, "follow-up")
    return win


@scene("75b-side-question-new", "Side questions' +: a new one asked in its own box, and another waiting for its "
                                "question", size=(1200 + SIDEBAR, H))
def _(app):
    proj = os.path.join(T.TMP, "btw-new-proj")
    os.makedirs(proj, exist_ok=True)
    win = app.window(opts(cwd=proj))
    T.write_session(proj, win.session_id, title="Main chat")
    win.show_panel(True, "btw")
    plus = getattr(win.panel, "btw_new", None)
    if plus is not None:  # (before the +: the page as it was)
        plus.emit("clicked")
        first = win.panel.side_questions()[0]
        if hasattr(first, "composer"):
            first.composer.get_buffer().set_text("what does make do here?")
            first.submit()
        else:
            first.follow.set_text("what does make do here?")
            first.follow.emit("activate")
        T.wait_for(lambda: first.answer is not None and not first.busy, "answer")
        plus.emit("clicked")
    return win


def _side_questions_three(app, name):
    proj = os.path.join(T.TMP, name)
    os.makedirs(proj, exist_ok=True)
    win = app.window(opts(cwd=proj))
    T.write_session(proj, win.session_id, title="Main chat")
    for q in ("what does make do here?", "is the build cached?", "which tests are slow?"):
        win.ask_side_question(q)
    T.wait_for(lambda: all(not q.busy for q in win.panel.side_questions()), "answers")
    return win


@scene("75c-side-questions-rows", "Side questions as rows titled with their questions: one open under its line, two "
                                  "closed", size=(1200 + SIDEBAR, H))
def _(app):
    win = _side_questions_three(app, "btw-rows-proj")
    for q in win.panel.side_questions()[1:]:
        if hasattr(q, "set_expanded"):
            q.set_expanded(False)
    return win


@scene("75d-side-question-full", "A side question filling the Side questions page, a back button out",
       size=(1200 + SIDEBAR, H))
def _(app):
    win = _side_questions_three(app, "btw-full-proj")
    q = win.panel.side_questions()[1]
    if hasattr(win.panel, "open_side_question"):
        win.panel.open_side_question(q)
    return win


@scene("75e-side-questions-empty", "The Side questions page with none asked yet", size=(1200 + SIDEBAR, H))
def _(app):
    proj = os.path.join(T.TMP, "btw-empty-proj")
    os.makedirs(proj, exist_ok=True)
    win = app.window(opts(cwd=proj))
    win.show_panel(True, "btw")
    return win


@scene("76-tail-background-tasks", "Viewing a session running elsewhere: its background command and subagent "
                                   "(with its own transcript) rebuilt from the files, in the side panel",
       size=(1200 + SIDEBAR, H))
def _(app):
    proj = os.path.join(T.TMP, "bg-tail-proj")
    os.makedirs(proj, exist_ok=True)
    T.write_bg_session(proj, "abababab-0000-4000-8000-0000000000aa", live=True)
    win = app.window(opts(cwd=proj))
    T.wait_for(lambda: win.sidebar.find_info("abababab-0000-4000-8000-0000000000aa"), "listed")
    win.resume_session(win.sidebar.find_info("abababab-0000-4000-8000-0000000000aa"))
    T.pump(0.3)
    win.show_panel(True, "agents")
    win.panel.open_agent("ta1")
    return win


@scene("80-artifact-published", "Claude published an artifact: its card shows the page's title, version, Open and copy")
def _(app):
    win = app.window(opts())
    win.add(win.user_bubble("Build me an ops dashboard", []))
    publish_artifact(win)
    win.render_assistant([{"type": "text", "text": f"Your dashboard is live: {ARTIFACT_URL}"}])
    return win


@scene("81-artifact-links", "Artifact links in a reply become cards (known title, or 'Claude artifact'); links are clickable")
def _(app):
    win = app.window(opts())
    publish_artifact(win, "a1", "Ops Dashboard", "chart")
    win.render_assistant([{"type": "text", "text": "Two pages to compare:\n\n"
                           f"- the new one: {ARTIFACT_URL}\n- last month's: https://claude.ai/artifact/OLD123xyz\n\n"
                           "Docs: https://docs.gtk.org/gtk4/"}])
    return win


@scene("82-panel-artifacts", "Side panel, Artifacts: everything published or linked in this session", size=(1200 + SIDEBAR, H))
def _(app):
    win = app.window(opts())
    publish_artifact(win, "p1", "Ops Dashboard", "chart")
    publish_artifact(win, "p2", "Weekly Report", "doc", "https://claude.ai/artifact/WEEKLYrep0rt")
    publish_artifact(win, "p3", "Depot Map", "map", "https://claude.ai/artifact/DEPOTmap99")
    win.show_panel(True, "artifacts")
    return win


@scene("83-artifact-history", "A resumed Claude Desktop session: artifacts it published come back as cards")
def _(app):
    proj = os.path.join(T.TMP, "artifact-proj")
    os.makedirs(proj, exist_ok=True)
    page = os.path.join(proj, "quote-builder.html")
    with open(page, "w") as f:
        f.write("<title>Quote Builder</title>")
    sid = "77777777-0000-4000-8000-000000000007"
    d = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", proj.replace("/", "-"))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{sid}.jsonl")
    recs = [{"type": "user", "cwd": proj, "entrypoint": "claude-desktop", "message": {"content": "Make a quote builder"}},
            {"type": "assistant", "cwd": proj, "message": {"content": [{"type": "text", "text": "Done — it's published."}]}},
            {"type": "frame-link", "frameUrl": "https://claude.ai/artifact/QUOTEbuild3r", "path": page,
             "title": "Quote Builder", "artifactCount": 1},
            {"type": "custom-title", "customTitle": "Quote builder", "sessionId": sid}]
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(r) for r in recs) + "\n")
    age(path)
    win = app.window(opts(cwd=proj))
    win.resume_session(baude.read_session_info(path))
    T.wait_for(lambda: win.session_id and win.proc, "resume")
    return win


def feedback_with_capture(win, regions=True):
    """A draft: text, and a capture of a chat with a reply (and two marked regions)."""
    win.render_assistant([{"type": "text", "text": "The background build is still running; I'll check it in a minute."},
                          {"type": "tool_use", "id": "fb1", "name": "Bash",
                           "input": {"command": "make -j8", "description": "Build everything"}}])
    win.render_tool_results([{"type": "tool_result", "tool_use_id": "fb1", "content": "ok"}])
    T.pump(0.4)
    draft = baude.FeedbackDraft()
    draft.text = "The process rows in the side panel wrap the whole command over dozens of lines."
    cap = baude.capture_window(win)
    if regions:
        for want in ("background build", "Build everything"):
            it = next(i for i in cap.items if want in i["text"])
            cap.regions.append((it["x"] - 6, it["y"] - 6, it["w"] + 12, it["h"] + 12))
    draft.captures.append(cap)
    win.feedback_draft = draft
    return cap


@scene("90-feedback-dialog", "Menu → Send Feedback…: what's the issue or suggestion, and an empty box with + to "
                             "add a screen capture")
def _(app):
    win = app.window(opts())
    dlg = win.open_feedback()
    dlg.text.grab_focus()
    return win


@scene("91-feedback-capture-bar", "After +: the dialog steps aside and a bar at the bottom waits — go to what you "
                                  "want to show, then Capture")
def _(app):
    win = app.window(opts())
    win.render_assistant([{"type": "text", "text": "Here's the table you asked for."}])
    dlg = win.open_feedback()
    T.pump(0.2)
    win.start_feedback_capture(dlg)
    T.pump(0.4)
    return win


@scene("92-feedback-with-capture", "Back in the dialog with a capture (the window as an image, plus its text on "
                                   "screen); click it to mark regions, + for another")
def _(app):
    win = app.window(opts())
    feedback_with_capture(win)
    win.open_feedback()
    return win


@scene("93-feedback-regions", "Marking regions on a capture: drag to outline what you mean, right-click one to "
                              "remove it; the text inside each is saved with it", size=(1200 + SIDEBAR, 860))
def _(app):
    win = app.window(opts())
    cap = feedback_with_capture(win)
    baude.RegionEditor(cap).present(win)
    return win


@scene("60-effort-unavailable", "A model without thinking effort: the effort picker is greyed out")
def _(app):
    win = app.window(opts())
    T.wait_for(lambda: win.model_dd.values, "models")
    win.model_dd.set_selected(win.model_dd.values.index("claude-haiku-4-5"))
    return win


@scene("61-context-high", "Context meter close to full (red), with a message to show it in use")
def _(app):
    win = app.window(opts())
    win.render_assistant([{"type": "text", "text": "Long conversation…"}])
    win.show_context({"totalTokens": 181000, "maxTokens": 200000,
                      "categories": [{"name": "Messages", "tokens": 160000, "kind": "used"}]})
    return win


@scene("62-full-width", "Full-width text on a wide window", size=(1400 + SIDEBAR, 760))
def _(app):
    win = app.window(opts())
    win.lookup_action("fullwidth").change_state(GLib.Variant.new_boolean(True))
    win.render_assistant([{"type": "text", "text": "This paragraph stretches across the whole window "
                                                    "instead of stopping at 860 pixels. " * 4}])
    return win


@scene("63-zoomed", "Text zoomed to 140% (Ctrl +)")
def _(app):
    win = app.window(opts())
    win.set_zoom(1.4)
    win.render_assistant([{"type": "text", "text": "## Zoomed\nBigger text in the chat **and** the input box."}])
    win.input.get_buffer().set_text("typing at 140%")
    return win


# ---- the Feedback tab: a fake GitHub with feedback at every stage, the images of real windows (fixview.py)

def real_drag(win, labels, start, end):
    """A real mouse (tests/mouse.py) dragged from a character of a label to one of another, through those between."""
    import mouse
    hand = mouse.Mouse(win)

    def at(label, i):
        ox, oy = label.get_layout_offsets()
        pos = label.get_layout().index_to_pos(len(label.get_text()[:i].encode()))
        return label.compute_point(win, T.widgets.Graphene.Point().init(
            ox + pos.x / T.Pango.SCALE + 1, oy + (pos.y + pos.height / 2) / T.Pango.SCALE))[1]

    points = [at(labels[start[0]], start[1]), at(labels[start[0]], start[1] + 8),
              *(at(labels[i], 4) for i in range(start[0] + 1, end[0])), at(labels[end[0]], end[1])]
    hand.down(points[0].x, points[0].y)
    T.pump(0.05)
    for p in points[1:]:
        hand.drag(p.x, p.y)
        T.pump(0.05)
    hand.up(points[-1].x, points[-1].y)
    T.pump(0.1)


@scene("97-chat-selection", "A drag from one paragraph of a reply to another: what it passes is selected, and copied")
def _(app):
    win = app.window(opts())
    win.render_assistant([{"type": "text", "text":
                           "The nightly job stopped at the export step, because the bucket's credentials had expired "
                           "an hour before it ran.\n\nThree things follow from that:\n\n"
                           "- the export is still queued, so nothing was lost\n"
                           "- the retry won't help until the credentials are renewed\n"
                           "- yesterday's numbers are the newest in the dashboard\n\n"
                           "Once they're renewed, run the job again by hand and it picks up where it stopped, with no "
                           "need to start the export from the beginning."}])
    T.pump(0.4)
    labels = [w for w in T.widgets.walk(win.transcript) if isinstance(w, Gtk.Label) and w.get_selectable()]
    real_drag(win, labels, (0, 20), (len(labels) - 1, 40))
    return win


def window_png(app, css=None):
    """A real window as a PNG, offscreen or not: the empty chat, with css added (what a fix might have changed)."""
    app.next_size = (900, 560)
    win = app.window(opts())
    provider = None
    if css:
        provider = Gtk.CssProvider()
        provider.load_from_string(css)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    T.pump(0.5)
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, win.get_width(), win.get_height())
    png = win.get_native().get_renderer().render_texture(snap.to_node(), None).save_to_png_bytes().get_data()
    if provider:
        Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), provider)
    T.close(win)
    return png


def ago_iso(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))


def issue(number, title, body, age, state="OPEN", reason="", comments=()):
    return {"number": number, "title": title, "body": body, "state": state, "stateReason": reason,
            "author": {"login": "nassir90"}, "createdAt": ago_iso(age), "updatedAt": ago_iso(age / 3),
            "url": f"https://github.com/nassir90/Baude/issues/{number}", "comments": list(comments)}


def fix_comment(text="", age=600, **mark):
    return {"author": {"login": "nassir90"}, "authorAssociation": "OWNER", "createdAt": ago_iso(age),
            "body": T.fixes.round_body("nassir90/Baude", text, **mark)}


def saved_note(name, text, png, issue=None):
    """A note as Send Feedback… leaves it, with a capture."""
    folder = os.path.join(baude.feedback_dir(), name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "feedback.md"), "w") as f:
        f.write(f"# Feedback 2026-10-{name[6:8]} 14:20\n\n{text}\n\n## Context\n\n- session: s1 (Runner Dashboard)\n"
                f"- folder: {T.TMP}\n- Baude: x\n")
    with open(os.path.join(folder, "capture-1.png"), "wb") as f:
        f.write(png)
    if issue:
        with open(os.path.join(folder, "issue.json"), "w") as f:
            json.dump(issue, f)
    return folder


def fixes_tab(app, select=None, show_all=False, viewer="ADMIN", notes=False, private=False):
    """A window with ☰ → Ongoing Feedback open, read from a fake GitHub: returned with the page ready. private: there's
    a private repository for feedback (fixes.PRIVATE_FEEDBACK) too, that it's shown."""
    fake = os.path.join(T.TMP, "fake-gh")
    with open(fake, "w") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{os.path.join(HERE, "fake_gh.py")}" "$@"\n')
    os.chmod(fake, 0o755)
    for path, png in (("12/1/1-before.png", window_png(app)),
                      ("12/1/1-after.png", window_png(app, ".composer-box { box-shadow: 0 0 0 2px @accent_color; }"))):
        kept = os.path.join(T.fixes.fixes_home(), "shots", path)
        os.makedirs(os.path.dirname(kept), exist_ok=True)
        with open(kept, "wb") as f:
            f.write(png)
    pr = {"number": 41, "title": "Give the input box a stronger outline", "state": "OPEN", "isDraft": False,
          "headRefName": "fix/12", "url": "https://github.com/nassir90/Baude/pull/41", "mergedAt": None,
          "updatedAt": ago_iso(600), "closingIssuesReferences": [{"number": 12}]}
    merged = {**pr, "number": 38, "state": "MERGED", "headRefName": "fix/8", "mergedAt": ago_iso(86400),
              "closingIssuesReferences": [{"number": 8}], "url": "https://github.com/nassir90/Baude/pull/38"}
    state = {"viewer": viewer, "prs": [pr, merged], "issues": [
        issue(12, "The input box is hard to find when the chat is empty",
              "In an empty chat, the input box barely stands out from the window around it.\n\n"
              "Could it have a stronger outline?\n\n_Sent from Baude's Send Feedback…_", 7200, comments=[
                  fix_comment(age=1500, state="working", round=1, machine="naza-mbp"),
                  fix_comment("The input box now has a 2px outline in the accent colour (`.composer-box` in "
                              "`widgets.py`), in both themes.", age=600, state="ready", round=1, machine="naza-mbp",
                              shots=[{"label": "The empty chat", "before": "12/1/1-before.png",
                                      "after": "12/1/1-after.png"}])]),
        issue(11, "Kubernetes view forgets the namespace when it reloads",
              "After a cluster reconnects, the namespace goes back to *default*.", 18000, comments=[
                  fix_comment(age=240, state="working", round=1, machine="mac-studio")]),
        issue(10, "Zoom is jittery: passes through two sizes on resize",
              "Pressing ⌘+ on a long chat, the text jumps to one size and then settles on another.\n\n"
              "## Context\n\n- folder: ~/projects/Baude\n\n_Sent from Baude's Send Feedback…_", 90000),
        issue(9, "Show which model answered each message", "A small label beside each reply would do.", 172800),
        issue(8, "Highlight a hovered git reference in the Git pane", "As the editor does.", 400000, "CLOSED", "COMPLETED"),
        issue(7, "Replace the sidebar with a floating palette", "It would save room.", 600000, "CLOSED", "NOT_PLANNED",
              comments=[{"author": {"login": "nassir90"}, "authorAssociation": "OWNER", "createdAt": ago_iso(500000),
                        "body": "Rejected from Baude."}]),
    ]}
    if private:
        inside = {**issue(3, "The approval card's border is thicker than the tool rows'",
                          "Approvals have a 2px border, failed and passed tools 1px.", 5400, comments=[
                              fix_comment("Every card's border is now 1px (`.perm-card` in `widgets.py`).", age=300,
                                          state="ready", round=1, machine="naza-mbp")]),
                  "url": "https://github.com/nassir90/BaudeInternal/issues/3"}
        state["issues_on"] = {"nassir90/BaudeInternal": [inside]}
        state["prs"].append({**pr, "number": 44, "title": "One border width for every card",
                             "headRefName": "fix/BaudeInternal-3", "url": "https://github.com/nassir90/Baude/pull/44",
                             "updatedAt": ago_iso(300), "closingIssuesReferences": []})
    path = os.path.join(T.TMP, "fake-gh.json")
    with open(path, "w") as f:
        json.dump(state, f)
    os.environ.update(BAUDE_GH=fake, FAKE_GH_STATE=path, FAKE_GH_SIGNED_IN="1")
    T.feedback._gh_seen.update(at=None, repo=None)
    T.fixes._can_write.clear()
    T.fixes._private_seen.clear()
    if notes:
        png = window_png(app)
        saved_note("20261020-142000", "The sessions sidebar jumps when a chat finishes\n\nIt scrolls back to the top, and I "
                                      "lose my place.", png)
        saved_note("20261021-142000", "The input box is hard to find when the chat is empty", png,
                   {"url": "https://github.com/nassir90/Baude/issues/12", "number": 12, "repo": T.feedback.code_repo()})
    app.next_size = (W, H)
    win = app.window(opts())
    win.activate_action("win.fixes")
    page = win.tabs.get(("fixes",))
    T.wait_for(lambda: page.read_at, "the feedback read")
    if show_all:
        page.all_btn.set_active(True)
    if select:
        page.rows.select_row(next(page.rows.get_row_at_index(n) for n in range(len(page.listed()))
                                  if page.rows.get_row_at_index(n).number == select))
    T.pump(0.6)
    return win, page


@scene("96-feedback-ready", "☰ → Ongoing Feedback: a fix ready for review, its images before and after, Merge or Discard")
def _(app):
    return fixes_tab(app)[0]


@scene("96b-feedback-open", "Feedback nobody's started on: Fix here starts a fix on this machine")
def _(app):
    return fixes_tab(app, select=10)[0]


@scene("96c-feedback-working", "A round running on someone's machine, seen by everyone")
def _(app):
    return fixes_tab(app, select=11)[0]


@scene("96d-feedback-all", "All of it: merged and rejected too, a rejected one reopened from here")
def _(app):
    return fixes_tab(app, select=7, show_all=True)[0]


@scene("96e-feedback-merge", "Merging a fix asks first: main reaches everyone")
def _(app):
    win, page = fixes_tab(app)
    page.merge()
    T.pump(0.4)
    return win


@scene("96g-feedback-offline-note", "A note saved on this machine and never filed: its own row, with its captures")
def _(app):
    return fixes_tab(app, select=os.path.join(baude.feedback_dir(), "20261020-142000"), notes=True)[0]


@scene("96h-feedback-note-on-issue", "A note filed as an issue: shown under that issue, with its captures")
def _(app):
    return fixes_tab(app, select=12, notes=True)[0]


@scene("96i-feedback-private", "Feedback kept on a private repository, beside Baude's: each says where it is, its fix "
                                "a PR on Baude")
def _(app):
    return fixes_tab(app, select="BaudeInternal-3", private=True, notes=True)[0]


@scene("96f-feedback-read-only", "Without write access to the repository: read only")
def _(app):
    return fixes_tab(app, viewer="READ")[0]


@scene("96j-feedback-loading", "Reading it again: the refresh icon, in its full colour, turning (here, part way round)")
def _(app):
    win, page = fixes_tab(app)
    page.refresh_btn.set_loading(True)
    page.refresh_btn.turning.at = lambda _now: (0.125, T.widgets.Turning.SPEED)  # (a picture holds still)
    page.refresh_btn.queue_draw()
    return win


def held_read(seconds=5):
    """The feedback's read kept back a while, for a picture of the page as it opens; the read itself put back after."""
    import threading
    hold, repo = threading.Event(), T.feedback.issues_repo
    T.feedback.issues_repo = lambda: (hold.wait(seconds), repo())[1]
    GLib.timeout_add(int(seconds * 1000) + 500, lambda: setattr(T.feedback, "issues_repo", repo) or False)


@scene("96k-feedback-first-open", "Opened for the first time, reading: the page's there, the list says it's loading, nothing on the right")
def _(app):
    win = app.window(opts())
    if os.path.exists(T.fixes.fixes_home()):
        shutil.rmtree(T.fixes.fixes_home(), ignore_errors=True)
    held_read()
    win.open_fixes()
    T.pump(0.3)
    return win


@scene("96l-feedback-opened-again", "Opened again: what was read last time, at once, as it's read again")
def _(app):
    win, page = fixes_tab(app)
    win.tabs.close(("fixes",))
    T.pump(0.2)
    held_read()
    win.open_fixes()
    T.pump(0.3)
    return win


# --------------------------------------------------------------- capture --

def capture(win, size, path):
    try:
        # the widget size excludes the client-side shadow (a few px per side) that the capture includes
        T.wait_for(lambda: all(0 <= want - got <= 24 for want, got in zip(size, (win.get_width(), win.get_height()))),
                   f"window at {size}", 5)
    except AssertionError:
        raise AssertionError(f"window is {win.get_width()}x{win.get_height()}, wanted {size[0]}x{size[1]}")
    T.pump(0.6)  # let layout, the 120 ms reveal animations and popovers settle
    if os.environ.get("DISPLAY"):
        img = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])  # the whole screen, so popovers are included
        img.crop((0, 0, *size)).save(path)
        return
    if sys.platform == "darwin":
        try:
            img = capture_macos(win)
            img.crop((0, 0, min(img.width, size[0]), min(img.height, size[1]))).save(path)
            return
        except subprocess.CalledProcessError:  # (no Screen Recording for this process: GTK draws it, as elsewhere)
            pass
    # elsewhere without X: GTK draws the window itself, and the popovers open in it (surfaces of their own) where they are
    img = render_widget(win, path)
    for pop in T.find(win, Gtk.Popover, lambda p: p.get_visible() and p.get_mapped()):
        # (each placed in the surface of what it's in: the window's, or another popover's, which is placed in its own)
        x = y = 0
        outer = pop
        while isinstance(outer, Gtk.Popover) and outer.get_surface() is not None:
            x, y = x + outer.get_surface().get_position_x(), y + outer.get_surface().get_position_y()
            outer = outer.get_parent().get_native() if outer.get_parent() is not None else None
        if pop.get_width() and pop.get_height():
            shot = render_widget(pop, path + ".popover.png")
            os.remove(path + ".popover.png")
            img.paste(shot, (x, y), shot)
    img.crop((0, 0, min(img.width, size[0]), min(img.height, size[1]))).save(path)


def render_widget(widget, path):
    """A widget as GTK draws it (its own surface's renderer), as an RGBA image (saved at path on the way)."""
    paintable = Gtk.WidgetPaintable.new(widget)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, widget.get_width(), widget.get_height())
    widget.get_native().get_renderer().render_texture(snap.to_node(), None).save_to_png(path)
    return Image.open(path).convert("RGBA")


def capture_macos(win):
    """macOS: the window as the screen shows it, its traffic lights too (macOS draws those, not GTK). screencapture -l
    takes one window by its number, with the windows it owns (its popovers), so a window of another app in front of
    it never gets in. Scaled back to points."""
    import ctypes
    import subprocess
    import tempfile
    import macos

    main = macos.native_window(win)
    number = macos.objc_send(main, b"windowNumber", ret=ctypes.c_long)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        subprocess.run(["screencapture", "-x", "-o", "-l", str(number), tmp], check=True)
        img = Image.open(tmp).convert("RGB")
    finally:
        os.unlink(tmp)
    scale = img.width / macos.objc_send(main, b"frame", ret=macos._rect()).w
    return img.resize((round(img.width / scale), round(img.height / scale)), Image.LANCZOS)


def reset_state():
    baude.save_settings(zoom=1.0, full_width=False, sidebar=True, collapsed_folders=["archived"], archived=[],
                        signal_reader=False, sidebar_network=None, sidebar_hosts=None, sidebar_groups=None,
                        hidden_remote=None)
    os.environ["BAUDE_GH"] = ""  # (a Feedback tab's fake GitHub is that scene's, its notes too)
    shutil.rmtree(baude.feedback_dir(), ignore_errors=True)
    T.feedback._gh_seen.update(at=None, repo=None)


THEMES = ("light", "dark")


def contact_sheet(rows, out_dir, theme, per_row=4, thumb_w=360):
    """matrix-<theme>.png: every scene in one theme, in a grid, each with its name above."""
    pad, label_h = 18, 20
    cells = []
    for name, _, _ in rows:
        im = Image.open(os.path.join(out_dir, f"{name}-{theme}.png")).convert("RGB")
        cells.append((name, im.resize((thumb_w, round(im.height * thumb_w / im.width)))))
    grid = [cells[i:i + per_row] for i in range(0, len(cells), per_row)]
    heights = [label_h + max(im.height for _, im in line) for line in grid]
    bg, fg = ("white", "black") if theme == "light" else ("#1e1e1e", "#e8e8e8")
    sheet = Image.new("RGB", (pad + per_row * (thumb_w + pad), pad + sum(h + pad for h in heights)), bg)
    draw = ImageDraw.Draw(sheet)
    y = pad
    for line, h in zip(grid, heights):
        for col, (name, im) in enumerate(line):
            x = pad + col * (thumb_w + pad)
            draw.text((x, y + 4), name, fill=fg)
            sheet.paste(im, (x, y + label_h))
        y += h + pad
    sheet.save(os.path.join(out_dir, f"matrix-{theme}.png"))


INDEX_HEAD = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>baude screenshots</title>
<style>
 :root { --bg: #f6f5f4; --fg: #222; --dim: #666; --line: #d0d0d0; --btn: #fff; }
 :root[data-theme="dark"] { --bg: #1e1e1e; --fg: #eee; --dim: #aaa; --line: #444; --btn: #2d2d2d; }
 body { font: 15px system-ui, sans-serif; margin: 0; background: var(--bg); color: var(--fg); }
 header { position: sticky; top: 0; z-index: 1; display: flex; gap: 16px; align-items: center; padding: 12px 24px;
          background: var(--bg); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
 header h1 { font-size: 18px; margin: 0; } header .meta { color: var(--dim); flex: 1; font-size: 13px; }
 .switch { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
 .switch button { font: inherit; padding: 6px 14px; border: 0; background: var(--btn); color: var(--fg); cursor: pointer; }
 .switch button[aria-pressed="true"] { background: #3584e4; color: white; }
 main { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 520px), 1fr)); gap: 28px; padding: 24px; }
 h2 { margin: 0 0 4px; font-size: 16px; } p { margin: 0 0 10px; color: var(--dim); font-size: 14px; }
 img { width: 100%; border: 1px solid var(--line); border-radius: 6px; display: block; }
</style>
"""

INDEX_SCRIPT = """<script>
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  for (const img of document.querySelectorAll("img[data-scene]")) {
    img.src = img.dataset.scene + "-" + theme + ".png";
    img.parentElement.href = img.src;
  }
  for (const b of document.querySelectorAll(".switch button")) b.setAttribute("aria-pressed", b.dataset.theme === theme);
  try { localStorage.setItem("baude-shots-theme", theme); } catch (e) {}
}
let start = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
try { start = localStorage.getItem("baude-shots-theme") || start; } catch (e) {}
for (const b of document.querySelectorAll(".switch button")) b.addEventListener("click", () => setTheme(b.dataset.theme));
setTheme(start);
</script>
"""


def write_index(rows, out_dir):
    """index.html: one image per scene and a single Light / Dark switch for the whole page."""
    cards = "\n".join(
        f'<section><h2>{html.escape(n)}</h2><p>{html.escape(d)}</p>'
        f'<a href="{n}-light.png"><img data-scene="{n}" src="{n}-light.png" alt="{html.escape(n)}" loading="lazy"></a>'
        f'</section>' for n, d, _ in rows)
    header = (f'<header><h1>baude screenshots</h1><span class="meta">{len(rows)} scenes · generated '
              f'{time.strftime("%Y-%m-%d %H:%M")} by tests/screenshots.sh</span>'
              '<span class="switch" role="group" aria-label="Theme"><button data-theme="light">Light</button>'
              '<button data-theme="dark">Dark</button></span></header>')
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(INDEX_HEAD + header + "\n<main>\n" + cards + "\n</main>\n" + INDEX_SCRIPT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="only scenes whose name contains this")
    ap.add_argument("--only", help="only the scene of this name, and no gallery: what a fix's shots are (fixes.shoot)")
    ap.add_argument("--theme", choices=THEMES, help="only this theme, not both")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(HERE), "screenshots"))
    args = ap.parse_args()
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    app = T.App()
    style = Adw.StyleManager.get_default()
    rows, failed = [], 0
    for name, description, size, build in sorted(SCENES, key=lambda sc: sc[0]):
        if args.k not in name or args.only not in (None, name):
            continue
        made = 0
        for theme, scheme in (("light", Adw.ColorScheme.FORCE_LIGHT), ("dark", Adw.ColorScheme.FORCE_DARK)):
            if args.theme not in (None, theme):
                continue
            style.set_color_scheme(scheme)
            reset_state()
            win = None
            app.next_size = size
            try:
                win = build(app)
                capture(win, size, os.path.join(out_dir, f"{name}-{theme}.png"))
                made += 1
            except Exception as e:  # noqa: BLE001 — keep going, report at the end
                failed += 1
                print(f"FAIL  {name} ({theme}): {e!r}")
            finally:
                for w in Gtk.Window.list_toplevels():  # dialogs and popovers too
                    if isinstance(w, baude.BaudeWindow):
                        T.close(w)
                reset_state()
        if made == (1 if args.theme else 2):  # only count scenes rendered in this run, not leftovers from an earlier one
            rows.append((name, description, size))
            print(f"ok    {name}")
    if rows and not args.only and not args.theme:
        write_index(rows, out_dir)
        for theme in THEMES:
            contact_sheet(rows, out_dir, theme)
        stale = os.path.join(out_dir, "matrix.png")  # the old side-by-side sheet
        if os.path.exists(stale):
            os.remove(stale)
    print(f"\n{len(rows)} scenes, {failed} failures -> {out_dir}/index.html")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
