"""recordstore — Record's notebooks, without a window: its names and nouns, a page's [[links]], headings and
files, the hosts whose notebooks it lists (network), the notebooks themselves (Store: records.json), Claude Code's
transcripts and the passages cited from them, the logo's SVG, and Record in the list of apps (install, uninstall).
"""
import copy
import glob
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.parse
import uuid

from gi.repository import GLib  # noqa: E402

import lookupgit  # noqa: E402
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; beside this file)
import refkinds  # noqa: E402
import markdownview  # noqa: E402
import chatparts  # noqa: E402
from baudelib import wording  # noqa: E402  (its words, Baude's: schema/copy)
from baudelib.transcripts import (  # noqa: E402
    ref_epoch, rewind_mark, started_outside, strip_reminders, task_notification, typed_at,
)
from baudelib.claude import MODELS  # noqa: E402  (the models Claude Code runs)
import baudelib.client  # noqa: E402  (the hosts, as Baude has them: another's notebooks, through its host process)
import baudelib.hosts  # noqa: E402  (this machine's host process started again: Restart Daemon)
import baudelib.serverstate  # noqa: E402  (the sessions this machine's host process keeps: bot_started)
from baudelib import applist  # noqa: E402  (Record in the list of apps, as Emission is)
from baudelib import notes  # noqa: E402  (this machine's notebooks, as its host process gives them to another's Record)
from baudelib.notes import data_dir  # noqa: E402,F401
import meta  # noqa: E402  (Meta, the helper in the right bar: Baude's too, beside this file)


APP_ID = "dev.baude.Record"
QUOTE_LIMIT = 6000


def noun(plural=False):
    """What the app, and the one reading with you, is called."""
    return "Record" + ("s" if plural else "")


def book(plural=False):
    """What there's one of per subject (its notes)."""
    return "Notebook" + ("s" if plural else "")


def base_name(text):
    """The part of a name you choose: its name, its spaces evened out."""
    return re.sub(r"\s+", " ", text.strip())


def full_name(notebook):
    return base_name(notebook["name"]) or "Untitled"


def claude_exe():
    """RECORD_CLAUDE, else claude on PATH, else (macOS) the newest one the Claude desktop app keeps (baudelib's)."""
    return baudelib.claude.claude_exe("RECORD_CLAUDE")


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
# linked with [[Page]], and @name for anything Baude's ⌘K can find (files, branches, tables… of the chat's folder).

HOME = "Home"
TO_LEARN = "To learn"  # the heading words you've heard but don't understand yet go under
LINK_RE = re.compile(r"\[\[([^\]|\n]+)(?:\|([^\]\n]*))?\]\]")
# what Settings → Markup visibility offers: the notes' markup (# ** ` > the - of a list…) shown on the caret's line, never,
# or always (a link's [[ ]] and address kept hidden)
MARKUP_MODES = {"line": "Show formatting on current line", "none": "Don't show formatting",
                "all": "Show all formatting (Except Links)"}
REF_RE = meta.REF_RE  # (@name, @{a name})
ISSUE_OPENS = re.compile(r"[\s(\[{<]")  # (what a # that's asking for GitHub's list comes after, if anything does)
# (a line's quote and list marks, and a task's box: a # after them is first on the line, as far as it's concerned)
ISSUE_LEAD = re.compile(r"\s*(?:>\s*)*(?:(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)?")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)
LIST_ITEM_RE = re.compile(r"(\s*)([-*+]|\d+[.)])(\s+)(?:\[([ xX])\]\s+)?")  # (an item: its marker, its box)
MD_LINK_RE = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\s]+)\)")
URL_RE = re.compile(r"\bhttps?://[^\s<>\"]+[^\s<>\".,;:!?)]")
LINKING_RE = re.compile(r"(?:^|(?<=\s))\+([\w.\-:]*)$")  # +, and what's typed after it: +'s list of pages
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


