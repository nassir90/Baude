"""chatfind — a selection across a chat's text (ChatSelection) and ⌘F in a chat (ChatFind): passages shaded,
matches marked.
"""
import bisect
import math
import re
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk, Pango  # noqa: E402,F401

from baudelib import wording  # noqa: E402
from widgets import accent_rgba, focus_shown, walk, watch_style  # noqa: E402
from chatparts import FindBar, SummaryPin  # noqa: E402


def layout_origin(label):
    """Where a label's text is drawn in it (GTK's get_layout_location, which GTK 4 doesn't give): its alignment's."""
    layout = label.get_layout()
    _ink, logical = layout.get_pixel_extents()
    xalign = label.get_xalign() if label.get_direction() != Gtk.TextDirection.RTL else 1 - label.get_xalign()
    x = math.floor(xalign * (label.get_width() - logical.width) - logical.x)
    if (baseline := label.get_baseline()) != -1:
        return x, baseline - layout.get_baseline() // Pango.SCALE
    return x, math.floor((label.get_height() - logical.height) * label.get_yalign())


def char_at(label, x, y):
    """The character of a label's text at (x, y), in its coordinates: the nearest, (x, y) off its text."""
    ox, oy = layout_origin(label)
    layout = label.get_layout()
    _inside, index, trailing = layout.xy_to_index(int((x - ox) * Pango.SCALE), int((y - oy) * Pango.SCALE))
    return len(layout.get_text().encode()[:index].decode("utf-8", "ignore")) + trailing


def passage_rects(label, start, end, target):
    """Where characters start to end of a label's text are, a rectangle a line, in target's coordinates."""
    layout = label.get_layout()
    text = layout.get_text()
    sb, eb = len(text[:start].encode()), len(text[:end].encode())
    ox, oy = layout_origin(label)
    ok, o = label.compute_point(target, Graphene.Point().init(ox, oy))
    if not ok or eb <= sb:
        return []
    rects, it = [], layout.get_iter()
    while True:
        line = it.get_line_readonly()
        a, b = line.start_index, line.start_index + line.length
        if a < eb and b > sb:
            s, e = max(sb, a), min(eb, b)
            x0 = layout.index_to_pos(s).x
            x1 = layout.index_to_pos(e).x if e < b else (lambda r: r.x + r.width)(it.get_line_extents()[1])
            y0, y1 = it.get_line_yrange()
            if x1 > x0:
                rects.append(Graphene.Rect().init(o.x + x0 / Pango.SCALE, o.y + y0 / Pango.SCALE,
                                                  (x1 - x0) / Pango.SCALE, (y1 - y0) / Pango.SCALE))
        if not it.next_line():
            return rects


def inner_scroller(label, root):
    """The scroller a label of root's is seen through, a code block's or a table's; None if it's only root's own."""
    scroller = label.get_ancestor(Gtk.ScrolledWindow)
    return scroller if scroller is not None and scroller.is_ancestor(root) else None


def shade_passage(snapshot, fill, label, rects, target, root):
    """A passage's rectangles (passage_rects) filled, no further than the scroller it's seen through."""
    if not rects:
        return
    clip = inner_scroller(label, root)
    if clip is not None:
        snapshot.push_clip(clip.compute_bounds(target)[1])
    for rect in rects:
        snapshot.append_color(fill, rect)
    if clip is not None:
        snapshot.pop()


class SelectionMarks(Gtk.Widget):
    """A ChatSelection's passages, drawn over the chat."""

    def __init__(self, selection):
        super().__init__(can_target=False, hexpand=True, vexpand=True)
        self.selection = selection

    def do_snapshot(self, snapshot):
        self.selection.draw(snapshot, self)


