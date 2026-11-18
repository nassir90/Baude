"""A Bot's memory, on the side panel's Memory page: its memory.md on its host (the memory op), in Record's page editor
(record.DiaryEditor). What the Bot wrote there since you last looked is shown as Record shows Meta's edits, each change
green and red with Accept and Reject on it; what you write or accept is saved to its host, a moment after."""
import difflib
import time
import uuid

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

import widgets  # noqa: E402

PAGE = "Memory"
SAVE_MS = 600  # (what's typed, saved this long after the last key)
_css_loaded = False


def changes(seen, now):
    """The Bot's changes to its memory, seen -> now, as Record's proposals (record.RecordChat's): [{id, old, new, start}],
    a run of lines each, start where old begins in seen."""
    a, b = seen.splitlines(keepends=True), now.splitlines(keepends=True)
    if a and not a[-1].endswith("\n"):
        a[-1] += "\n"
    if b and not b[-1].endswith("\n"):
        b[-1] += "\n"
    starts, at = [], 0
    for line in a:
        starts.append(at)
        at += len(line)
    starts.append(at)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        out.append({"id": str(uuid.uuid4()), "notebook": PAGE, "page": PAGE, "old": "".join(a[i1:i2]),
                    "new": "".join(b[j1:j2]), "start": starts[i1], "status": "pending", "t": time.time() + len(out)})
    return out


class _Store:
    """What DiaryEditor asks its window's Store: settings, and save (to the Bot's host: BotMemory.save_soon)."""
    markup = "line"
    spell_check = True

    def __init__(self, owner):
        self.owner = owner
        self.meta = {"messages": []}

    def save(self):
        self.owner.save_soon()

    def page_file(self, _notebook, _page):
        return self.owner.path


class _Pages:
    """DiaryEditor's pages' list: one page, its memory."""
    def names(self):
        return [PAGE], []

    def prune(self, *_a):
        pass

    def changed(self, *_a):
        pass

    def typed(self, *_a):
        pass


class _Chat:
    """DiaryEditor's Record chat: a decision on a change saved (BotMemory.decided); the rest of it (its tint of what
    Meta added, its selection to quote...) Record's own, nothing here."""
    def __init__(self, owner):
        self.owner = owner

    agent = None  # (no reply coming: what's proposed is the notebook's, record.pending_edits)

    def __getattr__(self, _name):
        return lambda *_a, **_k: None

    def decided(self, p):
        self.owner.decided(p)

    def set_selection(self, *_a):
        pass

    def follow_moves(self, *_a):
        pass

    def edit_cards_changed(self, *_a):
        pass


class _Tabs:
    """DiaryEditor's window's tabs: none here (the page is the panel's)."""
    def get(self, _key):
        return None

    def retitle(self, *_a, **_k):
        pass


class _Host:
    """What record.DiaryEditor takes as its window (Record's Window): Baude's window's focus and notes, and the rest
    a one-page notebook's (no notebooks, no folder to look in, no links to follow)."""

    def __init__(self, owner, win):
        self.owner, self.win = owner, win
        self.store, self.pages, self.chat, self.tabs = _Store(owner), _Pages(), _Chat(owner), _Tabs()
        self.current = None

    def get_focus(self):
        return self.win.get_focus()

    def toast(self, text):
        self.win.note(text)

    def space(self):
        return None

    def read_folders(self):
        return []

    def open_note(self, _name):
        return self.owner.editor

    def open_ref(self, *_a, **_k):
        return None

    def open_citation(self, *_a, **_k):
        return None

    def hover_ref(self, *_a, **_k):
        return None

    def hover_left(self, *_a, **_k):
        return None

    def note_renamed(self, *_a, **_k):
        return None