def relinked_pages(pages, moved):
    """pages ({name: text}) with moved's renamed ({old: new}: a page and its sub-pages, DiaryEditor.rename_page's or
    Meta's move_pages) and every [[link]] to any of them following; a +link stays one where it still can (its page's
    sub-page)."""
    def relinked(text, here, there):  # (here: the page the text is in, before; there: after)
        def relink(m):
            to = page_target(here, m.group(1))
            if to not in moved:
                return m.group(0)
            written = page_link(there, moved[to]) if m.group(1).strip().startswith("+") else moved[to]
            return f"[[{written}|{m.group(2)}]]" if m.group(2) is not None else f"[[{written}]]"
        return LINK_RE.sub(relink, text)
    return {moved.get(n, n): relinked(text, n, moved.get(n, n)) for n, text in pages.items()}


def heading_levels(lines):
    """Each line's heading level, as the notes draw headings: 0 if it isn't one (nor is a # in a code fence)."""
    code = {n for first, last, _closed in markdownview.fence_spans(lines) for n in range(first, last + 1)}
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
        return notebook.get("host") or "", unicodedata.normalize("NFC", file_part(base_name(notebook["name"]))).casefold()
    names, taken = [key(notebook) for notebook in notebooks], set()
    return {notebook.get("id"): fresh_path(file_part(base_name(notebook["name"])) + (f" ({notebook.get('id')})" if names.count(key(notebook)) > 1 else ""), taken)
            for notebook in notebooks}


def page_paths(notebooks):
    """Where each page's file goes (Store.mirror_pages, export_zip): {(its notebook's id, its name): a path}, in the
    notebook's folder (book_folders), a sub-page in a folder named for its parent, as Zim keeps them; another host's
    (its "host": Store.take) in hosts/<its name>/. No two are the same."""
    taken, paths, books = set(), {}, book_folders(notebooks)
    for notebook in notebooks:
        for name in sorted(notebook["pages"]):
            folder = books[notebook.get("id")]
            if notebook.get("host"):
                folder = os.path.join("hosts", file_part(notebook["host"]) or "host", folder)
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
    return bool(m.group(2)) and bool(lookupgit.GITHUB_NAME.fullmatch(m.group(1).strip().removeprefix("@"))) and \
        m.group(1).lstrip().startswith("@")


plain_ref = meta.plain_ref  # (a name as plain @ has it: @name, or @{name})

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


HERE_HOST = "here"  # (the host whose notebooks are shown: this machine's key, as Baude's New session has it)
THIS_MACHINE = wording.say("senders.local")  # (this machine, by name, as Baude says it)
_NETWORK = []


def local_remote():
    """This machine's host process (its socket), if it's there."""
    return next((r for r in network().alive() if r.mine), None)


def network():
    """The hosts there are, as Baude knows them (baudelib.client.Network: this machine's host process, by its socket,
    and those it's linked to, and those given by where ssh goes): one for the app, started when it's first wanted."""
    if not _NETWORK:
        _NETWORK.append(baudelib.client.Network(  # (servers.json Baude's to keep: read here, not written)
            lambda fn: GLib.idle_add(lambda: fn() and False),
            hosts=[a for a in os.environ.get("BAUDE_HOSTS", "").split(",") if a.strip()], readonly=True))
    return _NETWORK[0]


def remote_name(r):
    return r.label


def host_key(r):
    """A host's key in the dropdown: this machine's, for a host process on it (its notebooks are records.json here);
    else its own."""
    return HERE_HOST if baudelib.client.is_here(r.info) else r.key


