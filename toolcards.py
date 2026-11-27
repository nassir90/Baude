"""toolcards — a chat's tool calls: artifacts (ArtifactChip), a call's input and summary (tool_input_view,
summarize_tool), commands, a task's note, thinking, a question asked (QuestionForm), a call's card (ToolCard) and
runs of them (ToolGroup), and a process's row (ProcessRow, paint_command).
"""
import base64
import html
import json
import os
import re
import weakref

from baudelib import toolnames, wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

import pointers  # noqa: E402  (the pointer's shape: Record's too)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
import codespans  # noqa: E402
import markdownview  # noqa: E402
import chatparts  # noqa: E402
from widgets import Disclosure, _card_status, _status_icon  # noqa: E402,F401
from widgets import IMAGE_POOL, discard, ensure_icons, globe_icon, themed_icon, toast, walk  # noqa: E402,F401
from imageviews import IMAGE_LINK, image_link_markup, image_widget  # noqa: E402,F401
from markdownview import ARTIFACT_TITLES, CodeBlock, diff_block, MarkdownView, link_markup, sel_label  # noqa: E402,F401
from panels import natural_height  # noqa: E402,F401

from signalrefs import TOOL_NAMES  # noqa: E402


# -------------------------------------------------------------- artifacts --
# Claude publishes pages with its Artifact tool ("Published <file> at https://claude.ai/artifact/<id>
# (Version 1 ...) Icon: "chart"."). baude shows them as cards with a real title and open/copy.

ARTIFACT_URL = re.compile(r"https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]+")
ARTIFACT_ICONS = {"chart": "x-office-spreadsheet-symbolic", "dashboard": "x-office-spreadsheet-symbolic",
                  "table": "x-office-spreadsheet-symbolic", "calendar": "x-office-calendar-symbolic",
                  "map": "baude-map-symbolic", "code": "utilities-terminal-symbolic",
                  "doc": "x-office-document-symbolic", "document": "x-office-document-symbolic",
                  "slides": "x-office-presentation-symbolic", "deck": "x-office-presentation-symbolic",
                  "image": "image-x-generic-symbolic", "music": "audio-x-generic-symbolic",
                  "game": "applications-games-symbolic", "mail": "mail-unread-symbolic"}


