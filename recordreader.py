"""recordreader — Record's left column and the chat read: the notebooks' column, its width and its slide
(FixedWidth, ColumnSlide), the open notebook's pages (PagesList), a name's card on hover, and the chat last opened
(Reader) with the passages picked in it (Picks).
"""
import os
import re
import urllib.parse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

import pointers  # noqa: E402  (the pointer's shape: beside this file)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; beside this file)
from baudelib import wording  # noqa: E402  (its words, Baude's: schema/copy)
import livefeed  # noqa: E402  (a chat followed as it's written, as Baude's: beside this file)
import metalook  # noqa: E402
from recordstore import (  # noqa: E402
    HOME, LINK_RE, MARKUP_MODES, base_name, citation_link, page_target, parse_records, passage_in, plain_words,
    short_page, without_to_learn,
)
from recordui import ReaderNames, UNEXPLAINED, ZoomPlace, bot_started, search_spaces  # noqa: E402


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
        self.typing = {}  # (a page's title being typed in, not yet kept: {page: (its notebook, the name shown)}, typed)
        self.head = widgets.group_header("Pages", win.store.pages_folded, lambda: self.set_folded(not self.folded),
                                         plus=("list-add-symbolic", "New page (+Name in a page makes one under it)",
                                               self.new_page))
        self.head.set_margin_start(12)
        self.head.set_margin_end(8)
        self.head.set_margin_top(6)
        self.append(self.head)
        self.index = widgets.TreeList(selection_mode=Gtk.SelectionMode.SINGLE)
        self.index.add_css_class("navigation-sidebar")
        self.index.connect("row-activated", lambda _l, row: not row.gone and self.win.open_note(row.page))
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
        selected. Those Meta's added, deleted (listed still, struck through) or moved, till they're kept or undone,
        marked so (metalook.mark_row), and those it's highlighted."""
        while (r := self.index.get_row_at_index(0)) is not None:
            self.index.remove(r)
        if self.notebook is None:
            return
        self.listed = order, grey = self.names()
        chat = getattr(self.win, "chat", None)
        own = f"{self.notebook.get('id')}\n"
        marks = {key[len(own):]: mark for key, mark in (chat.marks("page") if chat is not None else {}).items()
                 if key.startswith(own)}
        gone = [n for n, (how, *_r) in marks.items() if how == "removed" and n not in order]
        if gone:
            order = sorted({*order, *gone}, key=lambda n: (n.partition(":")[0] != HOME, [s.lower() for s in n.split(":")]))
        shown = getattr(self.win.diary_shown(), "page", None)
        rows = {}
        for name in order:
            depth = name.count(":")
            box = Gtk.Box(spacing=6, margin_start=6 + depth * widgets.TreeList.INDENT, margin_end=4)
            lb = widgets.FadeLabel(label=self.shown_name(name), xalign=0, hexpand=True)  # (too long: it fades where it's cut)
            if name in grey:
                lb.add_css_class("dim-label")
                lb.add_css_class("page-placeholder")
            box.append(lb)
            row = rows[name] = Gtk.ListBoxRow(child=box, tooltip_text=name if depth else None)
            row.page, row.tree_anchor = name, lb  # (the line runs down from under its parent's name to its own)
            row.tree_parent = rows.get(name.rpartition(":")[0]) if depth else None
            row.gone = name in gone  # (deleted: there's nothing to open)
            if name in marks:
                metalook.mark_row(row, ("page", own + name), marks[name], chat, lb, inline=False)  # (where it was:
                # under the pointer)
            if not row.gone:
                click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)  # (right-click: page_menu)
                click.connect("pressed", lambda _g, _n, x, y, row=row: self.page_menu(row, x, y))
                row.add_controller(click)
            self.index.append(row)
            if name == shown:
                self.index.select_row(row)

    def shown_name(self, page):
        """A page's name in the list: what's typed in its title as it's typed (typed), while its page is open;
        else its own (and what was typed let go: its page closed, deleted or moved before the name was kept)."""
        notebook, name = self.typing.get(page, (None, None))
        editor = self.win.tabs.get(("note", page)) if notebook is not None else None
        if editor is None or getattr(editor, "notebook", None) is not notebook:
            self.typing.pop(page, None)
            return short_page(page)
        return name if notebook is self.notebook else short_page(page)

    def typed(self, notebook, page, name):
        """A page's title typed in (DiaryEditor._title_typed): its row named so at once, where it is (it's sorted
        again once the name's kept); name None, its own name back."""
        if name is None:
            self.typing.pop(page, None)
        else:
            self.typing[page] = (notebook, name)
        if notebook is not self.notebook:
            return
        i = 0
        while (row := self.index.get_row_at_index(i)) is not None:
            if row.page == page:
                row.tree_anchor.set_label(self.shown_name(page))
            i += 1

    def page_menu(self, row, x, y):
        """A page's menu (right-click on it in the list): New Page… (one under it), then Rename… (its title, to
        type in) and Delete…, as a notebook's; Home's has only the first."""
        menu = Gio.Menu()
        sections = [(("New Page…", "pages.new-under"),)]
        if row.page != HOME:
            sections.append(((wording.say("common.rename_ellipsis"), "pages.rename"),
                             (wording.say("common.delete_ellipsis"), "pages.delete")))
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
        d.add_response("cancel", wording.say("common.cancel"))
        d.add_response("ok", wording.say("common.add"))
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
    names the chat's folder has (⌘K's: files, branches, Kubernetes objects, tables, what Signal looked up) are links:
    one opens where it is. Ctrl (or ⌘) and a drag picks another passage, numbered, every one quoted; a drag without
    it, or a click, lets them all go."""

    PICKING = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.SUPER_MASK

    WIDTH = 760  # its column, unless the text is full width

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.path, self.items, self.rows = win, None, [], []
        self.spawned = False  # (read as a chat a Bot started: bot_started, as it was when its reading began)
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
        self.chat_find = widgets.ChatFind(self.box, self.scroll, pane=self)  # (⌘F / Ctrl+F: Window.find_in_code)
        self.stack.add_named(self.chat_find.wrap(), "chat")
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
        self.read_from_start()

    def read_from_start(self):
        """The chat drawn again from its start: as it's first shown, or once it's known to be a Bot's (bot_changed)."""
        self.items, self.seen = [], {}
        self.spawned = bot_started(self.path)
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

    def bot_changed(self):
        """The hosts changed: a chat a host now lists as one a Bot started (bot_started), drawn again so (a Bot's chat
        stays one: what this machine's host process keeps was read as it was first shown)."""
        if self.path and not self.spawned and bot_started(self.path, kept=False):
            self.read_from_start()

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
        """The folder's names are in (or one folder's of several, the rest waited for): the replies shown, linked again
        (each with its logo)."""
        self.waiting = self.space.refresh(self._catalogued) if self.space is not None and self.space.busy else None
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
        self.items, self.seen = [], {}
        while (c := self.box.get_first_child()) is not None:
            self.box.remove(c)
        self.rows = []

    def _read(self, lines):
        """What's been added to the chat (livefeed.Tail, the moment it's written): its prose drawn, a reply written in
        several records the one it's part of, grown."""
        n = len(self.items)
        parse_records(lines, self.items, self.seen, self.spawned)
        keep = n  # (a revert took back what's after the record it keeps: those rows go)
        if n and (len(self.items) < n or self.rows[n - 1].item is not self.items[n - 1]):
            keep = next(i for i, row in enumerate(self.rows) if i >= len(self.items) or row.item is not self.items[i])
            for row in self.rows[keep:]:
                self.box.remove(row)
            del self.rows[keep:]
        grew = keep and self.items[keep - 1].pop("grew", False)
        if grew:
            self.rows[keep - 1].prose.set_text(self.items[keep - 1]["text"])
        for mark in self.seen.pop("\0summaries", ()):  # (a compaction's summary, read after it was drawn)
            row = next((r for r in reversed(self.rows[:keep]) if r.item is mark), None)
            if row is not None:
                row.prose.set_summary(mark["summary"])
        for it in self.items[keep:]:
            it.pop("grew", None)
            self._add(it)
        if grew or len(self.items) != n or keep < n:
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
        if it["role"] == "compact":  # (where Claude Code compacted it: its marker, as Baude's chat draws one)
            mark = widgets.CompactMark(it.get("meta") or {})
            mark.set_summary(it.get("summary"))
            row.append(mark)
            row.prose, row.item = mark, it
            self.rows.append(row)
            self.box.append(row)
            return
        if it["role"] == "start":  # (what a session started from outside was asked: at its top, as Baude has it)
            view = widgets.start_prompt_view(it["text"])
            row.append(view)
            row.prose, row.item = view, it
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
        return [(wording.say("common.copy"), self.copy_passages), ("Copy citation link", self.copy_citations)]

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


def markup_row(win):
    """Settings → Markup visibility (and Meta's row for it): which of the notes' Markdown marks are shown."""
    modes = list(MARKUP_MODES)
    row = Adw.ComboRow(title="Markup visibility", model=Gtk.StringList.new(list(MARKUP_MODES.values())),
                       selected=modes.index(win.store.markup))
    row.connect("notify::selected", lambda r, _p: win.set_markup(modes[r.get_selected()]))
    return row


def page_key(notebook, page):
    """A page as Meta's marks and actions name it: its notebook's id and its name."""
    return f"{notebook.get('id')}\n{page}"


def page_name(text):
    """A page's name as typed, its parts' spaces evened out (Work : Plans is Work:Plans)."""
    return ":".join(base_name(part) for part in str(text or "").split(":") if base_name(part))
