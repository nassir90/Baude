"""recordchat — RecordChat, Meta in Record's right bar (metachat.MetaChat): what Meta's column asks of Record,
an edit's card, what's asked and what it says. Its tools are in recordtools.py (RecordTools).
"""
import difflib
import os
import re

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

import pointers  # noqa: E402  (the pointer's shape: beside this file)
import theming  # noqa: E402
from baudelib import wording  # noqa: E402  (its words, Baude's: schema/copy)
from baudelib import meshtools  # noqa: E402  (META: who Meta's edits are by)
import meta  # noqa: E402  (Meta, the helper in the right bar: Baude's too, beside this file)
import metalook  # noqa: E402
import metachat  # noqa: E402
from recordstore import (  # noqa: E402
    HERE_HOST, MARKUP_MODES, QUOTE_LIMIT, TO_LEARN, base_name, book, child_env, claude_exe, diary_mentions, full_name,
    noun, plain_refs, short_page,
)
from recordmeta import (  # noqa: E402
    RECORD_PROMPT, RECORD_TOOLS, REFS_LIMIT, REF_LIMIT, none_open_said, notebook_said, page_named, pages_root,
    passage_said, pending_edits, place_change, ref_said, selection_said, set_md, split_unknown, what_it_is,
)
from recordui import ZoomPlace, backup_zip, label, notes_first  # noqa: E402
from diaryeditor import DiaryEditor  # noqa: E402
from recordreader import markup_row, on_name_hover, page_key  # noqa: E402
from recordtools import RecordTools  # noqa: E402


