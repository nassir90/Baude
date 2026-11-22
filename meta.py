"""meta — Meta, the helper in Baude's and Record's right bar: one agent that operates the app it's in.

Meta is a Claude Code session (`claude -p` speaking stream-json, baudelib's ClaudeProc, as Baude runs its chats), on
Haiku unless Settings → Meta says another. It reads with Claude Code's own Read, Grep and Glob, and acts with the app's
tools, which come in on a Unix socket of the app's (ToolSocket) through a relay Claude Code runs as an MCP server (RELAY).
On Claude's models it also runs commands, edits files and looks things up on the web (COMMAND_TOOLS), in Claude Code's
Auto mode, unless Settings → Meta → Run commands is off; other models only read (runs_commands):

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
index (sections), and how its actions are undone (apply_app). Nothing here is Baude's or Record's own.

Meta's session and its tools are in metaagent.py, how its column looks in metalook.py, the column in metachat.py
(MetaChat), with its cards in metacards.py and its tools' handling in metatools.py."""
import ast
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

import fontsetup  # noqa: E402
import lookup  # noqa: E402
import lookupgit  # noqa: E402
import update  # noqa: E402
import widgets  # noqa: E402
import chatparts  # noqa: E402
import pickers  # noqa: E402
import theming  # noqa: E402
from baudelib import codex, wire, wording  # noqa: E402
from baudelib import models as modelslib  # noqa: E402
from baudelib import providers as providerslib  # noqa: E402
from baudelib.claude import EFFORT_LABELS, MODELS  # noqa: E402
from baudelib.models import model_program  # noqa: E402

NAME = "Meta"
MODEL = "haiku"  # its model, unless Settings → Meta → Intelligence says another (settings.json meta_model)
MODELS_OFFERED = list(reversed(MODELS))  # ((value, label): what Intelligence offers till claude's listed its own)
MODEL_KEYS = ("value", "displayName", "resolvedModel", "description", "supportsEffort",
              "supportedEffortLevels")  # (what's kept of each: models; what it resolves to, to tell it's the same one)