class BotMemory(Gtk.Box):
    """The Memory page: the open Bot's memory, read from its host (show), edited in Record's page editor."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.sid, self.remote, self.path, self.editor = win, None, None, None, None
        self.seen = {}  # (session id -> its memory as last accepted here: what the Bot's changes are shown against)
        self.saving = None
        self.empty = Adw.StatusPage(icon_name="document-edit-symbolic", title="No memory yet",
                                    description="What this Bot learns and keeps shows here. You can write in it too.")
        self.append(self.empty)

    def _ensure_editor(self):
        global _css_loaded
        if self.editor is not None:
            return
        import record  # (Record's page editor: beside this file)
        if not _css_loaded:
            css = Gtk.CssProvider()
            css.load_from_string(record.EDITOR_CSS)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            _css_loaded = True
        self.notebook = {"id": PAGE, "pages": {PAGE: ""}, "messages": []}  # (its changes: messages' proposals)
        self.host = _Host(self, self.win)
        self.host.current = self.notebook
        self.editor = record.DiaryEditor(self.host, self.notebook, PAGE)
        self.editor.title.get_parent().set_visible(False)  # (one page, its name the panel's: nothing to rename)
        self.editor.set_vexpand(True)
        self.append(self.editor)

    def show(self, info):
        """The Bot info is (the sidebar's: its id, its host), read from its host; None: none open."""
        sid, remote = (info or {}).get("id"), (info or {}).get("remote")
        if sid == self.sid and remote is self.remote:
            return  # (read already: refresh reads it again)
        self.flush()
        self.sid, self.remote = sid, remote
        if sid is None or remote is None:
            return
        remote.request({"op": "memory", "session": sid}, lambda r, sid=sid: self._read(sid, r))

    def refresh(self):
        """Read again (a turn of the Bot's ended: it may have written in it)."""
        if self.sid is not None and self.remote is not None and self.saving is None:
            self.remote.request({"op": "memory", "session": self.sid}, lambda r, sid=self.sid: self._read(sid, r))

    def _read(self, sid, reply):
        if sid != self.sid or reply.get("op") == "error":
            return
        self._ensure_editor()
        self.path, text = reply.get("path"), reply.get("text") or ""
        seen = widgets.load_settings().get("bot_memory_seen", {}).get(sid)
        if seen is None:
            seen = text
            self._keep_seen(text)
        # (what's accepted, and the Bot's changes since, as Record's proposals: DiaryEditor.load shows them)
        self.notebook["pages"][PAGE] = seen
        self.notebook["messages"] = [{"proposals": changes(seen, text)}]
        self.editor.load()
        self.empty.set_visible(False)

    def _keep_seen(self, text):
        seen = dict(widgets.load_settings().get("bot_memory_seen") or {})
        seen[self.sid] = text
        widgets.save_settings(bot_memory_seen=seen)

    def written(self):
        """The memory as it's to be on its host: what's accepted, with what's still waiting put in, so a change not
        decided yet isn't lost."""
        ed = self.editor
        text = ed.kept()
        import record
        for e in sorted((e for e in ed.proposals if e["p"].get("status") == "pending"),
                        key=lambda e: -ed.kept_offset(ed.buf.get_iter_at_mark(e["a"]).get_offset())):
            p = e["p"]
            at = ed.kept_offset(ed.buf.get_iter_at_mark(e["a"]).get_offset())
            if p["old"] and text[at:at + len(p["old"])] != p["old"]:
                at, _found = record.place_change(text, p["old"], at)
                if at is None:
                    continue
            text = text[:at] + p["new"] + text[at + len(p["old"]):]
        return text

    def decided(self, _p):
        self.save_soon()

    def save_soon(self):
        if self.sid is None or self.editor is None or self.editor.loading:
            return
        if self.saving is not None:
            GLib.source_remove(self.saving)
        self.saving = GLib.timeout_add(SAVE_MS, self.flush)

    def flush(self):
        """What's written and accepted, saved to the Bot's host now."""
        if self.saving is not None:
            GLib.source_remove(self.saving)
        self.saving = None
        if self.sid is None or self.remote is None or self.editor is None:
            return False
        self._keep_seen(self.editor.kept())
        self.remote.request({"op": "memory", "session": self.sid, "text": self.written()},
                            lambda r: r.get("op") == "error" and self.win.note(f"Memory: {r.get('error')}"))
        return False
