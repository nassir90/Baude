"""A Bot's memory, on the side panel's Memory page: its memory.md on its host (the memory op), in Record's page editor
(diaryeditor.DiaryEditor). What the Bot wrote there since you last decided is shown as Record shows Meta's edits, each
change green and red with Accept and Reject on it, against what you've accepted (memory.seen.md, beside it on its
host). What you write, accept or reject is saved to its host a moment after, as long as the Bot hasn't written in it
since it was read; if it has, the host says so with what it wrote, and what you did is put in with that (merge) and
saved."""
import difflib
import time
import uuid

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

import widgets  # noqa: E402
from baudelib import wire, wording  # noqa: E402
from baudelib.client import UNREACHED  # noqa: E402

PAGE = "Memory"
SAVE_MS = 600  # (what's typed, saved this long after the last key)
TRIES = 3  # (a save the Bot's writing came before: sent again on what it wrote, this many times in all)
_css_loaded = False
_host_type = None


def _lines(text):
    return text.splitlines(keepends=True)


def changes(seen, now):
    """The Bot's changes to its memory, seen -> now, as Record's proposals (recordchat.RecordChat's): [{id, old, new,
    start, ...}], start where old begins in seen, old whole lines of it. Lines put in go with the line before them, as
    it is (the line after, at the top), so they're shown where they go; lines changed one for one are a change each,
    each with its own Accept and Reject."""
    a, b = _lines(seen), _lines(now)
    starts = [0]
    for line in a:
        starts.append(starts[-1] + len(line))
    out, lead = [], ""

    def add(i, old, new):
        out.append({"id": str(uuid.uuid4()), "notebook": PAGE, "page": PAGE, "old": old, "new": new,
                    "start": starts[i], "status": "pending", "t": time.time() + len(out)})
    ops = [o for o in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes() if o[0] != "equal"]
    for n, (op, i1, i2, j1, j2) in enumerate(ops):
        put = "".join(b[j1:j2])
        if op == "insert" and i1 > 0:
            add(i1 - 1, a[i1 - 1], lead + a[i1 - 1] + put if i1 == 1 else a[i1 - 1] + put)
            lead = ""
        elif op == "insert" and not a:
            add(0, "", put)
        elif op == "insert":  # (at the top: with the first line, or the next's lines put in after it, if there are)
            if n + 1 < len(ops) and ops[n + 1][0] == "insert" and ops[n + 1][1] == 1:
                lead = put
            else:
                add(0, a[0], put + a[0])
        elif op == "replace" and i2 - i1 == j2 - j1:
            for k in range(i2 - i1):
                if a[i1 + k] != b[j1 + k]:
                    add(i1 + k, a[i1 + k], b[j1 + k])
        else:
            add(i1, "".join(a[i1:i2]), put)
    return out


def merge(base, ours, theirs):
    """ours and theirs, each base with changes of its own, in one: both's changes made (one both made, once), by
    base's lines; or None if they change the same lines, or put lines in at one place, differently."""
    a = _lines(base)

    def hunks(other):
        b = _lines(other)
        return [(i1, i2, tuple(b[j1:j2])) for op, i1, i2, j1, j2 in
                difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes() if op != "equal"]
    both = sorted(set(hunks(ours)) | set(hunks(theirs)))
    for x, y in zip(both, both[1:]):
        if y[0] < x[1] or x[0] == x[1] == y[0] == y[1]:
            return None
    out, at = [], 0
    for i1, i2, lines in both:
        out += [*a[at:i1], *lines]
        at = i2
    return "".join(out + a[at:])


class _Store:
    """DiaryEditor's window's Store (recordui.EditorHost.store): a change saved to the Bot's host (BotMemory.save_soon),
    its page's file there, and Record's settings: the markup on the caret's line, spelling checked."""
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
    """DiaryEditor's pages' list (recordui.EditorHost.pages): its one page, Memory, listed; what's typed in it or its
    title kept as it is."""
    def names(self):
        return [PAGE], []

    def prune(self, *_a):
        pass

    def changed(self, *_a):
        pass

    def typed(self, *_a):
        pass


class _Chat:
    """DiaryEditor's Record (recordui.EditorHost.chat): a change accepted or rejected saved to the Bot's host
    (BotMemory.decided); a selection, a move, a tint and a card kept as they are."""
    agent = None  # (what's proposed is the notebook's, recordmeta.pending_edits: the Bot's changes)

    def __init__(self, owner):
        self.owner = owner

    def decided(self, p):
        self.owner.decided(p)

    def set_selection(self, *_a):
        pass

    def follow_moves(self, *_a):
        pass

    def edit_cards_changed(self, *_a):
        pass

    def tint(self, *_a):
        pass