class RecordChat(RecordTools, metachat.MetaChat):
    """Meta in Record (metachat.MetaChat), the window's conversation, whichever notebook's open: a passage picked in a
    chat read, or selected
    on a page, quoted with what's asked; what's referenced goes with the message (ref_said); a change it proposes to a
    page's text is shown on the page, to accept or reject, and the pages it adds, deletes and moves are marked in the
    pages' list (PagesList.fill)."""

    APP = noun()
    WHAT = "a notes app: notebooks of pages in Markdown that the user writes, and the Claude Code chats they read."
    LISTED = "the pages"
    PAGES = True

    def __init__(self, win):
        super().__init__(win)
        self.notebook, self.quote, self.selection, self.drawn = None, "", None, False
        self.add_css_class("record-column")  # (the frame's: theming.CSS)
        self.place = ZoomPlace(self.scroll, self.box, self.box)  # (through the zoom: Window.apply_zoom)
        self._spans_seen = {}  # (a page's key: (its text before, now, where what was put in is): changed_spans)
        self.pending_box = self._pending_bar()
        self.bars.append(self.pending_box)
        # (what's quoted, in the box over what's written, with the look a sent bubble's quote has: quote_said)
        self.quote_box = Gtk.Box(spacing=6, margin_top=10, margin_start=10, margin_end=10)
        self.quote_label = label("", css=("quote-in-bubble", "quote-chip"))
        self.quote_label.set_hexpand(True)
        self.quote_label.set_lines(3)
        self.quote_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.quote_box.append(self.quote_label)
        x = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.START, tooltip_text="Drop the passage")
        x.add_css_class("flat")
        x.add_css_class("quote-drop")
        x.connect("clicked", lambda *_: (self.set_quote(""), self.composer.grab_focus()))
        self.quote_box.append(x)
        self.quote_box.set_visible(False)
        self.writing.head.append(self.quote_box)
        self.settings = self.make_settings()

    def show(self, notebook):
        """Another notebook open (or none): the conversation and its session go on, what the tools work on is that
        one, and Meta's told so with the next message (context). A passage selected on the page of a notebook left
        goes."""
        if self.selection is not None and self.selection["notebook"] != (notebook or {}).get("id"):
            self.set_quote("")
        self.notebook = notebook
        if not self.drawn:
            self.drawn = True
            self.render()
        self.show_pending_bar()
        self.edit_cards_changed()  # (an edit's Accept and Reject on its card: only while its notebook's open)
        self.marks_changed()  # (the notebooks' list, and the pages' and the pages open, as Meta's marks have them)

    # ---- what Meta.MetaChat asks of Record
    def messages(self):
        return self.win.store.meta["messages"]

    def actions(self):
        return self.win.store.meta.setdefault("meta_actions", [])

    def save(self, now=False):
        (self.win.store.save_now if now else self.win.store.save)()

    def space(self):
        return self.win.space()

    def first_refs(self, items):
        return notes_first(items)

    def scope(self):
        return tuple(self.win.read_folders())

    def agent_setup(self):
        return {"cwd": pages_root(), "roots": [pages_root(), *self.win.read_folders()], "exe": claude_exe(),
                "env_of": child_env, "protect": [pages_root()]}  # (the pages' files: changed through its tools only)

    def app_prompt(self):
        return RECORD_PROMPT.format(name=meta.NAME, to_learn=TO_LEARN)

    def app_tools(self):
        return RECORD_TOOLS

    def app_handlers(self):
        on_open = {"propose_edit": self.propose, "list_edits": self.list_edits, "revise_edit": self.revise_edit,
                   "withdraw_edit": self.withdraw_edit, "highlight_pages": self.highlight_pages}
        return {**{name: self._on_open(fn) for name, fn in on_open.items()},
                "add_page": self.add_page, "move_pages": self.move_pages, "add_notebook": self.add_notebook,
                "delete_page": self.delete_page, "edit_page": self.edit_page}

    def _on_open(self, fn):
        """A tool that works on the notebook open: with none open, why it can't."""
        def run(turn, args):
            self._same_open(turn)
            if self.notebook is None:
                raise ValueError(f"No {book().lower()} is open: add_page takes notebook, move_pages from_notebook "
                                 "and to_notebook, and add_notebook makes one.")
            return fn(turn, args)
        return run

    def _same_open(self, turn):
        """A tool that works on the notebook open, called in a turn asked with another open (or none): why not."""
        if turn is not None and "notebook" in turn and turn["notebook"] is not self.notebook:
            raise ValueError(f"The user opened another {book().lower()} (or closed it) since their message: nothing "
                             "was done. Their next message says which is open.")

    def make_settings(self):
        win = self.win
        out = meta.shared_settings(win, self, noun(), lambda: win.store.zoom_default)
        out["markup"] = meta.Setting("markup", "Markup visibility", lambda: win.store.markup, win.set_markup,
                                     lambda: markup_row(win), list(MARKUP_MODES),
                                     note="; ".join(f"{k}: {v}" for k, v in MARKUP_MODES.items()))
        backup = "Include glossary and chats in backup"
        out["backup_all"] = meta.Setting(
            "backup_all", backup, lambda: win.store.backup_all,
            lambda v: win.change_action_state("backupall", GLib.Variant.new_boolean(v)),
            lambda: theming.settings_switch(backup, backup_zip().replace(os.path.expanduser("~"), "~", 1),
                                            "win.backupall"), bool)

        return out

    def told(self):
        return self.win.store.meta.setdefault("meta_told", [])

    def unkept(self):
        """The changes to the left bars not kept yet, oldest first: marked, and kept or undone together (and those
        made in a notebook's own conversation, from when each had one)."""
        convs = [self.win.store.meta, *self.win.store.notebooks]
        return sorted((a for c in convs for a in c.get("meta_actions") or ()
                       if a["state"] == "done" and not a.get("kept")), key=lambda a: a.get("t") or 0)

    def marks_changed(self):
        """What Meta's marked changed: the notebooks' list, the pages' list, the pages open (tint), and a deleted one's
        tab gone once it's kept or undone."""
        self.win.mark_books()
        if self.notebook is None:
            return
        if self.win.pages.notebook is self.notebook:
            self.win.pages.fill()
        for child in self.win.tabs.children():
            if isinstance(child, DiaryEditor) and child.notebook is self.notebook:
                self.tint(child)
        gone = {key for key, (how, *_r) in self.marks("page").items() if how == "removed"}
        for key in [k for k in list(self.win.tabs.pages) if k[0] == "gone" and k[1] not in gone]:
            self.win.tabs.close(key)

    def tint(self, editor):
        """A page open as Meta's marks have it: one it added, all its text green (metalook.mark_rgba, lit as its
        change's box is under the pointer: light); else as it is."""
        key = page_key(editor.notebook, editor.page)
        mark = self.marks("page").get(key) if editor.notebook is self.notebook else None
        buf = editor.buf
        tag = buf.get_tag_table().lookup("meta-tint") or buf.create_tag("meta-tint")
        buf.remove_tag(tag, *buf.get_bounds())
        slots = self.painted.setdefault(("page", key), {})
        if mark is None or mark[0] not in ("added", "edited"):
            slots.pop("text", None)
            return
        if mark[0] == "edited":  # (the text the change put in, as the page has it now, past what's proposed)
            spans = [(editor.buf_offset(a), editor.buf_offset(b, past=False))
                     for a, b in self.changed_spans(mark[2], key, editor.kept())]
        else:
            spans = [(0, buf.get_char_count())]

        def paint(lit=False):
            tag.set_property("background-rgba", metalook.mark_rgba(mark[0], lit))
            for a, b in spans:
                buf.apply_tag(tag, buf.get_iter_at_offset(a), buf.get_iter_at_offset(b))
        paint()
        slots["text"] = paint

    def changed_spans(self, action, key, now):
        """Where a page's text (now) has what a change of Meta's put in it: [(start, end)], against its text before
        the change (text_before)."""
        before = self.text_before(action, key) or ""
        cached = self._spans_seen.get(key)
        if cached is not None and cached[:2] == (before, now):
            return cached[2]
        words = lambda text: re.findall(r"\w+|\s+|[^\w\s]", text)  # noqa: E731  (whole words: "under Work", not "W")
        old, new = words(before), words(now)
        starts = [0]
        for w in new:
            starts.append(starts[-1] + len(w))
        ops = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
        spans = [(starts[j1], starts[j2]) for op, _i1, _i2, j1, j2 in ops if op in ("replace", "insert") and j2 > j1]
        self._spans_seen[key] = (before, now, spans)
        return spans

    def text_before(self, action, key):
        """A page's text before a change of Meta's: as the first of its parts that touched the page had it, the
        page's name traced back through the moves among them (None: it wasn't there)."""
        book_id, _, name = key.partition("\n")
        before = None
        for part in reversed(action.get("parts") or [action]):
            if part.get("kind") != "pages":
                continue
            name = (part["undo"].get("moved") or {}).get(name, name)
            books = part["undo"]["books"].get(book_id) or {}
            if name in books:
                before = books[name]
        return before

    def mark_name(self, kind, key, action):
        if kind == "notebook":
            nb = next((t for t in self.win.store.every_book() if t.get("id") == key), None)
            ghost = next((p["redo"]["add"] for p in metalook.action_parts(action) if p.get("kind") == "notebook" and
                          (p.get("redo") or {}).get("add", {}).get("id") == key), None)
            return full_name(nb or ghost or {"name": key})
        if kind == "page" and (nb := self._book(key.partition("\n")[0])) is not None and nb is not self.notebook:
            return f"{full_name(nb)}/{key.partition(chr(10))[2]}"  # (another notebook's: its notebook's name too)
        return super().mark_name(kind, key, action)

    def _book(self, book_id, host=None):
        """A notebook by its id: on host (its key), if said; else the picked host's first, then any listed."""
        store = self.win.store
        if host is not None:
            return next((t for t in store.books_of(host) if t.get("id") == book_id), None) \
                if host == HERE_HOST or host in store.remote_base else None
        return next((t for t in [*store.notebooks, *store.every_book()] if t.get("id") == book_id), None)

    def open_mark(self, kind, key, how, action):
        """A page in a change's box clicked: opened (in its notebook); one deleted, its text as it was, all red, in a
        tab that can't be written in (open_gone). A notebook: opened."""
        if kind == "notebook":
            if self._book(key) is not None:
                self.win.select(key)
            return
        book_id, _, page = key.partition("\n")
        nb = next((t for t in self.win.store.every_book() if t.get("id") == book_id), None)
        if kind != "page" or nb is None:
            return
        if how == "removed" and action["state"] == "done" and page not in nb["pages"]:
            return self.open_gone(key, page, self.text_before(action, key) or "")  # (a bundle's: its parts')
        if page in nb["pages"]:
            self.open_page(nb, page)

    def open_gone(self, key, page, text):
        def make():
            view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=24,
                                right_margin=24, top_margin=16, bottom_margin=16)
            view.add_css_class("diary")
            buf = view.get_buffer()
            buf.set_text(text)
            tag = buf.create_tag("meta-tint", strikethrough=True)

            def paint(lit=False):
                tag.set_property("background-rgba", metalook.mark_rgba("removed", lit))
                buf.apply_tag(tag, *buf.get_bounds())
            paint()
            self.painted.setdefault(("page", key), {})["text"] = paint
            return Gtk.ScrolledWindow(child=view, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.win.tabs.open(("gone", key), make, f"{short_page(page)} (deleted)", icon="user-trash-symbolic",
                           tooltip=f"{page}: deleted by {meta.NAME}; undo brings it back")

    def tool_summary(self, name, inp):
        short = name.removeprefix("mcp__meta__")
        if short in ("propose_edit", "add_page", "delete_page", "edit_page"):
            return short_page(str(inp.get("page") or ""))
        if short == "revise_edit" and (p := self._proposal(inp.get("id"))) is not None:
            return short_page(p["page"])
        if short == "move_pages":
            pages = ", ".join(short_page(str(p)) for p in inp.get("pages") or ())
            return f"{pages} → {short_page(str(inp.get('under') or '')) or 'the top'}" + \
                (f" (from {inp['from_notebook']})" if inp.get("from_notebook") else "") + \
                (f" (into {inp['to_notebook']})" if inp.get("to_notebook") else "")
        if short == "add_notebook":
            return base_name(str(inp.get("name") or ""))
        return super().tool_summary(name, inp)

    def call_hunks(self, it):
        """propose_edit's, revise_edit's and edit_page's: the passage it replaces (revise_edit's, if it gave none: the
        one its edit replaced then, kept with it as it ran) and what it puts in."""
        short, inp = (it.get("name") or "").removeprefix("mcp__meta__"), it.get("input") or {}
        if short not in ("propose_edit", "revise_edit", "edit_page"):
            return []
        if short == "revise_edit":
            old = it.get("replaced", inp.get("old_text"))
            if old is None and it.get("state") == "running" and (p := self._proposal(inp.get("id"))) is not None:
                old = p["old"]  # (its edit's, as it's not run yet)
        else:
            old = inp.get("old_text") or ""
        new = str(inp.get("new_text") or "")
        return [] if old is None or str(old) == new else [(str(old), new)]

    def call_row(self, it):
        if (it.get("name") or "").removeprefix("mcp__meta__") != "edit_page" or self.notebook is None:
            return None
        name = str((it.get("input") or {}).get("page") or "")
        return "page", page_key(self.notebook, page_named(self.notebook, name, self.win.store) or name)

    def _proposal(self, pid):
        """An edit the agent proposed to the notebook's pages, by its id, whatever's become of it; or None."""
        if (p := self.win.store.edit_of(pid)) is not None:  # (as its host last listed it)
            return p
        live = self.agent.turn["proposals"] if self.agent is not None and self.agent.turn is not None else ()
        return next((p for m in self.messages() for p in m.get("proposals") or () if p["id"] == pid),
                    next((p for p in live if p["id"] == pid), None))

    # ---- an edit's card: its Accept and Reject while it waits, as its page has them, so a hunk drawn anywhere has
    # them in reach (its page not open, or not the tab shown, or the edit's somewhere off the page's screen)
    EDIT_ID_RE = re.compile(r"\b[Ee]dit ([0-9a-f]{10})\b")
    EDIT_SAID = {"accepted": "accepted", "rejected": "rejected", "withdrawn": "taken back"}

    def card_edit_id(self, it):
        """The id of the edit a call's card is of: propose_edit's (kept with its card as it ran, or said in its
        answer), revise_edit's (its input's); or None."""
        short = (it.get("name") or "").removeprefix("mcp__meta__")
        if short == "revise_edit":
            return str((it.get("input") or {}).get("id") or "") or None
        if short != "propose_edit":
            return None
        if it.get("edit"):
            return it["edit"]
        m = self.EDIT_ID_RE.search(str(it.get("result") or ""))
        return m.group(1) if m else None

    def _last_edit_cards(self):
        """{an edit's id: the input of the last call that made or changed it}: the card that has it as it is now. Its
        input, as that's the one thing every copy of a call's item shares (its card's drawn from a copy, and its call
        id can come after it's drawn). Worked out once for every card drawn, till something changes (_edits_stale)."""
        if (last := self.__dict__.get("_edit_last")) is not None:
            return last
        live = self.agent.turn["items"] if self.agent is not None and self.agent.turn is not None else ()
        last = {}
        for it in [it for m in self.messages() for it in m.get("items") or ()] + list(live):
            if it.get("kind") == "tool" and it.get("state") != "error" and (pid := self.card_edit_id(it)) is not None \
                    and self.call_hunks(it):
                last[pid] = it.get("input")
        self.__dict__["_edit_last"] = last
        return last

    def _edits_stale(self):
        self.__dict__["_edit_last"] = None

    def card_extra(self, it, line, hunks):
        if hunks is None or (pid := self.card_edit_id(it)) is None:
            return
        slot = Gtk.Box(spacing=3, valign=Gtk.Align.CENTER)
        line.append(slot)
        # (kept till the cards are drawn again, not pruned here: a reply's cards are made before it's in the window)
        self.__dict__.setdefault("edit_slots", []).append((slot, pid, it.get("input"), hunks))
        self._fill_edit_slot(slot, pid, it.get("input"), hunks)

    def _fill_edit_slot(self, slot, pid, key, hunks):
        """A card's edit as it is now: Accept and Reject while it waits (and is this card's), else what became of it,
        its hunk faded unless it's in the page."""
        while (c := slot.get_first_child()) is not None:
            slot.remove(c)
        p = self._proposal(pid)
        if p is None:
            return
        status, buttons = p.get("status"), ()
        said = self.EDIT_SAID.get(status)
        here = self.notebook is not None and p.get("notebook") == self.notebook.get("id")
        if self._last_edit_cards().get(pid) is not key:
            said = "revised"  # (a later call changed it: that one's card has it as it is, and what became of it)
        elif status == "pending" and not here:
            nb = self._book(p.get("notebook"))
            said = f"waiting in {full_name(nb)}" if nb is not None else "waiting in a notebook that's gone"
            if nb is not None:
                btn = Gtk.Button(label=wording.say("common.open"),
                                 tooltip_text=wording.say("meta.open_item", name=full_name(nb)),
                                 valign=Gtk.Align.CENTER)
                btn.add_css_class("flat")
                btn.add_css_class("meta-batch")
                btn.connect("clicked", lambda *_: self.open_edit(p))
                buttons = (btn,)
        elif status == "pending" and p["page"] not in self.notebook["pages"]:
            said = "its page is gone"
            buttons = (self._edit_button(p, False, "Reject (its page is gone)"),)
        elif status == "pending":
            buttons = (self._edit_button(p, True, "Accept"), self._edit_button(p, False, "Reject"))
        if said:
            slot.append(label(said, css=("dim-label", "caption"), wrap=False))
        for btn in buttons:
            slot.append(btn)
        (hunks.add_css_class if said and status != "accepted" or said == "revised" else
         hunks.remove_css_class)("dim-label")

    def _edit_button(self, p, accept, tip):
        if p.get("by") not in (None, meshtools.META):  # (another's now: revised by them since)
            tip = wording.say("edit_review.accept_tip_by" if accept else "edit_review.reject_tip_by", by=p["by"])
        btn = Gtk.Button(tooltip_text=tip, valign=Gtk.Align.CENTER)
        btn.set_child(Gtk.Image(icon_name="object-select-symbolic" if accept else "window-close-symbolic",
                                pixel_size=12))
        for c in ("edit-control", "edit-card-control", "edit-accept" if accept else "edit-reject"):
            btn.add_css_class(c)
        pointers.hand(btn)
        btn.connect("clicked", lambda *_: self.decide_from_card(p, accept))
        return btn

    def edit_cards_changed(self):
        """Every edit's card shown, as its edit is now (one decided, revised, taken back; another notebook open)."""
        self._edits_stale()
        slots = self.__dict__.setdefault("edit_slots", [])
        if self.get_root() is not None:  # (nothing's being drawn now: a card out of the window is one drawn again)
            slots[:] = [s for s in slots if s[0].get_root() is not None]
        for s in slots:
            self._fill_edit_slot(*s)

    def open_edit(self, p):
        """An edit waiting in another notebook: that one opened, and the edit shown on its page, the caret in it."""
        self.win.select(p.get("notebook"))
        nb = self.notebook
        if nb is not None and nb.get("id") == p.get("notebook") and p["page"] in nb["pages"] and \
                (editor := self.win.open_note(p["page"])) is not None:
            editor.show_proposal(p, scroll=True, caret=True)
            editor.view.grab_focus()

    def decide_from_card(self, p, accept):
        """An edit's Accept or Reject on its card: done on its page, as its own are (the page opened, the edit shown
        there and decided: DiaryEditor.decide); its page gone, Reject only, with no page. Only ever in the edit's own
        notebook, the one open: another's page of the same name is never touched."""
        if p.get("status") != "pending" or self.notebook is None or p.get("notebook") != self.notebook.get("id"):
            return self.edit_cards_changed()
        if p["page"] not in self.notebook["pages"]:
            if not accept:
                p["status"] = "rejected"
                self.decided(p, accept)
            return self.edit_cards_changed()
        if (editor := self.win.open_note(p["page"])) is not None and editor.notebook is self.notebook:
            editor.show_proposal(p, scroll=True)
            editor.decide(p, accept)

    # ---- what's asked
    def set_quote(self, text):
        self.quote, self.selection = text[:QUOTE_LIMIT], None
        self.quote_label.set_text(" ".join(text.split()))
        self.quote_box.set_visible(bool(text))

    def set_passages(self, texts):
        """The passages picked in the chat read (Reader.picks), quoted: more than one, each with its number, as the
        chat shows it ([1] …, a blank line between each)."""
        texts = [t.strip() for t in texts if t.strip()]
        if len(texts) < 2:
            return self.set_quote(texts[0] if texts else "")
        self.set_quote("\n\n".join(f"[{n}] {t}" for n, t in enumerate(texts, 1)))

    def set_selection(self, notebook, page, start, end, text):
        """A passage selected on a page of notes (DiaryEditor._quote_selection), quoted with where it is: what's asked
        is about it, and a change the agent proposes to it replaces it (propose)."""
        self.set_quote(text)
        self.selection = {"notebook": notebook.get("id"), "page": page, "start": start, "end": end, "text": text}
        self.quote_label.set_text(f"{short_page(page)} · {' '.join(text.split())}")

    def plain(self, text):
        return plain_refs(text)

    def said_where(self, m):
        """Where a user's message was asked, for a new session's earlier conversation: the notebook open then."""
        if m.get("role") != "user" or "notebook" not in m:
            return ""
        nb = self._book(m["notebook"]) if m["notebook"] else None
        return f"[in {full_name(nb)}] " if nb is not None else "[no notebook open] " if not m["notebook"] else ""

    def make_message(self, text):
        msg = super().make_message(text)
        notebook, space = self.notebook, self.space()
        msg["notebook"] = notebook.get("id") if notebook is not None else None
        if msg.get("refs"):  # (each said as the catalogue says it)
            msg["refs"] = [{"surface": it["surface"], "what": what_it_is(it, space), "icon": it.get("icon")}
                           for it in self.referenced(text)]
        if self.quote:
            msg["quote"] = self.quote
            if notebook is not None and self.selection is not None and \
                    self.selection["notebook"] == notebook.get("id"):
                msg["selection"] = self._selection_now(notebook) or \
                    {"page": self.selection["page"], "start": None, "end": None, "text": self.selection["text"]}
        return msg

    def can_ask_empty(self):
        return bool(self.quote)

    def empty_question(self):
        return "What does this mean?"

    def before_ask(self):
        if self.notebook is not None:
            self.notebook.pop("fresh", None)  # (asked about: kept, Store.is_blank)
        self.win.store.flush()  # (the pages' files as they are: what it reads)

    def taken(self, msg):
        self.set_quote("")

    def held(self):
        return self.quote, self.selection, self.quote_label.get_text()

    def put_held(self, held):
        if held is not None:
            quote, selection, shown = held
            self.set_quote(quote or "")
            self.selection = selection
            self.quote_label.set_text(shown)

    def restored(self, msg):
        sel, nb = msg.get("selection"), self.notebook
        if sel and nb is not None and msg.get("notebook") == nb.get("id"):
            self.set_selection(nb, sel["page"], sel.get("start"), sel.get("end"), msg.get("quote") or sel["text"])
        else:
            self.set_quote(msg.get("quote") or "")

    def turn_extra(self, msg):
        return {"proposals": [], "selection": msg.get("selection"), "notebook": self.notebook}

    def first_said(self, agent):
        return [self.open_said()]

    def open_said(self):
        """The notebook open (notebook_said), or, with none, the notebooks there are."""
        store = self.win.store
        return notebook_said(self.notebook, store, self.space(), self.win.chat_read()) if self.notebook is not None \
            else none_open_said(store)

    def context(self, agent, msg):
        """The notebook open, once another's opened (a new session's: first_said); what's become of the
        changes proposed; what's referenced (ref_said, of Window.space), the passage quoted from a chat (its names,
        the chat's) or selected on a page."""
        notebook, store, space, parts = self.notebook, self.win.store, self.space(), []
        if not agent.fresh and getattr(agent, "seen", notebook) is not notebook:  # (a new session's: first_said)
            parts.append(f"(The user opened another {book().lower()} since: your tools work on it now.)"
                         if notebook is not None else f"(The user closed the {book().lower()} since: none is "
                         "open now.)")
            parts.append(self.open_said())
        agent.seen = notebook
        if (place := self.win.place()) != getattr(agent, "seen_place", None):  # (host / notebook / page, as it changes)
            parts.append(f"(Open: {place})")
            agent.seen_place = place
        books = {t.get("id"): t for t in store.notebooks}
        for m in self.messages():  # (Meta's own edits, kept with its turns, decided since: by Meta still, its own)
            for p in m.get("proposals") or ():
                if p.get("status") in ("accepted", "rejected") and not p.get("told"):
                    if not agent.fresh and p.get("by") in (None, meshtools.META):
                        nb = books.get(p.get("notebook")) or notebook
                        where = f" in {full_name(nb)}" if nb is not None and nb is not notebook else ""
                        parts.append(f"(The change you proposed to {p['page']}{where} was {p['status']} by the user.)")
                    p["told"] = True
        if notebook is not None and (waiting := self.pending(notebook)):
            mine = [p for p in waiting if p.get("by") in (None, meshtools.META)]
            others = {}
            for p in waiting:
                if p not in mine:
                    others.setdefault(p["by"], []).append(p["page"])
            if mine:
                pages = list(dict.fromkeys(p["page"] for p in mine))
                parts.append(f"(Your edits still waiting for the user: {len(mine)}, on {', '.join(pages)}. list_edits "
                             "shows them.)")
            for by, pages in others.items():
                parts.append(f"({by}'s edits waiting for the user: {len(pages)}, on "
                             f"{', '.join(dict.fromkeys(pages))}. list_edits shows them.)")
        left, said = REFS_LIMIT, []
        for item in self.referenced(msg["text"]):
            text, paths = ref_said(item, store, space, min(REF_LIMIT, max(2000, left)))
            left -= len(text)
            agent.allowed.update(os.path.realpath(p) for p in paths if p)
            said.append(text)
        if said:
            parts.append("<referenced>\n" + "\n".join(said) + "\n</referenced>")
        if msg.get("selection"):
            parts.append(selection_said(msg["selection"], notebook, store))
        elif msg.get("quote"):
            chat_space = self.win.reader.space if self.win.reader is not None else None
            parts.append(passage_said(msg["quote"], chat_space or space))
        return parts

    # ---- what it says
    def render(self):
        self.__dict__["edit_slots"] = []  # (every card drawn again: their slots too)
        self._edits_stale()
        super().render()
        self.show_pending_bar()

    def _clear(self):
        super()._clear()
        self.after_revert()

    def after_revert(self):
        for child in self.win.tabs.children():  # (and what it proposed, from the pages open)
            if isinstance(child, DiaryEditor) and child.notebook is self.notebook:
                child.show_pending()

    def quote_said(self, m):
        q = super().quote_said(m)
        if q is not None and m.get("selection"):
            return f"{short_page(m['selection']['page'])} · {q}"
        return q

    def prose(self, text):
        """A reply's text, its folders' names linked (Window.follow), as Read shows a chat's."""
        lb = label(select=True)
        lb.add_css_class("prose")
        self.set_prose(lb, text)
        self.linked(lb)
        return lb

    def set_prose(self, lb, text):
        set_md(lb, text, self.win.names_in(text))

    def linked(self, lb):
        lb.connect("activate-link", lambda _l, uri: self.win.follow(uri, from_chat=True))
        on_name_hover(lb, self.win)

    def has_work(self, m):
        return super().has_work(m) or bool(m.get("proposals"))

    def _bubble(self, m):
        if m["role"] != "user" and m.get("items") is None and (m.get("proposals") or m.get("revised") or
                                                                m.get("withdrew")) and \
                not m.get("text") and not m.get("error") and not super().has_work(m):  # (from before tool cards)
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            edited = list(dict.fromkeys([short_page(p["page"]) for p in m.get("proposals") or ()] +
                                        [short_page(page) for page in m.get("revised") or ()]))  # (as it was then)
            gone = [short_page(page) for page in m.get("withdrew") or () if short_page(page) not in edited]
            said = ["Edited " + ", ".join(edited)] if edited else []
            said += ["Took back an edit to " + ", ".join(gone)] if gone else []
            b.append(label(" · ".join(said), css=("dim-label", "caption")))  # (it only edited)
            b.set_margin_start(12)
            b.prose = b.status = label("")
            outer.append(b)
            outer.bubble = b
            return outer
        outer = super()._bubble(m)
        if m["role"] != "user" and m.get("unknown") and self.notebook is not None and \
                m.get("notebook", self.notebook.get("id")) == self.notebook.get("id"):  # (chips add to its Home)
            outer.bubble.append(self._unknown_chips(m))
        return outer

    def reply_text(self, text):
        reply, _ = split_unknown(text)
        return re.sub(r"\n?\s*\**Unknown:?\**[^\n]*$", "", reply) if "Unknown" in text[-200:] else reply

    def worked(self, turn):
        return super().worked(turn) or bool(turn.get("proposals") or turn.get("revised") or turn.get("withdrew"))

    def finish(self, m, turn):
        if turn.get("notebook") is not None:
            m["notebook"] = turn["notebook"].get("id")
        m["text"], m["unknown"] = split_unknown(m["text"])
        if m["unknown"] and (texts := [it for it in m.get("items") or () if it["kind"] == "text"]):
            texts[-1]["text"] = split_unknown(texts[-1]["text"])[0]  # (its last part ended with the line)
        for key in ("proposals", "revised", "withdrew"):  # (the same proposals the pages show: decided there, kept here)
            if turn.get(key):
                m[key] = turn[key]

    def pending(self, notebook):
        return pending_edits(notebook, self.win.store, self.agent)

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

    def decided(self, p, accept=None):
        """A proposed edit accepted or rejected on its page: said to its notebook's host (Window.decide_edit), and, if
        it's Meta's, told it with the next message."""
        self.win.decide_edit(p, p.get("status") == "accepted" if accept is None else accept)
        self.show_pending_bar()
        self.edit_cards_changed()

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