class Store:
    """Record's notebooks (records.json), and another host's while one's picked (Window.pick_host): notebooks is the
    picked host's, so all that reads and changes them works on those; a change to another's is sent to it as it's
    saved (push), and what it has is taken in as it's listed (take)."""

    UNSENT = "\0unsent"  # (in remote_base, for what a host wasn't sent after all: it differs, so it's sent again)

    def __init__(self):
        self.path = os.path.join(data_dir(), "records.json")
        self.host, self.picked = HERE_HOST, HERE_HOST  # (the host shown; the one picked, kept: Window.show_host)
        self.host_name = self.picked_name = THIS_MACHINE  # (by name: the one picked's kept too, for while it's unknown)
        self.local, self.remote, self.remote_base = [], {}, {}  # (this machine's; another's, by its key; as it has them)
        self.loading = set()  # (hosts whose notebooks are asked for, not listed yet)
        self.unsent = {}  # (host: a change to it wasn't taken: Window.send_notes's count then; sent again with the next)
        self.sender = None  # (fn(host, changes): a change sent to a host, Window.send_notes)
        self.notebooks, saved_notes = [], None
        self.full_width = not os.path.exists(self.path)  # (☰ → Full-width text, as Baude's: on for a new installation)
        self.page_files, self.mirrored = {}, {}  # (each page's file, and what's been written in them: mirror_pages)
        self.spell_check = True  # (☰ → Settings… → Check Spelling: the pages' squiggles, spelling.SpellCheck)
        self.backup_all = False  # (☰ → Settings…: the rolling zip with the glossary and the chats too: Window.roll_backup)
        self.unzipped = True  # (saved since that zip was made)
        self.reading = None  # (the chat last shown, whichever notebook's open: {path, title, cwd}; Window.open_chat)
        self.chats = []  # (the chats open, a tab each, in their order: [{path, title, cwd}])
        self.pages_folded = self.books_folded = False  # (the pages' list, and the notebooks', folded away)
        self.markup = "line"  # (the notes' formatting shown: "line", the caret's; "none"; "all", all but links')
        self.books_width = None  # (the notebooks' column, dragged; None: as it comes)
        self.zoom = self.zoom_default = 1.0  # (Ctrl/⌘ + − 0: the notes', the chat's and the conversation's text)
        self.meta = {"messages": []}  # (Meta's conversation, the window's, whichever notebook's open: RecordChat)
        try:
            with open(self.path) as f:
                saved = json.load(f)
                self.notebooks = saved.get("records", [])
                self.picked = saved.get("host") if isinstance(saved.get("host"), str) else HERE_HOST
                self.picked_name = saved.get("host_name") if isinstance(saved.get("host_name"), str) else self.picked
                self.full_width = bool(saved.get("full_width"))
                saved_notes = saved.get("notes")  # ("markdown": the notes are; else Zim's markup, from before)
                self.zoom = float(saved.get("zoom") or 1.0)
                self.zoom_default = float(saved.get("zoom_default") or 1.0)  # (from before it was Baude's too)
                self.books_width = saved.get("books_width")
                self.backup_all = bool(saved.get("backup_all"))
                self.spell_check = saved.get("spell_check") is not False
                self.reading = saved.get("reading")
                self.chats = [c for c in saved.get("chats") or ([self.reading] if self.reading else [])
                              if isinstance(c, dict) and c.get("path")]
                self.pages_folded = bool(saved.get("pages_folded"))
                self.books_folded = bool(saved.get("books_folded"))
                self.markup = saved.get("markup") if saved.get("markup") in MARKUP_MODES else "line"
                if isinstance(saved.get("meta"), dict):
                    self.meta = {**saved["meta"], "messages": list(saved["meta"].get("messages") or [])}
                chosen = saved.get("agent_model")  # (the model chosen for Record's own agent: Meta's, if it has none)
                if chosen in dict(MODELS) and chosen != meta.MODEL and "meta_model" not in widgets.load_settings():
                    widgets.save_settings(meta_model=chosen)
        except (OSError, ValueError, AttributeError):
            pass
        shared = widgets.load_settings().get("zoom_default")  # (the Global Scale, Baude's too: Record's own, kept there)
        if shared:
            self.zoom_default = round(min(widgets.ZOOM_MAX, max(widgets.ZOOM_MIN, float(shared))), 2)
        elif self.zoom_default != 1.0:
            widgets.save_settings(zoom_default=self.zoom_default)
        zim = bool(self.notebooks) and saved_notes != "markdown"  # (notes from before Markdown: Zim's markup, made so once)
        if zim:
            self.backup(force=True)  # (the file as it was, kept first)
        for t in self.notebooks:  # (a name is kept as it was typed: each mode shows it its own way, see full_name)
            if "pages" not in t:  # (a glossary from before the diary: its terms, as Home's list)
                t["pages"] = {HOME: glossary_page(t.pop("glossary", []))}
            elif zim:
                t["pages"] = {n: zim_to_markdown(text) for n, text in t["pages"].items()}
        chats = self._without_chats()
        self._pending = None
        self.on_saved = []  # (fn(saving): told when a save is coming, and when it's done)
        if zim or chats:
            self.save_now()  # (Markdown now, notebooks without chats: kept, not made so again)

    @property
    def notebooks(self):
        """The picked host's notebooks: this machine's, else another's as it last listed them (and changed here)."""
        return self.local if self.host == HERE_HOST else self.remote.setdefault(self.host, [])

    @notebooks.setter
    def notebooks(self, value):
        if self.host == HERE_HOST:
            self.local = value
        else:
            self.remote[self.host] = value

    def take(self, host, name, books, ids=None):
        """A host's notebooks as it's listed them ([notes.shared]; ids: every one's, in order, those not in books
        unchanged since it last had them): those known here changed in place (one open stays open), new ones added,
        gone ones taken out; each marked with the host's name (page_paths). What's been changed here meanwhile is
        kept, merged with what's changed there (notes.merge3), and sent with the next push. True if anything was."""
        have = self.remote.setdefault(host, [])
        by_id, base = {t.get("id"): t for t in have}, self.remote_base.get(host)
        listed = {b["id"]: b for b in books}
        out, now, merged = [], {}, False
        for i in (ids if ids is not None else list(listed)):
            t, b, was = by_id.get(i), listed.get(i), (base or {}).get(i)
            if b is None:  # (the same as it had it)
                if t is not None and was is not None:
                    out.append(t)
                    now[i] = was
                continue
            if t is None:
                t = {"id": b["id"], "name": b["name"], "pages": dict(b["pages"]), "messages": []}
            else:
                if was is None or t["name"] == was["name"]:  # (renamed here, and not sent yet: kept)
                    t["name"] = b["name"]
                pages = {p: self.merged_page(t["pages"].get(p), b["pages"].get(p),
                                             (was or {}).get("pages", {}).get(p, self.UNSENT) if was else None)
                         for p in set(t["pages"]) | set(b["pages"])}
                pages = {p: text for p, text in pages.items() if text is not None}
                merged |= pages != b["pages"] or t["name"] != b["name"]
                if t["pages"] != pages:
                    t["pages"].clear()  # (the same dict: what has the notebook keeps it)
                    t["pages"].update(pages)
            t["host"] = name
            out.append(t)
            now[i] = copy.deepcopy(b)
        if base is not None:  # (made here meanwhile, not sent yet: kept)
            made = [t for t in have if t.get("id") not in now and t.get("id") not in base]
            out[:0] = made
            merged |= bool(made)
        have[:] = out
        self.remote_base[host] = now
        self.loading.discard(host)
        if host == self.host:
            self.mirror_pages()
        return merged

    def merged_page(self, mine, theirs, was):
        """A page as it is here (mine) and as a host has it now (theirs), from what it had (was; None: nothing known
        of it, UNSENT: changed here and not sent): theirs where it's changed only there, mine where only here, both
        merged where both (a page deleted on one side and written on the other: written)."""
        if was is None or mine == was:
            return theirs
        if theirs == was:
            return mine
        if mine is None or theirs is None:
            return mine if mine is not None else theirs
        return notes.merge3("" if was == self.UNSENT else was, mine, theirs)  # (UNSENT: made on both: both kept)

    def books_of(self, host):
        """A host's notebooks, as listed here (this machine's, records.json's)."""
        return self.local if host == HERE_HOST else self.remote.setdefault(host, [])

    def every_book(self):
        """Every notebook known here: this machine's, and each host's listed."""
        return self.local + [t for books in self.remote.values() for t in books]

    def host_of(self, notebook):
        """The host a notebook's on (its key), or None if it's gone."""
        if any(t is notebook for t in self.local):
            return HERE_HOST
        return next((h for h, books in self.remote.items() if any(t is notebook for t in books)), None)

    def push(self, host=None):
        """A host's notebooks (the one shown's, if not said), if it's another: what's changed of them here since it
        last had them (or wasn't sent after all: failed), sent to it (sender): a notebook made, dropped, renamed, its
        pages set or deleted. True if anything was."""
        host = host or self.host
        if host == HERE_HOST or host in self.loading or self.sender is None:
            return False
        base = self.remote_base.get(host)
        if base is None:  # (never listed here: nothing known of what it has)
            return False
        now = {t["id"]: notes.shared(t) for t in self.remote.get(host, [])}
        changes = []
        for i, b in now.items():
            was = base.get(i)
            if was is None:
                changes.append({**b, "new": True})
                continue
            c = {"id": i}
            if b["name"] != was["name"]:
                c["name"] = b["name"]
            pages = {p: text for p, text in b["pages"].items() if was["pages"].get(p) != text}
            pages.update({p: None for p in was["pages"] if p not in b["pages"]})
            if pages:  # (with what it had of each, was: where it's changed there since, both kept: notes.apply)
                c["pages"] = pages
                c["was"] = {p: was["pages"][p] for p in pages if was["pages"].get(p) not in (None, self.UNSENT)}
            if len(c) > 1:
                changes.append(c)
        changes += [{"id": i, "drop": True} for i in base if i not in now]
        if changes:
            self.remote_base[host] = copy.deepcopy(now)
            self.sender(host, changes)
        return bool(changes)

    def failed(self, host, changes):
        """Changes a host didn't take: marked in what it's said to have (remote_base), so the next push sends them
        again (a notebook made: made again; dropped: dropped again; a name, a page: set again, with what it had of the
        page then, so what's been written there since is kept too), and nothing else."""
        base = self.remote_base.setdefault(host, {})
        for c in changes:
            if c.get("new"):
                base.pop(c["id"], None)
                continue
            if c.get("drop"):
                base.setdefault(c["id"], {"id": c["id"], "name": self.UNSENT, "pages": {}})
                continue
            was = base.setdefault(c["id"], {"id": c["id"], "name": self.UNSENT, "pages": {}})
            if "name" in c:
                was["name"] = self.UNSENT
            for p in c.get("pages") or {}:
                was["pages"][p] = (c.get("was") or {}).get(p, self.UNSENT)

    def apply_here(self, changes):
        """Changes another machine's Record made to this machine's notebooks (handed over by the host process here:
        notes.write): made, and saved. The ids changed."""
        done = notes.apply(self.local, changes)
        self.save_now()
        return done

    def _without_chats(self):
        """Notebooks from when each was attached to a chat, made as they are now (a notebook has no chat: Read shows
        the one last opened, whichever notebook's open), once, the file kept first: the first one's chat the one Read
        shows (its folder the one @ knows the names of), and a notebook made only for its chat (blank: nothing written
        or asked) gone. True if there were any."""
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
            blank = not any(text.strip() for text in t["pages"].values()) and not t.get("messages")
            if chat.get("path") and blank and t.get("fresh"):  # (one from before "fresh": kept)
                self.notebooks.remove(t)
        if self.reading and not self.chats:  # (that chat open, in a tab of its own)
            self.chats = [self.reading]
        return True

    SAVE_MS = 250  # how soon a change is written: as you type, as a document online saves
    BACKUP_EVERY, BACKUPS = 600, 50  # a copy of what was saved before, at most every 10 minutes; the last 50 kept

    def new(self, name, host=None, host_name=None):
        """A new one, "fresh" until it's named or anything's put in it (is_blank: a blank one isn't kept); on the host
        shown (or host, by its key, and name)."""
        host = host or self.host
        t = {"id": uuid.uuid4().hex[:12], "name": name, "created": time.time(), "pages": {HOME: ""},
             "messages": [], "fresh": True}
        if host != HERE_HOST:
            t["host"] = host_name or self.host_name  # (its pages' files with that host's: page_paths)
        self.books_of(host).insert(0, t)
        self.save_now()
        return t

    @staticmethod
    def is_blank(t):
        """Made with New and nothing done with it since: not named, nothing written or asked. (One from before "fresh"
        never is: what's kept stays.)"""
        return bool(t.get("fresh")) and not any(text.strip() for text in t["pages"].values()) and \
            not t.get("messages")

    def delete(self, notebook):
        """A notebook gone, from whichever host's it is (that one, not one of the same id: the host shown may have
        changed since it was asked for)."""
        for host, books in [(HERE_HOST, self.local), *self.remote.items()]:
            if any(t is notebook for t in books):
                books[:] = [t for t in books if t is not notebook]
                self.save_now()
                if host != self.host:
                    self.push(host)
                return

    def mirror_pages(self):
        """Every page of notes as a plain file too, read-only, in a folder that's Record's own: pages/<notebook>/<page>.md.
        The notes are kept in the one file an agent can't follow a link into; these it can read, and what they link to beside
        them (Copy Link: MainTabs.copy_link). Written as a page changes, gone as it goes or is renamed. Nothing's taken away
        but what's been written here. What's kept is records.json: a file that can't be written here stops neither that nor
        the other files."""
        root = os.path.join(data_dir(), "pages")
        shown = [("", self.local)] + ([(self.host, self.notebooks)] if self.host != HERE_HOST else [])
        self.page_files, wanted = {}, {}  # (by host too: another's notebook of the same id, as a copied records.json has)
        for host, books in shown:  # (another host's in hosts/<its name>/: page_paths)
            pages = {t.get("id"): t["pages"] for t in books}
            for (book, name), rel in page_paths(books).items():
                self.page_files[host, book, name] = path = os.path.join(root, rel)
                wanted[path] = pages[book][name]
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
        path = self.page_files.get(("" if not notebook.get("host") else self.host, notebook.get("id"), name))
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
            json.dump({"records": self.local, "host": self.picked, "host_name": self.picked_name, "notes": "markdown", "full_width": self.full_width, "zoom": self.zoom,
                       "zoom_default": self.zoom_default, "reading": self.reading, "chats": self.chats,
                       "pages_folded": self.pages_folded, "books_folded": self.books_folded,
                       "markup": self.markup, "books_width": self.books_width, "backup_all": self.backup_all,
                       "spell_check": self.spell_check, "meta": self.meta},
                      f, indent=1)
            f.flush()
            os.fsync(f.fileno())  # (on the disk before it takes the file's place)
        os.replace(tmp, self.path)
        self.unzipped = True
        self.mirror_pages()
        for host in list(self.remote):  # (each host's, changed here: Meta's tools change any host's)
            self.push(host)
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
    """A message's text, without the reminders a client put before what was typed (strip_reminders)."""
    if isinstance(content, str):
        return strip_reminders(content)
    if isinstance(content, list):
        return "\n\n".join(t for b in content if isinstance(b, dict) and b.get("type") == "text"
                           and (t := strip_reminders(b.get("text", ""))).strip())
    return ""