def html_title(path):
    """The <title> of a local HTML file (the Artifact tool requires pages to have one),
    or the first heading of a Markdown file."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(200_000)
    except (OSError, TypeError):
        return None
    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.S | re.I)
    if m:
        return html.unescape(" ".join(m.group(1).split())) or None
    m = re.search(r"^#\s+(.+)$", head, re.M)
    return m.group(1).strip() if m else None


def artifact_from_tool(inp, result_text):
    """Artifact info from an Artifact tool call and its result, or None if nothing was published."""
    m = re.search(r"Published (\S+) at (" + ARTIFACT_URL.pattern + ")", result_text or "")
    url = m.group(2) if m else None
    if url is None and inp.get("type_url") and (m2 := ARTIFACT_URL.search(result_text or "")):
        url = m2.group(0)  # created from a type: the new artifact's link
    if url is None:
        return None
    path = (m.group(1) if m else None) or inp.get("file_path")
    version = re.search(r"\(Version (\d+)", result_text or "")
    icon = re.search(r'Icon: "([\w-]+)"', result_text or "")
    title = (inp.get("title") or html_title(path)
             or (humanize(os.path.splitext(os.path.basename(path))[0]).capitalize() if path else None))
    return {"url": url, "title": title or wording.say("artifacts.untitled"), "path": path,
            "icon": (icon.group(1) if icon else inp.get("icon")),
            "version": int(version.group(1)) if version else None, "description": inp.get("description")}


class ArtifactChip(Adw.ActionRow):
    """An artifact's row in the side panel's list, as a subagent's is (as wide, as tall): [icon] Title /
    claude.ai/artifact/... · v2   [Open] [Copy link]; activated (a click anywhere on it), opened."""

    def __init__(self, info):
        super().__init__(activatable=True, title_lines=1, subtitle_lines=1)
        self.add_css_class("artifact-chip")
        self.info = info
        ensure_icons()
        icon = Gtk.Image.new_from_icon_name(themed_icon(ARTIFACT_ICONS.get(info.get("icon") or ""), globe_icon()))
        icon.add_css_class("artifact-icon")  # (the icons' size: Settings → Size)
        self.add_prefix(icon)
        sub = info["url"].replace("https://", "")
        if info.get("version"):
            sub = wording.say("artifacts.link_version", link=sub, version=info["version"])
        self.set_title(html.escape(info.get("title") or wording.say("artifacts.untitled")))
        self.set_subtitle(html.escape(sub))
        self.title, self.subtitle = (next(w for w in walk(self) if isinstance(w, Gtk.Label) and w.has_css_class(c))
                                     for c in ("title", "subtitle"))
        self.subtitle.set_ellipsize(Pango.EllipsizeMode.MIDDLE)  # (a link's start and end say which it is)
        self.open_btn = Gtk.Button(label=wording.say("common.open"), valign=Gtk.Align.CENTER, tooltip_text=info["url"])
        self.open_btn.connect("clicked", lambda *_: self.open())
        self.copy_btn = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                                   tooltip_text=wording.say("artifacts.copy_link"))
        self.copy_btn.add_css_class("flat")
        self.copy_btn.connect("clicked", lambda *_: (self.get_clipboard().set(info["url"]),
                                                     toast(self, wording.say("tabs.link_copied"))))
        self.add_suffix(self.open_btn)
        self.add_suffix(self.copy_btn)
        self.connect("activated", lambda *_: self.open())
        pointers.hand(self)  # (a click anywhere on it opens it; its buttons keep their own)
        self.set_tooltip_text(info.get("description") or info["url"])

    def open(self):
        Gtk.UriLauncher(uri=self.info["url"]).launch(self.get_root(), None, None)


# ------------------------------------------------------------- tool views --
# Tool inputs are shown as what they mean (a command, a diff, a path), never as raw JSON.

LANG_BY_EXT = {".py": "python", ".js": "javascript", ".ts": "typescript", ".sh": "bash", ".rs": "rust",
               ".go": "go", ".json": "json", ".md": "markdown", ".html": "html", ".css": "css",
               ".c": "c", ".h": "c", ".cpp": "cpp", ".java": "java", ".rb": "ruby", ".toml": "toml",
               ".yaml": "yaml", ".yml": "yaml", ".sql": "sql", ".tsx": "tsx", ".jsx": "jsx"}


def _first_line(v, limit=120):
    v = str(v).strip()
    return (v.splitlines()[0] if v else "")[:limit]


ANSWER_TASK = "mcp__mesh__answer_task"  # (a Bot's answer to its task's ask: drawn as "Answered <task>")


def answer_line(inp):
    """An answer_task call's answer in a line of at most 120 characters: each field = value (its first line), · between
    (a value as it's written: text as it is, the rest as JSON), or its text's first line."""
    fields = inp.get("answer")
    if isinstance(fields, str):  # (an answer given as JSON)
        try:
            fields = json.loads(fields)
        except ValueError:
            pass
    if isinstance(fields, dict):
        return _first_line(" · ".join(
            f"{k} = {_first_line(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))}"
            for k, v in fields.items()))
    return _first_line(inp["text"]) if isinstance(inp.get("text"), str) else ""


def answer_result(text):
    """What an answer_task call came back with ({task, run, values or text}; Codex's: inside the MCP result it gives),
    or None (it failed, or isn't that)."""
    try:
        got = json.loads(text or "")
        if isinstance(got, dict) and isinstance(got.get("content"), list):
            got = json.loads("".join(c.get("text") or "" for c in got["content"] if isinstance(c, dict)))
    except ValueError:
        return None
    return got if isinstance(got, dict) and isinstance(got.get("task"), str) else None


def summarize_tool(name, inp):
    """One line for a tool card header: what the call is for (Claude's description), or failing that,
    what it touches (a path, a pattern, a URL). A command without a description gets nothing."""
    if not isinstance(inp, dict):
        return ""
    if name == "AskUserQuestion" and isinstance(inp.get("questions"), list) and inp["questions"] \
            and isinstance(inp["questions"][0], dict):
        qs = inp["questions"]
        if isinstance(inp.get("answers"), dict) and inp["answers"]:  # (answered: what was picked, by header)
            return " · ".join(f"{q.get('header') or q.get('question')}: {inp['answers'][q.get('question')]}"
                              for q in qs if isinstance(q, dict) and q.get("question") in inp["answers"])
        return _first_line(str(qs[0].get("question", ""))) + (f" (+{len(qs) - 1} more)" if len(qs) > 1 else "")
    if name == ANSWER_TASK:
        return answer_line(inp)
    if name == "ToolSearch" and (picked := toolnames.selected(inp.get("query"))) is not None:
        return ", ".join(tool_label(n) for n in picked)  # (the tools it loads, as their cards name them)
    if isinstance(inp.get("command"), str) and not (inp.get("description") or "").strip():
        return ""  # nothing rather than our own words: a description on this line is always Claude's
    for key in ("description", "command", "file_path", "notebook_path", "pattern", "url", "query",
                "prompt", "path", "skill"):
        if isinstance(inp.get(key), str) and inp[key].strip():
            return _first_line(inp[key])
    return ""


def _path_label(path, extra=""):
    lbl = sel_label(f"<tt>{html.escape(path)}</tt>" + (f"  <span alpha='60%'>{html.escape(extra)}</span>"
                                                        if extra else ""))
    lbl.add_css_class("tool-path")
    return lbl


def _value_widget(v, depth=0):
    """A readable widget for any JSON value: nested objects become indented rows."""
    if isinstance(v, str) and "\n" in v:
        return CodeBlock(v)
    if isinstance(v, bool):
        return sel_label(wording.say("tool_calls.yes" if v else "tool_calls.no"))
    if isinstance(v, (str, int, float)):
        return sel_label(html.escape(str(v)))
    if isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v):
        return sel_label(html.escape(", ".join(str(x) for x in v)))
    if isinstance(v, list) and all(isinstance(x, dict) and len(x) == 1 for x in v):
        # [{"login": "naza"}, {"login": "bob"}] -> naza, bob
        return sel_label(html.escape(", ".join(str(next(iter(x.values()))) for x in v)))
    if isinstance(v, dict) and depth < 3:
        return _kv_rows(v, depth=depth + 1) or sel_label("—")
    if isinstance(v, list) and depth < 3:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for x in v:
            box.append(_value_widget(x, depth + 1))
        return box
    return sel_label(html.escape(json.dumps(v, ensure_ascii=False)), mono=True)


def _kv_rows(inp, skip=(), depth=0):
    grid = Gtk.Grid(column_spacing=12, row_spacing=4)
    row = 0
    for k, v in inp.items():
        if k in skip or v in (None, "", [], {}):
            continue
        key = Gtk.Label(label=humanize(k), xalign=1, yalign=0)
        key.add_css_class("dim-label")
        val = _value_widget(v, depth)
        val.set_hexpand(True)
        grid.attach(key, 0, row, 1, 1)
        grid.attach(val, 1, row, 1, 1)
        row += 1
    return grid if row else None


humanize = toolnames.humanize  # (snake_case / camelCase / kebab-case -> 'words like this')


def tool_display(name):
    """(friendly title, source or None) for a tool name: an MCP server's tool (mcp__<server>__<tool>) as its server
    and its tool are named (baudelib.toolnames: what they and its host say, else its name in words):
    mcp__github__create_issue -> ("Create issue", "GitHub"); any other as it is."""
    got = toolnames.describe(name)
    if got is None:
        return name, None
    return got[1], got[0] or wording.say("tool_calls.mcp")  # (an unnamed connector: only an id)


def tool_label(name):
    """A tool's name as it's shown: an MCP server's tool as "Server / Tool" (mcp__mesh__tasks: Mesh / Tasks)."""
    return toolnames.label(name, wording.say("tool_calls.mcp"))


def tool_input_view(name, inp):
    """A readable rendering of a tool call's input."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    if not isinstance(inp, dict) or not inp:
        return box
    add = box.append
    base = name.split("__")[-1] if name.startswith("mcp__") else name
    # the header already shows the description, so the views don't repeat it
    if base in ("Bash", "BashOutput", "PowerShell") and "command" in inp:
        shell = "bash" if base != "PowerShell" else "powershell"
        add(CodeBlock(inp["command"], shell, syntax=shell))
        rest = _kv_rows(inp, skip=("command", "description"))
    elif base == "Codex file changes":
        for change in inp.get("changes", []):
            add(_path_label(change["path"], ""))
            add(CodeBlock(change.get("diff") or "", "diff"))
        rest = _kv_rows(inp, skip=("changes",))
    elif base == "Edit" and "file_path" in inp:
        add(_path_label(inp["file_path"], wording.say("tool_calls.all_occurrences") if inp.get("replace_all") else ""))
        add(diff_block(inp.get("old_string"), inp.get("new_string"), inp["file_path"]))
        rest = None
    elif base == "MultiEdit" and "file_path" in inp:
        add(_path_label(inp["file_path"], wording.say("tool_calls.edits", n=len(inp.get("edits") or []))))
        for e in inp.get("edits") or []:
            add(diff_block(e.get("old_string"), e.get("new_string"), inp["file_path"]))
        rest = None
    elif base == "Write" and "file_path" in inp:
        content = inp.get("content") or ""
        lines = content.splitlines()
        add(sel_label(wording.say("tool_calls.line_count", n=len(lines))))
        shown = "\n".join(lines[:2000]) + ("\n" + wording.say("tool_calls.more_lines", n=len(lines) - 2000) if len(lines) > 2000 else "")
        add(CodeBlock(shown, LANG_BY_EXT.get(os.path.splitext(inp["file_path"])[1], ""), path=inp["file_path"]))
        rest = None
    elif base == "Read" and "file_path" in inp:
        span = ""
        if inp.get("offset") or inp.get("limit"):
            start = inp.get("offset") or 1
            span = wording.say("code_view.lines", start=start, end=start + inp["limit"] - 1) if inp.get("limit") \
                else wording.say("tool_calls.from_line", start=start)
        add(_path_label(inp["file_path"], span))
        rest = _kv_rows(inp, skip=("file_path", "offset", "limit"))
    elif base in ("Grep", "Glob") and "pattern" in inp:
        grep = base == "Grep"  # (a regex drawn as one, and as it's written: a label's text, never markup or markdown)
        add(CodeBlock(inp["pattern"], "regex" if grep else "glob", syntax="regex" if grep else None))
        rest = _kv_rows(inp, skip=("pattern",))
    elif base == "WebFetch" and "url" in inp:
        add(sel_label(f'<a href="{html.escape(inp["url"])}">{html.escape(inp["url"])}</a>'))
        if inp.get("prompt"):
            add(MarkdownView(inp["prompt"]))
        rest = None
    elif base in ("Task", "Agent") and "prompt" in inp:
        add(MarkdownView(inp["prompt"]))
        rest = _kv_rows(inp, skip=("prompt", "description"))
    elif base == "AskUserQuestion" and isinstance(inp.get("questions"), list):
        answers = inp.get("answers") if isinstance(inp.get("answers"), dict) else {}
        for q in inp["questions"]:
            if not isinstance(q, dict):
                continue
            add(sel_label(f"<b>{html.escape(str(q.get('question', '')))}</b>"))
            for o in q.get("options") or []:
                o = o if isinstance(o, dict) else {"label": str(o)}
                desc = o.get("description") or ""
                add(sel_label(f"  •  {html.escape(str(o.get('label', '')))}" +
                              (f"  <span alpha='60%'>{html.escape(desc)}</span>" if desc else "")))
            if q.get("question") in answers:
                add(sel_label(f"  →  {html.escape(str(answers[q['question']]))}"))
        rest = None
    elif base == "TodoWrite" and isinstance(inp.get("todos"), list):
        for t in inp["todos"]:
            mark = {"completed": "☑", "in_progress": "◐"}.get(t.get("status"), "☐")
            text = html.escape(t.get("content") or t.get("activeForm") or "")
            add(sel_label(f"{mark}  <s>{text}</s>" if t.get("status") == "completed" else f"{mark}  {text}"))
        rest = None
    elif base == "ToolSearch" and (picked := toolnames.selected(inp.get("query"))) is not None:
        for n in picked:  # (the tools it loads, a line each, as their cards name them)
            add(sel_label(html.escape(tool_label(n))))
        rest = _kv_rows(inp, skip=("query",))
    else:
        rest = _kv_rows(inp)
    if rest:
        add(rest)
    return box


# What a restart waits for: a command running (real work, a process of its own), not the model writing (a turn cut short
# there is resumed from its transcript, and said so: show_cut). A call of one of these till its result (a subagent's
# too), and a background task that isn't a subagent till its end.
COMMAND_TOOLS = ("Bash", "PowerShell")
CUT_CONTINUE = wording.say("cut_short.message")


def command_what(inp):
    """A command call's input, as a restart's wait says it: its description, else its command's first line."""
    what = (inp or {}).get("description") or ((inp or {}).get("command") or "").strip().splitlines()[:1]
    what = what if isinstance(what, str) else (what[0] if what else "a command")
    return what if len(what) <= 80 else what[:79] + "…"


def track_commands(commands, msg):
    """commands ({id: what}) as claude's msg leaves them (track_commands of a Desktop chat's claude left mid-turn)."""
    t, content = msg.get("type"), (msg.get("message") or {}).get("content")
    if t == "assistant" and isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use" and \
                    str(b.get("name") or "").split("__")[-1] in COMMAND_TOOLS:
                commands[b.get("id")] = command_what(b.get("input"))
    elif t == "user" and isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                commands.pop(b.get("tool_use_id"), None)
    elif t == "system" and msg.get("subtype") == "task_started" and msg.get("task_type") != "local_agent":
        commands[f"task:{msg.get('task_id')}"] = msg.get("description") or wording.say("task_notes.unnamed")
    elif t == "system" and msg.get("subtype") == "task_notification":
        commands.pop(f"task:{msg.get('task_id')}", None)
    elif t == "result":  # (a turn's calls are done by its end: what's left running is a background task)
        for k in [k for k in commands if not str(k).startswith("task:")]:
            commands.pop(k)


STOP_SAID_RE = re.compile(r"\W*(api error:\s*)?((the )?(request|operation) (was )?(aborted|interrupted|cancell?ed)"
                          r"( by (the )?user)?|this operation was aborted|aborted|interrupted|cancell?ed"
                          r"|\[request interrupted by user[^\]]*\])\W*", re.I)


def ended_by_stop(msg):
    """Whether a turn's end (its result), coming after Esc or the stop button, is what stopping it says: Claude Code's
    error_during_execution, or its words (⚠ This operation was aborted, API Error: Request was aborted.), Codex's
    too. Not another error that came meanwhile (a sign-in's, a full context's): that one's still said."""
    return msg.get("subtype") == "error_during_execution" or bool(STOP_SAID_RE.fullmatch(msg.get("result") or ""))


def api_error_label(text):
    """What Claude Code says in place of a reply that didn't come ("API Error: …", or a synthetic message): in the
    colour a failed tool call's is (Adwaita's error), its text where a tool call's starts (api-error: theming.CSS), and
    "API Error" in bold."""
    m = re.match(r"(API Error)(.*)", text or "", re.S)
    markup = f"<b>{m.group(1)}</b>{html.escape(m.group(2))}" if m else html.escape(text or "")
    lbl = sel_label(markup, css="error")
    lbl.add_css_class("api-error")
    return lbl


def task_note_widget(note, card=None, on_jump=None):
    """A background task's ending whose card isn't in the chat (it started before what's drawn of it), as one quiet
    line: Bash · <its description> — finished (exit 0), with no mark before it; a failure all in the colour a failed
    tool call's border is. Never the raw command or XML. Its card drawn after all (an earlier part of the chat, drawn
    later): row.link_card(card, on_jump) names it as its card does, the description a link back to it (on_jump(card))."""
    status = note.get("status") or "completed"
    row = Gtk.Box(halign=Gtk.Align.START)
    row.add_css_class("task-note")
    summary = html.unescape(note.get("summary") or "")
    ending = chatparts.TASK_ENDINGS_SAID.get(status, status)
    if (code := re.search(r"exit code (\d+)", summary)) is not None:
        ending = wording.say("task_notes.ending_exit", ending=ending, code=code.group(1))
    label = Gtk.Label(xalign=0, wrap=True, selectable=True)
    label.add_css_class("error" if status in ("failed", "killed", "error") else "dim-label")
    label.set_tooltip_text(summary or None)

    def link_card(card, on_jump=None):
        inp = card.input if card is not None else {}
        if card is not None:
            what = card.base
        else:
            what = wording.say("common.agent") if note.get("task_type") == "local_agent" or \
                summary.startswith(("Agent", "Subagent")) else wording.say("task_notes.background_command")
        desc = inp.get("description") or note.get("description")
        if not desc:  # no description: the command's first line, or what the summary quotes
            quoted = re.match(r'[^"]*"(.*)"', summary, re.S)
            desc = (inp.get("command") or (quoted.group(1) if quoted else summary)).strip().splitlines()[0:1]
            desc = desc[0] if desc else wording.say("task_notes.unnamed")
        if len(desc) > 80:
            desc = desc[:79] + "…"
        text = html.escape(desc)
        if card is not None and on_jump is not None:
            text = f'<a href="card">{text}</a>'
            label.connect("activate-link", lambda *_: (on_jump(card), True)[1])
        label.set_markup(f"<b>{html.escape(what)}</b> · {text} — {html.escape(ending)}")
    link_card(card, on_jump)
    row.link_card = link_card
    row.append(label)
    return row


class StatusSlot(Gtk.Box):
    """A fixed 16px place for a status icon whose content changes (spinner -> ✓/✗).
    Rows keep this one widget: swapping prefixes in and out of an Adw row is unreliable."""

    def __init__(self, state="running"):
        super().__init__(valign=Gtk.Align.CENTER)
        self.state, self.icon = None, None
        self.set_state(state)

    def set_state(self, state):
        if state == self.state:
            return
        if self.icon is not None:
            self.remove(self.icon)
        self.state, self.icon = state, _status_icon(state)
        self.append(self.icon)


class ThinkingView(Gtk.Box):
    """Claude's narration between steps. Current models' thinking arrives summarized: a sentence
    or two ("The workspace and its 7 default apps are installing… I'll upgrade it once that's done"),
    which Claude Code and Claude Desktop show in the conversation, dimmed. That's what this is:
    visible text, not a folded "thinking…" (the model's raw reasoning comes as empty, signed
    blocks, which are never drawn)."""

    def __init__(self, text=""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("thinking")
        self.md = MarkdownView(text)
        Gtk.Box.append(self, self.md)  # (append() itself takes streamed text, like MarkdownView's)

    def append(self, delta):  # streaming deltas, like MarkdownView
        self.md.append(delta)

    def finish(self):
        self.md.finish()

    def has_text(self):
        return bool(self.md.text.strip())


# Claude Code's whole result for an edit or a write that went through: the path and that it worked, and maybe that
# the file is in its context. The card says all that already, in its name, its summary and its border. (Anything more,
# a note that the file changed on disk, say, isn't matched: it's shown.)
FILE_WRITTEN = re.compile(r"(?:The file (?:(?! \()[^\n])+ has been updated successfully\."
                          r"|File created successfully at: (?:(?! \()[^\n])+)"
                          r"(?:\s+\(file state is current in your context[^()\n]*\))?")
# (and a Skill's, that it started: the card's name and summary say which; what it was given is its Input)
SKILL_LAUNCHED = re.compile(r"Launching skill: [^\n]+")


def said_nothing(base, text, is_error):
    """Is a call's whole result only that it went through (an edit or write made, a skill started): no Output to
    open, since the card says that already?"""
    text = (text or "").strip()
    if is_error:
        return False
    return base in ("Edit", "MultiEdit", "Write") and bool(FILE_WRITTEN.fullmatch(text)) or \
        base == "Skill" and bool(SKILL_LAUNCHED.fullmatch(text))


class QuestionForm(Gtk.Box):
    """claude's AskUserQuestion: each question with its options (one picked, or several if it says so) and Other, to
    type an answer of your own. Answer sends {question: label(s), comma-joined} back; Skip declines them all."""

    def __init__(self, questions, on_answer, on_skip):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.picks = []  # per question: (question text, [(label, CheckButton)], Other's CheckButton, its Entry)
        for q in questions:
            if not isinstance(q, dict) or not q.get("question"):
                continue
            qbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            head = html.escape(str(q.get("header") or ""))
            qbox.append(sel_label((f"<span alpha='60%'>{head}</span>  " if head else "") +
                                  f"<b>{html.escape(str(q['question']))}</b>"))
            multi, group, opts = bool(q.get("multiSelect")), None, []
            for o in q.get("options") or []:
                o = o if isinstance(o, dict) else {"label": str(o)}
                label = str(o.get("label", ""))
                b = Gtk.CheckButton()
                text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                text.append(Gtk.Label(label=label, xalign=0, wrap=True))
                if o.get("description"):
                    d = Gtk.Label(label=str(o["description"]), xalign=0, wrap=True)
                    d.add_css_class("dim-label")
                    text.append(d)
                b.set_child(text)
                if not multi:
                    b.set_group(group)
                    group = group or b
                b.connect("toggled", lambda *_: self.sync())
                opts.append((label, b))
                qbox.append(b)
            other = Gtk.CheckButton(label=wording.say("common.other"))
            if not multi:
                other.set_group(group)
            entry = Gtk.Entry(placeholder_text=wording.say("asks.own_answer"), hexpand=True)
            entry.set_visible(False)
            other.connect("toggled", lambda b, e=entry: (e.set_visible(b.get_active()), b.get_active() and e.grab_focus(),
                                                         self.sync()))
            entry.connect("changed", lambda *_: self.sync())
            entry.connect("activate", lambda *_: self.send.get_sensitive() and self.send.emit("clicked"))
            qbox.append(other)
            qbox.append(entry)
            self.picks.append((str(q["question"]), opts, other, entry))
            self.append(qbox)
        row = Gtk.Box(spacing=6)
        self.send = Gtk.Button(label=wording.say("asks.answer"))
        self.send.add_css_class("suggested-action")
        self.send.connect("clicked", lambda *_: on_answer(self.answers()))
        skip = Gtk.Button(label=wording.say("asks.skip"))
        skip.connect("clicked", lambda *_: on_skip())
        row.append(self.send)
        row.append(skip)
        self.append(row)
        self.sync()

    def answers(self):
        out = {}
        for question, opts, other, entry in self.picks:
            chosen = [label for label, b in opts if b.get_active()]
            if other.get_active() and entry.get_text().strip():
                chosen.append(entry.get_text().strip())
            out[question] = ", ".join(chosen)
        return out

    def sync(self):
        """Answer only once every question has one."""
        self.send.set_sensitive(all(self.answers().values()))


class ToolCard(Gtk.Box):
    """One tool call. state: running -> done | error, or running -> background -> done | error
    when the CLI runs it as a background task (its result only says it started); running -> interrupted when its turn
    ends without its result (stopped with Esc)."""

    def __init__(self, name, tool_id):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("tool-card")
        TOOL_NAMES.add(name)  # (a tool this chat called: Signal can name it)
        self.name, self.tool_id, self.partial_json, self.input = name, tool_id, "", {}
        self.state, self.task, self.decision, self.listeners = "running", None, None, []
        self.artifact, self.asking, self.opened_to_ask = None, None, False
        self.answer = None  # (an answer_task call's, once it's taken: answer_result)
        # one line:  Name [Command] [Output]  summary (cut off with …)        [spinner, while it runs]
        # and under it, what those open: what was asked, what came back (once there is some)
        grid = self.grid = Gtk.Grid()
        self.line = Gtk.Box(spacing=8)
        self.status = None  # (_card_status)
        self.title = Gtk.Label(xalign=0)
        self.header = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True, selectable=True)
        self.header.set_can_focus(False)  # selectable for copying, but not a tab stop in every card
        self.image_acts = None  # (a Read of an image: its path in the header the link that opens it)
        self.header.connect("activate-link", lambda _l, uri: uri == IMAGE_LINK and (self.image_acts.launch() or True))
        toggles = self.toggles = Gtk.Box(spacing=4)
        for w in (self.title, toggles, self.header):
            self.line.append(w)
        grid.attach(self.line, 0, 0, 1, 1)
        self.append(grid)
        base = self.base = name.split("__")[-1]
        # (a Write's is the whole file it wrote, not a change: Input)
        self.input_toggle = Disclosure(toggles, grid, 1, wording.say("common.command") if base in ("Bash", "BashOutput", "PowerShell")
                                       else wording.say("common.prompt") if base in ("Task", "Agent")
                                       else wording.say("tool_calls.diff") if base in ("Edit", "MultiEdit") else wording.say("meta.input"),
                                       button=True)
        self.output_toggle = Disclosure(toggles, grid, 2, wording.say("meta.output"), button=True)
        self.output_toggle.set_visible(False)  # until there is output
        self.show_status()
        # what's folded away is built when first opened (a long session has thousands of these), and so are the
        # boxes it goes in: the input's, the output's (media_box, children, result_box: made when first used)
        self.input_box, self._media_box, self._output = None, None, None
        self.input_stale, self.pending_output = True, []
        self.input_toggle.on_expand.append(self.build_input)
        self.output_toggle.on_expand.append(self.build_output)
        self.set_input({}, whole=False)

    # ---- state
    def set_state(self, state):
        was, self.state = self.state, state
        self.show_status()
        if state in ("error", "interrupted"):  # (one stopped part way: red, as a failed one)
            self.add_css_class("error")
        elif was == "interrupted":  # (its result in a later part of the chat after all)
            self.remove_css_class("error")
        if state == "error":
            self.output_toggle.set_label(wording.say("meta.error"))
        if state == "interrupted" and not self.input_whole:  # (stopped before its input came in: nothing to open)
            self.input_toggle.set_visible(False)
            self.toggles.set_visible(self.output_toggle.get_visible())
        self.refresh_header()
        for fn in self.listeners:
            fn(self)

    def show_status(self):
        if self.status is not None:
            self.line.remove(self.status)
        self.status = _card_status(self.state) if self.asking is None else None  # (waiting on you: not running)
        if self.status is not None:
            self.line.append(self.status)

    def ask(self, buttons):
        """Asked whether to allow it, here in the card: what it would run, opened, and under it buttons (Allow, Deny…).
        It's waiting on you then, not running: no spinner, the border in the colour of waiting."""
        self.answered()  # (asked again before an answer: the earlier buttons go)
        self.asking = buttons
        self.add_css_class("asking")
        self.show_status()
        if self.input_toggle.get_visible() and not self.input_toggle.get_expanded() and self.base != "AskUserQuestion":
            self.opened_to_ask = True
            self.input_toggle.set_expanded(True)
        buttons.set_margin_top(8)
        self.grid.attach(buttons, 0, 4, 1, 1)
        for fn in self.listeners:
            fn(self)

    def answered(self):
        """The ask is answered (or can't be any more): the buttons go, and what was opened for it folds again."""
        buttons, self.asking = self.asking, None
        if buttons is None:
            return
        self.grid.remove(buttons)
        discard(buttons)
        self.remove_css_class("asking")
        if self.opened_to_ask:
            self.opened_to_ask = False
            self.input_toggle.set_expanded(False)
        self.show_status()
        for fn in self.listeners:
            fn(self)

    def set_decision(self, decision):
        """'allowed' / 'denied' / 'always allowed' from a permission prompt; only a denial is said in the header (an
        allowed call runs, then is done: that says it)."""
        self.decision = decision
        self.refresh_header()

    def refresh_header(self):
        s = summarize_tool(self.name, self.input)
        extra = []
        if self.state == "background":
            extra.append(wording.say("tool_calls.running_in_background"))
        elif self.state == "interrupted":
            extra.append(wording.say("meta.interrupted"))
        if self.decision == "denied":
            extra.append(wording.say("tool_calls.denied"))
        tail = "".join(f"  <span alpha='55%'>· {html.escape(e)}</span>" for e in extra)
        if self.answer is not None:  # (a task's ask answered: the task, and the answer as it was taken)
            s = answer_line({"answer": self.answer.get("values"), "text": self.answer.get("text")})
        title = wording.say("tool_calls.answered_task", task=self.answer["task"]) if self.answer is not None else \
            tool_label(self.name)
        if self.answer is not None:  # (a task's name, however long: cut, the chat no wider for it)
            self.title.set_ellipsize(Pango.EllipsizeMode.END)
            self.title.set_max_width_chars(40)
        self.title.set_markup(f"<b>{html.escape(title)}</b>")
        shown = html.escape(s)
        if self.image_acts is not None and s and s == _first_line(self.input.get("file_path") or ""):
            shown = image_link_markup(s, self.image_acts)
        elif self.base in ("Grep", "Glob") and s and s == _first_line(self.input.get("pattern") or ""):
            shown = f"<tt>{shown}</tt>"  # (a pattern, as it's written: in the code's letters)
        markup = f"{shown}{tail}" if shown else tail.lstrip()  # (nothing before it: no gap)
        self.header.set_markup(markup)
        # (a selectable, ellipsized label given markup once it's shown draws it without the markup's look, GTK 4.24,
        # till its style next changes: the look given again as the label's own attributes)
        # (Pango's markup has no links: the label draws its own)
        self.header.set_attributes(Pango.parse_markup(re.sub(r"</?a\b[^>]*>", "", markup), -1, "\0")[1])

    def show_output(self):
        self.toggles.set_visible(True)
        if getattr(self, "view_btn", None) is not None and self.state != "error":
            return  # (a subagent's: View opens it in the side panel, in place of an Output to fold open; an Error stays)
        self.output_toggle.set_visible(True)

    def set_input(self, inp, whole=True):
        """What it was given; whole: all of it (not yet, as a card's first drawn, before its input streams in)."""
        self.input = inp if isinstance(inp, dict) else {}
        self.input_whole = whole
        self.refresh_header()
        # a plain Read's only input is the path, which the header already shows
        redundant = self.name == "Read" and not (self.input.get("offset") or self.input.get("limit") or
                                                 self.input.get("pages"))
        self.input_toggle.set_visible(not redundant)
        # (no buttons, as an image's Read: nor their place, so the summary is a space after the name)
        self.toggles.set_visible(not redundant or self.output_toggle.get_visible())
        self.input_stale = True
        if self.input_toggle.get_expanded():
            self.build_input()
        for fn in self.listeners:
            fn(self)

    @property
    def media_box(self):
        """Images the tool returned, right under the header (an image Read is the image)."""
        if self._media_box is None:
            self._media_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False,
                                      margin_top=6, margin_bottom=2)
            self.grid.attach(self._media_box, 0, 3, 1, 1)
        return self._media_box

    def output_boxes(self):
        """(children, result_box): subagent messages land in the first when there's no side-panel view for them."""
        if self._output is None:
            out = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=2)
            self._output = (Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4),
                            Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6))
            out.append(self._output[0])
            out.append(self._output[1])
            self.output_toggle.set_child(out)
        return self._output

    children = property(lambda self: self.output_boxes()[0])
    result_box = property(lambda self: self.output_boxes()[1])

    def build_input(self):
        if not self.input_stale:
            return
        self.input_stale = False
        if self.input_box is None:
            self.input_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=6, margin_bottom=2)
            self.input_toggle.set_child(self.input_box)
        if (c := self.input_box.get_first_child()) is not None:
            self.input_box.remove(c)
            discard(c)
        self.input_box.append(tool_input_view(self.name, self.input))

    def build_output(self):
        pending, self.pending_output = self.pending_output, []
        read = self.input.get("file_path") if self.base == "Read" else None  # (a file's lines: its code, numbered)
        for shown, lang in pending:
            self.result_box.append(CodeBlock(shown, lang, path=read or "", numbered=bool(read)))

    def add_partial_json(self, chunk):
        self.partial_json += chunk
        try:
            self.set_input(json.loads(self.partial_json))
        except json.JSONDecodeError:
            pass

    def add_output_text(self, text, lang=""):
        # it scrolls inside its own pane now, so keep much more (a label this size is still cheap)
        shown = text if len(text) < 200_000 else text[:200_000] + "\n" + wording.say("tool_calls.cut_chars", n=len(text))
        self.pending_output.append((shown, lang))
        if self.output_toggle.get_expanded():
            self.build_output()
        self.show_output()

    def set_result(self, content, is_error):
        if self.asking is not None:  # (it ended without an answer, as a turn stopped: nothing to answer now)
            self.answered()
        texts, images = [], []
        for c in content if isinstance(content, list) else [content]:
            if isinstance(c, dict) and c.get("type") == "image" and c.get("source", {}).get("data"):
                images.append(base64.b64decode(c["source"]["data"]))
            elif isinstance(c, dict):
                texts.append(c.get("text", ""))
            elif c:
                texts.append(str(c))
        text = "\n".join(t for t in texts if t)
        if self.task is not None and (m := re.search(r"Output is being written to: (\S+)", text)):
            self.task.setdefault("output_file", m.group(1).rstrip("."))  # live output before it ends
        if text and self.base in ("Agent", "Task") and not text.startswith("Async agent launched"):
            if text.strip() != getattr(self, "report", None):  # (its task's ending may have shown it)
                self.report = text.strip()
                self.result_box.append(MarkdownView(text))  # a finished agent's answer is markdown
                self.show_output()
        elif self.name == ANSWER_TASK and not is_error and (got := answer_result(text)) is not None:
            self.answer = got  # (it went through: the card says what was answered, no Output to open)
        elif text and not said_nothing(self.base, text, is_error):  # (it went through: no Output to open)
            self.add_output_text(text)
        for data in images:
            try:
                path = self.input.get("file_path") if self.base == "Read" else None
                image = image_widget(data, path=path)
                self.media_box.append(image)
                if path and self.image_acts is None:
                    self.image_acts = image.image_actions
            except GLib.Error:
                self.media_box.append(sel_label(wording.say("tool_calls.image_not_decoded")))
        self.artifact = artifact_from_tool(self.input, text) if self.base == "Artifact" else None
        if self.artifact:
            ARTIFACT_TITLES[self.artifact["url"]] = self.artifact["title"]  # a publish is authoritative
            version = f"  <span alpha='55%'>v{self.artifact['version']}</span>" if self.artifact.get("version") else ""
            line = sel_label("Published " + link_markup(self.artifact["url"]) + version)
            line.add_css_class("artifact-line")
            self.media_box.append(line)
        if images or self.artifact or self._media_box is not None:
            self.media_box.set_visible(bool(images) or bool(self.artifact))
        # a backgrounded call's result only says it started: keep spinning until the task ends
        running_in_bg = self.task is not None and self.task.get("status", "running") == "running"
        self.set_state("error" if is_error else "background" if running_in_bg else "done")

    def bind_task(self, task):
        """The CLI turned this call into a background task (a command or a subagent)."""
        self.task = task
        if self.state == "done":  # the 'started' result came first
            self.set_state("background")

    def finish_task(self, task):
        """The background task ended: final status, summary and (for commands) its output."""
        self.task = task
        status = task.get("status")
        if task.get("summary") and task.get("task_type") != "local_bash" \
                and task["summary"].strip() != getattr(self, "report", None):
            self.report = task["summary"].strip()
            self.result_box.append(MarkdownView(task["summary"]))  # a subagent's report is markdown
            self.show_output()
        out = read_tail(task.get("output_file")) if task.get("task_type") == "local_bash" else ""
        if out:
            self.add_output_text(out, "output")
        elif task.get("task_type") == "local_bash" and task.get("summary"):
            self.add_output_text(task["summary"], status or "")
        self.set_state("done" if status == "completed" else "error")

    def has_media(self):
        return self._media_box is not None and self._media_box.get_visible()


