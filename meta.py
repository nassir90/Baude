"""meta — Meta, the helper in Baude's and Record's right bar: one agent that operates the app it's in.

Meta is a Claude Code session (`claude -p` speaking stream-json, baudelib's ClaudeProc, as Baude runs its chats), on
Haiku unless Settings → Meta says another. It reads with Claude Code's own Read, Grep and Glob, and acts with the app's
tools, which come in on a Unix socket of the app's (ToolSocket) through a relay Claude Code runs as an MCP server (RELAY):

  * it knows the app through a light index (MetaChat.index_text), a line or two per part with short ids, sent with a
    session's first message, and opens a part in full with app_index;
  * set_setting changes a setting at once, and the row Settings has for it shows under the reply, for the user to go on
    adjusting (as NoonPad's Copilot puts a slider under the corner radius it chose); Meta's told what they settle on;
  * show puts UI under the reply: a setting's row, rows of chats or pages to click, the changelog, a button;
  * every change it makes is an action in a journal (MetaChat.actions): under the reply it made it in, with undo and
    redo, kept across restarts; a change to what the left bar lists (a chat, a page) is marked there, green added, red
    removed, blue moved, until it's kept or undone (mark_row);
  * in code mode (Settings → Meta) its tools are one, run_python: up to CODE_LINES lines of Python calling them as
    app.<tool>(…) (run_code), each call journaled as the tool's own is.

Each app makes its column a MetaChat of its own: its tools (app_tools), what they do (app_handlers), its part of the
index (sections), and how its actions are undone (apply_app). Nothing here is Baude's or Record's own."""
import ast
import html
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

import fontsetup  # noqa: E402
import lookup  # noqa: E402
import update  # noqa: E402
import widgets  # noqa: E402
from baudelib.claude import MODELS, ClaudeProc  # noqa: E402

NAME = "Meta"
MODEL = "haiku"  # its model, unless Settings → Meta → Intelligence says another (settings.json meta_model)
MODELS_OFFERED = list(reversed(MODELS))  # ((value, label): what Intelligence offers, the quickest first)
CODE_LINES = 10  # the most lines of Python run_python runs
READ_TOOLS = ("Read", "Grep", "Glob")  # Claude Code's own: those that only read
# its session's environment: its MCP tools loaded at the start (not through a ToolSearch turn), no auto memory, no
# claude.ai connectors, no telemetry or update checks (as Baude's Signal reader has it)
AGENT_ENV = {"ENABLE_TOOL_SEARCH": "false", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
             "ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
REF_LIMIT = 20000  # characters of a referenced page's, file's or chat's text put in with a message (past it: where to Read)
EARLIER_LIMIT = 16000  # of what was said before a session started, with its first message
REF_RE = re.compile(r"(?<![\w@])@(?:\{([^}\n]+)\}|([\w][\w./:\-]*[\w/]|\w))")  # (@name, @{a name})
REF_COLOR = "#2f8f72"  # (an @name: the notes' colour for one)
MARK_HOWS = ("added", "removed", "moved", "highlight")


def model():
    """The model Meta runs on: settings.json's meta_model, else Haiku."""
    chosen = widgets.load_settings().get("meta_model")
    return chosen if chosen in dict(MODELS) else MODEL


def code_mode():
    """Whether Meta's tools are run_python alone (Settings → Meta → Code mode)."""
    return bool(widgets.load_settings().get("meta_code_mode"))


# ---------------------------------------------------------------- text

def label(text="", css=(), wrap=True, select=False, xalign=0.0, markup=False):
    lb = (widgets.WrapLabel if wrap else Gtk.Label)(xalign=xalign, wrap=wrap, selectable=select)
    if wrap:
        lb.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if markup:
        lb.set_markup(text)
    else:
        lb.set_text(text)
    for c in css:
        lb.add_css_class(c)
    return lb


def _name_link(name, shown):
    return f'<a href="ref:{urllib.parse.quote(name, safe="")}">{shown}</a>'


def _inline(s, names=()):
    """A line's inline markdown as Pango markup, the names (from SearchSpace.mentions) linked as ref:name."""
    out = []
    for part in re.split(r"(`[^`\n]+`)", s):
        if len(part) > 1 and part.startswith("`") and part.endswith("`"):
            code = '<span font_family="monospace" size="' + widgets.code_markup_size() + '" background="#8882">' + \
                html.escape(part[1:-1], False) + "</span>"
            name = next((n for n in (part[1:-1], lookup.ref_key(part[1:-1])) if n in names), None)
            out.append(_name_link(name, code) if name else code)
            continue
        found = []  # (the names in prose, kept out of the formatting as \0n\0, put back linked)
        if names:
            keep = lambda m: m[0] if m[0] not in names else (found.append(m[0]) or f"\0{len(found) - 1}\0")  # noqa: E731
            part = lookup._CATALOGUE_TOKEN.sub(keep, lookup.GITHUB_NAME.sub(keep, part))
        t = html.escape(part, False)
        t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                   lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])", r"<i>\1</i>", t)
        t = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<i>\1</i>", t)
        t = re.sub(r"\0(\d+)\0", lambda m: _name_link(found[int(m[1])], html.escape(found[int(m[1])], False)), t)
        out.append(t)
    return "".join(out)