def _is_noise(text):
    s = text.lstrip()
    return not s or s.startswith(("<command-", "<local-command", "<system-reminder", "<ci-monitor-event", "Caveat:", "[Request interrupted",
                                  "This session is being continued from a previous conversation"))


def parse_records(lines, items, seen=None, spawned=False):
    """Append the prose of transcript lines to items: [{"role": "user"|"assistant", "text", "id"}]. An assistant
    message written as several records (one per block) becomes one item. A background task's ending, which Claude Code
    writes as a message of yours (<task-notification>), is a quiet line of its own: {"role": "note", "text", "markup",
    "failed", "summary"}, as Baude's chat says it. Where Claude Code compacted the conversation is {"role": "compact",
    "text" (chatparts.compact_text), "meta" (its compactMetadata), "summary" (what it wrote, once read), "id"}. The
    prompt a session was started with from outside (its first user record, not typed: started_outside) is {"role":
    "start", "text", "id"}, drawn at the chat's top, unless a Bot started the chat on the mesh (spawned: bot_started),
    whose task is a message of yours as any prompt is. A revert (rewind_mark) takes back the items after the record it
    keeps; seen: each record's uuid -> how many items there were after it (kept by a caller that reads on, as
    written)."""
    seen, last = ({} if seen is None else seen), None
    for line in lines:
        if last:  # (the record before: how many items there were once it was read)
            seen[last], last = len(items), None
        if (mark := rewind_mark(line)) is not None:
            if (n := 0 if mark[0] is None else seen.get(mark[0])) is not None:
                del items[n:]
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        last = d.get("uuid")
        kind = d.get("type")
        if kind == "system" and d.get("subtype") == "compact_boundary" and not d.get("isSidechain"):
            meta = d.get("compactMetadata") or {}
            items.append({"role": "compact", "text": chatparts.compact_text(meta), "meta": meta, "id": d.get("uuid")})
            continue
        if d.get("isCompactSummary"):  # (under its boundary's: the messages a partial compaction kept between them)
            mark = next((it for it in reversed(items) if it["role"] == "compact"), None)
            if mark is not None and not mark.get("summary"):
                mark["summary"] = _text_of((d.get("message") or {}).get("content"))
                seen.setdefault("\0summaries", []).append(mark)
            continue
        if kind not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta") or d.get("isCompactSummary"):
            continue
        msg = d.get("message") or {}
        text = _text_of(msg.get("content")).strip()
        if kind == "user":
            first = "\0first user" not in seen  # (kept with seen: the file's first user record is read once)
            seen["\0first user"] = True
            if first and started_outside(d) and not spawned and not _is_noise(text):
                items.append({"role": "start", "text": text, "id": d.get("uuid")})
                continue
            if _is_noise(text) or d.get("toolUseResult") is not None:
                continue
            if (note := task_notification(text)) is not None:
                markup, plain, failed = chatparts.task_note_line(note)
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
    if last:
        seen[last] = len(items)
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
    as epoch seconds (typed_at: read back from the end till there's one, refkinds.last_typed), else None."""
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
            if last and (when := typed_at(d)) and when > (said or ""):
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
    return title, cwd, ref_epoch(said) if said or not tail else refkinds.last_typed(path)


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
        return wording.say("chats.no_folder_desktop")
    return "~" + d[len(home):] if d.startswith(home) else d


# ---------------------------------------------------------------- the logo
# Baude's speech bubble, in green (or the accent picked), holding three lines that fade from the newest: the three messages it remembers.

def logo():
    """The logo (schema/icons/apps/record.svg) in the accent picked in Settings, Baude's: widgets.logo's."""
    return widgets.logo("record")


# ---------------------------------------------------------------- in the list of apps

def apps_place():
    """The list of apps install() puts Record in, as the platform names it."""
    return applist.place()


def install():
    """Baude, Record, Emission and Multiplex in the platform's list of apps, together (applist.install_all): macOS's
    ~/Applications/NAME.app, Linux's .desktop entry and logo each, the logos in the accent picked. Each starts its
    command in ~/.local/bin, written with the others (bins.install). Where Record's is."""
    applist.install_all(widgets.load_settings().get("accent"))
    return applist.launchers(APP_ID, "Record")[0]


def installed():
    """Whether all four are in the platform's list of apps (Settings → Show in Applications)."""
    return applist.all_installed()


def uninstall():
    """All four apps' launchers taken out of the list: what was removed."""
    return applist.uninstall_all()


def restart_path():
    """What a Record restarting (☰ → Restart Record) leaves for the one it starts: Window.state, as JSON."""
    return os.path.join(data_dir(), "restart.json")
