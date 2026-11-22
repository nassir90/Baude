"""recordui — Record's pieces under its window: its CSS and zoom classes, Open a Chat…, a zip of notebooks
(export_zip, zip_sizes), what @ and ⌘K search (Spaces, ChatNames, NotesNames, ReaderNames), nouns.py's
Unexplained, a view's place through a zoom (ZoomPlace), the notes' text (NotesView), a page's Undo (PageHistory), an
edit's Accept and Reject (EditControls) and what a DiaryEditor needs of its window (EditorHost).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
import zlib

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

import lookup  # noqa: E402  (⌘K's engine: what a chat's folder has; beside this file)
import lookupgit  # noqa: E402
import pointers  # noqa: E402  (the pointer's shape: beside this file)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; beside this file)
import panels  # noqa: E402
from baudelib import tokens  # noqa: E402  (its measures, Baude's: schema/tokens)
from baudelib import wording  # noqa: E402  (its words, Baude's: schema/copy)
import baudelib.client  # noqa: E402  (the hosts, as Baude has them: another's notebooks, through its host process)
import baudelib.hosts  # noqa: E402  (this machine's host process started again: Restart Daemon)
import baudelib.serverstate  # noqa: E402  (the sessions this machine's host process keeps: bot_started)
from baudelib.notes import data_dir  # noqa: E402,F401
import meta  # noqa: E402  (Meta, the helper in the right bar: Baude's too, beside this file)
from recordstore import (  # noqa: E402
    CITE_SCHEME, HOME, LINK_RE, REF_RE, _NETWORK, book_folders, chat_transcripts, file_part, full_name, network, noun,
    page_paths, parse_records, recent_sessions, session_facts, session_info, short_dir,
)


# ---------------------------------------------------------------- UI pieces

# A page's editor's look (DiaryEditor, EditControls): Baude's Bot memory pane loads it too
EDITOR_CSS = """
button.edit-control { min-height: 0; min-width: 0; padding: 0; border-radius: 0; color: white; box-shadow: none; }
button.edit-accept { background: #2ec27e; }
button.edit-accept:hover { background: #26a269; }
button.edit-reject { background: #e01b24; }
button.edit-reject:hover { background: #c01c28; }  /* (an edit's Accept and Reject, on its page: EditControls) */
button.edit-card-control { min-width: 22px; min-height: 18px; }  /* (and on its card: RecordChat.card_extra) */
.diary-title { font-size: 1.75em; font-weight: 800; }
.diary { font-size: 1.05em; background: transparent; }
/* a page's selection is NotesView's own, drawn to its text (_snapshot_selection) */
textview.own-selection > text > selection { background-color: transparent; }
entry.diary-title { background: none; }
entry.diary-title, entry.diary-title:focus-within { outline: none; box-shadow: none; padding-left: 0; padding-right: 0; }
"""

CSS = EDITOR_CSS + tokens.css("""
.reader-user { background: @baude_raised; border-radius: {radius.bubble}; padding: {gap.bubble-v} {gap.bubble-h}; }
.reader-assistant { padding: 4px 2px; }
.reader-assistant label.prose { font-size: 1.02em; }
.pending-bar { background: alpha(@success_color, 0.12); border-radius: 8px; padding: 1px 2px 1px 10px; }
.pending-bar button { min-height: 24px; min-width: 24px; padding: 0; }  /* (edits waiting: RecordChat._pending_bar) */
.quote-in-bubble { border-left: 3px solid alpha(@view_fg_color, 0.25); padding-left: 8px; font-size: 0.92em;
                   opacity: 0.8; }  /* (a quote as Baude's chat has one: .md-quote) */
.quote-in-bubble.quote-chip { border-left-color: @accent_bg_color; opacity: 1; color: alpha(@card_fg_color, 0.8); }
/* (one to send, its bar as bright as the accent: RecordChat.quote_box) */
button.quote-drop { min-width: 24px; min-height: 24px; padding: 0; border-radius: 0; }
dropdown.notes-host > button { padding: 2px 1px 2px 4px; min-height: 24px; }
dropdown.notes-host > button > box { border-spacing: 2px; }  /* (the notebooks' host, beside Notebooks: room for its
   name in the column) */
/* Baude's look (theming.CSS): the notebooks, the bars, the pages' list and Meta's column the frame, no lines
   between them; the page, Read and the viewers each the inside, a box set into it */
paned.notes-split > separator { background: none; background-image: none; box-shadow: none; }
.ref-card contents { padding: 6px 8px; }  /* (a name's card, just under the pointer: Window.ref_card) */
.page-placeholder { font-style: italic; }
.unknown-chip { padding: 2px 10px; border-radius: 99px; font-size: 0.9em; }
""")

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


label = meta.label  # (a label as Meta's column has them)

class ChatDialog(Adw.Dialog):
    """Open a Chat…: the Claude Code chats, the one picked (on_pick gets its session) read in a tab of its own."""

    def __init__(self, on_pick):
        super().__init__(title="Open a chat", content_width=560, content_height=620)
        self.on_pick, self.sessions = on_pick, []
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12, margin_end=12, margin_bottom=12)
        self.search = Gtk.SearchEntry(placeholder_text="Search chats")
        self.search.connect("search-changed", lambda *_: self.fill())
        box.append(self.search)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START)
        self.list.add_css_class("boxed-list")
        self.list.connect("row-activated", self._activated)
        sc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.list)
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
        while (r := self.list.get_row_at_index(0)) is not None:
            self.list.remove(r)
        q = self.search.get_text().lower().strip()
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
        self.on_pick(row.session)
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


def backup_zip():
    """The rolling backup (Window.roll_backup), in a .local folder, on a Mac too."""
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "record", "notebooks-backup.zip")


def conversation_text(notebook):
    """A notebook's conversation with the Record, as Markdown."""
    def quoted(text):
        return "".join(f"> {line}\n" for line in text.splitlines()) + "\n" if text else ""
    return "\n\n".join(f"**{'You' if m.get('role') == 'user' else noun()}:**\n\n{quoted(m.get('quote'))}{m.get('text', '')}"
                       for m in notebook.get("messages") or [])


def glossary_text(folders):
    """What's been looked up in folders (Signal's glossary: a notebook's chats', zip_entries), as Markdown: each name,
    and what it is."""
    parts = []
    for folder in folders:
        if refs := lookup.Folder.for_folder(folder).glossary():
            parts.append(f"# {folder}\n\n" + "\n".join(f"- **{ref.surface}**: {ref.summary()}" for ref in refs))
    return "\n\n".join(parts)


def bot_started(path, kept=True):
    """Is a chat's transcript one a Bot started on the mesh (its host keeps it with spawned_by)? This machine's host
    process's own, as it keeps them (read, not asked: no connection made; not if kept is False); another host's, as
    the hosts looked for list them (none looked for yet: no)."""
    sid = os.path.basename(path or "").removesuffix(".jsonl")
    if kept and (meta := baudelib.serverstate.kept_session(sid)) is not None:
        return bool(meta.get("spawned_by"))
    return bool(_NETWORK) and any(s.get("spawned_by") and sid in (s["id"], s.get("cli_id"))
                                  for s in network().sessions())


def chat_text(path, title, cwd):
    """What's shown of a chat (parse_records: what's said, not the tools' calls or what they gave back), as Markdown."""
    items = []
    with open(path, encoding="utf-8", errors="replace") as f:
        parse_records(f, items, spawned=bot_started(path))
    head = f"# {title or 'Untitled chat'}\n\n{cwd or ''}".strip()
    who = {"user": "You", "start": "Started with"}

    def said(it):
        if it["role"] != "compact":
            return f"**{who.get(it['role'], 'Claude')}:**\n\n{it['text']}"
        summary = it.get("summary")  # (a compaction: what it did, and what it wrote)
        return f"*{it['text']}*" + (f"\n\n**Summary of the earlier conversation:**\n\n{summary}" if summary else "")
    return "\n\n".join([head] + [said(it) for it in items if it["role"] != "note"])  # (a task's ending: said by neither)


def chats_of(notebook, transcripts):
    """Of transcripts [(path, title, cwd)], those a notebook has to do with: cited or named in its notes (a notebook
    has no chat of its own)."""
    notes = "\n".join(notebook["pages"].values())
    cited = set(re.findall(re.escape(CITE_SCHEME) + r"([\w-]+)", notes))
    named = {m.group(1)[1:].strip() for m in LINK_RE.finditer(notes) if m.group(1).lstrip().startswith("@")} | \
        {m.group(1) or m.group(2) for m in REF_RE.finditer(notes)}
    return [t for t in transcripts if t[1] in named or os.path.basename(t[0])[:-len(".jsonl")] in cited]


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
            chats = chats_of(notebook, transcripts)
            folders = dict.fromkeys(os.path.realpath(c[2]) for c in chats if c[2] and os.path.isdir(c[2]))
            yield os.path.join("glossary", book + ".md"), glossary_text(folders)
            for chat in chats:
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
    """⌘K's search over several spaces (search_spaces: the chat read's folder, the chats, the notes): each folder's
    names, its folder's name said with each; a name two have is the first's. An item keeps its folder as root."""

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
        waiting_on = [s for s in self.spaces if s.busy]

        def check():  # (once any of them is in: listed then, the rest waited for in turn, as the caller asks again)
            if all(s.busy for s in waiting_on):
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
    """What @ and ⌘K search: the folders read (Window.read_folders), each one's names (search_space), every Claude
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
        return [{"surface": c["title"], "canonical": "", "icon": "claude", "what": wording.say("chats.chat"),
                 "kind": "chat", "at": c["used"],
                 "ago": (ago := widgets.time_ago(c["used"])),  # (under the pointer: widgets.time_tip)
                 "where": (where := short_dir(c["cwd"]) if c["cwd"] else wording.say("chats.no_folder")),
                 "summary": f"{where} · {ago}",
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

    def changed(self, notebook, page):
        """When a page's file was last written (⌘K's and @'s order: lookup.item_when), or None."""
        path = self.store.page_files.get(("" if not notebook.get("host") else self.store.host, notebook.get("id"), page))
        try:
            return os.path.getmtime(path) if path else None
        except OSError:
            return None

    def make_items(self):
        mine, out = self.current(), []
        books = ([mine] if mine else []) + [t for t in self.store.notebooks if t is not mine]
        for t in books:
            name, pages = full_name(t), sorted(t["pages"], key=lambda p: (p != HOME, p.lower()))
            for page in pages:
                out.append({"surface": page if t is mine else f"{name}/{page}", "canonical": "", "icon": "page",
                            "what": "Page", "kind": "page", "summary": name, "where": name, "notebook": t.get("id"),
                            "page": page, "mine": t is mine, "line": None, "entry": None, "faint": False,
                            "at": self.changed(t, page)})
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
    """What Baude's markdown (markdownview.MarkdownView, through markdownview.SIGNAL) asks of a Signal to link a reply's
    names, from the notebook's search (Record has no Signal, and looks nothing up): a name its folders have is a link
    with its logo before it, as Baude links what its catalogue has; clicked, it says what it is (Window.open_ref).
    folder: the chat's, whose repository is what a bare PR #12 in it means (never a vendored checkout's)."""

    phrase_re, strong, _by_surface = None, frozenset(), {}

    def __init__(self, space, folder=None):
        self.space, self.refs, self.catalogue, self.folder = space, {}, {}, folder and os.path.realpath(folder)
        self.update()

    def update(self):
        """The names as the folders have them now (once they're catalogued)."""
        refs, catalogue, own = {}, {}, lookupgit.GITHUB.own_repository(self.folder)
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
        return self.before if (self.left or self.away is not None) and self.before else panels.scroll_place(self.scroll)

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
            if self.job is None and not self.left and (place := panels.scroll_place(self.scroll)):
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
            if getattr(self.scroll, "let_go", None) is None:  # (unless it's being held somewhere: panels.hold_place)
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
        self.before = self.before if self.left else panels.scroll_place(self.scroll)
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

    def _mark(self, top):
        return None  # (its anchor is a line number: no labels to hold a line of, the place is how far through the line)


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
        self.spell = None  # (the spelling's squiggles, drawn over the text: spelling.SpellCheck)
        self.restyled = None  # (told when its style's changed: the text's size or colour, DiaryEditor._heading_looks)
        self.resized, self.width_was = None, None  # (told when its width's changed)
        self.add_css_class("own-selection")  # (GTK's selection unseen: _snapshot_selection draws it)

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
        if layer != Gtk.TextViewLayer.ABOVE_TEXT:
            return
        self._snapshot_selection(snapshot, self.in_view())
        if self.spell is not None:
            self.spell.snapshot(snapshot)  # (a word misspelled, its squiggle; a grammar error's)
        if not (self.logos or self.marks):
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

    SELECTION_CAP = 1.5  # (a line's selection no taller than this many of the text's lines)
    SELECTION_END = 0.35  # (a line's end inside a selection, its newline: this many of the text's line high, wide)

    def _snapshot_selection(self, snapshot, view):
        """What's selected, in view, a rectangle a line shrunk to its text: as wide as what's selected of the line; as
        tall as a line of its style's text (the text's, or a heading's at its scale: the same for every line of it,
        whatever's in it), no more than SELECTION_CAP text lines, at the bottom of the line. None of the space a
        heading has above it, or between paragraphs. A line's end that's selected, a little more: a blank line in the
        selection shows. Over the text, as a chat's selection is, so a code span's or a highlight's colour is under it."""
        buf = self.get_buffer()
        bounds = buf.get_selection_bounds()
        if not bounds:
            return
        start, end = bounds
        first, last = max(start.get_offset(), view[0]), min(end.get_offset(), view[1])
        if first >= last:
            return
        fill = widgets.accent_rgba()
        fill.alpha = 0.3  # (as ChatSelection shades a chat's)
        text_h = self.create_pango_layout("Ag").get_pixel_size()[1]
        cap, nub = text_h * self.SELECTION_CAP, round(text_h * self.SELECTION_END)
        table = buf.get_tag_table()
        headings = [(tag, tag.get_property("scale") if tag.get_property("scale-set") else 1)
                    for n in range(1, 7) if (tag := table.lookup(f"h{n}")) is not None]
        it = buf.get_iter_at_offset(first)
        while it.get_offset() < last:
            here = self.get_cursor_locations(it)[0]
            nxt = it.copy()  # (the next display line's start: a wrapped line's or the next paragraph's)
            if not self.forward_display_line(nxt) or nxt.get_offset() <= it.get_offset():
                nxt = buf.get_end_iter()
            extra = 0
            if last < nxt.get_offset() or nxt.is_end():  # (the selection ends on this line)
                x = self.get_cursor_locations(buf.get_iter_at_offset(last))[0].x
            else:
                ink = nxt.copy()
                ink.backward_char()
                if ink.get_char() == "\n":  # (its newline selected too)
                    x, extra = self.get_cursor_locations(ink)[0].x, nub
                else:  # (wrapped: to the end of its last character, whose caret's place is on the next line)
                    x = self.char_rect(ink).x + self.get_iter_location(ink).width
            line = buf.get_iter_at_line(it.get_line())[1]
            scale = next((k for tag, k in headings if line.has_tag(tag)), 1)
            height = min(text_h * scale, cap)
            left, width = min(x, here.x), abs(x - here.x) + extra  # (a line right to left too)
            if width:
                snapshot.append_color(fill, Graphene.Rect().init(left, here.y + here.height - height, width, height))
            it = nxt

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
WORD_JOINER = "\u2060"  # (before an edit's Accept and Reject, in its green: DiaryEditor._put_greens)


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


class _Overhang(Gtk.LayoutManager):
    """EditControls' layout: its buttons side by side, as tall as the green beside them (the text's ascent and
    descent), though the row asks the line for the ascent only. A widget in a line stands on its baseline (GTK has
    no say for its own), so asking for more would push the line up; the descent's hung below, in the line's own."""

    def __init__(self, spacing):
        super().__init__()
        self.spacing, self.up, self.down = spacing, 0, 0

    def do_get_request_mode(self, _widget):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, widget, orientation, _for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return self.up, self.up, -1, -1
        kids = widget.buttons
        w = sum(k.measure(orientation, -1)[1] for k in kids) + self.spacing * max(0, len(kids) - 1)
        return w, w, -1, -1

    def do_allocate(self, widget, _width, _height, _baseline):
        x = 0
        for k in widget.buttons:
            w = k.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            box = Gdk.Rectangle()
            box.x, box.y, box.width, box.height = x, 0, w, self.up + self.down
            k.size_allocate(box, -1)
            x += w + self.spacing


class EditControls(Gtk.Box):
    """A proposed edit's Accept and Reject, on its page where its green ends (DiaryEditor._put_greens: at its anchor):
    a tick on green and a cross on red, square-cornered, as tall as the green, its top and bottom (size), in its line.
    Neither takes the focus."""

    def __init__(self, editor, p, size):
        super().__init__(margin_start=4, margin_end=2)
        self.layout = _Overhang(3)
        self.set_layout_manager(self.layout)
        self.buttons = []
        for icon, tip, css, accept in (
                ("object-select-symbolic", wording.say("edit_review.accept_tip"), "edit-accept", True),
                ("window-close-symbolic", wording.say("edit_review.reject_tip"), "edit-reject", False)):
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
        self.size(size)

    def size(self, size):
        """size: the text's (ascent, descent) in px. Each button that tall in all, its top the green's and its bottom
        the green's (the descent below the baseline: _Overhang), a little wider than the ascent, its mark most of
        that."""
        if size != self.px:
            self.px = size
            up, down = size
            self.layout.up, self.layout.down = up, down
            for btn in self.buttons:
                btn.set_size_request(round(up * 1.45), up + down)
                btn.get_child().set_pixel_size(max(8, round(up * 0.85)))
            self.layout.layout_changed()


class EditorHost:
    """What a DiaryEditor needs of the window it's in (its win). Record's Window is one, with its own of each; another
    (Baude's Memory page, botmemory) subclasses this, which has each method doing nothing, or what a window with no
    notebooks open says.
    store: .save() (the page changed: kept soon), .markup ("line": a line's markup shown with the caret on it),
           .spell_check, .meta ({"messages": [...]}: what's proposed, pending_edits), .page_file(notebook, page)
    pages: the pages' list: .names() ([names], _), .prune(), .changed(), .typed(notebook, page, shown)
    chat:  the Record: .decided(p), .set_selection(notebook, page, start, end, text), .follow_moves(notebook, moved),
           .edit_cards_changed(), .tint(editor), .agent (its reply coming, or None)
    tabs:  .get(key), .retitle(key, title, tooltip=None)
    current: the notebook open (its page's selection quoted to the Record, its pages' names the + list's)"""
    pages_made = True  # (a [[link]] followed and a +Name typed make a page; False: the notebook's one page is all)
    store = pages = chat = tabs = current = None

    def get_focus(self):
        return None

    def toast(self, text):
        pass

    def space(self):
        """What @ searches (a chat's folder, its chats): None, nothing to search."""
        return None

    def read_folders(self):
        return []

    def open_note(self, name, select=True):
        """A page opened (made if there's none yet): its editor, or None."""
        return None

    def open_ref(self, name):
        pass

    def open_citation(self, url):
        pass

    def hover_ref(self, name, at=None):
        pass

    def hover_left(self):
        pass

    def note_renamed(self, moved):
        pass