def md_markup(text, names=()):
    """Markdown as Pango markup: headings, lists, quotes, code (a block, monospace), rules; names linked."""
    lines, out, code = text.split("\n"), [], None
    mono = lambda t: '<span font_family="monospace" size="' + widgets.code_markup_size() + '">' + \
        html.escape(t, False) + "</span>"  # noqa: E731
    for line in lines:
        if line.lstrip().startswith("```"):
            if code is None:
                code = []
            else:
                out.append(mono("\n".join(code)))
                code = None
            continue
        if code is not None:
            code.append(line)
            continue
        m = re.match(r"(#{1,6})\s+(.*)", line)
        if m:
            size = "x-large" if len(m.group(1)) == 1 else "large" if len(m.group(1)) == 2 else "medium"
            out.append(f'<span size="{size}" weight="bold">{_inline(m.group(2), names)}</span>')
        elif re.match(r"\s*([-*+])\s+", line):
            ind = len(line) - len(line.lstrip())
            out.append("   " * (ind // 2) + " •  " + _inline(re.sub(r"^\s*[-*+]\s+", "", line), names))
        elif line.lstrip().startswith("|"):
            out.append(mono(line))
        elif line.startswith(">"):
            out.append("<i>" + _inline(line.lstrip("> "), names) + "</i>")
        elif re.fullmatch(r"\s*([-*_])\1{2,}\s*", line):
            out.append("―――")
        else:
            out.append(_inline(line, names))
    if code is not None:
        out.append(mono("\n".join(code)))
    return "\n".join(out)


def set_md(lb, text, names=()):
    markup = md_markup(text, names)
    try:  # (checked as Pango has it: <a> is the label's own, which Pango doesn't know)
        Pango.parse_markup(re.sub(r"</?a\b[^>]*>", "", markup), -1, "\0")
        lb.set_markup(markup)
    except GLib.Error:
        lb.set_text(text)


def plain_ref(surface):
    """A name as plain @ has it: @name, or @{name} when it has characters a bare one can't."""
    return "@" + (surface if re.fullmatch(r"[\w][\w./:\-]*[\w/]|\w", surface) else "{" + surface + "}")


def attr(value):
    return html.escape(str(value), quote=True)


def trimmed(text, limit, where=None):
    """text, or its start as far as limit and what's left said, with where to read the rest."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n[… {len(text) - limit} more characters" + (f": Read {where}" if where else "") + "]"


def said_value(value):
    """A setting's value as the index and the replies say it: on/off, a number as Settings shows it, a name."""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return widgets.fmt_scale(value)
    return str(value) if re.fullmatch(r"[\w.\-]+", str(value)) else json.dumps(str(value))


def transcript_text(path, limit=REF_LIMIT):
    """A Claude Code chat's transcript as text: who said what, each tool call a line; its end, as far as limit."""
    lines = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                if e.get("type") not in ("user", "assistant") or e.get("isSidechain") or e.get("isMeta"):
                    continue
                content, parts = (e.get("message") or {}).get("content"), []
                for c in [{"type": "text", "text": content}] if isinstance(content, str) else content or []:
                    if c.get("type") == "text" and (c.get("text") or "").strip():
                        parts.append(c["text"].strip())
                    elif c.get("type") == "tool_use":
                        inp = json.dumps(c.get("input") or {}, ensure_ascii=False)
                        parts.append(f"[{c.get('name')} {inp[:200]}]")
                if parts:
                    lines.append(("User: " if e["type"] == "user" else "Claude: ") + "\n".join(parts))
    except OSError as e:
        return f"(not read: {e})"
    text = "\n\n".join(lines)
    return text if len(text) <= limit else f"[… its first {len(text) - limit} characters not put here: Read {path}]\n" + \
        text[-limit:]


def earlier_said(messages, limit=EARLIER_LIMIT):
    """What was said before this session (a new one: the app started again, another model), the newest as far back
    as limit."""
    lines, size = [], 0
    for m in reversed([m for m in messages if not m.get("error")]):
        who = "User" if m.get("role") == "user" else NAME
        body = m.get("text") or ""
        if m.get("quote"):
            body = f"[quoting] {m['quote'][:1500]} [/quoting]\n" + body
        for p in m.get("proposals") or ():
            body += f"\n(an edit to {p['page']}: {p.get('status', 'pending')})"
        size += len(body)
        if size > limit and lines:
            break
        lines.append(f"{who}: {body}")
    return "<earlier_conversation>\n" + "\n\n".join(reversed(lines)) + "\n</earlier_conversation>" if lines else ""


# ---------------------------------------------------------------- what's running

def process_rows():
    """This process and those it started, and theirs (ps): [{pid, parent, memory_mb, cpu, up, command}], this one
    first. Empty if ps can't be run."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss=,pcpu=,etime=,command="], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows, children = {}, {}
    for line in out.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        pid, ppid = int(parts[0]), int(parts[1])
        rows[pid] = {"pid": pid, "parent": ppid, "memory_mb": round(int(parts[2]) / 1024) if parts[2].isdigit() else 0,
                     "cpu": parts[3], "up": parts[4], "command": parts[5][:160]}
        children.setdefault(ppid, []).append(pid)
    me, found, todo = os.getpid(), [], [os.getpid()]
    while todo:
        pid = todo.pop(0)
        if pid in rows:
            found.append(rows[pid])
        todo += [c for c in children.get(pid, ()) if c != pid]
    return [r for r in found if r["pid"] == me] + [r for r in found if r["pid"] != me]


def process_said(row):
    """A process as a line of the index's: what it is, by its command, briefly."""
    cmd = row["command"]
    words = cmd.split()
    name = os.path.basename(words[0]) if words else "?"
    if name.startswith("python") and len(words) > 1:
        name = os.path.basename(next((w for w in words[1:] if not w.startswith("-")), words[1]))
    return f"{row['pid']} {name} · {row['memory_mb']} MB · {row['cpu']}% · up {row['up']}"


# ---------------------------------------------------------------- code mode
# run_python's code is checked here (check_code: a few statements; nothing but app's tools, plain builtins and the
# plain methods of strings, lists, dicts and sets), then run in a Python of its own (run_isolated: PYTHON_RUNNER), so
# nothing it does can stop the app or reach into it: limited in time, memory and what it may write, on macOS in a
# sandbox (no files written, no processes, no network but the app's socket). Its calls of app's tools come back on
# the ToolSocket, as Meta's own do, each on run_python's card.

CODE_BUILTINS = ("abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int", "isinstance", "len",
                 "list", "map", "max", "min", "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple",
                 "zip", "Exception", "ValueError", "KeyError", "RuntimeError")
# the attributes code may use, but app's tools: the plain methods of strings, lists, dicts and sets
SAFE_ATTRS = frozenset((
    "get", "items", "keys", "values", "append", "extend", "insert", "pop", "remove", "sort", "index", "count", "copy",
    "update", "setdefault", "add", "discard", "union", "intersection", "difference", "issubset", "issuperset",
    "lower", "upper", "strip", "lstrip", "rstrip", "split", "rsplit", "splitlines", "join", "replace", "startswith",
    "endswith", "find", "rfind", "title", "capitalize", "casefold", "isdigit", "isalpha", "isalnum", "isspace",
    "partition", "rpartition", "zfill", "center", "ljust", "rjust", "removeprefix", "removesuffix"))
CODE_BANNED = {"exec", "eval", "open", "compile", "globals", "locals", "vars", "getattr", "setattr", "delattr",
               "input", "breakpoint", "help", "type", "object", "super", "memoryview", "classmethod",
               "staticmethod", "property", "dir", "id", "hasattr"}
CODE_SECONDS = 10  # how long run_python's code runs before it's stopped
SANDBOX = "/usr/bin/sandbox-exec"  # (macOS's: what the code's Python may do, PYTHON_SANDBOX)
PYTHON_SANDBOX = ('(version 1)(allow default)(deny file-write* (subpath "/Users") (subpath "/private") '
                  '(subpath "/tmp") (subpath "/Volumes") (subpath "/Library") (subpath "/Applications"))'
                  '(deny process-fork)(deny network-outbound (remote ip))(deny network-inbound)')

# what runs the code, in a Python of its own (python -I -c): its limits set, app made of the tools it's given (a call
# each: the app's socket, as the relay calls), the code run with the plain builtins, what it printed and its last
# expression's value written out
PYTHON_RUNNER = r'''import ast, builtins, json, socket, sys
try:
    import resource
    for limit, value in ((resource.RLIMIT_CPU, int(sys.argv[4])), (resource.RLIMIT_FSIZE, 0),
                         (resource.RLIMIT_AS, 2 << 30)):
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            pass
except ImportError:
    pass
sock, token, spec = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
code = sys.stdin.read()


def call(tool, args):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock)
    s.sendall((json.dumps({"tool": tool, "args": args, "agent": token, "nested": True}) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        chunk = s.recv(1 << 16)
        if not chunk:
            break
        data += chunk
    s.close()
    return json.loads(data or b"{}")


class App:
    pass


app = App()
for name, props in spec["tools"].items():
    def fn(*args, _name=name, _props=props, **kw):
        if len(args) > len(_props):
            raise TypeError(f"{_name} takes at most {len(_props)} arguments")
        res = call(_name, {**dict(zip(_props, args)), **kw})
        if res.get("error"):
            raise RuntimeError(res["error"])
        return res["data"] if "data" in res else res.get("text")
    setattr(app, name, fn)
printed = []


def say(*args, sep=" ", end="\n"):
    printed.append(sep.join(map(str, args)) + end)


env = {"__builtins__": {**{n: getattr(builtins, n) for n in spec["builtins"]}, "print": say}, "app": app}
tree = ast.parse(code, "<meta>", "exec")
last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
value = None
try:
    exec(compile(tree, "<meta>", "exec"), env)
    if last is not None:
        value = eval(compile(ast.Expression(last.value), "<meta>", "eval"), env)
except Exception as e:
    printed.append(f"{type(e).__name__}: {e}\n")
out = "".join(printed)
if value is not None:
    out += value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str, indent=1)
sys.stdout.write(out.strip() or "(nothing printed)")
'''


def check_code(code, tools):
    """The code run_python's given, parsed, if it's code it may run: at most CODE_LINES lines and as many statements;
    no imports, global or nonlocal, classes, generators or async; no name starting with _ nor any of the builtins
    that reach past app (CODE_BANNED); and of attributes only app's tools (tools: their names) and the plain
    methods of strings, lists, dicts and sets (SAFE_ATTRS): none of an object's insides. Raises ValueError, saying why
    not."""
    lines = [line for line in code.splitlines() if line.strip()]
    if len(lines) > CODE_LINES:
        raise ValueError(f"That's {len(lines)} lines: run_python runs at most {CODE_LINES}.")
    try:
        tree = ast.parse(code, "<meta>", "exec")
    except SyntaxError as e:
        raise ValueError(f"SyntaxError: {e.msg} (line {e.lineno})")
    if (n := sum(isinstance(node, ast.stmt) for node in ast.walk(tree))) > CODE_LINES:
        raise ValueError(f"That's {n} statements: run_python runs at most {CODE_LINES}.")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef, ast.AsyncFunctionDef,
                             ast.Await, ast.AsyncFor, ast.AsyncWith, ast.Yield, ast.YieldFrom)):
            raise ValueError(f"{type(node).__name__} isn't allowed: only app's functions and plain Python.")
        if isinstance(node, ast.Name) and (node.id.startswith("_") or node.id in CODE_BANNED):
            raise ValueError(f"{node.id} isn't allowed.")
        if isinstance(node, ast.Attribute):
            on_app = isinstance(node.value, ast.Name) and node.value.id == "app"
            if on_app and node.attr not in tools or not on_app and node.attr not in SAFE_ATTRS:
                raise ValueError(f".{node.attr} isn't allowed: app's tools and strings', lists', dicts' and sets' "
                                 "plain methods are.")
    return tree


