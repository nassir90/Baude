#!/usr/bin/env python3
"""record — notes, and an agent beside them that reads them, and AI-written prose, with you.

Each notebook is a glossary, kept as notes: wiki pages (Markdown, [[links]] between them, @name for a page, a notebook
or anything Baude's ⌘K finds) of the things you already understand, in your own words. The Record, in the right bar, is
an agent (Claude Code, run as `claude -p`, Haiku unless Settings → Intelligence says another) for the notebook open:
  * it reads the notebook's pages and folders, and whatever's @-referenced in what you ask (a page, a notebook, a file,
    a chat: its text goes with your message), and remembers the conversation;
  * a passage selected on a page, or picked in a chat read, is quoted with what you ask: asked to fill it in or change
    it, it proposes the change, shown on the page as a diff for you to accept or reject. It never writes your notes;
  * asked what a chat's passage means, it explains it through your notes, and names what it would need to know when
    they aren't enough.

Records are listed on the left. Each has its own notes and folders, whose names @ and ⌘K know. Read shows the
Claude Code chat last opened (an @chat in the notes, ⌘K, Open a Chat…, Baude's Open in Record; any transcript
Claude Code keeps, live ones included), whichever notebook's open: you select a passage and ask about it.

    python3 record.py
    python3 record.py --chat TRANSCRIPT.jsonl   # reading that chat (Baude's menu → Open in Record does this)
    python3 record.py --install | --uninstall   # app-menu launcher with the logo (per user)

Record is part of Baude: it lives beside baude.py and searches a notebook's folders with lookup.py, the engine behind
Baude's ⌘K, for @ in the diary, for the names linked in the chat, and for what its questions say those names are.
One Record runs at a time: started again, it hands what it was asked to the one running.

Its session is `claude -p` speaking stream-json (baudelib's ClaudeProc), its tools Read, Grep and Glob and its own
propose_edit, in Claude Code's default permission mode. Set RECORD_CLAUDE=/path/to/claude to use a specific claude
binary (else claude on PATH, else, on macOS, the Claude desktop app's). Records are saved in
$XDG_DATA_HOME/record/records.json (macOS: ~/Library/Application Support/Record; RECORD_DATA=dir puts them somewhere
else).
"""
import codecs
import contextlib
import glob
import html
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import uuid
import weakref
import zipfile
import zlib

import macos  # (no GTK: before it's loaded)

macos.full_redraws()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

import lookup  # noqa: E402  (⌘K's engine: what a chat's folder has; beside this file)
import pointers  # noqa: E402  (the pointer's shape: beside this file)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; beside this file)
import fontsetup  # noqa: E402  (its fonts, as Baude's: set up before Pango's first used)
fontsetup.setup()
from baudelib.transcripts import task_notification  # noqa: E402  (a background task's ending, as said)
from baudelib.claude import MODELS, ClaudeProc  # noqa: E402  (Claude Code's process, as Baude runs its chats)
import feedback  # noqa: E402  (Send Feedback…, as Baude's: beside this file)
import fixview  # noqa: E402  (☰ → Ongoing Feedback, as Baude's)
import livefeed  # noqa: E402  (a chat followed as it's written, as Baude's: beside this file)
import viewers  # noqa: E402  (Baude's viewers, in Record's window: beside this file)
from codeview import FindInText  # noqa: E402  (⌘F on a page of notes: the Code page's find bar)
import update  # noqa: E402  (a new version from git, restarted onto once idle, as Baude's: beside this file)

APP_ID = "dev.baude.Record"
AGENT_MODEL = "haiku"  # the agent's model, unless Settings → Intelligence says another
AGENT_MODELS = list(reversed(MODELS))  # ((value, label): what Intelligence offers, the quickest first)
QUOTE_LIMIT = 6000
MACOS_DESKTOP_CLAUDE = "~/Library/Application Support/Claude/claude-code"


def noun(plural=False):
    """What the app, and the one reading with you, is called."""
    return "Record" + ("s" if plural else "")


def book(plural=False):
    """What there's one of per subject (its notes, its conversation)."""
    return "Notebook" + ("s" if plural else "")


def base_name(text):
    """The part of a name you choose: its name, its spaces evened out."""
    return re.sub(r"\s+", " ", text.strip())


def full_name(notebook):
    return base_name(notebook["name"]) or "Untitled"


def data_dir():
    d = os.environ.get("RECORD_DATA") or own_data_dir("Record")
    os.makedirs(d, exist_ok=True)
    return d


def own_data_dir(app):
    """Where an app of ours keeps its data: ~/Library/Application Support/<app> on macOS, else
    $XDG_DATA_HOME/<app, lower case>."""
    if sys.platform == "darwin":
        return os.path.expanduser(f"~/Library/Application Support/{app}")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), app.lower())


def claude_exe():
    """RECORD_CLAUDE, else claude on PATH, else (macOS) the newest one the Claude desktop app keeps."""
    exe = os.environ.get("RECORD_CLAUDE") or shutil.which("claude")
    if exe or sys.platform != "darwin":
        return exe or "claude"
    found = []
    root = os.path.expanduser(MACOS_DESKTOP_CLAUDE)
    for path in glob.glob(os.path.join(root, "*", "*", "claude.app", "Contents", "MacOS", "claude")):
        version = os.path.relpath(path, root).split(os.sep)[0]
        if os.access(path, os.X_OK) and re.fullmatch(r"\d+(\.\d+)*", version):
            found.append((tuple(int(n) for n in version.split(".")), os.path.getmtime(path), path))
    return max(found)[2] if found else "claude"


def child_env():
    """The environment for claude: ours, minus what a parent Claude Code session sets for its own children
    (its auth proxy and session ids would make the child think it's a subprocess of that session)."""
    env = dict(os.environ)
    for k in list(env):
        if k.startswith(("CLAUDE_CODE_", "CLAUDE_AGENT_SDK")) or k in (
                "CLAUDECODE", "ANTHROPIC_BASE_URL", "CLAUDE_PID", "CLAUDE_EFFORT", "AI_AGENT"):
            del env[k]
    return env


# ---------------------------------------------------------------- the store

# ---------------------------------------------------------------- the diary
# Each one's glossary is its notes: a small wiki of pages in Markdown (as Self's editor draws it; Zim's before), its
# linked with [[Page]], and @name for anything Baude's ⌘K can find (files, branches, tables… of the notebook's folders).

HOME = "Home"
TO_LEARN = "To learn"  # the heading words you've heard but don't understand yet go under
LINK_RE = re.compile(r"\[\[([^\]|\n]+)(?:\|([^\]\n]*))?\]\]")
# what Settings → Markup visibility offers: the notes' markup (# ** ` > the - of a list…) shown on the caret's line, never,
# or always (a link's [[ ]] and address kept hidden)
MARKUP_MODES = {"line": "Show formatting on current line", "none": "Don't show formatting",
                "all": "Show all formatting (Except Links)"}
REF_RE = re.compile(r"(?<![\w@])@(?:\{([^}\n]+)\}|([\w][\w./:\-]*[\w/]|\w))")
ISSUE_OPENS = re.compile(r"[\s(\[{<]")  # (what a # that's asking for GitHub's list comes after, if anything does)
# (a line's quote and list marks, and a task's box: a # after them is first on the line, as far as it's concerned)
ISSUE_LEAD = re.compile(r"\s*(?:>\s*)*(?:(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)?")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)
LIST_ITEM_RE = re.compile(r"(\s*)([-*+]|\d+[.)])(\s+)(?:\[([ xX])\]\s+)?")  # (an item: its marker, its box)
MD_LINK_RE = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\s]+)\)")
URL_RE = re.compile(r"\bhttps?://[^\s<>\"]+[^\s<>\".,;:!?)]")
SUBPAGE_RE = re.compile(r"(?:^|(?<=\s))\+(\w[\w.\-]*(?::\w[\w.\-]*)*)$")  # +Ideas just typed, at the caret


def page_target(page, target):
    """The page a link in page goes to, as Zim has it: [[+Ideas]] its own sub-page (page:Ideas), [[:Name]] and
    [[Name]] the page Name. A sub-page's name is its parent's, a colon, and its own."""
    target = target.strip()
    if target.startswith("+"):
        return f"{page}:{target[1:]}" if target[1:] else page
    return target.lstrip(":") or HOME


def page_link(page, target):
    """How a link in page to target is written: +Ideas for one of its own sub-pages, else target's name."""
    return "+" + target[len(page) + 1:] if target.startswith(page + ":") else target


def heading_levels(lines):
    """Each line's heading level, as the notes draw headings: 0 if it isn't one (nor is a # in a code fence)."""
    code = {n for first, last, _closed in widgets.fence_spans(lines) for n in range(first, last + 1)}
    levels = []
    for n, line in enumerate(lines):
        m = None if n in code else re.match(r"(#{1,6})\s", line)
        levels.append(len(m.group(1)) if m else 0)
    return levels


def subtree_end(levels, at):
    """The line after the heading at's subtree: the next heading no deeper than it, or the end."""
    return next((i for i in range(at + 1, len(levels)) if 0 < levels[i] <= levels[at]), len(levels))


def swap_with_sibling(lines, at, by):
    """The heading on line at and all under it swapped with its sibling above (by -1) or below (by 1), as Org's
    M-up and M-down do: a sibling is the nearest heading that way at its level with nothing shallower between, so it
    never leaves its parent or goes inside another heading's family. Each keeps its own text, and the blank lines between
    them stay where they are. (the new lines, from, to: the lines replaced; the heading's new line), or None if there's
    no sibling that way."""
    levels = heading_levels(lines)
    level, end = levels[at], subtree_end(levels, at)
    if by > 0:
        if end >= len(lines) or levels[end] != level:
            return None
        one, two, three = at, end, subtree_end(levels, end)
    else:
        above = at - 1
        while above >= 0 and (levels[above] == 0 or levels[above] > level):
            above -= 1
        if above < 0 or levels[above] != level:
            return None
        one, two, three = above, at, end

    def text_end(start, stop):
        while stop > start + 1 and not lines[stop - 1].strip():
            stop -= 1
        return stop
    first, second = text_end(one, two), text_end(two, three)
    new = lines[two:second] + lines[first:two] + lines[one:first] + lines[second:three]
    moved = one + (second - two) + (two - first) if by > 0 else one
    return new, one, three, moved


def file_part(name):
    """A name as one part of a path: what a file's name can't hold goes from it."""
    name = re.sub(r"[/\\:\x00-\x1f]", "-", name).strip(" .")
    return name.encode(errors="ignore")[:200].decode(errors="ignore") or "Untitled"


def fresh_path(path, taken):
    """path, or with a number if taken has it already (on a Mac's disk, Plans and plans are one file): taken has it now."""
    out, n = path, 1
    while unicodedata.normalize("NFC", out).casefold() in taken:
        n += 1
        out = f"{path} ({n})"
    taken.add(unicodedata.normalize("NFC", out).casefold())
    return out


def book_folders(notebooks):
    """A folder's name for each notebook, {its id: the name}, no two the same. A folder doesn't come to hold another's pages
    as notebooks come and go: two of one name both go by their ids too."""
    def key(notebook):
        return unicodedata.normalize("NFC", file_part(base_name(notebook["name"]))).casefold()
    names, taken = [key(notebook) for notebook in notebooks], set()
    return {notebook.get("id"): fresh_path(file_part(base_name(notebook["name"])) + (f" ({notebook.get('id')})" if names.count(key(notebook)) > 1 else ""), taken)
            for notebook in notebooks}


def page_paths(notebooks):
    """Where each page's file goes (Store.mirror_pages, export_zip): {(its notebook's id, its name): a path}, in the
    notebook's folder (book_folders), a sub-page in a folder named for its parent, as Zim keeps them. No two are the same."""
    taken, paths, books = set(), {}, book_folders(notebooks)
    for notebook in notebooks:
        for name in sorted(notebook["pages"]):
            folder = books[notebook.get("id")]
            paths[notebook.get("id"), name] = fresh_path(os.path.join(folder, *map(file_part, name.split(":"))), taken) + ".md"
    return paths


def short_page(name):
    """A page's own name, past its parents': Ideas, of Home:Ideas."""
    return name.rpartition(":")[2] or name


def whole_links(text, start, end):
    """A selection of text, start to end, so that it doesn't split a link: one that takes in its name's end or start takes
    the marks there too ([[ ]], a link's address, an @{name}'s braces, all unseen), and one that runs out of the link part
    way through its name takes it all, as what's left of it would be no link. A part of a name alone is left as it is."""
    spans = [(m.start(), *link_name(m), m.end()) for m in LINK_RE.finditer(text)]
    spans += [(m.start(), m.start(1), m.end(1), m.end()) for m in MD_LINK_RE.finditer(text)]
    spans += [(m.start(), m.start(1), m.end(1), m.end()) for m in REF_RE.finditer(text) if m.group(1)]
    was = start, end
    for a, name, name_end, b in spans:
        if b <= was[0] or a >= was[1]:
            continue
        if a < was[0] <= name or name < was[0] < name_end < was[1]:
            start = a
        elif name_end <= was[0] < b:
            start = b
        if a < was[1] <= name:
            end = a
        elif name_end <= was[1] < b or was[0] < name < was[1] < name_end:
            end = b
    return (start, end) if start < end else was


def link_name(m):
    """Where a link's name is, of a LINK_RE match: (start, end) offsets. It's all that's shown of a link: its [[ and
    ]], a sub-page's +, and the target of [[target|label]] are hidden (the diary's "hidden" tag)."""
    if m.group(2):
        return m.start(2), m.end(2)
    plus = len(m.group(1)) - len(m.group(1).lstrip()) + (1 if m.group(1).lstrip().startswith("+") else 0)
    return m.start(1) + plus, m.end(1)


def glossary_page(entries):
    """The terms of an old glossary ([{term, meaning}]) as a page: a list, with the ones not understood yet under
    To learn."""
    known = [f"- **{g['term'].strip()}**: {g['meaning'].strip()}" for g in entries
             if g.get("term", "").strip() and g.get("meaning", "").strip()]
    vague = [f"- {g['term'].strip()}" for g in entries if g.get("term", "").strip() and not g.get("meaning", "").strip()]
    text = "\n".join(known)
    if vague:
        text += ("\n\n" if text else "") + f"## {TO_LEARN}\n" + "\n".join(vague)
    return text


def zim_to_markdown(text):
    """A page in Zim's markup (the notes' before Markdown), in Markdown: ====== Heading ====== as # Heading (Zim's six
    = the biggest), //italic// as *italic*, ''code'' as `code`, __marked__ as ==marked==, a done box [*] as - [x], a
    box [ ] as - [ ], a * item as a - one; **bold**, ~~struck~~, [[links]] and @names as they are."""
    text = re.sub(r"^(={2,6})\s+(.+?)\s+\1\s*$", lambda m: "#" * (7 - len(m.group(1))) + " " + m.group(2), text,
                  flags=re.M)
    text = re.sub(r"(?<![:/])//(.+?)(?<![:/])//", r"*\1*", text)
    text = re.sub(r"''(.+?)''", r"`\1`", text)
    text = re.sub(r"__(.+?)__", r"==\1==", text)
    text = re.sub(r"^([ \t]*)\[[*x]\](?=\s)", r"\1- [x]", text, flags=re.M)
    text = re.sub(r"^([ \t]*)\[ \](?=\s)", r"\1- [ ]", text, flags=re.M)
    return re.sub(r"^([ \t]*)\*(?=\s)", r"\1-", text, flags=re.M)


def ref_text(surface, label=None):
    """How @ writes a name once it's made (@'s list, To notes): [[@name]], a reference that behaves as a link does
    (its marks hidden, what's typed at its edges going beside it, not into it); @{name} for one with what a link
    can't hold (] | or a new line). A pull request or an issue (its label, the list's: a branch may be called
    feature/login#12) shows as its #12, its repository unseen, so that it's the same one whichever folders are
    attached: [[@owner/name#12|#12]]."""
    if label:
        return f"[[@{surface}|#{label}]]"
    return f"[[@{surface}]]" if not re.search(r"[\]|\n]", surface) else "@{" + surface + "}"


def github_ref(m):
    """Whether a link (a LINK_RE match) is a pull request's or an issue's, as ref_text writes one: #12 is all that's
    seen of it, and it's one thing, to take whole (a branch called feature/login#12 is written with no label)."""
    return bool(m.group(2)) and bool(lookup.GITHUB_NAME.fullmatch(m.group(1).strip().removeprefix("@"))) and \
        m.group(1).lstrip().startswith("@")


def plain_ref(surface):
    """A name as plain @ has it: @name, or @{name} when it has characters a bare one can't."""
    return "@" + (surface if re.fullmatch(r"[\w][\w./:\-]*[\w/]|\w", surface) else "{" + surface + "}")


def issue_query(before, at):
    """What a # at position at of a line is searching GitHub's list for, the line typed as far as the caret (before), or
    None if it's asking for nothing: it isn't after the line's start, a space or an opening bracket (C#, a web address's,
    &#39;); what follows it is a space or another # (a heading's); it's in a heading, a # of which is never asked about
    (## Title ##); or it's a number and a space after it (#1 priority: said, and on to what's next)."""
    rest = before[at + 1:]
    if at and not ISSUE_OPENS.fullmatch(before[at - 1]) or rest[:1].isspace() or rest[:1] == "#":
        return None
    lead = ISSUE_LEAD.match(before).end()
    if at > lead and re.match(r"#{1,6}\s", before[lead:]) or re.fullmatch(r"\d+\s.*", rest):
        return None
    return rest


def plain_refs(text):
    """A page's [[@name]] references as plain @names (what the Record's asked about reads)."""
    return LINK_RE.sub(lambda m: plain_ref(m.group(1)[1:].strip()) if m.group(1).lstrip().startswith("@")
                       else m.group(0), text)


def add_to_learn(text, term):
    """A page with term listed under its To learn heading (made at the end if there's none)."""
    m = re.search(rf"^#{{1,6}}\s+{re.escape(TO_LEARN)}\s*$", text, re.M | re.I)
    if not m:
        return text.rstrip() + ("\n\n" if text.strip() else "") + f"## {TO_LEARN}\n- {term}\n"
    nxt = HEADING_RE.search(text, m.end())
    at = nxt.start() if nxt else len(text)
    before = text[:at].rstrip("\n")
    return before + f"\n- {term}\n" + ("\n" + text[at:] if nxt else "")


def without_to_learn(text):
    """A page without its To learn list: what's on it isn't known yet."""
    m = re.search(rf"^#{{1,6}}\s+{re.escape(TO_LEARN)}\s*$", text, re.M | re.I)
    if not m:
        return text
    nxt = HEADING_RE.search(text, m.end())
    return text[:m.start()] + (text[nxt.start():] if nxt else "")


def diary_mentions(notebook, term):
    t = term.strip().lower()
    return any(re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", p.lower()) for p in notebook["pages"].values())


class Store:
    def __init__(self):
        self.path = os.path.join(data_dir(), "records.json")
        self.notebooks, self.full_width, saved_notes = [], False, None  # (full_width: ☰ → Full-width text, as Baude's)
        self.page_files, self.mirrored = {}, {}  # (each page's file, and what's been written in them: mirror_pages)
        self.backup_all = False  # (☰ → Settings…: the rolling zip with the glossary and the chats too: Window.roll_backup)
        self.unzipped = True  # (saved since that zip was made)
        self.reading = None  # (the chat last shown, whichever notebook's open: {path, title, cwd}; Window.open_chat)
        self.chats = []  # (the chats open, a tab each, in their order: [{path, title, cwd}])
        self.pages_folded = self.books_folded = False  # (the pages' list, and the notebooks', folded away)
        self.markup = "line"  # (the notes' formatting shown: "line", the caret's; "none"; "all", all but links')
        self.agent_model = AGENT_MODEL  # (Settings → Intelligence: the model the Record's agent runs on)
        self.books_width = None  # (the notebooks' column, dragged; None: as it comes)
        self.zoom = self.zoom_default = 1.0  # (Ctrl/⌘ + − 0: the notes', the chat's and the conversation's text)
        try:
            with open(self.path) as f:
                saved = json.load(f)
                self.notebooks = saved.get("records", [])
                self.full_width = bool(saved.get("full_width"))
                saved_notes = saved.get("notes")  # ("markdown": the notes are; else Zim's markup, from before)
                self.zoom = float(saved.get("zoom") or 1.0)
                self.zoom_default = float(saved.get("zoom_default") or 1.0)
                self.books_width = saved.get("books_width")
                self.backup_all = bool(saved.get("backup_all"))
                self.reading = saved.get("reading")
                self.chats = [c for c in saved.get("chats") or ([self.reading] if self.reading else [])
                              if isinstance(c, dict) and c.get("path")]
                self.pages_folded = bool(saved.get("pages_folded"))
                self.books_folded = bool(saved.get("books_folded"))
                self.markup = saved.get("markup") if saved.get("markup") in MARKUP_MODES else "line"
                self.agent_model = saved.get("agent_model") if saved.get("agent_model") in dict(AGENT_MODELS) \
                    else AGENT_MODEL
        except (OSError, ValueError, AttributeError):
            pass
        zim = bool(self.notebooks) and saved_notes != "markdown"  # (notes from before Markdown: Zim's markup, made so once)
        if zim:
            self.backup(force=True)  # (the file as it was, kept first)
        for t in self.notebooks:  # (a name is kept as it was typed: each mode shows it its own way, see full_name)
            if "pages" not in t:  # (a glossary from before the diary: its terms, as Home's list)
                t["pages"] = {HOME: glossary_page(t.pop("glossary", []))}
            elif zim:
                t["pages"] = {n: zim_to_markdown(text) for n, text in t["pages"].items()}
            t.setdefault("folders", [])
        chats = self._without_chats()
        self._pending = None
        self.on_saved = []  # (fn(saving): told when a save is coming, and when it's done)
        if zim or chats:
            self.save_now()  # (Markdown now, notebooks without chats: kept, not made so again)

    def _without_chats(self):
        """Notebooks from when each was attached to a chat, made as they are now (a notebook has no chat: Read shows
        the one last opened, whichever notebook's open), once, the file kept first: a chat's folder one of its
        notebook's (@ knows its names, as it did), the first one's chat the one Read shows, and a notebook made only
        for its chat (blank: nothing written, asked or attached) gone. True if there were any."""
        had = [t for t in self.notebooks if "chat" in t]
        if not any(t.get("chat") for t in had):
            for t in had:
                t.pop("chat")
            return bool(had)
        self.backup(force=True)
        for t in had:
            chat = t.pop("chat") or {}
            if chat.get("path") and self.reading is None:
                self.reading = {k: chat.get(k) for k in ("path", "title", "cwd")}
            blank = not any(text.strip() for text in t["pages"].values()) and not t.get("folders") and \
                not t.get("messages")
            if chat.get("path") and blank and t.get("fresh"):  # (one from before "fresh": kept)
                self.notebooks.remove(t)
            elif chat.get("cwd") and chat["cwd"] not in t["folders"]:
                t["folders"].insert(0, chat["cwd"])
        if self.reading and not self.chats:  # (that chat open, in a tab of its own)
            self.chats = [self.reading]
        return True

    SAVE_MS = 250  # how soon a change is written: as you type, as a document online saves
    BACKUP_EVERY, BACKUPS = 600, 50  # a copy of what was saved before, at most every 10 minutes; the last 50 kept

    def new(self, name):
        """A new one, "fresh" until it's named or anything's put in it (is_blank: a blank one isn't kept)."""
        t = {"id": uuid.uuid4().hex[:12], "name": name, "created": time.time(), "pages": {HOME: ""},
             "folders": [], "messages": [], "fresh": True}
        self.notebooks.insert(0, t)
        self.save_now()
        return t

    @staticmethod
    def is_blank(t):
        """Made with New and nothing done with it since: not named, nothing written, attached or asked. (One from
        before "fresh" never is: what's kept stays.)"""
        return bool(t.get("fresh")) and not any(text.strip() for text in t["pages"].values()) and \
            not t.get("folders") and not t.get("messages")

    def delete(self, notebook):
        self.notebooks = [t for t in self.notebooks if t["id"] != notebook["id"]]
        self.save_now()

    def mirror_pages(self):
        """Every page of notes as a plain file too, read-only, in a folder that's Record's own: pages/<notebook>/<page>.md.
        The notes are kept in the one file an agent can't follow a link into; these it can read, and what they link to beside
        them (Copy Link: MainTabs.copy_link). Written as a page changes, gone as it goes or is renamed. Nothing's taken away
        but what's been written here. What's kept is records.json: a file that can't be written here stops neither that nor
        the other files."""
        root = os.path.join(data_dir(), "pages")
        self.page_files = {key: os.path.join(root, rel) for key, rel in page_paths(self.notebooks).items()}
        books = {t.get("id"): t["pages"] for t in self.notebooks}
        wanted = {path: books[book][name] for (book, name), path in self.page_files.items()}
        failed = None
        gone = self.mirrored.keys() - wanted.keys()
        for path in gone:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as e:
                failed = e
                continue
            del self.mirrored[path]
        for path, text in wanted.items():
            if self.mirrored.get(path) != text:
                self.mirrored.pop(path, None)
                try:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path + ".tmp", "w", encoding="utf-8", errors="replace") as f:
                        f.write(text)
                    os.replace(path + ".tmp", path)
                    os.chmod(path, 0o444)
                    self.mirrored[path] = text
                except OSError as e:
                    failed = e
        if gone:
            for folder, _subs, _files in os.walk(root, topdown=False):
                if folder != root and not os.listdir(folder):
                    os.rmdir(folder)
        if failed is not None:
            print(f"Record: the pages' files: {failed}", file=sys.stderr)

    def page_file(self, notebook, name):
        """A page's file, as the page is now (None if it couldn't be written)."""
        self.save_now()
        path = self.page_files.get((notebook.get("id"), name))
        return path if path and self.mirrored.get(path) == notebook["pages"].get(name) else None

    def flush(self):
        """A save that's coming, made now (leaving a page, a notebook, the window, Record)."""
        if self._pending is not None:
            self.save_now()

    def save(self):
        """Save in a moment (SAVE_MS): every change, as it's made."""
        if self._pending is None:
            self._pending = GLib.timeout_add(self.SAVE_MS, self.save_now)
            for fn in self.on_saved:
                fn(True)

    def save_now(self):
        """Written now, whole, in place of the file (a half-written file is never what's there); the file as it was
        kept first in backups/ if the last copy there is BACKUP_EVERY old (backup)."""
        if self._pending is not None:
            GLib.source_remove(self._pending)
            self._pending = None
        self.backup()
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"records": self.notebooks, "notes": "markdown", "full_width": self.full_width, "zoom": self.zoom,
                       "zoom_default": self.zoom_default, "reading": self.reading, "chats": self.chats,
                       "pages_folded": self.pages_folded, "books_folded": self.books_folded,
                       "markup": self.markup, "books_width": self.books_width, "backup_all": self.backup_all,
                       "agent_model": self.agent_model}, f, indent=1)
            f.flush()
            os.fsync(f.fileno())  # (on the disk before it takes the file's place)
        os.replace(tmp, self.path)
        self.unzipped = True
        self.mirror_pages()
        for fn in self.on_saved:
            fn(False)
        return False

    def backup(self, force=False):
        """records.json as it is, copied to backups/records-<time>.json, unless the newest copy is younger than
        BACKUP_EVERY (force: whatever its age); the oldest past BACKUPS go. What a slip (or a bug) loses can be had
        back from there."""
        if not os.path.exists(self.path):
            return
        folder = os.path.join(os.path.dirname(self.path), "backups")
        try:
            os.makedirs(folder, exist_ok=True)
            kept = sorted(n for n in os.listdir(folder) if n.startswith("records-") and n.endswith(".json"))
            if not force and kept and time.time() - os.path.getmtime(os.path.join(folder, kept[-1])) < self.BACKUP_EVERY:
                return
            shutil.copyfile(self.path, os.path.join(folder, time.strftime("records-%Y%m%d-%H%M%S.json")))
            for old in kept[:max(0, len(kept) + 1 - self.BACKUPS)]:
                os.remove(os.path.join(folder, old))
        except OSError as e:
            print("record: no backup of records.json:", e, file=sys.stderr)


# ---------------------------------------------------------------- transcripts

def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _is_noise(text):
    s = text.lstrip()
    return not s or s.startswith(("<command-", "<local-command", "<system-reminder", "<ci-monitor-event", "Caveat:", "[Request interrupted",
                                  "This session is being continued from a previous conversation"))


def parse_records(lines, items):
    """Append the prose of transcript lines to items: [{"role": "user"|"assistant", "text", "id"}]. An assistant
    message written as several records (one per block) becomes one item. A background task's ending, which Claude Code
    writes as a message of yours (<task-notification>), is a quiet line of its own: {"role": "note", "text", "markup",
    "failed", "summary"}, as Baude's chat says it."""
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        kind = d.get("type")
        if kind not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta") or d.get("isCompactSummary"):
            continue
        msg = d.get("message") or {}
        text = _text_of(msg.get("content")).strip()
        if kind == "user":
            if _is_noise(text) or d.get("toolUseResult") is not None:
                continue
            if (note := task_notification(text)) is not None:
                markup, plain, failed = widgets.task_note_line(note)
                items.append({"role": "note", "text": plain, "markup": markup, "failed": failed,
                              "summary": note.get("summary"), "id": d.get("uuid")})
                continue
            items.append({"role": "user", "text": text, "id": d.get("uuid")})
        elif text:
            mid = msg.get("id")
            if items and items[-1]["role"] == "assistant" and mid and items[-1]["id"] == mid:
                items[-1]["text"] += "\n\n" + text
                items[-1]["grew"] = True
            else:
                items.append({"role": "assistant", "text": text, "id": mid})
    return items


CITE_SCHEME = "baude-chat:"  # (what follows a passage cited from a chat: Reader.citation, Window.open_citation)
CITE_WORDS = 8  # (of a long passage, its link holds this many words from each end: as a browser's text fragment does)
CITE_BEFORE = 4  # (and the words before it, to tell it from the same words said elsewhere)


def plain_words(text):
    """Text as a passage is matched: what's shown of it, any white space one space (a logo's place is a U+2063)."""
    return " ".join(text.replace("\u2063", "").split())


def citation_link(session, message, before, passage):
    """A link to a passage of a chat, in markdown, to put in the notes: found again by its words, as a browser's link to
    a highlight is (#:~:text=[prefix-,]start[,end]), starting from the message it's in, not by a place in the chat."""
    def part(words):
        return urllib.parse.quote(words, safe="").replace("-", "%2D")
    words = passage.split()
    pieces = [" ".join(words)] if len(words) <= CITE_WORDS * 2 else \
        [" ".join(words[:CITE_WORDS]), " ".join(words[-CITE_WORDS:])]
    if (prefix := " ".join(before.split()[-CITE_BEFORE:])):
        pieces[0:0] = [prefix + "-"]
    fragment = ",".join(part(p[:-1]) + "-" if p is pieces[0] and prefix else part(p) for p in pieces)
    url = f"{CITE_SCHEME}{urllib.parse.quote(session, safe='')}?message={urllib.parse.quote(message or '', safe='')}" \
          f"&length={len(passage)}#:~:text={fragment}"
    shown = passage if len(passage) <= 60 else passage[:60].rsplit(" ", 1)[0] + "…"
    return f"[“{re.sub(r'[][*`@]', '', shown)}”]({url})"  # (what the notes would read as markup, or an @name)


def parse_citation(url):
    """What a citation link says: {session, message, length, prefix, start, end}; None if it isn't one."""
    if not url.startswith(CITE_SCHEME):
        return None
    head, _hash, fragment = url[len(CITE_SCHEME):].partition("#:~:text=")
    session, _q, query = head.partition("?")
    pieces = fragment.split(",") if fragment else []
    prefix = urllib.parse.unquote(pieces.pop(0)[:-1]) if pieces and pieces[0].endswith("-") else ""
    if not pieces or not re.fullmatch(r"[\w-]+", urllib.parse.unquote(session)):
        return None
    fields = urllib.parse.parse_qs(query)
    length = fields.get("length", [""])[0]
    return {"session": urllib.parse.unquote(session), "prefix": prefix, "message": fields.get("message", [""])[0],
            "length": int(length) if length.isdigit() else 0,
            "start": urllib.parse.unquote(pieces[0]), "end": urllib.parse.unquote(pieces[1]) if len(pieces) > 1 else ""}


def passage_in(texts, cite):
    """Where a citation's passage is in texts (in order, each as a label has it): [(which, start, end)], else None. The
    words before it tell it from the same words said elsewhere; if they've gone, its first place."""
    chars, where = [], []
    for n, text in enumerate(texts):
        gap = bool(chars)
        for i, ch in enumerate(text):
            if ch == "\u2063":
                continue
            if ch.isspace():
                gap = bool(chars)
                continue
            if gap:
                chars.append(" ")
                where.append(None)
                gap = False
            chars.append(ch)
            where.append((n, i))
    text = "".join(chars)
    starts, at = [], text.find(cite["start"])
    while at != -1:
        starts.append(at)
        at = text.find(cite["start"], at + 1)
    if not starts:
        return None
    start = next((s for s in starts if cite["prefix"] and text[:s].rstrip().endswith(cite["prefix"])), starts[0])
    stop = start + len(cite["start"])
    if cite["end"]:
        ends, at = [], text.find(cite["end"], stop)
        while at != -1:
            ends.append(at + len(cite["end"]))
            at = text.find(cite["end"], at + 1)
        if not ends:
            return None
        # (its last words may be said more than once: the place that makes it as long as it was, the words still there)
        stop = min(ends, key=lambda e: abs(e - start - cite.get("length", 0))) if cite.get("length") else ends[0]
    found = {}
    for place in where[start:stop]:
        if place is not None:
            first = found.setdefault(place[0], [place[1], place[1]])
            first[1] = place[1]
    return [(n, a, b + 1) for n, (a, b) in found.items()]


def session_path(session):
    """The transcript of a chat by its id, on this machine, whichever project it's in: None if there's none."""
    if not re.fullmatch(r"[\w-]+", session or ""):
        return None
    return next(iter(glob.glob(os.path.join(glob.escape(projects_dir()), "*", session + ".jsonl"))), None)


def session_info(path):
    """(title, cwd) of a transcript, from its first and last 256 KB."""
    return session_facts(path)[:2]


def session_facts(path):
    """(title, cwd, when it was last typed in) of a transcript, the first two from its first and last 256 KB, the last
    as epoch seconds (widgets.typed_at: read back from the end till there's one, widgets.last_typed), else None."""
    title = first = cwd = said = None
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(262144)
            f.seek(max(0, size - 262144))
            tail = f.read() if size > 262144 else b""
    except OSError:
        return None, None, None
    for chunk in (head, tail):
        last = chunk is tail or not tail  # (the end of the file: where the last thing typed is looked for)
        for raw in chunk.split(b"\n"):
            if not raw.startswith(b"{"):
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            cwd = cwd or d.get("cwd")
            if last and (when := widgets.typed_at(d)) and when > (said or ""):
                said = when
            k = d.get("type")
            if k == "custom-title" and d.get("customTitle"):
                title = d["customTitle"]
            elif k in ("ai-title", "summary") and not title:
                title = d.get("aiTitle") or d.get("title") or d.get("summary")
            elif k == "user" and first is None and not d.get("isMeta"):
                t = _text_of((d.get("message") or {}).get("content")).strip()
                if not _is_noise(t):
                    first = t
    title = title or (first or "").split("\n")[0][:90] or "Untitled chat"
    return title, cwd, widgets.ref_epoch(said) if said or not tail else widgets.last_typed(path)


def projects_dir():
    """Where Claude Code keeps its transcripts (as Baude finds them: CLAUDE_CONFIG_DIR's, else ~/.claude's)."""
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")


def desktop_sessions_dir():
    """Where the Claude desktop app keeps its Code tab's chats: a JSON file each (local_*.json), under its account's
    folders."""
    base = (os.path.expanduser("~/Library/Application Support") if sys.platform == "darwin"
            else os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"))
    return os.path.join(base, "Claude", "claude-code-sessions")


_REPLACED = {}  # (a desktop chat's file: (its mtime, its size, the transcripts it left behind))


def replaced_transcripts():
    """The transcripts (their ids) the Claude desktop app's chats have left behind. A chat it restarts (its first
    message sent again), forks or resumes goes on in a new transcript under a new id, with the old ones' ids as its
    priorCliSessionIds; they stay in Claude Code's folder, each with the chat's title, but the app shows the chat
    once. So do @, ⌘K and Open a Chat…"""
    out = set()
    for f in glob.glob(os.path.join(desktop_sessions_dir(), "**", "local_*.json"), recursive=True):
        try:
            st = os.stat(f)
        except OSError:
            continue
        known = _REPLACED.get(f)
        if known is None or known[:2] != (st.st_mtime, st.st_size):
            try:
                with open(f, encoding="utf-8") as fh:
                    ids = json.load(fh).get("priorCliSessionIds") or []
            except (OSError, ValueError, AttributeError):
                ids = []
            known = _REPLACED[f] = (st.st_mtime, st.st_size, frozenset(i for i in ids if isinstance(i, str)))
        out |= known[2]
    return out


def chat_transcripts():
    """Claude Code's transcripts (*.jsonl in its projects' folders), but those the desktop app has left behind
    (replaced_transcripts)."""
    gone = replaced_transcripts()
    return [p for p in glob.glob(os.path.join(projects_dir(), "*", "*.jsonl"))
            if os.path.basename(p)[:-len(".jsonl")] not in gone]


def recent_sessions(limit=80):
    paths = chat_transcripts()
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    out = []
    for p in paths[:limit]:
        title, cwd, said = session_facts(p)
        if title:
            mtime = os.path.getmtime(p)
            # (used: when it was last typed in, what the list goes by; the file's time moves with every reply)
            out.append({"path": p, "title": title, "cwd": cwd, "mtime": mtime, "used": said or mtime})
    out.sort(key=lambda s: -s["used"])
    return out


def short_dir(d):
    if not d:
        return ""
    home = os.path.expanduser("~")
    if "scratch-workspaces" in d:
        return "No folder"
    return "~" + d[len(home):] if d.startswith(home) else d


# ---------------------------------------------------------------- markdown → Pango

def _name_link(name, shown):
    return f'<a href="ref:{urllib.parse.quote(name, safe="")}">{shown}</a>'


def _inline(s, names=()):
    """A line's inline markdown as Pango markup, the names (from SearchSpace.mentions) linked as ref:name."""
    out = []
    for part in re.split(r"(`[^`\n]+`)", s):
        if len(part) > 1 and part.startswith("`") and part.endswith("`"):
            code = '<span font_family="monospace" size="' + widgets.code_markup_size() + '" background="#8882">' + html.escape(part[1:-1], False) + "</span>"
            name = next((n for n in (part[1:-1], lookup.ref_key(part[1:-1])) if n in names), None)
            out.append(_name_link(name, code) if name else code)
            continue
        found = []  # (the names in prose, kept out of the formatting as \0n\0, put back linked)
        if names:
            keep = lambda m: m[0] if m[0] not in names else (found.append(m[0]) or f"\0{len(found) - 1}\0")  # noqa: E731
            part = lookup._CATALOGUE_TOKEN.sub(keep, lookup.GITHUB_NAME.sub(keep, part))
        t = html.escape(part, False)
        t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])", r"<i>\1</i>", t)
        t = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<i>\1</i>", t)
        t = re.sub(r"\0(\d+)\0", lambda m: _name_link(found[int(m[1])], html.escape(found[int(m[1])], False)), t)
        out.append(t)
    return "".join(out)


def md_markup(text, names=()):
    lines, out, code = text.split("\n"), [], None
    for line in lines:
        if line.lstrip().startswith("```"):
            if code is None:
                code = []
            else:
                out.append('<span font_family="monospace" size="' + widgets.code_markup_size() + '">' + html.escape("\n".join(code), False) + "</span>")
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
            out.append('<span font_family="monospace" size="' + widgets.code_markup_size() + '">' + html.escape(line, False) + "</span>")
        elif line.startswith(">"):
            out.append("<i>" + _inline(line.lstrip("> "), names) + "</i>")
        elif re.fullmatch(r"\s*([-*_])\1{2,}\s*", line):
            out.append("―――")
        else:
            out.append(_inline(line, names))
    if code is not None:
        out.append('<span font_family="monospace" size="' + widgets.code_markup_size() + '">' + html.escape("\n".join(code), False) + "</span>")
    return "\n".join(out)


def set_md(label, text, names=()):
    markup = md_markup(text, names)
    try:  # (checked as Pango has it: <a> is the label's own, which Pango doesn't know)
        Pango.parse_markup(re.sub(r"</?a\b[^>]*>", "", markup), -1, "\0")
        label.set_markup(markup)
    except GLib.Error:
        label.set_text(text)


def paned_grip(paned):
    """paned, its rule picked before its end child (as Baude's paned_grip): over the 6px either side of the rule that a
    press drags it from, the pointer is the resize arrows, the end child's side too."""
    ends = (paned.get_start_child(), paned.get_end_child())
    handle = paned.get_first_child()
    while handle is not None and handle in ends:
        handle = handle.get_next_sibling()
    if handle is not None and ends[1] is not None:
        handle.insert_after(paned, ends[1])
    return paned


# ---------------------------------------------------------------- the agent
# Record's agent is a Claude Code session (claude -p, speaking stream-json: baudelib's ClaudeProc, as Baude runs its
# chats), one for the conversation with the notebook open, kept while it's open: it remembers what's been said, as a
# chat does, and a new one (a notebook gone back to, Record started again, another model) is told what was said before.
# It reads (Read, Grep, Glob) the notebook's pages, as their files (Store.mirror_pages), and its folders, and changes
# nothing: a change to a page it proposes with propose_edit (its MCP tool, answered here: RecordChat.propose), which the
# page shows as a diff, to accept or reject (DiaryEditor.show_proposal).

SYSTEM = """You are {name}, the agent in the user's notes app. They keep notebooks of pages in Markdown \
([[Page]] links a page to another; @name is a page, a notebook, a file, a chat or anything else their folders have). \
You see the notebook they have open: its pages, the folders attached to it, and what they @-reference or select. Your \
tools only read: Read, Grep and Glob, over the notebook's pages (each one a file, read-only) and its folders.

You never write the notes yourself. To change a page (fill a passage in, rewrite one, add to it), call propose_edit \
with the page, the exact text it replaces and what replaces it: the user sees the edit on the page, and accepts or \
rejects it. When they've selected a passage on a page and ask you to fill it in or change it, the text it replaces is \
that selection, exactly. Write in the page's Markdown and its voice, and only what was asked. Don't announce, \
describe or sum up an edit: the user sees it. After one, say nothing unless there's something they need to know (a \
guess you made, a TODO you left). You'll be told whether they accepted it.

Your edits stay pending, across turns and sessions, until the user accepts or rejects them: a draft over the page, \
which they may keep writing in meanwhile. Work on that draft. list_edits shows what's pending; propose_edit's old_text \
is found in the page as it reads with your pending edits applied, so a change to text one of them puts in revises \
that edit, and one to text an edit replaces replaces it, rather than adding a second; revise_edit changes an edit \
outright and withdraw_edit takes one back. The page's file has none of your pending edits in it.

What the user @-references comes with their message: its text, or where to read it. Use it and the conversation as \
what you know of the subject, and read more with your tools when that helps. Don't make facts up: where something \
isn't known, say so, or leave a clear TODO in what you propose.

The User knows quite a fair bit about computer science as-it-was. C++, CLI, HTTP, HTTPS, QUIC, TDD etc. all of \
these acronyms are known. Where things get more complicated are terms such as "golden files" etc., pretentious \
terms used to obfuscate something very simple. I.e. golden files are really just "expected outputs", however \
some people would prefer, to the great misfortune of the onboarding engineer, to use the term "golden files". \
Things like that often come up that the {name} needs to understand is outside of the glossary.

When they ask what a passage from a chat they're reading means, you're their peer, not an expert: explain it through \
their notes (their glossary: what they already understand, in their own words), everyday language and computer \
science basics, reusing their wording; never explain or flag standard computer science. Say the simple thing a piece \
of in-group jargon means if the context makes it clear ("golden files" = expected outputs). If the passage needs an \
idea that's in none of those, say so plainly and give your best guess, marked as a guess. Terms under a "{to_learn}" \
heading are words they've heard but don't understand yet: don't explain through them. A passage's project names come \
with what each one is: those are known, not jargon. Keep it to a few short sentences, and end that reply with exactly \
one last line: "Unknown: " then the jargon and ideas from the passage that are neither computer science basics nor in \
their notes, separated by semicolons, each a short noun phrase, or "Unknown: none". Only a reply explaining a passage \
ends so.

Be brief and plain, with no headings unless you're asked for them. Talk like a peer."""

# the agent's own tools (MCP), as Claude Code sees them; Record answers them (RecordChat.propose)
AGENT_MCP_TOOLS = [
    {"name": "propose_edit",
     "description": "Propose a change to a page of the user's notes, in the notebook open. The user sees it on the "
     "page as a diff and accepts or rejects it; nothing is written until they accept. old_text is the text that "
     "new_text replaces, copied exactly (whitespace and Markdown too), and found once in the page as it reads with "
     "your pending edits applied (list_edits): give more of what's around it if it's there more than once. Text one "
     "of your pending edits puts in, or text it replaces: that edit is revised to take this in, not a second one made. "
     "To fill in or change a passage the user selected, old_text is that selection. An empty old_text adds new_text "
     "at the page's end. Returns the edit's id, or why nothing was shown.",
     "inputSchema": {"type": "object", "required": ["page", "old_text", "new_text"], "properties": {
         "page": {"type": "string", "description": "the page's name (Spec, or Spec:Auth for a sub-page) or its file"},
         "old_text": {"type": "string"}, "new_text": {"type": "string"}}}},
    {"name": "list_edits",
     "description": "Your edits still waiting for the user to accept or reject, in the notebook open (or on one "
     "page), in page order: each one's id, page, old_text (what it replaces, as the page has it now) and new_text.",
     "inputSchema": {"type": "object", "properties": {
         "page": {"type": "string", "description": "only this page's (its name or its file)"}}}},
    {"name": "revise_edit",
     "description": "Change one of your pending edits (list_edits): new_text is what it puts in now; old_text, if "
     "given, what it replaces now (found once in the page, not in another pending edit's passage). It stays one "
     "edit, waiting for the user. new_text the same as what it replaces takes it back.",
     "inputSchema": {"type": "object", "required": ["id", "new_text"], "properties": {
         "id": {"type": "string"}, "new_text": {"type": "string"}, "old_text": {"type": "string"}}}},
    {"name": "withdraw_edit",
     "description": "Take back one of your pending edits (list_edits): it goes from the page, the page as it is.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}},
]
AGENT_TOOLS = ("Read", "Grep", "Glob")  # Claude Code's own: those that only read
# its session's environment: its MCP tools loaded at the start (not through a ToolSearch turn), no auto memory, no
# claude.ai connectors, no telemetry or update checks (as Baude's Signal reader has it)
AGENT_ENV = {"ENABLE_TOOL_SEARCH": "false", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
             "ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
REF_LIMIT = 20000  # characters of a referenced page's, file's or chat's text put in with a message (past it: where to Read)
REFS_LIMIT = 60000  # of all a message references, together
NOTES_LIMIT = 30000  # of the notebook's pages, with a session's first message (past it: their files, to Read)
EARLIER_LIMIT = 16000  # of what was said before a session started, with its first message

# the relay Claude Code starts as the agent's MCP server (python -c, nothing written): stdio JSON-RPC in, Record's
# socket out (ToolSocket), a call a line. As Baude's Signal reader's (SIGNAL_READER_MCP)
AGENT_RELAY = r'''import json, socket, sys

sock = sys.argv[1]


def call(tool, args):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock)
    s.sendall((json.dumps({"tool": tool, "args": args}) + "\n").encode())
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
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "record", "version": "1"}}
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
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "Record: " + str(e)}})
'''


def what_it_is(item, space):
    """An item of lookup's, said briefly: "Database table, relay (pg-1)", "File, src/jobs.py", "Chat, ~/shop · 2h ago",
    "Page, Garden"."""
    if item.get("kind") == "chat":
        return f"Chat, {item['summary']}"
    if item.get("kind") in ("page", "notebook"):
        return f"{item['what']}, {item['summary']}"
    root = item.get("root") or space.root  # (its own folder's, of several: Spaces)
    where = os.path.relpath(item["path"], root) if item.get("path") and root else item["summary"]
    return f"{item['what']}, {where}"


def said_names(text, space):
    """A page's @names, each followed by what it is (what_it_is), if space knows it; its [[@name]]s as @names."""
    def known(m):
        item = space.find(m.group(1) or m.group(2)) if space is not None else None
        return m.group(0) + (f" ({what_it_is(item, space)})" if item else "")
    return REF_RE.sub(known, plain_refs(text))


def attr(value):
    return html.escape(str(value), quote=True)


def trimmed(text, limit, where=None):
    """text, or its start as far as limit and what's left said, with where to read the rest."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n[… {len(text) - limit} more characters" + (f": Read {where}" if where else "") + "]"


def notebook_said(notebook, store, space, limit=NOTES_LIMIT):
    """A session's first words: the notebook open, its pages (each one's file, and its text as far as limit, its
    @names said) and its folders."""
    files = page_paths(store.notebooks)
    root = pages_root()
    pages = sorted(notebook["pages"], key=lambda p: (p != HOME, p.lower()))
    parts = [f'<notebook name="{attr(full_name(notebook))}">']
    left = limit
    for name in pages:
        text = notebook["pages"][name].strip()
        file = os.path.join(root, files.get((notebook.get("id"), name), ""))
        if not text:
            parts.append(f'<page name="{attr(name)}" file="{attr(file)}">(empty)</page>')
        elif len(text) <= left:
            parts.append(f'<page name="{attr(name)}" file="{attr(file)}">\n{said_names(text, space)}\n</page>')
            left -= len(text)
        else:
            parts.append(f'<page name="{attr(name)}" file="{attr(file)}">(not put here: Read its file)</page>')
    folders = attached_folders(notebook)
    parts.append("Its folders: " + (", ".join(folders) if folders else "none attached"))
    parts.append("</notebook>")
    return "\n".join(parts)


def earlier_said(messages, limit=EARLIER_LIMIT):
    """What was said before this session (a new one: Record started again, the notebook gone back to, another model),
    the newest as far back as limit."""
    lines, size = [], 0
    for m in reversed([m for m in messages if not m.get("error")]):
        who = "User" if m.get("role") == "user" else noun()
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


def referenced(text, space):
    """The things a message @-references that the catalogue knows (Window.space), each once, in order."""
    out, seen = [], set()
    for m in REF_RE.finditer(plain_refs(text)):
        item = space.find(m.group(1) or m.group(2)) if space is not None else None
        if item is not None and item["surface"] not in seen:
            seen.add(item["surface"])
            out.append(item)
    return out


def file_text(path, limit):
    """A file's text, as far as limit, or None: not text, or not to be had."""
    try:
        with open(path, "rb") as f:
            data = f.read(limit * 4 + 1)
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    try:  # (what's read may end part way into a character: that part's left for the rest)
        text = codecs.getincrementaldecoder("utf-8")().decode(data, final=False)
    except UnicodeDecodeError:
        return None
    return trimmed(text, limit, path)


def ref_said(item, store, space, limit=REF_LIMIT):
    """What an @-referenced thing is, for the agent: a page's or a notebook's text (its pages', as far as limit), a
    file's text (or where it is, to read), a chat's, or what it is. (said, paths it may read)."""
    kind, books = item.get("kind"), {t.get("id"): t for t in store.notebooks}
    root, files = pages_root(), page_paths(store.notebooks)
    if kind == "page" and (notebook := books.get(item.get("notebook"))) is not None:
        file = os.path.join(root, files.get((notebook.get("id"), item["page"]), ""))
        text = notebook["pages"].get(item["page"], "")
        return (f'<page notebook="{attr(full_name(notebook))}" name="{attr(item["page"])}" file="{attr(file)}">\n'
                f'{trimmed(said_names(text, space), limit, file) or "(empty)"}\n</page>'), [file]
    if kind == "notebook" and (notebook := books.get(item.get("notebook"))) is not None:
        parts, left, paths = [f'<notebook name="{attr(full_name(notebook))}">'], limit, []
        for name in sorted(notebook["pages"], key=lambda p: (p != HOME, p.lower())):
            file = os.path.join(root, files.get((notebook.get("id"), name), ""))
            text = notebook["pages"][name].strip()
            paths.append(file)
            body = said_names(text, space) if len(text) <= left else "(not put here: Read its file)"
            left -= len(text) if len(text) <= left else 0
            parts.append(f'<page name="{attr(name)}" file="{attr(file)}">\n{body or "(empty)"}\n</page>')
        return "\n".join(parts + ["</notebook>"]), paths
    if kind == "chat" and item.get("path"):
        try:
            text = chat_text(item["path"], item["surface"], item.get("where"))
        except OSError:
            text = None
        if text is not None and len(text) > limit:  # (its end: what was said last)
            text = f"[… its first {len(text) - limit} characters not put here: Read {item['path']}]\n" + text[-limit:]
        return f'<chat title="{attr(item["surface"])}" transcript="{attr(item["path"])}">\n{text or "(nothing said)"}\n' \
               f'</chat>', [item["path"]]
    path = item.get("path")
    if path and os.path.isdir(path):
        return f'<folder name="{attr(item["surface"])}" path="{attr(path)}">{attr(what_it_is(item, space))}: ' \
               f'Glob and Grep it</folder>', [path]
    if path and os.path.isfile(path):
        text = file_text(path, limit)
        line = f' line="{item["line"]}"' if item.get("line") else ""
        return (f'<file name="{attr(item["surface"])}" path="{attr(path)}"{line}>\n{text}\n</file>' if text is not None
                else f'<file name="{attr(item["surface"])}" path="{attr(path)}"{line}>(not text: '
                     f'{attr(what_it_is(item, space))})</file>'), [path]
    return f'<ref name="{attr(item["surface"])}">{attr(what_it_is(item, space))}</ref>', []


def passage_said(quote, space):
    """A passage quoted from a chat being read (Reader.picks), with what its project names are."""
    names = space.mentions(quote[:QUOTE_LIMIT]) if space is not None else []
    said = "".join(f"\n- {it['surface']}: {what_it_is(it, space)}" for it in names[:30])
    tag = "passages" if quote.startswith("[1] ") and "\n\n[2] " in quote else "passage"
    opened = "[passages, numbered as the user sees them]" if tag == "passages" else "[passage]"
    return f"{opened}\n{quote[:QUOTE_LIMIT]}\n[/{tag}]\n" + \
        (f"[project names in it]{said}\n[/project names]\n" if said else "")


def selection_said(selection, notebook, store):
    """A passage selected on a page of notes (DiaryEditor._quote_selection), and where it is."""
    file = os.path.join(pages_root(), page_paths(store.notebooks).get((notebook.get("id"), selection["page"]), ""))
    said = "(Selected on the page: what the user is asking about, or wants filled in or changed. A change to it is a " \
        "propose_edit with this as old_text.)" if selection.get("start") is not None else \
        "(Quoted from the page, where it isn't as it was any more: the page has changed since.)"
    return f'<selection page="{attr(selection["page"])}" file="{attr(file)}">\n{selection["text"]}\n</selection>\n' \
        f'{said}\n'


def page_named(notebook, name, store):
    """The page of notebook a name means (the agent's: its name, any case, its own name past its parents', or its file),
    or None."""
    name = name.strip()
    pages = notebook["pages"]
    if name in pages:
        return name
    for test in (lambda p: p.lower() == name.lower(), lambda p: short_page(p).lower() == name.lower()):
        found = [p for p in pages if test(p)]
        if len(found) == 1:
            return found[0]
    files = {os.path.join(pages_root(), path): page for (book, page), path in page_paths(store.notebooks).items()
             if book == notebook.get("id")}
    path = os.path.realpath(os.path.join(pages_root(), name)) if name else ""
    return next((page for file, page in files.items() if os.path.realpath(file) == path or
                 file.endswith(os.sep + name.lstrip(os.sep))), None)


def pending_edits(notebook, agent=None):
    """The edits the agent's proposed to a notebook's pages that wait for you, in the order it made them (the reply
    still coming's, agent's, last)."""
    live = agent.turn["proposals"] if agent is not None and agent.turn is not None and agent.notebook is notebook \
        else ()
    props = [p for m in notebook.get("messages") or () for p in m.get("proposals") or ()]
    return [p for p in props + [p for p in live if p not in props] if p.get("status") == "pending"]


def staged(text, edits):
    """A page's text as it reads with edits applied: (that text, its pieces). edits: [(start, old, new, edit)], in the
    page's own places; one that overlaps one before it is left out. At one place an insertion (old empty) comes before
    a replacement, and edits otherwise in the order they were made. A piece: (its start and end in the staged text,
    its start and end in the page's, the edit it's of or None)."""
    out, pieces, at, pos = [], [], 0, 0
    order = lambda x: (x[0], bool(x[1]), x[3].get("t", 0) if isinstance(x[3], dict) else 0)  # noqa: E731
    for start, old, new, edit in sorted(edits, key=order):
        if start < at:
            continue
        if start > at:
            out.append(text[at:start])
            pieces.append((pos, pos + start - at, at, start, None))
            pos += start - at
        out.append(new)
        pieces.append((pos, pos + len(new), start, start + len(old), edit))
        pos, at = pos + len(new), start + len(old)
    out.append(text[at:])
    pieces.append((pos, pos + len(text) - at, at, len(text), None))
    return "".join(out), pieces


def staged_at(pieces, at):
    """A place in the page's own text as one in its staged text (in a passage an edit replaces: that edit's start)."""
    for ss, se, ks, ke, edit in pieces:
        if ks <= at <= ke:
            return ss + (at - ks) if edit is None else ss
    return at


def kept_at(pieces, at, pick=min):
    """A place in the staged text as one in the page's own (where an edit's new text starts or ends: its passage's
    start or end). Where it's more than one (an edit that puts nothing in is all of its passage at one place): pick's
    of them, min the first, max the last."""
    places = []
    for ss, se, ks, ke, edit in pieces:
        if edit is None and ss <= at <= se:
            places.append(ks + (at - ss))
        elif edit is not None and at in (ss, se):
            places += [ks] if at == ss else []
            places += [ke] if at == se else []
    return pick(places) if places else None


def place_change(text, old, near=None):
    """Where a proposed change goes in a page's text: (offset, the text it replaces), or (None, why not). old there once,
    or, if more than once, the one nearest near (where the selection was); with its spaces either side left out, if it's
    there so and not as given. An empty old: the page's end."""
    if not old:
        return len(text), ""
    for want in dict.fromkeys((old, old.strip())):
        if not want:
            continue
        at = [m.start() for m in re.finditer(re.escape(want), text)]
        if len(at) == 1 or at and near is not None:
            return min(at, key=lambda i: abs(i - near)) if near is not None else at[0], want
        if len(at) > 1:
            return None, f"old_text is in the page {len(at)} times: give more of what's around it, so it's there once."
    return None, "old_text isn't in the page as it is now: copy it exactly from the page (Read its file)."


def split_unknown(text):
    """(reply, [unknown terms]) — the reply without its last "Unknown:" line."""
    m = re.search(r"\n?\s*\**Unknown:\**\s*(.*?)\s*$", text, re.I | re.S)
    if not m or "\n" in m.group(1).strip():
        return text.strip(), []
    terms = [t.strip(" .*`\"'") for t in re.split(r"[;,]", m.group(1))]
    terms = [t for t in terms if t and t.lower() not in ("none", "nothing", "n/a")]
    return text[:m.start()].strip(), terms


def pages_root():
    """The folder the pages' files are in (Store.mirror_pages): the agent's working folder."""
    root = os.path.join(data_dir(), "pages")
    os.makedirs(root, exist_ok=True)
    return root


def tool_said(name, inp):
    """A tool call of the agent's, as its reply says it while it works."""
    short = lambda p: os.path.basename(str(p).rstrip(os.sep)) or str(p)  # noqa: E731
    if name == "Read":
        return f"Reading {short(inp.get('file_path', ''))}…"
    if name == "Grep":
        return f"Searching for “{inp.get('pattern', '')}”…"
    if name == "Glob":
        return f"Looking for {inp.get('pattern', '')}…"
    if name == "mcp__record__propose_edit":
        return f"Proposing a change to {short_page(str(inp.get('page', '')))}…"
    if name == "mcp__record__list_edits":
        return "Looking at the edits waiting…"
    if name in ("mcp__record__revise_edit", "mcp__record__withdraw_edit"):
        return "Revising an edit…" if name.endswith("revise_edit") else "Taking back an edit…"
    return f"{name}…"


class ToolSocket:
    """Where the agent's own tools come in (propose_edit): a Unix socket of Record's, its owner's only, that the relay
    Claude Code runs as the agent's MCP server (AGENT_RELAY) talks to, a call a line. A call's answered on the main loop
    (handler(tool, args) -> {"text"} or {"error"}), its thread waiting for that."""

    _one = []

    @classmethod
    def get(cls):
        if not cls._one:
            cls._one.append(cls())
        return cls._one[0]

    def __init__(self):
        self.handler = None
        self.path = os.path.join(tempfile.gettempdir(), f"record-tools-{os.getpid()}.sock")
        for old in glob.glob(os.path.join(tempfile.gettempdir(), "record-tools-*.sock")):  # (left by a Record gone, or
            try:                                                                            # restarted: no atexit then)
                pid = int(os.path.basename(old)[len("record-tools-"):-len(".sock")])
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
        threading.Thread(target=self._serve, args=(srv,), daemon=True, name="record-tools").start()
        import atexit
        atexit.register(lambda: os.path.exists(self.path) and os.unlink(self.path))

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
            if req.get("tool") == "_tools":
                res = {"tools": AGENT_MCP_TOOLS}
            else:
                box, done = {}, threading.Event()
                GLib.idle_add(self._call, req.get("tool"), req.get("args") or {}, box, done)
                res = box.get("result") if done.wait(120) else None
                res = res or {"error": f"{noun()} didn't answer: try again"}
            conn.sendall((json.dumps(res, ensure_ascii=False, default=str) + "\n").encode())
        except (OSError, ValueError) as e:
            try:
                conn.sendall((json.dumps({"error": str(e)}) + "\n").encode())
            except OSError:
                pass
        finally:
            conn.close()

    def _call(self, tool, args, box, done):
        try:
            box["result"] = self.handler(tool, args) if self.handler is not None else {"error": f"{noun()} is closing"}
        except Exception as e:  # noqa: BLE001 (the agent's told; Record goes on)
            box["result"] = {"error": f"{type(e).__name__}: {e}"}
        done.set()
        return False


class AgentProc(ClaudeProc):
    """baudelib's ClaudeProc, on GTK's main loop, in Record's environment for claude (child_env)."""
    dispatch = staticmethod(GLib.idle_add)
    env_of = staticmethod(child_env)


class Agent:
    """Record's agent: a Claude Code session for a notebook's conversation (one turn at a time: ask), on the model
    Settings → Intelligence says. Its tools only read (AGENT_TOOLS), in Claude Code's own default permission mode (Baude's
    Manual): what's in the pages' folder and the notebook's folders it reads as it likes; anything else it asks for,
    and is let read what the user referenced, else the user's asked (RecordChat.ask_permission); anything but its tools
    it's refused. Its own tool, propose_edit, comes in on the ToolSocket. Its callbacks reach chat on the main loop:
    _delta, _status, _done, agent_gone."""

    STOP_WAIT = 3000  # ms for an interrupted turn to end, before the session's stopped

    def __init__(self, chat, notebook, model, folders):
        self.chat, self.notebook, self.model, self.folders = chat, notebook, model, list(folders)
        self.key = Agent.key_for(notebook, model, folders)
        self.turn, self.fresh, self.stderr, self.closed = None, True, [], False
        self.allowed = set()  # (paths the user referenced: read when it asks)
        tools = ToolSocket.get()
        tools.handler = chat._tool
        relay = {"mcpServers": {"record": {"command": sys.executable, "args": ["-I", "-c", AGENT_RELAY, tools.path]}}}
        args = ["--system-prompt", SYSTEM.format(name=noun(), to_learn=TO_LEARN), "--tools", ",".join(AGENT_TOOLS),
                "--mcp-config", json.dumps(relay), "--strict-mcp-config",
                "--allowedTools", ",".join(f"mcp__record__{t['name']}" for t in AGENT_MCP_TOOLS),  # (they write
                # nothing: asking for them would be noise)
                "--no-session-persistence",  # (no transcript: it would be listed with your chats, for @ and ⌘K)
                "--disable-slash-commands", "--setting-sources", ""]
        for folder in self.folders:
            args += ["--add-dir", folder]
        self.proc = AgentProc(self.on_msg, self.on_exit, pages_root(), model, "default", extra=args, env=AGENT_ENV,
                              exe=claude_exe())

    @staticmethod
    def key_for(notebook, model, folders):
        """What a session's made for: one made for another is closed, and a new one started (RecordChat.submit)."""
        return notebook.get("id"), model, tuple(folders)

    def ask(self, text, turn):
        """A message (turn: what the reply's made of, as it comes: text, proposals, the selection it's about)."""
        self.turn, self.fresh = turn, False
        self.proc.user(text)

    def stop(self):
        """The turn interrupted (Esc, the stop button): what's been said kept; the session too, unless it won't stop."""
        if self.turn is None or self.turn.get("stopped"):
            return
        self.turn["stopped"] = True
        self.chat._drop_asking(deny=True)  # (a read it's waiting to be let make: not)
        self.proc.control({"subtype": "interrupt"})
        turn = self.turn
        GLib.timeout_add(self.STOP_WAIT, lambda: self.turn is turn and self.close() and False)

    def close(self):
        """Done with: its turn ended where it is, the session stopped."""
        if self.closed:
            return False
        self.closed = True
        turn, self.turn = self.turn, None
        self.proc.kill()
        if turn is not None:
            self.chat._done(self, "".join(turn["text"]).strip(), None, turn)
        self.chat.agent_gone(self)
        return False

    def may_read(self, path):
        """Whether a path is one it reads without asking: in the pages' folder or the notebook's, or referenced."""
        if not path:
            return False  # (asked with none: Claude Code found it outside its folders some other way, a pattern's)
        path = os.path.realpath(os.path.join(pages_root(), os.path.expanduser(str(path))))
        roots = [os.path.realpath(pages_root()), *self.folders, *(p for p in self.allowed if os.path.isdir(p))]
        return path in self.allowed or any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)

    def permission(self, msg):
        rid, req = msg["request_id"], msg.get("request") or {}
        name, inp = req.get("tool_name") or "", req.get("input") or {}
        if name.startswith("mcp__record__") or name in AGENT_TOOLS and self.may_read(inp.get("file_path") or
                                                                                      inp.get("path")):
            self.proc.respond(rid, {"behavior": "allow", "updatedInput": inp})
        elif name in AGENT_TOOLS:
            self.chat.ask_permission(self, rid, name, inp)
        else:
            self.proc.respond(rid, {"behavior": "deny", "message": f"{noun()}'s agent reads, and proposes changes with "
                                    "propose_edit: it doesn't run other tools."})

    def answer(self, rid, allow, name, inp):
        """The user's answer to a read it asked for (RecordChat.ask_permission)."""
        if self.closed:
            return
        if allow:
            if path := inp.get("file_path") or inp.get("path"):
                self.allowed.add(os.path.realpath(os.path.join(pages_root(), os.path.expanduser(str(path)))))
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
                    "error": f"record: unsupported {(msg.get('request') or {}).get('subtype')}"}})
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
                self.chat._delta(self, "".join(turn["text"]))
        elif t == "assistant":
            for c in (msg.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    self.chat._status(self, tool_said(c.get("name"), c.get("input") or {}))
                elif c.get("type") == "text" and not turn.get("streamed") and c.get("text"):
                    turn["text"].append(c["text"])
                    self.chat._delta(self, "".join(turn["text"]))
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

    EXIT_WAIT = 200  # ms for what it said as it ended (stderr, read on its own thread) to come in

    def on_exit(self, code):
        if not self.closed:
            GLib.timeout_add(self.EXIT_WAIT, self._exited, code)
        return False

    def _exited(self, code):
        if self.closed:
            return False
        self.closed = True
        turn, self.turn = self.turn, None
        if turn is not None:
            error = None if turn.get("stopped") else \
                ("\n".join(self.stderr[-6:]).strip() or f"claude exited with {code}")[-600:]
            self.chat._done(self, "".join(turn["text"]).strip(), error, turn)
        self.chat.agent_gone(self)
        return False


# ---------------------------------------------------------------- UI pieces

CSS = """
.reader-user { background: @baude_raised; border-radius: 12px; padding: 8px 12px; }
.reader-assistant { padding: 4px 2px; }
.reader-assistant label.prose { font-size: 1.02em; }
.bubble-user { background: alpha(@accent_bg_color, 0.15); border-radius: 12px; padding: 8px 12px; }
.bubble-record { background: @baude_raised; border-radius: 12px; padding: 8px 12px; }
.bubble-error { background: alpha(@error_bg_color, 0.15); border-radius: 12px; padding: 8px 12px; }
label.said-refs link { color: #2f8f72; }  /* (an @reference in what you wrote: as a page's @names, RecordChat._said) */
button.edit-control { min-height: 0; min-width: 0; padding: 0; border-radius: 4px; color: white; box-shadow: none; }
button.edit-accept { background: #2ec27e; }
button.edit-accept:hover { background: #26a269; }
button.edit-reject { background: #e01b24; }
button.edit-reject:hover { background: #c01c28; }  /* (an edit's Accept and Reject, on its page: EditControls) */
.pending-bar { background: alpha(@success_color, 0.12); border-radius: 8px; padding: 1px 2px 1px 10px; }
.pending-bar button { min-height: 24px; min-width: 24px; padding: 0; }  /* (edits waiting: RecordChat._pending_bar) */
.quote-chip { background: alpha(@accent_bg_color, 0.10); border-left: 3px solid @accent_bg_color;
              border-radius: 6px; padding: 6px 8px; }
.quote-in-bubble { border-left: 3px solid alpha(@view_fg_color, 0.25); padding-left: 8px; font-size: 0.92em;
                   opacity: 0.8; }  /* (a quote as Baude's chat has one: .md-quote) */
.diary-title { font-size: 1.75em; font-weight: 800; }
.diary { font-size: 1.05em; background: transparent; }
/* Baude's look (widgets.CSS): the notebooks, the bars, the pages' list and the Record's column the frame, no lines
   between them; the page, Read and the viewers each the inside, a box set into it */
entry.diary-title { background: none; }
entry.diary-title, entry.diary-title:focus-within { outline: none; box-shadow: none; padding-left: 0; padding-right: 0; }
paned.notes-split > separator { background: none; background-image: none; box-shadow: none; }
.ref-card contents { padding: 6px 8px; }  /* (a name's card, just under the pointer: Window.ref_card) */
.page-placeholder { font-style: italic; }
.unknown-chip { padding: 2px 10px; border-radius: 99px; font-size: 0.9em; }
"""

# The zoom's sizes, a class for each (Window.apply_zoom). A class changes only what has it and what's in it; loading a
# style sheet anew, as this was, styles every widget of every window and tab afresh, with all of libadwaita's rules.
ZOOM_SIZES = range(round(widgets.ZOOM_MIN * 100), round(widgets.ZOOM_MAX * 100) + 1, round(widgets.ZOOM_STEP * 100))
ZOOM_CSS = "".join(f".zoom-{n} {{ font-size: {n / 100:.2f}em; }}\n" for n in ZOOM_SIZES)


def zoom_class(zoom):
    """The class for this zoom (the nearest there is: one saved by hand may be between them)."""
    return f"zoom-{min(ZOOM_SIZES, key=lambda n: abs(n - zoom * 100))}"


def set_zoom_class(widget, name):
    """widget's text the size of that class, and so all that's in it."""
    if (was := getattr(widget, "zoomed", None)) != name:
        if was:
            widget.remove_css_class(was)
        widget.add_css_class(name)
        widget.zoomed = name


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


def folder_attachment(folder):
    """A folder picked to attach (AttachDialog): no transcript (path None), the folder's name, the folder. A Record
    has it among its folders, whose names its @ and questions know."""
    folder = os.path.realpath(os.path.expanduser(folder))
    return {"path": None, "title": os.path.basename(folder.rstrip(os.sep)) or folder, "cwd": folder}


class FoldersPage(Gtk.Box):
    """The right bar's Folders page: the folders attached to the notebook shown, whose names @, ⌘K and the questions know
    (attached_folders), each with × to let it go, and + to attach another (Window.attach)."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12, margin_end=12, margin_top=4,
                         margin_bottom=12)
        self.win = win
        head = Gtk.Box(spacing=6)
        title = label("Attached Folders", css=("heading",), wrap=False)
        title.set_hexpand(True)
        head.append(title)
        self.add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Attach a folder", css_classes=["flat"])
        self.add_btn.connect("clicked", lambda *_: win.attach())
        head.append(self.add_btn)
        self.append(head)
        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"], valign=Gtk.Align.START)
        self.none = label("None yet. @, ⌘K and the questions know the files, branches and the rest of the folders "
                          "attached here.", css=("dim-label",))
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.append(self.rows)
        inner.append(self.none)
        self.append(Gtk.ScrolledWindow(child=inner, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))

    def fill(self):
        """The notebook shown's, as they are now."""
        self.rows.remove_all()
        folders = attached_folders(self.win.current) if self.win.current else []
        for folder in folders:
            row = Adw.ActionRow(title=GLib.markup_escape_text(os.path.basename(folder) or folder),
                                subtitle=GLib.markup_escape_text(short_dir(folder)))
            row.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
            gone = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                              tooltip_text="Detach this folder")
            gone.connect("clicked", lambda _b, folder=folder: self.win.detach_folder(folder))
            row.add_suffix(gone)
            self.rows.append(row)
        self.rows.set_visible(bool(folders))
        self.none.set_visible(not folders)
        self.add_btn.set_sensitive(self.win.current is not None)


class AttachDialog(Adw.Dialog):
    """Attach…: a folder to attach (any one, or one the chats ran in) whose names @, ⌘K and the questions know, one
    more of them; on_pick gets a folder_attachment. chats: Open a Chat… instead: the Claude Code chats, the one picked
    (on_pick gets its session) read on the Read tab."""

    FOLDERS = 8  # the recent chats' folders listed before you search

    def __init__(self, on_pick, chats=False):
        super().__init__(title="Open a chat" if chats else "Attach folders", content_width=560, content_height=620)
        self.on_pick, self.sessions, self.chats = on_pick, [], chats
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12, margin_end=12, margin_bottom=12)
        self.search = Gtk.SearchEntry(placeholder_text="Search chats" if chats else "Search folders")
        self.search.connect("search-changed", lambda *_: self.fill())
        box.append(self.search)
        lists = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.folders = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.folders.add_css_class("boxed-list")
        self.folders.connect("row-activated", self._activated)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.list.connect("row-activated", self._activated)
        lists.append(self.list if chats else self.folders)
        sc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=lists)
        self.spinner = Adw.Spinner()
        self.stack = Gtk.Stack()
        self.stack.add_named(self.spinner, "loading")
        self.stack.add_named(sc, "list")
        box.append(self.stack)
        tv.set_content(box)
        self.set_child(tv)
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        s = recent_sessions()
        GLib.idle_add(self._loaded, s)

    def _loaded(self, s):
        self.sessions = s
        self.fill()
        self.stack.set_visible_child_name("list")

    def fill(self):
        for lb in (self.folders, self.list):
            while (r := lb.get_row_at_index(0)) is not None:
                lb.remove(r)
        q = self.search.get_text().lower().strip()
        choose = Adw.ActionRow(title="Choose a folder…", activatable=True,
                               subtitle="Any folder: its files, branches and the rest")
        choose.add_prefix(Gtk.Image(icon_name="folder-open-symbolic"))
        choose.session = None
        self.folders.append(choose)
        seen = {}  # the folders the chats ran in, the most recent first
        for s in self.sessions:
            if s["cwd"] and os.path.isdir(s["cwd"]) and s["cwd"] not in seen:
                seen[s["cwd"]] = s["used"]
        shown = [d for d in seen if not q or q in d.lower()]
        for d in shown if q else shown[:self.FOLDERS]:
            row = Adw.ActionRow(title=GLib.markup_escape_text(os.path.basename(d.rstrip(os.sep)) or d), activatable=True)
            widgets.subtitle_time(row, seen[d], f"{short_dir(d)} · ")
            row.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
            row.session = folder_attachment(d)
            self.folders.append(row)
        n = 0
        for s in self.sessions:
            if q and q not in s["title"].lower() and q not in (s["cwd"] or "").lower():
                continue
            row = Adw.ActionRow(title=GLib.markup_escape_text(s["title"]), activatable=True)
            widgets.subtitle_time(row, s["used"], f"{short_dir(s['cwd'])} · ")
            if time.time() - s["mtime"] < 180:
                live = Gtk.Label(label="live")
                live.add_css_class("success")
                live.add_css_class("caption-heading")
                row.add_suffix(live)
            row.session = s
            self.list.append(row)
            n += 1
        self.list.set_visible(n > 0)

    def _activated(self, _lb, row):
        if row.session is None:  # Choose a folder…
            Gtk.FileDialog(title="Attach to a folder", modal=True).select_folder(
                self.get_root(), None, self._chosen)
            return
        self.on_pick(row.session)
        self.close()

    def _chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:  # (cancelled)
            return
        if folder is not None and folder.get_path():
            self.on_pick(folder_attachment(folder.get_path()))
            self.close()


_SPACES = {}


def search_space(folder):
    """⌘K's search (lookup.SearchSpace) of a chat's folder, one per folder, its catalogue begun when it's made;
    None without a folder."""
    if not folder or not os.path.isdir(folder):
        return None
    if folder not in _SPACES:
        _SPACES[folder] = lookup.SearchSpace(lookup.Folder.for_folder(folder), GLib.timeout_add)
        _SPACES[folder].refresh()  # (catalogued now: @, the chat's links and the questions find it ready)
    return _SPACES[folder]


def attached_folders(notebook):
    """The folders a notebook knows the names of (Attach…), each once, as they are (realpath)."""
    notebook, out = notebook or {}, []
    for f in list(notebook.get("folders") or []):
        f = os.path.realpath(os.path.expanduser(f))
        if os.path.isdir(f) and f not in out:
            out.append(f)
    return out


def backup_zip():
    """The rolling backup (Window.roll_backup), in a .local folder, on a Mac too."""
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "record", "notebooks-backup.zip")


def conversation_text(notebook):
    """A notebook's conversation with the Record, as Markdown."""
    def quoted(text):
        return "".join(f"> {line}\n" for line in text.splitlines()) + "\n" if text else ""
    return "\n\n".join(f"**{'You' if m.get('role') == 'user' else noun()}:**\n\n{quoted(m.get('quote'))}{m.get('text', '')}"
                       for m in notebook.get("messages") or [])


def glossary_text(notebook):
    """What's been looked up in a notebook's folders (Signal's glossary), as Markdown: each name, and what it is."""
    parts = []
    for folder in attached_folders(notebook):
        if refs := lookup.Folder.for_folder(folder).glossary():
            parts.append(f"# {folder}\n\n" + "\n".join(f"- **{ref.surface}**: {ref.summary()}" for ref in refs))
    return "\n\n".join(parts)


def chat_text(path, title, cwd):
    """What's shown of a chat (parse_records: what's said, not the tools' calls or what they gave back), as Markdown."""
    items = []
    with open(path, encoding="utf-8", errors="replace") as f:
        parse_records(f, items)
    head = f"# {title or 'Untitled chat'}\n\n{cwd or ''}".strip()
    return "\n\n".join([head] + [f"**{'You' if it['role'] == 'user' else 'Claude'}:**\n\n{it['text']}" for it in items
                                  if it["role"] != "note"])  # (a task's ending: said by neither)


def chats_of(notebook, transcripts):
    """Of transcripts [(path, title, cwd)], those a notebook has to do with: in a folder attached to it, or cited or named in
    its notes (a notebook has no chat of its own)."""
    folders = attached_folders(notebook)
    notes = "\n".join(notebook["pages"].values())
    cited = set(re.findall(re.escape(CITE_SCHEME) + r"([\w-]+)", notes))
    named = {m.group(1)[1:].strip() for m in LINK_RE.finditer(notes) if m.group(1).lstrip().startswith("@")} | \
        {m.group(1) or m.group(2) for m in REF_RE.finditer(notes)}

    def here(cwd):
        cwd = os.path.realpath(cwd) if cwd else None
        return cwd and any(cwd == f or cwd.startswith(f + os.sep) for f in folders)
    return [t for t in transcripts if here(t[2]) or t[1] in named or os.path.basename(t[0])[:-len(".jsonl")] in cited]


def zip_entries(notebooks, transcripts=None):
    """What's in a zip of notebooks (export_zip, zip_sizes), in order: (its name, its text), or for a chat the chat itself,
    (path, title, cwd), its text being the long part. transcripts [(path, title, cwd)]: the glossary and the chats too."""
    paths, books = page_paths(notebooks), book_folders(notebooks)
    for notebook in notebooks:
        book = books[notebook.get("id")]
        for name, text in notebook["pages"].items():
            yield os.path.join("notes", paths[notebook.get("id"), name]), text
        yield os.path.join("conversations", book + ".md"), conversation_text(notebook)
        if transcripts is not None:
            yield os.path.join("glossary", book + ".md"), glossary_text(notebook)
            for chat in chats_of(notebook, transcripts):
                session = os.path.basename(chat[0])[:-len(".jsonl")]
                yield os.path.join("chats", book, f"{file_part(chat[1] or 'Untitled chat')} ({session[:8]}).md"), chat


def export_zip(path, notebooks, extras=False):
    """Notebooks in a zip at path, whole or not at all: notes/<notebook>/<page>.md (Store's folders), and
    conversations/<notebook>.md; extras, the glossary (glossary/<notebook>.md) and the chats (chats/<notebook>/<chat>.md) too.
    notebooks: a copy, this is to run off the main loop: it reads every chat a notebook has to do with, and they can be large."""
    transcripts = [(p, *session_info(p)) for p in chat_transcripts()] if extras else None
    texts = {}  # (a chat of two notebooks: read once)
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for name, text in zip_entries(notebooks, transcripts):
                if isinstance(text, tuple):
                    if text[0] not in texts:
                        texts[text[0]] = chat_text(*text)
                    text = texts[text[0]]
                if text.strip():
                    z.writestr(name.replace(os.sep, "/"), text.encode("utf-8", "replace"))
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())  # (on the disk before it takes the last one's place)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


_ZIPPED = {}  # (a transcript's path: [its mtime, size and whether it can be read; its (title, cwd); what its chat comes to in a zip]: zip_sizes)
_ZIP_SIZES = threading.Lock()  # (one at a time: Settings opened twice doesn't read a large chat twice)


def deflated_size(text):
    """What a text comes to in a zip, as zipfile deflates it."""
    z = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
    return len(z.compress(text.encode("utf-8", "replace"))) + len(z.flush())


def zip_sizes(notebooks, stop=None):
    """The bytes export_zip makes of notebooks, (without the glossary and chats, with them), exactly, with nothing written;
    with them, None if a chat can't be read. notebooks: a copy, this is to run off the main loop: the first time, it reads every
    chat they have to do with; after that, only a transcript that's changed (_ZIPPED). stop: a threading.Event, set once it's
    not wanted: it soon comes back with None."""
    with _ZIP_SIZES:
        transcripts, deflated = [], {}
        for p in chat_transcripts():
            if stop is not None and stop.is_set():
                return None
            try:
                st = os.stat(p)
            except OSError:  # (gone since it was listed)
                continue
            seen = (st.st_mtime_ns, st.st_size, os.access(p, os.R_OK))
            known = _ZIPPED.get(p)
            if known is None or known[0] != seen:
                known = _ZIPPED[p] = [seen, session_info(p), None]
            transcripts.append((p, *known[1]))

        def size(chats):
            total = 22  # (the zip's end; a file has 30 bytes and its name before it, 46 and its name at the end)
            for name, text in zip_entries(notebooks, chats):
                if stop is not None and stop.is_set():
                    return None
                if isinstance(text, tuple):
                    known = _ZIPPED[text[0]]
                    if known[2] is None:
                        known[2] = deflated_size(chat_text(*text))
                    total += 76 + 2 * len(name.encode("utf-8")) + known[2]
                elif text.strip():
                    if text not in deflated:
                        deflated[text] = deflated_size(text)
                    total += 76 + 2 * len(name.encode("utf-8")) + deflated[text]
            return total
        alone = size(None)
        if alone is None:
            return None
        try:
            whole = size(transcripts)
        except Exception as e:  # (a chat gone as it was read, say. The export says too, if it's still so)
            print(f"Record: an export's size with its chats: {e!r}", file=sys.stderr)
            return alone, None
        return None if whole is None else (alone, whole)


def size_said(n):
    """A number of bytes in words: 412 bytes, 3.4 KB, 12 KB, 3.4 MB, 177 MB."""
    if n < 1000:
        return f"{n} bytes"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1000
        if n < 999.5 or unit == "TB":
            break
    return f"{n:.1f} {unit}" if n < 9.95 else f"{round(n)} {unit}"


class Spaces(lookup.SearchSpace):
    """⌘K's search over several folders (a Record attached to more than one): each folder's names, its folder's name
    said with each; a name two have is the first folder's (the chat's, then as they were attached). An item keeps its
    folder as root."""

    def __init__(self, spaces):
        super().__init__(None, GLib.timeout_add)
        self.spaces = spaces

    @property
    def root(self):
        return self.spaces[0].root

    @property
    def busy(self):
        return any(s.busy for s in self.spaces)

    def refresh(self, on_ready=None):
        for s in self.spaces:
            if (waiting := s.refresh()) is not None:  # (each its own: a folder's catalogue, or the chats')
                GLib.source_remove(waiting)
        if not self.busy:
            return None

        def check():
            if self.busy:
                return True
            self._items = None
            for s in self.spaces:
                s._items = None  # (theirs too: each made afresh)
            if on_ready is not None:
                on_ready()
            return False
        return GLib.timeout_add(400, check)

    def make_items(self):
        out, on_github = [], set()
        for s in self.spaces:
            if s.root is None:  # (the chats: no folder of theirs)
                out += s.items()
                continue
            name = os.path.basename(s.root.rstrip(os.sep)) or s.root
            for it in s.items():
                if it.get("label") is None:
                    out.append({**it, "root": s.root, "summary": f"{name} · {it['summary']}"})
                elif it["surface"] not in on_github:  # (GitHub's say their repository: and once, if two folders have it)
                    on_github.add(it["surface"])
                    out.append({**it, "root": s.root})
        return out

    def items(self):
        parts = tuple(s.items() for s in self.spaces)  # (made again once any folder's are: lookup.SearchSpace.items)
        if self._items is None or any(x is not y for x, y in zip(parts, getattr(self, "_parts", ()))):
            self._items, self._by, self._parts = self.make_items(), {}, parts
            for it in self._items:
                self._by.setdefault(it["surface"], it)  # (the first folder's)
        return self._items


_MANY = {}


def search_spaces(folders, notes=None):
    """What @ and ⌘K search: a Record's folders (attached_folders), each one's names (search_space), every Claude
    Code chat by its title (chat_names), and with notes (NotesNames) the notebooks and their pages, in one (Spaces)."""
    key = (tuple(folders), id(notes))
    if key not in _MANY:
        _MANY[key] = Spaces([search_space(f) for f in folders] + [chat_names()] + ([notes] if notes else []))
    return _MANY[key]


class ChatNames(lookup.SearchSpace):
    """Every Claude Code chat by its title (once: chat_transcripts), for @ and ⌘K (Claude's logo before it): its
    folder and how long ago, said. Read in the background (a transcript's title once, again when it changes),
    afresh every TTL seconds."""

    TTL = 120
    _info = {}  # (a transcript: (its mtime, its size, its title, its folder, when it was last typed in))

    def __init__(self):
        super().__init__(None, GLib.timeout_add)
        self.chats, self.at, self.reading = [], 0.0, False

    @property
    def root(self):
        return None

    @property
    def busy(self):
        return self.reading

    def refresh(self, on_ready=None):
        if not self.reading and time.time() - self.at > self.TTL:
            self.reading = True
            threading.Thread(target=self._read, daemon=True, name="record-chats").start()
        if not self.reading:
            return None

        def check():
            if self.reading:
                return True
            if on_ready is not None:
                on_ready()
            return False
        return GLib.timeout_add(400, check)

    def _read(self):
        chats = []
        try:
            for p in chat_transcripts():
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                known = self._info.get(p)
                if known is None or known[:2] != (st.st_mtime, st.st_size):
                    title, cwd, said = session_facts(p)
                    known = self._info[p] = (st.st_mtime, st.st_size, title, cwd, said or st.st_mtime)
                if known[2]:  # (used: when it was last typed in, what ⌘K's chats go by)
                    chats.append({"path": p, "title": known[2], "cwd": known[3], "mtime": known[0], "used": known[4]})
            chats.sort(key=lambda c: -c["used"])
        finally:
            self.chats, self.at, self.reading = chats, time.time(), False

    def make_items(self):
        return [{"surface": c["title"], "canonical": "", "icon": "claude", "what": "Chat", "kind": "chat",
                 "at": c["used"], "ago": (ago := widgets.time_ago(c["used"])),  # (under the pointer: widgets.time_tip)
                 "where": (where := short_dir(c["cwd"]) if c["cwd"] else "no folder"), "summary": f"{where} · {ago}",
                 "path": c["path"], "line": None, "entry": None, "faint": False} for c in self.chats]

    def items(self):
        if self._items is None or self._made is not self.chats:
            self._items, self._made = self.make_items(), self.chats
            self._by = {}
            for it in self._items:
                self._by.setdefault(it["surface"], it)
        return self._items


_CHATS = []


def chat_names():
    """The chats' names (ChatNames): one for the app."""
    if not _CHATS:
        _CHATS.append(ChatNames())
        _CHATS[0].refresh()
    return _CHATS[0]


class NotesNames(lookup.SearchSpace):
    """The notes, for @ (the notes' and the Record's box) and ⌘K, beside the folders' names and the chats: every
    notebook, and each one's pages, a page's or a notebook's logo before it. The open notebook's pages go by their
    names (Spec), another's by its notebook's and its own (Garden/Spec). Made afresh as pages and notebooks come, go and
    are renamed. current(): the notebook open, or None."""

    KINDS = ("page", "notebook")

    def __init__(self, store, current):
        super().__init__(None, GLib.timeout_add)
        self.store, self.current = store, current

    @property
    def root(self):
        return None

    @property
    def busy(self):
        return False

    def refresh(self, on_ready=None):
        return None

    def _key(self):
        mine = self.current()
        return (mine or {}).get("id"), tuple((t.get("id"), t["name"], tuple(t["pages"])) for t in self.store.notebooks)

    def make_items(self):
        mine, out = self.current(), []
        books = ([mine] if mine else []) + [t for t in self.store.notebooks if t is not mine]
        for t in books:
            name, pages = full_name(t), sorted(t["pages"], key=lambda p: (p != HOME, p.lower()))
            for page in pages:
                out.append({"surface": page if t is mine else f"{name}/{page}", "canonical": "", "icon": "page",
                            "what": "Page", "kind": "page", "summary": name, "where": name, "notebook": t.get("id"),
                            "page": page, "mine": t is mine, "line": None, "entry": None, "faint": False})
            out.append({"surface": name, "canonical": "", "icon": "notebook", "what": "Notebook", "kind": "notebook",
                        "summary": f"{len(pages)} page{'s' if len(pages) != 1 else ''}", "where": "",
                        "notebook": t.get("id"), "mine": t is mine, "line": None, "entry": None, "faint": False})
        return out

    def items(self):
        key = self._key()
        if self._items is None or key != self._made:
            self._items, self._made, self._by = self.make_items(), key, {}
            for it in self._items:
                self._by.setdefault(it["surface"], it)
        return self._items


def notes_first(items):
    """@'s list with nothing typed after the @ yet: the notebook's own pages first, then the notebooks, the other
    notebooks' pages, and the rest."""
    def rank(it):
        return 0 if it.get("kind") == "page" and it.get("mine") else 1 if it.get("kind") == "notebook" else \
            2 if it.get("kind") == "page" else 3
    return sorted(items, key=rank)


class ReaderNames:
    """What Baude's markdown (widgets.MarkdownView, through widgets.SIGNAL) asks of a Signal to link a reply's names,
    from the notebook's search (Record has no Signal, and looks nothing up): a name its folders have is a link with
    its logo before it, as Baude links what its catalogue has; clicked, it says what it is (Window.open_ref). folder:
    the chat's, whose repository is what a bare PR #12 in it means (never a vendored checkout's)."""

    phrase_re, strong, _by_surface = None, frozenset(), {}

    def __init__(self, space, folder=None):
        self.space, self.refs, self.catalogue, self.folder = space, {}, {}, folder and os.path.realpath(folder)
        self.update()

    def update(self):
        """The names as the folders have them now (once they're catalogued)."""
        refs, catalogue, own = {}, {}, lookup.GITHUB.own_repository(self.folder)
        for it in self.space.items():
            s = it["surface"]
            if s in refs:
                continue
            if it.get("entry") is not None:
                catalogue[s] = it["entry"]
                refs[s] = lookup.catalogue_ref(s, it["entry"])
                if it.get("label") and it["entry"]["github"]["repo"] == own:
                    refs[f"PR #{it['label']}"] = refs[s]
            elif (ref := self._looked_up(s)) is not None:  # (one Baude's Signal looked up: its glossary's)
                refs[s] = ref
        self.refs, self.catalogue = refs, catalogue

    def _looked_up(self, surface):
        for space in getattr(self.space, "spaces", [self.space]):
            if (ref := getattr(space.source, "refs", {}).get(surface)) is not None:
                return ref
        return None

    def decision(self, _message, _surface):
        return None

    def reading(self):
        return False

    def message_phrases(self, _message):
        return None

    said = {}  # (each reply's "the X"s nothing introduced, by its hash: Reader.mark_nouns)

    def unexplained(self, message):
        return self.said.get(message)


def nouns_command():
    """How nouns.py is run, with NLTK: by uv, in an environment of its own (NLTK fetched once, Python 3.10 to 3.14,
    the ones NLTK's wheels are for); else by this Python if it has NLTK. None: neither (or RECORD_NOUNS=off)."""
    if os.environ.get("RECORD_NOUNS") == "off":
        return None
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nouns.py")
    uv = shutil.which("uv") or next((p for p in ("/opt/homebrew/bin/uv", "/usr/local/bin/uv",
                                                 os.path.expanduser("~/.local/bin/uv"),
                                                 os.path.expanduser("~/.cargo/bin/uv")) if os.path.exists(p)), None)
    if uv:
        return [uv, "run", "--quiet", "--no-project", "--python", ">=3.10,<3.15", "--with", "nltk==3.9.2",
                "python", "-I", script]
    import importlib.util
    return [sys.executable, "-I", script] if importlib.util.find_spec("nltk") else None


class Unexplained:
    """nouns.py kept running (NLTK loads once, and a message is tagged once), asked from a thread: ask(request,
    done) calls done(flagged) on the main loop. Asked again while it works, only the newest is asked next. If it
    can't be started (no uv, no network for NLTK the first time), it's tried again after 5 minutes."""

    RETRY = 300

    def __init__(self):
        self.proc, self.next, self.busy, self.failed = None, None, False, 0.0
        self.lock = threading.Lock()

    def ask(self, request, done):
        with self.lock:
            self.next = (request, done)
            if self.busy:
                return
            self.busy = True
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        while True:
            with self.lock:
                if self.next is None:
                    self.busy = False
                    return
                (request, done), self.next = self.next, None
            if (flagged := self._ask(request)) is not None:
                GLib.idle_add(lambda d=done, f=flagged: d(f) and False)

    def _ask(self, request):
        if self.proc is None or self.proc.poll() is not None:
            cmd = nouns_command()
            if cmd is None or time.monotonic() - self.failed < self.RETRY:
                return None
            env = dict(os.environ, RECORD_NLTK_DATA=os.environ.get("RECORD_NLTK_DATA")
                       or os.path.join(data_dir(), "nltk_data"))
            try:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
            except OSError as e:
                print("record: nouns.py:", e, file=sys.stderr)
                self.failed = time.monotonic()
                return None
        try:
            self.proc.stdin.write(json.dumps(request) + "\n")
            self.proc.stdin.flush()
            answer = json.loads(self.proc.stdout.readline() or "null")
        except (OSError, ValueError):
            answer = None
        if not isinstance(answer, dict) or "flagged" not in answer:
            print("record: nouns.py:", (answer or {}).get("error", "stopped"), file=sys.stderr)
            self.failed = time.monotonic()
            self.stop()
            return None
        return answer["flagged"]

    def stop(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc = None


UNEXPLAINED = Unexplained()


class ZoomPlace:
    """A scrolled window's place kept through a change of its text's size (the zoom, Settings → Size), as Baude's chat
    keeps its through one (BaudeWindow.keep_place_through_restyle). Laying out a long chat at a new size takes seconds,
    so only what's near the view is laid out at once: the rest is hidden (nor is it styled) and comes back a few messages
    a frame, nearest first. Each frame, after GTK's layout and before anything is drawn, what was at the top of the view
    is put back there, as far through its message as it was (or the view kept at the end, if it was there). widget: what
    has the zoom's class; its messages are box's children (DiaryPlace: a page of notes has none)."""

    BACK = 32  # messages back a frame
    ABOVE, BELOW = 1, 2  # (the pages laid out at once, above and below the view: the next few are a frame away)
    CALM = 3  # (frames of the same height that say it's done: a code block follows the view's height a frame later)

    def __init__(self, scroll, widget, box=None):
        self.scroll, self.widget, self.box, self.job = scroll, widget, box, None
        self.paused = False  # (what's hidden kept hidden, its width still changing: Window._side_moving)
        self.content = scroll.get_child().get_child()  # (what's scrolled: inside its viewport)
        self.left = False  # (messages still hidden: the tab left before they were all back)
        self.away = None  # (its tab not shown as every widget's size changed: put_away)
        self.before = None  # (where it was read to as the zoom began, at the new size as near as can be said: saved)
        scroll.connect("map", self._shown)

    def _shown(self, _scroll):
        if self.left or self.away is not None:
            self.through()

    def extent(self, anchor):
        """Where anchor is in what's scrolled, (y, height); None if it can't be said."""
        ok, b = anchor.compute_bounds(self.content)
        return (b.get_y(), b.get_height()) if ok else None

    def saved(self):
        """Where it's been read to, for a restart: with messages still hidden, its tab left in the middle of a zoom, its
        scrollbar's place says nothing of that, so where it was as the zoom began."""
        return self.before if (self.left or self.away is not None) and self.before else widgets.scroll_place(self.scroll)

    def split(self):
        """(What's at the top of the view, the messages above it to bring back, those below it to bring back, those
        left near it), the ones far from the view hidden. (Those shown as this frame began aren't laid out, at the new
        size or anywhere: where they are can't be read, so they go back to being hidden.)"""
        for unit in self.job["fresh"] if self.job else ():
            self._hide(unit)
        adj = self.scroll.get_vadjustment()
        top, page = adj.get_value(), adj.get_page_size()
        anchor, above, below, near = None, [], [], []
        unit = self.box.get_first_child()
        while unit is not None:
            if getattr(unit, "zoom_spacer", False):  # (a hidden one's room)
                pass
            elif getattr(unit, "zoom_hidden", False):  # (still hidden from the last time)
                (below if anchor else above).append(unit)
            elif unit.get_visible() and (at := self.extent(unit)) is not None:
                if anchor is None and at[0] + at[1] > top:
                    anchor = unit
                if at[0] + at[1] < top - self.ABOVE * page or at[0] > top + self.BELOW * page:
                    self._hide(unit, at[1])
                    (below if anchor else above).append(unit)
                else:
                    near.append(unit)
            unit = unit.get_next_sibling()
        below.reverse()  # (both lists pop() the message nearest the view first)
        if near:  # (what's left shown, and how tall it was: how much taller it's grown says the rooms', _rescale)
            self.near_was = [(unit, at[1]) for unit in near if (at := self.extent(unit)) is not None]
        return anchor, above, below, near

    def _hide(self, unit, height=None):
        """A message hidden, an empty box as tall as it was in its place: the chat stays about as tall as it was, so its
        scrollbar stays still while messages are hidden (without, it grew to most of the bar and jumped about as they
        came back)."""
        if height is None:
            ok, b = unit.compute_bounds(self.content)
            height = b.get_height() if ok else 0
        height += unit.get_margin_top() + unit.get_margin_bottom()  # (the room it took, its margins too)
        unit.set_visible(False)
        unit.zoom_hidden = True
        if (room := getattr(unit, "zoom_room", None)) is None:
            room = unit.zoom_room = Gtk.Box()
            room.zoom_spacer = True  # (not a message: split passes it by)
            self.box.insert_child_after(room, unit)
        room.zoom_was = height  # (as tall as it was: as it would be now, _rescale)
        room.set_size_request(-1, max(0, round(height * getattr(self, "grown", 1.0))))

    def _rescale(self):
        """The hidden messages' rooms as tall as they'd be now, as near as can be said: as much taller (narrower, or its
        text bigger) as what's still shown has grown since. So the chat's height, and its scrollbar's thumb, change as
        smoothly as the text does, not in steps as each message comes back to its height at the new width."""
        was = now = 0.0
        for unit, height in getattr(self, "near_was", ()):
            if unit.get_visible() and unit.get_mapped() and (at := self.extent(unit)) is not None:
                was += height
                now += at[1]
        if was <= 0:
            return
        self.grown = now / was
        unit = self.box.get_first_child()
        while unit is not None:
            if getattr(unit, "zoom_spacer", False):
                want = max(0, round(unit.zoom_was * self.grown))
                if unit.get_size_request()[1] != want:
                    unit.set_size_request(-1, want)
            unit = unit.get_next_sibling()

    def _show(self, unit):
        if (room := getattr(unit, "zoom_room", None)) is not None:  # (gone with it: only there while it's hidden)
            self.box.remove(room)
            unit.zoom_room = None
        unit.set_visible(True)
        unit.zoom_hidden = False

    def through(self, change=None, ratio=1.0):
        """change() (the text's size, ratio times what it was), the place kept and what's far off brought back in the
        frames to come. In the middle of one, what the reader's place was stays what it's been noted as: the chat's
        height and its scrollbar are in no state to say."""
        clock, adj = self.scroll.get_frame_clock(), self.scroll.get_vadjustment()
        if clock is None or adj.get_page_size() <= 0 or not self.scroll.get_mapped():  # (no place to keep: not shown)
            return change() if change else None
        if self.away is not None:  # (as it was when it was put away: nothing of it has been laid out since)
            anchor, above, below, near, place = self.away
            self.away = None
            for unit in near:
                self._show(unit)
        else:
            if self.job is None and not self.left and (place := widgets.scroll_place(self.scroll)):
                self.before = {**place, "value": place["value"] * ratio}
            if self.job is None:  # (how far down it is, as the scrollbar has it: held there meanwhile, _hold)
                adj = self.scroll.get_vadjustment()
                self.thumb = adj.get_value() / adj.get_upper() if adj.get_upper() > 0 else 0.0
            anchor, above, below, _near = self.split()
            place = None
        if change:
            change()
        job = self.job
        going = job is not None
        if going and job.get("anchor") is not None:
            anchor = job["anchor"]
        if job is None:
            job = self.job = {"clock": clock, "handlers": (clock.connect("before-paint", self._back),
                                                           clock.connect_after("update", self._frame),
                                                           clock.connect_after("layout", self._hold))}
            clock.begin_updating()  # (a frame for each lot, whatever else has one)
            self.scroll.held = True  # (what's hidden shrinks the chat, to its end for a frame: not to be followed from)
        job.update(anchor=anchor, above=above, below=below, upper=None, calm=0, frames=0, shown=0, fresh=[])
        self.left = False
        if not going:
            job["kept"] = None
            self.note()
        if place is not None:
            job["bottom"], job["through"] = place
        job["put_back"] = place is not None  # (the place as it was put away: till it's been put back, _frame leaves it)

    def note(self):
        """Where the reader is, as last drawn and scrolled since: the end, or how far through what's at the top. Still
        where _hold put it, or pushed back to the end of what's laid out (no hand did that: messages are hidden), it
        stands as it was."""
        job, adj = self.job, self.scroll.get_vadjustment()
        value, at_end = adj.get_value(), adj.get_value() + adj.get_page_size() >= adj.get_upper() - 1
        if (kept := job["kept"]) is not None and (abs(value - kept) <= 0.5 or value < kept and at_end):
            return
        job["bottom"] = at_end
        job["through"] = None
        if job["anchor"] is not None and (at := self.extent(job["anchor"])) is not None:
            job["through"] = (value - at[0]) / at[1] if at[1] > 0 else 0.0
        job["mark"] = self._mark(value) if job["anchor"] is not None and not at_end else None

    MARK_UNITS = 3  # (messages looked through, from the one at the top, for text to hold the place by)

    def _mark(self, top):
        """The text at the top of the view: (a label, its character there, how far below the view's top that character's
        line is), its line held there as everything round it rewraps (_hold). A message's place as a fraction of it, as
        before, wasn't: its text rewrapped, the line at the top moved a line's height up or down, a jitter all through a
        side bar's slide. None: no text near the top."""
        unit, looked = self.job["anchor"], 0
        while unit is not None and looked < self.MARK_UNITS:
            if unit.get_visible() and not getattr(unit, "zoom_spacer", False):
                looked += 1
                for w in widgets.walk(unit):
                    if not isinstance(w, Gtk.Label) or not w.get_mapped() or not w.get_text():
                        continue
                    ok, b = w.compute_bounds(self.content)
                    if not ok or b.get_y() + b.get_height() <= top:
                        continue
                    _x, y = w.get_layout_offsets()
                    _in, index, _trailing = w.get_layout().xy_to_index(0, int(max(0.0, top - b.get_y() - y) * Pango.SCALE))
                    if (at := self._char_y(w, index)) is not None:
                        return w, index, at - top
            unit = unit.get_next_sibling()
        return None

    def _char_y(self, label, index):
        """Where label's character index's line is, in what's scrolled; None if it can't be said."""
        ok, at = label.compute_point(self.content, Graphene.Point().init(0, 0))
        if not ok:
            return None
        _x, y = label.get_layout_offsets()
        return at.y + y + label.get_layout().index_to_pos(index).y / Pango.SCALE

    def _back(self, _clock):
        """As each frame begins: the next few hidden messages shown, styled at the new size in this frame (shown after
        GTK's styling, in the frame's update, one was drawn at the old size, then the new)."""
        job = self.job
        if job is None or not self.scroll.get_mapped():
            return
        shown = 0
        while not self.paused and shown < self.BACK and (job["above"] or job["below"]):
            for side in (job["below"], job["above"]):
                if side:
                    unit = side.pop()
                    self._show(unit)
                    job["fresh"].append(unit)
                    shown += 1
        job["shown"] = shown

    def _frame(self, _clock):
        """Before each frame's layout: the place noted afresh (the reader can scroll meanwhile, and isn't pulled back:
        what _back just brought back isn't laid out yet); done once all's back and the height's stopped changing. The
        tab left meanwhile: what's still hidden waits till it's shown again."""
        job = self.job
        if job is None:
            return
        if not self.scroll.get_mapped():
            self.left = bool(job["above"] or job["below"])
            return self.stop()
        self._rescale()
        if not job["put_back"]:
            self.note()
        if job["shown"]:
            return
        if self.paused:  # (not done: what's hidden comes back once it's let go)
            job["calm"], job["frames"] = 0, 0
            return
        upper = self.scroll.get_vadjustment().get_upper()
        job["calm"] = job["calm"] + 1 if upper == job["upper"] else 0
        job["upper"], job["frames"] = upper, job["frames"] + 1
        if job["calm"] >= self.CALM or job["frames"] > 60:
            self.stop()

    def _hold(self, _clock):
        """After each frame's layout, before it's drawn: the place put back."""
        job, adj = self.job, self.scroll.get_vadjustment()
        if job is None:
            return
        mark, to = job.get("mark"), None
        if job["bottom"]:
            to = adj.get_upper() - adj.get_page_size()
        elif mark is not None and mark[0].get_mapped() and (y := self._char_y(mark[0], mark[1])) is not None:
            to = y - mark[2]  # (the line that was at the top, where it was)
        elif job["through"] is not None and (at := self.extent(job["anchor"])) is not None:
            to = at[0] + job["through"] * at[1]
        if to is not None and abs(round(to) - adj.get_value()) >= 1:  # (whole pixels: a fraction moved the scrollbar's
            adj.set_value(round(to))                                     # thumb, and showed it, each frame for nothing)
        # the scrollbar's thumb where it was: its top is how far down the value is of all there is, and what's hidden
        # and what's rewrapping changed all there is a little each frame, the thumb a pixel up or down. So all there is,
        # as the scrollbar has it, is what keeps it there (never less than the view past where it is); the true height's
        # back as it's done
        if not job["bottom"] and (thumb := getattr(self, "thumb", 0.0)) > 0 and adj.get_value() > 0:
            upper = max(adj.get_value() + adj.get_page_size(), adj.get_value() / thumb)
            if abs(upper - adj.get_upper()) >= 0.5:
                adj.set_upper(upper)
        job["kept"], job["fresh"], job["put_back"] = adj.get_value(), [], False

    def stop(self):
        job, self.job = self.job, None
        if getattr(self, "thumb", 0.0) > 0:  # (the scrollbar's own height back: laid out again, it says what's there)
            self.content.queue_resize()
        self.near_was, self.grown, self.thumb = (), 1.0, 0.0
        if job is not None:
            for handler in job["handlers"]:
                job["clock"].disconnect(handler)
            job["clock"].end_updating()
            if getattr(self.scroll, "let_go", None) is None:  # (unless it's being held somewhere: widgets.hold_place)
                self.scroll.held = False

    def zoom(self, name):
        """The text the size of class name: a tab's as it's shown, whatever size it was left at."""
        if (was := getattr(self.widget, "zoomed", None)) != name:
            sizes = [int(n.rpartition("-")[2]) if n and n.rpartition("-")[2].isdigit() else 0 for n in (was, name)]
            self.through(lambda: set_zoom_class(self.widget, name), sizes[1] / sizes[0] if all(sizes) else 1.0)

    def put_away(self):
        """Its tab not shown as every widget's size changes with it (Settings → Size): its place kept, and nothing of it
        styled or laid out till it's shown (through)."""
        adj = self.scroll.get_vadjustment()
        if self.away is not None or adj.get_page_size() <= 0:
            return
        self.before = self.before if self.left else widgets.scroll_place(self.scroll)
        anchor, above, below, near = self.split()
        at = self.extent(anchor) if anchor is not None else None
        through = (adj.get_value() - at[0]) / at[1] if at and at[1] > 0 else None
        for unit in near:
            self._hide(unit)
        self.away = anchor, above, below, near, (adj.get_value() + adj.get_page_size() >= adj.get_upper() - 1, through)


class DiaryPlace(ZoomPlace):
    """A page of notes' place: it's one text, nothing of it to hide; what's at the top of the view is a line of it (a
    paragraph, as GTK has them)."""

    def __init__(self, editor):
        super().__init__(editor.scroll, editor)
        self.view, self.buf = editor.view, editor.buf

    def extent(self, line):
        """Where the line is, in the adjustment's terms (the text scrolls itself: its buffer's y, from the top of the
        view's, on from where the adjustment is), and how tall."""
        ok, it = self.buf.get_iter_at_line(line)
        if not ok:
            return None
        y, height = self.view.get_line_yrange(it)
        return y - self._top() + self.scroll.get_vadjustment().get_value(), height

    def _top(self):
        return self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, 0, 0)[1]

    def split(self):
        return self.view.get_line_at_y(self._top())[0].get_line(), [], [], []


class NotesView(Gtk.TextView):
    """The notes' text, with what's drawn over what's kept unseen in it (the DiaryEditor's _highlight says where):
    an @name's logo where its @ is (logos: [(offset, logo)], what ⌘K knows it as, in the @names' colour), and a list's
    bullet (over its -: solid, hollow a level in, solid the next; as Baude's chat draws a list's, bold, the text's
    colour a little faded, right of a column two characters wide: marker_col), a box ticked or not, a rule (marks:
    [(offset, "bullet" | "hollow" | "box" | "done" | "rule")]), as Self's editor draws them; and under the text a code
    block's card, as Baude's chat draws one, its fences' lines its top and bottom (blocks: [(first line, last line)]:
    its text set in by the "pre" tag)."""

    COLOR, BOX = Gdk.RGBA(), Gdk.RGBA()
    COLOR.parse("#2f8f72")
    BOX.parse("#4f8a7c")
    GLYPHS = {"bullet": "•", "hollow": "◦", "box": "☐", "done": "☑"}  # (a level in: hollow, then solid)
    MARKER_ALPHA = 0.7  # (a list's bullets and numbers: Baude's .md-marker)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.logos, self.marks, self.blocks = [], [], []
        self.restyled = None  # (told when its style's changed: the text's size or colour, DiaryEditor._heading_looks)
        self.resized, self.width_was = None, None  # (told when its width's changed)

    def do_size_allocate(self, width, height, baseline):
        Gtk.TextView.do_size_allocate(self, width, height, baseline)
        if self.resized is not None and width != self.width_was:
            self.width_was = width
            GLib.idle_add(self.resized)

    def do_css_changed(self, change):
        Gtk.TextView.do_css_changed(self, change)
        if self.restyled is not None and not getattr(self, "restyling", False):  # (once GTK's done restyling: a tag
            self.restyling = True                                                 # changed in it, its lines laid out
            GLib.idle_add(self._restyle)                                          # again in the midst of that)

    def _restyle(self):
        self.restyling = False
        if self.restyled is not None:
            self.restyled()
        return False

    def char_rect(self, it):
        """it's character's box, in buffer coordinates (as the layer is drawn), from the caret's places before and
        after it. (GTK 4.24's get_iter_location places a character as if nothing before it on its line were hidden:
        an @name after a **bold**, a nested bullet after its unseen spaces, drawn too far right. The caret's place is
        right.)"""
        here = self.get_cursor_locations(it)[0]
        after = it.copy()
        width = 0
        if after.forward_char() and (nxt := self.get_cursor_locations(after)[0]).y == here.y:
            width = max(0, nxt.x - here.x)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = here.x, here.y, width, here.height
        return rect

    def in_view(self):
        """The lines in view: (first offset, last offset, first line, last line). Only what's there is drawn: asking
        where a character is lays its line out, and every bullet of a long page each frame was most of a reflow's
        time."""
        rect = self.get_visible_rect()
        top = self.get_line_at_y(rect.y)[0]
        bottom = self.get_line_at_y(rect.y + rect.height)[0]
        if not bottom.ends_line():
            bottom.forward_to_line_end()
        return top.get_offset(), bottom.get_offset(), top.get_line(), bottom.get_line()

    def do_snapshot_layer(self, layer, snapshot):
        if layer == Gtk.TextViewLayer.BELOW_TEXT:
            if self.blocks:
                self._snapshot_blocks(snapshot, self.in_view())
            return
        if layer != Gtk.TextViewLayer.ABOVE_TEXT or not (self.logos or self.marks):
            return
        buf = self.get_buffer()
        first, last, _l1, _l2 = self.in_view()
        col, glyphs = self.marker_col(), {}  # (the column, and each glyph's layout: once a frame, not once a bullet)
        for offset, logo in self.logos:
            if not first <= offset <= last:
                continue
            r = self.char_rect(buf.get_iter_at_offset(offset))
            size = min(r.height * 0.8, max(r.width, r.height * 0.6))
            widgets.draw_ref_icon(snapshot, logo, r.x + (r.width - size) / 2, r.y + (r.height - size) / 2, size,
                                  self.COLOR)
        for offset, kind in self.marks:
            if not first <= offset <= last:
                continue
            r = self.char_rect(buf.get_iter_at_offset(offset))
            if kind == "rule":
                y = r.y + r.height // 2
                rule = Graphene.Rect().init(r.x, y, max(0, self.get_width() - self.get_right_margin() - r.x), 1)
                color = self.get_color()
                color.alpha *= 0.3
                snapshot.append_color(color, rule)
                continue
            bullet = kind in ("bullet", "hollow")
            if kind not in glyphs:
                layout = self.create_pango_layout(self.GLYPHS[kind])
                if bullet:
                    attrs = Pango.AttrList()
                    attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
                    layout.set_attributes(attrs)
                glyphs[kind] = layout, layout.get_pixel_extents()[1], self.marker_color() if bullet else self.BOX
            layout, logical, color = glyphs[kind]
            # (a bullet at the right of its column, as Baude's chat has it; a box where its [ ] is)
            x = r.x + col - logical.width - logical.x if bullet else r.x
            snapshot.save()
            snapshot.translate(Graphene.Point().init(x, r.y + (r.height - logical.height) / 2))
            snapshot.append_layout(layout, color)
            snapshot.restore()

    def _snapshot_blocks(self, snapshot, view):
        """Each code block's card in view (in_view): Baude's raised fill and one rule round it, the width of the text."""
        buf, fill, rule = self.get_buffer(), self.get_color(), self.get_color()
        fill.alpha *= 0.06
        rule.alpha *= 0.14
        left, width = self.get_left_margin(), self.get_width() - self.get_left_margin() - self.get_right_margin()
        for first, last in self.blocks:
            if last < view[2] or first > view[3]:
                continue
            top, _h = self.get_line_yrange(buf.get_iter_at_line(first)[1])
            y, height = self.get_line_yrange(buf.get_iter_at_line(last)[1])
            card = Gsk.RoundedRect()
            card.init_from_rect(Graphene.Rect().init(left, top, max(0, width), y + height - top), 8)
            snapshot.push_rounded_clip(card)
            snapshot.append_color(fill, card.bounds)
            snapshot.pop()
            snapshot.append_border(card, [1, 1, 1, 1], [rule] * 4)

    def marker_col(self):
        """A list marker's column (Baude's chat's: two characters wide, its bullet or number at the right of it), in
        the view's font as it is (zoomed)."""
        return self.create_pango_layout("00").get_pixel_size()[0]

    def marker_color(self):
        color = self.get_color()
        color.alpha *= self.MARKER_ALPHA
        return color


# Text kept, its place too, but not seen (what's drawn over it): all but clear. GTK draws a clear colour, alpha 0, as
# if no colour were set, the alpha dropped: the - a bullet's drawn over came out black, an @ under its logo too.
UNSEEN = "rgba(0,0,0,0.002)"


class PageHistory:
    """A page's Undo and Redo (⌘Z and ⇧⌘Z; Ctrl+Z, and Ctrl+Shift+Z or Ctrl+Y), kept here, GTK's buffer's own off: what
    a change the Record proposes puts in is in the buffer, green, but isn't the page's (DiaryEditor.kept), so it's in
    none of what's undone, and one shown or taken away since moves nothing. A step is what one of the text view's
    actions did (a key, a paste, an edit of many steps), as places in the page's own text; letters typed one after
    another are one step to a word, and a Backspace or Delete held down is one, as GTK's own Undo has them."""
    LIMIT = 1000

    def __init__(self, editor):
        self.editor, self.undos, self.redos = editor, [], []
        self.paused, self.open = 0, None  # (edits not the page's: not kept; the action being kept: its edits so far)
        buf = editor.buf
        buf.set_enable_undo(False)
        buf.connect("insert-text", self._inserting)
        buf.connect("delete-range", self._deleting)
        buf.connect("begin-user-action", lambda *_: self.paused or setattr(self, "open", []))
        buf.connect("end-user-action", lambda *_: self._ended())

    def clear(self):
        self.undos, self.redos, self.open = [], [], None

    def _inserting(self, _buf, at, text, _n):
        if not self.paused and text:
            self._add("+", self.editor.kept_offset(at.get_offset()), text)

    def _deleting(self, _buf, a, b):
        if not self.paused:
            x, y = sorted((a.get_offset(), b.get_offset()))
            if text := self.editor.kept_between(x, y):
                self._add("-", self.editor.kept_offset(x), text)

    def _add(self, kind, at, text):
        if self.open is not None:
            self.open.append((kind, at, text))
        else:
            self._push([(kind, at, text)])

    def _ended(self):
        ops, self.open = self.open, None
        if ops:
            self._push(ops)

    def _push(self, ops):
        self.redos.clear()
        if self.undos and self._merged(self.undos[-1], ops):
            return
        self.undos.append({"ops": ops, "typed": len(ops) == 1 and len(ops[0][2]) == 1})
        del self.undos[:-self.LIMIT]

    @staticmethod
    def _merged(step, ops):
        """A letter typed, or deleted, onto the step before's, that one letters too, just where it left off: the same
        step. A word after a space starts one (the space goes with the word before it); a new line is one of its own."""
        if not step["typed"] or len(ops) != 1 or len(ops[0][2]) != 1 or "\n" in ops[0][2]:
            return False
        (kind, at, text), (was_kind, was_at, was) = ops[0], step["ops"][0]
        if kind != was_kind or "\n" in was:
            return False
        if kind == "+":
            if at != was_at + len(was) or not text.isspace() and was[-1].isspace():
                return False
            step["ops"][0] = ("+", was_at, was + text)
        elif at + 1 == was_at:  # (Backspace: the letter before)
            step["ops"][0] = ("-", at, text + was)
        elif at == was_at:  # (Delete: the one after)
            step["ops"][0] = ("-", at, was + text)
        else:
            return False
        return True

    def undo(self):
        return self._step(self.undos, self.redos, back=True)

    def redo(self):
        return self._step(self.redos, self.undos, back=False)

    def _step(self, source, target, back):
        if not source:
            return False
        step = source.pop()
        ops = [("-" if kind == "+" else "+", at, text) for kind, at, text in reversed(step["ops"])] if back else \
            step["ops"]
        self.editor.apply_steps(ops)
        step["typed"] = False  # (what's typed next, there, is a step of its own)
        target.append(step)
        return True


class EditControls(Gtk.Box):
    """A proposed edit's Accept and Reject, on its page where its green ends (DiaryEditor._put_greens: at its anchor):
    a tick on green and a cross on red, as tall as the text's letters (size), in its line. Neither takes the focus: the
    caret stays where it was in the page."""

    def __init__(self, editor, p, px):
        super().__init__(spacing=3, margin_start=4, margin_end=2)
        self.buttons = []
        for icon, tip, css, accept in (("object-select-symbolic", "Accept (Tab)", "edit-accept", True),
                                       ("window-close-symbolic", "Reject (Esc)", "edit-reject", False)):
            btn = Gtk.Button(tooltip_text=tip, focus_on_click=False, can_focus=False)
            btn.set_child(Gtk.Image(icon_name=icon))
            btn.add_css_class("edit-control")
            btn.add_css_class(css)
            pointers.hand(btn)
            btn.connect("clicked", lambda *_, accept=accept: p in (e["p"] for e in editor.proposals) and
                        editor.decide(p, accept))
            self.append(btn)
            self.buttons.append(btn)
        self.px = None
        self.size(px)

    def size(self, px):
        """Each button px tall (and a little wider), its mark most of that."""
        if px != self.px:
            self.px = px
            for btn in self.buttons:
                btn.set_size_request(round(px * 1.45), px)
                btn.get_child().set_pixel_size(max(8, round(px * 0.85)))


class DiaryEditor(Gtk.Box, FindInText):
    """A Record's glossary, its notes: pages of Markdown drawn as they read, as Self's editor draws them (headings,
    **bold**, *italic*, ==marked==, `code`, - lists, > quotes, [links](url)), the markup shown on the caret's line
    only; [[Page]] opens a page (made if there's
    none yet); @ lists what Baude's ⌘K finds in the notebook's folders, and every chat by its title, as you type, and
    @name opens it; ⌘F finds in it, as in a code tab (FindInText)."""
    WIDTH = 820  # its column, unless the text is full width
    # (⌘F finds what's seen, as it reads: bold words in some **bold** words, nothing in the markup hidden, so no match
    # counts with nothing to show for it: match_span)
    FIND_FLAGS = FindInText.FIND_FLAGS | Gtk.TextSearchFlags.VISIBLE_ONLY
    PAGES_WIDTH = 200  # the pages' column, at first (dragged: as it's left); the notebooks' column too
    FOLLOW_MARGIN = 32  # (the caret kept this far from the edge it's typed towards: follow_caret)
    FOLLOW_MS = 140
    FOLLOW_TRIES = 8
    # (headings under the page's title, .diary-title, 1.75em and 800 to the text's 1.05: the first a little smaller,
    # each deeper one smaller again, as Org mode's custom styles have them, and lighter, the third on grey too
    # (HEADING_INK); the fourth the text's size, and the two below it smaller still)
    TAGS = {"h1": {"weight": 800, "scale": 1.4}, "h2": {"weight": 700, "scale": 1.22},
            "h3": {"weight": 600, "scale": 1.1}, "h4": {"weight": 500}, "h5": {"weight": 500, "scale": 0.9},
            "h6": {"weight": 500, "scale": 0.85},
            "mark": {"foreground": "#8a8a8a"}, "bold": {"weight": 700}, "italic": {"style": Pango.Style.ITALIC},
            "highlight": {"background": "rgba(246,211,45,0.35)"}, "strike": {"strikethrough": True},
            "verbatim": {"family": "monospace", "background": "rgba(128,128,128,0.16)"},
            "pre": {"family": "monospace", "left_margin": 34, "right_margin": 34, "pixels_above_lines": 0},  # (a ``` block:
            #        its card's NotesView's, the text set MARGIN + 12 in)
            "quote": {"foreground": "#8a8a8a", "style": Pango.Style.ITALIC, "left_margin": 40},
            "link": {"foreground": "#3584e4", "underline": Pango.Underline.SINGLE},
            # (to a page with nothing in it yet: grey and italic, as the page list shows it)
            "newlink": {"foreground": "#8a8a8a", "underline": Pango.Underline.SINGLE, "style": Pango.Style.ITALIC},
            "hidden": {"invisible": True},  # (markup away from the caret's line; a link's [[ ]] always)
            "drawn": {"foreground": UNSEEN},  # (a bullet's -, a box's [ ], a rule's ---: drawn over, NotesView)
            "logo-at": {"foreground": UNSEEN},  # (an @name's @, unseen: its logo there; over "ref", below)
            "logo-room": {"letter_spacing": 4 * Pango.SCALE},  # (a pull request's #: the room an @ has, for its logo)
            "ref": {"foreground": "#2f8f72", "weight": 600, "background": "rgba(79,138,124,0.14)"},
            # (@'s search as it's typed, spaces and all: as an @name looks, _mark_token)
            "token": {"foreground": "#2f8f72", "weight": 600, "background": "rgba(79,138,124,0.14)"},
            "bullet": {"weight": 700},  # (a numbered item's number, coloured as bullets are: _highlight)
            # (a change the Record proposes, as tracked changes read: what it takes away struck through on red, what it
            # puts in just after it on green, in the page's own lines, Accept and Reject at its end; none of that green the
            # page's till it's accepted, nor to be written in: show_proposal)
            "proposal-old": {"background": "rgba(224,27,36,0.16)", "strikethrough": True},
            "proposal-new": {"background": "rgba(46,194,126,0.22)"},
            "proposal-lock": {"editable": False}}
    MARGIN = 22  # the page's text, and its title, this far in
    # a list as Baude's chat lays one out (widgets.MarkdownView): LIST_PAD in, INDENT more a level (its spaces kept,
    # unseen), its text MARKER_GAP after its marker's column (NotesView.marker_col)
    INDENT, LIST_PAD, MARKER_GAP = widgets.INDENT_STEP, 6, 6
    # Markdown, as Self's editor has it: (pattern, tag, the markup's length each side) for the text between
    INLINE = ((re.compile(r"`([^`\n]+)`"), "verbatim", 1),
              (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), "bold", 2),
              (re.compile(r"(?<![\w_])__(?=\S)(.+?)(?<=\S)__(?![\w_])"), "bold", 2),
              (re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])"), "italic", 1),
              (re.compile(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])"), "italic", 1),
              (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), "strike", 2),
              (re.compile(r"==(?=\S)(.+?)(?<=\S)=="), "highlight", 2))

    def __init__(self, win, notebook, page):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.notebook, self.page, self.loading = win, notebook, page, False  # (a page of a notebook's: a tab's)
        self.token = None  # the @word being typed: (its offset, what's typed after @)
        self.token_far = None  # (the farthest the caret's been in it: its colour reaches that far)
        # (a page in a tab of its own, Window.open_note: its title and its text; the pages' list is the window's,
        # PagesList, under the notebooks)
        main = self
        head = Gtk.Box(spacing=4, margin_start=self.MARGIN, margin_end=self.MARGIN, margin_top=8, margin_bottom=2)
        self.title = Gtk.Entry(has_frame=False, hexpand=True)
        self.title.add_css_class("diary-title")
        self.title.connect("activate", lambda *_: self._title_entered())
        leave = Gtk.EventControllerFocus()
        # (once what took the focus is done: it can be a page's row, being clicked, and renaming lists the pages again)
        leave.connect("leave", lambda *_: GLib.idle_add(self.rename_page))
        self.title.add_controller(leave)
        head.append(self.title)
        main.append(head)
        # The lines' spacing above them, not below (the first's in top_margin's 10): GTK 4.24 asked for the point just
        # under a line with hidden markup on it (a hover, a click) takes the line's whole length as what's seen of it,
        # runs off its end and aborts ("Byte index … is off the end of the line")
        self.view = NotesView(wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=self.MARGIN, right_margin=self.MARGIN,
                              top_margin=7, bottom_margin=40, pixels_above_lines=3, accepts_tab=False)
        self.view.add_css_class("diary")
        widgets.no_text_drag(self.view)  # (a selection isn't dragged away: a press in it places the caret)
        widgets.word_steps(self.view)  # (Option and an arrow: as far as a closing } or a line's full stop too)
        self.buf = self.view.get_buffer()
        for name, props in self.TAGS.items():  # (code, `x` and ``` blocks, at code's size beside the notes': widgets')
            self.buf.create_tag(name, **({**props, "scale": widgets.code_scale()} if name in ("verbatim", "pre") else props))
        for n in self.HEADING_SPACE:  # (a heading's space below, as the space above the line after it: _heading_looks)
            self.buf.create_tag(f"after-h{n}")
        self.view.restyled = self._heading_looks
        self._heading_looks()
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.notebook is not None and self._highlight())
        tags = self.buf.get_tag_table()  # (the @'s unseen, though "ref" colours the rest of its @name)
        tags.lookup("logo-at").set_priority(tags.get_size() - 1)
        self.buf.connect("changed", self._changed)
        self.typed_at = None  # (where an @ or a # was just typed: their searches start only there, _token_changed)
        self.buf.connect("insert-text", lambda _b, at, text, _n: ("@" in text or "#" in text) and len(text) <= 2 and
                         setattr(self, "typed_at", at.get_offset() + max(text.rfind("@"), text.rfind("#"))))
        self.caret_was = None  # (where the caret was: which way it went, _caret_in_marker)
        self.snapping = None  # (the caret's snap out of a list item's marker, waiting: _caret_moved)
        self.buf.connect("notify::cursor-position", lambda *_: self.loading or (self._caret_moved(),
                                                                                self._caret_line(),
                                                                                self._token_changed()))
        self.buf.connect("mark-set", lambda _b, _i, mark: mark.get_name() == "selection_bound" and
                         not self.loading and self._caret_line())
        self.quoting = None  # (a selection's quote to the Record, once it's settled: _quote_soon)
        self.buf.connect("mark-set", lambda _b, _i, mark: mark.get_name() in ("insert", "selection_bound") and
                         not self.loading and self._quote_soon())
        self.proposals, self.placing = [], None  # (the Record's proposed changes shown on it: show_proposal)
        self.history = PageHistory(self)  # (Undo and Redo, what's proposed in none of it)
        self.view.connect("paste-clipboard", self._paste)
        self.view.connect("cut-clipboard", lambda *_: self._whole_links())
        self.view.connect("copy-clipboard", lambda *_: self._whole_links())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", lambda *_: self.follow_soon() or False)  # (the caret moved by a key: still seen)
        keys.connect("key-pressed", self._key)
        self.view.add_controller(keys)
        click = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)  # (before the text view's own)
        click.connect("pressed", self._pressed)
        click.connect("released", self._click)
        click.connect("stopped", lambda *_: setattr(self, "pressed", None))  # (dragged away: not a click)
        self.view.add_controller(click)
        self.pressed = None  # (the link a click came down on: _pressed)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._hover)
        motion.connect("leave", lambda *_: (setattr(self, "hovered", None), self.win.hover_left()))
        self.view.add_controller(motion)
        self.hovered = None  # (the @name the pointer's over: _hover)
        self.following, self.follow_frames, self.follow_tries, self.follow_anim = None, 0, 0, None  # (follow_soon)
        # (the text scrolls itself, a scrollable clamp round it: in a viewport, as an Adw.Clamp is put in one, it was as
        # tall as all of it, so a change of its width laid out every line of a long page at once, a frame or more
        # each. Scrolling itself, GTK lays out what's in view, the rest as it's idle. Window.apply_full_width)
        self.clamp = Adw.ClampScrollable(maximum_size=self.WIDTH, child=self.view)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.clamp)
        self.title_hits, self.on_title = [], None  # (the find bar's matches in the title, and the one it's on: find)
        main.prepend(self.make_find_bar())  # (over all of the page, the title's in what it finds)
        self.search_bar.connect("notify::search-mode-enabled",  # (its close button too: back to writing)
                                lambda *_: self.search_bar.get_search_mode() or self.view.grab_focus())
        self.title.connect("changed", lambda *_: self._find_again())
        main.append(self.scroll)
        # @'s list: every name found, best first, each with its logo as ⌘K lists it (widgets.PickList: Baude's / is
        # one too). Its rows are pop_shown's.
        self.picks = widgets.PickList(self.view, self._pop_bind, self._insert_ref)
        self.pop, self.pop_list, self.pop_sel, self.pop_model, self.pop_note = \
            self.picks.pop, self.picks.list, self.picks.sel, self.picks.model, self.picks.note
        self.pop_shown = []  # (nothing picked till it's asked for, in #'s: _fill_pop)
        self.pop_moved, self.pop_query = False, None  # (moved in with the arrows, while this was what's searched for)
        self.waiting = None
        self.place = DiaryPlace(self)  # (through the zoom: Window.zoom_tab)
        self.load()

    # ---- its page
    def load(self):
        """Its page's text, drawn, the caret at the start (made; renamed), nothing to undo."""
        self._drop_all()
        self.loading = True
        with self._quiet():
            self.buf.set_text(self.notebook["pages"].get(self.page, ""))
        self.history.clear()
        self.buf.place_cursor(self.buf.get_start_iter())
        self.loading = False
        self._highlight()
        self._hide_pop()
        self.title.set_text(short_page(self.page))
        self.title.set_tooltip_text(self.page if ":" in self.page else None)
        self.title.set_editable(self.page != HOME)
        self.show_pending()

    def open_page(self, name, push=True):
        """A page (a [[link]] followed): in a tab of its own (Window.open_note), shown. Returns its editor."""
        return self.win.open_note(name)

    def _up_to_title(self):
        """Up on the text's first line, as Down in the title comes down to it: up into the title, the caret about
        straight above where it was (title_offset_at), none of it selected, so what's typed next doesn't replace it.
        False anywhere lower: Up's as it always was."""
        caret = self.buf.get_iter_at_mark(self.buf.get_insert())
        at = self.view.get_iter_location(caret)
        if at.y != self.view.get_iter_location(self.buf.get_start_iter()).y:
            return False  # (on a lower line, or the first one's lower rows, wrapped)
        x, y = self.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, at.x, at.y)
        offset = self.title_offset_at(self.view, x)
        self.title.grab_focus_without_selecting()
        self.title.set_position(offset)
        return True

    def title_offset_at(self, widget, x):
        """The place in the title's text nearest to x across widget (in its own coordinates): the end, past its last
        letter; the start, left of its first. Where the text's laid out as the title draws it, so not if it's scrolled
        along (a name longer than its box): about right is all it's for."""
        text = self.title.get_delegate()
        ok, point = widget.compute_point(text, Graphene.Point().init(x, 0))
        words = self.title.get_text()
        if not ok or not words:
            return -1
        _inside, index, trailing = text.create_pango_layout(words).xy_to_index(int(point.x * Pango.SCALE), 0)
        return len(words.encode()[:index].decode(errors="ignore")) + trailing

    def _title_entered(self):
        """Enter in the title: the page renamed, and on to its text, from the top. (A name that's taken: it stays in the
        title, to try another.)"""
        if self.rename_page() is False:
            return
        self.buf.place_cursor(self.buf.get_start_iter())
        self.scroll.get_vadjustment().set_value(0)
        self.view.grab_focus()

    def rename_page(self):
        """The title edited (the page's own name: it stays under its parent): the page renamed with its sub-pages,
        and every [[link]] to any of them; a +link stays one where it still can (its page's sub-page). Their tabs
        follow (Window.note_renamed). False if there's a page of that name already."""
        typed, old = self.title.get_text().strip(), self.page
        parent = old.rpartition(":")[0]
        new = f"{parent}:{typed}" if parent and typed else typed
        pages = self.notebook["pages"]
        if old == HOME or not typed or new == old:
            self.title.set_text(short_page(old))
            return
        listed = set(pages) | set(self.win.pages.names()[0])  # (a sub-page only a link has yet moves too)
        moved = {n: new + n[len(old):] for n in listed if n == old or n.startswith(old + ":")}
        if any(to in pages and to not in moved for to in moved.values()):
            self.title.set_text(short_page(old))
            self.win.toast(f"There's a page called {typed} already")
            return False

        def relinked(text, here, there):  # (here: the page the text is in, before; there: after)
            def relink(m):
                to = page_target(here, m.group(1))
                if to not in moved:
                    return m.group(0)
                written = page_link(there, moved[to]) if m.group(1).strip().startswith("+") else moved[to]
                return f"[[{written}|{m.group(2)}]]" if m.group(2) is not None else f"[[{written}]]"
            return LINK_RE.sub(relink, text)
        renamed = {moved.get(n, n): relinked(text, n, moved.get(n, n)) for n, text in pages.items()}
        pages.clear()  # (the same dict: the Record keeps it)
        pages.update(renamed)
        self.win.store.save()
        self.win.note_renamed(moved)

    def reload(self):
        """The page changed elsewhere (a word added to To learn, a link renamed): shown again, the caret kept, nothing
        to undo (as GTK's own Undo had it)."""
        if self.notebook and self.kept() != self.notebook["pages"].get(self.page, ""):
            at = self.kept_offset(self.buf.get_property("cursor-position"))
            self._drop_all()
            self.loading = True
            with self._quiet():
                self.buf.set_text(self.notebook["pages"].get(self.page, ""))
            self.history.clear()
            self.buf.place_cursor(self.buf.get_iter_at_offset(min(at, self.buf.get_char_count())))
            self.loading = False
            self._highlight()
            self.show_pending()  # (its text new: where what's proposed is found again)

    def stop(self):
        """Its tab closed (or the window going): its timer stopped."""
        self.place.stop()
        if self.waiting is not None:
            GLib.source_remove(self.waiting)
            self.waiting = None

    def tab_link(self):
        """Its file, for an agent to read (MainTabs.copy_link)."""
        return self.win.store.page_file(self.notebook, self.page) if self.notebook else None

    # ---- the text
    def text(self):
        """The buffer's text: all of it, the links' hidden marks too (the buffer's "text" leaves out what's hidden, and
        with it every link's [[ ]]), and what a proposal puts in (green: not the page's, kept), its Accept and Reject's
        anchor a U+FFFC (a slice, not GTK's text, which leaves anchors out). Its offsets are the buffer's."""
        return self.buf.get_slice(*self.buf.get_bounds(), True)

    def kept(self):
        """The page's text, as it's kept (saved, its file written, read by the Record): the buffer's without what's
        proposed in it."""
        text, out, at = self.text(), [], 0
        for x, y in self._greens():
            out.append(text[at:x])
            at = y
        out.append(text[at:])
        return "".join(out)

    def _greens(self):
        """Where what's proposed is in the buffer, in order: [(start, end)] (none while it's taken out: _greens_out)."""
        if not self.proposals:
            return []
        return sorted((self.buf.get_iter_at_mark(e["g0"]).get_offset(), self.buf.get_iter_at_mark(e["g1"]).get_offset())
                      for e in self.proposals if e.get("g0") is not None)

    def kept_offset(self, offset):
        """A place in the buffer as one in the page's own text (kept): what's proposed before it left out."""
        at = offset
        for x, y in self._greens():
            if x >= offset:
                break
            at -= min(y, offset) - x
        return at

    def buf_offset(self, at, past=True):
        """A place in the page's own text (kept) as one in the buffer; past: after what's proposed there, if anything
        is (where a passage starts), else before it (where one ends)."""
        offset = at
        for x, y in self._greens():
            if x < offset or past and x == offset:
                offset += y - x
            else:
                break
        return offset

    def kept_between(self, a, b):
        """The page's own text between two places in the buffer (what's proposed there left out)."""
        return "".join(self.buf.get_slice(self.buf.get_iter_at_offset(x), self.buf.get_iter_at_offset(y), True)
                       for x, y in self._kept_pieces(a, b))

    def _kept_pieces(self, a, b):
        """The stretches between two places in the buffer that are the page's own: [(start, end)]."""
        pieces, at = [], a
        for x, y in self._greens():
            if y <= at or x >= b:
                continue
            if x > at:
                pieces.append((at, x))
            at = max(at, y)
        if at < b:
            pieces.append((at, b))
        return pieces

    def _changed(self, _buf):
        if self.loading or self.notebook is None:
            return
        self.notebook["pages"][self.page] = self.kept()
        self.notebook.pop("fresh", None)  # (written in: kept, Store.is_blank)
        self.win.pages.prune()
        self.win.store.save()
        self._highlight()
        self.win.pages.changed()  # (a link made or gone, a page written in or emptied: listed so)
        if self.win.get_focus() is self.view:  # (written, not set by something else: a restart's text, say)
            self.follow_soon()

    def follow_soon(self, again=False):
        """The caret kept in view as it's typed or moved with the keys, once what changed has been laid out (a new line
        makes the page taller only then: the second frame from now, or if the window's busy a little after; again: it
        wasn't yet, a try of FOLLOW_TRIES). The text view can't do this itself: it sits in a clamp in the scroller, so it
        has nothing of its own to scroll, and it scrolls only as focus moves to it."""
        self.follow_tries = self.follow_tries + 1 if again else 0
        self.follow_frames = 2
        if self.following is None and self.view.get_mapped():
            self.following = self.view.add_tick_callback(self._follow_tick)

    def _follow_tick(self, _view, _clock):
        self.follow_frames -= 1
        if self.follow_frames > 0:
            return GLib.SOURCE_CONTINUE
        self.following = None
        self.follow_caret()
        return GLib.SOURCE_REMOVE

    def follow_caret(self, it=None):
        """If the caret (or it: a match, find_scroll) is past an edge of what's shown (FOLLOW_MARGIN short of it),
        scrolled smoothly back into view."""
        rect = self.view.char_rect(it or self.buf.get_iter_at_mark(self.buf.get_insert()))
        y = self.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, rect.x, rect.y)[1]
        ok, at = self.view.compute_point(self.scroll, Graphene.Point().init(0, y))
        if not ok:
            return
        adj = self.scroll.get_vadjustment()
        past_bottom = at.y + rect.height + self.FOLLOW_MARGIN - self.scroll.get_height()
        past_top = self.FOLLOW_MARGIN - at.y
        if past_bottom <= 0 and past_top <= 0:
            return
        by = past_bottom if past_bottom > 0 else -past_top
        most = adj.get_upper() - adj.get_page_size()
        to = min(max(adj.get_value() + by, 0), most)
        if it is None and adj.get_value() + by > most and self.follow_tries < self.FOLLOW_TRIES:
            self.follow_soon(again=True)  # (the page isn't its new height yet: its bottom margin has room for the caret's)
        if self.follow_anim is not None:
            self.follow_anim.pause()
        self.follow_anim = Adw.TimedAnimation.new(self.scroll, adj.get_value(), to, self.FOLLOW_MS,
                                                  Adw.PropertyAnimationTarget.new(adj, "value"))
        self.follow_anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self.follow_anim.play()

    # the space above and below a heading as Google Docs' own styles have it, in points against its 11pt text: more
    # above than below, so each goes with what follows it (_heading_looks). Its space below is the line after it's
    # space above ("after-h1"...): a heading's own, under hidden markup ("# "), had GTK stop over it, the pointer's
    # place in it a byte off the end of the line
    HEADING_SPACE = {1: (20, 6), 2: (18, 6), 3: (16, 4), 4: (14, 4), 5: (12, 4), 6: (12, 4)}
    HEADING_INK = {3: 0.82, 4: 0.7, 5: 0.7, 6: 0.6}  # (the deeper ones grey: this much of the text's colour)

    def _heading_looks(self):
        """The headings' space above and below (HEADING_SPACE) in pixels for the text's size now, and their grey
        (HEADING_INK) of the text's colour now, made again as the zoom, Settings → Size or the theme changes them."""
        font = self.view.get_pango_context().get_font_description()
        px = font.get_size() / Pango.SCALE * (1 if font.get_size_is_absolute() else 96 / 72)
        ink = self.view.get_color()
        if (key := (round(px, 2), ink.to_string())) == getattr(self, "_headings_drawn_for", None):
            return
        self._headings_drawn_for = key  # (not _heading_key: that's Alt and an arrow on a heading)
        table = self.buf.get_tag_table()
        for n, (above, below) in self.HEADING_SPACE.items():
            tag = table.lookup(f"h{n}")
            tag.set_property("pixels-above-lines", round(above / 11 * px))
            table.lookup(f"after-h{n}").set_property(  # (on top of what every line has above it)
                "pixels-above-lines", round(below / 11 * px) + self.view.get_pixels_above_lines())
            if n in self.HEADING_INK:
                grey = ink.copy()
                grey.alpha *= self.HEADING_INK[n]
                tag.set_property("foreground-rgba", grey)

    def _highlight(self):
        """The page drawn as it reads, in Markdown as Self's editor draws it: headings, lists (a bullet, a box drawn
        for - and [ ]), quotes, rules, code; **bold**, *italic*, ~~struck~~, ==marked==, `code`, [links](url);
        [[pages]] and @names. Markup shows only on the lines the caret (or the selection) is on; a bullet is drawn
        on every line, so an item never shifts."""
        buf = self.buf
        start, end = buf.get_bounds()
        buf.remove_all_tags(start, end)
        if (tone := self.view.marker_color()).to_string() != getattr(self, "_tone", None):  # (a number's: the theme's)
            self._tone = tone.to_string()
            buf.get_tag_table().lookup("bullet").set_property("foreground-rgba", tone)
        self._heading_looks()
        text = buf.get_slice(start, end, True)
        ins = buf.get_iter_at_mark(buf.get_insert())
        sel = buf.get_iter_at_mark(buf.get_selection_bound())
        shown = range(min(ins.get_line(), sel.get_line()), max(ins.get_line(), sel.get_line()) + 1)
        self._shown_lines = (shown.start, shown.stop)
        mode = self.win.store.markup

        def tag(name, a, b):
            if b > a:
                buf.apply_tag_by_name(name, buf.get_iter_at_offset(a), buf.get_iter_at_offset(b))
        marks, taken = [], []  # (what NotesView draws: (offset, kind)); (spans already styled: none overlaps)

        def free(a, b):
            return all(b <= x or a >= y for x, y in taken)
        refs = []  # ([[@name]]: a reference made, drawn as an @name is: (where its @ is, where it ends, the name))
        for m in LINK_RE.finditer(text):  # ([[pages]]: their marks always hidden, as asked of them)
            a, b = link_name(m)
            tag("hidden", m.start(), a)
            tag("hidden", b, m.end())
            taken.append((m.start(), m.end()))
            if m.group(1).lstrip().startswith("@"):
                refs.append((a, b, m.group(1).strip()[1:]))
                continue
            kind = "link" if self.notebook["pages"].get(page_target(self.page, m.group(1)), "").strip() else "newlink"
            tag(kind, a, b)
        space = self.win.space() if self.win.current is self.notebook else None
        logos = []  # (an @name's logo where its @ is: what ⌘K knows it as; the @ kept, unseen, NotesView)
        for a, b, name in refs:
            tag("ref", a, b)
            item = space.find(name) if space is not None and not space.busy else None
            if item is not None and item.get("icon"):
                logos.append((a, item["icon"]))
                tag("logo-at", a, a + 1)
                if item.get("label") and item["kind"] == "pr":
                    tag("logo-room", a, a + 1)
        for m in REF_RE.finditer(text):
            if not free(m.start(), m.end()):
                continue
            tag("ref", m.start(), m.end())
            taken.append((m.start(), m.end()))
            item = space.find(m.group(1) or m.group(2)) if space is not None and not space.busy else None
            if item is not None and item.get("icon"):
                logos.append((m.start(), item["icon"]))
                tag("logo-at", m.start(), m.start() + 1)
                if m.group(1):  # (@{a name}: its braces kept, unseen)
                    tag("hidden", m.start() + 1, m.start() + 2)
                    tag("hidden", m.end() - 1, m.end())
        pos, item = 0, None  # (item: the list item the lines after it may go on with: its text's margin)
        spans = widgets.fence_spans(text.split("\n"), typing=shown)
        blocks = [[first, last] for first, last, _closed in spans]  # (each code block's lines, for its card: NotesView)
        in_block = {n for first, last, _closed in spans for n in range(first, last + 1)}
        fences = {first for first, _last, _closed in spans} | {last for _first, last, closed in spans if closed}
        below = None  # (the heading just before: its space below, this line's above)
        for n, line in enumerate(text.split("\n")):
            a, b = pos, pos + len(line)
            pos = b + 1
            after, below = below, None
            if after and not re.match(r"#{1,6}\s", line):  # (another heading: its own space above)
                tag(after, a, b + 1)
            here = n in shown if mode == "line" else mode == "all"  # (Settings → Markup visibility)
            hide = (lambda x, y: tag("mark", x, y)) if here else (lambda x, y: tag("hidden", x, y))
            hide_link = hide if mode != "all" else (lambda x, y: tag("hidden", x, y))  # (all but a link's)
            if n in in_block:  # (a code block's line; a fence, its card's top or bottom: its ``` markup)
                tag("pre", a, b + 1)
                if n in fences:
                    hide(a, b)
                continue
            body = 0
            if not LIST_ITEM_RE.match(line) and not line.startswith(" "):
                item = None
            if m := re.match(r"(#{1,6})(\s+)", line):
                tag(f"h{len(m.group(1))}", a, b)
                below = f"after-h{len(m.group(1))}"
                hide(a, a + m.end())
                body = m.end()
            elif m := LIST_ITEM_RE.match(line):
                level = len(m.group(1).expandtabs(4)) // 2  # (its level: a margin, its spaces unseen)
                base = self.MARGIN + self.LIST_PAD + self.INDENT * min(8, level)
                tag("hidden", a, a + len(m.group(1)))
                # its text MARKER_GAP after the marker's column (as Baude's chat has it): the space after the marker
                # spaced out to there (letter spacing, which Pango puts half each side of it: both between the marker
                # and the text). Pango drops the half after at a line's end: the space the line's last (nothing
                # written yet), twice as much, so the caret's where the text will start. A box's after it as it is.
                marker, col = line[m.start(2):m.end(3)], self.view.marker_col() + self.MARKER_GAP
                width = self._prefix_px(marker)
                if col > width:
                    last = m.start(3) + 1 == len(line)
                    tag(self._spaced((col - width) * (2 if last else 1)), a + m.start(3), a + m.start(3) + 1)
                item = base + max(col, width) + \
                    (self._prefix_px(line[m.end(3):m.end()]) if m.group(4) is not None else 0)
                # (a long one wraps under its text, as a Shift+Enter line does: hung, its first line from the marker)
                tag(self._hung(base, item), a, b + 1)
                if m.group(2) in "-*+":  # (a bullet: drawn, the - kept unseen)
                    tag("drawn", a + m.start(2), a + m.end(2))
                    marks.append((a + m.start(2), "hollow" if level % 2 else "bullet"))
                else:
                    tag("bullet", a + m.start(2), a + m.end(2))
                if m.group(4) is not None:  # (a box: drawn, ticked or not)
                    tag("drawn", a + m.start(4) - 1, a + m.end(4) + 1)
                    marks.append((a + m.start(4) - 1, "done" if m.group(4) in "xX" else "box"))
                body = m.end()
            elif item is not None and (m := re.match(r" +", line)):  # (an item's text going on: under its text)
                tag(self._hang(item), a, b + 1)
                tag("hidden", a, a + m.end())
                body = m.end()
            elif m := re.match(r">\s?", line):
                tag("quote", a, b + 1)
                hide(a, a + m.end())
                body = m.end()
            elif re.fullmatch(r"\s*(-{3,}|\*{3,}|_{3,})\s*", line):
                if not here:
                    tag("drawn", a, b)
                    marks.append((a, "rule"))
                else:
                    tag("mark", a, b)
                continue
            for m in MD_LINK_RE.finditer(line, body):  # ([text](url): the text, a link; its marks off the caret's line)
                x, y = a + m.start(), a + m.end()
                if free(x, y):
                    taken.append((x, y))
                    hide_link(x, x + 1)
                    tag("link", x + 1, x + 1 + len(m.group(1)))
                    hide_link(x + 1 + len(m.group(1)), y)
            for m in URL_RE.finditer(line, body):
                x, y = a + m.start(), a + m.end()
                if free(x, y):
                    taken.append((x, y))
                    tag("link", x, y)
            for rx, name, side in self.INLINE:
                for m in rx.finditer(line, body):
                    x, y = a + m.start(), a + m.end()
                    if free(x, y):
                        taken.append((x, y))
                        tag(name, x + side, y - side)
                        hide(x, x + side)
                        hide(y - side, y)
        self.view.logos, self.view.marks, self.view.blocks = logos, marks, blocks
        self.view.queue_draw()
        self._mark_token()  # (@'s search, if it's on: its colour over what was just drawn)
        self._mark_proposals()  # (and a proposed change's)
        self._find_again()


    # ---- a passage selected: quoted to the Record (RecordChat.set_selection), as a chat's passages are (Reader)
    QUOTE_MS = 250

    def _quote_soon(self):
        if self.buf.get_has_selection():
            if self.quoting is not None:
                GLib.source_remove(self.quoting)
            self.quoting = GLib.timeout_add(self.QUOTE_MS, self._quote_selection)
        return False

    def _quote_selection(self):
        """A passage you've selected on the page (the page has the focus), once the selection's settled: quoted to the
        Record with where it is. What's asked is about it, and a change the Record proposes to it replaces it."""
        self.quoting = None
        bounds = self.buf.get_selection_bounds()
        if bounds and self.win.get_focus() is self.view and self.notebook is self.win.current:
            x = self.kept_offset(bounds[0].get_offset())
            text = self.kept_between(bounds[0].get_offset(), bounds[1].get_offset())  # (what's proposed in it left out)
            if len(text.strip()) > 2:
                self.win.chat.set_selection(self.notebook, self.page, x, x + len(text), text)
        return False

    # ---- a change the Record proposes (RecordChat.propose), as tracked changes read: what it takes away struck through
    # on red, where it is; what it puts in just after it, green, in the page's own lines (it wraps with them, in their
    # font and size), and at its end its Accept and Reject (EditControls, in the line where its anchor is). The green's in
    # the buffer, but not the page's: it's left out of what's kept (kept: saved, its file, what the Record reads), it
    # can't be edited (proposal-lock), and Undo has none of it (PageHistory). Accept (its tick, Tab with the caret in it)
    # puts it in the page in the red's place, as one edit, to undo; Reject (its cross, Esc) takes it away, the red as it
    # was.

    def show_proposal(self, p, scroll=False, draw=True, caret=False):
        """A proposed change (one of a reply's: RecordChat.propose), shown on the page; scroll: brought into view;
        caret: the caret put in it, just before its Accept (Tab and Esc take it or leave it: Previous and Next)."""
        shown = next((e for e in self.proposals if e["p"]["id"] == p["id"]), None)
        if shown is None:
            sel = self.buf.get_selection_bounds()  # (the passage selected, to ask: let go, the change in its place)
            sel = [self.kept_offset(i.get_offset()) for i in sel] if sel else None
            ins = self.kept_offset(self.buf.get_property("cursor-position"))
            with self._quiet():
                for e in self.proposals:  # (all of them laid in again, after this one's placed: in their order)
                    self._take_green(e)
            text, start, old = self.kept(), p.get("start", 0), p["old"]
            if not old:
                start = len(text)  # (one that adds: at the page's end, as it is now)
            elif text[start:start + len(old)] != old:
                start, found = place_change(text, old, start)
                if start is not None:
                    old = p["old"] = found  # (as it's on the page now: its spaces either side gone, say)
            gone = start is None  # (what it changes isn't on the page any more: shown at its end)
            start = len(text) if gone else start
            end = start if gone else start + len(old)
            # (marks, the text changing around them: one on the passage's start, kept there as text's typed before it;
            # one on its end, kept there as text's typed after it; the green's, put in after that: _put_greens)
            shown = {"p": p, "a": self.buf.create_mark(None, self.buf.get_iter_at_offset(start), False),
                     "b": self.buf.create_mark(None, self.buf.get_iter_at_offset(end), True), "g0": None, "g1": None,
                     "gone": gone}
            self.proposals.append(shown)
            self.proposals.sort(key=lambda e: (not e["p"]["old"], e["p"].get("t", 0)))  # (as staged has them)
            was = self.buf.create_mark(None, self.buf.get_iter_at_offset(ins), True)  # (the caret: kept where it was)
            with self._quiet():
                self._put_greens()
            if sel and sel[0] < end and start <= sel[1]:  # (let go, the change in its place)
                self.buf.place_cursor(self.buf.get_iter_at_mark(shown["b"]))
            else:
                self.buf.place_cursor(self.buf.get_iter_at_mark(was))
            self.buf.delete_mark(was)
            if draw:
                self._highlight()
        if caret:  # (in its green, just before its Accept: Tab and Esc are its, whatever's beside it)
            self.buf.place_cursor(self.buf.get_iter_at_child_anchor(shown["anchor"]))
        if scroll:
            self._scroll_soon(shown)

    def show_pending(self):
        """The changes proposed to this page that wait for you, shown on it (made; its text new)."""
        self._drop_all()
        if self.notebook is None:
            return
        for p in pending_edits(self.notebook, getattr(getattr(self.win, "chat", None), "agent", None)):
            if p.get("page") == self.page:
                self.show_proposal(p, draw=False)
        self._highlight()

    def _drop_all(self):
        for e in list(self.proposals):
            self._drop_proposal(e)

    def edit_start(self, p):
        """Where a shown change's passage is now, in the page's own text, or None (not shown, or not there as it was)."""
        e = next((e for e in self.proposals if e["p"] is p), None)
        if e is None or e["a"].get_deleted():
            return None
        a, b = (self.buf.get_iter_at_mark(e[k]).get_offset() for k in ("a", "b"))
        return self.kept_offset(a) if self.kept_between(a, b) == p["old"] else None

    def unshow(self, p):
        """A change taken off the page (to be shown again as it's revised, or taken back), the page drawn again."""
        if (e := next((e for e in self.proposals if e["p"] is p), None)) is not None:
            self._drop_proposal(e)
            self._highlight()

    @contextlib.contextmanager
    def _quiet(self):
        """Edits that aren't the page's (what's proposed, put in or taken out; the page's text set afresh): not kept,
        not undone, nothing drawn for them till they're done."""
        was, self.loading = self.loading, True
        self.history.paused += 1
        try:
            yield
        finally:
            self.history.paused -= 1
            self.loading = was

    @contextlib.contextmanager
    def _greens_out(self):
        """What's proposed taken out of the buffer for what's done in it (an Undo, an Accept, a heading moved), so the
        page's own text is all there is, and its offsets are the page's; put back after, each just after its passage,
        where that is then. Nothing's kept or drawn in it (loading): whoever does it draws it after (_changed)."""
        with self._quiet():
            for e in self.proposals:
                self._take_green(e)
        was, self.loading = self.loading, True
        try:
            yield
        finally:
            self.loading = was
            with self._quiet():
                self._replace_passages()
                self._put_greens()

    def _replace_passages(self):
        """Each change's passage where it is now, its green out (_greens_out): found again near where it was if what
        was done moved it (a heading's section moved, say), and one that adds at the page's end again."""
        text = self.text()
        for e in self.proposals:
            p, a = e["p"], self.buf.get_iter_at_mark(e["a"]).get_offset()
            b = self.buf.get_iter_at_mark(e["b"]).get_offset()
            if not p["old"]:
                at, old = len(text), ""
            elif text[a:b] == p["old"]:
                continue
            else:
                at, old = place_change(text, p["old"], a)
                if at is None or old != p["old"]:
                    continue  # (not on the page as it was: left where it is)
            self.buf.move_mark(e["a"], self.buf.get_iter_at_offset(at))
            self.buf.move_mark(e["b"], self.buf.get_iter_at_offset(at + len(old)))

    def _put_greens(self):
        """Each change shown without its green: that put in, just after its passage (the last first, so two at one
        place are in their order). A whole line, or lines, replaced: the green on a line of its own, under it."""
        for e in reversed(self.proposals):
            if e["g0"] is not None:
                continue
            p, a, b = e["p"], self.buf.get_iter_at_mark(e["a"]), self.buf.get_iter_at_mark(e["b"])
            body = p["new"].rstrip("\n")
            trail = p["new"][len(body):]
            lead = "\n" if p["old"] and not e["gone"] and not p["old"].endswith("\n") and not body.startswith("\n") \
                and a.starts_line() and b.ends_line() else ""
            at, words = b.get_offset(), lead + body
            self.buf.insert(b, words)
            e["anchor"] = self.buf.create_child_anchor(self.buf.get_iter_at_offset(at + len(words)))
            self.buf.insert(self.buf.get_iter_at_offset(at + len(words) + 1), trail)
            e["g0"] = self.buf.create_mark(None, self.buf.get_iter_at_offset(at), False)
            e["g1"] = self.buf.create_mark(None, self.buf.get_iter_at_offset(at + len(words) + 1 + len(trail)), True)
            e["lead"], e["body"] = len(lead), len(body)
            e["controls"] = EditControls(self, p, self._control_px())
            self.view.add_child_at_anchor(e["controls"], e["anchor"])
        for e in self.proposals:  # (a passage with nothing in it, where a green went in: its start back before that)
            a, b = self.buf.get_iter_at_mark(e["a"]), self.buf.get_iter_at_mark(e["b"])
            if a.compare(b) > 0:
                self.buf.move_mark(e["a"], b)

    def _take_green(self, e):
        if e["g0"] is not None:
            self.buf.delete(self.buf.get_iter_at_mark(e["g0"]), self.buf.get_iter_at_mark(e["g1"]))
            for k in ("g0", "g1"):
                self.buf.delete_mark(e[k])
                e[k] = None
            e["anchor"] = e["controls"] = None  # (its Accept and Reject gone with it)

    def _control_px(self):
        """How tall an edit's Accept and Reject are: the text's letters' height, its font's ascent (a widget in a line
        stands on its baseline, so that keeps the line as it is)."""
        return max(10, round(self.view.get_pango_context().get_metrics(None, None).get_ascent() / Pango.SCALE))

    def _mark_proposals(self):
        """Each change's passage struck through on red (any other's green in it left be), what it puts in green, and
        none of that green to be written in (after _highlight: it takes every tag away); its Accept and Reject the
        text's height, as that is now."""
        if not self.proposals:
            return
        buf = self.buf

        def tag(name, x, y):
            if y > x:
                buf.apply_tag_by_name(name, buf.get_iter_at_offset(x), buf.get_iter_at_offset(y))
        for e in self.proposals:
            if e["g0"] is None:
                continue
            for x, y in self._kept_pieces(*(buf.get_iter_at_mark(e[k]).get_offset() for k in ("a", "b"))):
                tag("proposal-old", x, y)
            g0, g1 = (buf.get_iter_at_mark(e[k]).get_offset() for k in ("g0", "g1"))
            tag("proposal-new", g0 + e["lead"], g0 + e["lead"] + e["body"])
            tag("proposal-lock", g0, g1)
            e["controls"].size(self._control_px())

    def _proposal_at(self, offset):
        """The change shown a place in the buffer is in (Tab and Esc's): the one whose green it's inside, else the
        narrowest whose passage it's in (one change's inside another's), else one whose green it's at an edge of; or
        None."""
        shown = [e for e in self.proposals if e["g0"] is not None]
        at = lambda e, k: self.buf.get_iter_at_mark(e[k]).get_offset()  # noqa: E731
        if inside := next((e for e in shown if at(e, "g0") < offset < at(e, "g1")), None):
            return inside
        if passage := [e for e in shown if at(e, "a") <= offset <= at(e, "b")]:
            return min(passage, key=lambda e: at(e, "b") - at(e, "a"))
        return next((e for e in shown if offset in (at(e, "g0"), at(e, "g1"))), None)

    def _green_at(self, offset):
        """The change shown whose green the character at a place in the buffer is, or None."""
        return next((e for e in self.proposals if e["g0"] is not None and self.buf.get_iter_at_mark(e["g0"]).get_offset()
                     <= offset < self.buf.get_iter_at_mark(e["g1"]).get_offset()), None)

    def _edits_green(self, keyval, state):
        """Whether a key would change what's proposed (it's not the page's yet): a letter typed in it, a new line, a
        paste or a cut there, a Backspace or Delete into it. Kept from the text view, and from what this editor does
        for those keys itself (a list's next item, Tab's indent...)."""
        greens = self._greens()
        if not greens:
            return False
        mod = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK)
        back, forth = keyval == Gdk.KEY_BackSpace, keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete)
        if not (back or forth or Gdk.keyval_to_unicode(keyval) and not mod or mod and keyval in (
                Gdk.KEY_v, Gdk.KEY_V, Gdk.KEY_x, Gdk.KEY_X) or keyval in (
                Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_KP_Tab)):
            return False
        if self.buf.get_has_selection():
            s, t = (i.get_offset() for i in self.buf.get_selection_bounds())
            return any(s < y and t > x for x, y in greens)
        at = self.buf.get_property("cursor-position")
        return any(x < at < y or back and at == y or forth and at == x for x, y in greens)

    def _scroll_soon(self, e):
        """A change brought into view, once it's laid out (the next frames: its lines' heights only then)."""
        self.scroll_to = e
        if self.placing is None:
            self.place_frames = 3
            self.placing = self.view.add_tick_callback(self._scroll_tick)

    def _scroll_tick(self, _view, _clock):
        self.place_frames -= 1
        if self.place_frames > 0:
            return GLib.SOURCE_CONTINUE
        self.placing = None
        e, self.scroll_to = self.scroll_to, None
        if e in self.proposals and e["g0"] is not None:
            top = self.view.get_iter_location(self.buf.get_iter_at_mark(e["a"]))
            end = self.view.get_iter_location(self.buf.get_iter_at_mark(e["g1"]))
            self._scroll_into_view(top.y, end.y + end.height - top.y)
        return GLib.SOURCE_REMOVE

    def _scroll_into_view(self, y, height):
        """The page scrolled, if it needs to be, to show from y, in the text, height pixels down."""
        wy = self.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, 0, y)[1]
        ok, at = self.view.compute_point(self.scroll, Graphene.Point().init(0, wy))
        if not ok:
            return
        adj, margin = self.scroll.get_vadjustment(), self.FOLLOW_MARGIN
        past_bottom = at.y + height + margin - self.scroll.get_height()
        past_top = margin - at.y
        by = -past_top if past_top > 0 else min(past_bottom, -past_top) if past_bottom > 0 else 0
        if by:
            adj.set_value(min(max(adj.get_value() + by, 0), adj.get_upper() - adj.get_page_size()))

    def decide(self, p, accept):
        """Accept: what's proposed put in the page in its passage's place, as one edit (undone at once with ⌘Z), where
        its passage is now; Reject: the page left as it is. Either way it goes from the page, and the Record's told
        (RecordChat.decided)."""
        e = next((e for e in self.proposals if e["p"]["id"] == p["id"]), None)
        if e is None:
            return
        if accept:
            a, b = (self.buf.get_iter_at_mark(e[k]).get_offset() for k in ("a", "b"))
            at, old = self.kept_offset(a), self.kept_between(a, b)
            if old != p["old"]:
                at, old = place_change(self.kept(), p["old"], at)
                if at is None or old != p["old"]:
                    return self.win.toast("What it changes isn't on the page as it was: nothing's changed")
            with self._greens_out():
                self._forget(e)
                self.buf.begin_user_action()
                try:
                    self.buf.delete(self.buf.get_iter_at_offset(at), self.buf.get_iter_at_offset(at + len(old)))
                    self.buf.insert(self.buf.get_iter_at_offset(at), p["new"])
                finally:
                    self.buf.end_user_action()
            self.buf.place_cursor(self.buf.get_iter_at_offset(self.buf_offset(at + len(p["new"]), past=False)))
        else:
            self._drop_proposal(e)
        p["status"] = "accepted" if accept else "rejected"
        self._changed(self.buf) if accept else self._highlight()
        self.win.chat.decided(p)

    def _drop_proposal(self, e):
        """A change no longer shown (decided; the page drawn afresh): its green taken out, its marks gone. One that's
        still waiting keeps where its passage is, for the next time it's shown. (Drawn again by whoever drops it.)"""
        if e not in self.proposals:
            return
        if e["p"].get("status") == "pending" and not e["a"].get_deleted():
            e["p"]["start"] = self.kept_offset(self.buf.get_iter_at_mark(e["a"]).get_offset())
        with self._quiet():
            self._take_green(e)
        self._forget(e)

    def _forget(self, e):
        if e in self.proposals:
            self.proposals.remove(e)
        for k in ("a", "b", "g0", "g1"):
            if e.get(k) is not None and not e[k].get_deleted():
                self.buf.delete_mark(e[k])
            e[k] = None

    def apply_steps(self, ops):
        """Undo's or Redo's edits (PageHistory): [("+" or "-", where in the page's own text, the text)], made with
        what's proposed taken out a moment; the caret after the last."""
        caret = None
        self.history.paused += 1
        try:
            with self._greens_out():
                for kind, at, text in ops:
                    it = self.buf.get_iter_at_offset(at)
                    if kind == "+":
                        self.buf.insert(it, text)
                        caret = at + len(text)
                    else:
                        self.buf.delete(it, self.buf.get_iter_at_offset(at + len(text)))
                        caret = at
        finally:
            self.history.paused -= 1
        if caret is not None:
            self.buf.place_cursor(self.buf.get_iter_at_offset(self.buf_offset(caret, past=False)))
        self._changed(self.buf)

    # ---- ⌘F (FindInText: the bar, the matches)
    def open_search(self):
        """As a code tab's; a selection in the title, where the focus is, is what it looks for too."""
        picked = self.title.get_selection_bounds() if self.title.get_focus_child() is not None else ()
        words = self.title.get_text()[picked[0]:picked[1]] if picked else ""
        FindInText.open_search(self)
        if words:
            self.search_entry.set_text(words)
            self.search_entry.select_region(0, -1)

    def find_adjustment(self):
        return self.scroll.get_vadjustment()

    def find_colours(self):
        FindInText.find_colours(self)
        self._paint_title()

    def clear_matches(self):
        FindInText.clear_matches(self)
        self.title_hits, self.on_title = [], None
        self.title.set_attributes(Pango.AttrList())

    def find(self, text, scroll=True, near=None):
        """What's found in the text, and in the title, which comes before it: the title's the first if the page is at its
        top as the search is typed, or if that's the one it was on."""
        on_title = self.on_title
        hits = [m.span() for m in re.finditer(re.escape(text), self.title.get_text(), re.IGNORECASE)] if text else []
        at_top = self.scroll.get_vadjustment().get_value() <= 0
        first = bool(hits) and (at_top if scroll and near is None else on_title is not None)
        FindInText.find(self, text, scroll and not first, near)
        self.title_hits = hits
        if first or (hits and not self.matches):
            self._show_title_hit(min(on_title or 0, len(hits) - 1))
        else:
            self._paint_title()
            self._say_which()

    def find_next(self, step):
        total = len(self.title_hits) + len(self.matches)
        if not total:
            return FindInText.find_next(self, step)
        at = self.on_title if self.on_title is not None else len(self.title_hits) + self.match_index
        to = (at + step) % total
        if to < len(self.title_hits):
            self._show_title_hit(to)
        else:
            self.show_match(to - len(self.title_hits))

    def show_match(self, i, scroll=True):
        self.on_title = None
        FindInText.show_match(self, i, scroll)
        self._paint_title()
        self._say_which()

    def _show_title_hit(self, i):
        if 0 <= self.match_index < len(self.matches):
            s, e = self.matches[self.match_index]
            self.buf.remove_tag(self.current_tag, self.buf.get_iter_at_offset(s), self.buf.get_iter_at_offset(e))
        self.match_index, self.on_title = -1, i
        if self.title.get_focus_child() is None:  # (shown, in a title too long for its box; not as it's written in)
            self.title.set_position(self.title_hits[i][1])
        self._paint_title()
        self._say_which()

    def _say_which(self):
        if self.title_hits:
            at = self.on_title if self.on_title is not None else len(self.title_hits) + self.match_index
            more = "+" if len(self.matches) >= self.MATCH_LIMIT else ""
            self.search_count.set_label(f"{at + 1} of {len(self.title_hits) + len(self.matches)}{more}")

    def _paint_title(self):
        """The title's matches shaded as the text's are (it's an entry: no tags), the current one more."""
        match, current = self.find_tints()
        text, attrs = self.title.get_text(), Pango.AttrList()
        for n, (a, b) in enumerate(self.title_hits):
            red, green, blue, alpha = current if n == self.on_title else match
            first, last = len(text[:a].encode()), len(text[:b].encode())
            for attr in (Pango.attr_background_new(red * 257, green * 257, blue * 257),
                         Pango.attr_background_alpha_new(int(alpha * 65535))):
                attr.start_index, attr.end_index = first, last
                attrs.insert(attr)
        self.title.set_attributes(attrs)

    def _find_again(self):
        """The page drawn again, its tags all taken off, as it's written in or the caret goes to another line (what's
        hidden changing with it): the find bar's matches found afresh, the one it was on still the current one, and
        the page left where it is. (They're tags on the buffer, so they're in neither what's kept, text(), nor what's
        undone.)"""
        if self.search_bar.get_search_mode() and (typed := self.search_entry.get_text()):
            mark = self.buf.get_mark("found")
            self.find(typed, scroll=False, near=self.buf.get_iter_at_mark(mark).get_offset() if mark else None)

    def match_span(self, start, end):
        """A match from its first letter seen (GTK starts one before any markup hidden there). None if all that's seen
        of it is what's drawn over: a bullet's -, a box's [ ], a rule's ---, an @name's @ aren't there as written, and
        shaded they're a sliver beside what's drawn."""
        tags = self.buf.get_tag_table()
        hidden, unseen = tags.lookup("hidden"), (tags.lookup("drawn"), tags.lookup("logo-at"))
        while start.has_tag(hidden) and start.compare(end) < 0:
            start.forward_char()
        it = start.copy()
        while it.compare(end) < 0:
            if not it.has_tag(hidden) and not any(it.has_tag(tag) for tag in unseen):
                return start, end
            it.forward_char()
        return None

    def find_top(self):
        """The offset at the top of what's shown (it's the scroller that scrolls: follow_soon)."""
        ok, top = self.scroll.compute_point(self.view, Graphene.Point().init(0, 0))
        y = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, 0, int(top.y))[1] if ok else 0
        return self.view.get_line_at_y(y)[0].get_offset()

    def find_scroll(self, mark):
        self.follow_caret(self.buf.get_iter_at_mark(mark))

    def close_search(self):
        """Esc in the bar: back in the text, the match it was on selected, as an editor's find leaves it, to be written
        over or gone on from (its line's markup shown, as the caret's is). On a match in the title, nothing's selected:
        what's written next is for the text, and over a title it would rename the page."""
        if self.matches and self.on_title is None:
            self.buf.select_range(*(self.buf.get_iter_at_offset(at) for at in self.matches[self.match_index]))
        FindInText.close_search(self)
        self.follow_soon()

    def _caret_line(self):
        """The caret (or the selection) on other lines: their markup shown, the last ones' hidden again."""
        if self.win.store.markup != "line":  # (shown on every line, or none: nothing changes as the caret goes)
            return
        a = self.buf.get_iter_at_mark(self.buf.get_insert()).get_line()
        b = self.buf.get_iter_at_mark(self.buf.get_selection_bound()).get_line()
        if (min(a, b), max(a, b) + 1) != getattr(self, "_shown_lines", None):
            self._highlight()

    def _prefix_px(self, prefix):
        """How wide an item's marker (or box) is as it's written, its spaces after it too."""
        layout = self.view.create_pango_layout(prefix)
        if re.match(r"\d", prefix):  # (a number's bold, the bullet tag's)
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
            layout.set_attributes(attrs)
        return round(layout.get_size()[0] / Pango.SCALE)  # (the nearest pixel: the item's text starts in between)

    def _spaced(self, px):
        """The tag that puts px more round a character (the space after a list marker, to its column's end:
        _highlight), made the first time."""
        name = f"spaced{px}"
        if self.buf.get_tag_table().lookup(name) is None:
            self.buf.create_tag(name, letter_spacing=px * Pango.SCALE)
        return name

    def _hung(self, start, px):
        """The tag that hangs a list item's paragraph: its first line from start (its marker), the lines it wraps
        onto from px (its text), made the first time. (GTK's negative indent: the first line at the margin, the
        rest that much further in.)"""
        name = f"hung{start}-{px}"
        if self.buf.get_tag_table().lookup(name) is None:
            self.buf.create_tag(name, left_margin=start, indent=start - px)
        return name

    def _hang(self, px):
        """The tag that puts a line's text px in (an item's text going on under it: _highlight), made the first
        time."""
        name = f"hang{px}"
        if self.buf.get_tag_table().lookup(name) is None:
            self.buf.create_tag(name, left_margin=px)
        return name

    def _continued(self, line_no):
        """(the item's line start, its LIST_ITEM_RE match, the line's spaces) if line line_no goes on with a list
        item: spaces at its start (Shift+Enter in an item puts them, as many as the item's marker takes), and only
        such lines between it and the item. Else None."""
        ok, start = self.buf.get_iter_at_line(line_no)
        if not ok:
            return None
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        text = self.buf.get_slice(start, end, True)
        if not (spaces := re.match(r" +", text)) or LIST_ITEM_RE.match(text):
            return None
        for up in range(line_no - 1, -1, -1):
            if (hit := self._item_at(up)) is not None:
                return hit[0], hit[1], spaces.end()
            ok, at = self.buf.get_iter_at_line(up)
            stop = at.copy()
            if not stop.ends_line():
                stop.forward_to_line_end()
            if not self.buf.get_slice(at, stop, True).startswith(" "):
                return None
        return None

    def _continue_item(self):
        """Shift+Enter in a list item, or in a line going on with one: a new line of the item's text, under it (its
        spaces as many as the item's marker takes, as Markdown has it). True if it was in one."""
        if self.buf.get_has_selection():
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        if (hit := self._item_at(ins.get_line())) is not None and ins.get_line_offset() >= hit[1].end():
            col = hit[1].end()
        elif (cont := self._continued(ins.get_line())) is not None and ins.get_line_offset() >= cont[2]:
            col = cont[2]
        else:
            return False
        self.buf.begin_user_action()
        self.buf.insert_at_cursor("\n" + " " * col)
        self.buf.end_user_action()
        return True

    def _unbreak(self):
        """Backspace at the start of a line going on with an item: back onto the line before (its spaces and the line
        break gone). True if it was."""
        if self.buf.get_has_selection():
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        if (cont := self._continued(ins.get_line())) is None or ins.get_line_offset() != cont[2]:
            return False
        start = ins.copy()
        start.set_line_offset(0)
        start.backward_char()
        self.buf.begin_user_action()
        self.buf.delete(start, ins)
        self.buf.end_user_action()
        return True

    def _item_at(self, line_no):
        """(the line's start, its LIST_ITEM_RE match) if line line_no is a list item, else None."""
        ok, start = self.buf.get_iter_at_line(line_no)
        if not ok:
            return None
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        m = LIST_ITEM_RE.match(self.buf.get_slice(start, end, True))
        return (start, m) if m else None

    def _indent_items(self, step):
        """Tab (step 1) or Shift+Tab (-1), as an editor's: each list item the caret or the selection is on goes in,
        or out, by its marker's width (- : two spaces, 1. : three). True if there was one."""
        bounds = self.buf.get_selection_bounds() or (self.buf.get_iter_at_mark(self.buf.get_insert()),) * 2
        lines = [n for n in range(bounds[0].get_line(), bounds[1].get_line() + 1) if self._item_at(n)]
        if not lines:
            return False
        self.buf.begin_user_action()
        for n in lines:
            start, m = self._item_at(n)
            unit = 2 if m.group(2) in "-*+" else len(m.group(2)) + 1
            if step > 0:
                self.buf.insert(start, " " * unit)
            elif (out := min(unit, len(m.group(1)) - len(m.group(1).lstrip(" ")))) > 0:
                end = start.copy()
                end.forward_chars(out)
                self.buf.delete(start, end)
        self.buf.end_user_action()
        return True

    def _unbullet(self):
        """Backspace just after a list item's bullet (or box): in by a level, it comes out one; at the left, the bullet
        goes and the line is text. True if it was."""
        if self.buf.get_has_selection():
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        if (hit := self._item_at(ins.get_line())) is None or ins.get_line_offset() != hit[1].end():
            return False
        start, m = hit
        if m.group(1):
            return self._indent_items(-1)
        end = start.copy()
        end.forward_chars(m.end())
        self.buf.begin_user_action()
        self.buf.delete(start, end)
        self.buf.end_user_action()
        return True

    def _caret_moved(self):
        """Where the caret's gone, and the text's length then; its snap out of a list item's marker (_caret_in_marker)
        once GTK's done moving it, not in the midst of that (its own steps, a click, a drag still going on)."""
        was, self.caret_was = self.caret_was, (self.buf.get_iter_at_mark(self.buf.get_insert()).get_offset(),
                                               self.buf.get_char_count())
        if was is None or was[1] != self.buf.get_char_count():
            return  # (moved by the text changing, as it's typed or as an edit puts in a bullet: left where it's put)
        if self.snapping is None:
            self.snapping = GLib.idle_add(self._caret_in_marker, was[0], priority=GLib.PRIORITY_HIGH_IDLE)  # (before
                                                                                     # the frame it'd be drawn in)

    def _caret_in_marker(self, was):
        """A list item has two places for the caret: before its bullet (or number), and at its text. Its unseen indent,
        the bullet and the space after it (and a box) are one step: the caret moved into them goes on the way it was
        going, right to the text, left to before the bullet, and left again to the line before. A selection's moving
        end too."""
        self.snapping = None
        if self.notebook is None:
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        if (hit := self._item_at(ins.get_line())) is None:
            return False
        start, m = hit
        before, text, at = len(m.group(1)), m.end(), ins.get_line_offset()
        if at == before or at >= text:
            return False
        to = start.copy()
        back = was > ins.get_offset()
        if at < before and back and was == start.get_offset() + before and start.get_line() > 0:
            to.backward_char()  # (left from before the bullet: the line before's end)
        else:
            to.forward_chars(before if at < before or back else text)
        if self.buf.get_has_selection():
            self.buf.move_mark(self.buf.get_insert(), to)
        else:
            self.buf.place_cursor(to)
        return False

    def _out_of_marker(self):
        """The caret in a list item's bullet, box or indent (all drawn over, or unseen): put just after them, so what's
        typed goes in the item's text, not its marker."""
        if self.buf.get_has_selection():
            return
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        start = ins.copy()
        start.set_line_offset(0)
        if self._green_at(start.get_offset()) is not None:
            return  # (a line of what's proposed: its marker isn't the page's to type after)
        if (hit := self._item_at(ins.get_line())) is not None and ins.get_line_offset() < hit[1].end():
            to = hit[0].copy()
            to.forward_chars(hit[1].end())
            self.buf.place_cursor(to)
        elif (cont := self._continued(ins.get_line())) is not None and ins.get_line_offset() < cont[2]:
            to = ins.copy()
            to.set_line_offset(cont[2])  # (a line going on with an item: after its unseen spaces)
            self.buf.place_cursor(to)

    def _list_enter(self):
        """Enter in a list item, as Self's editor has it: the next item (a number one more, a box not ticked); Enter
        on an empty one ends the list. True if it was in one."""
        if self.buf.get_has_selection():
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        line = ins.copy()
        line.set_line_offset(0)
        end = ins.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        text = self.buf.get_slice(line, end, True)
        m = LIST_ITEM_RE.match(text)
        if not m and (cont := self._continued(ins.get_line())) is not None:  # (on a line going on with an item: the
            m = cont[1]                                                        # list's next item)
            n = re.match(r"\d+", m.group(2))
            marker = f"{int(n.group(0)) + 1}{m.group(2)[n.end():]}" if n else m.group(2)
            prefix = m.group(1) + marker + m.group(3) + ("[ ] " if m.group(4) else "")
            self.buf.begin_user_action()
            if not text.strip():  # (an empty one: the new item in its place)
                self.buf.delete(line, end)
                self.buf.insert_at_cursor(prefix)
            else:
                self.buf.insert_at_cursor("\n" + prefix)
            self.buf.end_user_action()
            return True
        if not m or ins.get_line_offset() < m.end():
            return False
        self.buf.begin_user_action()
        if not text[m.end():].strip() and ins.equal(end):  # (an empty item: the list ends)
            self.buf.delete(line, end)
        else:
            n = re.match(r"\d+", m.group(2))
            marker = f"{int(n.group(0)) + 1}{m.group(2)[n.end():]}" if n else m.group(2)
            self.buf.insert_at_cursor("\n" + m.group(1) + marker + m.group(3) + ("[ ] " if m.group(4) else ""))
        self.buf.end_user_action()
        return True

    def _paste(self, view):
        """A web address pasted over a selection: the selection, as a link to it ([text](url)), as Self's editor has
        it. Anything else pasted as it is."""
        if not self.buf.get_has_selection():
            return
        view.stop_emission_by_name("paste-clipboard")
        a, b = self.buf.get_selection_bounds()
        if any(a.get_offset() < y and b.get_offset() > x for x, y in self._greens()):
            return  # (over what's proposed: it isn't the page's to paste over)
        marks = (self.buf.create_mark(None, a, True), self.buf.create_mark(None, b, False))
        view.get_clipboard().read_text_async(None, self._pasted, marks)

    def _pasted(self, clipboard, result, marks):
        try:
            pasted = clipboard.read_text_finish(result) or ""
        except GLib.Error:
            pasted = ""
        a, b = (self.buf.get_iter_at_mark(m) for m in marks)
        chosen = self.buf.get_slice(a, b, True)
        url = pasted.strip()
        self.buf.begin_user_action()
        self.buf.delete(a, b)
        a = self.buf.get_iter_at_mark(marks[0])
        self.buf.insert(a, f"[{chosen}]({url})" if re.fullmatch(r"https?://\S+", url) and chosen else pasted)
        self.buf.end_user_action()
        for m in marks:
            self.buf.delete_mark(m)

    def _at(self, x, y):
        """What's under the pointer: ("link", page), ("ref", name) or ("url", address), or None."""
        bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        ok, it = self.view.get_iter_at_location(bx, by)
        if not ok:
            return None
        off, text = it.get_offset(), self.text()
        for m in LINK_RE.finditer(text):
            if m.start() <= off < m.end():
                if m.group(1).lstrip().startswith("@"):  # ([[@name]]: a reference)
                    return "ref", m.group(1).strip()[1:]
                return "link", page_target(self.page, m.group(1))
        for m in REF_RE.finditer(text):
            if m.start() <= off < m.end():
                return "ref", m.group(1) or m.group(2)
        for m in MD_LINK_RE.finditer(text):
            if m.start() <= off < m.end():
                return "url", m.group(2)
        for m in URL_RE.finditer(text):
            if m.start() <= off < m.end():
                return "url", m.group(0)
        return None

    def _pressed(self, gesture, n, x, y):
        """A click come down on a link: taken from the text view, which would put the caret in it (and, the line's
        markup shown for the caret, move the link from under the pointer); it's followed as the click ends (_click),
        the caret left where it was. With ⌥ (Alt) or Shift, or a second click, it's the text view's: the caret in it,
        a selection. Elsewhere, the text view's, but the caret it puts at the end of a line's text goes past the
        markup hidden after it (_past_markup)."""
        self.pressed = None
        if gesture.get_current_button() != 1 or n != 1 or \
                gesture.get_current_event_state() & (Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SHIFT_MASK):
            return
        if (hit := self._at(x, y)) is not None:
            self.pressed = hit
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        # where the line's text ends as it's shown, and where the line does, while its markup is still hidden
        line = self.view.get_line_at_y(self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, 0, int(y))[1])[0]
        end = line.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        shown, hidden = end.copy(), self.buf.get_tag_table().lookup("hidden")
        while shown.compare(line) > 0 and (before := shown.copy()).backward_char() and before.has_tag(hidden):
            shown = before
        if shown.compare(end) < 0:
            GLib.idle_add(self._past_markup, shown.get_offset(), end.get_offset())

    def _past_markup(self, shown, end):
        """After a click, once the text view has put the caret: put at the end of a line's text, just before markup
        hidden there (a code span's closing `, a bold's **, a link's address or ]]), it goes after it, at the line's
        end, out of the span, as a click at the end of a line means."""
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        if not self.buf.get_has_selection() and ins.get_offset() == shown and end <= self.buf.get_char_count():
            self.buf.place_cursor(self.buf.get_iter_at_offset(end))
        return False

    def _click(self, _gesture, _n, _x, _y):
        hit, self.pressed = self.pressed, None
        if hit is None:
            return
        if hit[0] == "link":
            self.open_page(hit[1])
        elif hit[0] == "url" and hit[1].startswith(CITE_SCHEME):
            self.win.open_citation(hit[1])
        elif hit[0] == "url":
            Gtk.UriLauncher(uri=hit[1]).launch(self.win, None, None)
        else:
            self.win.open_ref(hit[1])

    def _hover(self, _c, x, y):
        """Over a link or an @name, the hand, as links have; else the I-beam. Set once the text view's own handling of
        the motion is done: after typing, its first motion puts its I-beam back (it hid the pointer while you typed),
        which would cover the hand. Come over an @name, what it is (Window.hover_ref), once till it's left."""
        hit = self._at(x, y)
        GLib.idle_add(DiaryEditor._pointer, weakref.ref(self.view), pointers.HAND if hit else pointers.TEXT)
        name = hit[1] if hit and hit[0] == "ref" else None
        if name != self.hovered:
            self.hovered = name
            if name:
                self.win.hover_ref(name, at=(self.view, x, y))
            else:
                self.win.hover_left()

    @staticmethod
    def _pointer(view, want):
        view = view()
        if view is not None:
            pointers.set_pointer(view, want)
        return False

    # ---- @
    def _token_changed(self):
        """The caret moved or the text changed: @'s list follows the @word it's at the end of, if any. Once @'s list
        is up, what's typed after the @ is its search, spaces and all (each word found in any order), till Esc, Enter
        or Tab takes or leaves it, or a new line, or the caret goes back before the @. A new @ or # typed after a space
        starts a list of its own, and what's before it stays as it was typed. # does the same, for GitHub's issues and
        pull requests, where there are any and only as long as what's typed still asks for them (issue_query)."""
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        line = ins.copy()
        line.set_line_offset(0)
        before = self.buf.get_slice(line, ins, True)
        start, offset = line.get_offset(), ins.get_offset()
        if self.token is not None and self.typed_at is not None and self.typed_at > self.token[0] and \
                start < self.typed_at <= offset and ISSUE_OPENS.fullmatch(before[self.typed_at - start - 1]):
            self.token, self.pop_moved = None, False
        if self.token is not None and self.token[0] < offset:
            typed = self.buf.get_slice(self.buf.get_iter_at_offset(self.token[0]), ins, True)
            if typed.startswith("#") and "\n" not in typed:
                return self._issue_search(before, self.token[0] - start, start)
            if typed.startswith("@") and "\n" not in typed:
                self.token = (self.token[0], typed[1:].lstrip("{"))
                self.token_far = max(self.token_far or 0, offset)
                self._mark_token()
                return self._fill_pop()
        m = re.search(r"(?<![\w@])@(\{[^}\n]*|[\w./:\-]*)$", before) or re.search(r"#[\w./:\-]*$", before)
        if not m or offset - len(m.group(0)) != self.typed_at:  # (only an @ just typed: the caret come
            return self._hide_pop()  # back to an @name's end doesn't start its search again, to take in what's next)
        if m.group(0).startswith("#"):
            return self._issue_search(before, m.start(), start)
        self.token = (offset - len(m.group(0)), m.group(1).lstrip("{"))
        self.token_far = offset
        self._mark_token()
        self._fill_pop()

    def _issue_search(self, before, at, start):
        """A # at position at of its line (it starts at offset start; before: the line up to the caret), and what's been
        typed after it: #'s list follows, as long as it's asking (issue_query, or it's in code: neither says so). A
        line's first # with nothing after it yet waits to see, listing nothing: a heading's, as like as not."""
        query = issue_query(before, at)
        if query is None or self._in_code(start + len(before)):
            return self._hide_pop()
        if not query and at == ISSUE_LEAD.match(before).end():
            self.token = self.token_far = None  # (typed_at kept: what follows decides)
            self._mark_token()
            if self.pop.get_visible():
                self.pop.popdown()
            return None
        self.token = (start + at, query)
        self.token_far = max(self.token_far or 0, start + len(before))
        self._mark_token()
        return self._fill_pop()

    def _in_code(self, offset):
        """Whether a place in the page is in a code block or a `code` span (a # there is a comment's, a colour's)."""
        text = self.text()
        before = text[:offset]
        at = before.count("\n")
        return any(first <= at <= last for first, last, _closed in widgets.fence_spans(text.split("\n"), typing=(at,))) or \
            before.rpartition("\n")[2].count("`") % 2 == 1

    def _mark_token(self):
        """@'s search while it's on: what's been typed of it, from the @ to the farthest the caret has been, in the
        @names' colour; none once it's left."""
        start, end = self.buf.get_bounds()
        self.buf.remove_tag_by_name("token", start, end)
        if self.token is not None and self.token_far is not None:
            b = min(self.token_far, self.buf.get_char_count())
            if b > self.token[0]:
                self.buf.apply_tag_by_name("token", self.buf.get_iter_at_offset(self.token[0]),
                                           self.buf.get_iter_at_offset(b))

    def _pop_bind(self, i, icon, name, where):
        it = self.pop_shown[i]
        icon.icon = it["icon"]
        name.set_text(it.get("label") or it["surface"])
        where.set_text(f"{it['what']} · {it['summary']}")
        widgets.time_tip(where, it.get("at"), it.get("ago"))

    def pop_selected(self):
        """The item @'s list has selected, or None."""
        i = self.picks.picked()
        return self.pop_shown[i] if i is not None and i < len(self.pop_shown) else None

    def _issue_list(self):
        """Whether the list up is #'s."""
        return self.token is not None and self.buf.get_iter_at_offset(self.token[0]).get_char() == "#"

    def _exact(self):
        """Whether #'s list's first is the number typed: what's been typed means that one (issues: it's first)."""
        return bool(self.pop_shown) and self.pop_shown[0].get("label") == self.token[1].strip()

    def _fill_pop(self):
        space = self.win.space()
        issues = self._issue_list()
        kept = self.pop_selected() if self.pop_moved and self.pop_query == self.token[1] else None
        self.pop_shown, matched = ([], 0) if space is None else \
            space.issues(self.token[1]) if issues else space.search(self.token[1])  # (all of them)
        if issues and not self.pop_shown:  # (a # is an ordinary thing to type: no list, and nothing said)
            return self._hide_pop()
        self.pop_query = self.token[1]
        at = next((i for i, it in enumerate(self.pop_shown) if kept and it["surface"] == kept["surface"]), None)
        self.pop_moved = at is not None  # (the same search, listed again: what's picked stays picked)
        if at is None and (not issues or self._exact()):  # (# picks nothing it wasn't asked to: Enter's a new line)
            at = 0
        if space is None:
            note = "Attach a folder to @ what's in it"
        else:
            if self.waiting is None:  # (catalogued afresh if it's old: listed again once it's in)
                self.waiting = space.refresh(self._catalogued)
            note = (f"{matched} found" if matched else "Nothing found") + (" · cataloguing…" if space.busy else "")
        it = self.buf.get_iter_at_offset(self.token[0])
        loc = self.view.char_rect(it)
        x, y = self.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, loc.x, loc.y)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = x, y, 1, loc.height
        self.picks.show(len(self.pop_shown), at, note, rect)

    def _catalogued(self):
        self.waiting = None
        self.github_changed()

    def github_changed(self):
        """What the names are has changed (the catalogue's in, or GitHub's lists are new)."""
        self._highlight()  # (the @names' logos, now they're known)
        if self.pop.get_visible() and self.token is not None:
            self._fill_pop()

    def _hide_pop(self):
        self.token = self.token_far = self.typed_at = None
        self.pop_moved = False
        self._mark_token()
        if self.pop.get_visible():
            self.pop.popdown()

    def _key(self, _c, keyval, _code, state):
        if keyval == Gdk.KEY_Escape and self.search_bar.get_search_mode() and not self.pop.get_visible():
            FindInText.close_search(self)  # (the find bar open as you write: closed, the caret left where it is)
            return True
        primary = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK)
        if primary and keyval in (Gdk.KEY_z, Gdk.KEY_Z, Gdk.KEY_y, Gdk.KEY_Y):  # (the page's own Undo: PageHistory)
            again = keyval in (Gdk.KEY_y, Gdk.KEY_Y) or state & Gdk.ModifierType.SHIFT_MASK
            self.history.redo() if again else self.history.undo()
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_Escape) and not self.pop.get_visible() and not state & (
                Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.ALT_MASK
                | Gdk.ModifierType.SHIFT_MASK) and \
                (e := self._proposal_at(self.buf.get_property("cursor-position"))) is not None:
            self.decide(e["p"], keyval == Gdk.KEY_Tab)  # (the caret in a change proposed: Tab takes it, Esc leaves it)
            return True
        if self._edits_green(keyval, state):
            return True  # (what's proposed isn't the page's: nothing typed into it)
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_KP_Tab) and \
                self.pop.get_visible() and self._issue_list() and not self.pop_moved and not self._exact():
            # (#'s list, and nothing asked of it: what was typed stays as typed. Enter's a new line, as it always is)
            self._hide_pop()
            if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                return True
        if state & Gdk.ModifierType.ALT_MASK and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK) \
                and not self.pop.get_visible() and self._moved_heading(keyval, bool(state & Gdk.ModifierType.SHIFT_MASK)):
            return True
        if keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete, Gdk.KEY_KP_Delete) or (
                Gdk.keyval_to_unicode(keyval) and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK)):
            self._whole_links()  # (what's selected, about to go: no link's marks left behind)
        if Gdk.keyval_to_unicode(keyval) and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK):
            self._into_span()  # (a whole formatted span selected: what's typed goes in it, the formatting kept)
        if widgets.leave_wrapped(self.buf, keyval, state):
            return True  # (just formatted, and Left or Right: outside its marks)
        if not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK) and \
                widgets.wrap_selection(self.buf, chr(Gdk.keyval_to_unicode(keyval) or 0)):
            return True  # (a selection, and * ` or /: bold, code, italic)
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter) and \
                not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK):
            self._em_dash()  # (and the space or Enter goes in after it, as typed)
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self._sub_page() and keyval == Gdk.KEY_space:
            return False  # (the space follows the link, as typed; Enter's the Enter it would have been: a list goes on)
        if keyval == Gdk.KEY_BackSpace and (self._unbullet() or self._unbreak()):
            return True
        if keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete, Gdk.KEY_KP_Delete) and \
                self._delete_at_link(keyval == Gdk.KEY_BackSpace):
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_KP_Tab) and not self.pop.get_visible() and \
                self._indent_items(-1 if keyval == Gdk.KEY_ISO_Left_Tab or state & Gdk.ModifierType.SHIFT_MASK else 1):
            return True
        if Gdk.keyval_to_unicode(keyval) and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK):
            self._out_of_marker()
        if not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK) and \
                (Gdk.keyval_to_unicode(keyval) or keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)):
            self._out_of_link()  # (what's typed at a link's edge goes beside it, not into its name)
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up) and not self.pop.get_visible() and not state & (
                Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK
                | Gdk.ModifierType.ALT_MASK) and self._up_to_title():
            return True
        if not self.pop.get_visible():
            if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                return False  # (Shift+Enter: a line of the item's text under it; Enter: the list's next item)
            return self._continue_item() if state & Gdk.ModifierType.SHIFT_MASK else self._list_enter()
        if keyval == Gdk.KEY_Escape:
            self._hide_pop()
            return True
        steps = widgets.PickList.STEPS
        if state & Gdk.ModifierType.SHIFT_MASK and keyval in (*steps, Gdk.KEY_Left, Gdk.KEY_Right):
            self._hide_pop()  # (Shift and an arrow: selecting the text, not picking in the list)
            return False
        if keyval in steps:
            if self.pop_shown:
                self.picks.move(steps[keyval])
                self.pop_moved = True
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Tab) and self.pop_selected() is not None:
            if self._issue_list() and not self.pop_moved:
                self.pop_sel.set_selected(0)  # (the number typed: not whatever the pointer's over)
            self._insert_ref()
            return True
        return False

    def _moved_heading(self, keyval, shift):
        """_heading_key, with what's proposed taken out first wherever it could move lines (Up or Down: a heading's, a
        list item's; Left or Right: on a heading's line): lines moved with what's proposed in them would make that the
        page's. Nothing done: the caret and the selection where they were."""
        if not self._greens() or keyval not in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right):
            return self._heading_key(keyval, shift)
        marks = (self.buf.get_insert(), self.buf.get_selection_bound())
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            ins, sel = (self.buf.get_iter_at_mark(m).get_line() for m in marks)
            lines = self.text().split("\n")
            if not any(re.match(r"#{1,6}\s", lines[n]) for n in range(min(ins, sel), max(ins, sel) + 1)):
                return self._heading_key(keyval, shift)
        ins, sel = (self.kept_offset(self.buf.get_iter_at_mark(m).get_offset()) for m in marks)
        with self._greens_out():
            done = self._heading_key(keyval, shift)
        if done:
            self._changed(self.buf)
        else:
            self.buf.select_range(*(self.buf.get_iter_at_offset(self.buf_offset(k, past=False)) for k in (ins, sel)))
            self._highlight()
        return done

    def _heading_key(self, keyval, shift):
        """Alt and an arrow on a heading, as Org's: up or down, swapped with its sibling that way, all under it going
        along; Shift+Alt left or right, its level and all its children's up or down one; Alt left or right, its level
        alone, or every heading's in the selection. True if it was one of these on a heading (with nothing to do, as
        no sibling or a level past 1 to 6, nothing's done): elsewhere the key's as it always was."""
        lines = self.text().split("\n")
        levels = heading_levels(lines)
        at = self.buf.get_iter_at_mark(self.buf.get_insert()).get_line()
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and not shift:
            by = -1 if keyval == Gdk.KEY_Up else 1
            if not levels[at]:
                return self._move_item(lines, at, by)
            self._swap_heading(lines, at, by)
            return True
        if keyval not in (Gdk.KEY_Left, Gdk.KEY_Right):
            return False
        if shift:
            rows = [i for i in range(at, subtree_end(levels, at)) if levels[i]] if levels[at] else []
        elif self.buf.get_has_selection():
            start, end = self.buf.get_selection_bounds()
            last = end.get_line() - (1 if end.get_line_offset() == 0 and end.get_line() > start.get_line() else 0)
            rows = [i for i in range(start.get_line(), last + 1) if levels[i]]
        else:
            rows = [at] if levels[at] else []
        if not rows:
            return False
        by = 1 if keyval == Gdk.KEY_Right else -1
        if all(1 <= levels[i] + by <= 6 for i in rows):
            self._in_one_step(lambda: self._shift_headings(rows, by))
        return True

    def _shift_headings(self, rows, by):
        for row in rows:
            start = self.buf.get_iter_at_line(row)[1]
            if by > 0:
                self.buf.insert(start, "#")
            else:
                after = start.copy()
                after.forward_char()
                self.buf.delete(start, after)

    def _move_item(self, lines, at, by):
        """Alt and Up or Down in a list item: it, and all under it, past its sibling that way. True if it's in one (with
        none that way, nothing's done); elsewhere the key's as it always was."""
        start = at
        while start > 0 and lines[start].startswith((" ", "\t")) and lines[start].strip() and \
                not LIST_ITEM_RE.match(lines[start]):
            start -= 1
        if not LIST_ITEM_RE.match(lines[start]):
            return False
        self._swap_lines(lines, widgets.swap_list_item(lines, at, by))
        return True

    def _swap_heading(self, lines, at, by):
        self._swap_lines(lines, swap_with_sibling(lines, at, by))

    def _swap_lines(self, lines, swapped):
        if swapped is None:
            return
        new, one, three, moved = swapped
        column = self.buf.get_iter_at_mark(self.buf.get_insert()).get_line_offset()

        def replace():
            start = self.buf.get_iter_at_line(one)[1]
            end = self.buf.get_iter_at_line(three)[1] if three < len(lines) else self.buf.get_end_iter()
            self.buf.delete(start, end)
            self.buf.insert(start, "\n".join(new) + ("\n" if three < len(lines) else ""))
            self.buf.place_cursor(self.buf.get_iter_at_line_offset(moved, column)[1])
        self._in_one_step(replace)

    def _in_one_step(self, edit):
        """An edit of many steps: one undo, the page kept and drawn again once, when it's done."""
        was, self.loading = self.loading, True
        self.buf.begin_user_action()
        try:
            edit()
        finally:
            self.buf.end_user_action()
            self.loading = was
        self._changed(self.buf)

    def _whole_links(self):
        """A selection about to be cut, copied, deleted or typed over: no link split by it (whole_links), the marks
        unseen so nothing looks different."""
        if not self.buf.get_has_selection():
            return
        start, end = (i.get_offset() for i in self.buf.get_selection_bounds())
        wide = whole_links(self.text(), start, end)
        if wide != (start, end):
            ins = self.buf.get_iter_at_mark(self.buf.get_insert()).get_offset()
            first, last = self.buf.get_iter_at_offset(wide[0]), self.buf.get_iter_at_offset(wide[1])
            self.buf.select_range(*((first, last) if ins == start else (last, first)))

    def _into_span(self):
        """A whole formatted span selected (its text and the markup round it, hidden: a double-click on it, say):
        narrowed to its text, so what's typed in its place is formatted so, the markup kept. Markup shown (Settings
        → Markup visibility, the caret's line) is the selection's as it's shown."""
        if not self.buf.get_has_selection():
            return
        s, e = (i.get_offset() for i in self.buf.get_selection_bounds())
        text, hidden = self.text(), self.buf.get_tag_table().lookup("hidden")
        start = text.rfind("\n", 0, s) + 1
        end = len(text) if (n := text.find("\n", s)) < 0 else n
        if e > end:
            return

        def unseen(x, y):
            return all(self.buf.get_iter_at_offset(i).has_tag(hidden) for i in range(x, y))
        for rx, _name, side in self.INLINE:
            for m in rx.finditer(text[start:end]):
                x, y = start + m.start(), start + m.end()
                if x <= s <= x + side and y - side <= e <= y and (s, e) != (x + side, y - side) and \
                        unseen(s, x + side) and unseen(y - side, e):
                    self.buf.select_range(self.buf.get_iter_at_offset(x + side), self.buf.get_iter_at_offset(y - side))
                    return

    def _em_dash(self):
        """-- just typed on its own, whitespace before it, then a space or Enter: it's an em dash, as the text's -- is
        drawn in a reply (widgets.EM_DASH). Not in code: a `span` (an odd number of backticks before it on the line,
        so one still being typed too) or a ``` block. True if it was."""
        if self.notebook is None or self.buf.get_has_selection():
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        line = ins.copy()
        line.set_line_offset(0)
        before = self.buf.get_slice(line, ins, True)
        if not re.search(r"(?<=\s)--$", before) or before[:-2].count("`") % 2:
            return False
        start = self.buf.get_iter_at_offset(ins.get_offset() - 2)
        table = self.buf.get_tag_table()
        if any(start.has_tag(table.lookup(name)) for name in ("verbatim", "pre")):
            return False
        self.buf.begin_user_action()
        self.buf.delete(start, ins)
        self.buf.insert_at_cursor("\u2014")
        self.buf.end_user_action()
        return True

    def _sub_page(self):
        """+Ideas just typed, then a space or Enter: it becomes [[+Ideas]], a link to this page's sub-page Ideas, which
        the page list has for the link, grey until something's written in it, as Zim does. True if it was."""
        if self.notebook is None or self.buf.get_has_selection():
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        line = ins.copy()
        line.set_line_offset(0)
        m = SUBPAGE_RE.search(self.buf.get_slice(line, ins, True))
        if not m:
            return False
        start = self.buf.get_iter_at_offset(ins.get_offset() - len(m.group(0)))
        self.buf.begin_user_action()
        self.buf.delete(start, ins)
        self.buf.insert_at_cursor(f"[[+{m.group(1)}]]")  # (listed, and drawn, as the text changes: _changed)
        self.buf.end_user_action()
        return True

    def _delete_at_link(self, back):
        """Backspace or Delete at a link: its hidden marks ([[ ]], +, target|) are never broken into text. Backspace
        just after it takes its name's last letter, Delete just before it its first, as a link's text is edited in
        Zim; once there's none left, the link goes. At its name's first letter Backspace reaches past the marks to
        the text before the link, at its last Delete to the text after. A pull request's or an issue's #12 goes whole,
        from anywhere in it (as #1, it would still be #12 when it's clicked). True if it was a link's."""
        if self.notebook is None or self.buf.get_has_selection():
            return False
        at = self.buf.get_property("cursor-position")
        text = self.text()
        for m in LINK_RE.finditer(text):
            if m.start() > at:
                break
            if at > m.end():
                continue
            a, b = link_name(m)
            if github_ref(m) and (a < at <= m.end() if back else m.start() <= at < b):
                return self._delete_span(m.start(), m.end())
            if back and (at == m.end() or b < at < m.end()):
                return self._shorten(m, a, b, last=True)
            if not back and (at == m.start() or m.start() < at < a):
                return self._shorten(m, a, b, last=False)
            if back and m.start() < at <= a:
                return self._delete_span(m.start() - 1, m.start()) if m.start() else True
            if not back and b <= at < m.end():
                return self._delete_span(m.end(), m.end() + 1) if m.end() < len(text) else True
        return False

    def _shorten(self, m, a, b, last):
        if b - a <= 1:
            return self._delete_span(m.start(), m.end())
        return self._delete_span(b - 1, b) if last else self._delete_span(a, a + 1)

    def _delete_span(self, a, b):
        if any(a < y and b > x for x, y in self._greens()):
            return True  # (what's proposed, in a link or beside it: not the page's to delete)
        self.buf.begin_user_action()
        self.buf.delete(self.buf.get_iter_at_offset(a), self.buf.get_iter_at_offset(b))
        self.buf.end_user_action()
        return True

    def _out_of_link(self):
        """The caret at a link's name's first or last letter (or among its hidden marks): put just outside the link,
        so what's typed there goes beside it, not into its name. Inside its name, it stays."""
        if self.notebook is None or self.buf.get_has_selection():
            return
        at = self.buf.get_property("cursor-position")
        for m in LINK_RE.finditer(self.text()):
            if m.start() > at:
                break
            a, b = link_name(m)
            to = m.start() if m.start() < at <= a else m.end() if b <= at < m.end() or github_ref(m) and a < at < b \
                else None
            if to is not None:
                self.buf.place_cursor(self.buf.get_iter_at_offset(to))
                return

    def _insert_ref(self):
        item = self.pop_selected()
        if item is None or self.token is None:
            return
        start = self.buf.get_iter_at_offset(self.token[0])
        end = self.buf.get_iter_at_mark(self.buf.get_insert())
        self._hide_pop()
        self.buf.begin_user_action()
        self.buf.delete(start, end)
        self.buf.insert_at_cursor(ref_text(item["surface"], item.get("label")) + " ")
        self.buf.end_user_action()
        self.view.grab_focus()
        self._hide_pop()


INSIDE_EDGE = widgets.INSIDE_GAP  # (from a column's edge to the inside's)


class FixedWidth(Gtk.Widget):
    """child held at width (its minimum and its natural width both), or none while it's hidden: the notebooks'
    column, as wide as the pages' was, whatever its names would have it be. (A widget of its own: Adw.Bin measures
    through its layout manager, and a subclass's do_measure is never asked.)"""

    GRIP = 6  # px of its right edge that drag it
    LEAST, MOST = 160, 600  # px: past the window's buttons and the toggle at the least, the most for a very wide window
    CLOSE_WITHIN = widgets.PaneSlide.CLOSE_WITHIN  # (dragged on till it'd be this wide: slide, holds it shut)

    def __init__(self, child, width):
        super().__init__(hexpand=False)  # (set: never the spare width, whatever's inside would take)
        self.child, self.width, self.showing = child, width, None
        self.set_overflow(Gtk.Overflow.HIDDEN)  # (showing less of it, as it opens or closes: ColumnSlide)
        child.set_parent(self)
        child.connect("notify::visible", lambda *_: self.queue_resize())
        # (its right edge drags it wider or narrower: the arrows over it, the press heard here, as this doesn't move as
        # that edge does, and before what's inside: the rest of the column's its own)
        self.resized = None  # (told its width as a drag ends)
        self.slide = None
        self.grip = pointers.set_pointer(Gtk.Box(), "ew-resize")
        self.grip.set_parent(self)
        self.dragged_from = None
        drag = Gtk.GestureDrag(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)

    def grip_past(self, host):
        """The rest of what drags its right edge, to go over what's beside it: the gap there and as far again into the
        inside, so that the arrows and the press are either side of the edge that's seen, as a pane's rule's are. host hears
        the press, as what doesn't move as that edge does: a drag measured from the grip, which does, is told less for every
        pixel it's gone, and the width shakes."""
        grip = pointers.set_pointer(Gtk.Box(width_request=INSIDE_EDGE + self.GRIP, halign=Gtk.Align.START), "ew-resize")
        grip.add_css_class("column-grip")
        self.past = grip
        grip.set_visible(self.child.get_visible())
        self.child.connect("notify::visible", lambda c, _p: grip.set_visible(c.get_visible()))
        drag = Gtk.GestureDrag(propagation_phase=Gtk.PropagationPhase.CAPTURE)

        def began(gesture, x, y):
            if host.pick(x, y, Gtk.PickFlags.DEFAULT) is grip:
                self._drag_begin(gesture, self.width, 0)
            else:
                gesture.set_state(Gtk.EventSequenceState.DENIED)
        drag.connect("drag-begin", began)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        host.add_controller(drag)
        return grip

    def _drag_begin(self, gesture, x, _y):
        if self.showing is not None or not self.child.get_visible() or x < self.width - self.GRIP:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.dragged_from = self.width
        if self.slide is not None:
            self.slide.motion(True)

    def _drag_update(self, _gesture, dx, _dy):
        if self.dragged_from is None:
            return
        wanted = self.dragged_from + dx
        if self.slide is None or not self.slide.hold(wanted <= self.CLOSE_WITHIN, self.limited(wanted)):
            self.set_width(wanted)

    def _drag_end(self, _gesture, _dx, _dy):
        if self.dragged_from is None:
            return
        began, self.dragged_from = self.dragged_from, None
        if self.slide is not None:
            self.slide.motion(False)  # (a slide shut after it, let_go's: its own)
        if self.slide is not None and self.slide.let_go(began):
            return
        if self.resized is not None:
            self.resized(self.slide.held_at if self.slide is not None and self.slide.glide is not None else self.width)

    def limited(self, width):
        """Between LEAST and half of what the window has, and no wider than leaves what's beside it its least: wider, the
        window's insides were wider than it, the column and the right bar past its edges (#166). Never under LEAST."""
        window = self.get_root()
        if window is None or not window.get_width():
            return round(max(self.LEAST, min(self.MOST, width)))
        across = Gtk.Orientation.HORIZONTAL
        beside = window.measure(across, -1)[0] - self.measure(across, -1)[0]  # (the least of all but this)
        most = min(window.get_width() // 2, window.get_width() - beside)
        return round(max(self.LEAST, min(most, self.MOST, width)))

    def set_width(self, width):
        """As wide as that, limited."""
        width = self.limited(width)
        if width != self.width:
            self.width = width
            self.queue_resize()

    def do_measure(self, orientation, for_size):
        if self.child is None or not self.child.get_visible():
            return 0, 0, -1, -1
        if orientation == Gtk.Orientation.HORIZONTAL:
            shown = self.width if self.showing is None else self.showing
            return shown, shown, -1, -1
        return self.child.measure(orientation, self.width)

    def do_size_allocate(self, width, height, baseline):
        if self.child is not None and self.child.get_visible():
            self.child.allocate(self.width, height, baseline, None)  # (where it is: what's beside it moves over it)
            at = Gsk.Transform().translate(Graphene.Point().init(max(0, width - self.GRIP), 0))
            self.grip.set_child_visible(self.showing is None)
            self.grip.allocate(self.GRIP, height, baseline, at)
        else:
            self.grip.set_child_visible(False)

    def do_dispose(self):
        if self.child is not None:
            self.child.unparent()
            self.grip.unparent()
            self.child = None
        Gtk.Widget.do_dispose(self)


class ColumnSlide(widgets.SideSlide):
    """The notebooks' column opened and closed smoothly: how much of it's shown (FixedWidth.showing), what's beside it
    following its edge, and nothing left to change when it's done. (Its toggle's in the window's bar: that doesn't move.)"""

    def __init__(self, fixed, pane, toggles=()):
        self.host, self.fixed = fixed, fixed
        super().__init__(pane, toggles)

    def open_at(self, width):
        self.fixed.width = width

    def value(self):
        return float(self.fixed.width if self.fixed.showing is None else self.fixed.showing)

    def apply(self, value):
        self.fixed.showing = round(value)
        self.fixed.queue_resize()

    def closed_value(self):
        return 0.0

    def open_value(self):
        return float(self.fixed.width)

    def remember_open(self):
        pass

    def settle(self):
        self.fixed.showing = None
        self.fixed.queue_resize()


class PagesList(Gtk.Box):
    """The open notebook's pages, at the top of the left column (Window; the notebooks under them: pages are gone
    to more often): a header as Baude's days have (its arrow folds the list away, its + makes a page), then a compact
    list, a sub-page on a line off its parent's, as Baude lists its chats. A page clicked opens in a tab of its own
    (Window.open_note); right-click one for a sub-page under it, or to rename or delete it. What it lists is Zim's:
    the pages written in, every page a link goes to (grey till it's written in), Home and the pages open, and their
    parents."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.notebook, self.listed = win, None, None
        self.head = widgets.group_header("Pages", win.store.pages_folded, lambda: self.set_folded(not self.folded),
                                         plus=("list-add-symbolic", "New page (+Name in a page makes one under it)",
                                               self.new_page))
        self.head.set_margin_start(12)
        self.head.set_margin_end(8)
        self.head.set_margin_top(6)
        self.append(self.head)
        self.index = widgets.TreeList(selection_mode=Gtk.SelectionMode.SINGLE)
        self.index.add_css_class("navigation-sidebar")
        self.index.connect("row-activated", lambda _l, row: self.win.open_note(row.page))
        self.revealer = Gtk.Revealer(child=Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                              child=self.index),
                                     transition_type=Gtk.RevealerTransitionType.NONE,
                                     reveal_child=not win.store.pages_folded)
        self.append(self.revealer)
        self.set_vexpand(not win.store.pages_folded)
        self.revealer.set_vexpand(not win.store.pages_folded)
        acts = Gio.SimpleActionGroup()  # (a page's menu, right-click in the list: New Page…, Rename…, Delete…;
        for name, fn in (("new-under", lambda n: self.new_page(under=n)),  # its name the target)
                         ("rename", self.rename_from_menu), ("delete", lambda n: self.win.delete_page(n))):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            a.connect("activate", lambda _a, v, fn=fn: fn(v.get_string()))
            acts.add_action(a)
        self.insert_action_group("pages", acts)

    @property
    def folded(self):
        return not self.revealer.get_reveal_child()

    def set_folded(self, folded):
        """The list folded away (its header's arrow), or shown again; kept."""
        self.revealer.set_reveal_child(not folded)
        self.revealer.set_vexpand(not folded)
        self.set_vexpand(not folded)
        self.head.set_collapsed(folded)
        self.win.store.pages_folded = folded
        self.win.store.save()
        self.win.left_sizes()

    def show(self, notebook):
        self.notebook, self.listed = notebook, None
        self.fill()

    def names(self):
        """What the list lists: (sorted names, the grey ones: nothing written in them yet)."""
        pages = self.notebook["pages"] if self.notebook else {}
        names = {n for n, text in pages.items() if text.strip()} | {HOME, *self.win.open_pages()} | {
            page_target(n, m.group(1)) for n, text in pages.items() for m in LINK_RE.finditer(text)
            if not m.group(1).lstrip().startswith("@")}  # ([[@name]]: a reference, not a page)
        for n in list(names):  # (a parent made by its sub-pages' names, Work of Work:Plans)
            while ":" in n:
                n = n.rpartition(":")[0]
                names.add(n)
        names.discard(None)
        order = sorted(names, key=lambda n: (n.partition(":")[0] != HOME, [s.lower() for s in n.split(":")]))
        return tuple(order), frozenset(n for n in names if n != HOME and not pages.get(n, "").strip())

    def prune(self):
        """Empty pages but Home and those open let go: one a link goes to is listed for the link (names), and one
        nothing goes to goes from the list, as the link to it went. Nothing written is ever let go."""
        if self.notebook is None:
            return
        pages, keep = self.notebook["pages"], {HOME, *self.win.open_pages()}
        for n in [n for n, text in pages.items() if not text.strip() and n not in keep]:
            del pages[n]

    def changed(self):
        """A page written in (a link made or gone, a page written in or emptied): listed again if that's changed."""
        if self.notebook is not None and self.names() != self.listed:
            self.fill()

    def fill(self):
        """The pages (names), each sub-page under its parent (by its own name, indented, on Baude's tree line),
        Home's first; one with nothing written in it yet is grey and italic, as Zim shows them; the page shown
        selected."""
        while (r := self.index.get_row_at_index(0)) is not None:
            self.index.remove(r)
        if self.notebook is None:
            return
        self.listed = order, grey = self.names()
        shown = getattr(self.win.diary_shown(), "page", None)
        rows = {}
        for name in order:
            depth = name.count(":")
            box = Gtk.Box(spacing=6, margin_start=6 + depth * widgets.TreeList.INDENT, margin_end=4)
            lb = widgets.FadeLabel(label=short_page(name), xalign=0, hexpand=True)  # (too long: it fades where it's cut)
            if name in grey:
                lb.add_css_class("dim-label")
                lb.add_css_class("page-placeholder")
            box.append(lb)
            row = rows[name] = Gtk.ListBoxRow(child=box, tooltip_text=name if depth else None)
            row.page, row.tree_anchor = name, lb  # (the line runs down from under its parent's name to its own)
            row.tree_parent = rows.get(name.rpartition(":")[0]) if depth else None
            click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)  # (right-click: page_menu)
            click.connect("pressed", lambda _g, _n, x, y, row=row: self.page_menu(row, x, y))
            row.add_controller(click)
            self.index.append(row)
            if name == shown:
                self.index.select_row(row)

    def page_menu(self, row, x, y):
        """A page's menu (right-click on it in the list): New Page… (one under it), then Rename… (its title, to
        type in) and Delete…, as a notebook's; Home's has only the first."""
        menu = Gio.Menu()
        sections = [(("New Page…", "pages.new-under"),)]
        if row.page != HOME:
            sections.append((("Rename…", "pages.rename"), ("Delete…", "pages.delete")))
        for items in sections:
            section = Gio.Menu()
            for label_, action in items:
                item = Gio.MenuItem.new(label_, None)
                item.set_action_and_target_value(action, GLib.Variant.new_string(row.page))
                section.append_item(item)
            menu.append_section(None, section)
        pop = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        pop.set_parent(row)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    def rename_from_menu(self, name):
        if (editor := self.win.open_note(name)) is not None:
            editor.title.grab_focus()
            editor.title.select_region(0, -1)

    def new_page(self, under=None):
        """A page, its name typed in: the list's +, or New Page… on one (under: its name), the new one under it."""
        d = Adw.AlertDialog(heading="New page", body=f"Under {under}" if under else "")
        e = Gtk.Entry(activates_default=True)
        d.set_extra_child(e)
        d.add_response("cancel", "Cancel")
        d.add_response("ok", "Add")
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")
        d.connect("response", lambda _d, r: r == "ok" and (typed := e.get_text().strip()) and
                  self.win.open_note(f"{under}:{typed}" if under else typed))
        d.present(self.win)
        e.grab_focus()


NAME_CARD_WAIT = 300  # ms the pointer rests on a name before its card comes up (as a tooltip waits)


def on_name_hover(widget, win):
    """As the pointer rests on a name's link in widget (a label, or a reply's labels: MarkdownView), what it is shown
    (win.hover_ref), once till it leaves the link. Not as text is selected: the card comes up under the pointer, and a drag
    going on to it stops selecting. So it waits, a press calls it off, and a press takes one that's up away."""
    waiting = [None]

    def called_off():
        if waiting[0] is not None:
            GLib.source_remove(waiting[0])
            waiting[0] = None

    def rested(name, x, y):
        waiting[0] = None
        win.hover_ref(name, from_chat=True, at=(widget, x, y))
        return False

    def moved(c, x, y):
        if c.get_current_event_state() & Gdk.ModifierType.BUTTON1_MASK:
            return
        w = widget.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w is not widget and not isinstance(w, Gtk.Label):
            w = w.get_parent()
        # (by where the pointer is: get_current_uri is the caret's link, the first in a selectable label not clicked in)
        ok, at = widget.compute_point(w, Graphene.Point().init(x, y)) if isinstance(w, Gtk.Label) else (False, None)
        uri = (widgets.hover_uri(w, at.x, at.y) if ok else None) or ""
        name = next((urllib.parse.unquote(uri[len(s):].partition("#")[0]) for s in ("ref:", widgets.REF_SCHEME)
                     if uri.startswith(s)), None)
        if name != getattr(widget, "hovered", None):
            widget.hovered = name
            called_off()
            if name:
                waiting[0] = GLib.timeout_add(NAME_CARD_WAIT, rested, name, x, y)
            else:
                win.hover_left()

    def left(*_):
        widget.hovered = None
        called_off()
        win.hover_left()

    def event(c, *_):
        if (e := c.get_current_event()) is not None and e.get_event_type() == Gdk.EventType.BUTTON_PRESS:
            widget.hovered = None
            called_off()
            if win.card is not None:
                GLib.idle_add(win.hide_card)
        return False
    motion = Gtk.EventControllerMotion()  # (after the label's own, which says which link is under the pointer)
    motion.connect("motion", moved)
    motion.connect("leave", left)
    widget.add_controller(motion)
    press = Gtk.EventControllerLegacy()  # (not a gesture: it doesn't come between the press and the label's selecting)
    press.connect("event", event)
    widget.add_controller(press)


class Picks(Gtk.Widget):
    """The passages picked in the chat read (Reader.picks), drawn over it: each shaded as a selection is (but the
    one, its label's own selection), and, more than one picked, each its number in a dot at its top left, as its
    quote to the Record has it."""

    def __init__(self, reader):
        super().__init__(can_target=False, hexpand=True, vexpand=True)
        self.reader = reader

    def do_snapshot(self, snapshot):
        picks = self.reader.shown_picks()
        cited = [q for q in self.reader.cited if self.reader._there(q)]
        if not picks and not cited:
            return
        accent = widgets.accent_rgba()
        fill = accent.copy()
        fill.alpha = 0.3
        for q in cited:  # (a citation followed to here: Reader.show_passage; a message far off, hidden for now: not yet)
            if q["label"].get_mapped():
                widgets.shade_passage(snapshot, fill, q["label"], widgets.passage_rects(q["label"], q["start"], q["end"], self),
                                      self, self.reader.box)
        for n, p in enumerate(picks, 1):
            first = None
            for q in p.get("parts") or [p]:  # (a passage across labels: each's part)
                label = q["label"]
                if not label.get_mapped():  # (a message far off, hidden for now: ZoomPlace)
                    continue
                rects = widgets.passage_rects(label, q["start"], q["end"], self)
                if not rects:
                    continue
                first = first or (label, rects[0])
                ok, a, b = label.get_selection_bounds()
                if not (ok and (a, b) == (q["start"], q["end"]) or p.get("parts") and self.reader.selection.shown):
                    # (a label's own selection, or ChatSelection's, is drawn already)
                    widgets.shade_passage(snapshot, fill, label, rects, self, self.reader.box)
            if first is not None and len(picks) > 1:
                self._number(snapshot, first[0], n, first[1], accent)

    def _number(self, snapshot, label, n, first, accent):
        layout = label.create_pango_layout(str(n))  # (in the label's font: as big as it is, zoomed)
        font = label.get_pango_context().get_font_description().copy()
        if font.get_size_is_absolute():
            font.set_absolute_size(font.get_size() * 0.7)
        else:
            font.set_size(int(font.get_size() * 0.7))
        font.set_weight(Pango.Weight.BOLD)
        layout.set_font_description(font)
        _ink, logical = layout.get_pixel_extents()
        h = logical.height + 2
        w = max(h, logical.width + h * 0.5)
        x, y = first.get_x() - w * 0.6, first.get_y() - h / 2  # (on the passage's corner, a little left of it)
        dot = Graphene.Rect().init(x, y, w, h)
        rounded = Gsk.RoundedRect()  # (kept: what init_from_rect gives back doesn't keep it)
        rounded.init_from_rect(dot, h / 2)
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(accent, dot)
        snapshot.pop()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(x + (w - logical.width) / 2 - logical.x, y + 1))
        white = Gdk.RGBA()
        white.parse("white")
        snapshot.append_layout(layout, white)
        snapshot.restore()


class Reader(Gtk.Box):
    """The chat last opened (an @chat in the notes, ⌘K, Open a chat…, Baude's Open in Record), its prose, live,
    whichever notebook's open: a notebook has no chat. Selecting a passage quotes it to the notebook's Record. The
    names the notebook's folders and the chat's have (⌘K's: files, branches, Kubernetes objects, tables, what Signal
    looked up) are links: one opens where it is. Ctrl (or ⌘) and a drag picks another passage, numbered, every one
    quoted; a drag without it, or a click, lets them all go."""

    PICKING = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.SUPER_MASK

    WIDTH = 760  # its column, unless the text is full width

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.path, self.items, self.rows = win, None, [], []
        self.waiting = None
        self.stack = Gtk.Stack(vexpand=True)
        self.empty = Adw.StatusPage(icon_name="baude-claude-symbolic", title="No chat open",
                                    description="Open a Claude Code chat to read it here: @ one in your notes, find "
                                                "it with ⌘K, or Open in Record in Baude.")
        btn = Gtk.Button(label="Open a chat…", halign=Gtk.Align.CENTER)
        btn.add_css_class("pill")
        btn.add_css_class("suggested-action")
        btn.connect("clicked", lambda *_: win.choose_chat())
        self.empty.set_child(btn)
        self.stack.add_named(self.empty, "empty")
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=16, margin_bottom=32,
                           margin_start=20, margin_end=20)
        # the passages picked ({label, start, end, text}, in order), one being picked with Ctrl, drawn over the chat
        self.picks, self.picking = [], None
        self.citing, self.cited, self.cite_tries = None, [], 0  # (a citation followed: show_passage)
        self.over = Gtk.Overlay(child=self.box)
        self.marks = Picks(self)
        self.over.add_overlay(self.marks)
        self.over.connect("get-child-position", self._laid_out)
        drag = Gtk.GestureDrag(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", self._pick_begin)
        drag.connect("drag-update", self._pick_update)
        drag.connect("drag-end", self._pick_end)
        self.over.add_controller(drag)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._copy_picks)
        self.over.add_controller(keys)
        self.clamp = Adw.Clamp(maximum_size=self.WIDTH, child=self.over)  # (Window.apply_full_width)
        self.scroll = widgets.markdown_scroller(self.clamp)  # (Baude's chat's: a code block its own height)
        self.selection = widgets.ChatSelection(self.box, self.scroll, self.over, on_selected=self._took_across,
                                                on_own=self._took_selection)
        self.selection.menu_at = self._passage_menu
        self.place = ZoomPlace(self.scroll, self.box, self.box)  # (through the zoom: Window.zoom_tab)
        self.stack.add_named(self.scroll, "chat")
        self.append(self.stack)
        self.follow = livefeed.FollowEnd(self.scroll)  # (the newest in view, once it's read to, as in Baude's chat)
        self.follow.attach()
        self.tail = livefeed.Tail(lambda lines: self.as_mine(lambda: self._read(lines)), self._rewritten)
        self.space = None  # (the names its replies are linked with: show)
        self.chat, self.names, self.base_dir = None, None, None  # (its chat, and its names: show)
        self.nouns_wait = None
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self._relink_marked())  # (the theme's red)

    def show(self, chat, folders=()):
        """chat (its transcript: {path, title, cwd}) read, else nothing; its replies' names are folders' (the
        notebook's and the chat's: search_spaces). The same chat again (another notebook's open): read on from where
        it is, its names the new folders', its nouns marked again (the notebook's notes are what's known)."""
        if self.waiting is not None:
            GLib.source_remove(self.waiting)
        space = self.space = search_spaces(list(folders))
        # (the replies' names are links as the folders have them, each with its logo; a relative path is the chat's:
        # this chat's, as each chat open has its own, put in place as it's shown or drawn, activate)
        self.base_dir = (chat or {}).get("cwd") or (folders[0] if folders else os.getcwd())
        self.chat, self.names = chat, ReaderNames(space, self.base_dir)
        self.activate()
        self.waiting = space.refresh(self._catalogued)
        if chat and chat["path"] == self.path:
            if self.waiting is None:  # (its names in already: linked again now)
                self._catalogued()
            self.mark_nouns_soon()
            return
        self.path = chat["path"] if chat else None
        self.items = []
        while (c := self.box.get_first_child()) is not None:
            self.box.remove(c)
        self.rows, self.picks, self.picking = [], [], None
        self.citing, self.cited = None, []
        self.stack.set_visible_child_name("chat" if self.path else "empty")
        self.tail.stop()
        if self.path:
            self.follow.stick = True
            self.tail.start(self.path, 0)
            self.tail.read(new=False)  # (and its nouns marked: mark_nouns_soon)

    def mark_nouns_soon(self):
        """In a moment (the chat may still be coming in): the "the X"s its replies take as known, that nothing in it
        nor in the notes introduced, marked in red (nouns.py)."""
        if self.nouns_wait is not None:
            GLib.source_remove(self.nouns_wait)
        self.nouns_wait = GLib.timeout_add(1500, self._mark_nouns)

    def _mark_nouns(self):
        self.nouns_wait = None
        if self.path and self.items:
            texts = [{"id": widgets.message_key(it["text"]) if it["role"] == "assistant" else f"you {i}",
                      "text": it["text"], "mark": it["role"] == "assistant"} for i, it in enumerate(self.items)]
            known = [without_to_learn(p) for p in ((self.win.current or {}).get("pages") or {}).values()]
            path = self.path
            UNEXPLAINED.ask({"texts": texts, "known": known}, lambda flagged: self._marked(path, flagged))
        return False

    def _marked(self, path, flagged):
        """nouns.py's answer: each reply's phrases red, the replies whose phrases changed drawn again."""
        if path != self.path or self.names is None:
            return
        said = {key: re.compile(r"(?<![\w-])(?:" + "|".join(map(re.escape, sorted(ps, key=len, reverse=True)))
                                + r")(?![\w-])") for key, ps in flagged.items()}
        before, self.names.said = self.names.said, said

        def relink():
            for row in self.rows:
                if row.item["role"] == "assistant":
                    key = widgets.message_key(row.item["text"])
                    if getattr(before.get(key), "pattern", None) != getattr(said.get(key), "pattern", None):
                        row.prose.relink()
        self.as_mine(relink)
        self._cite_again()

    def _relink_marked(self):
        if self.names is not None and self.names.said:
            self.as_mine(lambda: [row.prose.relink() for row in self.rows if row.item["role"] == "assistant" and
                                  widgets.message_key(row.item["text"]) in self.names.said])

    def _catalogued(self):
        """The folder's names are in: the replies shown, linked again (each with its logo)."""
        self.waiting = None
        if self.names is not None:
            self.names.update()
        self.as_mine(lambda: [row.prose.relink() for row in self.rows if row.item["role"] == "assistant"])
        self._cite_again()

    def github_changed(self):
        """GitHub's lists have changed, or where its pull requests stand: what can look different is a reply with a #
        in it, or a pull request's link."""
        if self.names is not None:
            self.names.update()
        self.as_mine(lambda: [row.prose.relink() for row in self.rows if row.item["role"] == "assistant" and
                              ("#" in row.item["text"] or "/pull/" in row.item["text"])])
        self._cite_again()

    def activate(self):
        """Its chat's names the ones replies are linked with (widgets.SIGNAL and MarkdownView.base_dir are
        everyone's: each chat open has its own, put in place as its tab's shown, and as it draws)."""
        if self.names is not None:
            widgets.SIGNAL, widgets.MarkdownView.base_dir = self.names, self.base_dir

    def as_mine(self, fn):
        """fn() with this chat's names in place (activate); the shown chat's again after, if this isn't it."""
        was = widgets.SIGNAL, widgets.MarkdownView.base_dir
        self.activate()
        try:
            return fn()
        finally:
            if self.win.reader is not self:
                widgets.SIGNAL, widgets.MarkdownView.base_dir = was

    def stop(self):
        """Its tab closed (or the window going): its reading and its timers stopped (each holds it)."""
        self.place.stop()
        self.tail.stop()
        self.follow.detach()
        for attr in ("waiting", "nouns_wait"):
            if getattr(self, attr) is not None:
                GLib.source_remove(getattr(self, attr))
                setattr(self, attr, None)

    def _rewritten(self):
        """The chat's file got shorter (rewritten): read again from its start (livefeed.Tail)."""
        self.items = []
        while (c := self.box.get_first_child()) is not None:
            self.box.remove(c)
        self.rows = []

    def _read(self, lines):
        """What's been added to the chat (livefeed.Tail, the moment it's written): its prose drawn, a reply written in
        several records the one it's part of, grown."""
        n = len(self.items)
        parse_records(lines, self.items)
        grew = n and self.items[n - 1].pop("grew", False)
        if grew:
            self.rows[n - 1].prose.set_text(self.items[n - 1]["text"])
        for it in self.items[n:]:
            it.pop("grew", None)
            self._add(it)
        if grew or len(self.items) > n:
            self.mark_nouns_soon()

    def _add(self, it):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        if it["role"] == "note":  # (a background task's ending: one quiet line, as Baude's chat has it)
            prose = Gtk.Label(xalign=0, wrap=True, selectable=True)
            prose.set_markup(it["markup"])
            prose.add_css_class("error" if it["failed"] else "dim-label")
            prose.set_tooltip_text(it.get("summary") or None)
            row.add_css_class("reader-note")
            row.append(prose)
            row.prose, row.item = prose, it
            self.rows.append(row)
            self.box.append(row)
            return
        if it["role"] == "user":
            # (Baude's markdown, as a reply's and as Baude's chat draws what you wrote)
            prose = widgets.MarkdownView(it["text"] if len(it["text"]) < 1200 else it["text"][:1200] + "…")
            prose.add_css_class("prose")
            row.add_css_class("reader-user")
            row.set_halign(Gtk.Align.END)
        else:  # (Baude's markdown: tables, code with its copy button, images; its names links, with their logos)
            prose = widgets.MarkdownView(it["text"])
            prose.add_css_class("prose")
            row.add_css_class("reader-assistant")
            on_name_hover(prose, self.win)
        click = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("released", lambda *_: GLib.idle_add(self._took_selection, prose))
        prose.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-released", lambda *_: GLib.idle_add(self._took_selection, prose))
        prose.add_controller(keys)
        row.append(prose)
        row.prose, row.item = prose, it
        self.rows.append(row)
        self.box.append(row)

    def _labels(self):
        for row in self.rows:
            for w in widgets.walk(row.prose):
                if isinstance(w, Gtk.Label) and w.get_selectable():
                    yield w

    def _took_selection(self, prose):
        """A message's selection (a label's, or one of a reply's: MarkdownView), made without Ctrl: the one passage
        picked, and quoted. (The same one again, a key let go: left as it is, those picked with it too. One across
        messages' labels isn't a label's: _took_across.)"""
        if self.selection.shown:
            return False
        for w in widgets.walk(prose):
            if isinstance(w, Gtk.Label):
                ok, start, end = w.get_selection_bounds()
                if ok and end > start and len(w.get_text()[start:end].strip()) > 2:
                    if not any(p["label"] is w and (p["start"], p["end"]) == (start, end) for p in self.picks):
                        self.picks = [self._pick(w, start, end)]
                        self._picked()
                    break
        return False

    def _took_across(self, text):
        """A selection across labels (widgets.ChatSelection): the one passage picked, whole, and quoted."""
        parts = [self._pick(*part) for part in self.selection.parts()]
        if parts:
            self.picks = [{**parts[0], "text": text, "parts": parts}]
            self._picked()

    @staticmethod
    def _pick(label, start, end):
        return {"label": label, "start": start, "end": end, "text": label.get_text()[start:end]}

    def _there(self, p):
        """A passage picked still as it was: its label in the chat, its text the same (a reply still coming in is
        laid out afresh: what was picked in it is gone)."""
        if p.get("parts"):
            return all(self._there(q) for q in p["parts"])
        label = p["label"]
        return label.is_ancestor(self.box) and label.get_text()[p["start"]:p["end"]] == p["text"]

    @staticmethod
    def _meets(p, label, start, end):
        """Is a passage picked over those characters of label (each of its parts, one across labels)?"""
        return any(q["label"] is label and q["start"] < end and start < q["end"] for q in p.get("parts") or [p])

    def shown_picks(self):
        picks = [p for p in self.picks if self._there(p)]
        if (p := self.picking) is not None and p["start"] != p["end"]:
            a, b = sorted((p["start"], p["end"]))
            picks.append(self._pick(p["label"], a, b))
        return picks

    def _picked(self):
        """The passages picked, quoted to the Record (numbered, more than one) and drawn."""
        self.picks = [p for p in self.picks if self._there(p)]
        if self.picks:
            self.win.chat.set_passages([p["text"] for p in self.picks])
        self.marks.queue_draw()

    def _laid_out(self, _over, child, _alloc):
        if child is self.marks:  # (the chat laid out again: its passages elsewhere, perhaps)
            self.marks.queue_draw()
        return False

    def _label_at(self, x, y):
        w = self.over.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w is not self.over:
            if isinstance(w, Gtk.Label):
                return w if w.get_selectable() and w.is_ancestor(self.box) else None
            w = w.get_parent()
        return None

    def _char(self, label, x, y):
        ok, p = self.over.compute_point(label, Graphene.Point().init(x, y))
        return widgets.char_at(label, p.x, p.y) if ok else 0

    def _pick_begin(self, gesture, x, y):
        """A press on the chat. With Ctrl (or ⌘): another passage being picked, where it is (the press the Reader's
        alone: on macOS a label takes Ctrl and a click for its menu). Without: every passage picked let go, and every
        message's selection but the one pressed on (that one's GTK's), and the press the chat's as ever."""
        button = gesture.get_current_button()
        adding = gesture.get_current_event_state() & self.PICKING
        label = self._label_at(x, y)
        if button not in (Gdk.BUTTON_PRIMARY, Gdk.BUTTON_SECONDARY) or not adding:
            if button == Gdk.BUTTON_PRIMARY:
                for w in self._labels():
                    if w is not label and w.get_selection_bounds()[0]:
                        w.select_region(0, 0)
                if self.picks or self.cited:
                    self.picks, self.citing, self.cited = [], None, []
                    self.marks.queue_draw()
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if label is not None:
            at = self._char(label, x, y)
            self.picking = {"label": label, "start": at, "end": at}

    def _pick_update(self, gesture, dx, dy):
        if self.picking is None:
            return
        _ok, x, y = gesture.get_start_point()
        self.picking["end"] = self._char(self.picking["label"], x + dx, y + dy)
        self.marks.queue_draw()

    def _pick_end(self, _gesture, _dx, _dy):
        """Ctrl's drag let go: the passage it went over picked (one it went over again, or all but, taken back
        first); a click on one without a drag lets that one go."""
        p, self.picking = self.picking, None
        if p is None:
            return
        label, (a, b) = p["label"], sorted((p["start"], p["end"]))
        if b - a and label.get_text()[a:b].strip():
            self.picks = [q for q in self.picks if not self._meets(q, label, a, b)]
            self.picks.append(self._pick(label, a, b))
        else:
            self.picks = [q for q in self.picks if not self._meets(q, label, a, a + 1)]
        if not (len(self.picks) == 1 and self.picks[0].get("parts")):  # (the one across labels let go, or not alone)
            self.selection.forget()
        if len(self.picks) > 1:  # (all drawn alike, by Picks: numbered, each its passage)
            for w in self._labels():
                if w.get_selection_bounds()[0]:
                    w.select_region(0, 0)
        self._picked()

    def _copy_picks(self, _c, keyval, _code, state):
        """Copy (Ctrl or ⌘ and C), a passage picked: them all, a blank line between each (one picked with Ctrl has no
        selection of GTK's to copy). With Shift too: a citation link to each."""
        if keyval in (Gdk.KEY_c, Gdk.KEY_C) and state & self.PICKING and state & Gdk.ModifierType.SHIFT_MASK:
            self.copy_citations()
            return True
        if self.picks and keyval in (Gdk.KEY_c, Gdk.KEY_C) and state & self.PICKING:
            self.copy_passages()
            return True
        return False

    def tab_link(self):
        """The chat's transcript, for an agent to read (MainTabs.copy_link)."""
        return self.path

    def copy_passages(self):
        self.get_clipboard().set("\n\n".join(p["text"].strip() for p in self.picks if self._there(p)))

    def _passage_menu(self, label, at):
        """What a right click on a passage picked offers (widgets.ChatSelection); elsewhere, the label's own menu."""
        if not any(self._there(p) and self._meets(p, label, at - 1, at + 1) for p in self.picks):
            return None
        return [("Copy", self.copy_passages), ("Copy citation link", self.copy_citations)]

    def citation(self, p):
        """A passage picked as a link to it, to put in the notes (citation_link)."""
        parts = p.get("parts") or [p]
        first = parts[0]
        row = next((r for r in self.rows if first["label"].is_ancestor(r)), None)
        return citation_link(os.path.splitext(os.path.basename(self.path))[0], row.item.get("id") if row else None,
                             plain_words(first["label"].get_text()[:first["start"]]),
                             plain_words(" ".join(q["text"] for q in parts)))

    def copy_citations(self):
        links = [self.citation(p) for p in self.picks if self._there(p)]
        if links:
            self.get_clipboard().set("\n".join(links))
            self.win.toast("Citation link copied" if len(links) == 1 else f"{len(links)} citation links copied")

    def find_passage(self, cite):
        """A citation's passage in this chat: its parts, as picks' are (from its message on; if that's gone, anywhere).
        None if its words aren't here any more."""
        at = next((i for i, row in enumerate(self.rows) if cite["message"] and row.item.get("id") == cite["message"]),
                  None)
        for rows in ([self.rows[at:]] if at else []) + [self.rows]:
            labels = [w for row in rows for w in widgets.walk(row.prose) if isinstance(w, Gtk.Label) and
                      w.get_selectable()]
            if (found := passage_in([w.get_text() for w in labels], cite)):
                return [self._pick(labels[n], a, b) for n, a, b in found]
        return None

    def show_passage(self, cite):
        """A citation followed to this chat: the view at its passage, shaded till the next click."""
        self.citing, self.cited, self.cite_tries = cite, [], 0
        GLib.timeout_add(30, self._to_cited)

    def _to_cited(self):
        """Once the chat's laid out, a few frames in a long one: the view at the passage, a code block's own too, and
        held there as it settles (widgets.hold_place). Going to it, it's no longer following the chat's end."""
        if self.citing is None:
            return False
        if self.place.job is not None:  # (a zoom bringing messages back: what's far off isn't anywhere yet)
            return True
        if (found := self.find_passage(self.citing)) is None:
            self.citing = None
            self.win.toast("That passage isn't in the chat any more")
            return False
        first = found[0]
        inner = widgets.inner_scroller(first["label"], self.box)
        ok, at = (inner or first["label"]).compute_point(self.clamp, Graphene.Point().init(0, 0))
        adj = self.scroll.get_vadjustment()
        self.cite_tries += 1
        if (not ok or not first["label"].get_mapped() or at.y + 1 > adj.get_upper()) and self.cite_tries < 60:
            return True
        self.cited = found
        if inner is not None and (rects := widgets.passage_rects(first["label"], first["start"], first["end"], inner)):
            for inner_adj, place, size in ((inner.get_vadjustment(), rects[0].get_y(), inner.get_height()),
                                           (inner.get_hadjustment(), rects[0].get_x(), inner.get_width())):
                if not 0 <= place < size:
                    inner_adj.set_value(inner_adj.get_value() + place - size / 3)
        self.follow.stop_glide()
        self.follow.stick = False
        widgets.hold_place(self.scroll, {"value": max(0, at.y - adj.get_page_size() / 3), "at_end": False}, 1.5)
        self.marks.queue_draw()
        return False

    def _cite_again(self):
        """Labels drawn again (the names come in, a moment after a chat opens): the passage found again in them."""
        if self.citing is not None and self.cited:
            self.cited = self.find_passage(self.citing) or []
            self.marks.queue_draw()


class RefCompleter:
    """@'s list over the Record's box: what @ lists in the notes (Window.space: the notebooks and their pages, the
    folders' names, the chats), as the notes' @ looks and goes (DiaryEditor._token_changed) and as Baude's / is over its
    composer (SlashCompleter). An @ typed at a word's start starts it; what's typed after it is its search, spaces and
    all, till Enter or Tab takes what's picked (as @name, or @{a name} with spaces), Esc leaves it, a new line, the caret
    back before the @, or words that find nothing. With nothing typed yet, the notebook's own pages come first."""

    def __init__(self, win, view, anchor):
        self.win, self.view, self.anchor = win, view, anchor
        self.picks = widgets.PickList(anchor, self._bind, self.accept, position=Gtk.PositionType.TOP)
        self.pop = self.picks.pop
        self.pop.set_halign(Gtk.Align.START)  # (its left side on the @'s: PickList.over)
        self.pop.set_offset(0, -10)
        self.shown, self.query, self.moved, self.waiting, self.at = [], None, False, None, None
        buf = view.get_buffer()
        # (the @names it knows, as the notes colour theirs, and the search being typed: after the box's own markdown)
        buf.create_tag("record-ref", foreground="#2f8f72", weight=600, background="rgba(79,138,124,0.14)")
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
        space, q = self.win.space(), word[1]
        if space is None:
            return self.forget()
        shown, matched = space.search(q)
        if not q.strip():
            shown = notes_first(shown)
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
        text, space = buf.get_text(start, end, True), self.win.space()
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


class SaidLabel(widgets.LinkLabel):
    """What you wrote to the Record (RecordChat._said): a LinkLabel whose logos are in the @names' colour, as a page
    draws them (NotesView.COLOR), not the text's. (LinkLabel draws them in get_color()'s; the text's own is GTK's.)"""

    def get_color(self):
        return NotesView.COLOR


class RecordChat(Gtk.Box):
    """Your conversation with the Record, the notebook's agent (Agent): a passage picked in a chat read, or selected on
    a page, quoted with what's asked; @ in the box lists what's to reference (RefCompleter), and what's referenced goes
    with the message (ref_said); a change it proposes to a page is said under its reply, and shown on the page."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("record-column")  # (the frame's: widgets.CSS)
        self.win, self.notebook, self.ask, self.agent, self.quote, self.selection = win, None, None, None, "", None
        self.live_widget, self.asking = None, []  # (the reply coming, and the reads it's asking to make: cards)
        # (no name here: the notebooks' list says which it is, and the window's title)
        head = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_end=8, margin_top=10, margin_bottom=6)
        clear = Gtk.Button(icon_name="edit-clear-all-symbolic", tooltip_text="Clear conversation")
        clear.add_css_class("flat")
        clear.connect("clicked", lambda *_: self._clear())
        head.append(clear)
        self.append(head)  # (no rule under it: the frame has none)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                           margin_start=12, margin_end=12)
        self.scroll = widgets.markdown_scroller(self.box, vexpand=True)  # (as Baude's chat: widgets)
        self.place = ZoomPlace(self.scroll, self.box, self.box)  # (through the zoom: Window.apply_zoom)
        # the conversation and the box asked in, set into the column as a chat is into Baude's window, and a subagent's
        # into its side panel (the inside: widgets.inside), Clear above it on the frame. No gap of its own on the
        # page's side: the page's is the one between them, as thin as the window's edges are
        talk = widgets.inside(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), start=False)
        talk.append(self.scroll)
        self.append(talk)
        # composer
        comp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=10, margin_end=10, margin_bottom=10)
        self.pending_box = self._pending_bar()
        comp.append(self.pending_box)
        self.quote_box = Gtk.Box(spacing=6)
        self.quote_box.add_css_class("quote-chip")
        self.quote_label = label("", css=("caption",))
        self.quote_label.set_hexpand(True)
        self.quote_label.set_lines(3)
        self.quote_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.quote_box.append(self.quote_label)
        x = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.START, tooltip_text="Drop the passage")
        x.add_css_class("flat")
        x.add_css_class("circular")
        x.connect("clicked", lambda *_: self.set_quote(""))
        self.quote_box.append(x)
        self.quote_box.set_visible(False)
        comp.append(self.quote_box)
        # the box asked in: Baude's (widgets.Composer), its markdown shown as it's written, its button red to stop
        # (no words laid over it: a label over a text view sat 2px off its caret, and wrapped below its one line at a
        # bigger Text Scale)
        self.writing = widgets.Composer()  # (its button's tip says the model: model_changed)
        self.composer, self.send = self.writing.input, self.writing.send
        self.refs = RefCompleter(win, self.composer, self.writing)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.composer.add_controller(keys)
        self.send.connect("clicked", lambda *_: self.ask.stop() if self.ask else self.submit())
        comp.append(self.writing)
        talk.append(comp)
        self.model_changed()

    def show(self, notebook):
        self.close_agent()
        self.notebook = notebook
        self.set_quote("")
        self.render()

    def close_agent(self):
        """Its session stopped (another notebook shown, the conversation cleared, the window gone): the next message
        starts another, told what was said."""
        if self.agent is not None:
            self.agent.close()
        self.agent = self.ask = None

    def agent_gone(self, agent):
        if agent is self.agent:
            self.agent = self.ask = None
            self.writing.set_busy(False)
            self._drop_asking()

    def model_changed(self):
        """Settings → Intelligence: the send button says the model it asks; the next message is the new model's
        (submit)."""
        model = dict(AGENT_MODELS).get(self.win.store.agent_model, self.win.store.agent_model)
        self.writing.send_tip = f"Ask {model} (Enter)\nNew line: Shift+Enter\n@: a page, a notebook, a file…"
        if self.ask is None:
            self.send.set_tooltip_text(self.writing.send_tip)

    def set_quote(self, text):
        self.quote, self.selection = text[:QUOTE_LIMIT], None
        self.quote_label.set_text("“" + " ".join(text.split()) + "”" if text else "")
        self.quote_box.set_visible(bool(text))

    def set_passages(self, texts):
        """The passages picked in the chat read (Reader.picks), quoted: more than one, each with its number, as the
        chat shows it ([1] …, a blank line between each)."""
        texts = [t.strip() for t in texts if t.strip()]
        if len(texts) < 2:
            return self.set_quote(texts[0] if texts else "")
        self.set_quote("\n\n".join(f"[{n}] {t}" for n, t in enumerate(texts, 1)))
        self.quote_label.set_text("  ".join(f"{n} “{' '.join(t.split())}”" for n, t in enumerate(texts, 1)))

    def set_selection(self, notebook, page, start, end, text):
        """A passage selected on a page of notes (DiaryEditor._quote_selection), quoted with where it is: what's asked
        is about it, and a change the agent proposes to it replaces it (propose)."""
        self.set_quote(text)
        self.selection = {"notebook": notebook.get("id"), "page": page, "start": start, "end": end, "text": text}
        self.quote_label.set_text(f"{short_page(page)} · “{' '.join(text.split())}”")

    def _key(self, _c, keyval, _code, state):
        """Enter asks (Ctrl+Enter too); Shift+Enter, or Enter in a code block, is a new line (a list's next item,
        a code block's closing fence put in), as in Baude; a selection and * ` or / is made bold, code or italic;
        @'s list, while it's up, has the arrows, Enter, Tab and Esc (RefCompleter); Esc stops the reply."""
        if self.refs.handle_key(keyval, state):
            return True
        if widgets.leave_wrapped(self.composer.get_buffer(), keyval, state):
            return True  # (just formatted, and Left or Right: outside its marks)
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
                return markdown.newline()  # (False: the text view puts in a plain new line)
            self.submit()
            return True
        if keyval == Gdk.KEY_Escape and self.ask:
            self.ask.stop()
            return True
        return False

    def _clear(self):
        self.close_agent()
        self.notebook["messages"] = []
        self.win.store.save_now()
        self.render()
        for child in self.win.tabs.children():  # (and what it proposed, from the pages open)
            if isinstance(child, DiaryEditor) and child.notebook is self.notebook:
                child.show_pending()

    def render(self):
        while (c := self.box.get_first_child()) is not None:
            self.box.remove(c)
        for m in self.notebook["messages"] if self.notebook else ():
            self.box.append(self._bubble(m))
        if self.ask is not None and self.live_widget is not None:  # (the reply coming, and what it's asking: kept)
            self.box.append(self.live_widget)
            for card in self.asking:
                self.box.append(card)
        self.show_pending_bar()
        GLib.timeout_add(60, self._to_bottom)

    def _to_bottom(self):
        if getattr(self.scroll, "held", False):
            return False  # (put back where it was read to: widgets.hold_place)
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_upper())
        return False

    def _bubble(self, m):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if m["role"] == "user":
            b.add_css_class("bubble-user")
            outer.set_margin_start(28)
            if m.get("quote"):
                q = m["quote"]
                where = f"{short_page(m['selection']['page'])} · " if m.get("selection") else ""
                b.append(label(where + "“" + (" ".join(q.split())[:400] + ("…" if len(q) > 400 else "")) + "”",
                               css=("quote-in-bubble",), select=True))
            b.append(self._said(m))
        elif (m.get("proposals") or m.get("revised") or m.get("withdrew")) and not m["text"] and not m.get("error"):
            edited = list(dict.fromkeys([short_page(p["page"]) for p in m.get("proposals") or ()] +
                                        [short_page(page) for page in m.get("revised") or ()]))  # (as it was then)
            gone = [short_page(page) for page in m.get("withdrew") or () if short_page(page) not in edited]
            said = ["Edited " + ", ".join(edited)] if edited else []
            said += ["Took back an edit to " + ", ".join(gone)] if gone else []
            b.append(label(" · ".join(said), css=("dim-label", "caption")))  # (it only edited)
            b.set_margin_start(12)
            b.prose = b.status = label("")
        else:
            b.add_css_class("bubble-error" if m.get("error") else "bubble-record")
            outer.set_margin_end(28)
            prose = label(select=True)
            set_md(prose, m["text"] or ("…" if not m.get("proposals") else ""), self.win.names_in(m["text"] or ""))
            prose.connect("activate-link", lambda _l, uri: self.win.follow(uri, from_chat=True))
            on_name_hover(prose, self.win)
            prose.set_visible(bool(m["text"]) or not m.get("proposals"))
            b.append(prose)
            b.prose = prose
            b.status = label("", css=("dim-label", "caption"))
            b.status.set_visible(False)
            b.append(b.status)
            if m.get("unknown"):
                b.append(self._unknown_chips(m))
        outer.append(b)
        outer.bubble = b
        return outer

    def _said(self, m):
        """What you wrote, its @references in it as a page draws an @name: the logo where its @ is (LinkLabel draws
        it), the name in the @names' colour on their patch, a pull request or an issue by its number alone, as the page
        shows one (ref_text); clicked, opened as one on a page is."""
        refs, text = {r["surface"]: r for r in m.get("refs") or ()}, plain_refs(m["text"])
        out, at = [], 0
        for found in REF_RE.finditer(text):
            r = refs.get(found.group(1) or found.group(2))
            if r is None:
                continue
            github = lookup.GITHUB_NAME.fullmatch(r["surface"]) if (r.get("icon") or "").startswith(("pr_", "issue")) \
                else None  # (by its logo: a branch may be called feature/login#12)
            name = html.escape(github[2] if github else r["surface"], False)
            out += [html.escape(text[at:found.start()], False),
                    f'<a href="ref:{urllib.parse.quote(r["surface"], safe="")}"><span foreground="#2f8f72" '
                    f'background="#4f8a7c" bgalpha="14%" weight="600" underline="none">'
                    f'{widgets.ref_icon_slot(r.get("icon")) or "@"}{name}</span></a>']
            at = found.end()
        out.append(html.escape(text[at:], False))
        said = SaidLabel(xalign=0.0, wrap=True, selectable=True)
        said.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        said.add_css_class("said-refs")
        said.set_markup("".join(out))
        said.connect("activate-link", lambda _l, uri: self.win.follow(uri, from_chat=True))
        on_name_hover(said, self.win)
        return said

    def pending(self, notebook):
        return pending_edits(notebook, self.agent)

    def _pending_bar(self):
        """Over the box asked in, while there are edits waiting: how many, and Previous and Next, each to one on its
        page, the caret in it (Tab takes it, Esc leaves it, as its Accept and Reject do)."""
        bar = Gtk.Box(spacing=2)
        bar.add_css_class("pending-bar")
        self.pending_label = label("", css=("caption",))
        self.pending_label.set_hexpand(True)
        bar.append(self.pending_label)
        for icon, tip, by in (("go-up-symbolic", "Previous edit", -1), ("go-down-symbolic", "Next edit", 1)):
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER)
            btn.add_css_class("flat")
            btn.add_css_class("circular")
            btn.connect("clicked", lambda *_, by=by: self.next_edit(by))
            bar.append(btn)
        bar.set_visible(False)
        self.at_edit = None  # (the edit Previous and Next last went to: its id)
        return bar

    def show_pending_bar(self):
        n = len(self.pending(self.notebook)) if self.notebook is not None else 0
        self.pending_label.set_text(f"{n} edit{'s' if n != 1 else ''} pending")
        self.pending_box.set_visible(n > 0)

    def next_edit(self, by):
        """Previous (by -1) or Next (1): the edit before or after the one last gone to (the first, or the last), shown
        on its page, scrolled to, the caret in it."""
        waiting = self.pending(self.notebook) if self.notebook is not None else []
        if not waiting:
            return self.show_pending_bar()
        ids = [p["id"] for p in waiting]
        at = (ids.index(self.at_edit) + by) % len(ids) if self.at_edit in ids else (0 if by > 0 else len(ids) - 1)
        p = waiting[at]
        self.at_edit = p["id"]
        if p["page"] in self.notebook["pages"] and (editor := self.win.open_note(p["page"])) is not None:
            editor.show_proposal(p, scroll=True, caret=True)
            editor.view.grab_focus()

    def decided(self, p):
        """A proposed edit accepted or rejected on its page: kept so, and told the agent with the next message."""
        self.win.store.save()
        self.show_pending_bar()

    def _unknown_chips(self, m):
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6, row_spacing=4,
                           column_spacing=4)
        flow.append(label("Not in your notes:", css=("dim-label", "caption"), wrap=False))
        for t in m["unknown"]:
            have = diary_mentions(self.notebook, t)
            btn = Gtk.Button(label=("✓ " if have else "+ ") + t, tooltip_text=f"Add to {TO_LEARN}")
            btn.add_css_class("unknown-chip")
            btn.set_sensitive(not have)
            btn.connect("clicked", lambda bt, t=t: (self.win.add_unknown(t), bt.set_label("✓ " + t), bt.set_sensitive(False)))
            flow.append(btn)
        return flow

    def _selection_now(self, notebook):
        """The passage selected on a page, as it is now (where it was, or else where the page has it nearest), or None
        if it's not on its page any more."""
        sel = self.selection
        if not sel or sel["notebook"] != notebook.get("id") or sel["page"] not in notebook["pages"]:
            return None
        text = notebook["pages"][sel["page"]]
        if text[sel["start"]:sel["end"]] != sel["text"]:
            start, found = place_change(text, sel["text"], sel["start"])
            if start is None:
                return None
            sel = {**sel, "start": start, "end": start + len(found), "text": found}
        return {k: sel[k] for k in ("page", "start", "end", "text")}

    def message_for(self, agent, msg, space, chat_space):
        """What's sent for a message: a new session's notebook and what was said before it, what's become of the
        changes proposed, what's referenced (ref_said, of space: Window.space's), the passage quoted from a chat (its
        names, chat_space's) or selected on a page, and what was written."""
        notebook, store, parts = agent.notebook, self.win.store, []
        if agent.fresh:
            parts.append(notebook_said(notebook, store, space))
            if earlier := earlier_said(notebook["messages"][:-1]):
                parts.append(earlier)  # (what was decided too)
        for m in notebook["messages"]:
            for p in m.get("proposals") or ():
                if p.get("status") in ("accepted", "rejected") and not p.get("told"):
                    if not agent.fresh:
                        parts.append(f"(The change you proposed to {p['page']} was {p['status']} by the user.)")
                    p["told"] = True
        if waiting := pending_edits(notebook):
            pages = list(dict.fromkeys(p["page"] for p in waiting))
            parts.append(f"(Your edits still waiting for the user: {len(waiting)}, on {', '.join(pages)}. list_edits "
                         "shows them.)")
        left, said = REFS_LIMIT, []
        for item in referenced(msg["text"], space):
            text, paths = ref_said(item, store, space, min(REF_LIMIT, max(2000, left)))
            left -= len(text)
            agent.allowed.update(os.path.realpath(p) for p in paths if p)
            said.append(text)
        if said:
            parts.append("<referenced>\n" + "\n".join(said) + "\n</referenced>")
        if msg.get("selection"):
            parts.append(selection_said(msg["selection"], notebook, store))
        elif msg.get("quote"):
            parts.append(passage_said(msg["quote"], chat_space))
        parts.append(msg["text"])
        return "\n\n".join(p.strip("\n") for p in parts if p)

    def submit(self):
        buf = self.composer.get_buffer()
        text = self.writing.text().strip()  # (its bullets back to -)
        if self.ask or not self.notebook or not (text or self.quote):
            return
        notebook, store = self.notebook, self.win.store
        store.flush()  # (the pages' files as they are: what it reads)
        space = self.win.space()
        msg = {"role": "user", "text": text or "What does this mean?", "t": time.time()}
        if self.quote:
            msg["quote"] = self.quote
            if self.selection is not None and self.selection["notebook"] == notebook.get("id"):
                msg["selection"] = self._selection_now(notebook) or \
                    {"page": self.selection["page"], "start": None, "end": None, "text": self.selection["text"]}
        refs = referenced(msg["text"], space)
        if refs:
            msg["refs"] = [{"surface": it["surface"], "what": what_it_is(it, space), "icon": it.get("icon")}
                           for it in refs]
        notebook["messages"].append(msg)
        buf.set_text("")
        self.set_quote("")
        folders = self.win.read_folders()  # (the notebook's, and the chat Read shows's)
        key = Agent.key_for(notebook, store.agent_model, folders)
        if self.agent is not None and self.agent.key != key:
            self.close_agent()  # (another model, other folders: a session for them, told what was said)
        try:
            if self.agent is None:
                self.agent = Agent(self, notebook, store.agent_model, folders)
            turn = {"text": [], "proposals": [], "selection": msg.get("selection"), "streamed": False}
            # (a passage from the chat being read: its names, as the chat links them)
            chat_space = self.win.reader.space if self.win.reader is not None else None
            self.agent.ask(self.message_for(self.agent, msg, space, chat_space or space), turn)
        except OSError as e:
            self.agent = None
            notebook["messages"].append({"role": "record", "text": f"Couldn't run claude ({e}). Set RECORD_CLAUDE to "
                                         "its path.", "error": True, "t": time.time()})
            store.save_now()
            return self.render()
        self.ask = self.agent
        self.live_widget = self._bubble({"role": "record", "text": "Reading…"})
        self.render()
        self.writing.set_busy(True)
        store.save_now()

    def _delta(self, agent, text):
        if agent is self.ask and self.live_widget is not None:
            reply, _ = split_unknown(text)
            reply = re.sub(r"\n?\s*\**Unknown:?\**[^\n]*$", "", reply) if "Unknown" in text[-200:] else reply
            set_md(self.live_widget.bubble.prose, reply or "…")
            GLib.idle_add(self._to_bottom)

    def _status(self, agent, said):
        """What the agent's doing (a page read, a search, a change proposed), under what it's said so far."""
        if agent is self.ask and self.live_widget is not None:
            status = self.live_widget.bubble.status
            status.set_text(said)
            status.set_visible(True)
            GLib.idle_add(self._to_bottom)

    def ask_permission(self, agent, rid, name, inp):
        """A read outside the notebook's pages and folders, not of anything referenced: the user's asked, as Baude asks
        in its Manual mode, Allow or Deny."""
        path = str(inp.get("file_path") or inp.get("path") or inp.get("pattern") or inp.get("glob") or "")
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("bubble-record")
        verb = {"Read": "read", "Grep": "search in", "Glob": "look for files in"}.get(name, name)
        card.append(label(f"{noun()} would like to {verb} {path.replace(os.path.expanduser('~'), '~', 1)}"))
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
        """The reads asked for, gone (deny: answered no, the turn stopped; else the turn's over)."""
        for card in list(self.asking):
            if deny:
                card.answer(False)
            elif card.get_parent() is not None:
                self.box.remove(card)
        self.asking = []

    TOOLS = ("propose_edit", "list_edits", "revise_edit", "withdraw_edit")

    def _tool(self, tool, args):
        """The agent's own tools (ToolSocket), on the main loop."""
        agent = self.agent
        if tool not in self.TOOLS:
            return {"error": f"There's no tool {tool}."}
        if agent is None or agent.turn is None or agent.notebook is not self.notebook:
            return {"error": "That notebook isn't open any more: nothing was done."}
        return self.run_tool(agent.turn, tool, args)

    def run_tool(self, turn, tool, args):
        """One of the agent's tools, in a turn of its (turn: what its reply's made of, the edits it made among it)."""
        return {"propose_edit": self.propose, "list_edits": self.list_edits, "revise_edit": self.revise_edit,
                "withdraw_edit": self.withdraw_edit}[tool](turn, args)

    def _page(self, args):
        """The page a tool's call names, or (None, the error to answer it with)."""
        notebook = self.notebook
        page = page_named(notebook, str(args.get("page") or ""), self.win.store)
        if page is None:
            names = ", ".join(sorted(notebook["pages"])[:40])
            return None, {"error": f"There's no page {args.get('page')!r} in this notebook. Its pages: {names}."}
        return page, None

    def _spans(self, page):
        """A page's own text, and the edits waiting on it, where each is in that text now: [(start, old, new, edit)]
        (one whose passage isn't on the page as it was: left out)."""
        notebook, editor = self.notebook, self.win.tabs.get(("note", page))
        text = editor.kept() if editor is not None else notebook["pages"].get(page, "")
        out = []
        for p in pending_edits(notebook, self.agent):
            if p["page"] != page:
                continue
            at = editor.edit_start(p) if editor is not None else None
            if at is None and not p["old"]:
                at = len(text)
            elif at is None:
                at = p.get("start", 0) if text[p.get("start", 0):p.get("start", 0) + len(p["old"])] == p["old"] else \
                    place_change(text, p["old"], p.get("start"))[0]
            if at is not None:
                out.append((at, p["old"], p["new"], p))
        return text, out

    def _edited(self, turn, p):
        """A turn's edit to a page, made or changed: kept with the turn (said under its reply), shown on its page."""
        if p["status"] != "pending":
            if p["page"] not in turn.setdefault("withdrew", []):
                turn["withdrew"].append(p["page"])
        elif p not in turn["proposals"] and p["page"] not in turn.setdefault("revised", []):
            turn["revised"].append(p["page"])
        self._status(self.agent, f"{'Edited' if p['status'] == 'pending' else 'Took back an edit to'} "
                                 f"{short_page(p['page'])}")
        if p["status"] == "pending" and (editor := self.win.open_note(p["page"])) is not None:
            editor.show_proposal(p, scroll=True)
        self.show_pending_bar()

    def _restage(self, turn, p, **fields):
        """A pending edit changed (old, new, start) or taken back (status): off its page, changed, and shown again."""
        if (editor := self.win.tabs.get(("note", p["page"]))) is not None:
            editor.unshow(p)
        p.update(fields)
        if p["status"] == "pending" and p["old"] == p["new"]:
            p["status"] = "withdrawn"  # (what it puts in is what's there: no edit)
        self._edited(turn, p)

    def propose(self, turn, args):
        """propose_edit: a change to a page of the notebook's, its old text found once (or where the selection was) in
        the page as it reads with the agent's pending edits applied (staged). Text only in such an edit's new text, or
        in what it replaces: that edit revised to take this in, one edit for the passage they all cover (the others in
        it taken back); else a new one. Shown on the page (DiaryEditor.show_proposal) to accept or reject: nothing's
        written here."""
        page, error = self._page(args)
        if error:
            return error
        old, new = str(args.get("old_text") or ""), str(args.get("new_text") or "")
        if old == new:
            return {"error": "new_text is old_text: that changes nothing."}
        text, spans = self._spans(page)
        sel = turn.get("selection")
        near = sel["start"] if sel and sel["page"] == page and sel.get("start") is not None else None
        view, pieces = staged(text, spans)
        if not old:
            if view and not view.endswith("\n") and not new.startswith("\n"):
                new = "\n" + new  # (added at the end, after what's there and what's waiting: on a line of its own)
            return self._new_edit(turn, page, len(text), "", new)
        at, found = place_change(view, old, staged_at(pieces, near) if near is not None else None)
        if at is None and any(want and want in view for want in (old, old.strip())):
            return {"error": found}  # (in the page as the edits have it, more than once)
        if at is None:  # (not in the page as the edits have it: text one of them replaces, perhaps)
            k, kept_found = place_change(text, old, near)
            if k is None:
                return {"error": found}
            end = k + len(kept_found)
            hit = [x for x in spans if x[0] < end and x[0] + len(x[1]) > k]
            if not hit:
                return self._new_edit(turn, page, k, kept_found, new)
            k0, k1 = min(k, hit[0][0]), max(end, max(x[0] + len(x[1]) for x in hit))
            return self._merged(turn, [x[3] for x in hit], k0, text[k0:k1], text[k0:k] + new + text[end:k1])
        end = at + len(found)
        hit = [piece for piece in pieces if piece[4] is not None and
               (piece[0] < end and piece[1] > at or piece[0] == piece[1] and at < piece[0] < end)]
        if not hit:  # (none of a pending edit's passage in it: one of its own)
            k0, k1 = kept_at(pieces, at, max), kept_at(pieces, end, min)
            return self._new_edit(turn, page, k0, text[k0:k1], new)
        s0, s1 = min(at, hit[0][0]), max(end, hit[-1][1])  # (all of theirs in it, and none of another's)
        k0 = hit[0][2] if s0 == hit[0][0] else kept_at(pieces, at, max)
        k1 = hit[-1][3] if s1 == hit[-1][1] else kept_at(pieces, end, min)
        return self._merged(turn, [piece[4] for piece in hit], k0, text[k0:k1], view[s0:at] + new + view[end:s1])

    def _new_edit(self, turn, page, start, old, new):
        p = {"id": uuid.uuid4().hex[:10], "page": page, "old": old, "new": new, "start": start, "status": "pending",
             "t": time.time()}
        turn["proposals"].append(p)
        self._edited(turn, p)
        return {"text": f"Edit {p['id']} shown on the page {page} for the user to accept or reject. Nothing is written "
                        "until they accept; you'll be told which."}

    def _merged(self, turn, edits, start, old, new):
        """Pending edits a change reaches into, as one: the first revised to replace old with new, the others taken
        back."""
        first = edits[0]
        for p in edits[1:]:
            self._restage(turn, p, status="withdrawn")
        self._restage(turn, first, old=old, new=new, start=start)
        if first["status"] == "withdrawn":
            return {"text": f"That puts back what's on the page: your edit {first['id']} is taken back."}
        joined = f" (and took in {', '.join(p['id'] for p in edits[1:])}, now taken back)" if edits[1:] else ""
        return {"text": f"Revised your pending edit {first['id']}{joined}: it replaces {old!r} with {new!r} now, still "
                        "waiting for the user."}

    def list_edits(self, turn, args):
        """list_edits: the agent's edits waiting for the user, page by page, each as the page has its passage now."""
        pages = sorted({p["page"] for p in pending_edits(self.notebook, self.agent)})
        if args.get("page"):
            page, error = self._page(args)
            if error:
                return error
            pages = [page] if page in pages else []
        listed = []
        for page in pages:
            spans = self._spans(page)[1]
            listed += [{"id": p["id"], "page": page, "old_text": old, "new_text": new}
                       for _at, old, new, p in sorted(spans, key=lambda x: x[0])]
            placed = {id(x[3]) for x in spans}
            listed += [{"id": p["id"], "page": page, "old_text": p["old"], "new_text": p["new"],
                        "note": "what it replaces isn't on the page any more: revise its old_text, or withdraw it"}
                       for p in pending_edits(self.notebook, self.agent) if p["page"] == page and id(p) not in placed]
        if not listed:
            return {"text": "No edits of yours are waiting" + (f" on {pages[0] if pages else args['page']}."
                                                               if args.get("page") else ".")}
        return {"text": json.dumps(listed, ensure_ascii=False, indent=1)}

    def _pending_named(self, args):
        p = next((p for p in pending_edits(self.notebook, self.agent) if p["id"] == str(args.get("id") or "")), None)
        return p, None if p is not None else {"error": f"No edit of yours with the id {args.get('id')!r} is waiting: "
                                                         "list_edits shows those that are."}

    def revise_edit(self, turn, args):
        """revise_edit: a pending edit's new text (and the passage it replaces, if given) changed; it stays one edit."""
        p, error = self._pending_named(args)
        if error:
            return error
        new, fields = str(args.get("new_text") or ""), {}
        if not p["old"] and not ("old_text" in args and args["old_text"]) and not new.startswith("\n"):
            text, spans = self._spans(p["page"])  # (an addition: after what's there and what's waiting before it)
            before = staged(text, [x for x in spans if x[3] is not p and (x[1] or x[3].get("t", 0) < p.get("t", 0))])[0]
            if before and not before.endswith("\n"):
                new = "\n" + new
        if "old_text" in args and str(args["old_text"]) != p["old"]:
            text, spans = self._spans(p["page"])
            near = next((x[0] for x in spans if x[3] is p), p.get("start"))
            at, old = place_change(text, str(args["old_text"]), near)
            if at is None:
                return {"error": old}
            if any(x[3] is not p and x[0] < at + len(old) and x[0] + len(x[1]) > at for x in spans):
                return {"error": "That's in the passage another of your pending edits replaces: revise that one."}
            fields = {"old": old, "start": at}
        self._restage(turn, p, new=new, **fields)
        if p["status"] == "withdrawn":
            return {"text": f"That puts back what's on the page: your edit {p['id']} is taken back."}
        return {"text": f"Edit {p['id']} revised, still waiting for the user."}

    def withdraw_edit(self, turn, args):
        """withdraw_edit: a pending edit taken back, from its page."""
        p, error = self._pending_named(args)
        if error:
            return error
        self._restage(turn, p, status="withdrawn")
        return {"text": f"Edit {p['id']} taken back."}

    def _done(self, agent, text, error, turn):
        notebook, props, revised = agent.notebook, turn.get("proposals") or [], turn.get("revised") or []
        withdrew = turn.get("withdrew") or []
        if agent is self.ask:
            self.ask, self.live_widget = None, None
            self.writing.set_busy(False)
            self._drop_asking()
        if error:
            m = {"role": "record", "text": error, "error": True, "t": time.time()}
        elif text.strip() or props or revised or withdrew:
            reply, unknown = split_unknown(text)
            m = {"role": "record", "text": reply, "unknown": unknown, "t": time.time()}
        else:
            m = None
        if m is not None:
            if props:
                m["proposals"] = props  # (the same ones the pages show: what's decided there is kept here)
            if revised:
                m["revised"] = revised  # (pages whose edits waiting it changed: said as edited)
            if withdrew:
                m["withdrew"] = withdrew  # (and those it took one back from)
            notebook["messages"].append(m)
        self.win.store.save_now()
        if notebook is self.notebook:
            self.render()
        return False


# ---------------------------------------------------------------- the window

class Settings(Adw.PreferencesDialog):
    """☰ → Settings… (⌘, on macOS, Ctrl+, elsewhere), as Baude's: Intelligence (the model the Record's agent runs on,
    Haiku unless another's chosen), Full-width text, Code Font, Accent Colour, Show in Applications, and the sizes, 1.0 the usual: the
    text's and the icons' (Baude's settings: the same in both, Code Font and Accent Colour too), and the zoom Ctrl/⌘ 0 goes back to;
    Export Notebook… and Export All…, each saying what its zip comes to; Send Feedback… and the feedback sent. Each
    takes at once (Code Font from the next start) and is kept."""

    def __init__(self, win):
        super().__init__(title="Settings")
        page = widgets.settings_page()
        group = Adw.PreferencesGroup()
        models = [value for value, _label in AGENT_MODELS]
        thinks = Adw.ComboRow(title="Intelligence", subtitle=f"The model {noun()}'s agent thinks with",
                              model=Gtk.StringList.new([label for _value, label in AGENT_MODELS]),
                              selected=models.index(win.store.agent_model) if win.store.agent_model in models else 0)
        thinks.connect("notify::selected", lambda r, _p: win.set_agent_model(models[r.get_selected()]))
        group.add(thinks)
        page.add(group)
        group = Adw.PreferencesGroup()
        group.add(widgets.settings_switch("Full-width text", None, "win.fullwidth"))
        modes = list(MARKUP_MODES)
        formatting = Adw.ComboRow(title="Markup visibility",
                                  model=Gtk.StringList.new(list(MARKUP_MODES.values())),
                                  selected=modes.index(win.store.markup))
        formatting.connect("notify::selected", lambda r, _p: win.set_markup(modes[r.get_selected()]))
        group.add(formatting)
        group.add(widgets.settings_code_font(win, "Record"))  # (Baude's too, and the same setting)
        group.add(widgets.settings_accent())  # (Baude's too, and the same setting)
        group.add(update.settings_row())  # (Baude's too: the one setting)
        self.version_row = update.version_row(self)  # (and the version, its changelog: Baude's too)
        group.add(self.version_row)
        group.add(widgets.settings_switch("Include glossary and chats in backup",
                                          backup_zip().replace(os.path.expanduser("~"), "~", 1),
                                          "win.backupall"))
        group.add(widgets.settings_switch(f"Show in {'Applications' if sys.platform == 'darwin' else 'application menu'}",
                                          launchers(APP_ID, "Record")[0].replace(os.path.expanduser("~"), "~", 1),
                                          "win.launcher"))
        page.add(group)
        sizes = Adw.PreferencesGroup(title="Size")  # (the text's and the icons' are Baude's too)
        sizes.add(widgets.settings_size("Text Scale", None, widgets.scale_setting("text_scale"),
                                        lambda s: win.set_scale("text_scale", s)))
        sizes.add(widgets.settings_size("Icon Scale", None, widgets.scale_setting("icon_scale"),
                                        lambda s: win.set_scale("icon_scale", s)))
        sizes.add(widgets.settings_size("Global Scale", None, win.store.zoom_default,  # (the zoom ⌘0 goes back to)
                                        win.set_zoom_default, widgets.ZOOM_MIN, widgets.ZOOM_MAX))
        page.add(sizes)

        def go(row, action):  # (this goes first: a capture is of the window, not of this over it)
            row.get_ancestor(Adw.Dialog).close()
            win.activate_action(action, None)
        exports = [(f"Export {book()}…", "win.export", [win.current])] if win.current else []
        exports.append((f"Export All {book(plural=True)}…", "win.exportall", win.store.notebooks))
        group = Adw.PreferencesGroup()
        self.export_rows = []  # (each says its sizes: _sized)
        for title, action, _notebooks in exports:
            row = Adw.ActionRow(title=title, subtitle="Working out size…", activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _row, chosen: win.activate_action(chosen, None), action)  # (this stays: choose_zip)
            group.add(row)
            self.export_rows.append(row)
        page.add(group)
        group = Adw.PreferencesGroup()
        for title, action in (("Send Feedback…", "win.feedback"), ("Ongoing Feedback", "win.fixes")):
            row = Adw.ButtonRow(title=title, end_icon_name="go-next-symbolic")
            row.connect("activated", go, action)
            group.add(row)
        page.add(group)
        self.add(page)
        copies, stop = json.loads(json.dumps([notebooks for _title, _action, notebooks in exports])), threading.Event()
        self.connect("closed", lambda *_: stop.set())  # (nothing to say them to: no more reading large chats)
        threading.Thread(target=Settings._work_out_sizes, args=([self], copies, stop), daemon=True).start()

    @staticmethod
    def _work_out_sizes(holding, copies, stop):
        """A thread. This in a list, let go of on the main loop: the last reference to a widget mustn't go off it. (Not a
        weak one: nothing else in Python need have hold of this, once it's presented.)"""
        for i, notebooks in enumerate(copies):
            try:
                sizes = zip_sizes(notebooks, stop)
                if sizes is None:
                    break
                alone, whole = sizes
                said = f"{size_said(alone)}; with glossary and chats, not known" if whole is None else \
                    f"{size_said(alone)}, or {size_said(whole)} with glossary and chats"
            except Exception as e:  # (a thread. The export says too, if it's still so)
                print(f"Record: an export's size: {e!r}", file=sys.stderr)
                said = "Size not known"
            GLib.idle_add(Settings._sized, holding, i, said)
        GLib.idle_add(holding.clear)

    @staticmethod
    def _sized(holding, i, said):
        settings = holding[0]
        if settings.get_realized():  # (closed, or its window gone: left alone)
            settings.export_rows[i].set_subtitle(said)
        return False


class Window(Adw.ApplicationWindow, feedback.FeedbackHost, viewers.ViewerHost):
    """Record's window, as Baude's is laid out: on the left the notebooks and, under them, the open one's pages
    (PagesList); in the middle what's open, a tab each (widgets.MainTabs: a page of notes, a chat to read, a file's
    code), their bar the window's top; on the right the Record (your conversation with it), Files and Git, as Baude's
    side panel has them. Either side shown or hidden at once (Ctrl+B, Ctrl+Alt+B)."""

    def __init__(self, app):
        super().__init__(application=app, title=noun(), default_width=1320, default_height=860)
        self.store, self.current = Store(), None
        self.notes = NotesNames(self.store, lambda: self.current)  # (the notebooks and their pages, for @ and ⌘K)
        self.card, self.card_going = None, None  # (the name's card showing: ref_card; its going: hover_left)
        self.switching = False  # (another notebook's tabs coming in: _show)
        self.shown_order = []  # (the tabs as they were shown, the last last: reader, diary, code_page)
        split = Gtk.Box()
        # left: the notebooks, New Notebook at the top as Baude's New session is, and under them the open one's pages,
        # which fold away; its toggle in the window's bar, by the window's buttons, as Baude's sessions' is
        side = self.books = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT, width_request=FixedWidth.LEAST)
        side.add_css_class("record-books")
        side.add_css_class("baude-frame")  # (the frame, as the bars and the right bar are: widgets.CSS)
        side_toggle = self._sidebar_toggle()
        books = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        books.add_css_class("session-sidebar")  # (Baude's look: its group headers, its compact rows)
        self.pages = PagesList(self)  # (first: pages are gone to more often than notebooks)
        books.append(self.pages)
        self.books_head = widgets.group_header(book(plural=True), self.store.books_folded,
                                               lambda: self.fold_books(not self.store.books_folded),
                                               plus=("list-add-symbolic", f"New {book()} (Ctrl+N)", self.new_notebook))
        self.books_head.set_margin_start(12)
        self.books_head.set_margin_end(8)
        self.books_head.set_margin_top(10)
        books.append(self.books_head)
        self.notebook_list = Gtk.ListBox()
        self.notebook_list.add_css_class("navigation-sidebar")
        self.notebook_list.add_css_class("notebook-sidebar")
        self.notebook_list.connect("row-selected", self._selected)
        keys = Gtk.EventControllerKey()  # (F2, as Baude's list renames a chat)
        keys.connect("key-pressed", lambda _c, kv, _k, _s: (kv == Gdk.KEY_F2 and self.rename()) or kv == Gdk.KEY_F2)
        self.notebook_list.add_controller(keys)
        self.books_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.notebook_list,
                                               propagate_natural_height=True)
        self.books_reveal = Gtk.Revealer(child=self.books_scroll, transition_type=Gtk.RevealerTransitionType.NONE,
                                         reveal_child=not self.store.books_folded)
        books.append(self.books_reveal)
        side.set_content(books)
        # (at the foot, under a rule from the notebooks: Update Available, while a new version waits: update_waiting)
        self.update_bar = widgets.UpdateBar(self.update_now)
        side.add_bottom_bar(self.update_bar)
        self.left_sizes()
        # (held at its width: as it'd lay itself out, a long notebook's or page's name in full makes it wider; a name
        # too long ends in …. Not an Adw.Clamp: one takes the width the column asks for and centres it in that)
        column = FixedWidth(side, self.store.books_width or self.BOOKS_WIDTH)
        column.resized = self._books_resized
        split.append(column)
        # the middle: what's open, a tab each, their bar at the top of it, as Baude's chat has its tabs
        # (⌥⌘← ⌥⌘→: back and forward through them)
        self.tabs = widgets.MainTabs(closed=self._tab_closed, reopen=self.reopen_tab, copy_links=True)
        self.tabs.view.connect("notify::selected-page", lambda *_: self._tab_shown())
        main = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT, hexpand=True)
        main.add_css_class("record-content")
        main.add_css_class("baude-frame")
        # the window's bar, all the way across over the three: the window's buttons and the notebooks' toggle at its
        # start, the tabs, then the right bar's toggle and ☰. Nothing a side bar's dragged over hides them. (All of it
        # the bar's title, nothing either side of it: a title's centred, with a gap to match whatever's beside it, so
        # the tabs would have had less on one side. The window's buttons native on macOS, as a bar's own are: otherwise
        # they take no room, the traffic lights over what's there)
        self.tabs.bar.set_hexpand(True)
        whole = Gtk.Box(spacing=6, hexpand=True)
        head = Adw.HeaderBar(title_widget=whole, show_start_title_buttons=False, show_end_title_buttons=False)
        head.add_css_class("tabs-head")  # (the tabs on the bar, as tall as it: widgets.CSS)
        start_buttons = Gtk.WindowControls(side=Gtk.PackType.START, use_native_controls=sys.platform == "darwin")
        end_buttons = Gtk.WindowControls(side=Gtk.PackType.END, use_native_controls=sys.platform == "darwin")
        for buttons in (start_buttons, end_buttons):  # (none to show, none's gap: as a header bar's)
            buttons.bind_property("empty", buttons, "visible",
                                  GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.INVERT_BOOLEAN)
        whole.append(start_buttons)
        whole.append(side_toggle)
        whole.append(self.tabs.bar)
        menu = Gio.Menu()
        menu.append("Open a Chat…", "win.openchat")
        menu.append(widgets.COPY_LINK, "win.copylink")
        menu.append("Detach all", "win.detach")
        view = Gio.Menu()
        view.append("Full-width text", "win.fullwidth")
        menu.append_section(None, view)
        tools = Gio.Menu()  # (Baude's views over the window; Files and Git are in the right bar, code in tabs)
        for title, page in (("Kubernetes…", "kube"), ("Databases…", "db")):
            tools.append(title, f"win.viewer::{page}")
        menu.append_section(None, tools)
        app_menu = Gio.Menu()
        item = Gio.MenuItem.new("Settings…", "win.settings")
        item.set_attribute_value("accel", GLib.Variant.new_string(widgets.SETTINGS_KEY))
        app_menu.append_item(item)
        item = Gio.MenuItem.new("Send Feedback…", "win.feedback")
        item.set_attribute_value("accel", GLib.Variant.new_string(widgets.FEEDBACK_KEY))
        app_menu.append_item(item)
        app_menu.append("Ongoing Feedback", "win.fixes")
        app_menu.append(f"Restart {noun()}", "win.restart")
        menu.append_section(None, app_menu)
        menu_btn = self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        for name, fn in (("openchat", self.choose_chat), ("copylink", self.tabs.copy_link), ("rename", self.rename),
                         ("export", lambda: self.current and self.ask_export([self.current], self.current["name"])),
                         ("exportall", lambda: self.ask_export(self.store.notebooks, book(plural=True))),
                         ("detach", self.detach),
                         ("delete", self.delete),
                         ("new", lambda *_: self.new_notebook()), ("restart", lambda: self.restart()),
                         ("settings", lambda: Settings(self).present(self)), ("feedback", lambda: self.open_feedback()),
                         ("fixes", lambda: self.open_fixes())):
            a = Gio.SimpleAction(name=name)
            a.connect("activate", lambda _a, _p, fn=fn: fn())
            self.add_action(a)
        wide = Gio.SimpleAction.new_stateful("fullwidth", None, GLib.Variant.new_boolean(self.store.full_width))
        wide.connect("change-state", Window._full_width_toggled, weakref.ref(self))  # (no closure over the window)
        self.add_action(wide)
        back_up = Gio.SimpleAction.new_stateful("backupall", None, GLib.Variant.new_boolean(self.store.backup_all))
        back_up.connect("change-state", Window._backup_all_toggled, weakref.ref(self))
        self.add_action(back_up)
        viewer = Gio.SimpleAction.new("viewer", GLib.VariantType.new("s"))
        viewer.connect("activate", lambda _a, page, ref: (w := ref()) and w.open_viewer(page.get_string()),
                       weakref.ref(self))
        self.add_action(viewer)
        app.set_accels_for_action("win.new", ["<primary>n"])
        app.set_accels_for_action("win.copylink", [widgets.COPY_LINK_KEY])
        launcher = Gio.SimpleAction.new_stateful("launcher", None, GLib.Variant.new_boolean(installed()))
        launcher.connect("change-state", Window._launcher_toggled)
        self.add_action(launcher)
        keys = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)  # (as Baude's: its keys for the same things)
        for triggers, fn in (("<Control>b F9", lambda: self.left_slide.toggle()),
                             ("<Control><Alt>b", lambda: self.right_slide.toggle()),
                             (widgets.zoom_keys("w"), lambda: self.tabs.close_current()),
                             (widgets.NAV_KEYS["back"], lambda: self.tabs.back()),  # (⌥⌘ ← → ↑: a browser's
                             (widgets.NAV_KEYS["forward"], lambda: self.tabs.forward()),  # back, forward, up)
                             (widgets.NAV_KEYS["up"], lambda: self.go_up()),
                             (widgets.SETTINGS_KEY, lambda: Settings(self).present(self)),
                             (widgets.PALETTE_KEY, lambda: self.open_palette()),
                             (" ".join(m + "f" for m in widgets.ZOOM_MODS),  # (as Baude's: what's over it first)
                              lambda: self.kube.find() or self.schema.start_find() or self.find_in_code()),
                             (widgets.zoom_keys("plus", "<Shift>plus", "equal", "KP_Add"),
                              lambda: self.zoom_code(+1) or self.set_zoom(self.store.zoom + widgets.ZOOM_STEP)),
                             (widgets.zoom_keys("minus", "underscore", "KP_Subtract"),
                              lambda: self.zoom_code(-1) or self.set_zoom(self.store.zoom - widgets.ZOOM_STEP)),
                             (widgets.zoom_keys("0", "KP_0"),
                              lambda: self.zoom_code(0) or self.set_zoom(self.store.zoom_default))):
            for trigger in triggers.split():
                keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(trigger),
                                                   Gtk.CallbackAction.new(lambda *_, fn=fn: fn() or True)))
        self.add_controller(keys)
        self.zoom_css = Gtk.CssProvider()  # (its sizes, a class each: the notes' page, the chat read, the conversation)
        self.zoom_css.load_from_string(ZOOM_CSS)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self.zoom_css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        self.zoom_wait = self.zoom_saving = None  # (the frame to zoom in; its saving: set_zoom)
        self.rolling, self.zipping = GLib.timeout_add_seconds(Store.BACKUP_EVERY, self.roll_backup), False  # (roll_backup)
        self.scale_wait, self.text_scaled = None, False  # (the sizes applied in a moment; the text's among them: set_scale)
        # right: the Record (your conversation with it), its folders, Files and Git, as Baude's side panel has them
        self.chat = RecordChat(self)
        self.apply_zoom()
        self.right_stack = Adw.ViewStack(vexpand=True)
        widgets.ensure_icons()  # (baude-record-symbolic, baude-claude-symbolic: Baude's own icons)
        self.right_stack.add_titled_with_icon(self.chat, "record", noun(), "baude-record-symbolic")
        self.folders_page = FoldersPage(self)
        self.right_stack.add_titled_with_icon(self.folders_page, "folders", "Folders", "folder-open-symbolic")
        self.add_viewer_pages(self.right_stack, code=False)  # (Files and Git; code opens in a tab: show_code)
        right = self.right = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        right.add_css_class("baude-frame")
        # (its pages' switcher at the top of it, under the window's bar, as Baude's side panel has its own)
        right.add_top_bar(Adw.ViewSwitcher(stack=self.right_stack, policy=Adw.ViewSwitcherPolicy.NARROW,
                                           margin_top=6, margin_bottom=6, halign=Gtk.Align.CENTER))
        right.set_content(self.right_stack)
        right.set_size_request(340, -1)
        self.right_toggle = Gtk.ToggleButton(icon_name="sidebar-show-right-symbolic", active=True,
                                             tooltip_text=f"{noun()}, Files and Git (Ctrl+Alt+B)")
        whole.append(self.right_toggle)
        whole.append(menu_btn)
        whole.append(end_buttons)
        self.content_stack = Gtk.Stack()
        none = Adw.StatusPage(icon_name="system-users-symbolic", title=f"Make a {book()}",
                              description=f"A {book()} is your notes on a subject, and a {noun()} that reads AI prose "
                                          "with you through them: it knows only what's in them and remembers only "
                                          "the last three messages.")
        b = Gtk.Button(label=f"New {book()}", halign=Gtk.Align.CENTER)
        b.add_css_class("pill")
        b.add_css_class("suggested-action")
        b.connect("clicked", lambda *_: self.new_notebook())
        none.set_child(b)
        self.content_stack.add_named(widgets.inside(none), "none")
        self.content_stack.add_named(widgets.inside(Gtk.Box()), "empty")
        self.content_stack.add_named(widgets.inside(self.tabs), "tabs")
        beside = Gtk.Overlay(child=self.content_stack)  # (the left edge's grip goes on past the column: FixedWidth.grip_past)
        beside.add_overlay(column.grip_past(split))
        main.set_content(beside)
        main.add_bottom_bar(self.make_capture_bar())  # (feedback.FeedbackHost: Capture while one's pending)
        # (the right bar's rule stops where the notes are at their least: it never goes over them, nor does the
        # column's edge, dragged, push them under it: FixedWidth.limited)
        self.paned = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=main, end_child=right,
                                          resize_start_child=True, resize_end_child=False, shrink_start_child=False,
                                          shrink_end_child=False, hexpand=True, position=100_000))
        self.paned.add_css_class("main-split")  # (its rule unseen: the inside's edge is the line; still dragged)
        self.paned.add_css_class("inside-before")
        split.append(self.paned)
        self.right_slide = widgets.PaneSlide(self.paned, right, [self.right_toggle])
        self.left_slide = ColumnSlide(column, side, [side_toggle])
        column.slide = self.left_slide
        self.left_slide.moving = self.right_slide.moving = self._side_moving
        framed = Adw.ToolbarView(content=split)
        framed.add_css_class("baude-frame")  # (the bar on the frame, as the columns are: widgets.CSS)
        framed.add_top_bar(head)
        self.toasts = Adw.ToastOverlay(child=self.viewer_overlays(framed))  # (Kubernetes, Databases: over it all)
        self.set_content(self.toasts)
        self.fill_sidebar()
        chats = list(self.store.chats)  # (taken first: showing a notebook keeps what's open, and none is yet)
        first = self.notebook_list.get_row_at_index(0)
        if first:
            self.notebook_list.select_row(first)
        else:
            self._show(None)
        for chat in chats:  # (the chats open when it last closed, back in their tabs)
            if os.path.exists(chat.get("path") or ""):
                self.open_chat(chat["path"], select=False, present=False)
        self.apply_full_width(self.store.full_width)
        self.connect("close-request", lambda *_: self.store.save_now() and False)
        self.connect("notify::is-active", Window._activated)  # (a plain function: no closure over the window)
        widgets.watch_github(self, Window.github_folders, Window._github_changed)
        update.watch_input(self)  # (what update.quiet reads: a new version waits while you're at it)
        widgets.warm_shaders(self)  # (after its first frame)

    BOOKS_WIDTH = DiaryEditor.PAGES_WIDTH  # the notebooks' column, in px: the pages' column's width

    def _side_moving(self, on):
        """A side bar's started moving (sliding, or dragged), or it's still again. A chat read in the tab shown (Reader)
        is all of it laid out again at each new width, and a long one took a frame or more for each: so while the bar
        moves its messages far from the view are hidden (ZoomPlace, as for the zoom), and what's in view is all that's laid
        out again; once it's still they come back, a few a frame, nearest first, its place kept. (A page of notes lays out
        only what's in view already.)"""
        place = getattr(self.tabs.current(), "place", None)
        if place is None or isinstance(place, DiaryPlace):
            return
        if on:
            place.through()
        place.paused = on

    def _books_resized(self, width):
        """The notebooks' column dragged wider or narrower: kept."""
        self.store.books_width = width
        self.store.save()

    def fold_books(self, folded):
        """The notebooks' list folded away (its header's arrow), or shown again; kept."""
        self.store.books_folded = folded
        self.books_reveal.set_reveal_child(not folded)
        self.books_head.set_collapsed(folded)
        self.store.save()
        self.left_sizes()

    def left_sizes(self):
        """The left column's height shared: the pages take what's spare while they're shown, the notebooks their
        own height, up to a few rows' (more scroll); with the pages folded, the notebooks what's spare."""
        if not hasattr(self, "books_scroll"):
            return
        pages = not self.store.pages_folded
        self.books_scroll.set_max_content_height(240 if pages else -1)
        self.books_scroll.set_vexpand(not pages)
        self.books_reveal.set_vexpand(not pages and not self.store.books_folded)

    @staticmethod
    def _sidebar_toggle():
        """The notebooks shown or hidden (both toggles, the notebooks' bar's and the content's, are the one switch)."""
        return Gtk.ToggleButton(icon_name="sidebar-show-symbolic", active=True, tooltip_text=book(plural=True))

    def state(self):
        """What a window made again puts back (restart): the Record shown, its tab, the window's
        size, the question being written with its quote and its caret, on a page of notes (its text is kept as it's
        written) its caret and a new title not yet taken, and where each open page and chat and the Record's own
        conversation have been read to."""
        shown = self.tabs.current()
        note = shown if isinstance(shown, DiaryEditor) else None
        focus = self.get_focus()
        return {"record": self.current["id"] if self.current else None,
                "tab": list(shown.tab_key) if shown is not None and shown.tab_key[0] != "code" else None,
                "size": [self.get_width(), self.get_height()], "maximized": self.is_maximized(),
                "frame": widgets.window_frame(self),  # (macOS: the display it's on, where; widgets.put_back)
                "right": self.paned.get_width() - self.paned.get_position() if self.right.get_visible() else None,
                "right_page": self.right_stack.get_visible_child_name(),
                "fixes": self.tabs.get(("fixes",)).state() if self.tabs.get(("fixes",)) else None,
                "composer": widgets.typed_text(self.chat.composer), "composer_caret": widgets.caret_of(self.chat.composer),
                "quote": self.chat.quote, "note_caret": widgets.caret_of(note.view) if note else None,
                "note_title": note.title.get_text() if note else None,
                "focus": "composer" if focus is self.chat.composer else "note" if note and focus is note.view else None,
                "places": [[list(c.tab_key), c.place.saved() if hasattr(c, "place") else widgets.scroll_place(c.scroll)]
                           for c in self.tabs.children()
                           if isinstance(c, (DiaryEditor, Reader))],
                "chat_place": widgets.scroll_place(self.chat.scroll)}

    def restore(self, state):
        if state.get("size") and all(state["size"]):
            self.set_default_size(*state["size"])
        widgets.put_back(self, state.get("frame"), state.get("maximized"))  # (where it was, on macOS)
        if state.get("right"):  # (the right bar's width, once the window's laid out)
            GLib.idle_add(lambda: self.paned.set_position(max(0, self.paned.get_width() - state["right"])) and False)
        if state.get("right_page"):
            self.right_stack.set_visible_child_name(state["right_page"])
        if state.get("record") and any(t["id"] == state["record"] for t in self.store.notebooks):
            self.select(state["record"])
            if state.get("fixes"):
                self.open_fixes(select=False).restore(state["fixes"])
            if state.get("tab"):
                self.tabs.show(tuple(state["tab"]))
            self.chat.composer.get_buffer().set_text(state.get("composer") or "")
            widgets.put_caret(self.chat.composer, state.get("composer_caret"))
            self.chat.set_quote(state.get("quote") or "")
            note = self.tabs.current()
            if isinstance(note, DiaryEditor):
                widgets.put_caret(note.view, state.get("note_caret"))
                if state.get("note_title"):
                    note.title.set_text(state["note_title"])
            for key, place in state.get("places") or ():
                if isinstance(tab := self.tabs.get(tuple(key)), (DiaryEditor, Reader)):
                    widgets.hold_place(tab.scroll, place)
            widgets.hold_place(self.chat.scroll, state.get("chat_place"))
            focus = self.chat.composer if state.get("focus") == "composer" else \
                note.view if state.get("focus") == "note" and isinstance(note, DiaryEditor) else None
            if focus is not None:  # (once it's shown: you go on typing)
                GLib.idle_add(lambda: focus.grab_focus() and False)

    def idle(self):
        """Nothing a restart would cut short or take from under you (update.Updater): restart_blockers."""
        return not self.restart_blockers()

    def restart_blockers(self):
        """What a restart waits for, said (the Update Available bar's tooltip), [] for none: a question being answered,
        a dialog or capture open, and you at it (the window you're in, something typed, clicked or scrolled in it in the
        last minute)."""
        why = ["a question being answered"] if self.chat.ask is not None else []
        bar = getattr(self, "capture_bar", None)
        if self.get_visible_dialog() is not None or (bar is not None and bar.get_reveal_child()):
            why.append("a dialog open")
        if not update.quiet(self):
            why.append("you, using this window")
        return why

    def update_waiting(self, waiting):
        """(update.Updater's) A new version found and waiting on idle, or not: the bar at the notebooks' foot."""
        self.update_bar.say(self.restart_blockers() if waiting else None)

    def update_now(self):
        """The bar clicked: restarted onto the new version now (a question being answered stopped, said and asked)."""
        u = getattr(self.get_application(), "updater", None)
        if u is None or not u.ready:
            return
        go = lambda: self.restart({"from": u.started, "to": u.ready,  # noqa: E731
                                   "commits": update.new_commits(u.started, u.ready, u.root)})
        if self.chat.ask is None:
            return go()
        dlg = Adw.AlertDialog(heading="Restart now?", body="The question being answered is stopped.",
                              default_response="cancel", close_response="cancel")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("go", "Restart Now")
        dlg.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "go" and go())
        dlg.present(self)

    def restart(self, updated=None):
        """☰ → Restart Record, as Baude's, or a new version found (update.Updater; updated, what by): this process
        replaced by a fresh Record (a new version picked up), which puts back what this one showed (restart_path;
        state) and gives the front back to the app you were in (update.restarted)."""
        self.store.save_now()
        with open(restart_path(), "w") as f:
            json.dump({**self.state(), "front": update.front_app(), "updated": updated}, f)
        app = self.get_application()
        if app is not None and app.server is not None:
            app.server.close()  # (the new one listens in its place)
        os.execv(sys.executable, [sys.executable, os.path.realpath(__file__)])

    # ---- sizes (Settings…), zoom (Ctrl/⌘ + − 0), the launcher, feedback
    ZOOM_SAVE_MS = 1000  # (the zoom written this long after the last step: a save writes every notebook)

    def set_zoom(self, zoom):
        """The notes', the chat's and the conversation's text this much bigger (1.0 the usual), kept. It's applied as
        the next frame begins: however many key presses there were since the last one, it's one change of size."""
        zoom = round(min(widgets.ZOOM_MAX, max(widgets.ZOOM_MIN, zoom)), 2)
        if zoom == self.store.zoom:
            return
        self.store.zoom = zoom
        if self.zoom_saving is not None:
            GLib.source_remove(self.zoom_saving)
        self.zoom_saving = GLib.timeout_add(self.ZOOM_SAVE_MS, self._zoom_saved)
        clock = self.get_frame_clock()
        if clock is None:  # (not on screen: no frame to wait for)
            self.apply_zoom()
        elif self.zoom_wait is None:
            self.zoom_wait = clock.connect("before-paint", self._zoom_frame)
            clock.request_phase(Gdk.FrameClockPhase.BEFORE_PAINT)

    def _zoom_frame(self, clock):
        clock.disconnect(self.zoom_wait)
        self.zoom_wait = None
        self.apply_zoom()

    def _zoom_saved(self):
        self.zoom_saving = None
        self.store.save()
        return False

    def set_markup(self, mode):
        """Settings → Markup visibility: the notes' markup shown on the caret's line, never, or always (but a link's); every
        page open drawn so, and it kept."""
        self.store.markup = mode
        self.store.save()
        for child in self.tabs.children():
            if isinstance(child, DiaryEditor):
                child._highlight()

    def set_zoom_default(self, zoom):
        """Settings → Zoom: what Ctrl/⌘ 0 goes back to; and the zoom now."""
        self.store.zoom_default = zoom
        self.store.save()  # (the zoom may be at it already)
        self.set_zoom(zoom)

    def apply_zoom(self):
        """The zoom on the conversation and on the tab shown, each keeping its place. A tab not shown has its turn as
        it's shown (_tab_shown), before its first frame: until then nothing in it is styled or laid out again."""
        name = zoom_class(self.store.zoom)
        self.chat.place.zoom(name)
        set_zoom_class(self.chat.composer, name)
        self.zoom_tab(self.tabs.current())

    def zoom_tab(self, tab):
        if isinstance(tab, (DiaryEditor, Reader)):
            tab.place.zoom(zoom_class(self.store.zoom))

    def set_scale(self, key, size):
        """Settings → Size: the text's or the icons' (Baude's settings.json, so Baude's too), applied app-wide a moment
        after the last change (the text's restyles every window: once for a few quick clicks)."""
        widgets.save_settings(**{key: size})
        self.text_scaled = self.text_scaled or key == "text_scale"
        if self.scale_wait is not None:
            GLib.source_remove(self.scale_wait)
        self.scale_wait = GLib.timeout_add(150, self._scales_changed)

    def _scales_changed(self):
        """Every widget's size changes with the text's (widgets.apply_scales: the display's font resolution), not only
        what has the zoom's class: what's shown keeps its place, and the tabs not shown wait till they are."""
        self.scale_wait = None
        if self.text_scaled:
            self.text_scaled = False
            shown = self.tabs.current()
            for tab in self.tabs.children():
                if isinstance(tab, (DiaryEditor, Reader)):
                    tab.place.through() if tab is shown else tab.place.put_away()
            self.chat.place.through()
        widgets.apply_scales(self.get_display())
        return False

    @staticmethod
    def _launcher_toggled(action, value):
        """Settings → Show in Applications: Record's launcher made, or taken away."""
        try:
            install() if value.get_boolean() else uninstall()
        except OSError as e:
            print("record: the launcher:", e, file=sys.stderr)
        action.set_state(GLib.Variant.new_boolean(installed()))

    def capture_areas(self):
        """The window's parts, by name, for the text a feedback capture records."""
        areas = {self.notebook_list: "notebooks", self.pages.index: "pages", self.chat: "conversation",
                 self.kube: "Kubernetes", self.schema: "databases", self.lightbox: "image"}
        for child in self.tabs.children():
            if isinstance(child, DiaryEditor):
                areas[child.view] = f"notes: {child.page}"
            elif isinstance(child, Reader):
                areas[child] = "chat"
        return areas

    def feedback_context(self):
        """What a note sent from Record says it came from (feedback.save_feedback): the notebook, the chat open, its
        first folder (else the chat's), and Record."""
        t, folders = self.current or {}, self.read_folders()
        chat = (self.reader.chat if self.reader is not None else None) or self.store.reading or {}
        return [f"- session: {os.path.splitext(os.path.basename(chat.get('path') or '-'))[0]} "
                f"({book()} {full_name(t) if t else '-'})",
                f"- folder: {folders[0] if folders else '-'}", f"- Record: {os.path.realpath(__file__)}"]

    def apply_full_width(self, on):
        """☰ → Full-width text, as Baude's: the chat read and the diary across the whole pane, not a column. (Adw.Clamp
        can't be switched off, so on is a limit wider than any screen.)"""
        for child in self.tabs.children():
            if isinstance(child, (Reader, DiaryEditor)):
                child.clamp.set_maximum_size(100_000 if on else child.WIDTH)
                child.clamp.set_tightening_threshold(100_000 if on else 400)

    @staticmethod
    def _backup_all_toggled(action, value, ref):
        action.set_state(value)
        if (win := ref()) is not None:
            win.store.backup_all = value.get_boolean()
            win.store.save_now()

    def ask_export(self, notebooks, name):
        """Settings → Export…: whether the glossary and the chats go too, then where the zip goes. Over Settings, which is
        still there if either's cancelled."""
        d = Adw.AlertDialog(heading=f"Export {name}")
        extras = Gtk.CheckButton(label="Include glossary and chats", tooltip_text=(
            "What's been looked up in its folders, and what's shown of the chats in them or named in its notes"))
        d.set_extra_child(extras)
        d.add_response("cancel", "Cancel")
        d.add_response("ok", "Export…")
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")
        d.connect("response", lambda _d, r: r == "ok" and self.choose_zip(notebooks, name, extras.get_active()))
        d.present(self)

    def choose_zip(self, notebooks, name, extras):
        def chosen(dialog, result):
            try:
                path = dialog.save_finish(result).get_path()
            except GLib.Error:  # (cancelled)
                return
            if isinstance(settings := self.get_visible_dialog(), Settings):  # (it's said over the window: export)
                settings.close()
            self.export(notebooks, path, extras)
        Gtk.FileDialog(title="Export", modal=True, initial_name=f"{file_part(name)}.zip").save(self, None, chosen)

    def export(self, notebooks, path, extras, done=None):
        """A zip made off the main loop (export_zip), of the notebooks as they are now. done(error): when it's made, or else
        said here."""
        copy = json.loads(json.dumps(notebooks))

        def said(error):
            if done is not None:
                done(error)
            else:
                self.toast(html.escape(f"Couldn't export: {error}" if error else f"Exported to {path}"))  # (a toast's markup)
            return False

        def run():
            error = None
            try:
                export_zip(path, copy, extras)
            except Exception as e:  # (a thread: said, whatever it was)
                error = str(e) or repr(e)
            GLib.idle_add(said, error)
        threading.Thread(target=run, daemon=True).start()

    def roll_backup(self):
        """Every BACKUP_EVERY, all the notebooks in the one zip in place of the last (backup_zip), if they've changed. With the
        glossary and the chats (Settings) they may have whether or not the notebooks have. With nothing written anywhere it's
        left as it is: that's more likely notes that couldn't be read (Store) than notes that are gone."""
        written = any(text.strip() for notebook in self.store.notebooks for text in notebook["pages"].values())
        if written and not self.zipping and (self.store.unzipped or self.store.backup_all):
            self.zipping, self.store.unzipped = True, False

            def done(error):
                self.zipping = False
                if error:
                    self.store.unzipped = True  # (the next time)
                    print(f"Record: the backup: {error}", file=sys.stderr)
            self.export(self.store.notebooks, backup_zip(), self.store.backup_all, done)
        return True

    @staticmethod
    def _full_width_toggled(action, value, ref):
        action.set_state(value)
        if (win := ref()) is not None:
            win.store.full_width = value.get_boolean()
            win.store.save_now()
            win.apply_full_width(value.get_boolean())

    @staticmethod
    def _activated(win, _pspec):
        if not win.is_active():
            win.store.flush()  # (gone to another window: what's typed is on the disk)

    def github_folders(self):
        """What GitHub's lists are read for (lookup.GitHub.look): the notebook's folders, and the chat shown's own, which
        nothing attached."""
        attached = attached_folders(self.current)
        return attached, [f for f in self.read_folders() if f not in attached]

    @staticmethod
    def _github_changed(win):
        """GitHub's lists have changed: the pull requests' and issues' logos in what's open, where they stand now."""
        for child in win.tabs.children():
            if isinstance(child, (DiaryEditor, Reader)):
                child.github_changed()
        win.git_page.pulls_changed()

    # sidebar
    def _row(self, t):
        row = Gtk.ListBoxRow()
        row.notebook = t
        box = Gtk.Box(spacing=6, margin_start=6, margin_end=4)  # (a line, as a page's in the pages' list)
        row.name = widgets.FadeLabel(xalign=0, hexpand=True)  # (too long: it fades where it's cut)
        box.append(row.name)
        row.set_child(box)
        self._fill_row(row)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)  # (right-click: Rename…, Delete…, as Baude's chats)
        click.connect("pressed", lambda _g, _n, x, y: self.book_menu(row, x, y))
        row.add_controller(click)
        return row

    def book_menu(self, row, x, y):
        """A notebook's menu (right-click on it): Rename… and Delete…, for the one clicked (it's selected first)."""
        self.notebook_list.select_row(row)
        menu = Gio.Menu()
        menu.append("Rename…", "win.rename")
        menu.append("Delete…", "win.delete")
        pop = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        pop.set_parent(row)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    @staticmethod
    def attached_words(notebook):
        """The folders a notebook's attached to, in a few words: the one's name, or how many."""
        folders = attached_folders(notebook)
        return (os.path.basename(folders[0]) if len(folders) == 1 else f"{len(folders)} folders") if folders else ""

    def _fill_row(self, row):
        row.name.set_text(full_name(row.notebook))
        row.set_tooltip_text(self.attached_words(row.notebook) or None)  # (its folders, the pointer over it)

    def fill_sidebar(self):
        while (r := self.notebook_list.get_row_at_index(0)) is not None:
            self.notebook_list.remove(r)
        for t in self.store.notebooks:
            self.notebook_list.append(self._row(t))

    def refresh_sidebar_row(self, notebook):
        i = 0
        while (r := self.notebook_list.get_row_at_index(i)) is not None:
            if r.notebook is notebook:
                self._fill_row(r)
            i += 1

    def _selected(self, _lb, row):
        self._show(row.notebook if row else None)

    def _show(self, notebook):
        self.store.flush()  # (the notebook left is on the disk)
        if self.current is not None and self.current is not notebook and Store.is_blank(self.current):
            GLib.idle_add(self.drop_blank, self.current)  # (left blank: not kept; once the selection has settled)
        changed = notebook is not self.current
        self.current = notebook
        if changed:
            self.viewer_at = None
            self.viewer_folder_changed()  # (the viewers on this notebook's folder)
        if changed:  # (the notebook left's pages' tabs closed: its own come in, as it had them open)
            self.switching = True
            try:
                for key in [k for k in list(self.tabs.pages) if k[0] == "note"]:
                    self.tabs.close(key)
            finally:
                self.switching = False
        if not notebook:
            self.pages.show(None)
            self.content_stack.set_visible_child_name("none")
            self.folders_page.fill()
            return
        self.folders_page.fill()
        self.pages.show(notebook)
        if changed:
            shown = notebook.get("page") if notebook.get("page") in notebook["pages"] else HOME
            for name in [n for n in notebook.get("open") or [] if n in notebook["pages"] and n != shown] + [shown]:
                self.open_note(name, select=name == shown)
        for child in self.tabs.children():  # (the chats open: their names the notebook's folders' too)
            if isinstance(child, Reader) and child.chat:
                child.show(child.chat, self.read_folders(child.chat))
        if self.reader is not None:
            self.reader.activate()
        self.chat.show(notebook)
        self._tab_shown()
        self.chat.composer.grab_focus()

    # actions
    def free_name(self, cwd=None):
        """A new one's name: named for cwd's folder (else Untitled), "Shop 2" if that's taken."""
        used = {full_name(t).lower() for t in self.store.notebooks}
        base = os.path.basename((cwd or "").rstrip(os.sep)) or "Untitled"
        name, n = base, 2
        while name.lower() in used:
            name, n = f"{base} {n}", n + 1
        return name

    def new_notebook(self):
        """New Notebook (Ctrl+N): a new one, to name. While one made with New is still blank (Store.is_blank), that one
        again, not another: blank ones don't pile up, as Baude lists a new chat only once it's begun."""
        blank = next((t for t in self.store.notebooks if Store.is_blank(t)), None)
        if blank is None:
            self.store.new(self.free_name())
            self.fill_sidebar()
            self.notebook_list.select_row(self.notebook_list.get_row_at_index(0))
        else:
            self.select(blank["id"])
        self.open_note(HOME)
        self.rename(f"New {book()}", "Create")

    def drop_blank(self, notebook):
        """One made with New and left blank, gone to another from: let go of (a blank one isn't kept)."""
        if notebook is not self.current and Store.is_blank(notebook) and notebook in self.store.notebooks:
            self.store.delete(notebook)
            i = 0
            while (r := self.notebook_list.get_row_at_index(i)) is not None:
                if r.notebook is notebook:
                    self.notebook_list.remove(r)
                    break
                i += 1
        return False

    def attach(self):
        """The Folders page's +: a folder picked, one more of the notebook's."""
        if not self.current:
            return
        notebook = self.current

        def pick(s):  # (a folder, folder_attachment: one more)
            if s["cwd"] not in notebook.setdefault("folders", []):
                notebook["folders"].append(s["cwd"])
            self.attached_changed(notebook)
        AttachDialog(pick).present(self)

    def detach_folder(self, folder):
        """The Folders page's ×: that folder let go."""
        notebook = self.current
        notebook["folders"] = [f for f in notebook.get("folders", []) if os.path.realpath(f) != folder]
        self.attached_changed(notebook)

    def choose_chat(self):
        """☰ → Open a Chat… (or Read's button): a Claude Code chat picked, read on the Read tab (open_chat)."""
        AttachDialog(lambda s: self.open_chat(s["path"]), chats=True).present(self)

    def attached_changed(self, notebook):
        """A folder attached or let go: shown, saved; the chat open's names the folders' now."""
        self.store.save_now()
        if notebook is self.current:
            self.folders_page.fill()
            for child in self.tabs.children():  # (the chats open: their names the folders' now)
                if isinstance(child, Reader) and child.chat:
                    child.show(child.chat, self.read_folders(child.chat))
        self.refresh_sidebar_row(notebook)

    def detach(self):
        """☰ → Detach all: the folders let go."""
        if self.current and self.current.get("folders"):
            self.current["folders"] = []
            self.store.save_now()
            self._show(self.current)
            self.refresh_sidebar_row(self.current)

    def rename(self, heading="Rename", doing="Rename"):
        if not self.current:
            return
        notebook = self.current
        d = Adw.AlertDialog(heading=heading)
        e = Gtk.Entry(text=base_name(notebook["name"]), activates_default=True, hexpand=True)
        box = Gtk.Box(spacing=6)
        box.append(e)
        d.set_extra_child(box)
        d.add_response("cancel", "Cancel")
        d.add_response("ok", doing)
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")

        def done(_d, r):
            if r == "ok" and base_name(e.get_text()):
                notebook["name"] = base_name(e.get_text())
                notebook.pop("fresh", None)  # (named: kept, Store.is_blank)
                self.store.save_now()
                self.refresh_sidebar_row(notebook)
        d.connect("response", done)
        d.present(self)
        e.grab_focus()

    def delete(self):
        if not self.current:
            return
        notebook = self.current
        d = Adw.AlertDialog(heading=f"Delete {full_name(notebook)}?",
                            body=f"Its notes ({len(notebook['pages'])} pages) and conversation go with it. "
                                 "The folders it's attached to aren't touched.")
        d.add_response("cancel", "Cancel")
        d.add_response("delete", "Delete")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, r):
            if r == "delete":
                self.store.delete(notebook)
                self.fill_sidebar()
                first = self.notebook_list.get_row_at_index(0)
                if first:
                    self.notebook_list.select_row(first)
                else:
                    self._show(None)
        d.connect("response", done)
        d.present(self)

    def add_unknown(self, term):
        notebook = self.current
        notebook["pages"][HOME] = add_to_learn(notebook["pages"].get(HOME, ""), term)
        self.store.save()
        if (home := self.tabs.get(("note", HOME))) is not None:
            home.reload()
        toast = Adw.Toast(title=f"“{term}” added to {TO_LEARN}", timeout=4)
        toast.set_button_label("Open")
        toast.connect("button-clicked", lambda *_: self.open_note(HOME))
        self._toast(toast)

    def toast(self, text):
        self._toast(Adw.Toast(title=text, timeout=4))

    def space(self):
        """⌘K's search of the notebook's folders, every chat by its title, and the notebooks and their pages (what @
        lists, in the notes and in the Record's box)."""
        return search_spaces(attached_folders(self.current), self.notes)

    def names_in(self, text):
        """The names in a text that the notebook's folders have (to link): none until they're catalogued."""
        space = self.space()
        return {it["surface"] for it in space.mentions(text)} if space is not None and not space.busy else set()

    def follow(self, uri, from_chat=False):
        """A link in the chat or a reply: a name (ref:) opened where it is (open_ref); a web link opens as links do."""
        if not uri.startswith("ref:"):
            return False
        self.open_ref(urllib.parse.unquote(uri[4:]), from_chat=from_chat)
        return True

    def open_ref(self, name, from_chat=False, _message=None):
        """A name clicked: opened where it is (open_item: its file on the Code tab, a branch on the Git tab, a
        Kubernetes object in its view, a table in the database diagram, a chat to read). One that has nowhere to
        open says what it is (ref_card), as the pointer over it does. from_chat: a name in the chat or a reply,
        not an @name in the notes (its names the Read tab's: a chat read there has its folder's too)."""
        space = self.reader.space if from_chat and self.reader is not None and self.reader.space is not None \
            else self.space()
        item = space.find(name) if space is not None else None
        if item is None and from_chat:  # (a page or a notebook, in the Record's reply: the notes')
            item = self.notes.find(name)
        if item is not None and item.get("kind") in NotesNames.KINDS:
            return self.open_notes_item(item)
        if item is None and (url := lookup.github_url(name)) is not None:  # (not listed now: its page is still there)
            return fixview.web(self, url)
        if item is None:
            where = ", ".join(short_dir(f) for f in attached_folders(self.current))
            return self.toast(f"@{name}: not found in {where}" if where
                              else f"@{name}: attach a folder to look it up")
        if item.get("kind") in ("branch", "k8s", "chat") or (item.get("entry") or {}).get("db") or \
                (item.get("path") and os.path.exists(item["path"])) or item.get("label"):
            self.open_item(item)
        else:
            self.ref_card(item, from_chat)

    def hover_ref(self, name, from_chat=False, at=None):
        """The pointer come over a name: what it is, just under the pointer (at: (the widget it's over, x, y)), or
        with no pointer, at the bottom (ref_card), unless that's showing already."""
        space = self.reader.space if from_chat and self.reader is not None and self.reader.space is not None \
            else self.space()
        if (item := space.find(name) if space is not None else None) is None:
            return
        self._card_stay()
        if self.card is None or self.card.surface != item["surface"]:
            self.ref_card(item, from_chat, at)

    CARD_LINGER = 350  # ms a name's card stays once the pointer's off it (time to come onto it, for To notes)

    def hover_left(self):
        """The pointer gone off a name: its card goes in a moment, unless the pointer comes onto the card."""
        if isinstance(self.card, Gtk.Popover) and self.card_going is None:
            self.card_going = GLib.timeout_add(self.CARD_LINGER, self._card_gone)

    def _card_stay(self):
        if self.card_going is not None:
            GLib.source_remove(self.card_going)
            self.card_going = None

    def _card_gone(self):
        self.card_going = None
        self.hide_card()
        return False

    def hide_card(self):
        card, self.card = self.card, None
        if isinstance(card, Gtk.Popover):
            card.popdown()
            GLib.idle_add(lambda: card.get_parent() is not None and card.unparent())
        elif card is not None:
            card.dismiss()

    def ref_card(self, item, from_chat=False, at=None):
        """What a name is, in place of the last one: just under the pointer (at: (widget, x, y)), a card that stays
        while the pointer's on the name or on it; with no pointer, at the bottom. A name in the chat's with To notes,
        to write it into the notes' page."""
        self.hide_card()
        what = f"{item['surface']}: {item['what']} · {item['summary']}"
        if at is not None and not at[0].get_mapped():  # (nothing on screen to put it under: GTK would crash)
            at = None
        if at is None:
            toast = Adw.Toast(title=GLib.markup_escape_text(what), timeout=6)
            if from_chat:
                toast.set_button_label("To notes")
                toast.connect("button-clicked", lambda *_: self.diary_ref(item["surface"], item.get("label")))
            toast.surface = item["surface"]
            toast.connect("dismissed", lambda t: setattr(self, "card", None) if self.card is t else None)
            self.card = toast
            self._toast(toast)
            return
        widget, x, y = at
        pop = Gtk.Popover(autohide=False, has_arrow=False, position=Gtk.PositionType.BOTTOM, can_focus=False)
        pop.add_css_class("ref-card")
        box = Gtk.Box(spacing=8, margin_start=4, margin_end=4, margin_top=2, margin_bottom=2)
        box.append(widgets.time_tip(Gtk.Label(label=what, xalign=0, wrap=True, max_width_chars=64,
                                              wrap_mode=Pango.WrapMode.WORD_CHAR), item.get("at"), item.get("ago")))
        if from_chat:
            btn = Gtk.Button(label="To notes", valign=Gtk.Align.CENTER)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_: (self.hide_card(), self.diary_ref(item["surface"], item.get("label"))))
            box.append(btn)
        pop.set_child(box)
        stay = Gtk.EventControllerMotion()  # (onto the card, for To notes: it stays; off it, it goes)
        stay.connect("enter", lambda *_: self._card_stay())
        stay.connect("leave", lambda *_: self.hover_left())
        pop.add_controller(stay)
        pop.surface = item["surface"]
        pop.set_parent(widget)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 14  # (below the pointer, clear of its arrow)
        pop.set_pointing_to(rect)
        self.card = pop
        pop.popup()

    def viewer_folder(self):
        """The folder the viewers show (viewers.ViewerHost): the one a name was last opened from (viewer_at), else
        the notebook's first, else the chat open's; None without one."""
        folders = self.read_folders()
        return self.viewer_at if self.viewer_at in folders else folders[0] if folders else None

    def git_chat(self):
        """The chat the Git page keeps its state for (GitPage.chat_check): the notebook's."""
        return (self.current or {}).get("id")

    def viewers(self, folder=None):
        """The viewers on a folder in a window of their own (viewers.ViewerWindow), not Record's: for when they're
        wanted apart. None, said, if there's no folder."""
        folder = folder or self.viewer_folder()
        if not folder:
            self.toast(f"Attach a folder to this {book()} first")
            return None
        return viewers.ViewerWindow.for_folder(folder, self.get_application())

    def open_viewer(self, page):
        """☰ → Kubernetes… or Databases… (over the window), or a page of the right bar's (files, git)."""
        if page == "kube":
            self.kube.open()
        elif page == "db":
            self.schema.open()
        else:
            self.right_slide.set_shown(True)
            self.right_stack.set_visible_child_name(page)

    def open_palette(self):
        """⌘K / Ctrl+K, as Baude's: every name the notebook's folders have, and the notebooks and their pages, searched
        as you type, a name opened where it is (open_item: its page, its file, the Git page, the Kubernetes view, the
        database diagram)."""
        space = self.space()
        if space is None:
            return self.toast(f"Attach a folder to this {book()} first")
        widgets.RefPalette(self, space, self.open_item).present(self)

    def open_notes_item(self, item):
        """A notebook (shown) or a page (open, in its notebook) from @ or ⌘K."""
        if item.get("notebook") != (self.current or {}).get("id"):
            self.select(item.get("notebook"))
        if item.get("kind") == "page" and self.current is not None and item.get("page") in self.current["pages"]:
            self.open_note(item["page"])

    def set_agent_model(self, model):
        """Settings → Intelligence: the model the Record's agent runs on, from its next message (RecordChat.submit)."""
        self.store.agent_model = model
        self.store.save()
        self.chat.model_changed()

    def open_item(self, item):
        """An @name opened where it is, in Record's tabs and views as Baude's ⌘K opens one: its file on the Code tab,
        a branch on the Git tab, a Kubernetes object in its view, a table in the database diagram; else its file in
        its app."""
        if item.get("kind") in NotesNames.KINDS:  # (a notebook, or a page of one)
            return self.open_notes_item(item)
        if item.get("kind") == "chat":  # (a chat by its title: on the Read tab)
            return self.open_chat(item["path"])
        if (listed := (item.get("entry") or {}).get("github")) is not None:  # (a pull request or an issue)
            return fixview.open_github(self, listed)
        if (root := item.get("root")) and root != self.viewer_folder():  # (one of the notebook's other folders)
            self.viewer_at = root
            self.viewer_folder_changed()
        if viewers.ViewerHost.open_item(self, item):
            return
        if item.get("path") and os.path.exists(item["path"]):
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(item["path"], None), None)

    def diary_ref(self, surface, label=None):
        """A name from the chat, written into the page of notes last shown (else Home) as "- @name: ", the caret
        after it for what it means to you."""
        editor = self.diary
        if editor is None:
            return
        self.tabs.show(editor.tab_key)
        buf = editor.buf
        text = editor.kept()
        at = editor.buf_offset(len(text), past=False)  # (the page's end: before an addition proposed there)
        written = ("" if not text or text.endswith("\n") else "\n") + f"- {ref_text(surface, label)}: "
        buf.insert(buf.get_iter_at_offset(at), written)
        buf.place_cursor(buf.get_iter_at_offset(at + len(written)))
        editor.view.grab_focus()
        editor.follow_soon()

    def read_folders(self, chat=None):
        """The folders a chat's names are from (chat: the one shown, or last shown): the notebook's, and its own (its
        links, a name in it opened)."""
        chat = chat or (self.reader.chat if self.reader is not None else None) or self.store.reading or {}
        folders, cwd = attached_folders(self.current), chat.get("cwd")
        if cwd and os.path.isdir(cwd) and os.path.realpath(cwd) not in folders:
            folders.append(os.path.realpath(cwd))
        return folders

    # ---- what's open: a tab each (a page of notes, a chat, a file's code)
    def open_pages(self):
        """The notebook's pages open, in their tabs' order."""
        return [c.page for c in self.tabs.children() if isinstance(c, DiaryEditor)]

    def open_note(self, name, select=True):
        """A page of the notebook's (made if there's none yet: a [[link]] followed, New page) in a tab of its own,
        shown (select); one open already, shown. Returns its editor (None without a notebook)."""
        notebook = self.current
        if notebook is None:
            return None
        name = (name or "").strip() or HOME
        self.store.flush()  # (the page left is on the disk)
        if name not in notebook["pages"]:
            notebook["pages"][name] = ""
            self.store.save()
        editor = self.tabs.open(("note", name), lambda: self._note(notebook, name), short_page(name),
                                icon="accessories-dictionary-symbolic", tooltip=name, select=select)
        self._keep_open()
        self.pages.fill()
        return editor

    def _note(self, notebook, name):
        editor = DiaryEditor(self, notebook, name)
        set_zoom_class(editor, zoom_class(self.store.zoom))
        on = self.store.full_width
        editor.clamp.set_maximum_size(100_000 if on else editor.WIDTH)
        editor.clamp.set_tightening_threshold(100_000 if on else 400)
        return editor

    def open_chat(self, path, select=True, present=True):
        """A chat to read (an @chat in the notes, ⌘K, Open a Chat…, record.py --chat from Baude's Open in Record) in a
        tab of its own, whichever notebook's open, kept open for the next time Record starts; one open already,
        shown. Nothing's made or attached for it: a notebook has no chat. Returns its Reader."""
        path = os.path.realpath(path)
        title, cwd = session_info(path)
        chat = {"path": path, "title": title or "Untitled chat", "cwd": cwd}
        reader = self.tabs.open(("chat", path), lambda: self._reader(), chat["title"],
                                icon="baude-claude-symbolic", tooltip=f"{chat['title']} · {short_dir(cwd or path)}",
                                select=select)
        reader.show(chat, self.read_folders(chat))
        if select:
            self.store.reading = chat
        self._keep_open()
        self.store.save_now()  # (open for the next start, whatever happens next)
        if present:
            self.present()
        return reader

    def open_citation(self, url):
        """A citation link followed from the notes: its chat open, at its passage (Reader.show_passage)."""
        cite = parse_citation(url)
        if (path := session_path(cite["session"]) if cite else None) is None:
            return self.toast("That chat isn't on this computer")
        self.open_chat(path).show_passage(cite)

    def _reader(self):
        reader = Reader(self)
        set_zoom_class(reader.box, zoom_class(self.store.zoom))
        on = self.store.full_width
        reader.clamp.set_maximum_size(100_000 if on else reader.WIDTH)
        reader.clamp.set_tightening_threshold(100_000 if on else 400)
        return reader

    def show_code(self, path, line=None, text=None, note=None):
        """A file (or a version's text) in a tab of its own (one open already: shown, at line), as Baude shows code.
        False if it isn't something to read here (a folder, a binary or very big file): its opener then opens it in
        the editor."""
        if text is None:
            if os.path.isdir(path):
                return False
            try:
                with open(path, "rb") as f:
                    data = f.read(viewers.CODE_LIMIT + 1)
            except OSError:
                return False
            if len(data) > viewers.CODE_LIMIT or b"\0" in data[:8192]:
                return False
            text = data.decode("utf-8", "replace")
        name = os.path.basename(path) + (f" ({note})" if note else "")
        page = self.tabs.open(("code", os.path.realpath(path), note or ""), lambda: viewers.CodePage(self), name,
                              icon=widgets.themed_icon("text-x-script-symbolic", "text-x-generic-symbolic"),
                              tooltip=path, quiet=True)
        page.show(path, text, line, note)
        self.tabs.remember()  # (shown at another line: a step back and forward go through)
        self.present()
        return True

    def image_source(self):
        """What the large image view goes through with ← and →: the tab shown (a chat, a fix's images in the Feedback tab)."""
        return self.tabs.current() or self.chat

    def open_fixes(self, select=True):
        """☰ → Ongoing Feedback, as Baude's: the feedback on Baude and Record and where each stands, in a tab of its own
        (fixview.FixesPage), opened or shown."""
        return self.tabs.open(("fixes",), lambda: fixview.FixesPage(self, claude_exe()), "Feedback",
                              icon="mail-send-symbolic", tooltip="The feedback on Baude, and where each stands",
                              select=select)

    def reopen_tab(self, key, line):
        """What was shown, gone back or forward to (MainTabs.back, forward), opened again if its tab's closed: a
        page of this notebook's, a chat, the feedback, a file's code at its line. False if it can't be (gone, another
        notebook's)."""
        if key[0] == "fixes":
            return bool(self.open_fixes())
        if key[0] == "note":
            return self.current is not None and key[1] in self.current["pages"] and self.open_note(key[1]) is not None
        if key[0] == "chat":
            return os.path.exists(key[1]) and self.open_chat(key[1], present=False) is not None
        if key[0] == "code":
            if key[2]:  # (a version's text: only while its tab's open)
                return self.tabs.show(key)
            return os.path.exists(key[1]) and self.show_code(key[1], line)
        return False

    def go_up(self):
        """Up (⌥⌘↑): from a sub-page, the page it's under; from a file's code, its folder on the Files page, the
        file selected. False from anything else."""
        page = self.tabs.current()
        if isinstance(page, DiaryEditor) and ":" in page.page:
            return self.open_note(page.page.rpartition(":")[0]) is not None
        if isinstance(page, viewers.CodePage) and page.path and os.path.exists(page.path):
            return self.show_folder(os.path.dirname(page.path), page.path)
        return False

    def show_folder(self, path, select=None):
        self.right_slide.set_shown(True)
        return viewers.ViewerHost.show_folder(self, path, select)

    def show_git(self, rev=None, sha=None, folder=None):
        self.right_slide.set_shown(True)
        return viewers.ViewerHost.show_git(self, rev, sha, folder)

    def find_in_code(self):
        """⌘F / Ctrl+F in a code tab: find in its file; on a page of notes: in its text. False if neither's shown."""
        page = self.tabs.current()
        if isinstance(page, viewers.CodePage) and page.code.get_visible():
            page.code.open_search()
            return True
        if isinstance(page, DiaryEditor):
            page.open_search()
            return True
        return False

    def zoom_code(self, steps):
        """Ctrl/⌘ + − 0 in a code tab (the focus anywhere but the conversation, whose text they size), or with the
        focus on code (the Kubernetes view's YAML): the code's own size, as Baude's, steps up or down (0: 1.0 again).
        False otherwise, where they size the notes, the chat read and the conversation."""
        focus = self.get_focus()
        in_chat = focus is not None and (focus is self.chat or focus.is_ancestor(self.chat))
        if in_chat or not (isinstance(self.tabs.current(), viewers.CodePage) or viewers.in_code(focus)):
            return False
        viewers.CodeView.set_zoom(viewers.CodeView.zoom + steps * widgets.ZOOM_STEP if steps else 1.0)
        return True

    def _last(self, kind):
        """The tab of a kind shown, else the one shown last of it, else None."""
        current = self.tabs.current()
        if isinstance(current, kind):
            return current
        seen = [c for c in self.shown_order if isinstance(c, kind) and c in self.tabs.children()]
        return seen[-1] if seen else next((c for c in reversed(self.tabs.children()) if isinstance(c, kind)), None)

    @property
    def reader(self):
        """The chat shown, else the one shown last (its Reader); None with none open."""
        return self._last(Reader)

    @property
    def code_page(self):
        """The code tab shown, else the one shown last (viewers.CodePage); None with none open."""
        return self._last(viewers.CodePage)

    @property
    def diary(self):
        """The page of notes shown, else the one shown last, else Home, opened (its DiaryEditor); None without a
        notebook."""
        return self._last(DiaryEditor) or self.open_note(HOME, select=False)

    def diary_shown(self):
        """The page of notes shown, else the one shown last (no page opened for it); None."""
        return self._last(DiaryEditor)

    def _tab_shown(self):
        """A tab shown: what's in it the one shown (a chat's names put in place; a page remembered, lit in the list);
        nothing open, so said."""
        child = self.tabs.current()
        if child is not None:
            self.shown_order = [c for c in self.shown_order if c is not child][-40:] + [child]
        self.zoom_tab(child)  # (it may have been left at another size: at this one in its first frame)
        if isinstance(child, Reader):
            child.activate()
            if child.chat:
                self.store.reading = child.chat
        elif isinstance(child, DiaryEditor) and self.current is not None and child.notebook is self.current:
            self.current["page"] = child.page
            if self.pages.notebook is self.current:
                self.pages.fill()
        if self.current is None and not isinstance(child, fixview.FixesPage):  # (the feedback needs no notebook)
            self.content_stack.set_visible_child_name("none")
        else:
            self.content_stack.set_visible_child_name("tabs" if self.tabs.children() else "empty")

    def _tab_closed(self, key, child):
        """A tab closed: what's in it stopped (a chat's reading, a page's timer), and what's open kept."""
        self.shown_order = [c for c in self.shown_order if c is not child]
        if hasattr(child, "stop"):
            child.stop()
        if isinstance(child, fixview.FixesPage):
            widgets.discard(child)  # (its images, and all that holds it)
        if self.switching:  # (another notebook's: what was open is kept as it was)
            return
        if isinstance(child, DiaryEditor):
            self.pages.prune()
            self.pages.fill()
        self._keep_open()
        self._tab_shown()

    def _keep_open(self):
        """What's open kept (records.json): the notebook's pages, and the chats, in their tabs' order."""
        if self.switching:
            return
        if self.current is not None:
            self.current["open"] = self.open_pages()
        self.store.chats = [c.chat for c in self.tabs.children() if isinstance(c, Reader) and c.chat]
        self.store.save()

    def delete_page(self, name):
        """A page deleted (its menu in the list), asked first: its tab closed; links to it stay, and make it again
        when followed."""
        notebook = self.current
        if notebook is None or name == HOME:
            return
        d = Adw.AlertDialog(heading=f"Delete {short_page(name)}?")
        d.add_response("cancel", "Cancel")
        d.add_response("delete", "Delete")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, r):
            if r == "delete" and name in notebook["pages"]:
                self.tabs.close(("note", name))
                notebook["pages"].pop(name, None)
                self.store.save()
                self.pages.fill()
        d.connect("response", done)
        d.present(self)

    def note_renamed(self, moved):
        """Pages renamed ({old: new}, a page and its sub-pages: DiaryEditor.rename_page): their tabs with them, every
        page open shown again (its links renamed), the list again."""
        for old, new in moved.items():
            if (editor := self.tabs.get(("note", old))) is not None:
                editor.page = new
                self.tabs.retitle(("note", old), short_page(new), new_key=("note", new), tooltip=new)
                editor.load()
        for child in self.tabs.children():
            if isinstance(child, DiaryEditor):
                child.reload()
        if self.current is not None and self.current.get("page") in moved:
            self.current["page"] = moved[self.current["page"]]
        self._keep_open()
        self.pages.fill()

    def let_go(self):
        """Before the window goes (a window made again in its place): saved, and its timers stopped (each holds the window, and the
        reader's would read the chat every 1.2s for good)."""
        self.store.save_now()
        Gtk.StyleContext.remove_provider_for_display(self.get_display(), self.zoom_css)  # (the new window's own)
        for wait in ("zoom_saving", "scale_wait", "rolling"):
            if getattr(self, wait) is not None:
                GLib.source_remove(getattr(self, wait))
                setattr(self, wait, None)
        if self.zoom_wait is not None:
            self.get_frame_clock().disconnect(self.zoom_wait)
            self.zoom_wait = None
        self.chat.place.stop()
        self.chat.close_agent()  # (its session too)
        for child in self.tabs.children():  # (a chat's reading, a page's timer)
            if hasattr(child, "stop"):
                child.stop()

    def select(self, tid):
        i = 0
        while (row := self.notebook_list.get_row_at_index(i)) is not None and row.notebook["id"] != tid:
            i += 1
        if row is not None:
            self.notebook_list.select_row(row)

    def _toast(self, toast):
        self.toasts.add_toast(toast)


# ---------------------------------------------------------------- the logo
# Baude's speech bubble, in green, holding three lines that fade from the newest: the three messages it remembers.

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect x="8" y="8" width="112" height="112" rx="26" fill="#4f8a7c"/>
  <path d="M30 38h68a8 8 0 0 1 8 8v36a8 8 0 0 1-8 8H58l-18 14v-14H30a8 8 0 0 1-8-8V46a8 8 0 0 1 8-8z"
        fill="#fff8f0"/>
  <path d="M38 52h52" stroke="#4f8a7c" stroke-width="7" stroke-linecap="round" opacity="0.3"/>
  <path d="M38 64h42" stroke="#4f8a7c" stroke-width="7" stroke-linecap="round" opacity="0.6"/>
  <path d="M38 76h28" stroke="#4f8a7c" stroke-width="7" stroke-linecap="round"/>
</svg>
"""


def icon_png(size=1024, macos=True):
    """The logo as a PNG; for macOS with the margin its own icons have (the tile 824 of 1024)."""
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    svg = ICON_SVG
    if macos:
        k = 112 * 1024 / 824  # (the tile is 112 of the SVG's 128)
        svg = svg.replace('viewBox="0 0 128 128"', f'viewBox="{64 - k / 2} {64 - k / 2} {k} {k}"')
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(size, size)
    loader.write(svg.encode())
    loader.close()
    return loader.get_pixbuf().save_to_bufferv("png", [], [])[1]


def objc_send(obj, sel, *args, types=(), ret=None):
    """[obj sel...] through the Objective-C runtime (there's no PyObjC): obj a class name (bytes) or an object;
    types the arguments' ctypes types."""
    import ctypes
    rt = objc_send.__dict__.get("rt")
    if rt is None:
        rt = objc_send.rt = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
        rt.objc_getClass.restype = rt.sel_registerName.restype = ctypes.c_void_p
        rt.objc_getClass.argtypes = rt.sel_registerName.argtypes = [ctypes.c_char_p]
    if isinstance(obj, bytes):
        obj = rt.objc_getClass(obj)
    f = ctypes.CFUNCTYPE(ret or ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, *types)(
        ctypes.cast(rt.objc_msgSend, ctypes.c_void_p).value)
    return f(obj, rt.sel_registerName(sel), *args)


def macos_app_name():
    """The menu bar says Record rather than Python: the bundle's name is read when the app
    starts, so it's renamed in memory before GTK starts it (and stays what it was then)."""
    import ctypes

    def ns(text):
        return objc_send(b"NSString", b"stringWithUTF8String:", text.encode(), types=(ctypes.c_char_p,))
    bundle = objc_send(b"NSBundle", b"mainBundle")
    for which in (b"infoDictionary", b"localizedInfoDictionary"):
        if info := objc_send(bundle, which):
            objc_send(info, b"setObject:forKey:", ns(noun()), ns("CFBundleName"),
                      types=(ctypes.c_void_p, ctypes.c_void_p))


def macos_dock_icon():
    """The Dock shows the logo instead of Python's rocket (GTK's icon names don't reach a Python process's tile)."""
    import ctypes
    png = icon_png()
    data = objc_send(b"NSData", b"dataWithBytes:length:", png, len(png), types=(ctypes.c_char_p, ctypes.c_ulong))
    image = objc_send(objc_send(b"NSImage", b"alloc"), b"initWithData:", data, types=(ctypes.c_void_p,))
    objc_send(objc_send(b"NSApplication", b"sharedApplication"), b"setApplicationIconImage:", image,
              types=(ctypes.c_void_p,))


def themed_icon():
    """Elsewhere, windows take the logo from the icon theme: it's put in an icon folder of Record's own."""
    root = os.path.join(data_dir(), "icons")
    path = os.path.join(root, "hicolor", "scalable", "apps", f"{APP_ID}.svg")
    try:
        if not os.path.exists(path) or open(path).read() != ICON_SVG:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(ICON_SVG)
    except OSError:
        return
    Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(root)
    Gtk.Window.set_default_icon_name(APP_ID)


# ---------------------------------------------------------------- in the list of apps

def launch_command():
    return [sys.executable, os.path.realpath(__file__)]


def launchers(app_id, name):
    """Where install puts an app's launcher: its .app (macOS), else its .desktop entry and its logo."""
    if sys.platform == "darwin":
        return [os.path.expanduser(f"~/Applications/{name}.app")]
    share = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return [os.path.join(share, "applications", f"{app_id}.desktop"),
            os.path.join(share, "icons", "hicolor", "scalable", "apps", f"{app_id}.svg")]


def remove(paths):
    """The paths that were there, removed."""
    there = [p for p in paths if os.path.exists(p)]
    for p in there:
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    return there


def install():
    """macOS: ~/Applications/Record.app (an .icns of the logo, started through the login shell for Homebrew's PATH).
    Linux: a .desktop entry and the logo in the user's hicolor theme."""
    import shlex
    import struct
    if sys.platform == "darwin":
        import plistlib
        app = os.path.expanduser("~/Applications/Record.app")
        contents = os.path.join(app, "Contents")
        os.makedirs(os.path.join(contents, "MacOS"), exist_ok=True)
        os.makedirs(os.path.join(contents, "Resources"), exist_ok=True)
        with open(os.path.join(contents, "Info.plist"), "wb") as f:
            plistlib.dump({"CFBundleName": "Record", "CFBundleDisplayName": "Record", "CFBundleIdentifier": APP_ID,
                           "CFBundlePackageType": "APPL", "CFBundleExecutable": "Record", "CFBundleIconFile": "Record",
                           "LSApplicationCategoryType": "public.app-category.productivity",
                           "NSHighResolutionCapable": True}, f)
        entries = b"".join(kind + struct.pack(">I", 8 + len(png)) + png for kind, png in
                           ((k, icon_png(n)) for k, n in ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
                                                          (b"ic10", 1024))))
        with open(os.path.join(contents, "Resources", "Record.icns"), "wb") as f:
            f.write(b"icns" + struct.pack(">I", 8 + len(entries)) + entries)
        script = os.path.join(contents, "MacOS", "Record")
        with open(script, "w") as f:
            f.write('#!/bin/sh\ncd "$HOME"\nexec "${SHELL:-/bin/zsh}" -lc \'exec "$0" "$@"\' '
                    + " ".join(shlex.quote(a) for a in launch_command()) + ' "$@"\n')
        os.chmod(script, 0o755)
        lsregister = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
                      "/Support/lsregister")
        if os.path.exists(lsregister):  # (listed now, ⌘Space too: they find it in time, a minute or more)
            subprocess.run([lsregister, "-f", app], capture_output=True, check=False)
        if shutil.which("mdimport"):
            subprocess.run(["mdimport", app], capture_output=True, check=False)
        return app
    share = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    icon = os.path.join(share, "icons", "hicolor", "scalable", "apps", f"{APP_ID}.svg")
    desktop = os.path.join(share, "applications", f"{APP_ID}.desktop")
    entry = "\n".join(["[Desktop Entry]", "Type=Application", "Name=Record",
                       "Comment=Read AI prose with a peer who knows what you know",
                       "Exec=" + " ".join(shlex.quote(a) for a in launch_command()), f"Icon={APP_ID}",
                       "Terminal=false", "Categories=Utility;GTK;", f"StartupWMClass={APP_ID}", ""])
    for path, data in ((icon, ICON_SVG), (desktop, entry)):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(data)
    return desktop


def installed():
    """Whether Record is in the platform's list of apps (Settings → Show in Applications)."""
    return os.path.exists(launchers(APP_ID, "Record")[0])


def uninstall():
    """Record's launcher: what was removed."""
    return remove(launchers(APP_ID, "Record"))


def socket_path():
    return os.path.join(data_dir(), "record.sock")


def restart_path():
    """What a Record restarting (☰ → Restart Record) leaves for the one it starts: Window.state, as JSON."""
    return os.path.join(data_dir(), "restart.json")


def hand_over(request, path=None):
    """Record is running already: hand it the request ({"chat": path} or {}). False if none is. (path: another
    socket's)"""
    import socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.settimeout(2)
            c.connect(path or socket_path())
            c.sendall(json.dumps(request).encode() + b"\n")
            return c.recv(16) == b"ok\n"
    except OSError:
        return False


def listen(on_request):
    """Be the Record that's running: a later start's request goes to on_request(dict), on the main loop. (Two would
    each save records.json over the other's.)"""
    import socket
    path = socket_path()
    if os.path.exists(path):
        os.remove(path)  # (left by a Record that's gone: hand_over couldn't reach it)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(4)

    def serve():
        while True:
            conn, _ = server.accept()
            with conn:
                try:
                    request = json.loads(conn.makefile().readline() or "{}")
                    conn.sendall(b"ok\n")
                except (OSError, ValueError):
                    continue
            GLib.idle_add(lambda r=request: on_request(r) and False)
    threading.Thread(target=serve, daemon=True, name="record-listen").start()
    return server


class App(Adw.Application):
    def __init__(self, chat=None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        GLib.set_application_name(noun())
        self.chat, self.server, self.win = chat, None, None
        self.updater = None  # (update.Updater: started with the window)

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.do_startup_look()
        pointers.start()
        import signal  # (told to stop, as logging out or `kill` does: what's typed is saved, then it stops)
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            try:
                gi.require_version("GLibUnix", "2.0")
                from gi.repository import GLibUnix
                GLibUnix.signal_add(GLib.PRIORITY_HIGH, sig, self._stop)
            except (ImportError, ValueError):  # older GLib
                GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, self._stop)

    def do_startup_look(self):
        """Light or dark as macOS is, as Baude follows it (widgets.macos_appearance), and Baude's sizes."""
        if sys.platform == "darwin":
            try:
                widgets.macos_appearance()
            except Exception as e:  # noqa: BLE001 (the look isn't worth not starting over)
                print("record: light and dark won't follow macOS:", e, file=sys.stderr)

    def _stop(self, *_):
        if self.win is not None:
            self.win.store.save_now()
        self.quit()
        return False

    def do_shutdown(self):
        if self.win is not None:
            self.win.store.save_now()  # (⌘Q, or the last window closed: what's typed is kept)
        UNEXPLAINED.stop()
        Adw.Application.do_shutdown(self)

    def do_activate(self):
        widgets.load_css()  # (Baude's: its compact lists, its viewers; its classes aren't Record's)
        widgets.apply_scales(Gdk.Display.get_default())  # (Settings → Size: the text's and the icons', Baude's)
        widgets.apply_accent(Gdk.Display.get_default())  # (Settings → Accent Colour, Baude's)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if sys.platform == "darwin":
            try:
                macos_dock_icon()
            except Exception as e:
                print("record: no Dock icon:", e, file=sys.stderr)
        else:
            themed_icon()
        self.win = Window(self)
        state = None
        try:  # (restarted, ☰ → Restart Record or a new version: what the one before showed)
            with open(restart_path()) as f:
                state = json.load(f)
            os.remove(restart_path())
            self.win.restore(state)
        except (OSError, ValueError):
            pass
        update.show(self.win, state if isinstance(state, dict) else None)
        if isinstance(state, dict):
            update.restarted(self.win, state, noun())
        if self.updater is None:
            self.updater = update.Updater("record", lambda: self.win is None or self.win.idle(),
                                          lambda updated: self.win.restart(updated),
                                          blocked=lambda waiting: self.win is not None and self.win.update_waiting(waiting))
        if self.chat:
            self.win.open_chat(self.chat)
        try:
            self.server = listen(lambda r: (self.win.open_chat(r["chat"]) if r.get("chat")
                                                                  else self.win.present()))
        except OSError as e:
            print("record: another start won't find this one:", e, file=sys.stderr)


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="record", description="Read AI prose with a peer who knows what you know.")
    ap.add_argument("--chat", help="a Claude Code transcript (.jsonl) to read")
    ap.add_argument("--install", action="store_true", help="add Record to your applications (per user)")
    ap.add_argument("--uninstall", action="store_true", help="remove it from them")
    args = ap.parse_args()
    if args.install:
        print("Installed:", install())
        return 0
    if args.uninstall:
        print("Removed:", ", ".join(uninstall()) or "nothing (not installed)")
        return 0
    chat = os.path.realpath(args.chat) if args.chat else None
    request = {"chat": chat} if chat else {}
    if hand_over(request):
        return 0
    if sys.platform == "darwin":
        try:
            macos_app_name()
        except Exception as e:
            print("record: still named Python:", e, file=sys.stderr)
    return App(chat).run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