CODE_LINES = 10  # the most lines of Python run_python runs
READ_TOOLS = ("Read", "Grep", "Glob")  # Claude Code's own: those that only read
# Claude Code's own that act, given on Claude's models (runs_commands), in its Auto mode: what its classifier holds back
# is put to the user (MetaChat.ask_permission)
COMMAND_TOOLS = ("Bash", "Edit", "Write", "WebFetch", "WebSearch")
EDIT_TOOLS = ("Edit", "Write")  # (those of them that write files: never under the app's own (MetaChat.agent_setup's protect))
NO_AUTO = set()  # (models claude started in another mode when Meta asked for Auto: each command put to the user)
# its session's environment: its MCP tools loaded at the start (not through a ToolSearch turn), no auto memory, no
# claude.ai connectors, no telemetry or update checks (as Baude's Signal reader has it)
AGENT_ENV = {"ENABLE_TOOL_SEARCH": "false", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
             "ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
REF_LIMIT = 20000  # characters of a referenced page's, file's or chat's text put in with a message (past it: where to Read)
EARLIER_LIMIT = 16000  # of what was said before a session started, with its first message
# how many tokens a session's window holds, by what runs its model, till the session says (get_context_usage): claude's
# (1M for a [1m] model's), pi-claude's (its CONTEXT), and the least Apple's on-device model has had
WINDOWS = {"claude": 200_000, "codex": 200_000, "pi": 32_768, "apple": 4_096}
CHARS_PER_TOKEN = 3  # (a token's characters, at the fewest, in ASCII; a character beyond it, a token: weight)
# a turn's error that says the session's window was outgrown: claude's ("Prompt is too long"), Apple's model's, those
# MLX serves (pi-claude's, from the server), and others' wordings of it
OVERFLOW_RE = re.compile(r"prompt is too long|context (window|length|size)|exceed\w*[^.]{0,40}\b(context|tokens)\b|"
                         r"too many tokens|maximum context", re.I)
REF_RE = re.compile(r"(?<![\w@])@(?:\{([^}\n]+)\}|([\w][\w./:\-]*[\w/]|\w))")  # (@name, @{a name})
REF_COLOR = "#2f8f72"  # (an @name: the notes' colour for one)
MARK_HOWS = ("added", "removed", "moved", "highlight")


UNAVAILABLE = wording.say("meta.unavailable")  # (Meta's, gone from the mesh: over its box, as Context Exhausted is)
TOO_SMALL = wording.say("meta.too_small")  # (its instructions alone, as it counts
# them, more than it holds: no clearing helps)
HERE_NAME = wording.say("senders.local")  # (this machine, as every host is written: by its name, which here is local)
PROVIDERS_WAIT = 0.5  # (s Settings waits for this machine's providers the first time: the rest come as they're found)


_codex_catalog = None
_codex_discovery_at = 0.0


def set_codex_catalog(catalog):
    """Use the local Baude host's chat catalog for Meta as well."""
    global _codex_catalog
    catalog = [m for m in catalog if model_program(m["value"]) == "codex"]
    if catalog != _codex_catalog:
        _codex_catalog = catalog
        sync_rows()
    return False


def codex_catalog():
    """Discover models in standalone Record too, where Baude's network isn't running."""
    global _codex_discovery_at
    if _codex_catalog is None and time.monotonic() >= _codex_discovery_at and (exe := modelslib.codex_exe()):
        _codex_discovery_at = time.monotonic() + 60

        def discover():
            try:
                channel, info = wire.connect_local(timeout=0.5)
            except OSError:
                info = {}
            else:
                channel.close()
            catalog = [m for m in info.get("models", []) if model_program(m["value"]) == "codex"]
            if not any(m["value"] != "codex/default" for m in catalog):
                try:
                    catalog = modelslib.codex_choices(codex.list_models(exe))
                except (OSError, RuntimeError):
                    return
            GLib.idle_add(lambda: set_codex_catalog(catalog) if _codex_catalog is None else False)

        threading.Thread(target=discover, daemon=True, name="meta-codex-catalog").start()
    return _codex_catalog or []


_CLAUDE_SAID = {}  # (claude's models as models() last read them, by value: what each resolves to, its description)


def models(live=False):
    """What Intelligence offers, as (value, label, effort levels or None, provider or None): the models this machine's
    claude listed last (settings.json's claude_models, as Meta's sessions and Baude's chats here have them from claude),
    in its order (MODELS_OFFERED till it has); then those of this machine's inference providers that call tools
    (baudelib.models.runnable_models: Apple's once apple-claude's built, MLX's that are downloaded, Ollama's, an
    OpenAI-compatible server's), as Baude's chats offer them; and every other host's in the mesh: one that serves an API
    run here, through the mesh's proxy (providers.mesh_endpoints), any other run there as an agent whose tools are
    Meta's here (providers.mesh_agents: agentproxy). Claude's have no provider: claude runs them, wherever it is. And
    Meta's own, if it's none of these (unavailable), unless live."""
    listed = [m for m in widgets.load_settings().get("claude_models") or []
              if isinstance(m, dict) and isinstance(m.get("value"), str) and m["value"] != "default"]
    _CLAUDE_SAID.clear()
    _CLAUDE_SAID.update((m["value"], m) for m in listed)  # (for model_entry: no settings read for each it's asked)
    out = [(m["value"], m.get("displayName") or m["value"],
            list(m.get("supportedEffortLevels") or []) if m.get("supportsEffort") else None, None) for m in listed] or \
        [(value, lbl, None, None) for value, lbl in MODELS_OFFERED]
    out += [(m["value"], m["displayName"], m.get("supportedEffortLevels") or None, None)
            for m in codex_catalog() if m["value"] != "codex/default"]
    out += [(m["value"], m["displayName"], list(m.get("supportedEffortLevels") or []) if m.get("supportsEffort") else None,
             m.get("provider") or providerslib.provider_of(m["value"]))
            for m in modelslib.runnable_models(tools=True, wait=PROVIDERS_WAIT)]
    out += [(f"openai/{e['id']}/{m['id']}", m.get("displayName") or m["id"],
             list(modelslib.EFFORT["supportedEffortLevels"]) if m.get("thinking") is True else None, f"openai/{e['id']}")
            for e in providerslib.mesh_endpoints() for m in e["models"] if m.get("tools", True)]
    out += [(m["value"], m["displayName"], None, m["provider"])
            for m in providerslib.mesh_agents() if m.get("tools", True)]  # (another host's that serves no API: run
    # there, its tools Meta's here)
    if live:
        return out
    chosen = widgets.load_settings().get("meta_model")  # (one picked from another claude's list, or a model this Mac
    if isinstance(chosen, str) and chosen and model_entry(chosen, out) is None:  # ran then: still there to see)
        out.append((chosen, local_name(chosen), None, providerslib.provider_of(chosen)))
    return out


def available(value=None):
    """Is a model (Meta's, if none's given) one Meta can run now: Claude's, or one in the mesh still (models, live)?
    One that's gone (its host left the mesh, its model removed) is listed still, as unavailable."""
    value = model() if value is None else value
    return model_program(value) == "claude" or model_entry(value, models(live=True)) is not None


def local_name(value):
    """A model's name where it's one this Mac runs besides Claude's (Apple's, MLX's, Ollama's, an endpoint's), else
    value."""
    found = [modelslib.APPLE_MODEL, *modelslib.mlx_models()]
    return next((m["displayName"] for m in found if m["value"] == value), None) or providerslib.model_name(value)


def listed_models(offered=None):
    """What Intelligence lists: Claude's models, then every other model of models() (this machine's first, then the
    mesh's), one of each kind (alike: the same model on two hosts, or served two ways, is one entry): Meta's own where
    it's of that kind, else the first that has it, which a pick puts Meta on. Meta's own is listed last if its host's
    gone and no other has it. Where two of a name aren't the same, each is told apart (providers.told_apart)."""
    offered = models() if offered is None else offered
    known = providerslib.entries()
    by_value = {}
    for m in offered:
        by_value.setdefault(m[0], m)
    out = [m for m in by_value.values() if m[3] is None] + [by_value[first["value"]] for first, _ms in alike(offered, known)]  # (each where its kind's first is)
    others = [described(m, known) for m in out if m[3] is not None]
    return [(m[0], providerslib.told_apart(described(m, known), others), *m[2:]) if m[3] is not None else m
            for m in out]


def claude_described(offered):
    """Entries of models() without a provider (Claude's, codex's) as claude listed them: {value, displayName}, and what
    each resolves to and its description where claude said them (_CLAUDE_SAID, as models() last read them): to tell
    which row a model said another way is (models.claude_match: claude-opus-5-5 is opus, "Opus 5.5 · …")."""
    return [{k: v for k, v in (_CLAUDE_SAID.get(m[0]) or {}).items() if k in ("resolvedModel", "description")} |
            {"value": m[0], "displayName": m[1]} for m in offered]


def described(m, known=None):
    """An entry of models() as providers has a model: {value, displayName, provider}, and what it's served as, its
    window, its version and its digest where they're said (known: providers.entries())."""
    known = providerslib.entries() if known is None else known
    return {"value": m[0], "displayName": m[1], "provider": m[3]} | \
        {k: v for k, v in (known.get(m[0]) or {}).items()
         if k in ("resolvedModel", "id", "window", "version", "digest")}


def alike(offered=None, known=None):
    """The models of offered (models()) with a provider, one of each kind (providers.alike): [(the one listed, every one
    of its kind)], as described has them, in offered's order. The one listed is Meta's own model where it's of its
    kind, else the first (this machine's, where it has it)."""
    known = providerslib.entries() if known is None else known
    return providerslib.alike([described(m, known) for m in (models() if offered is None else offered)
                               if m[3] is not None], [model()])


def listed(found, seed=False):
    """claude's initialize listed its models (found): those claude runs, kept as what Intelligence offers (models).
    Meta's own sessions keep theirs; seed (a chat of Baude's here) keeps them while there are none yet, or while those
    kept lack what claude says of each now (an older version's: no description, so claude-opus-5-5 isn't told to be
    opus)."""
    keep = [{k: m[k] for k in MODEL_KEYS if k in m} for m in found or []
            if isinstance(m, dict) and m.get("value") and m["value"] != "default" and model_program(m["value"]) == "claude"]
    had = widgets.load_settings().get("claude_models")
    said = {k for m in keep for k in m}  # (a list kept by an older version, without what claude says now of each,
    old = isinstance(had, list) and said - {k for m in had if isinstance(m, dict) for k in m}  # its description:
    if keep and keep != had and (not (seed and had) or old):  # replaced, seed or not)
        widgets.save_settings(claude_models=keep)


def model_entry(value, offered=None):
    """The entry of offered (models()) for a model value: its own; else Claude's that's the same one said another way
    (models.claude_match: opus, "Opus 5.5 · …", for claude-opus-5-5; while claude's listed none, the one alias in
    its id); else, for an alias (haiku), the first listed model
    with it as a word of its name (claude-haiku-4-5, haiku[1m]); None if none."""
    offered = models() if offered is None else offered
    family = re.compile(rf"(^|-){re.escape(value)}(-|\[|$)")
    if hit := next((m for m in offered if m[0] == value), None):
        return hit
    same = modelslib.claude_match(value, claude_described([m for m in offered if m[3] is None]),
                                  fallback=not _CLAUDE_SAID)  # (none listed yet: MODELS_OFFERED's aliases)
    return next((m for m in offered if same and m[0] == same["value"]), None) or \
        (next((m for m in offered if family.search(m[0])), None) if value in dict(MODELS) else None)


def model():
    """The model Meta runs on: settings.json's meta_model (picked from a list claude gave, and kept while another
    claude lists others; or one this Mac runs besides), else Haiku."""
    chosen = widgets.load_settings().get("meta_model")
    return chosen if isinstance(chosen, str) and chosen else MODEL


def model_value():
    """model() as Intelligence lists it (claude-haiku-4-5 for haiku, where it's listed so)."""
    return (model_entry(model()) or (model(),))[0]


def model_name(value=None):
    """A model's name as Intelligence lists it (Meta's own, if none's given)."""
    value = model() if value is None else value
    entry = model_entry(value)
    return entry[1] if entry else local_name(value)


def efforts():
    """The effort levels Meta's model takes (none: [])."""
    entry = model_entry(model())
    return list(entry[2] or []) if entry else []


def effort():
    """How hard Meta thinks: settings.json's meta_effort if its model takes it, else None (claude's own default)."""
    chosen = widgets.load_settings().get("meta_effort")
    return chosen if chosen in efforts() else None


def window(value, said=None):
    """How many tokens a model's session holds, till it says: its provider's say (Ollama's, an endpoint's, another
    host's), else what its last session said (modelslib.said_window, by said: its value, unless it's kept by
    another key: Baude's chat on another host's), else WINDOWS'."""
    program = model_program(value)
    said = (modelslib.window_of(value) if program != "claude" else None) or modelslib.said_window(said or value)
    if said:
        return said
    if program == "claude":
        return 1_000_000 if "[1m]" in value else WINDOWS[program]
    return WINDOWS[program]


def weight(text):
    """text's size as a window's room is counted (Agent.first): its ASCII characters, and each other one as
    CHARS_PER_TOKEN (a token of its own, at most: Chinese, say)."""
    return len(text) + (CHARS_PER_TOKEN - 1) * (len(text) - len(text.encode("ascii", "ignore")))


def code_mode():
    """Whether Meta's tools are run_python alone (Settings → Meta → Code mode)."""
    return bool(widgets.load_settings().get("meta_code_mode"))


def commands_on():
    """Settings → Meta → Run commands: on unless it's been turned off."""
    return widgets.load_settings().get("meta_commands") is not False


def takes_commands(value=None):
    """Whether a model (Meta's own, if none's given) is one Meta runs commands on: Claude's, run by claude here.
    Smaller models, run by pi or Apple's adapter or on another host, only read."""
    value = model() if value is None else value
    return model_program(value) == "claude" and modelslib.proxied(value) is None


def runs_commands():
    """Whether Meta's next session runs commands (COMMAND_TOOLS, in Auto mode): on Claude's models, Run commands on."""
    return commands_on() and takes_commands()


def auto_mode_note(value=None):
    """What to say when claude didn't start a model (Meta's own, if none's given) in Auto mode (NO_AUTO), else ""."""
    value = model() if value is None else value
    return wording.say("meta_settings.no_auto", model=model_name(value)) if value in NO_AUTO else ""


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
            part = lookup._CATALOGUE_TOKEN.sub(keep, lookupgit.GITHUB_NAME.sub(keep, part))
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
    more = len(text) - limit
    return text[:limit].rstrip() + "\n" + (wording.say("report.more_characters_read", n=more, where=where) if where else
                                          wording.say("report.more_characters", n=more))


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


REPORT_ITEM = 4000  # (characters of each tool call's input, and of its result, in a copied conversation)


def report_most():
    """How long a copied conversation is at most: what an issue's body holds, with room for the note around it."""
    from feedback import ISSUE_BODY_MOST
    return ISSUE_BODY_MOST - 5000


def report_head_text(head):
    """head, [(name, value)], as lines, the values in a column (those with none left out)."""
    head = [(k, v) for k, v in head if v not in (None, "")]
    width = max((len(k) for k, _ in head), default=0)
    return "\n".join(f"{k + ':':<{width + 1}} {v}" for k, v in head)


def capped_report(head, parts, most=None):
    """head's lines, then the parts (each a message, as text), as many of the last as fit in most (report_most): its
    end kept, as transcript_text keeps a transcript's, and how many were left out said."""
    most = report_most() if most is None else most
    top = report_head_text(head)
    kept, size = [], len(top)
    for part in reversed(parts):
        if size + len(part) + 2 > most - 200:
            if not kept:  # (the last by itself is too long: its end)
                kept.append("[…]" + part[-(most - 200 - size):])
            break
        kept.append(part)
        size += len(part) + 2
    cut = len(parts) - len(kept)
    return "\n\n".join([top] + ([wording.say("report.left_out", n=cut)] if cut else [])
                        + list(reversed(kept))).strip() + "\n"


def report_text(head, messages, limit=REPORT_ITEM, most=None):
    """A conversation as text to paste into an issue (Copy, under the box; a note sent with Send Feedback): head,
    [(name, value)], the lines over it (the app, the model and what runs it, its effort and modes, its tools, its
    context); then who said what (each reply with the model that said it), each tool call with its input and what it
    gave back (each as far as limit), each error said as one; its end, if it's longer than most (capped_report)."""
    parts = []
    for m in messages:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m["t"])) if m.get("t") else ""
        who = wording.say("report.user") if m.get("role") == "user" else (
            wording.say("report.meta_model", model=m["model"]) if m.get("model") else wording.say("meta_settings.title")) + \
            (" " + wording.say("report.error") if m.get("error") else "")
        out = [f"## {who}" + (f" · {when}" if when else "")]
        if m.get("role") == "user":
            if m.get("quote"):
                out.append("> " + "\n> ".join(m["quote"].splitlines()))
            out.append(m.get("text") or "")
            parts.append("\n".join(out))
            continue
        for text in m.get("warned") or ():
            out.append(f"({text})")
        items = m.get("items")
        if items is None:  # (a reply kept from before its parts were, an error's too: its text is its one part)
            items = [{"kind": "text", "text": m["text"]}] if m.get("text") else []
        for it in items:
            if it["kind"] == "text":
                if it.get("text", "").strip():
                    out.append(it["text"].strip())
                continue
            if it["kind"] == "compact":
                done = it.get("state") == "done"
                out.append(f"[{chatparts.compact_text(it.get('meta')) if done else 'compaction ' + it.get('state', '?')}]")
                continue
            inp = json.dumps(it.get("input") or {}, ensure_ascii=False, indent=1)
            out.append(wording.say("report.meta_tool", name=it.get("name") or "?", state=it.get("state") or "?")
                       + "\n" + trimmed(inp, limit))
            if it.get("result"):
                out.append(wording.say("report.result") + "\n" + trimmed(str(it["result"]), limit))
        if m.get("error") and m.get("text") and m.get("items") is not None:
            out.append(m["text"])
        parts.append("\n".join(out))
    return capped_report(head, parts, most)