def run_isolated(code, tools, sock, token):
    """Code check_code let by, run in a Python of its own (PYTHON_RUNNER; on macOS in PYTHON_SANDBOX), app its tools
    (tools: {name: its arguments in order}), calling them on sock as agent token: what it printed and its last
    expression's value, as text. Stopped after CODE_SECONDS."""
    spec = json.dumps({"tools": tools, "builtins": list(CODE_BUILTINS)})
    argv = [sys.executable, "-I", "-c", PYTHON_RUNNER, sock, token, spec, str(CODE_SECONDS)]
    if sys.platform == "darwin" and os.access(SANDBOX, os.X_OK):
        argv = [SANDBOX, "-p", PYTHON_SANDBOX, *argv]
    try:
        p = subprocess.run(argv, input=code, capture_output=True, text=True, timeout=CODE_SECONDS + 2,
                           cwd=tempfile.gettempdir(), env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    except subprocess.TimeoutExpired:
        return f"TimeoutError: stopped after {CODE_SECONDS} s"
    except OSError as e:
        return f"OSError: {e}"
    out = p.stdout.strip()
    if p.returncode < 0 and not out:
        out = f"Stopped: it ran past {CODE_SECONDS} s, or used too much memory."
    elif p.returncode and not out:
        out = (p.stderr.strip().splitlines() or [f"Its Python ended with {p.returncode}."])[-1]
    return trimmed(out or "(nothing printed)", 6000)


def api_doc(tools):
    """app's functions, a line each: its arguments (an optional one = None) and the first sentence of what it does."""
    lines = []
    for t in tools:
        schema = t.get("inputSchema") or {}
        need = set(schema.get("required") or ())
        args = ", ".join(p if p in need else f"{p}=None" for p in schema.get("properties") or {})
        first = re.split(r"(?<=\.)\s", t["description"], maxsplit=1)[0]
        lines.append(f"app.{t['name']}({args}): {first}")
    return "\n".join(lines)


# ---------------------------------------------------------------- settings

class Setting:
    """A setting Meta changes (set_setting) and shows (show setting:<name>): its value (get), changing it (put), the
    row Settings has for it (row), and what it takes: values a list of names, (low, high) for a size, or bool. note:
    what to know of it (the code font takes from the next start)."""

    def __init__(self, name, title, get, put, row, values, note="", meta_sets=True):
        self.name, self.title, self.get, self.put, self.row, self.values, self.note = \
            name, title, get, put, row, values, note
        self.meta_sets = meta_sets  # (False: Meta only shows it, the user changes it)

    def value(self):
        return self.get()

    def coerce(self, value):
        """value as the setting takes it, or ValueError saying what it takes."""
        if self.values is bool:
            if isinstance(value, bool):
                return value
            if str(value).strip().lower() in ("on", "true", "yes", "1"):
                return True
            if str(value).strip().lower() in ("off", "false", "no", "0"):
                return False
            raise ValueError(f"{self.name} is on or off.")
        if isinstance(self.values, tuple):
            try:
                v = round(float(value), 2)
            except (TypeError, ValueError):
                raise ValueError(f"{self.name} is a number from {self.values[0]} to {self.values[1]}.")
            if not self.values[0] <= v <= self.values[1]:
                raise ValueError(f"{self.name} is a number from {self.values[0]} to {self.values[1]}.")
            return v
        found = next((v for v in self.values if str(v).lower() == str(value).strip().lower()), None)
        if found is None:
            raise ValueError(f"{self.name} is one of: {', '.join(map(str, self.values))}.")
        return found

    def set(self, value):
        v = self.coerce(value)
        self.put(v)
        return v

    def takes(self):
        if self.values is bool:
            return "on|off"
        if isinstance(self.values, tuple):
            return f"{self.values[0]}–{self.values[1]}"
        return "|".join(map(str, self.values))


def model_row(chat):
    """Settings → Meta → Intelligence: the model Meta thinks with, from its next message."""
    values = [value for value, _label in MODELS_OFFERED]
    row = Adw.ComboRow(title="Intelligence", subtitle=f"The model {NAME} thinks with",
                       model=Gtk.StringList.new([lbl for _value, lbl in MODELS_OFFERED]),
                       selected=values.index(model()) if model() in values else 0)
    row.connect("notify::selected", lambda r, _p: (widgets.save_settings(meta_model=values[r.get_selected()]),
                                                   chat is not None and chat.model_changed()))
    return row


def code_mode_row(chat):
    """Settings → Meta → Code mode: Meta acts by writing a few lines of Python (run_python), from its next message."""
    row = Adw.SwitchRow(title="Code mode", subtitle=f"{NAME} acts by writing up to {CODE_LINES} lines of Python",
                        active=code_mode())
    row.connect("notify::active", lambda r, _p: (widgets.save_settings(meta_code_mode=r.get_active()),
                                                 chat is not None and chat.model_changed()))
    return row


def settings_group(chat):
    """Settings → Meta, in Baude's and Record's Settings: Intelligence and Code mode."""
    group = Adw.PreferencesGroup(title=NAME)
    group.add(model_row(chat))
    group.add(code_mode_row(chat))
    return group


def shared_settings(win, chat, app, zoom_default):
    """The settings Baude and Record share (settings.json's), as Meta changes them: win's set_scale, set_zoom_default
    and its fullwidth action; app the one it's in ("Baude", "Record"); zoom_default() the Global Scale."""
    display = win.get_display()

    def accent(v):
        widgets.save_settings(accent=None if v == "system" else v)
        widgets.apply_accent(display)

    def wide(v):
        if (action := win.lookup_action("fullwidth")) is not None:
            action.change_state(GLib.Variant.new_boolean(v))

    def auto(v):
        if update.upstream() is None:
            raise ValueError("Not a git checkout following a branch: there's nothing to update from.")
        widgets.save_settings(auto_update=v)

    def fonts():
        out = list(fontsetup.code_fonts())
        return out + [f for f in (fontsetup.code_font(), fontsetup.running_font()) if f not in out]
    out = [
        Setting("accent", "Accent Colour", lambda: widgets.load_settings().get("accent") or "system", accent,
                widgets.settings_accent, ["system", *widgets.ACCENTS]),
        Setting("code_font", "Code Font", fontsetup.code_font, lambda v: widgets.save_settings(code_font=v),
                lambda: widgets.settings_code_font(win, app), fonts(),
                note=f"takes from {app}'s next start: its row has Restart"),
        Setting("text_scale", "Text Scale", lambda: widgets.scale_setting("text_scale"),
                lambda v: win.set_scale("text_scale", v),
                lambda: widgets.settings_size("Text Scale", None, widgets.scale_setting("text_scale"),
                                              lambda s: win.set_scale("text_scale", s)),
                (widgets.SCALE_MIN, widgets.SCALE_MAX)),
        Setting("icon_scale", "Icon Scale", lambda: widgets.scale_setting("icon_scale"),
                lambda v: win.set_scale("icon_scale", v),
                lambda: widgets.settings_size("Icon Scale", None, widgets.scale_setting("icon_scale"),
                                              lambda s: win.set_scale("icon_scale", s)),
                (widgets.SCALE_MIN, widgets.SCALE_MAX)),
        Setting("global_scale", "Global Scale", zoom_default, win.set_zoom_default,
                lambda: widgets.settings_size("Global Scale", None, zoom_default(), win.set_zoom_default,
                                              widgets.ZOOM_MIN, widgets.ZOOM_MAX),
                (widgets.ZOOM_MIN, widgets.ZOOM_MAX), note="what ⌘0 / Ctrl+0 goes back to"),
        Setting("full_width", "Full-width text",
                lambda: bool((a := win.lookup_action("fullwidth")) and a.get_state().get_boolean()), wide,
                lambda: widgets.settings_switch("Full-width text", None, "win.fullwidth"), bool),
        Setting("auto_update", "Auto restart on update", update.enabled, auto, update.settings_row, bool),
        Setting("meta_model", "Intelligence", model,
                lambda v: (widgets.save_settings(meta_model=v), chat.model_changed()), lambda: model_row(chat),
                [v for v, _label in MODELS_OFFERED], note=f"{NAME}'s own model, from its next message"),
        Setting("meta_code_mode", "Code mode", code_mode,
                lambda v: (widgets.save_settings(meta_code_mode=v), chat.model_changed()), lambda: code_mode_row(chat),
                bool, note=f"{NAME} acts through run_python, from its next message; the user's to turn on",
                meta_sets=False),
    ]
    return {s.name: s for s in out}


# ---------------------------------------------------------------- its tools

SYSTEM = """You are {name}, the helper inside {app}, {what} You operate the app for the user: you read its state, \
change its settings, and organise and change what's in it, with your tools. Do what's asked; when it's ambiguous which \
things are meant, say which you'd take, or highlight them, before changing anything.

What you know of the app comes with the user's first message: a short index, a line or two per part, with short ids. \
app_index opens a part in full (its rows, a filter to narrow them): look rather than guess, and don't ask the user for \
what the index has.

Everything you do is recorded as an action, with an id, and can be undone: under your reply the user sees each one \
with undo and redo, and a change to {listed} is marked in the left bar (green added, red removed, blue moved) till they \
keep or undo it. undo and redo take one back or put it again. You're told when the user undoes something. Never do \
what can't be undone: say so plainly, and show its button (show button:<action>) for the user to press themselves.

Settings: set_setting changes one at once, and its row shows under your reply, where the user can go on adjusting it; \
you're told the value they settle on. Asked about a setting without being asked to change it, show its row (show \
setting:<name>). show also lists chats{and_pages} as rows the user can click, and shows the changelog.

Be brief and plain: a sentence or two, no headings. Don't describe what your tools did: the user sees it."""

CODE_SYSTEM = """

Code mode: run_python is your one tool for the app. It runs up to {lines} lines of Python with app, whose functions \
are the app's tools (below), and returns what's printed and the last expression's value. app.app_index gives its \
rows as dicts; the others give their answer as text, and raise RuntimeError for an error. Look things up and act in \
one go: filter, then act on what's found. No imports, no names starting with _; plain builtins only.
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


class Agent:
    """Meta's session: a Claude Code session for a conversation (one turn at a time: ask), on the model chosen. Its
    tools only read (READ_TOOLS), in Claude Code's own default permission mode: what's under roots it reads as it
    likes; anything else it asks for, and is let read what the user referenced, else the user's asked
    (MetaChat.ask_permission); anything but its tools it's refused. Its own tools come in on the ToolSocket. Its
    callbacks reach chat on the main loop: _said_more, _tool_started, _tool_ended, _done, agent_gone."""

    STOP_WAIT = 3000  # ms for an interrupted turn to end, before the session's stopped
    EXIT_WAIT = 200  # ms for what it said as it ended (stderr, read on its own thread) to come in

    def __init__(self, chat, key, system, tools, cwd, roots=(), model=MODEL, exe=None, env_of=None):
        self.chat, self.key, self.model = chat, key, model
        self.roots = [os.path.realpath(r) for r in roots if r]
        self.turn, self.fresh, self.stderr, self.closed = None, True, [], False
        self.allowed = set()  # (paths the user referenced: read when it asks)
        self.token = uuid.uuid4().hex
        self.cwd = cwd
        sock = ToolSocket.get()
        sock.add(self.token, lambda tool, args: chat._tool(self, tool, args), tools,
                 python=lambda args: chat._python(self, args))
        relay = {"mcpServers": {"meta": {"command": sys.executable, "args": ["-I", "-c", RELAY, sock.path, self.token]}}}
        args = ["--system-prompt", system, "--tools", ",".join(READ_TOOLS),
                "--mcp-config", json.dumps(relay), "--strict-mcp-config",
                "--allowedTools", ",".join(f"mcp__meta__{t['name']}" for t in tools),
                "--no-session-persistence",  # (no transcript: it would be listed with your chats)
                "--disable-slash-commands", "--setting-sources", ""]
        for root in self.roots:
            args += ["--add-dir", root]
        self.proc = MetaProc(self.on_msg, self.on_exit, cwd, model, "default", extra=args, env=AGENT_ENV, exe=exe,
                             env_of=env_of)

    def ask(self, text, turn):
        """A message (turn: what the reply's made of, as it comes: its text, actions, what it shows)."""
        self.turn, self.fresh = turn, False
        self.proc.user(text)

    def stop(self):
        """The turn interrupted (Esc, the stop button): what's been said kept; the session too, unless it won't stop."""
        if self.turn is None or self.turn.get("stopped"):
            return
        self.turn["stopped"] = True
        self.chat._drop_asking(deny=True)
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
        elif name in READ_TOOLS:
            self.chat.ask_permission(self, rid, name, inp)
        else:
            self.proc.respond(rid, {"behavior": "deny", "message": f"{NAME} reads, and acts with its own tools: it "
                                    "doesn't run others."})

    def answer(self, rid, allow, name, inp):
        """The user's answer to a read it asked for (MetaChat.ask_permission)."""
        if self.closed:
            return
        if allow:
            if path := inp.get("file_path") or inp.get("path"):
                self.allowed.add(os.path.realpath(os.path.join(self.cwd, os.path.expanduser(str(path)))))
            self.proc.respond(rid, {"behavior": "allow", "updatedInput": inp})
        else:
            self.proc.respond(rid, {"behavior": "deny", "message": f"The user didn't let you {name} that."})

    def on_msg(self, proc, msg):
        if proc is not self.proc or self.closed:
            return False
        t, turn = msg.get("type"), self.turn
        if t == "_stderr":
            self.stderr = (self.stderr + [msg.get("text", "")])[-20:]
        elif t == "control_request":
            if (msg.get("request") or {}).get("subtype") == "can_use_tool":
                self.permission(msg)
            else:
                proc.send({"type": "control_response", "response": {
                    "subtype": "error", "request_id": msg.get("request_id"),
                    "error": f"meta: unsupported {(msg.get('request') or {}).get('subtype')}"}})
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
            for c in (msg.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    self.chat._tool_started(self, turn, c)
                elif c.get("type") == "text" and not turn.get("streamed") and c.get("text"):
                    turn["text"].append(c["text"])
                    self.chat._said_more(self, turn, c["text"])
        elif t == "user":  # (what its tools gave back)
            for c in (msg.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    self.chat._tool_ended(self, turn, c)
        elif t == "result":
            self.turn = None
            text = "".join(turn["text"]).strip() or ("" if msg.get("is_error") else (msg.get("result") or "").strip())
            error = None
            if msg.get("is_error") and not turn.get("stopped"):
                error = (msg.get("result") or "; ".join(map(str, msg.get("errors") or [])) or
                         "claude reported an error")[-600:]
                if "log" in error.lower() and "in" in error.lower():
                    error += "\n\nRun `claude` in a terminal and /login, then ask again."
            self.chat._done(self, text, error, turn)
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
                ("\n".join(self.stderr[-6:]).strip() or f"claude exited with {code}")[-600:]
            self.chat._done(self, "".join(turn["text"]).strip(), error, turn)
        self.chat.agent_gone(self)
        return False


# ---------------------------------------------------------------- how it looks

CSS = f"""
label.said-refs link {{ color: {REF_COLOR}; }}  /* (an @reference in what you wrote: MetaChat._said) */
.meta-ui {{ margin-top: 2px; }}
.tool-card button.meta-batch {{ min-height: 0; min-width: 0; padding: 1px 5px; }}
.changes-bar {{ background: alpha(@accent_bg_color, 0.12); border-radius: 8px; padding: 1px 2px 1px 10px; }}
.changes-bar button {{ min-height: 24px; padding: 0 8px; }}
label.meta-note {{ font-size: 0.85em; opacity: 0.75; }}
"""
_CSS = []
MARK_COLORS = {"added": "#2ec27e", "removed": "#e01b24", "moved": "#3584e4"}  # (highlight: the accent's)
MARK_ALPHA, LIT_ALPHA = 0.28, 0.6  # (a mark's highlight; lit, its card under the pointer: MetaChat.light)


def install_css(display):
    """Meta's look, once for the app."""
    if not _CSS and display is not None:
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _CSS.append(provider)


def mark_row(row, key, mark, chat, name_label, inline=True):
    """A row of a left bar's (key: (kind, its key): a chat, a page) as Meta's changed it (mark: (how, note, action)):
    its name highlighted, square, behind its text only (green added, red removed and struck through, blue moved;
    highlighted, the accent), its note (the group it went to, where it came from) after its name if inline, else under
    the pointer with what was done. Lit more while the pointer's over the card of the call that did it (meta_paint,
    MetaChat.light). Kept or undone from that card, all the call did at once: nothing here takes part of it back."""
    how, note, action = mark

    def paint(lit=False):
        rgba = widgets.accent_rgba() if how == "highlight" else Gdk.RGBA()
        if how != "highlight":
            rgba.parse(MARK_COLORS[how])
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_background_new(*(round(c * 65535) for c in (rgba.red, rgba.green, rgba.blue))))
        attrs.insert(Pango.attr_background_alpha_new(round((LIT_ALPHA if lit else MARK_ALPHA) * 65535)))
        if how == "removed":
            attrs.insert(Pango.attr_strikethrough_new(True))
        name_label.set_attributes(attrs)
    paint()
    row.meta_paint = paint
    chat.painted[key] = row
    if note or action is not None:
        said = f"{NAME}: {action['label'] if action else 'highlighted'}" + (f" ({note})" if note else "")
        row.set_tooltip_text("\n".join(filter(None, [row.get_tooltip_text(), said])))
    if note and inline:
        said = Gtk.Label(label=note, ellipsize=Pango.EllipsizeMode.END, max_width_chars=12, valign=Gtk.Align.CENTER)
        said.add_css_class("meta-note")
        row.get_child().append(said)


class SaidLabel(widgets.LinkLabel):
    """What you wrote to Meta (MetaChat._said): a LinkLabel whose logos are in the @names' colour, as a page draws
    them, not the text's."""

    COLOR = Gdk.RGBA()
    COLOR.parse(REF_COLOR)

    def get_color(self):
        return self.COLOR


class RefCompleter:
    """@'s list over Meta's box: what space() lists (Record's: its notebooks and pages, the folders' names, the chats;
    Baude's: its chats and its chat's names), as Baude's / is over its composer (SlashCompleter). An @ typed at a word's
    start starts it; what's typed after it is its search, spaces and all, till Enter or Tab takes what's picked (as
    @name, or @{a name} with spaces), Esc leaves it, a new line, the caret back before the @, or words that find
    nothing. With nothing typed yet, first(items) puts them in order."""

    def __init__(self, space, view, anchor, first=None):
        self.space, self.view, self.anchor, self.first = space, view, anchor, first
        self.picks = widgets.PickList(anchor, self._bind, self.accept, position=Gtk.PositionType.TOP)
        self.pop = self.picks.pop
        self.pop.set_halign(Gtk.Align.START)  # (its left side on the @'s: PickList.over)
        self.pop.set_offset(0, -10)
        self.shown, self.query, self.moved, self.waiting, self.at = [], None, False, None, None
        buf = view.get_buffer()
        # (the @names it knows, as the notes colour theirs, and the search being typed: after the box's own markdown)
        buf.create_tag("record-ref", foreground=REF_COLOR, weight=600, background="rgba(79,138,124,0.14)")
        buf.connect("insert-text", self._inserting)
        buf.connect_after("changed", lambda *_: (self.refresh(), self.mark()))
        buf.connect("notify::cursor-position", lambda *_: (self.refresh(), self.mark()))

    def _inserting(self, buf, at, text, _n):
        if text != "@":
            return
        before = at.copy()
        if at.starts_line() or before.backward_char() and (before.get_char().isspace() or before.get_char() in "([{\"'"):
            if self.at is not None:
                buf.delete_mark(self.at)
            self.at = buf.create_mark(None, at, True)  # (left of what goes in: on the @)

    def word(self):
        """(where its @ is, what's typed after it) while @'s search is on, else None."""
        if self.at is None or self.at.get_deleted():
            return None
        buf = self.view.get_buffer()
        a = buf.get_iter_at_mark(self.at).get_offset()
        caret = buf.get_iter_at_mark(buf.get_insert()).get_offset()
        typed = buf.get_text(buf.get_iter_at_offset(a), buf.get_iter_at_offset(max(a, caret)), True)
        if caret <= a or not typed.startswith("@") or "\n" in typed:
            return None
        return a, typed[1:].lstrip("{")

    def forget(self):
        if self.at is not None:
            self.view.get_buffer().delete_mark(self.at)
            self.at = None
        self.hide()

    def refresh(self):
        word = self.word()
        if word is None:
            if self.at is not None or self.picks.visible():
                self.forget()
            return
        space, q = self.space(), word[1]
        if space is None:
            return self.forget()
        shown, matched = space.search(q)
        if not q.strip() and self.first is not None:
            shown = self.first(shown)
        if not shown and " " in q:  # (an @ and words that name nothing: just words)
            return self.forget()
        i = self.picks.picked()
        kept = self.shown[i]["surface"] if self.moved and q == self.query and i is not None and i < len(self.shown) \
            else None  # (the same search, listed again as the catalogue comes in: what's picked stays picked)
        self.shown, self.query = shown, q
        at = next((j for j, it in enumerate(shown) if it["surface"] == kept), None)
        self.moved = at is not None
        if self.waiting is None and space.busy:
            self.waiting = space.refresh(self._catalogued)
        note = (f"{matched} found" if matched else "Nothing found") + (" · cataloguing…" if space.busy else "")
        wide, most = self.anchor.get_width() - 10, widgets.PickList.WIDTH
        self.picks.scroll.set_size_request(min(most, wide) if wide > 0 else most, -1)
        self.picks.show(len(shown), (at or 0) if shown else None, note,
                        widgets.PickList.over(self.view, word[0], self.anchor))

    def _catalogued(self):
        self.waiting = None
        if self.picks.visible():
            self.refresh()
        self.mark()

    def mark(self):
        """The @names the catalogue knows, in the notes' @names' colour, and @'s search while it's on."""
        buf = self.view.get_buffer()
        start, end = buf.get_bounds()
        buf.remove_tag_by_name("record-ref", start, end)
        text, space = buf.get_text(start, end, True), self.space()
        for m in REF_RE.finditer(text):
            if space is not None and space.find(m.group(1) or m.group(2)) is not None:
                buf.apply_tag_by_name("record-ref", buf.get_iter_at_offset(m.start()), buf.get_iter_at_offset(m.end()))
        if (word := self.word()) is not None:
            buf.apply_tag_by_name("record-ref", buf.get_iter_at_offset(word[0]),
                                  buf.get_iter_at_offset(word[0] + 1 + len(word[1])))

    def _bind(self, i, icon, name, where):
        it = self.shown[i]
        icon.icon = it["icon"]
        name.set_text(it.get("label") or it["surface"])
        where.set_text(f"{it['what']} · {it['summary']}")
        widgets.time_tip(where, it.get("at"), it.get("ago"))

    def accept(self):
        """The picked name in place of the @word, a space after it (one there already is the one)."""
        i, word = self.picks.picked(), self.word()
        if i is None or i >= len(self.shown) or word is None:
            return
        buf = self.view.get_buffer()
        end = buf.get_iter_at_mark(buf.get_insert())
        if end.get_char() == " ":
            end.forward_char()
        surface = self.shown[i]["surface"]
        self.forget()
        buf.begin_user_action()
        buf.delete(buf.get_iter_at_offset(word[0]), end)
        buf.insert_at_cursor(plain_ref(surface) + " ")
        buf.end_user_action()
        self.view.grab_focus()

    def visible(self):
        return self.picks.visible()

    def hide(self):
        self.moved = False
        self.picks.hide()

    def handle_key(self, keyval, state=0):
        """True if the key was the list's."""
        if not self.visible():
            return False
        steps = widgets.PickList.STEPS
        if state & Gdk.ModifierType.SHIFT_MASK and keyval in (*steps, Gdk.KEY_Left, Gdk.KEY_Right):
            self.forget()  # (Shift and an arrow: selecting the text, not picking in the list)
            return False
        if keyval in steps:
            if self.shown:
                self.picks.move(steps[keyval])
                self.moved = True
        elif keyval in (Gdk.KEY_Tab, Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.picks.picked() is not None:
            self.accept()
        elif keyval == Gdk.KEY_Escape:
            self.forget()
        else:
            return False
        return True


def rows_group(rows, title=None, limit=None):
    """Rows to click (show chats, show pages), as Settings lists its rows: [{icon, title, subtitle, open}]; past limit,
    a Show N more row."""
    group = Adw.PreferencesGroup(title=title or "")
    group.add_css_class("meta-ui")
    shown = rows if limit is None else rows[:limit]

    def add(r):
        row = Adw.ActionRow(title=r["title"], subtitle=r.get("subtitle") or "", activatable=r.get("open") is not None,
                            use_markup=False)
        if r.get("icon"):
            row.add_prefix(Gtk.Image(icon_name=r["icon"]))
        if r.get("open") is not None:
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda *_, fn=r["open"]: fn())
        group.add(row)
    for r in shown:
        add(r)
    if len(shown) < len(rows):
        more = Adw.ButtonRow(title=f"Show {len(rows) - len(shown)} more")

        def all_of_them(btn):
            group.remove(btn)
            for r in rows[len(shown):]:
                add(r)
        more.connect("activated", all_of_them)
        group.add(more)
    return group


# ---------------------------------------------------------------- the column

class MetaChat(Gtk.Box):
    """Your conversation with Meta, in the right bar: replies, each with the actions it took (undo, redo) and what it
    shows (rows of settings, chats, pages; the changelog; a button); @ in the box lists what's to reference
    (RefCompleter); over the box, while changes in the left bar wait to be kept, how many, Keep and Undo. Each app's
    is a subclass of its own (APP, WHAT, LISTED; the hooks below)."""

    APP, WHAT, LISTED, PAGES = "the app", "", "what the left bar lists", False
    CHAT_ROWS = False  # (whether its left bar lists chats: what rename_chat changes is marked there)

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        install_css(Gdk.Display.get_default())
        widgets.ensure_icons()
        self.win, self.ask, self.agent = win, None, None
        self.live_widget, self.asking = None, []  # (the reply coming; the reads it asks to make)
        self.lit = {}  # (what highlight_ asked for, by kind: {kind: {key, …}}; gone with the next message)
        self.painted = {}  # ((kind, key): the left bar's row marked for it, as last made: mark_row, light)
        head = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_end=8, margin_top=10, margin_bottom=6)
        clear = Gtk.Button(icon_name="edit-clear-all-symbolic", tooltip_text="Clear conversation")
        clear.add_css_class("flat")
        clear.connect("clicked", lambda *_: self._clear())
        head.append(clear)
        self.head = head
        self.append(head)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                           margin_start=12, margin_end=12)
        self.scroll = widgets.markdown_scroller(self.box, vexpand=True)
        # the conversation and the box asked in, set into the column as a chat is into Baude's window (the inside:
        # widgets.inside), Clear above it on the frame
        talk = widgets.inside(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), start=False)
        talk.append(self.scroll)
        self.append(talk)
        comp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=10, margin_end=10, margin_bottom=10)
        self.bars = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)  # (an app's own, over the box)
        comp.append(self.bars)
        self.changes_box = self._changes_bar()
        comp.append(self.changes_box)
        self.writing = widgets.Composer()  # (its button's tip says the model: model_changed)
        self.composer, self.send = self.writing.input, self.writing.send
        self.refs = RefCompleter(self.space, self.composer, self.writing, self.first_refs)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.composer.add_controller(keys)
        self.send.connect("clicked", lambda *_: self.ask.stop() if self.ask else self.submit())
        comp.append(self.writing)
        talk.append(comp)
        self.settings = {}  # (made once the window is: make_settings)
        self.model_changed()

    # ---- what an app's MetaChat says
    def messages(self):
        """The conversation's messages (a list kept by the app)."""
        return []

    def actions(self):
        """Its actions, in the order they were taken (a list kept by the app)."""
        return []

    def save(self, now=False):
        pass

    def told(self):
        """What Meta's to be told with the next message (the user's undid or redid one of its actions): a list kept
        with the conversation it's for."""
        return []

    def ready(self):
        """Whether there's a conversation to ask in."""
        return True

    def space(self):
        """What @ lists (a lookup.SearchSpace), or None."""
        return None

    def first_refs(self, items):
        return items

    def agent_setup(self):
        """{cwd, roots, exe, env_of} for its session."""
        return {"cwd": os.path.expanduser("~"), "roots": ()}

    def scope(self):
        """What a session's for, besides its model and mode (Record's: the notebook and its folders)."""
        return ()

    def app_prompt(self):
        """What its instructions add for this app."""
        return ""

    def app_tools(self):
        return []

    def app_handlers(self):
        return {}

    def sections(self):
        """The index's parts: {name: (rows(filter) -> [dict], line(row) -> str)}; the app's, then settings', runtime's
        and the changelog's (shared_sections)."""
        return {}

    def index_lines(self):
        """The app's lines of the index (index_text puts them after its first)."""
        return []

    def running_lines(self):
        """What's running that the app knows of (its sessions, its agents), for the index's runtime part."""
        return []

    def find_chat(self, ref):
        """A chat a tool names (an id or its start, a title): {key, title, path} or None."""
        return None

    def chats_rows(self, refs):
        """Rows to click for chats (show chats): [{icon, title, subtitle, open}], or ValueError."""
        raise ValueError("There are no chats here.")

    def pages_rows(self, refs):
        raise ValueError("There are no pages here.")

    def button(self, action, items):
        """A button for the user to press (show button:<action>), or ValueError."""
        raise ValueError(f"There's no button {action}.")

    def buttons(self):
        """{action: what it does}: the buttons show offers."""
        return {}

    def rename_chat_to(self, chat, title):
        """A chat (find_chat's) renamed; raises ValueError if it can't be."""
        raise ValueError("Chats can't be renamed here.")

    def chat_text(self, chat):
        return transcript_text(chat["path"]) if chat.get("path") else "(no transcript)"

    def apply_app(self, kind, data):
        """An app's own action done or undone (data: its undo's or redo's)."""
        raise ValueError(f"No such action: {kind}")

    def marks_changed(self):
        """What's marked in the left bar changed (an action taken, kept or undone; a highlight)."""

    def context(self, agent, msg):
        """What goes with a message, before it (the app's): [text, …]."""
        return []

    def make_settings(self):
        return {}

    # ---- the conversation
    def model_changed(self):
        """Settings → Meta: the send button says the model it asks; the next message is the new model's (submit)."""
        name = dict(MODELS_OFFERED).get(model(), model())
        self.writing.send_tip = f"Ask {NAME} ({name}{', code mode' if code_mode() else ''}) (Enter)\n" \
                                "New line: Shift+Enter\n@: a chat, a file…"
        if self.ask is None:
            self.send.set_tooltip_text(self.writing.send_tip)

    def close_agent(self):
        """Its session stopped (the conversation cleared, the window gone): the next message starts another, told what
        was said."""
        if self.agent is not None:
            self.agent.close()
        self.agent = self.ask = None

    def agent_gone(self, agent):
        if agent is self.agent:
            self.agent = self.ask = None
            self.writing.set_busy(False)
            self._drop_asking()

    def _clear(self):
        self.close_agent()
        del self.messages()[:]
        self.save(now=True)
        self.render()

    def _key(self, _c, keyval, _code, state):
        """Enter asks (Ctrl+Enter too); Shift+Enter, or Enter in a code block, is a new line, as in Baude; a selection
        and * ` or / is made bold, code or italic; @'s list, while it's up, has the arrows, Enter, Tab and Esc
        (RefCompleter); Esc stops the reply."""
        if self.refs.handle_key(keyval, state):
            return True
        if widgets.leave_wrapped(self.composer.get_buffer(), keyval, state):
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and state & Gdk.ModifierType.ALT_MASK and \
                not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.SHIFT_MASK):
            return self.writing.markdown.move_item(-1 if keyval == Gdk.KEY_Up else 1)
        if not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK) and \
                widgets.wrap_selection(self.composer.get_buffer(), chr(Gdk.keyval_to_unicode(keyval) or 0)):
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            markdown = self.writing.markdown
            if not state & Gdk.ModifierType.CONTROL_MASK and (state & Gdk.ModifierType.SHIFT_MASK
                                                               or markdown.code_state()[0]):
                return markdown.newline()
            self.submit()
            return True
        if keyval == Gdk.KEY_Escape and self.ask:
            self.ask.stop()
            return True
        return False

    def render(self):
        while (c := self.box.get_first_child()) is not None:
            self.box.remove(c)
        for m in self.messages() if self.ready() else ():
            self.box.append(self._bubble(m))
        if self.ask is not None and self.live_widget is not None:  # (the reply coming, and what it's asking: kept)
            self.box.append(self.live_widget)
            for card in self.asking:
                self.box.append(card)
        self.show_changes_bar()
        for ms in (60, 300):  # (and again once what's tall, a changelog, a list of rows, is laid out)
            GLib.timeout_add(ms, self._to_bottom)

    def _to_bottom(self):
        if getattr(self.scroll, "held", False):
            return False  # (put back where it was read to: widgets.hold_place)
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_upper())
        return False

    def quote_said(self, m):
        """A user's message's quote, as its bubble shows it (above what they wrote), or None."""
        q = m.get("quote")
        return "“" + (" ".join(q.split())[:400] + ("…" if len(q) > 400 else "")) + "”" if q else None

    def prose(self, text):
        """A reply's text, as Baude's chat shows one: Markdown, in no bubble (Record's links its folders' names)."""
        return widgets.MarkdownView(text)

    def set_prose(self, widget, text):
        widget.set_text(text)

    def _bubble(self, m):
        """A message as Baude's chat has one: what you wrote in a bubble, on the right; Meta's reply in none, in the
        order it came (each part of what it said, and each tool it called, as a tool card, with what that showed
        under it)."""
        if m["role"] == "user":
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.END, margin_start=28)
            b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            b.add_css_class("user-bubble")
            if (q := self.quote_said(m)) is not None:
                b.append(label(q, css=("quote-in-bubble",), select=True))
            b.append(self._said(m))
            outer.append(b)
            outer.bubble = b
            return outer
        reply = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        reply.bubble, reply.prose = reply, None
        items = m.get("items")
        if m.get("error"):
            for it in items or ():
                if it["kind"] == "tool":
                    reply.append(self.tool_card(it))
                    self.add_shown(reply, it.get("shown") or ())
            reply.prose = label(m.get("text") or "", css=("error",), select=True)
            reply.append(reply.prose)
            return reply
        if items is None:  # (a reply kept from before its parts were: its text, then what it did)
            items = [{"kind": "text", "text": m["text"]}] if m.get("text") else []
        for it in items:
            if it["kind"] == "text":
                if (text := self.reply_text(it["text"]).strip()):
                    reply.prose = self.prose(text)
                    reply.append(reply.prose)
            else:
                reply.append(self.tool_card(it))
                self.add_shown(reply, it.get("shown") or ())
                reply.prose = None  # (what grows is the part after it, if one comes)
        if m.get("items") is None:
            self.add_work(reply, m)
        if not items and not m.get("actions") and not m.get("shown"):
            reply.prose = self.prose(m.get("text") or "…")
            reply.append(reply.prose)
        return reply

    def has_work(self, m):
        """Whether a reply did anything: called a tool, took an action, showed something."""
        return bool(m.get("actions") or m.get("shown") or m.get("code") or
                    any(it["kind"] == "tool" for it in m.get("items") or ()))

    def add_shown(self, box, specs):
        for spec in specs:
            try:
                if (w := self.shown_widget(spec)) is not None:
                    box.append(w)
            except (ValueError, KeyError) as e:
                box.append(label(f"({e})", css=("dim-label", "caption")))

    def add_work(self, box, m):
        """A reply kept from before its parts were: its actions, as one card's, and what it showed."""
        acts = {a["id"]: a for a in self.actions()}
        if mine := [acts[i] for i in m.get("actions") or () if i in acts]:
            box.append(self.tool_card({"kind": "tool", "name": "", "input": {}, "state": "done",
                                       "actions": [a["id"] for a in mine]}))
        self.add_shown(box, m.get("shown") or ())

    # ---- a tool call, as Baude's chat shows one
    def tool_title(self, name):
        """A tool's name as its card says it: Claude Code's as they are (Read), Meta's own in words (Move pages)."""
        name = name.removeprefix("mcp__meta__")
        return name if name in READ_TOOLS else name.replace("_", " ").capitalize() if name else "Changes"

    def tool_summary(self, name, inp):
        """What a call of a tool was for, in a line: a file read, a pattern, a page, the chats, a setting and its
        value, the code's first line."""
        name = name.removeprefix("mcp__meta__")
        short = lambda v: ", ".join(map(str, v)) if isinstance(v, list) else str(v)  # noqa: E731
        if name == "Read":
            return os.path.basename(str(inp.get("file_path") or "").rstrip(os.sep))
        if name in ("Grep", "Glob"):
            return str(inp.get("pattern") or "")
        if name == "run_python":
            return next((line for line in str(inp.get("code") or "").splitlines() if line.strip()), "")
        if name == "set_setting":
            return f"{inp.get('name')} → {said_value(inp.get('value'))}"
        if name == "show":
            return " · ".join(filter(None, [str(inp.get("what") or ""), short(inp.get("items") or [])]))
        return " · ".join(short(v) for v in inp.values() if v not in (None, "", []))[:200]

    def tool_card(self, it):
        """A tool card as Baude's chat has one (widgets.CSS's .tool-card): its name, what it was for, Input (Code)
        and Output to open; a spinner while it runs, red if it failed. A call that changed things: its actions one
        batch, Keep and Undo (Redo, undone) on its line, and the pointer over it lights what it changed in the left
        bar (light)."""
        name = it.get("name") or ""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("tool-card")
        if it.get("state") == "error":
            card.add_css_class("error")
        grid, line = Gtk.Grid(), Gtk.Box(spacing=8)
        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>{html.escape(self.tool_title(name))}</b>")
        toggles = Gtk.Box(spacing=4)
        header = Gtk.Label(label=self.tool_summary(name, it.get("input") or {}), xalign=0, hexpand=True,
                           ellipsize=Pango.EllipsizeMode.END)
        for w in (title, toggles, header):
            line.append(w)
        grid.attach(line, 0, 0, 1, 1)
        card.append(grid)
        base = name.removeprefix("mcp__meta__")
        if it.get("input"):
            code = base == "run_python"
            shown = str(it["input"].get("code") or "") if code else json.dumps(it["input"], ensure_ascii=False, indent=1)
            self._disclosure(toggles, grid, 1, "Code" if code else "Input", shown, "python" if code else "json")
        if it.get("result"):
            self._disclosure(toggles, grid, 2, "Error" if it.get("state") == "error" else "Output", it["result"],
                             "output")
        acts = {a["id"]: a for a in self.actions()}
        batch = [acts[i] for i in it.get("actions") or () if i in acts]
        if batch:
            card.set_tooltip_text("\n".join(a["label"] for a in batch))
            if all(a["state"] != "done" for a in batch):
                header.add_css_class("dim-label")
                header.set_label(header.get_label() + " · undone")
            line.append(self.batch_controls(batch))
            hover = Gtk.EventControllerMotion()
            hover.connect("enter", lambda *_: self.light(batch, True))
            hover.connect("leave", lambda *_: self.light(batch, False))
            card.add_controller(hover)
        if (status := widgets._card_status(it.get("state") or "done")) is not None:
            line.append(status)
        return card

    @staticmethod
    def _disclosure(toggles, grid, row, label_, text, lang):
        """A toggle on a card's line opening what it was given or gave back, made when it's first opened."""
        d = widgets.Disclosure(toggles, grid, row, label_, button=True)

        def build():
            if d.get_child() is None:
                block = widgets.CodeBlock(text, lang)
                block.set_margin_top(6)
                d.set_child(block)
        d.on_expand.append(build)
        return d

    def batch_controls(self, batch):
        """A call's actions, one batch: Keep (its marks in the left bar gone) while any's marked, and Undo, all of
        them; Redo, all of them, once they're undone. Never one of them alone: they were done together."""
        box = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        done = [a for a in batch if a["state"] == "done"]
        buttons = []
        if any(not a.get("kept") for a in done):
            buttons.append(("object-select-symbolic", "Keep", lambda: self.keep(done)))
        if done:
            buttons.append(("edit-undo-symbolic", "Undo", lambda: self.undo_many(batch, True)))
        else:
            buttons.append(("edit-redo-symbolic", "Redo", lambda: self.redo_many(batch, True)))
        for icon, tip, fn in buttons:
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER)
            btn.add_css_class("flat")
            btn.add_css_class("meta-batch")
            btn.connect("clicked", lambda *_, fn=fn: fn())
            box.append(btn)
        return box

    def light(self, batch, on):
        """What a batch of actions changed, lit in the left bar (on) or as it was, as the pointer goes over its card."""
        for a in batch:
            if a["state"] != "done" or a.get("kept"):
                continue
            for kind, key, *_rest in a.get("marks") or ():
                if (row := self.painted.get((kind, key))) is not None and hasattr(row, "meta_paint"):
                    row.meta_paint(on)

    def shown_widget(self, spec):
        """What show put under a reply, made from what's kept of it: a setting's row, rows of chats or pages, the
        changelog, a button."""
        kind = spec.get("kind")
        if kind == "setting":
            s = self.settings[spec["name"]]
            group = Adw.PreferencesGroup()
            group.add_css_class("meta-ui")
            group.add(s.row())
            return group
        if kind == "chats":
            return rows_group(self.chats_rows(spec.get("items") or []), limit=12)
        if kind == "pages":
            return rows_group(self.pages_rows(spec.get("items") or []), limit=12)
        if kind == "changelog":
            group = update.changelog_group(update.STARTED, limit=5)
            group.add_css_class("meta-ui")
            return group
        if kind == "button":
            btn = self.button(spec["action"], spec.get("items") or [])
            btn.set_halign(Gtk.Align.START)
            btn.add_css_class("meta-ui")
            return btn
        return None

    def _said(self, m):
        """What you wrote, its @references in it as a page draws an @name: the logo where its @ is (LinkLabel draws
        it), the name in the @names' colour on their patch, a pull request or an issue by its number alone;
        clicked, opened as one on a page is."""
        refs, text = {r["surface"]: r for r in m.get("refs") or ()}, self.plain(m["text"])
        out, at = [], 0
        for found in REF_RE.finditer(text):
            r = refs.get(found.group(1) or found.group(2))
            if r is None:
                continue
            github = lookup.GITHUB_NAME.fullmatch(r["surface"]) if (r.get("icon") or "").startswith(("pr_", "issue")) \
                else None
            name = html.escape(github[2] if github else r["surface"], False)
            out += [html.escape(text[at:found.start()], False),
                    f'<a href="ref:{urllib.parse.quote(r["surface"], safe="")}"><span foreground="{REF_COLOR}" '
                    f'background="#4f8a7c" bgalpha="14%" weight="600" underline="none">'
                    f'{widgets.ref_icon_slot(r.get("icon")) or "@"}{name}</span></a>']
            at = found.end()
        out.append(html.escape(text[at:], False))
        said = SaidLabel(xalign=0.0, wrap=True, selectable=True)
        said.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        said.add_css_class("said-refs")
        said.set_markup("".join(out))
        self.linked(said)
        return said

    def plain(self, text):
        return text

    def linked(self, lb):
        """A label with names in it, its links followed as the app follows them (Record's: open_ref)."""

    def referenced(self, text):
        """The things a message @-references that space() knows, each once, in order."""
        out, seen, space = [], set(), self.space()
        for m in REF_RE.finditer(self.plain(text)):
            item = space.find(m.group(1) or m.group(2)) if space is not None else None
            if item is not None and item["surface"] not in seen:
                seen.add(item["surface"])
                out.append(item)
        return out

    def make_message(self, text):
        """A message as it's kept: what was written, and what it references."""
        msg = {"role": "user", "text": text, "t": time.time()}
        if refs := self.referenced(text):
            msg["refs"] = [{"surface": it["surface"], "what": f"{it['what']}, {it['summary']}", "icon": it.get("icon")}
                           for it in refs]
        return msg

    def system(self, tools):
        """Its instructions: Meta's, the app's, and in code mode app's functions."""
        text = SYSTEM.format(name=NAME, app=self.APP, what=self.WHAT, listed=self.LISTED,
                             and_pages=" and pages" if self.PAGES else "")
        if extra := self.app_prompt():
            text += "\n\n" + extra
        if code_mode():
            text += CODE_SYSTEM.format(lines=CODE_LINES, doc=api_doc(tools))
        return text

    def all_tools(self):
        return SHARED_TOOLS + self.app_tools()

    def submit(self):
        buf = self.composer.get_buffer()
        text = self.writing.text().strip()
        if self.ask or not self.ready() or not (text or self.can_ask_empty()):
            return
        self.before_ask()
        msg = self.make_message(text or self.empty_question())
        self.messages().append(msg)
        buf.set_text("")
        self.taken(msg)
        if self.lit:
            self.lit = {}
            self.marks_changed()
        key = (self.scope(), model(), code_mode())
        if self.agent is not None and self.agent.key != key:
            self.close_agent()  # (another model, another scope: a session for them, told what was said)
        try:
            if self.agent is None:
                tools = self.all_tools()
                offered = [RUN_PYTHON] if code_mode() else tools
                setup = self.agent_setup()
                self.agent = Agent(self, key, self.system(tools), offered, setup["cwd"], setup.get("roots") or (),
                                   model(), setup.get("exe"), setup.get("env_of"))
                self.agent_made(self.agent)
            turn = {"text": [], "items": [], "actions": [], "shown": [], "code": [], "streamed": False,
                    **self.turn_extra(msg)}
            self.agent.ask(self.message_for(self.agent, msg), turn)
        except OSError as e:
            self.agent = None
            self.messages().append({"role": "meta", "text": f"Couldn't run claude ({e}).", "error": True,
                                    "t": time.time()})
            self.save(now=True)
            return self.render()
        self.ask = self.agent
        self.live_widget = self._live(turn)
        self.render()
        self.writing.set_busy(True)
        self.save(now=True)

    def agent_made(self, agent):
        """A session just started for the conversation (Record's: the notebook it's for, kept with it)."""

    def can_ask_empty(self):
        return False

    def empty_question(self):
        return ""

    def before_ask(self):
        pass

    def taken(self, msg):
        """A message just sent: what went with it let go (Record's quote)."""

    def turn_extra(self, msg):
        return {}

    def message_for(self, agent, msg):
        """What's sent for a message: a new session's index and what was said before it; what the user's done since
        (undone, redone, a row adjusted); the app's own (context); and what was written."""
        parts = []
        if agent.fresh:
            parts.append(f"<app_index>\n{self.index_text()}\n</app_index>")
            if earlier := earlier_said(self.messages()[:-1]):
                parts.append(earlier)
        parts += self.since()
        parts += self.context(agent, msg)
        parts.append(msg["text"])
        return "\n\n".join(p.strip("\n") for p in parts if p)

    def since(self):
        """What the user's done that Meta hasn't been told: its actions they undid or redid, and the settings they
        changed with the rows under its replies."""
        told = self.told()
        out = list(told)
        del told[:]
        for m in self.messages():
            specs = [s for it in m.get("items") or () if it["kind"] == "tool" for s in it.get("shown") or ()]
            for spec in specs or m.get("shown") or ():
                if spec.get("kind") != "setting" or "set" not in spec or spec["name"] not in self.settings:
                    continue
                now = self.settings[spec["name"]].value()
                if now != spec.get("told", spec["set"]):
                    out.append(f"(The user set {spec['name']} to {said_value(now)} with the row under your reply.)")
                    spec["told"] = now
        return out

    # ---- the reply as it comes
    def reply_text(self, text):
        """What of a reply's text is shown (Record's leaves out its Unknown: line)."""
        return text

    def _said_more(self, agent, turn, text):
        """More of what Meta's saying: the part it's on, or a new one after a tool's."""
        items = turn["items"]
        if items and items[-1]["kind"] == "text":
            items[-1]["text"] += text
        else:
            items.append({"kind": "text", "text": text})
        live = self.live_widget
        if agent is not self.ask or live is None:
            return
        shown = self.reply_text(items[-1]["text"]).strip()
        if live.parts == len(items) and live.prose is not None and shown:
            self.set_prose(live.prose, shown)  # (the part it's on, growing: only it's drawn again)
            GLib.idle_add(self._to_bottom)
        else:
            self._refresh_live(turn)

    def _tool_started(self, agent, turn, c):
        name, inp = c.get("name") or "", c.get("input") or {}
        made = next((it for it in turn["items"] if it["kind"] == "tool" and it.get("id") is None and it.get("input") == inp
                     and it["name"].removeprefix("mcp__meta__") == name.removeprefix("mcp__meta__")), None)
        if made is not None:  # (its call came in on the socket before Claude Code said it: that card's this one)
            made.update(id=c.get("id"), name=name)
        else:
            turn["items"].append({"kind": "tool", "id": c.get("id"), "name": name, "input": inp, "state": "running",
                                  "actions": [], "shown": []})
        if agent is self.ask:
            self._refresh_live(turn)

    def _tool_ended(self, agent, turn, c):
        item = next((it for it in turn["items"] if it["kind"] == "tool" and it.get("id") == c.get("tool_use_id")), None)
        if item is None:
            return
        content = c.get("content")
        text = content if isinstance(content, str) else "\n".join(
            b.get("text") or "" for b in content or () if isinstance(b, dict) and b.get("type") == "text")
        item["result"], item["state"] = trimmed(text, 4000), "error" if c.get("is_error") else "done"
        if agent is self.ask:
            self._refresh_live(turn)

    def _delta(self, agent, text):
        """(Record's edits say themselves as their cards: nothing more to say here.)"""

    def _status(self, agent, said):
        """(What it's doing is its tool cards: nothing more to say here.)"""

    def _refresh_live(self, turn):
        """The reply coming, made again with what it's done so far: its parts, its tool cards, what they show."""
        if self.ask is None or self.live_widget is None or self.ask.turn is not turn:
            return
        old = self.live_widget
        self.live_widget = self._live(turn)
        if old.get_parent() is self.box:
            self.box.insert_child_after(self.live_widget, old)
            self.box.remove(old)
        GLib.idle_add(self._to_bottom)

    def _live(self, turn):
        if not turn["items"]:
            reply = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            reply.append(label("Thinking…", css=("dim-label",)))
            reply.bubble, reply.prose = reply, None
        else:
            reply = self._bubble({"role": "meta", "items": turn["items"]})
            last = turn["items"][-1]
            if last["kind"] != "text":
                reply.prose = None  # (the part to grow is a new one: made when it comes)
        reply.parts = len(turn["items"])
        return reply

    def ask_permission(self, agent, rid, name, inp):
        """A read outside its folders, not of anything referenced: the user's asked, as Baude asks in its Manual mode,
        Allow or Deny."""
        path = str(inp.get("file_path") or inp.get("path") or inp.get("pattern") or inp.get("glob") or "")
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("tool-card")
        card.add_css_class("asking")  # (waiting on you: as a tool card asking in Baude's chat)
        verb = {"Read": "read", "Grep": "search in", "Glob": "look for files in"}.get(name, name)
        card.append(label(f"{NAME} would like to {verb} {path.replace(os.path.expanduser('~'), '~', 1)}"))
        row = Gtk.Box(spacing=6, halign=Gtk.Align.END)

        def answer(allow):
            if card in self.asking:
                self.asking.remove(card)
            if card.get_parent() is not None:
                self.box.remove(card)
            agent.answer(rid, allow, name, inp)
        for text, allow in (("Deny", False), ("Allow", True)):
            btn = Gtk.Button(label=text)
            if allow:
                btn.add_css_class("suggested-action")
            btn.connect("clicked", lambda *_, allow=allow: answer(allow))
            row.append(btn)
        card.append(row)
        card.answer = answer
        self.asking.append(card)
        self.box.append(card)
        GLib.idle_add(self._to_bottom)

    def _drop_asking(self, deny=False):
        for card in list(self.asking):
            if deny:
                card.answer(False)
            elif card.get_parent() is not None:
                self.box.remove(card)
        self.asking = []

    def finish(self, m, turn):
        """A reply kept: what the app adds to it (Record's: the edits it proposed)."""

    def worked(self, turn):
        return any(it["kind"] == "tool" for it in turn.get("items") or ())

    def _done(self, agent, text, error, turn):
        if agent is self.ask:
            self.ask, self.live_widget = None, None
            self.writing.set_busy(False)
            self._drop_asking()
        if error or text.strip() or self.worked(turn):
            m = {"role": "meta", "text": text, "t": time.time(),
                 "items": [{k: v for k, v in it.items() if k != "ran"} for it in turn["items"]]}
            for it in m["items"]:
                if it.get("state") == "running":  # (stopped before its result came)
                    it["state"] = "interrupted"
            if turn.get("actions"):
                m["actions"] = turn["actions"]  # (undo's turn: true)
            self.finish(m, turn)
            if error:  # (what it did before the error: kept, its actions undone from its cards)
                m["text"], m["error"] = error, True
        else:
            m = None
        if m is not None:
            self.store_reply(agent, m)
        self.save(now=True)
        if self.showing(agent):
            self.render()
        return False

    def store_reply(self, agent, m):
        self.messages().append(m)

    def showing(self, agent):
        """Whether the conversation agent's in is the one shown."""
        return True

    # ---- its tools
    def _guard(self, agent):
        """Why agent's tools can't act now (an error to answer with), or None."""
        if agent is not self.agent or agent.turn is None:
            return {"error": "That conversation isn't open any more: nothing was done."}
        return None

    def _tool(self, agent, tool, args):
        """Meta's tools (ToolSocket), on the main loop: run_python's code's calls of app's too."""
        if (error := self._guard(agent)) is not None:
            return error
        if tool == "run_python":
            return {"error": "run_python's code can't run Python itself."}
        return self.run_tool(agent.turn, tool, args)

    def handlers(self):
        return {"app_index": self.app_index, "set_setting": self.set_setting, "show": self.show_ui,
                "read_chat": self.read_chat, "rename_chat": self.rename_chat, "undo": self.undo_tool,
                "redo": self.redo_tool, **self.app_handlers()}

    def run_tool(self, turn, tool, args):
        """One of Meta's tools, in a turn (turn: what its reply's made of, the actions it takes among it)."""
        handler = self.handlers().get(tool)
        if handler is None:
            return {"error": f"There's no tool {tool}."}
        turn["at"] = self._item_for(turn, tool, args)
        try:
            res = handler(turn, args)
        except ValueError as e:
            res = {"error": str(e)}
        self._refresh_live(turn)
        return res

    @staticmethod
    def _item_for(turn, tool, args):
        """The card a call of Meta's tool is on: run_python's while its code runs; else the call Claude Code made
        that has yet to be answered (its arguments the same, else the first), or one made for it."""
        if turn.get("nested") is not None:
            return turn["nested"]
        waiting = [it for it in turn["items"] if it["kind"] == "tool" and not it.get("ran") and
                   it["name"].removeprefix("mcp__meta__") == tool and it.get("state") == "running"]
        item = next((it for it in waiting if it.get("input") == args), waiting[0] if waiting else None)
        if item is None:  # (the call's come in before Claude Code's said it: its card made now, _tool_started's)
            item = {"kind": "tool", "id": None, "name": tool, "input": args, "state": "running", "actions": [],
                    "shown": []}
            turn["items"].append(item)
        item["ran"] = True
        return item

    def act(self, turn, label_, kind, undo, redo, marks=()):
        """An action Meta's taken (done already): kept in the journal, under the reply it's in. undo, redo: what
        apply needs to take it back and do it again; marks: [kind, key, how, note] for the left bar's rows it changed
        (added, removed, moved)."""
        a = {"id": uuid.uuid4().hex[:6], "label": label_, "kind": kind, "undo": undo, "redo": redo, "state": "done",
             "kept": not marks, "marks": [list(m) for m in marks], "t": time.time()}
        self.actions().append(a)
        turn["actions"].append(a["id"])
        if (item := turn.get("at")) is not None:
            item.setdefault("actions", []).append(a["id"])
        self.save()
        if marks:
            self.marks_changed()
            self.show_changes_bar()
        return a

    def apply(self, kind, data):
        if kind == "setting":
            self.settings[data["name"]].set(data["value"])
        elif kind == "rename_chat":
            chat = self.find_chat(data["chat"])
            if chat is None:
                raise ValueError("That chat isn't here any more.")
            self.rename_chat_to(chat, data["title"])
        else:
            self.apply_app(kind, data)

    def undo_action(self, a, by_user=False):
        return self.undo_many([a], by_user)

    def undo_many(self, acts, by_user=False):
        """Actions taken back, the last first; said to Meta with the next message if the user did it. What can't be
        (gone since) is left as it is, and said."""
        failed = []
        for a in reversed([a for a in acts if a["state"] == "done"]):
            try:
                self.apply(a["kind"], a["undo"])
                a["state"] = "undone"
                if by_user:
                    self.told().append(f"(The user undid your action {a['id']}: {a['label']}.)")
            except (ValueError, KeyError, OSError) as e:
                failed.append(f"{a['label']}: {e}")
        self._journal_changed()
        if failed and by_user:
            widgets.toast(self, "Couldn't undo " + "; ".join(failed))
        return failed

    def redo_action(self, a, by_user=False):
        if a["state"] != "undone":
            return []
        try:
            self.apply(a["kind"], a["redo"])
        except (ValueError, KeyError, OSError) as e:
            if by_user:
                widgets.toast(self, f"Couldn't redo {a['label']}: {e}")
            return [str(e)]
        a["state"], a["kept"] = "done", not a.get("marks")
        if by_user:
            self.told().append(f"(The user redid your action {a['id']}: {a['label']}.)")
        self._journal_changed()
        return []

    def redo_many(self, acts, by_user=False):
        """Actions done again, in the order they were first done."""
        failed = []
        for a in [a for a in acts if a["state"] == "undone"]:
            failed += self.redo_action(a, by_user)
        return failed

    def keep(self, acts):
        """Changes in the left bar kept: their marks gone (undo's still under their reply)."""
        for a in acts:
            a["kept"] = True
        self._journal_changed()

    def _journal_changed(self):
        self.save()
        self.marks_changed()
        self.render()

    def unkept(self):
        return [a for a in self.actions() if a["state"] == "done" and not a.get("kept")]

    def marks(self, kind):
        """{key: (how, note, action)} of a kind of row the left bar lists (chat, page) that Meta's changed and that
        aren't kept yet, and those it's highlighted (how "highlight", no action)."""
        out = {}
        for a in self.unkept():
            for k, key, how, *note in a.get("marks") or ():
                if k == kind:
                    out[key] = (how, note[0] if note else "", a)
        for key in self.lit.get(kind, ()):
            out.setdefault(key, ("highlight", "", None))
        return out

    def _changes_bar(self):
        """Over the box asked in, while changes Meta's made in the left bar aren't kept: how many, Keep and Undo."""
        bar = Gtk.Box(spacing=2)
        bar.add_css_class("changes-bar")
        self.changes_label = label("", css=("caption",))
        self.changes_label.set_hexpand(True)
        self.changes_label.set_valign(Gtk.Align.CENTER)
        bar.append(self.changes_label)
        for text, fn in (("Keep", lambda: self.keep(self.unkept())),
                         ("Undo", lambda: self.undo_many(self.unkept(), True))):
            btn = Gtk.Button(label=text, valign=Gtk.Align.CENTER)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_, fn=fn: fn())
            bar.append(btn)
        bar.set_visible(False)
        return bar

    def show_changes_bar(self):
        n = len(self.unkept())
        self.changes_label.set_text(f"{n} change{'s' if n != 1 else ''} in the left bar")
        self.changes_box.set_visible(n > 0)

    def find_action(self, ref):
        ref = str(ref or "").strip()
        acts = self.actions()
        if ref in ("", "last"):
            return next((a for a in reversed(acts) if a["state"] == "done"), None)
        return next((a for a in acts if a["id"] == ref), None)

    def undo_tool(self, turn, args):
        a = self.find_action(args.get("action"))
        if a is None:
            raise ValueError("No such action of yours: its id is in your replies.")
        acts = [a]
        if args.get("turn"):
            turns = [m.get("actions") or [] for m in self.messages()] + [turn["actions"]]
            ids = next((ids for ids in turns if a["id"] in ids), [a["id"]])
            acts = [x for x in self.actions() if x["id"] in ids]
        if all(x["state"] != "done" for x in acts):
            return {"text": "That's undone already."}
        failed = self.undo_many(acts)
        return {"text": f"Undone: {', '.join(x['label'] for x in acts if x['state'] == 'undone')}." +
                (f" Not undone: {'; '.join(failed)}." if failed else "")}

    def redo_tool(self, turn, args):
        a = self.find_action(args.get("action"))
        if a is None or a["state"] != "undone":
            raise ValueError("No action of yours with that id is undone.")
        failed = self.redo_action(a)
        return {"error": failed[0]} if failed else {"text": f"Done again: {a['label']}."}

    # ---- the index
    def shared_sections(self):
        def settings_rows(_f):
            return [{"name": s.name, "value": s.value(), "takes": s.takes(), "title": s.title, "note": s.note}
                    for s in self.settings.values()]

        def runtime_rows(_f):
            return [{**r, "what": "process"} for r in process_rows()] + \
                [{"what": "running", "said": line} for line in self.running_lines()]

        def changelog_rows(_f):
            return [{"commit": short, "when": update.made_when(stamp), "subject": subject}
                    for short, stamp, subject in update.changelog(update.STARTED)[:60]]

        def buttons_rows(_f):
            return [{"action": k, "does": v} for k, v in self.buttons().items()]
        return {
            "settings": (settings_rows, lambda r: f"{r['name']} = {said_value(r['value'])} ({r['takes']}) · "
                                               f"{r['title']}" + (f" · {r['note']}" if r["note"] else "")),
            "runtime": (runtime_rows, lambda r: process_said(r) if r["what"] == "process" else r["said"]),
            "changelog": (changelog_rows, lambda r: f"{r['commit']} {r['when']} · {r['subject']}"),
            "buttons": (buttons_rows, lambda r: f"button:{r['action']} · {r['does']}"),
        }

    def all_sections(self):
        return {**self.sections(), **self.shared_sections()}

    def section_rows(self, section, filter_=""):
        parts = self.all_sections()
        if section not in parts:
            raise ValueError(f"No part {section!r}: its parts are {', '.join(parts)}.")
        rows_of, line_of = parts[section]
        rows, f = rows_of(filter_), (filter_ or "").strip().lower()
        return [(r, line_of(r)) for r in rows if not f or f in line_of(r).lower()]

    def app_index(self, turn, args):
        rows = self.section_rows(str(args.get("section") or "").strip(), str(args.get("filter") or ""))
        text = "\n".join(line for _r, line in rows) or "(none)"
        return {"text": trimmed(text, 12000), "data": [r for r, _line in rows]}

    def index_text(self):
        """The light index: what's running and how big, the settings, the app's own lines, and its parts to open."""
        rows = process_rows()
        me = rows[0] if rows else None
        commit = (update.STARTED or "")[:7] or "?"
        head = f"{self.APP} {commit} · pid {os.getpid()}" + \
            (f" · {me['memory_mb']} MB · up {me['up']}" if me else "") + \
            f" · {len(rows) - 1} child processes" * bool(rows)
        settings = "settings: " + " ".join(f"{s.name}={said_value(s.value())}" for s in self.settings.values())
        lines = [head, settings, *self.index_lines()]
        running = self.running_lines()
        if running:
            lines.append("running: " + " · ".join(running))
        lines.append(f"parts (app_index): {', '.join(self.all_sections())}")
        return "\n".join(lines)

    # ---- the shared tools
    def set_setting(self, turn, args):
        name = str(args.get("name") or "").strip()
        s = self.settings.get(name)
        if s is None:
            raise ValueError(f"No setting {name!r}: they're {', '.join(self.settings)}.")
        if not s.meta_sets:
            raise ValueError(f"{name} is the user's to change: show setting:{name} for them to.")
        before = s.value()
        now = s.set(args.get("value"))
        self._show(turn, {"kind": "setting", "name": name, "set": now})
        if now == before:
            return {"text": f"{name} is {said_value(now)} already. Its row is under your reply."}
        a = self.act(turn, f"{s.title}: {said_value(before)} → {said_value(now)}", "setting",
                     {"name": name, "value": before}, {"name": name, "value": now})
        return {"text": f"{name} is {said_value(now)} now (action {a['id']})" + (f"; {s.note}" if s.note else "") +
                ". Its row is under your reply."}

    def show_ui(self, turn, args):
        what, items = str(args.get("what") or "").strip(), [str(i) for i in args.get("items") or []]
        kind, _, rest = what.partition(":")
        if kind == "setting":
            if rest not in self.settings:
                raise ValueError(f"No setting {rest!r}: they're {', '.join(self.settings)}.")
            spec = {"kind": "setting", "name": rest}
        elif kind in ("chats", "pages"):
            (self.chats_rows if kind == "chats" else self.pages_rows)(items)  # (raises if one isn't there)
            spec = {"kind": kind, "items": items}
        elif kind == "changelog":
            spec = {"kind": "changelog"}
        elif kind == "button":
            self.button(rest, items)
            spec = {"kind": "button", "action": rest, "items": items}
        else:
            raise ValueError("show takes setting:<name>, chats, pages, changelog or button:<action>.")
        self._show(turn, spec)
        return {"text": "Shown under your reply."}

    @staticmethod
    def _show(turn, spec):
        """UI under the card of the call that showed it."""
        turn["shown"].append(spec)
        if (item := turn.get("at")) is not None:
            item.setdefault("shown", []).append(spec)

    def _chat(self, ref):
        chat = self.find_chat(str(ref or "").strip())
        if chat is None:
            raise ValueError(f"No chat {ref!r}: app_index chats lists them, each with its id.")
        return chat

    def read_chat(self, turn, args):
        chat = self._chat(args.get("chat"))
        return {"text": f'<chat title="{attr(chat["title"])}">\n{self.chat_text(chat)}\n</chat>'}

    def rename_chat(self, turn, args):
        chat, title = self._chat(args.get("chat")), " ".join(str(args.get("title") or "").split())
        if not title:
            raise ValueError("A title is needed.")
        before = chat["title"]
        if title == before:
            return {"text": "It's called that already."}
        self.rename_chat_to(chat, title)
        a = self.act(turn, f"Renamed “{before}” to “{title}”", "rename_chat", {"chat": chat["key"], "title": before},
                     {"chat": chat["key"], "title": title},
                     marks=[("chat", chat["key"], "moved", "renamed")] if self.CHAT_ROWS else ())
        return {"text": f"Renamed (action {a['id']})."}

    def _python(self, agent, args):
        """run_python, on the socket's thread (ToolSocket): its code checked on the main loop and its card found
        (_python_start), run in a Python of its own (run_isolated) whose calls to app the main loop answers as it's
        free to, and what it printed kept with its card (_python_end)."""
        code = str(args.get("code") or "")
        started = ToolSocket.main(self._python_start, agent, code) or {"error": f"{NAME} didn't answer: try again"}
        if started.get("error"):
            return started
        out = "(stopped)"
        try:
            out = run_isolated(code, started["tools"], ToolSocket.get().path, agent.token)
        finally:
            done = ToolSocket.main(self._python_end, agent, code, out)
        return done or {"text": out}

    def _python_start(self, agent, code):
        if (error := self._guard(agent)) is not None:
            return error
        tools = {t["name"]: list((t.get("inputSchema") or {}).get("properties") or {}) for t in self.all_tools()}
        try:
            check_code(code, tools)
        except ValueError as e:
            return {"error": str(e)}
        turn = agent.turn
        turn["nested"] = turn["at"] = self._item_for(turn, "run_python", {"code": code})  # (its calls: on its card)
        return {"tools": tools}

    def _python_end(self, agent, code, out):
        if (turn := agent.turn) is not None:
            turn["nested"] = None
            turn["code"].append({"code": code, "output": out})
            self._refresh_live(turn)
        return {"text": out}
