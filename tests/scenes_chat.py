"""scenes_chat — tests/screenshots.py's scenes of a chat: turns, permissions and questions, tool cards and their
output, images, markdown and tables, links and Signal, code, schemas, Kubernetes and Git, the box and its lists, and
the sessions sidebar.
"""
import base64
import json
import os
import shutil
import time

import test_baude as T  # noqa: E402  (sets up the throwaway env: fake claude, temp config dirs)
from test_baude import Gdk, Gtk, baude  # noqa: E402

from scenelib import (
    H, HERE, SIDEBAR, age, ask_permission, opts, png_bytes, scene, sidebar_row, sidebar_sessions, tool_with_output,
)


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


@scene("15-question-asking", "claude's AskUserQuestion: its questions and options, one picked, Other typed")
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "QUESTION where to keep categories")
    T.wait_for(lambda: T.button(win, "Answer") or T.button(win, "Allow"), "the question")
    form = next(iter(win.waiting_cards)).asking if T.button(win, "Answer") else None
    if form is not None:
        q = form.get_first_child()
        q.picks[0][1][0][1].set_active(True)
        q.picks[1][2].set_active(True)
        q.picks[1][3].set_text("Games")
    return win


@scene("16-question-answered", "claude's AskUserQuestion answered: the card folds, its answers sent and said back")
def _(app):
    win = app.window(opts())
    T.type_and_send(win, "QUESTION where to keep categories")
    T.wait_for(lambda: T.button(win, "Answer"), "the question")
    q = next(iter(win.waiting_cards)).asking.get_first_child()
    q.picks[0][1][0][1].set_active(True)
    q.picks[1][1][1][1].set_active(True)
    T.button(win, "Answer").emit("clicked")
    T.wait_for(lambda: not win.busy, "turn end")
    card = next(c for c in win.tool_cards.values() if c.name == "AskUserQuestion")
    card.output_toggle.set_expanded(True)
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
    """A small app's schema (the shape PG_SCHEMA_SQL returns: tests/parity/fixtures/databases.json's), for the diagram
    scene: no database needed."""
    with open(os.path.join(HERE, "parity", "fixtures", "databases.json")) as f:
        return baude.pg_normalize(json.load(f)["raw"])


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
    baude._CLAUDE_VERSION[baude.claudelib.claude_path()] = "2.1.300"
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


def queued_then_left(app):
    """A chat at a long turn (the fake's LINGER) with two messages queued under it, then another chat opened: the
    window, and the left chat's id. Its transcript's kept as claude keeps it (FAKE_CLAUDE_TRANSCRIPTS), to be drawn as
    it's opened again."""
    os.environ["FAKE_CLAUDE_TRANSCRIPTS"] = "1"
    win = app.window(opts())
    T.type_and_send(win, "LINGER: tidy the release notes for 0.9.")
    T.wait_for(lambda: win.busy and win.session_id, "the turn")
    T.type_and_send(win, "Then tag the release as v0.9.0.")
    T.type_and_send(win, "And push the tag to origin.")
    sid = win.session_id
    T.wait_for(lambda: win.sidebar.find_info(sid), "listed")
    win.new_session()
    T.pump(0.5)
    return win, sid


@scene("46-queue-kept", "A chat left with two messages queued mid-turn and opened again: its queue is under it as it was")
def _(app):
    win, sid = queued_then_left(app)
    win.resume_session(win.sidebar.find_info(sid))
    T.wait_for(lambda: win.session_id == sid, "the chat")
    T.pump(1.5)
    return win


@scene("46b-queue-sent-elsewhere", "A chat's turn ends while another chat is open: what was queued in it is sent to "
                                   "it then, and is there as it's opened")
def _(app):
    win, sid = queued_then_left(app)
    try:  # (sent, in the left chat's transcript at its host)
        T.wait_for(lambda: any("Then tag the release" in json.dumps(m) for m in T.user_messages()), "the queue sent", 15)
    except AssertionError:
        pass
    T.pump(1.5)
    win.resume_session(win.sidebar.find_info(sid))
    T.wait_for(lambda: win.session_id == sid, "the chat")
    T.pump(1.5)
    return win