class _Tabs:
    """DiaryEditor's window's tabs (recordui.EditorHost.tabs): the page is the side panel's, so asked for its tab it's
    None, and a retitle keeps the panel's name."""
    def get(self, _key):
        return None

    def retitle(self, *_a, **_k):
        pass


class _Host:
    """What diaryeditor.DiaryEditor takes as its window (a recordui.EditorHost, its base: BotMemory._ensure_editor):
    Baude's window's focus, its toasts, and a notebook of one page, Memory, that a [[link]] says it's the only page
    of."""
    pages_made = False

    def __init__(self, owner, win):
        self.owner, self.win = owner, win
        self.store, self.pages, self.chat, self.tabs = _Store(owner), _Pages(), _Chat(owner), _Tabs()
        self.current = None

    def get_focus(self):
        return self.win.get_focus()

    def toast(self, text):
        widgets.toast(self.owner, text)

    def open_note(self, name, select=True):
        if name != PAGE:
            self.toast(f"No page named {name}")
        return self.owner.editor


class BotMemory(Gtk.Box):
    """The Memory page: the open Bot's memory, read from its host (show), edited in Record's page editor."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        widgets.inside(self, start=False)  # (the dark inside a subagent's chat and a Record page are read in)
        self.win, self.sid, self.remote, self.path, self.editor = win, None, None, None, None
        self.asked = 0  # (the read the page waits for: one asked for before another Bot's was is let be)
        self.base = None  # (its memory as its host last said: {text, seen, mtime}, what a save's sent on)
        self.dirty = False  # (written in, or a change accepted or rejected, since it was last sent)
        self.saving = None  # (the save's timer)
        self.sending = 0  # (saves of this Bot's memory sent since it was shown, not answered yet)
        self.again = False  # (a read wanted, once what's written is in)
        self.after = {}  # (what's written in a Bot's memory left while a save of it was answered: {asked: (text, seen)},
        # sent once it is)
        self.failed = False  # (the last read failed: read again when it's shown again)
        self.offline = Gtk.Label(wrap=True, xalign=0, visible=False)  # (its host not reached: said above the memory,
        self.offline.add_css_class("memory-offline")                # greyed below it, as it was last read)
        grey = Gtk.CssProvider()
        grey.load_from_string(".memory-offline { background: alpha(currentColor, 0.08); color: alpha(currentColor, "
                              "0.75); padding: 8px 12px; }")
        self.offline.get_style_context().add_provider(grey, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.append(self.offline)
        self.empty = Adw.StatusPage(icon_name="document-edit-symbolic", vexpand=True)
        self.append(self.empty)
        self._status()

    def _ensure_editor(self):
        global _css_loaded, _host_type
        if self.editor is not None:
            return
        import diaryeditor  # (Record's page editor: beside this file)
        import recordui
        if not _css_loaded:
            css = Gtk.CssProvider()
            css.load_from_string(recordui.EDITOR_CSS)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            _css_loaded = True
        if _host_type is None:
            _host_type = type("BotMemoryHost", (_Host, recordui.EditorHost), {})
        self.notebook = {"id": PAGE, "pages": {PAGE: ""}, "messages": []}  # (its changes: messages' proposals)
        self.host = _host_type(self, self.win)
        self.host.current = self.notebook
        self.editor = diaryeditor.DiaryEditor(self.host, self.notebook, PAGE)
        self.editor.title.get_parent().set_visible(False)  # (its name the panel's, on its tab)
        self.editor.set_vexpand(True)
        self.append(self.editor)
        self.fit_width()

    def fit_width(self):
        """The text as wide as Baude's Full-width text has it (Record's page's column otherwise)."""
        if self.editor is not None:
            import widgets as w
            full = w.load_settings().get("full_width", True)
            self.editor.clamp.set_maximum_size(100_000 if full else self.editor.WIDTH)
            self.editor.clamp.set_tightening_threshold(100_000 if full else 400)

    def reached(self, ok, why=None):
        """Read (the page as it is), or not (why: the error said): the memory as it was last read, greyed, not to be
        written in, under a line saying what's wrong (why in its tooltip); nothing read yet, what's wrong in its
        place."""
        self.failed = not ok
        gone = getattr(self.remote, "state", "alive") != "alive" or why in UNREACHED
        said = wording.say("bot_memory.cant_connect") if gone else wording.say("bot_memory.couldnt_load")
        if not ok and self.base is None:
            self.offline.set_visible(False)
            return self._status(said.rstrip("."), wording.say("bot_memory.shown_when_back") if gone else why or "")
        self.offline.set_label(wording.say("bot_memory.last_saved", said=said) if not ok else "")
        self.offline.set_tooltip_text(why if not ok and not gone else None)
        self.offline.set_visible(not ok)
        if self.editor is not None:
            self.editor.set_sensitive(ok)

    def _status(self, title=wording.say("bot_memory.no_memory"), text=""):
        """The status page in the editor's place: before a Bot's memory is first read, or what went wrong reading it."""
        self.empty.set_title(title)
        self.empty.set_description(text)
        self.empty.set_visible(True)
        if self.editor is not None:
            self.editor.set_visible(False)

    def show(self, info):
        """The open chat (info, the sidebar's: its id, its host, whether it's a Bot; or None) shown: a Bot's memory read
        from its host, the page empty and not to be written in till that's in."""
        self.fit_width()
        info = info or {}
        sid, remote = info.get("id"), info.get("remote")
        bot = bool(info.get("bot")) and sid is not None and remote is not None
        if bot and sid == self.sid and remote is self.remote and self.failed \
                and not self.dirty:
            return self.refresh()  # (the same Bot, its last read failed: its host may be back)
        if bot and sid == self.sid and remote is self.remote or not bot and self.sid is None:
            return  # (shown already: refresh reads it again)
        if sid is not None and sid == self.sid:
            self._let_go()  # (the same chat a Bot no more: its memory kept on its host as it was last saved)
        elif self.dirty and self.sending:
            self._let_go()  # (another chat, a save of this one's on its way: what's written since, sent after it)
            self.after[self.asked] = (self.written(), self.editor.kept())
        else:
            self.flush()  # (another chat: what's written in this one saved first)
        self.asked += 1  # (what's asked and saved for the Bot shown before: let be as it's answered)
        self.sid, self.remote = (sid, remote) if bot else (None, None)
        self.base, self.again, self.sending = None, False, 0
        if self.editor is not None:
            self.notebook["pages"][PAGE] = ""
            self.notebook["messages"] = []
            self.editor.reload(pending=True)
            self.editor.set_sensitive(False)
            self.editor.set_visible(True)
            self.empty.set_visible(False)
        if bot:
            self._ask()

    def refresh(self):
        """Read again (a turn of the Bot's ended: it may have written in it), once what's written here is saved."""
        if self.sid is None or self.remote is None:
            return
        if self.dirty or self.saving is not None or self.sending:
            self.again = True
            if self.saving is None and not self.sending:
                self.flush()
            return
        self._ask()

    def _ask(self):
        asked = self.asked
        self.remote.request({"op": "memory", "session": self.sid}, lambda r: self._read(asked, r))

    def _read(self, asked, reply):
        if asked != self.asked:
            return
        if reply.get("op") == "error":
            return self.reached(False, wire.said(reply) or wording.say("bot_memory.no_reason"))
        if self.dirty or self.saving is not None or self.sending:
            self.again = True  # (written in since it was asked for: that's saved, then it's read again)
            return
        self._take(reply)

    def _take(self, reply):
        """The memory as its host has it (a read's, or a save's reply), the page showing it, unless it does already:
        what's accepted, and the Bot's changes since, as Record's proposals; the caret where it was."""
        self.reached(True)
        text = reply.get("text") or ""
        seen = reply.get("seen")
        seen = text if seen is None else seen  # (a host that keeps no seen: what's there taken as accepted)
        self.base = {"text": text, "seen": seen, "mtime": reply.get("mtime")}
        self.path = reply.get("path") or self.path
        self._ensure_editor()
        ed = self.editor
        if not (ed.get_sensitive() and ed.kept() == seen and self.written() == text):
            self.notebook["pages"][PAGE] = seen
            self.notebook["messages"] = [{"proposals": changes(seen, text)}]
            ed.reload(pending=True)
        ed.set_sensitive(True)
        ed.set_visible(True)
        self.empty.set_visible(False)

    def written(self):
        """The memory as it's to be on its host: what's accepted, with what's still waiting put in, so a change not
        decided yet isn't lost."""
        ed = self.editor
        text = ed.kept()
        import recordmeta
        for e in sorted((e for e in ed.proposals if e["p"].get("status") == "pending"),
                        key=lambda e: -ed.kept_offset(ed.buf.get_iter_at_mark(e["a"]).get_offset())):
            p = e["p"]
            at = ed.kept_offset(ed.buf.get_iter_at_mark(e["a"]).get_offset())
            if p["old"] and text[at:at + len(p["old"])] != p["old"]:
                at, _found = recordmeta.place_change(text, p["old"], at)
                if at is None:
                    continue
            text = text[:at] + p["new"] + text[at + len(p["old"]):]
        return text

    def decided(self, _p):
        self.save_soon()

    def save_soon(self):
        if self.sid is None or self.editor is None or self.editor.loading or not self.editor.get_sensitive():
            return
        self.dirty = True
        if self.saving is not None:
            GLib.source_remove(self.saving)
        self.saving = GLib.timeout_add(SAVE_MS, self.flush)

    def _let_go(self):
        if self.saving is not None:
            GLib.source_remove(self.saving)
        self.saving, self.dirty = None, False

    def flush(self):
        """What's written, accepted and rejected, saved to the Bot's host now (if anything is, and its host is there;
        one save at a time, the next sent as the one before is answered)."""
        if self.saving is not None:
            GLib.source_remove(self.saving)
        self.saving = None
        if not self.dirty or self.sid is None or self.remote is None or self.base is None or self.sending:
            return False
        if getattr(self.remote, "state", "alive") != "alive":
            widgets.toast(self, wording.say("bot_memory.not_saved"))
            return False  # (still to save: with the next change, or read)
        self.dirty = False
        self._send(self.asked, self.sid, self.remote, self.base, self.written(), self.editor.kept())
        return False

    def _send(self, asked, sid, remote, base, ours, seen, tries=TRIES):
        """ours (the memory as it's to be, seen what's accepted of it) sent to be kept, on base (the memory as its host
        last said: {text, mtime}, and theirs, if it said since that the Bot had made it that and the page doesn't show
        it yet: put in with ours first)."""
        text = ours
        if base.get("theirs") is not None:
            text = merge(base["text"], ours, base["theirs"])
            if text is None:
                widgets.toast(self, wording.say("bot_memory.conflict"))
                text = base["theirs"]
        if asked == self.asked:
            self.sending += 1
        remote.request({"op": "memory", "session": sid, "text": text, "seen": seen, "base_mtime": base["mtime"]},
                       lambda r: self._sent(asked, sid, remote, base, ours, seen, tries, r))

    def _sent(self, asked, sid, remote, base, ours, seen, tries, reply):
        """A save answered: what its host has now kept (and shown, if the page doesn't show it); or, the Bot having
        written in it since it was read, what was done here put in with what it wrote (merge), and sent again. If both
        changed the same lines, the Bot's text is kept as it is, and what you'd written shown as its changes, to take
        back."""
        mine = asked == self.asked
        if mine:
            self.sending -= 1
        said = {"text": reply.get("text") or "", "seen": reply.get("seen"), "mtime": reply.get("mtime")}
        if reply.get("op") == "error" and "text" in reply and tries > 1:
            return self._send(asked, sid, remote, {**said, "text": base["text"], "theirs": said["text"]}, ours, seen,
                              tries - 1)
        if reply.get("op") == "error":
            widgets.toast(self, wording.say("bot_memory.couldnt_save",
                                            why=wire.said(reply) or wording.say("bot_memory.no_reason")))
            if mine:
                self.dirty = True  # (still to save: with the next change, or read)
            self.after.pop(asked, None)
            return
        # (written in as that was saved, here or in a Bot's left: sent now, on what's kept, the Bot's changes that the
        # page didn't show put in with it)
        on = {**said, "text": ours, "theirs": said["text"] if said["text"] != ours else None}
        if not mine:
            if (later := self.after.pop(asked, None)) is not None:
                self._send(asked, sid, remote, on, *later)
            return
        if self.dirty or self.saving is not None:
            self.base = on
            if self.saving is None:
                self.flush()
            return
        self._take(reply)
        if self.again:
            self.again = False
            self._ask()

    def stop(self):
        """Its window closing: what's written saved now, and the editor's timers stopped."""
        self.flush()
        if self.editor is not None:
            self.editor.stop()