def read_tail(path, limit=6000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            data = f.read().decode("utf-8", "replace")
        return ("… " if size > limit else "") + data
    except (OSError, TypeError):
        return ""


class ToolGroup(Gtk.Grid):
    """Consecutive tool calls folded into one line, › 5 tool calls, 1 failed (a spinner after it while one runs);
    opened, the calls under it, a line each, their boxes' left edge where the chat's text starts."""

    def __init__(self):
        super().__init__()  # (no row gap: folded, nothing under its line)
        self.add_css_class("tool-group")
        self.state, self.status = "running", None
        self.row = Gtk.Box(spacing=8)
        self.attach(self.row, 0, 0, 1, 1)
        self.toggle = Disclosure(self.row, self, 1, "")
        self.toggle.text.remove_css_class("dim-label")
        self.items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        self.toggle.set_child(self.items)

    def add_item(self, widget):
        self.items.append(widget)
        if isinstance(widget, ToolCard):
            widget.listeners.append(lambda *_: self.refresh())
        self.refresh()

    def cards(self):
        out, child = [], self.items.get_first_child()
        while child is not None:
            if isinstance(child, ToolCard):
                out.append(child)
            child = child.get_next_sibling()
        return out

    def refresh(self):
        cards = self.cards()
        failed = sum(c.state == "error" for c in cards)
        cut = sum(c.state == "interrupted" for c in cards)
        self.toggle.set_label(wording.say("tool_calls.count", n=len(cards))
                              + (wording.say("tool_calls.failed", n=failed) if failed else "")
                              + (wording.say("tool_calls.interrupted", n=cut) if cut else ""))
        states = {c.state for c in cards if c.asking is None}  # (one waiting to be allowed isn't running)
        state = "running" if states & {"running", "background"} else "error" if "error" in states else \
            "interrupted" if "interrupted" in states and len(states) == 1 else "done"
        if state != self.state or self.status is None and state == "running":
            self.state = state
            if self.status is not None:
                self.row.remove(self.status)
            self.status = _status_icon(state) if state == "running" else None  # (a spinner says something; a tick doesn't)
            if self.status is not None:
                self.row.append(self.status)
        if any(c.has_media() or c.asking is not None for c in cards) and not self.toggle.get_expanded():
            self.toggle.set_expanded(True)  # images are the point, and a call asking you is waiting on you: shown


class ProcessRow(Gtk.ListBoxRow):
    """A background process's row in the side panel, as a subagent's is (as wide, as tall: its line an ActionRow):
    its status, what it is (Claude's words for it, else its command's first line, highlighted), stop while it runs;
    its command and its output's tail (inside) open under its line (toggle: ⌄, − to close it again, or a click on the
    line) or fill the page (full: SidePanel.open_process, back out of it), as a side question's do. Not on the darker
    inside (that's a chat's): under its line on the row's own fill, filling the page on the panel's."""

    def __init__(self, panel, tid):
        super().__init__(activatable=False)
        self.add_css_class("process-row")
        self.task_id = tid
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(rows)
        self.head = Adw.ActionRow(title_lines=1, subtitle_lines=1)
        self.status = StatusSlot()
        self.head.add_prefix(self.status)
        self.stop = Gtk.Button(icon_name="media-playback-stop-symbolic",
                               tooltip_text=wording.say("side_panel.stop_process"),
                               valign=Gtk.Align.CENTER)
        self.stop.add_css_class("flat")
        self.stop.connect("clicked", lambda *_: panel.win.stop_task(tid))
        self.toggle = Gtk.Button(valign=Gtk.Align.CENTER)
        self.toggle.add_css_class("flat")
        self.toggle.connect("clicked", lambda *_: self.set_expanded(not self.expanded()))
        self.full = Gtk.Button(icon_name="window-maximize-symbolic", valign=Gtk.Align.CENTER,
                               tooltip_text=wording.say("side_panel.open_process"))
        self.full.add_css_class("flat")
        self.full.add_css_class("side-question-full")  # (the size of a side question's)
        self.full.connect("clicked", lambda *_: panel.open_process(self))
        for w in (self.stop, self.toggle, self.full):
            self.head.add_suffix(w)
        click = Gtk.GestureClick()  # (the line, not its buttons: opened or closed under it, as it was)
        click.connect("released", lambda _g, n, x, y: n == 1 and self._clicked(x, y))  # (a double-click: once)
        self.head.add_controller(click)
        self.title, self.subtitle = (next(w for w in walk(self.head) if isinstance(w, Gtk.Label) and w.has_css_class(c))
                                     for c in ("title", "subtitle"))
        rows.append(self.head)
        # its command and its output: under its line, or the page's whole
        self.inside = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12, margin_end=12,
                            margin_top=12, margin_bottom=12)
        self.body.add_css_class("zoomable")
        self.command = None
        self.output = CodeBlock(wording.say("side_panel.no_output_yet"), "output")
        self.body.append(self.output)
        self.scroll = natural_height(Gtk.ScrolledWindow(child=self.body, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.inside.append(self.scroll)
        # (the row's fill under its line is the revealer's, so what goes to the page, and the square ExpandStack grows
        # for it, are on the panel's own, there and back)
        self.reveal = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.reveal.add_css_class("process-body")
        rows.append(self.reveal)
        self.put_inside_row()
        self.set_expanded(False)

    def _clicked(self, x, y):
        hit = self.head.pick(x, y, Gtk.PickFlags.DEFAULT)
        if hit is None or not (isinstance(hit, Gtk.Button) or hit.get_ancestor(Gtk.Button)):
            self.set_expanded(not self.expanded())

    def set_command(self, cmd):
        """Its command, over its output, highlighted as the shell's (once: a process's command doesn't change)."""
        if cmd and self.command is None:
            self.command = CodeBlock(cmd, "bash", syntax="bash")
            self.body.prepend(self.command)

    def get_subtitle(self):
        return self.head.get_subtitle()

    def expanded(self):
        return self.reveal.get_reveal_child()

    def opened(self):
        """Its command and output open under its line, all the way (not opening or closing there)."""
        return self.reveal.get_reveal_child() and self.reveal.get_child_revealed()

    def set_expanded(self, on):
        self.reveal.set_reveal_child(on)
        self.toggle.set_icon_name("list-remove-symbolic" if on else "pan-down-symbolic")
        self.toggle.set_tooltip_text(wording.say("side_panel.hide_output" if on else "side_panel.show_output"))

    def put_inside_row(self):
        """Its command and output back under its line (from the whole page), as tall as they are."""
        if (parent := self.inside.get_parent()) is not None:
            parent.remove(self.inside)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_vexpand(False)
        self.inside.set_vexpand(False)
        self.inside.set_margin_end(0)
        self.reveal.set_child(self.inside)

    def take_inside(self):
        """Its command and output, for the whole page: scrolled there."""
        self.reveal.set_child(None)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_propagate_natural_height(False)
        self.scroll.set_vexpand(True)
        self.inside.set_vexpand(True)
        self.inside.set_margin_end(widgets.INSIDE_GAP)  # (its right edge the list's)
        return self.inside


def paint_command(label, text):
    """label's text (text: a shell command, as it's drawn) in the code's colours, light or dark as the theme is, as a
    CodeBlock has them (Pygments loaded off the main thread the first time, as there)."""
    label.command_text = text
    ref = weakref.ref(label)

    def done(spans):
        if (lbl := ref()) is not None and getattr(lbl, "command_text", None) == text:
            lbl.command_spans = (text, spans)
            lbl.set_attributes(codespans.code_attrs(text, spans, Adw.StyleManager.get_default().get_dark())
                               if spans else None)
        return False
    if markdownview._pygments_loaded():
        done(codespans.code_spans(text, lang="bash"))
    else:
        IMAGE_POOL.submit(lambda: GLib.idle_add(done, codespans.code_spans(text, lang="bash")))


def unpaint_command(label):
    label.command_text = None
    label.set_attributes(None)