class ChatSelection:
    """A selection across a chat's text. Each paragraph, list item, table cell and code block is a label of its own
    (MarkdownView), and GTK's selection stops at the end of the one it began in. A drag that stays in its label is GTK's
    as ever (a double click's word, a triple click's line, a link's click). One that goes on to another label is this
    one's from there: that label hears no more of it, what's passed of each label is shaded by this (only one label
    can hold a selection in GTK: each takes the primary clipboard, and that lets the last one's go), the view scrolls at
    its edges, and Copy takes it all, in order. Shift and a click extends it, and Ctrl+A or ⌘A takes the whole chat.

    root: what holds the labels. area: what's heard, its scroller, so a drag can begin beside the text. over: an overlay
    with root as its child, that the shading's put in. on_selected(text): a selection made across labels, once made.
    on_own(label): a drag or a click let go in the label it began in, that label's selection (GTK's) settled."""

    EDGE = 28  # px from the view's top or bottom where a drag scrolls it
    DRAG_PX = 8  # (begun beside the text: moved this far before it's a drag)
    TICK_MS = 16
    SPEED = 0.35  # px a tick for each px past that
    TOP_SPEED = 48
    NOT_TEXT = (Gtk.Button, Gtk.Scrollbar, Gtk.Picture, Gtk.Editable, Gtk.TextView, Gtk.Expander, Gtk.ListBoxRow)
    HEARD = (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE, Gdk.EventType.MOTION_NOTIFY)
    OTHERS = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.SUPER_MASK

    def __init__(self, root, area, over, on_selected=None, on_own=None):
        self.root, self.area, self.on_selected, self.on_own = root, area, on_selected, on_own
        self.anchor, self.pressed_label, self.dragging, self.across = None, None, False, False
        self.shown = {}  # (what's selected, in order: label -> (start, end, its text as it was))
        self.labels, self.lengths, self.index, self.tops, self.bottoms, self.size = [], [], {}, [], [], None
        self.pointer, self.pressed_at, self.ticking = (0, 0), (0, 0), None
        self.menu_at = None  # (what a right click there offers: (label, character) -> [(its name, its function)], or None)
        self.marks = SelectionMarks(self)
        if isinstance(view := area.get_child(), Gtk.Viewport):  # (the focus moving doesn't move the chat: a press on a
            view.set_scroll_to_focus(False)                     # paragraph half in view, or a menu giving the focus back)
        over.add_overlay(self.marks)
        over.connect("get-child-position", self._laid_out)
        area.get_vadjustment().connect("value-changed", lambda *_: self.shown and self.marks.queue_draw())
        legacy = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        legacy.connect("event", self._event)
        area.add_controller(legacy)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        area.add_controller(keys)

    def _laid_out(self, _over, child, _alloc):
        if child is self.marks and self.shown:  # (the chat laid out again: its passages elsewhere, perhaps)
            self.marks.queue_draw()
        return False

    def parts(self):
        """What's selected across labels, in order, each still as it was: [(label, start, end)]."""
        return [(label, start, end) for label, (start, end, text) in self.shown.items()
                if label.is_ancestor(self.root) and label.get_text() == text]

    def text(self):
        """What's selected across labels, as it reads: a blank line between paragraphs, a list's items on their lines
        with their markers, a table's cells apart by tabs, its rows on their lines, a diff's lines as they were."""
        out, before = [], None
        for label, start, end in self.parts():
            piece = label.get_text()[start:end].replace("\u2063", "")  # (a logo's place: LinkLabel)
            if before is not None:
                out.append(self._between(before, label))
            prefix = getattr(label, "list_prefix", None)
            out.append((prefix if prefix is not None and start == 0 else "") + piece)
            before = label
        return "".join(out)

    @staticmethod
    def _between(a, b):
        parent = a.get_parent()
        if isinstance(parent, Gtk.Grid) and b.get_parent() is parent:
            return "\t" if parent.query_child(a)[1] == parent.query_child(b)[1] else "\n"
        if parent is b.get_parent() and any(a.has_css_class(f"diff-{kind}") for kind in ("add", "del", "ctx")):
            return "\n"
        if getattr(a, "list_prefix", None) is not None and getattr(b, "list_prefix", None) is not None:
            return "\n"
        return "\n\n"

    def copy(self):
        self.root.get_clipboard().set(self.text())

    def clear(self):
        """Nothing selected: what this drew, and what GTK did in the label pressed last. (A press in a selection is
        then a new one, not GTK's dragging of the text away: no_text_drag's choice, for labels.)"""
        self._own_selection_gone()
        self.forget()

    def forget(self):
        """What it drew let go (Reader's passages draw theirs once there's more than one), and the chat's labels with
        it: read again for the next one, and not kept from going with their chat till then."""
        self.labels, self.lengths, self.index, self.tops, self.bottoms, self.size = [], [], {}, [], [], None
        if self.shown:
            self.shown = {}
            self.marks.queue_draw()

    def _own_selection_gone(self):
        if (label := self.pressed_label) is not None and label.is_ancestor(self.root) and \
                label.get_selection_bounds()[0]:
            label.select_region(0, 0)

    def select_all(self):
        self._collect()
        if self.labels:
            self._own_selection_gone()
            self.anchor = (self.labels[0], 0)
            self._select_to((self.labels[-1], self.lengths[-1]))
            self._told()

    def draw(self, snapshot, target):
        if not self.shown:
            return
        self._collect()
        ok, at = self.area.compute_point(self.root, Graphene.Point().init(0, 0))
        top, bottom = (at.y, at.y + self.area.get_height()) if ok else (-math.inf, math.inf)
        fill = accent_rgba()
        fill.alpha = 0.3
        for label, start, end in self.parts():
            i = self.index.get(label)
            if i is None or self.bottoms[i] < top or self.tops[i] > bottom:
                continue  # (off the view: not worked out, and a selection can be the whole chat)
            shade_passage(snapshot, fill, label, passage_rects(label, start, end, target), target, self.root)

    # ---- the pointer

    def _event(self, controller, _event):
        event = controller.get_current_event()  # (its argument's None under PyGObject 3.58)
        kind = event.get_event_type() if event is not None else None
        native = self.root.get_native()
        if kind not in self.HEARD or native is None:
            return False
        tx, ty = native.get_surface_transform()
        _ok, x, y = event.get_position()
        self.pointer = x - tx, y - ty
        state = event.get_modifier_state()
        if kind == Gdk.EventType.BUTTON_RELEASE:
            return self._release()
        if kind == Gdk.EventType.BUTTON_PRESS:
            button = event.get_button()
            if button == Gdk.BUTTON_SECONDARY:
                return self._context_menu()
            if button != Gdk.BUTTON_PRIMARY or state & self.OTHERS:
                self.dragging = False
                return False
            return self._press(bool(state & Gdk.ModifierType.SHIFT_MASK))
        if not self.dragging:
            return False
        if not state & Gdk.ModifierType.BUTTON1_MASK:  # (let go where this didn't hear it)
            return self._release()
        return self._move()

    def _in(self, widget):
        native = self.root.get_native()
        ok, p = native.compute_point(widget, Graphene.Point().init(*self.pointer))
        return (p.x, p.y) if ok else (0, 0)

    def _hit(self, dragging=False):
        """(label, character) under the pointer, else the nearest there is. Not dragging, None on what isn't text (a
        button, a picture, the scrollbar): a press there is its own."""
        x, y = self._in(self.area)
        w = self.area.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w is not self.area:
            if isinstance(w, Gtk.Label) and w.get_selectable() and w.is_ancestor(self.root):
                return w, char_at(w, *self._in(w))
            if not dragging and isinstance(w, self.NOT_TEXT):
                return None
            w = w.get_parent()
        return self._nearest()

    def _collect(self):
        """The chat's labels in order, with where each is: read again once the chat's another size."""
        size = self.root.get_width(), self.root.get_height()
        if size == self.size and self.labels:
            return
        self.size = size
        self.labels = [w for w in walk(self.root) if isinstance(w, Gtk.Label) and w.get_selectable() and w.get_mapped()]
        self.lengths, self.tops, self.bottoms = [], [], []
        self.index = {w: i for i, w in enumerate(self.labels)}
        low = -math.inf
        for w in self.labels:
            ok, b = w.compute_bounds(self.root)
            top, bottom = (b.get_y(), b.get_y() + b.get_height()) if ok else (low, low)
            low = max(low, bottom)
            self.tops.append(top)
            self.bottoms.append(low)  # (never going back up, for bisect: a table's cells share a row)
            self.lengths.append(len(w.get_text()))

    def _nearest(self):
        """Off every label: in the gap above one, its start (so a drag down has all before it, and a drag up all of
        it); level with one, the character across from the pointer; past them all, the end."""
        self._collect()
        if not self.labels:
            return None
        y = self._in(self.root)[1]
        i = bisect.bisect_left(self.bottoms, y)
        if i >= len(self.labels):
            return self.labels[-1], self.lengths[-1]
        if y < self.tops[i]:
            return self.labels[i], 0
        return self.labels[i], char_at(self.labels[i], *self._in(self.labels[i]))

    def _press(self, shift):
        hit = self._hit()
        if hit is None:  # (not text, the scrollbar or a button: theirs, and what's selected stays)
            self.dragging = False
            return False
        if shift and self.anchor is not None and self.anchor[0].is_ancestor(self.root):
            self.dragging = True
            self._begin_across()
            self._select_to(hit)
            return True  # (the label would begin a selection of its own)
        self.clear()
        self.across = False
        self.anchor, self.dragging, self.pressed_at = hit, True, self.pointer
        x, y = self._in(self.area)
        w = self.area.pick(x, y, Gtk.PickFlags.DEFAULT)
        self.pressed_label = hit[0] if w is hit[0] or (w is not None and w.is_ancestor(hit[0])) else None
        return False

    def _move(self):
        hit = self._hit(dragging=True)
        if hit is None:
            return False
        if not self.across:
            if hit[0] is self.pressed_label:
                return False  # (in the label it began in: GTK's)
            if self.pressed_label is None and math.dist(self.pointer, self.pressed_at) < self.DRAG_PX:
                return False  # (a click beside the text that moved a little: not a drag)
            self._begin_across()
        self._select_to(hit)
        return True

    def _release(self):
        was = self.dragging
        self.dragging = False
        if self.ticking is not None:
            GLib.source_remove(self.ticking)
            self.ticking = None
        if was:  # (the release has to reach the label, for its gestures to end: its drag's last selects to here)
            GLib.idle_add(self._settled, priority=GLib.PRIORITY_HIGH_IDLE)
        return False

    def _settled(self):
        if self.across:
            self._own_selection_gone()
            self._told()
        elif self.pressed_label is not None and self.on_own is not None:
            self.on_own(self.pressed_label)
        return False

    def _told(self):
        if self.on_selected is not None and (text := self.text()).strip():
            self.on_selected(text)

    def _begin_across(self):
        """From here it's this one's: GTK's selection in the label it began in gone, and the focus in the chat so Copy
        reaches it (a press on a label that can't take the focus, or beside the text, left it where it was)."""
        self.across = True
        self.size = None
        self._own_selection_gone()
        window = self.root.get_root()
        focus = window.get_focus() if window is not None else None
        if focus is None or not (focus is self.root or focus.is_ancestor(self.root)):
            self.root.set_focusable(True)
            self.root.grab_focus()
        if self.ticking is None:
            self.ticking = GLib.timeout_add(self.TICK_MS, self._tick)

    def _select_to(self, hit):
        self._collect()
        if self.anchor is None or self.anchor[0] not in self.index or hit[0] not in self.index:
            return  # (drawn again under the drag)
        (a, at), (b, to) = self.anchor, hit
        first, last = self.index[a], self.index[b]
        if (first, at) > (last, to):
            first, last, at, to = last, first, to, at
        want = {}
        for i in range(first, last + 1):
            start, end = at if i == first else 0, to if i == last else self.lengths[i]
            if end > start:
                want[self.labels[i]] = (start, end, self.labels[i].get_text())
        if want != self.shown:
            self.shown = want
            self.marks.queue_draw()

    def _tick(self):
        """While a drag across is at the view's top or bottom, or past it: the view scrolled, faster the further."""
        if self.root.get_native() is None:  # (the chat gone under the drag)
            self.ticking, self.dragging = None, False
            return False
        y, height = self._in(self.area)[1], self.area.get_height()
        past = y - self.EDGE if y < self.EDGE else y - (height - self.EDGE) if y > height - self.EDGE else 0
        if past:
            adj = self.area.get_vadjustment()
            before = adj.get_value()
            adj.set_value(before + max(-self.TOP_SPEED, min(self.TOP_SPEED, past * self.SPEED)))
            if adj.get_value() != before and (hit := self._hit(dragging=True)) is not None:
                self._select_to(hit)
        return True

    def _key(self, _controller, keyval, _code, state):
        if not state & self.OTHERS or state & Gdk.ModifierType.SHIFT_MASK or \
                keyval not in (Gdk.KEY_c, Gdk.KEY_C, Gdk.KEY_a, Gdk.KEY_A):
            return False
        window = self.root.get_root()
        if isinstance(window.get_focus() if window is not None else None, (Gtk.Editable, Gtk.TextView)):
            return False  # (a box in the chat: its own)
        if keyval in (Gdk.KEY_a, Gdk.KEY_A):
            self.select_all()
            return True
        if self.parts():
            self.copy()
            return True
        return False

    def _context_menu(self):
        """A right click on what's selected across labels: this menu, as a label's own has no selection to copy."""
        hit = self._hit(dragging=True)
        if hit is None:
            return False
        items = self.menu_at(*hit) if self.menu_at is not None else \
            [(wording.say("common.copy"), self.copy)] if hit[0] in self.shown else None
        if not items:
            return False
        menu, group = Gio.Menu(), Gio.SimpleActionGroup()
        for i, (label, fn) in enumerate(items):
            action = Gio.SimpleAction.new(f"item{i}", None)
            action.connect("activate", lambda _a, _p, fn=fn: fn())
            group.add_action(action)
            menu.append(label, f"chat-text.item{i}")
        self.root.insert_action_group("chat-text", group)
        pop = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        pop.set_parent(self.root)
        x, y = self._in(self.root)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()
        return True


