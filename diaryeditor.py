"""diaryeditor — DiaryEditor, a page of a Record's notes: its title and text drawn as they read, a passage
selected and quoted to Meta, and the changes Meta proposes shown as tracked changes. ⌘F and @'s list on it are in
diaryfind.py (DiaryFind).
"""
import contextlib
import re
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk, Pango  # noqa: E402

import pointers  # noqa: E402  (the pointer's shape: beside this file)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; beside this file)
import markdownview  # noqa: E402
import composerview  # noqa: E402
from codeview import FindInText  # noqa: E402
import spelling  # noqa: E402  (the pages' spelling and grammar, as they're written: beside this file)
from recordstore import (  # noqa: E402
    HOME, LINK_RE, LIST_ITEM_RE, MD_LINK_RE, REF_RE, URL_RE, link_name, page_target, relinked_pages, short_page,
)
from recordmeta import pending_edits, place_change  # noqa: E402
from recordui import DiaryPlace, EditControls, NotesView, PageHistory, UNSEEN, WORD_JOINER  # noqa: E402
from diaryfind import DiaryFind  # noqa: E402


class DiaryEditor(DiaryFind, Gtk.Box, FindInText):
    """A Record's glossary, its notes: pages of Markdown drawn as they read, as Self's editor draws them (headings,
    **bold**, *italic*, ==marked==, `code`, - lists, > quotes, [links](url)), the markup shown on the caret's line
    only; [[Page]] opens a page (made if there's
    none yet); @ lists what Baude's ⌘K finds in the chat read's folder, and every chat by its title, as you type, and
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
            "link": {"underline": Pango.Underline.SINGLE},  # (in the accent: accent_changed)
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
    # a list as Baude's chat lays one out (markdownview.MarkdownView): LIST_PAD in, INDENT more a level (its spaces
    # kept, unseen), its text MARKER_GAP after its marker's column (NotesView.marker_col)
    INDENT, LIST_PAD, MARKER_GAP = markdownview.INDENT_STEP, 6, 6
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
        self.title.connect("changed", lambda *_: self._title_typed())
        title_keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)  # (before its text's IM)
        title_keys.connect("key-pressed", lambda _c, kv, _k, state: kv == Gdk.KEY_Escape and not state & (
            Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.SHIFT_MASK) and self._title_escaped())
        self.title.add_controller(title_keys)
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
        composerview.no_text_drag(self.view)  # (a selection isn't dragged away: a press in it places the caret)
        composerview.word_steps(self.view)  # (Option and an arrow: as far as a closing } or a line's full stop too)
        self.buf = self.view.get_buffer()
        for name, props in self.TAGS.items():  # (code, `x` and ``` blocks, at code's size beside the notes': widgets')
            self.buf.create_tag(name, **({**props, "scale": widgets.code_scale()} if name in ("verbatim", "pre") else props))
        for n in self.HEADING_SPACE:  # (a heading's space below, as the space above the line after it: _heading_looks)
            self.buf.create_tag(f"after-h{n}")
        # (a word misspelled, a red squiggle under it; a grammar error, a blue one: NotesView draws them)
        self.spell = self.view.spell = spelling.SpellCheck(self.view, self.view.char_rect,
                                                          lambda: self.win.store.spell_check, skip=("proposal-old",))
        self.view.restyled = self._heading_looks
        self._heading_looks()
        widgets.watch_style(self, "notify::dark", lambda editor: editor.notebook is not None and editor._highlight())
        self.accent_changed()
        for what in ("notify::dark", "notify::accent-color"):  # (the theme's shade of it; the system's accent)
            widgets.watch_style(self, what, DiaryEditor.accent_changed)
        tags = self.buf.get_tag_table()  # (the @'s unseen, though "ref" colours the rest of its @name)
        tags.lookup("logo-at").set_priority(tags.get_size() - 1)
        self.buf.connect("changed", self._changed)
        self.typed_at = None  # (where an @, # or + was just typed: their searches start only there, _token_changed)
        self.buf.connect("insert-text", lambda _b, at, text, _n: any(c in text for c in "@#+") and len(text) <= 2 and
                         setattr(self, "typed_at", at.get_offset() + max(text.rfind(c) for c in "@#+")))
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
        False anywhere lower, or with the title hidden: Up's as it always was."""
        if not self.title.is_visible():
            return False
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

    def _title_typed(self):
        """The title as it's typed in: the page's name in the pages' list and on its tab says what's typed, at once
        (renamed only once it's kept: rename_page). Nothing typed, or only spaces: its own name, as it'd stay."""
        win = self.win
        if self.notebook is None or getattr(win, "pages", None) is None:
            return
        typed, own = self.title.get_text().strip(), short_page(self.page)
        shown = typed if typed and self.page != HOME else own
        win.pages.typed(self.notebook, self.page, None if shown == own else shown)
        if win.tabs.get(("note", self.page)) is self:
            win.tabs.retitle(("note", self.page), shown, tooltip=self.page)

    def _title_escaped(self):
        """Escape in the title: what's typed in it let go, its own name back (in the list and on its tab too), and
        on to its text, the caret where it was (the find bar, open, closed too: as Escape in the text)."""
        self.title.set_text(short_page(self.page))
        if self.search_bar.get_search_mode():
            FindInText.close_search(self)
        self.view.grab_focus()
        return True

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

        renamed = relinked_pages(pages, moved)
        pages.clear()  # (the same dict: the notebook keeps it)
        pages.update(renamed)
        self.win.chat.follow_moves(self.notebook, moved)  # (the edits waiting on them go with them, as Meta's moves')
        self.win.store.save()
        self.win.pages.typed(self.notebook, old, None)  # (kept: listed by its new name, in its place)
        self.win.note_renamed(moved)
        self.win.chat.edit_cards_changed()  # (an edit's card: its page's there again)

    def reload(self, pending=False):
        """The page changed elsewhere (a word added to To learn, a link renamed): shown again, the caret kept, nothing
        to undo (as GTK's own Undo had it). pending: what's proposed to it changed: shown again even if its text's the
        same."""
        if self.notebook and (pending or self.kept() != self.notebook["pages"].get(self.page, "")):
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

    def accent_changed(self):
        """Links, [[pages]] and [names](url), in the accent as Baude's are (Settings → Accent Colour), its shade for
        text in the theme, light or dark (theming.apply_accent asks again as it changes)."""
        self.buf.get_tag_table().lookup("link").set_property("foreground-rgba", widgets.accent_text_rgba())

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
        # (an edit's Accept and Reject, U+FFFC and a joiner, after a ``` its green ends with: still a bare fence)
        spans = markdownview.fence_spans(text.replace("\ufffc", "").replace(WORD_JOINER, "").split("\n"), typing=shown)
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
        if (chat := getattr(self.win, "chat", None)) is not None:
            chat.tint(self)  # (and Meta's, a page it added: all green)
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
        for p in pending_edits(self.notebook, self.win.store, getattr(getattr(self.win, "chat", None), "agent", None)):
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

    @staticmethod
    def adds(p):
        """Is a change lines put in after its passage's (its new its old, whole lines, with more after)? Its green is
        then only what it puts in, and its passage isn't struck through: it stays."""
        old, new = p["old"], p["new"]
        return bool(old) and old.endswith("\n") and len(new) > len(old) and new.startswith(old)

    def _put_greens(self):
        """Each change shown without its green: that put in, just after its passage (the last first, so two at one
        place are in their order). A whole line, or lines, replaced: the green on a line of its own, under it."""
        for e in reversed(self.proposals):
            if e["g0"] is not None:
                continue
            p, a, b = e["p"], self.buf.get_iter_at_mark(e["a"]), self.buf.get_iter_at_mark(e["b"])
            put = p["new"][len(p["old"]):] if self.adds(p) and not e["gone"] else p["new"]
            body = put.rstrip("\n")
            trail = put[len(body):]
            lead = "\n" if p["old"] and not e["gone"] and not p["old"].endswith("\n") and not body.startswith("\n") \
                and a.starts_line() and b.ends_line() else ""
            at, words = b.get_offset(), lead + body
            # (its Accept and Reject after a word joiner, unseen and seen, in its line: GTK 4.24 puts a widget just after
            # hidden text, a [[link]]'s ]] or **bold**'s ** off the caret's line, at its line's start or off the page)
            tie = "" if words.endswith("\n") or not words and b.starts_line() else WORD_JOINER
            words += tie
            self.buf.insert(b, words)
            e["anchor"] = self.buf.create_child_anchor(self.buf.get_iter_at_offset(at + len(words)))
            self.buf.insert(self.buf.get_iter_at_offset(at + len(words) + 1), trail)
            e["g0"] = self.buf.create_mark(None, self.buf.get_iter_at_offset(at), False)
            e["g1"] = self.buf.create_mark(None, self.buf.get_iter_at_offset(at + len(words) + 1 + len(trail)), True)
            e["lead"], e["body"], e["tie"] = len(lead), len(body), len(tie)
            e["controls"] = EditControls(self, p, self._control_px())
            self.view.add_child_at_anchor(e["controls"], e["anchor"])
        for e in self.proposals:  # (a passage with nothing in it, where a green went in: its start back before that)
            a, b = self.buf.get_iter_at_mark(e["a"]), self.buf.get_iter_at_mark(e["b"])
            if a.compare(b) > 0:
                self.buf.move_mark(e["a"], b)

    def _take_green(self, e):
        """A change's green out of the buffer, its Accept and Reject off the page first, on their own: taking away the
        one under the pointer has GTK look up the caret there and then, which it can't do with the green half
        deleted (the caret's at its anchor once one's pressed, or Previous or Next has put it there)."""
        if e["g0"] is not None:
            self.view.remove(e["controls"])
            self.buf.delete(self.buf.get_iter_at_mark(e["g0"]), self.buf.get_iter_at_mark(e["g1"]))
            for k in ("g0", "g1"):
                self.buf.delete_mark(e[k])
                e[k] = None
            e["anchor"] = e["controls"] = None

    def _control_px(self, anchor=None):
        """How tall an edit's Accept and Reject are: (up, down), the text's font's ascent and, under its baseline, its
        descent, as its green is (a widget in a line stands on its baseline: EditControls asks the line for the ascent
        only, so that keeps the line as it is). On a line of their own (anchor: one that takes a paragraph out, say),
        no green to match and no text's descent under them: the ascent only, clear of the line below."""
        m = self.view.get_pango_context().get_metrics(None, None)
        up = max(10, round(m.get_ascent() / Pango.SCALE))
        if anchor is not None:
            at = self.buf.get_iter_at_child_anchor(anchor)
            after = at.copy()
            after.forward_char()
            if at.starts_line() and after.ends_line():
                return up, 0
        return up, max(0, round((m.get_ascent() + m.get_descent()) / Pango.SCALE) - up)

    def _mark_proposals(self):
        """Each change's passage struck through on red (any other's green in it left be), what it puts in green, and
        none of that green to be written in (after _highlight: it takes every tag away); its Accept and Reject the
        text's height, as that is now."""
        if not self.proposals:
            return
        buf = self.buf
        hidden = buf.get_tag_table().lookup("hidden")

        def tag(name, x, y):
            if y > x:
                buf.apply_tag_by_name(name, buf.get_iter_at_offset(x), buf.get_iter_at_offset(y))
        for e in self.proposals:
            if e["g0"] is None:
                continue
            for x, y in self._kept_pieces(*(buf.get_iter_at_mark(e[k]).get_offset() for k in ("a", "b"))):
                if not self.adds(e["p"]):
                    tag("proposal-old", x, y)
            g0, g1 = (buf.get_iter_at_mark(e[k]).get_offset() for k in ("g0", "g1"))
            tag("proposal-new", g0 + e["lead"], g0 + e["lead"] + e["body"])
            tag("proposal-lock", g0, g1)
            # (its Accept and Reject, and the joiner before them, never in markup hidden off the caret's line, a fence's
            # ``` or a link's target: GTK puts a widget in hidden text at its line's start, or off the page; nor its
            # red or green, if all of either is: a link's target changed)
            at = g0 + e["lead"] + e["body"]
            buf.remove_tag(hidden, buf.get_iter_at_offset(at), buf.get_iter_at_offset(at + e.get("tie", 0) + 1))
            for x, y in [(g0 + e["lead"], at), *self._kept_pieces(*(buf.get_iter_at_mark(e[k]).get_offset()
                                                                      for k in ("a", "b")))]:
                start = buf.get_iter_at_offset(x)
                if y > x and start.has_tag(hidden):
                    end = start.copy()
                    end.forward_to_tag_toggle(hidden)
                    if end.get_offset() >= y:
                        buf.remove_tag(hidden, start, buf.get_iter_at_offset(y))
            e["controls"].size(self._control_px(e["anchor"]))

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