def transcript_report(head, path, limit=REPORT_ITEM, most=None):
    """A Claude Code chat's transcript (path) as text for an issue, as report_text's: head's lines, then who said what,
    each tool call with its whole input, its result and whether it failed (each as far as limit); its end, if it's
    longer than most."""
    parts, calls = [], {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                if e.get("type") == "system" and e.get("subtype") == "compact_boundary" and not e.get("isSidechain"):
                    when = (e.get("timestamp") or "").replace("T", " ")[:19]
                    parts.append(f"## {chatparts.compact_text(e.get('compactMetadata'))}" + (f" · {when}" if when else ""))
                    continue
                if e.get("type") not in ("user", "assistant") or e.get("isSidechain") or e.get("isMeta"):
                    continue
                msg = e.get("message") or {}
                if e.get("isCompactSummary"):  # (what that compaction wrote: under its heading)
                    content = msg.get("content")
                    text = content if isinstance(content, str) else "\n".join(
                        b.get("text") or "" for b in content or () if isinstance(b, dict) and b.get("type") == "text")
                    said = f"{wording.say('report.summary')}\n{trimmed(text.strip(), limit)}"
                    if parts and parts[-1].startswith("## Conversation compacted") and "\n" not in parts[-1]:
                        parts[-1] += "\n" + said
                    else:
                        parts.append("## " + said)
                    continue
                content, out = msg.get("content"), []
                for c in [{"type": "text", "text": content}] if isinstance(content, str) else content or []:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "text" and (c.get("text") or "").strip():
                        out.append(c["text"].strip())
                    elif c.get("type") == "tool_use":
                        calls[c.get("id")] = c.get("name")
                        inp = json.dumps(c.get("input") or {}, ensure_ascii=False, indent=1)
                        out.append(f"{wording.say('report.tool_call', name=c.get('name'))}\n{trimmed(inp, limit)}")
                    elif c.get("type") == "tool_result":
                        res = c.get("content")
                        res = res if isinstance(res, str) else "\n".join(
                            b.get("text") or f"[{b.get('type')}]" for b in res or () if isinstance(b, dict))
                        name = calls.get(c.get("tool_use_id")) or wording.say("report.unknown_tool")
                        out.append(f"{wording.say('report.tool_error' if c.get('is_error') else 'report.tool_result', name=name)}"
                                   f"\n{trimmed(res, limit)}")
                if not out:
                    continue
                results = e["type"] == "user" and all(isinstance(c, dict) and c.get("type") == "tool_result"
                                                      for c in content or ()) and not isinstance(content, str)
                who = wording.say("report.tool_results") if results else wording.say("report.user") if e["type"] == "user" \
                    else (wording.say("report.claude_model", model=msg["model"])
                          if msg.get("model") and msg["model"] != "<synthetic>" else wording.say("report.claude")) + \
                    (" " + wording.say("report.api_error") if e.get("isApiErrorMessage") else "")
                when = (e.get("timestamp") or "").replace("T", " ")[:19]
                parts.append(f"## {who}" + (f" · {when}" if when else "") + "\n" + "\n".join(out))
    except OSError as err:
        parts.append(wording.say("report.not_read", why=err))
    return capped_report(head, parts or [wording.say("report.nothing_said")], most)


def earlier_said(messages, limit=EARLIER_LIMIT, where=lambda m: ""):
    """What was said before this session (a new one: the app started again, another model), the newest as far back
    as limit; where(m): what goes before a message's text (Record's: the notebook it was asked in)."""
    lines, size = [], 0
    for m in reversed([m for m in messages if not m.get("error")]):
        who = "User" if m.get("role") == "user" else NAME
        body = where(m) + (m.get("text") or "")
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
    row Settings has for it (row), and what it takes: values a list of names (or a function giving them, where they
    change), (low, high) for a size, or bool. note:
    what to know of it (the code font takes from the next start)."""

    def __init__(self, name, title, get, put, row, values, note="", meta_sets=True):
        self.name, self.title, self.get, self.put, self.row, self.values, self.note = \
            name, title, get, put, row, values, note
        self.meta_sets = meta_sets  # (False: Meta only shows it, the user changes it)

    def options(self):
        """values as they are now (a list's given as a function where it changes: the models claude lists)."""
        return self.values() if callable(self.values) and self.values is not bool else self.values

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
        found = next((v for v in self.options() if str(v).lower() == str(value).strip().lower()), None)
        if found is None:
            raise ValueError(f"{self.name} is one of: {', '.join(map(str, self.options()))}.")
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
        return "|".join(map(str, self.options()))


EFFORT_ROWS = weakref.WeakSet()  # (the Effort rows up: each offers the model's levels, model_picked)
COMMAND_ROWS = weakref.WeakSet()  # (the Run commands rows up: each greyed on a model that only reads)
MODEL_ROWS = weakref.WeakSet()  # (the Intelligence and Provider rows up: each lists what's picked now)


def sync_rows():
    """Every Provider, Intelligence, Effort and Run commands row up as it is now (after the row picked in has had its say)."""
    def sync():
        for row in [*MODEL_ROWS, *EFFORT_ROWS, *COMMAND_ROWS]:
            row.sync()
        return False
    GLib.idle_add(sync)


providerslib.listen(sync_rows)  # (this machine's providers found again, and changed: the rows as they are now)

CHATS = weakref.WeakSet()  # (the MetaChats up: each told as its model changes in another app, settings_changed)
META_SETTINGS = {"meta_model", "meta_effort", "meta_code_mode", "meta_commands"}


def settings_changed(keys, _before):
    """Meta's model, effort, Code mode or Run commands changed in another app here (theming.watch_settings): every row up
    shows it, and each conversation's next message is the new model's."""
    if keys & META_SETTINGS:
        sync_rows()
        for chat in list(CHATS):
            chat.model_changed()


def model_picked(chat, value):
    """Intelligence set to value (its row, set_setting; or Provider to another copy of Meta's model): Meta's next
    message is the new model's, and every row shows it (Effort its levels, Provider where it runs)."""
    widgets.save_settings(meta_model=value)
    sync_rows()
    if chat is not None:
        chat.model_changed()


def model_row(chat):
    """Settings → Meta → Intelligence: the model Meta thinks with, from its next message: Claude's, then every
    other in the mesh (listed_models; where it runs: provider_row), in a search box's list with each family's icon
    (pickers.ModelPicker). Its sync() lists them as they are now (sync_rows)."""
    row = Adw.ActionRow(title=wording.say("meta_settings.intelligence"),
                        subtitle=wording.say("meta_settings.intelligence_detail"))
    picker = pickers.ModelPicker([], wording.say("meta_settings.intelligence_detail"), lambda v: model_picked(chat, v))
    picker.set_valign(Gtk.Align.CENTER)
    picker.add_css_class("flat")
    row.add_suffix(picker)
    row.set_activatable_widget(picker)

    def sync():
        offered = listed_models()
        entry = model_entry(model(), offered) or (offered[0] if offered else None)
        picker.quiet = True
        try:
            picker.set_choices([(m[0], m[1]) for m in offered])
            picker.select(entry[0] if entry else None)
        finally:
            picker.quiet = False
    row.sync = sync
    sync()
    MODEL_ROWS.add(row)
    return row


def provider_row(chat):
    """Settings → Meta → Provider: where Meta's model runs, each provider that has it (alike: the same model on another
    host, or served another way), by host: this machine's (local) first. Picking another moves Meta's model there,
    from its next message. Greyed and off with one; gone on Claude's (claude runs where it is: no provider of its)."""
    row = Adw.ActionRow(title=wording.say("meta_settings.provider"),
                        subtitle=wording.say("meta_settings.provider_detail"))
    same = {}  # (each provider that has Meta's model: its copy there)
    picker = pickers.HostPicker(wording.say("meta_settings.provider_picker"), lambda pid: model_picked(chat, same[pid])
                                if pid in same and same[pid] != model() else sync_rows())
    picker.set_valign(Gtk.Align.CENTER)
    picker.add_css_class("flat")
    row.add_suffix(picker)
    row.set_activatable_widget(picker)

    def sync():
        offered = models()
        entry = model_entry(model(), offered)
        pid = entry[3] if entry else providerslib.provider_of(model())
        ms = next((ms for _m, ms in alike(offered) if any(m["value"] == entry[0] for m in ms)), None) \
            if entry and pid is not None else None
        same.clear()
        there = {HERE_NAME: []}  # (this machine's first)
        for m in ms or ([{"value": model(), "provider": pid}] if pid is not None else []):
            if m["provider"] in same:
                continue
            host, name = providerslib.where(m["provider"])
            there.setdefault(host or HERE_NAME, []).append((m["provider"], name))
            same[m["provider"]] = m["value"]
        picker.set_choices([(host, choices) for host, choices in there.items() if choices])
        picker.select(pid)
        row.set_sensitive(len(same) > 1)  # (one provider has it: greyed, off)
        row.set_visible(pid is not None)  # (Claude's: none)
    row.sync = sync
    sync()
    MODEL_ROWS.add(row)
    return row


def effort_row(chat):
    """Settings → Meta → Effort: how hard Meta thinks, of the levels its model takes, from its next message. Its sync()
    offers another model's levels (model_picked)."""
    row = Adw.ComboRow(title=wording.say("meta_settings.effort"))
    state = {"values": [None], "syncing": False}

    def sync():
        levels = efforts()
        state["values"] = [None, *levels]
        state["syncing"] = True
        row.set_model(Gtk.StringList.new([wording.say("meta_settings.effort_default"),
                                          *(EFFORT_LABELS.get(lv, lv.title()) for lv in levels)]))
        row.set_selected(state["values"].index(effort()))
        state["syncing"] = False
        row.set_sensitive(bool(levels))
        row.set_subtitle(wording.say("meta_settings.effort_detail") if levels else
                         wording.say("meta_settings.no_effort", model=model_name()))

    def picked(r, _p):
        if not state["syncing"] and r.get_selected() < len(state["values"]):
            widgets.save_settings(meta_effort=state["values"][r.get_selected()])
            if chat is not None:
                chat.model_changed()
    row.connect("notify::selected", picked)
    row.sync = sync
    sync()
    EFFORT_ROWS.add(row)
    return row


def code_mode_row(chat):
    """Settings → Meta → Code mode: Meta acts by writing a few lines of Python (run_python), from its next message."""
    row = Adw.SwitchRow(title=wording.say("meta_settings.code_mode"),
                        subtitle=wording.say("meta_settings.code_mode_detail", n=CODE_LINES),
                        active=code_mode())
    row.connect("notify::active", lambda r, _p: r.syncing or (widgets.save_settings(meta_code_mode=r.get_active()),
                                                              chat is not None and chat.model_changed()))
    return theming.follow_setting(row, "meta_code_mode", lambda v: row.set_active(bool(v)))


def commands_row(chat):
    """Settings → Meta → Run commands: on Claude's models, Meta runs shell commands, edits files and looks things up
    on the web, in Claude Code's Auto mode, from its next message. Greyed on a model that only reads (takes_commands);
    its sync() says which (sync_rows)."""
    row = Adw.SwitchRow(title=wording.say("meta_settings.run_commands"))
    row.connect("notify::active", lambda r, _p: r.syncing or (widgets.save_settings(meta_commands=r.get_active()),
                                                              chat is not None and chat.model_changed()))

    def sync():
        takes = takes_commands()
        was, row.syncing = row.syncing, True
        row.set_active(takes and commands_on())  # (off, greyed, on a model that only reads: the setting kept)
        row.syncing = was
        row.set_sensitive(takes)
        row.set_subtitle((wording.say("meta_settings.asks_each", note=auto_mode_note()) if auto_mode_note() else
                          wording.say("meta_settings.run_commands_detail")) if takes else
                         wording.say("meta_settings.only_reads", model=model_name()))
    row.sync = sync
    theming.follow_setting(row, "meta_commands", lambda _v: sync())
    sync()
    COMMAND_ROWS.add(row)
    return row


def settings_group(chat):
    """Settings → Meta, in Baude's and Record's Settings: Intelligence, Provider, Effort, Run commands and Code mode.
    They follow this machine's providers as they're found (Ollama started, a model pulled)."""
    group = Adw.PreferencesGroup(title=wording.say("meta_settings.title"))
    group.add(model_row(chat))
    group.add(provider_row(chat))  # (where the model above runs)
    group.add(effort_row(chat))
    group.add(commands_row(chat))
    group.add(code_mode_row(chat))
    return group


def shared_settings(win, chat, app, zoom_default):
    """The settings Baude and Record share (settings.json's), as Meta changes them: win's set_scale, set_zoom_default
    and its fullwidth action; app the one it's in ("Baude", "Record"); zoom_default() the Global Scale."""
    display = win.get_display()

    def accent(v):
        widgets.save_settings(accent=None if v == "system" else v)
        theming.apply_accent(display)

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
        Setting("accent", wording.say("shared_settings.accent_colour"), lambda: widgets.load_settings().get("accent") or "system",
                accent, theming.settings_accent, ["system", *widgets.ACCENTS]),
        Setting("code_font", wording.say("shared_settings.code_font"), fontsetup.code_font,
                lambda v: widgets.save_settings(code_font=v),
                lambda: theming.settings_code_font(win, app), fonts(),
                note=f"takes from {app}'s next start: its row has Restart"),
        Setting("text_scale", wording.say("meta_settings.text_scale"), lambda: widgets.scale_setting("text_scale"),
                lambda v: win.set_scale("text_scale", v),
                lambda: theming.settings_size(wording.say("meta_settings.text_scale"), None,
                                              widgets.scale_setting("text_scale"),
                                              lambda s: win.set_scale("text_scale", s), key="text_scale"),
                (widgets.SCALE_MIN, widgets.SCALE_MAX)),
        Setting("icon_scale", wording.say("meta_settings.icon_scale"), lambda: widgets.scale_setting("icon_scale"),
                lambda v: win.set_scale("icon_scale", v),
                lambda: theming.settings_size(wording.say("meta_settings.icon_scale"), None,
                                              widgets.scale_setting("icon_scale"),
                                              lambda s: win.set_scale("icon_scale", s), key="icon_scale"),
                (widgets.SCALE_MIN, widgets.SCALE_MAX)),
        Setting("global_scale", wording.say("meta_settings.global_scale"), zoom_default, win.set_zoom_default,
                lambda: theming.settings_size(wording.say("meta_settings.global_scale"), None, zoom_default(),
                                              win.set_zoom_default,
                                              widgets.ZOOM_MIN, widgets.ZOOM_MAX, key="zoom_default"),
                (widgets.ZOOM_MIN, widgets.ZOOM_MAX), note="what ⌘0 / Ctrl+0 goes back to"),
        Setting("full_width", wording.say("meta_settings.full_width"),
                lambda: bool((a := win.lookup_action("fullwidth")) and a.get_state().get_boolean()), wide,
                lambda: theming.settings_switch(wording.say("meta_settings.full_width"), None, "win.fullwidth"), bool),
        Setting("auto_update", wording.say("updates.auto_restart"), update.enabled, auto, update.settings_row, bool),
        Setting("meta_model", wording.say("meta_settings.intelligence"), model_value, lambda v: model_picked(chat, v),
                lambda: model_row(chat),
                lambda: [m[0] for m in models()], note=f"{NAME}'s own model, from its next message"),
        Setting("meta_effort", wording.say("meta_settings.effort"), lambda: effort() or "default",
                lambda v: (widgets.save_settings(meta_effort=None if v == "default" else v), chat.model_changed()),
                lambda: effort_row(chat), lambda: ["default", *efforts()],
                note=f"how hard {NAME} thinks, of the levels its model takes, from its next message"),
        Setting("meta_code_mode", wording.say("meta_settings.code_mode"), code_mode,
                lambda v: (widgets.save_settings(meta_code_mode=v), chat.model_changed()), lambda: code_mode_row(chat),
                bool, note=f"{NAME} acts through run_python, from its next message; the user's to turn on",
                meta_sets=False),
        Setting("meta_commands", wording.say("meta_settings.run_commands"), lambda: commands_on() and takes_commands(),
                lambda v: (widgets.save_settings(meta_commands=v), chat.model_changed()), lambda: commands_row(chat),
                bool, note=f"on Claude's models, {NAME} runs shell commands and edits files, from its next message; "
                "the user's to turn on or off", meta_sets=False),
    ]
    return {s.name: s for s in out}