def find_tints():
    """The (red, green, blue, alpha) of a match found and of the current one, for the light or dark theme: a code tab's
    and a page's (codeview.FindInText), a chat's (ChatFind)."""
    return ((255, 214, 102, 0.28), (255, 170, 40, 0.75)) if Adw.StyleManager.get_default().get_dark() else \
        ((255, 200, 0, 0.35), (255, 150, 0, 0.70))


class FindMarks(Gtk.Widget):
    """A ChatFind's matches, drawn over the chat's scroller."""

    def __init__(self, find):
        super().__init__(can_target=False, hexpand=True, vexpand=True, overflow=Gtk.Overflow.HIDDEN)
        self.find = find

    def do_snapshot(self, snapshot):
        self.find.draw(snapshot, self)


class ChatFind:
    """⌘F / Ctrl+F in a chat (Baude's, Meta's, a side question's, a subagent's, Record's Read): its find bar at the top
    of what's read (a bar over the chat, the text moved down for it, as a code tab's), every match in its text shaded,
    the current one more (find_tints, as a code tab's), "3 of 12" beside the box, Enter / ⌘G for the next, Shift+Enter /
    ⇧⌘G for the one before, Esc or × to close, the focus back where it was. A chat's text is many labels
    (MarkdownView): each label's matches are found, in order, and drawn over them; a part of a long chat set aside far
    from the view (BaudeWindow.cull_chunks) is in what's found, a closed tool card's or revealer's isn't. The current
    one's brought into view, in a code block's own scroller too. What's selected (one line) is what it looks for. A reply
    still coming in: its matches found again as Enter's pressed, from where the current one was.

    A long chat's earlier parts not drawn yet have no labels to look in: the count ends in + while there are some
    (undrawn), and stepping past the last match on the way to one draws the nearest that has the text in its transcript
    (beyond: BaudeWindow.find_beyond), then goes on to the match in it. Only as Enter's pressed, so typing reads no
    file, and only the parts that have it, so the chat isn't all drawn to be searched.

    root: what holds the labels, or a function giving it (a subagent's, whichever's shown). scroller: what they're read
    in (or, its own not scrolling, as a side question's in its row, the one it's in). pane: what the focus is in when
    this is the one ⌘F is for (the chat and its box: for_focus)."""

    MATCH_LIMIT = 2000
    live = weakref.WeakSet()  # (each made: for_focus)

    def __init__(self, root, scroller, pane=None):
        self._root, self.scroller, self.pane = root, scroller, pane or scroller
        self.matches, self.index, self.text, self.back_to = [], -1, "", None
        self.by_label, self.texts = {}, {}  # (each label's matches' places in matches, and its text as it was found)
        # (a chat with parts not drawn: undrawn() whether there are any; beyond(text, by, y0, y1, done) draws the
        # nearest that has text, going by (1 down, -1 up) from height y0 as far as y1 (None: round to y0 again; y0
        # None: from the view), calls done(whether it drew one) when it knows, and returns what stops its look)
        # drawn(): something that changes as its parts are drawn (by themselves too, as the view comes near): what
        # was found is found again then (_stale)
        self.undrawn = self.beyond = self.drawn = None
        self.drawn_then = None
        self.waiting = None  # (the frame waited for, a part just drawn laid out: _then)
        self.searching = None  # (what stops the look through the parts not drawn, while there's one: _step_beyond)
        self.marks = FindMarks(self)
        self.entry = Gtk.SearchEntry(placeholder_text=wording.say("find.placeholder"), hexpand=True)
        self.count = Gtk.Label(width_chars=10, xalign=1, css_classes=["dim-label", "caption"])
        bar = Gtk.Box(spacing=4)
        bar.append(self.entry)
        bar.append(self.count)
        for icon, tip, step in (("go-up-symbolic", wording.say("find.previous"), -1),
                                ("go-down-symbolic", wording.say("find.next"), 1)):
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=["flat"])
            btn.connect("clicked", lambda *_, step=step: self.step(step))
            bar.append(btn)
        self.bar = FindBar(child=bar, show_close_button=True)
        self.bar.add_css_class("chat-find")
        self.bar.connect_entry(self.entry)
        self.bar.connect("notify::search-mode-enabled", lambda *_: self.bar.get_search_mode() or self._closed())
        self.entry.connect("stop-search", lambda *_: self.bar.set_search_mode(False))
        self.entry.connect("search-changed", lambda e: self.find(e.get_text()))
        self.entry.connect("activate", lambda *_: self.step(1))
        self.entry.connect("next-match", lambda *_: self.step(1))
        self.entry.connect("previous-match", lambda *_: self.step(-1))
        keys = Gtk.EventControllerKey()  # (Shift+Enter: the match before)
        keys.connect("key-pressed", lambda _c, kv, _k, state: (self.step(-1), True)[1]
                     if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.SHIFT_MASK else False)
        self.entry.add_controller(keys)
        watch_style(self.marks, "notify::dark", lambda marks: marks.queue_draw())
        self.heard = []  # (the adjustments a scroll's heard on, to draw again: the chat's, and the one it's in: open)
        self._hear(scroller)
        ChatFind.live.add(self)

    def _hear(self, scroller):
        adj = scroller.get_vadjustment()
        if not any(a is adj for a in self.heard):
            self.heard.append(adj)
            adj.connect("value-changed", lambda *_: self.matches and self.marks.queue_draw())

    def wrap(self):
        """The bar over the scroller, the shading laid over that, for its pane to show in the scroller's place (an open
        compaction summary's heading pinned at its top too: SummaryPin)."""
        over = Gtk.Overlay(child=self.scroller, vexpand=True)
        over.add_overlay(self.marks)
        over.add_overlay(SummaryPin(self.scroller))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.bar)
        box.append(over)
        return box

    def root(self):
        return self._root() if callable(self._root) else self._root

    def scrolling(self):
        """What scrolls the chat: its scroller, or, that one never scrolling up or down, the one it's in."""
        scroller = self.scroller
        while scroller.get_policy()[1] == Gtk.PolicyType.NEVER and \
                (outer := scroller.get_parent() and scroller.get_parent().get_ancestor(Gtk.ScrolledWindow)) is not None:
            scroller = outer
        return scroller

    @classmethod
    def for_focus(cls, window):
        """The chat's find of window's that the focus is in (its pane), or None."""
        focus = window.get_focus()
        if focus is None:
            return None
        for find in cls.live:
            if find.pane.get_mapped() and (focus is find.pane or focus.is_ancestor(find.pane)):
                return find
        return None

    def open(self):
        """The bar shown, what's selected in the chat (a line of it) in it, and the focus in it."""
        window = self.pane.get_root()
        focus = window.get_focus() if isinstance(window, Gtk.Window) else None
        if focus is not None and not focus.is_ancestor(self.bar):
            self.back_to = weakref.ref(focus)
        self._hear(self.scrolling())  # (a side question's in its row: the list's)
        picked = self._picked()
        if picked:
            self.entry.set_text(picked)
        self.bar.set_search_mode(True)
        self.entry.grab_focus()
        self.entry.select_region(0, -1)
        if self.entry.get_text() and not self.matches:
            self.find(self.entry.get_text())
        return True

    def refresh(self):
        """What's shown changed under it (another subagent's): found again, if the bar's open."""
        if self.bar.get_search_mode():
            self.find(self.entry.get_text())

    def _picked(self):
        for w in self._labels():
            ok, a, b = w.get_selection_bounds()
            if ok and b > a and "\n" not in (text := w.get_text()[a:b]):
                return text
        return None

    def close(self):
        self.bar.set_search_mode(False)

    def _closed(self):
        """The bar closed (Esc, ×, close): the matches gone, and the focus, if it was in the bar, back where it was
        (a label's selection as it was: GTK selects all of one given the focus)."""
        self.matches, self.index, self.text, self.by_label, self.texts = [], -1, "", {}, {}
        self._stop_search()
        if self.waiting is not None:
            self.waiting[0].remove_tick_callback(self.waiting[1])
            self.waiting = None
        self.count.set_label("")
        self.marks.queue_draw()
        window = self.bar.get_root()
        focus = window.get_focus() if isinstance(window, Gtk.Window) else None
        back = self.back_to() if self.back_to is not None else None
        if back is None or not back.get_mapped() or (focus is not None and not focus.is_ancestor(self.bar)):
            return
        if isinstance(back, Gtk.Label):
            ok, a, b = back.get_selection_bounds()
            back.grab_focus()
            back.select_region(a, b) if ok else back.select_region(0, 0)
        else:
            focus_shown(back)

    def _labels(self):
        """The chat's text, in order: its selectable labels shown, or set aside (cull_chunks), not in what's hidden
        (a closed revealer, a closed expander's child)."""
        out = []

        def visit(w):
            if not w.get_visible():
                return
            if isinstance(w, Gtk.Label):
                if w.get_selectable():
                    out.append(w)
                return
            if isinstance(w, Gtk.Revealer) and not w.get_child_revealed():
                return
            closed = w.get_child() if isinstance(w, Gtk.Expander) and not w.get_expanded() else None
            child = w.get_first_child()
            while child is not None:
                if child is not closed:
                    visit(child)
                child = child.get_next_sibling()

        if (root := self.root()) is not None:
            visit(root)
        return out

    def find(self, text, after=None, quiet=False):
        """Every match of text in the chat's labels (case ignored), in order; the current one the first at or below
        after (a height in what's scrolled), else the first in view, else the first. quiet: none made the current one
        (its caller picks)."""
        self._stop_search()
        self.text, self.matches, self.index, self.by_label, self.texts = text, [], -1, {}, {}
        self.drawn_then = self.drawn() if self.drawn is not None else None
        self.marks.queue_draw()
        if not text:
            self.count.set_label("")
            return
        pattern = re.compile(re.escape(text), re.IGNORECASE)
        for label in self._labels():
            said = label.get_text()
            for m in pattern.finditer(said):
                if m.end() > m.start() and len(self.matches) < self.MATCH_LIMIT:
                    self.by_label.setdefault(label, []).append(len(self.matches))
                    self.matches.append((label, m.start(), m.end()))
            if label in self.by_label:
                self.texts[label] = said
            if len(self.matches) >= self.MATCH_LIMIT:
                break
        if not self.matches:
            # (Enter looks in what's not drawn)
            self.count.set_label(wording.say("find.earlier") if self._more() else wording.say("find.no_matches"))
            return
        if quiet:
            return
        if after is None:
            after = self.scrolling().get_vadjustment().get_value()
        self.show(self._first_below(after))

    def _more(self):
        return self.undrawn is not None and bool(self.undrawn())

    def _then(self, fn):
        """fn once what was just drawn is laid out (two frames on: its labels have their places then)."""
        scroller, frames = self.scrolling(), [0]
        if self.waiting is not None:
            self.waiting[0].remove_tick_callback(self.waiting[1])

        def tick(*_):
            frames[0] += 1
            if frames[0] < 2:
                return True
            self.waiting = None
            fn()
            return False
        self.waiting = (scroller, scroller.add_tick_callback(tick))

    def _stop_search(self):
        if self.searching is not None:
            self.searching()
            self.searching = None

    def _step_beyond(self, by):
        """Before the step: an undrawn part with the text between the current match and the next that way looked for
        (beyond: "searching…" meanwhile, the window still drawn and scrolled), and drawn if there's one; the step's
        made once that's laid out: to the match next to the current one, which is in the part drawn if it shows the
        text. None between: the step as it is. Whether it's looking (the step's made later)."""
        if self.beyond is None or not self._more():
            return False
        content, n = self._content(self.scrolling()), len(self.matches)
        y0 = y1 = key = None
        if n:
            label, start, _end = self.matches[self.index]
            key = (label, start)
            if (r := self._rect(self.index, content)) is not None:
                y0 = r.get_y()
            j = self.index + by
            if 0 <= j < n and (r := self._rect(j, content)) is not None:  # (else round the end: all that way)
                y1 = r.get_y()
            if y0 is None:
                return False
        text = self.text

        def go(drew):
            """The step, among the matches as they are now (found again: parts are drawn by themselves too, as the
            view comes near them, and their matches weren't in what was found before)."""
            if text != self.text:
                return
            self.find(text, quiet=True)
            at = next((i for i, (label, start, _e) in enumerate(self.matches) if (label, start) == key), None)
            if at is not None:
                self.show((at + by) % len(self.matches))
            elif self.matches:  # (none before: the first in what was drawn, or going up its last)
                self.show(self._first_below(self.scrolling().get_vadjustment().get_value()) if by > 0
                          else len(self.matches) - 1)
            elif drew:  # (drawn, and it doesn't show the text after all: the next part that has it)
                self.step(by)
            else:
                self.count.set_label(wording.say("find.no_matches"))

        def done(drew):
            self.searching = None
            if drew:
                self._then(lambda: go(True))  # (once it's laid out: its labels have their places then)
            else:
                go(False)
        self.count.set_label(wording.say("find.searching"))
        self.searching = stop = lambda: None  # (till it's known what stops it: done may be called at once)
        stops = self.beyond(text, by, y0, y1, done)
        if self.searching is stop:
            self.searching = stops
        return True

    def _first_below(self, y):
        """The first match whose label reaches below y (in what's scrolled), else the first."""
        content = self._content(self.scrolling())
        for label, idxs in self.by_label.items():
            ok, b = label.compute_bounds(content) if content is not None else (False, None)
            if ok and b.get_y() + b.get_height() > y:
                for i in idxs:
                    if (r := self._rect(i, content)) is not None and r.get_y() + r.get_height() > y:
                        return i
        return 0

    def _stale(self):
        if self.drawn is not None and self.drawn() != self.drawn_then:
            return True
        root = self.root()
        return any(root is None or not label.is_ancestor(root) or label.get_text() != said
                   for label, said in self.texts.items())

    def step(self, by):
        """The next match (by 1) or the one before (-1): found again first, from where the current one was, if the
        chat's changed under them. Not while a look through the parts not drawn is on (_step_beyond: that steps)."""
        if self.searching is not None:
            return
        if self.matches and self._stale():
            r = self._rect(self.index, self._content(self.scrolling())) if self.index >= 0 else None
            was = r.get_y() if r is not None else None
            key = self.matches[self.index][:2] if self.index >= 0 else None
            self.find(self.text, quiet=True)
            at = next((i for i, m in enumerate(self.matches) if m[:2] == key), None)
            if at is not None:  # (the current one's still there, more found around it: the step's from it)
                self.index = at
            else:
                self.find(self.text, after=None if was is None else was + (1 if by > 0 else 0))
                if by > 0 or not self.matches:
                    return
        if not self.matches:
            if self.entry.get_text():
                self.find(self.entry.get_text())
                if not self.matches:
                    self._step_beyond(by)
            return
        if self._step_beyond(by):
            return
        self.show((self.index + by) % len(self.matches))

    def show(self, i):
        """The i'th match the current one, brought into view: in its code block's scroller, then in the chat's."""
        self.index = i
        more = len(self.matches) >= self.MATCH_LIMIT or self._more()
        self.count.set_label(wording.say("find.match_of_more", at=i + 1, total=len(self.matches)) if more else
                             wording.say("find.match_of", at=i + 1, total=len(self.matches)))
        self.marks.queue_draw()
        label = self.matches[i][0]
        root = self.root()
        inner = inner_scroller(label, root) if root is not None else None
        if inner is not None:
            self._bring(inner, i, sideways=True)
        self._bring(self.scrolling(), i)

    def _bring(self, scroller, i, sideways=False):
        r = self._rect(i, self._content(scroller), last=True)
        if r is None:
            return
        top, bottom, x0, x1 = r
        adj = scroller.get_vadjustment()
        if top < adj.get_value() or bottom > adj.get_value() + adj.get_page_size():  # (else a third of the way down)
            adj.set_value(max(0, top - adj.get_page_size() / 3))
        if sideways and scroller.get_policy()[0] != Gtk.PolicyType.NEVER:
            h = scroller.get_hadjustment()
            if x0 < h.get_value() or x1 > h.get_value() + h.get_page_size():
                h.set_value(max(0, x0 - h.get_page_size() / 3))

    @staticmethod
    def _content(scroller):
        child = scroller.get_child()
        return child.get_child() if isinstance(child, Gtk.Viewport) else child

    def _rect(self, i, content, last=False):
        """Where the i'th match is in content (what a scroller scrolls): its first line's rectangle; last: (top, bottom,
        left, right) of all its lines."""
        label, start, end = self.matches[i]
        rects = passage_rects(label, start, end, content) if content is not None else []
        if not rects:
            return None
        if last:
            return (rects[0].get_y(), rects[-1].get_y() + rects[-1].get_height(),
                    min(r.get_x() for r in rects), max(r.get_x() + r.get_width() for r in rects))
        return rects[0]

    def draw(self, snapshot, target):
        """The matches in view shaded: each label looked at once, not each match."""
        if not self.matches:
            return
        height = target.get_height()
        match, current = (Gdk.RGBA() for _ in range(2))
        for rgba, (r, g, b, a) in zip((match, current), find_tints()):
            rgba.red, rgba.green, rgba.blue, rgba.alpha = r / 255, g / 255, b / 255, a
        root, origin = self.root(), Graphene.Point().init(0, 0)
        for label, idxs in self.by_label.items():
            if not label.get_mapped() or label.get_text() != self.texts[label]:
                continue
            ok, at = label.compute_point(target, origin)
            if not ok or at.y > height or at.y + label.get_height() < 0:
                continue  # (off the view)
            for i in idxs:
                _label, start, end = self.matches[i]
                shade_passage(snapshot, current if i == self.index else match, label,
                              passage_rects(label, start, end, target), target, root)
