"""panels — what opens, slides and moves in the windows: the main area's tabs (TabBody, MainTabs), a side bar
sliding open (SideSlide, PaneSlide; SLIDE_MS), a page opening out of its row (ExpandStack), and a scroller's
own glide (ScrollGlide) and place (scroll_place, hold_place).
"""
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402,F401

from baudelib import tokens  # noqa: E402  (the measures and colours below: schema/tokens, the Android app's too)
from baudelib import wording  # noqa: E402
from widgets import ensure_icons, paned_rule, toast, walk  # noqa: E402
from markdownview import CodeBlock  # noqa: E402


class TabBody(Gtk.Widget):
    """What's open in one of MainTabs' tabs, as its Adw.TabView has it: as large as what's in it while its tab is the
    one shown, and no size at all while it isn't. (The tab view is as large as the largest of all its tabs, the ones
    not shown too: a Feedback tab open behind the chat held the chat at the Feedback's least, 755px, and the side bars
    beside it back, or under them it went.)"""

    def __init__(self, child):
        super().__init__()
        self.child, self.shown = child, False
        child.set_parent(self)

    def set_shown(self, shown):
        if shown != self.shown:
            self.shown = shown
            self.queue_resize()

    def do_measure(self, orientation, for_size):
        if not self.shown or self.child is None:
            return 0, 0, -1, -1
        least, natural, least_base, natural_base = self.child.measure(orientation, for_size)
        return least, natural, least_base, natural_base

    def do_size_allocate(self, width, height, baseline):
        if self.child is not None:
            self.child.allocate(width, height, baseline, None)

    def do_dispose(self):
        if self.child is not None:
            self.child.unparent()
            self.child = None
        Gtk.Widget.do_dispose(self)


def tab_child(page):
    """What's open in a tab (an Adw.TabPage of MainTabs'): its TabBody's child."""
    return page.get_child().child


class MainTabs(Gtk.Box):
    """The main area's tabs, Baude's and Record's alike: what's open in it (a chat, a page of notes, a file's code),
    each in a tab of its own (Adw.TabView), and opened again, shown again, not opened twice (open: by its key). bar
    (an Adw.TabBar) is the window's to put where it wants it. A pinned one (Baude's chat) is first, and never closed;
    shown_alone False: the bar's hidden while there's nothing but that one. closed(key, child): called as one is
    closed (its ×, close_current). copy_links (Record's): right-click on one to copy its link (copy_link); rename
    (Record's, with it): (can(child), ask(child)), Rename… on those it can rename (a chat's). Only the
    tab shown has a size: the main area's least is that one's, not the widest of all that's open (TabBody).

    What's been shown is kept as a browser keeps its history (each tab shown, a link followed to it; a file's code at
    each line it was shown at: remember): back and forward show it again, reopen(key, line) asked to open it again
    (its tab closed since; a file's code, always, at its line) and False if it can't be (gone)."""

    HISTORY = 100

    def __init__(self, closed=None, shown_alone=True, reopen=None, copy_links=False, rename=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.view = Adw.TabView(vexpand=True, hexpand=True)
        self.bar = Adw.TabBar(view=self.view, autohide=False)
        # (its scrolling ScrollGlide's, as the chat's is, ahead of the tab box's own: on macOS every scroll is a
        # mouse's, and the bar took each of a touchpad's pixels up or down for a wheel's click, some 80 px, and its
        # sideways ones 2.5 times over as they came. Its scrollers: the pinned tabs', the others')
        self.bar_glides = [ScrollGlide(sw, sideways=True)
                           for sw in walk(self.bar) if isinstance(sw, Gtk.ScrolledWindow)]
        self.pages, self.closed, self.shown_alone = {}, closed, shown_alone
        self.menu_page = None  # (the tab its menu was opened on, if it has one: copy_links)
        self.history, self.at, self.moving, self.reopen = [], -1, False, reopen
        self.view.connect("notify::selected-page", lambda *_: self._sized())
        self.view.connect("notify::selected-page", lambda *_: self.remember())
        self.view.connect("close-page", self._close)
        for sig in ("page-attached", "page-detached"):
            self.view.connect(sig, lambda *_: self._bar_shown())
        self.append(self.view)
        if copy_links:
            menu, acts, copy = Gio.Menu(), Gio.SimpleActionGroup(), Gio.SimpleAction(name="copy-link")
            menu.append(wording.say("tabs.copy_link"), "tab.copy-link")
            self.view.set_menu_model(menu)
            self.view.connect("setup-menu", lambda _v, page: page is not None and setattr(self, "menu_page", page))
            copy.connect("activate", lambda *_: self.menu_page is not None and self.copy_link(tab_child(self.menu_page)))
            acts.add_action(copy)
            if rename is not None:
                can, ask = rename
                menu.append(wording.say("common.rename_ellipsis"), "tab.rename")
                named = Gio.SimpleAction(name="rename")
                named.connect("activate", lambda *_: self.menu_page is not None and ask(tab_child(self.menu_page)))
                acts.add_action(named)
                self.view.connect("setup-menu", lambda _v, page: page is not None and named.set_enabled(
                    bool(can(tab_child(page)))))
            self.bar.insert_action_group("tab", acts)

    def _bar_shown(self):
        # (Adw.TabBar's own autohide never hides with a pinned tab: by hand)
        if not self.shown_alone:
            self.bar.set_visible(self.view.get_n_pages() > self.view.get_n_pinned_pages())

    def _sized(self):
        """The tab shown is the one with a size (TabBody)."""
        shown = self.view.get_selected_page()
        for i in range(self.view.get_n_pages()):
            page = self.view.get_nth_page(i)
            page.get_child().set_shown(page is shown)

    def open(self, key, make, title, icon=None, tooltip=None, pinned=False, select=True, quiet=False):
        """What's open under key (made by make() the first time), its tab titled title (icon: a themed icon's name),
        shown (select). quiet: not kept in the history yet (a file's code, kept at its line once it's there:
        remember). Returns its widget."""
        page = self.pages.get(key)
        if page is None:
            child = make()
            child.tab_key = key
            body = TabBody(child)
            self.pages[key] = page = self.view.append_pinned(body) if pinned else self.view.append(body)
            self._sized()  # (the first: shown as it's put in; another: shown once it's picked)
        page.set_title(title)
        page.set_tooltip(GLib.markup_escape_text(tooltip or title))
        if icon:
            ensure_icons()  # (baude-…-symbolic: Baude's own, written where the icon theme looks)
            page.set_icon(Gio.ThemedIcon.new(icon))
        if select:
            was, self.moving = self.moving, self.moving or quiet
            try:
                self.view.set_selected_page(page)
            finally:
                self.moving = was
        return tab_child(page)

    def get(self, key):
        return tab_child(page) if (page := self.pages.get(key)) is not None else None

    def current(self):
        return tab_child(page) if (page := self.view.get_selected_page()) is not None else None

    def children(self):
        """What's open, in the tabs' order."""
        return [tab_child(self.view.get_nth_page(i)) for i in range(self.view.get_n_pages())]

    def show(self, key):
        if (page := self.pages.get(key)) is not None:
            self.view.set_selected_page(page)
        return page is not None

    def retitle(self, key, title, new_key=None, tooltip=None):
        """A tab's title (and key: what's open in it renamed) changed."""
        if (page := self.pages.get(key)) is None:
            return
        if new_key is not None and new_key != key:
            self.pages[new_key] = self.pages.pop(key)
            tab_child(page).tab_key = new_key
        page.set_title(title)
        page.set_tooltip(GLib.markup_escape_text(tooltip or title))

    def close(self, key):
        if (page := self.pages.get(key)) is not None:
            self.view.close_page(page)

    def copy_link(self, child=None):
        """A tab's absolute link (its child's tab_link(); child: the one shown) on the clipboard, to paste into an agent: a path
        it can read, a page of notes' file, a chat's transcript, a file's code at its line. Returns it (None: it has none)."""
        child = child or self.current()
        link = child.tab_link() if hasattr(child, "tab_link") else None
        if link:
            self.get_clipboard().set(link)
        toast(self, wording.say("tabs.link_copied") if link else wording.say("tabs.no_link"))
        return link

    # ---- history
    def remember(self):
        """What's shown now, kept (unless it's what was last, or it's being gone back or forward to): forward from
        here is forgotten, as a browser forgets it."""
        child = self.current()
        if self.moving or child is None or getattr(child, "tab_key", None) is None:
            return
        entry = (child.tab_key, getattr(child, "line", None) if child.tab_key[0] == "code" else None)
        if 0 <= self.at < len(self.history) and self.history[self.at] == entry:
            return
        del self.history[self.at + 1:]
        self.history.append(entry)
        del self.history[:-self.HISTORY]
        self.at = len(self.history) - 1

    def back(self):
        """What was shown before (⌥⌘←): shown again. False if there's nothing before."""
        return self._go(-1)

    def forward(self):
        """What was shown after it, gone back from (⌥⌘→): shown again. False if there's nothing after."""
        return self._go(1)

    def _go(self, step):
        i = self.at + step
        while 0 <= i < len(self.history):
            key, line = self.history[i]
            self.moving = True
            try:
                if key[0] == "code" and self.reopen is not None:  # (at its line: the window's show_code)
                    done = self.reopen(key, line)
                else:
                    done = self.show(key) or (self.reopen is not None and bool(self.reopen(key, line)))
            finally:
                self.moving = False
            if done:
                self.at = i
                return True
            del self.history[i]  # (gone: the next one along)
            if step < 0:
                self.at -= 1
                i -= 1
        return False

    def close_current(self):
        """The tab shown closed (⌘W), unless it's pinned. True if one was."""
        page = self.view.get_selected_page()
        if page is None or page.get_pinned():
            return False
        self.view.close_page(page)
        return True

    def _close(self, view, page):
        child = tab_child(page)
        key = getattr(child, "tab_key", None)
        if self.pages.get(key) is page:
            del self.pages[key]
        if self.menu_page is page:
            self.menu_page = None
        view.close_page_finish(page, True)
        if self.closed is not None:
            self.closed(key, child)
        return True  # (handled: finished here)


SLIDE_MS = 180  # (a side bar opening or closing: SideSlide. The tests set it to 0)


class SideSlide:
    """A side bar opened and closed smoothly, whatever opens it: toggles (kept in step), set_shown, toggle. It's visible
    from the moment it starts to open until it has finished closing, and what's beside it moves over it meanwhile; its
    `visible` set directly just takes effect, the toggles following. A subclass says how far open it is: value, apply,
    closed_value, open_value, and host (the widget on screen whatever the bar's doing)."""

    def __init__(self, pane, toggles=()):
        self.pane, self.toggles, self.anim, self.quiet = pane, list(toggles), None, False
        self.target = pane.get_visible()
        self.glide, self.held_shut, self.held_at, self.when_glided = None, False, None, None  # (a drag's: hold)
        self.moving = None  # (told True as it starts to slide or be dragged, False once it's still: motion)
        for toggle in self.toggles:
            toggle.set_active(self.target)
            toggle.connect("toggled", self._toggled)
        pane.connect("notify::visible", self._visible)

    def _toggled(self, toggle):
        if not self.quiet:
            self.set_shown(toggle.get_active())

    def _visible(self, *_):
        if not self.quiet:  # (set directly)
            self.target = self.pane.get_visible()
            self._sync_toggles()

    def _sync_toggles(self):
        self.quiet = True
        for toggle in self.toggles:
            toggle.set_active(self.target)
        self.quiet = False

    def _set_visible(self, on):
        was, self.quiet = self.quiet, True
        self.pane.set_visible(on)
        self.quiet = was

    def toggle(self):
        self.set_shown(not self.target)

    def set_shown(self, show):
        if show == self.target:
            return
        self.target = show
        self._sync_toggles()
        moving = self.anim is not None
        if moving:  # (turned back part way: from where it's got to)
            self.anim.pause()
            self.anim = None
        if not SLIDE_MS or not self.host.get_mapped():
            self._set_visible(show)
            if moving:
                self.settle()
            return
        if not moving:
            self.begin()
            self.motion(True)
            if show and not self.pane.get_visible():
                self._set_visible(True)
                self.remember_open()
                self.apply(self.closed_value())
            elif not show:
                self.remember_open()
        self.anim = Adw.TimedAnimation.new(self.host, self.value(), self.open_value() if show else self.closed_value(),
                                           SLIDE_MS, Adw.CallbackAnimationTarget.new(self.apply))
        self.anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self.anim.connect("done", self._done)
        self.anim.play()

    def hold(self, shut, at):
        """A drag going on. Dragged shut, it slides shut, but it's all still there (hidden, what's being dragged would go, and
        the drag with it) and the toggles are as they were; dragged out again, it slides back to at. Only let_go settles it.
        True while the drag's not to move it itself."""
        self.held_at = at
        if shut != self.held_shut:
            self.held_shut = shut
            self._glide(self.closed_value() if shut else at)
        return shut or self.glide is not None

    def _glide(self, to):
        if self.glide is not None:
            self.glide.pause()
            self.glide = None
        self.begin()
        if not SLIDE_MS or not self.host.get_mapped():
            self.apply(to)
            self._glided()
            return
        self.glide = Adw.TimedAnimation.new(self.host, self.value(), to, SLIDE_MS, Adw.CallbackAnimationTarget.new(self.apply))
        self.glide.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self.glide.connect("done", self._glided)
        self.glide.play()

    def _glided(self, *_):
        self.glide = None
        if not self.held_shut:  # (out again)
            self.open_at(self.held_at)
            self.settle()
        if self.when_glided is not None:
            self.when_glided()
            self.when_glided = None

    def let_go(self, at):
        """The drag's over. Shut as it ended: closed now (True), and it'll open at at."""
        if not self.held_shut:
            return False
        self.held_shut = False
        if self.glide is not None:
            self.glide.pause()
            self.glide = None
        self.set_shown(False)
        self.open_at(at)
        if self.anim is None:  # (no slide: settle's not to come)
            self.settle()
        return True

    def _done(self, _anim):
        self.anim = None
        if not self.target:
            self._set_visible(False)
        self.settle()
        self.motion(False)

    def motion(self, on):
        """It's started moving (on: a slide, a drag), or it's still again: moving told, if anything's to know."""
        if self.moving is not None:
            self.moving(on)

    def begin(self):
        """About to move: whatever lets it go narrower than it can be."""

    def settle(self):
        """Moved: as it was."""


class PaneSlide(SideSlide):
    """A side bar that's one side of a Gtk.Paned (end: its end child): the rule between them moved. Its child can be
    clipped narrower than its minimum only while it's moving; afterwards as it was set. The rule's drag is this one's, not
    the paned's (that would have the rule jump by what CSS moves it, paned.main-split): it stops at the bar's least, and only
    dragged on till that would leave the bar CLOSE_WITHIN wide does the bar slide shut, and only let go there does it stay shut
    (hold), opening again at the width it was."""

    CLOSE_WITHIN = 24  # px

    def __init__(self, paned, pane, toggles=(), end=True):
        self.host, self.end, self.open_size, self.begun = paned, end, None, False
        self.dragged_from, self.limits = None, None  # (where the rule was as a drag of it began, and where it can go)
        self.closed_by_drag = None  # (told the width it was then, on closing)
        super().__init__(pane, toggles)
        for controller in list(paned.observe_controllers()):
            if isinstance(controller, Gtk.GestureDrag):  # (GTK's: the pointer's, and touch's)
                controller.set_propagation_phase(Gtk.PropagationPhase.NONE)
        drag = Gtk.GestureDrag(button=1, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        paned.add_controller(drag)

    def _drag_begin(self, gesture, x, y):
        if self.anim is not None or self.glide is not None or \
                self.host.pick(x, y, Gtk.PickFlags.DEFAULT) is not paned_rule(self.host):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.dragged_from = self.host.get_position()
        self.limits = self.host.get_property("min-position"), self.host.get_property("max-position")
        self.motion(True)

    def _drag_update(self, _gesture, dx, _dy):
        if self.dragged_from is None:
            return
        wanted = self.dragged_from + dx
        at = round(max(self.limits[0], min(self.limits[1], wanted)))
        if not self.hold((self.host.get_width() - wanted if self.end else wanted) <= self.CLOSE_WITHIN, at):
            self.host.set_position(at)

    def _drag_end(self, _gesture, _dx, _dy):
        if self.dragged_from is None:
            return
        if self.held_shut:
            position, self.dragged_from = self.dragged_from, None
            if self.closed_by_drag is not None:
                self.closed_by_drag(self.host.get_width() - position if self.end else position)
            self.let_go(position)
        elif self.glide is not None:  # (on its way back to where it was let go: still the drag's)
            self.when_glided = lambda: (setattr(self, "dragged_from", None), self.motion(False))
        else:
            self.dragged_from = None
            self.motion(False)

    def following(self):
        """The rule's where a drag of it has put it, this moment: not sliding shut, or back, nor slid by a toggle meanwhile."""
        return self.dragged_from is not None and not self.held_shut and self.glide is None and self.anim is None

    def open_at(self, position):
        self.open_size = self.host.get_width() - position if self.end else position
        self.host.notify("position")  # (its slide's last wasn't the drag's, to what keeps track of that: following; this is)

    def value(self):
        return float(self.host.get_position())

    def apply(self, value):
        self.host.set_position(round(value))

    def closed_value(self):
        return float(self.host.get_width()) if self.end else 0.0

    def remember_open(self):
        position = self.host.get_position()
        size = self.host.get_width() - position if self.end else position
        # (never less than it can be: closed since it started there's no position yet, and it'd open at nothing, nothing to drag)
        self.open_size = max(size, self.pane.measure(Gtk.Orientation.HORIZONTAL, -1)[0])

    def open_value(self):
        return float(self.host.get_width() - self.open_size if self.end else self.open_size)

    def begin(self):
        if not self.begun:
            self.begun = True
            self.shrink = self.host.get_shrink_end_child() if self.end else self.host.get_shrink_start_child()
            (self.host.set_shrink_end_child if self.end else self.host.set_shrink_start_child)(True)

    def settle(self):
        self.begun = False
        (self.host.set_shrink_end_child if self.end else self.host.set_shrink_start_child)(self.shrink)
        if self.open_size is not None:  # (shown again directly: at the width it was)
            self.host.set_position(round(self.open_value()))


RIDE_GROW = 2  # (px each side drawn of what rides in ExpandStack's square: an outline drawn just outside it)
EXPAND_MS = tokens.ms("motion.expand")  # (a list's row opened into its page, and closed back into it: ExpandStack. 0:
# at once)


def _lerp(a, b, p):
    return a + (b - a) * p


def _rect(widget, to):
    ok, r = widget.compute_bounds(to)
    return (r.get_x(), r.get_y(), r.get_width(), r.get_height()) if ok else None


def _graphene(r):
    return Graphene.Rect().init(*r)


def _opacity(widget):
    """widget's opacity as it's drawn, its CSS's (a dim label's) too: what its parent's drawing of it is under. None
    if it can't be drawn now (waiting to be laid out)."""
    parent = widget.get_parent()
    if parent is None:
        return None
    snap = Gtk.Snapshot()
    parent.snapshot_child(widget, snap)
    node = snap.to_node()
    if node is None:
        return None
    while node is not None:
        if isinstance(node, Gsk.OpacityNode):
            return node.get_opacity()
        if isinstance(node, Gsk.ContainerNode):  # (a wrapped label's lines: under one opacity, or none)
            node = node.get_child(0) if node.get_n_children() == 1 else None
        else:
            node = node.get_child() if hasattr(node, "get_child") else None
    return 1.0


def _frozen(widget, grow=0):
    """A picture of widget as it is now, drawn now (not what it last drew: a redraw asked for, a spinner's, say, has
    thrown that away till the next frame), at its own size (grow: that much more each side, for what it draws outside
    itself, an outline's box-shadow); what it last drew if it can't be drawn now."""
    parent = widget.get_parent()
    ok, r = widget.compute_bounds(parent) if parent is not None else (False, None)
    if ok:
        snap = Gtk.Snapshot()
        snap.translate(Graphene.Point().init(grow - r.get_x(), grow - r.get_y()))
        parent.snapshot_child(widget, snap)
        node = snap.to_node()
        if node is not None:
            picture = Gtk.Snapshot()
            picture.append_node(node)
            return picture.to_paintable(Graphene.Size().init(r.get_width() + 2 * grow, r.get_height() + 2 * grow))
    return Gtk.WidgetPaintable.new(widget).get_current_image()


def _text(label, to):
    """Where label's text is drawn (in to's coordinates) and how: its font, colour, width and wrapping, for ExpandStack
    to draw it there itself, or anywhere between; None if it isn't laid out."""
    r = _rect(label, to) if label is not None else None
    if r is None:
        return None
    ox, oy = label.get_layout_offsets()  # (the label's own coordinates: from its start)
    ok, b = label.compute_bounds(label)
    tw, th = label.get_layout().get_pixel_size()
    return {"x": r[0] + ox - (b.get_x() if ok else 0), "y": r[1] + oy - (b.get_y() if ok else 0), "w": r[2],
            "h": r[3], "tw": tw, "th": th, "font": label.get_pango_context().get_font_description().copy(),
            "color": label.get_color(), "alpha": _opacity(label), "text": label.get_text(), "wrap": label.get_wrap(),
            "layout": label.get_layout().copy(), "baseline": label.get_layout().get_baseline() / Pango.SCALE}


def _place(widget, to):
    """Where widget is, for ExpandStack: a label's text (_text), anything else's rectangle."""
    if isinstance(widget, Gtk.Label):
        return _text(widget, to)
    r = _rect(widget, to) if widget is not None else None
    return None if r is None else {"x": r[0], "y": r[1], "w": r[2], "h": r[3]}


class ExpandStack(Gtk.Widget):
    """A Gtk.Stack (stack: its pages added and shown as any stack's) whose page of one thing opens out of that thing's
    row in a list (open), and closes back into it (close). Everything moves; nothing fades in or out but the row's
    background. The row goes up to be the page's header, as wide as the page's square (its darker inside) all the way,
    and the square opens under it: the rows above are pushed up off the page, those below down off it. The row's parts
    each become the header's (pairs): a label's text drawn as one line all the way, its place, size, weight and colour
    turning into the other's; anything else drawn as the header's, going from one place to the other. What only the
    page has slides in from the left (enter), what only the row has out to the right (leave); what's in the square
    comes with the header, shown as the square gets to it. Between the two pages nothing else is drawn of them."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.stack = Gtk.Stack(hexpand=True, vexpand=True)
        self.stack.set_parent(self)
        self.square = Gtk.Box(can_target=False)  # (the square as it grows: the page's square's background, any size)
        self.square.set_parent(self)
        self.square.set_child_visible(False)
        self.anim, self.move = None, None
        self.stilled = None  # (the list kept from the pointer while it moves: _still)

    def do_get_request_mode(self):
        return self.stack.get_request_mode()

    def do_measure(self, orientation, for_size):
        return self.stack.measure(orientation, for_size)

    def do_size_allocate(self, width, height, baseline):
        self.stack.allocate(width, height, baseline, None)
        m = self.move
        if m is None:
            return
        if m["live"]:  # (opening: the page's parts where they are as it's laid out)
            m["page"] = _rect(m["page_widget"], self) or m["page"]
            for part in m["parts"].values():
                part["rect"] = _rect(part["widget"], self) or part["rect"]
            for pair in m["pairs"]:
                pair["b"] = _place(pair["to"], self) or pair["b"]
            for ride in m["rides"]:
                ride["b"] = _rect(ride["to"], self) or ride["b"]
        else:  # (closing: the row where it is, the list shown again; not seen, nothing to move: over)
            m["row"] = self._seen(m["origin"], m["within"], m["across"])
            m["body"] = self._seen(m["body_widget"], m["within"]) if m.get("body_widget") is not None else None
            if m["row"] is None and not m.get("ending"):
                m["ending"] = True
                GLib.idle_add(lambda anim=self.anim: (anim is self.anim and self.finish(), False)[1])
            for pair in m["pairs"]:
                pair["a"] = _place(pair["from"], self)
            for ride in m["rides"]:
                ride["a"] = _rect(ride["from"], self) if self._whole(ride["from"], m["within"]) else None
            for part in m["leave"]:
                part["rect"] = _rect(part["widget"], self)
        g = self.geometry()
        if g is not None:
            r = g["square"]
            self.square.allocate(max(1, round(r[2])), max(1, round(r[3])), -1,
                                 Gsk.Transform().translate(Graphene.Point().init(r[0], r[1])))

    def _seen(self, origin, within, across=None):
        """origin's rectangle as it's seen (within: the scrolled window it's in, the part of it showing), from side to
        side across's (the list it's a card in, its edge too: as wide as the page's square, nothing grows across), on
        whole pixels (the list's parts, cut there, meet without a seam); None if it isn't seen."""
        r = _rect(origin, self) if origin.get_mapped() else None
        if r is None:
            return None
        if across is not None and (a := _rect(across, self)) is not None:
            r = (a[0], r[1], a[2], r[3])
        if within is not None and (w := _rect(within, self)) is not None:
            x0, y0 = max(r[0], w[0]), max(r[1], w[1])
            x1, y1 = min(r[0] + r[2], w[0] + w[2]), min(r[1] + r[3], w[1] + w[3])
            if x1 - x0 < 1 or y1 - y0 < 1:
                return None
            r = (x0, y0, x1 - x0, y1 - y0)
        x0, y0 = round(r[0]), round(r[1])
        return x0, y0, round(r[0] + r[2]) - x0, round(r[1] + r[3]) - y0

    def geometry(self):
        """Where things are at the move's p: the header (the row, gone up), the square under it, and how far the
        page's moved (dx, dy: its first label's text, from the row's to its own)."""
        m = self.move
        row, end, page, p = m["row"], m["parts"]["square"]["rect"], m["page"], m["p"]
        if row is None or end is None or page is None:
            return None
        head_end = (end[0], page[1], end[2], end[1] - page[1])
        body = m.get("body")
        if body is not None:  # (the row's own square, under its line: the header's from the line, the square from it)
            start = (row[0], row[1], row[2], max(1.0, body[1] - row[1]))
            head = tuple(_lerp(start[i], head_end[i], p) for i in range(4))
            bottom = _lerp(body[1] + body[3], end[1] + end[3], p)
            x, width = _lerp(body[0], end[0], p), _lerp(body[2], end[2], p)
            square = (x, head[1] + head[3], width, max(0.0, bottom - head[1] - head[3]))
            cut = body[1] + body[3]
        else:
            start = row
            head = tuple(_lerp(row[i], head_end[i], p) for i in range(4))
            bottom = _lerp(row[1] + row[3], end[1] + end[3], p)
            square = (head[0], head[1] + head[3], head[2], max(0.0, bottom - head[1] - head[3]))
            cut = row[1] + row[3]
        dx = dy = 0.0
        lead = next((q for q in m["pairs"] if q["a"] and q["b"] and "text" in q["a"]), None)
        if lead is not None:
            dx, dy = (lead["a"]["x"] - lead["b"]["x"]) * (1 - p), (lead["a"]["y"] - lead["b"]["y"]) * (1 - p)
        return {"head": head, "square": square, "end": end, "dx": dx, "dy": dy, "lead": lead, "start": start, "cut": cut}

    @staticmethod
    def _parts(square):
        parts = {"square": square}
        if isinstance(square, Gtk.ScrolledWindow) and square.get_child() is not None:
            parts["inside"] = square.get_child()  # (what's in it, scrolled as it is: without its edge)
        return parts

    def open(self, name, origin, square, pairs=(), enter=(), leave=(), within=None, across=None, body=None, rides=()):
        """Show page name, opened out of origin, a row of the page shown now (within: the scrolled window it's in):
        the row goes up to be the page's header and square (the page's darker inside) opens under it. pairs: (the
        row's widget, the page's[, True]), each turning into the other (with True, one drawing of it all the way,
        flipped across its middle: an arrow into its mirror); enter: the page's, coming in from the left; leave: the
        row's, going out to the right; across: what the row's a card in, whose sides it starts at; body: the row's own square, under its line (a thread opened there), which the page's
        square opens out of (none: from the row's bottom); rides: (the row's widget, the page's), each in the square,
        going from the one's place to the other's with it (the box it's asked in, under a thread). Just shown when there'd be nothing to see move: the row not
        on screen, the list
        not drawn since it was shown, or EXPAND_MS 0."""
        self.finish()
        shown = self.stack.get_visible_child()
        row = self._seen(origin, within, across) if EXPAND_MS and self.get_mapped() and shown else None
        if row is not None:
            self._still(shown, origin)  # (before its pictures: the row as it is, not under the pointer that clicked it)
        lst = _frozen(shown) if row is not None else None
        if row is None or lst.get_intrinsic_width() <= 0:  # (just shown, not drawn since: where it is isn't known)
            self._still(None)
            self.stack.set_visible_child_name(name)
            return
        start = [{"from": a, "to": b, "flip": bool(flip and flip[0]), "a": _place(a, self), "b": None,
                  "paint_from": _frozen(a)} for a, b, *flip in pairs]
        gone = [{"widget": w, "rect": _rect(w, self), "paint": _frozen(w)}
                for w in leave]
        lst_rect = _rect(shown, self)
        body = self._seen(body, within) if body is not None else None  # (where it is, before the list's gone)
        ridden = [{"from": a, "to": b, "a": _rect(a, self), "b": None, "paint_from": _frozen(a, RIDE_GROW)}
                  for a, b in rides if self._whole(a, within)]
        self.stack.set_visible_child_name(name)
        page = self.stack.get_visible_child()
        for pair in start:
            pair["paint"] = Gtk.WidgetPaintable.new(pair["to"])
        self.move = {"live": True, "p": 0.0, "row": row, "list": (lst, lst_rect), "page_widget": page, "page": None,
                     "body": body, "rides": ridden,
                     "pairs": start, "leave": gone,
                     "enter": [{"widget": w, "paint": Gtk.WidgetPaintable.new(w), "rect": None} for w in enter],
                     "parts": {k: {"widget": w, "paint": Gtk.WidgetPaintable.new(w), "rect": None}
                               for k, w in self._parts(square).items()}}
        for part in self.move["enter"]:
            self.move["parts"][f"enter{id(part)}"] = part  # (laid out with the rest)
        self._run(square, 0.0, 1.0)

    def close(self, name, origin, square, pairs=(), enter=(), leave=(), within=None, across=None, body=None,
              rides=()):
        """Show page name (the list origin's a row of) again, the page shown now closing back into origin: open's
        parts, as open was given them, the other way."""
        self.finish()
        if not (EXPAND_MS and self.get_mapped()) or origin is None or origin.get_parent() is None:
            self.stack.set_visible_child_name(name)
            return
        frozen = _frozen
        page = self.stack.get_visible_child()
        self._still(self.stack.get_child_by_name(name), origin)  # (the list, live as it comes back: none of it lit)
        move = {"live": False, "p": 1.0, "row": None, "list": None, "page": _rect(page, self) if page else None,
                "origin": origin, "within": within, "across": across, "body_widget": body, "body": None,
                "rides": [{"from": a, "to": b, "a": None, "b": _rect(b, self), "paint": frozen(b, RIDE_GROW)}
                          for a, b in rides],
                "pairs": [{"from": a, "to": b, "flip": bool(flip and flip[0]), "a": None, "b": _place(b, self),
                           "paint": frozen(b), "paint_from": Gtk.WidgetPaintable.new(a)} for a, b, *flip in pairs],
                "leave": [{"widget": w, "rect": None, "paint": Gtk.WidgetPaintable.new(w)} for w in leave],
                "enter": [{"widget": w, "paint": frozen(w), "rect": _rect(w, self)} for w in enter],
                "parts": {k: {"widget": w, "paint": frozen(w), "rect": _rect(w, self)}
                          for k, w in self._parts(square).items()}}
        self.stack.set_visible_child_name(name)
        self.move = move
        self._run(square, 1.0, 0.0)

    def _run(self, square, a, b):
        for cls in self.square.get_css_classes():
            self.square.remove_css_class(cls)
        for cls in square.get_css_classes():
            self.square.add_css_class(cls)
        self.square.set_child_visible(True)
        self.stack.set_can_target(False)  # (nothing under the picture taken for what's seen while it moves)
        anim = Adw.TimedAnimation.new(self, a, b, EXPAND_MS, Adw.CallbackAnimationTarget.new(self._step))
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        anim.connect("done", lambda an: an is self.anim and self._done())
        self.anim = anim
        self.queue_allocate()
        anim.play()

    def _step(self, p):
        if self.move is not None:
            self.move["p"] = p
            self.queue_allocate()
            self.queue_draw()

    def finish(self):
        """A move going on, at its end at once."""
        if self.anim is not None:
            self.anim.skip()  # (to its end: done)

    def _still(self, page, row=None):
        """Nothing in page (the list, row the one moving) under the pointer from now till the move's done: what
        the pointer was over not lit up any more, at once (not fading out, a picture of it taken as it fades: a lit row
        stretched to the header, its button's square with it), and nothing lit up as it passes under the pointer
        (while it moves nothing in it is anything the pointer's over: _run). None: page as it was again."""
        if self.stilled is not None:
            self.stilled.remove_css_class("expand-still")
        self.stilled = page
        if page is None:
            return
        page.add_css_class("expand-still")  # (no transitions in it: CSS)
        for w in walk(page):  # (what's lit drawn again: a picture of it taken now is of it as it's now styled, not as
            # it last drew, lit up; only that and the row, all of it (what's in it may be styled by its being lit), not
            # a whole list of open threads)
            if w.get_state_flags() & Gtk.StateFlags.PRELIGHT:
                w.unset_state_flags(Gtk.StateFlags.PRELIGHT)
                w.queue_draw()
        for w in walk(row) if row is not None else ():
            w.queue_draw()

    def _done(self):
        self._still(None)
        self.anim, self.move = None, None
        self.square.set_child_visible(False)
        self.stack.set_can_target(True)
        self.queue_allocate()
        self.queue_draw()

    def do_snapshot(self, snap):
        m = self.move
        g = self.geometry() if m is not None else None
        if g is None:  # (closing into a row not seen, or not laid out yet: just the page shown)
            self.snapshot_child(self.stack, snap)
            return
        p, row, head, sq, end = min(1.0, max(0.0, m["p"])), m["row"], g["head"], g["square"], g["end"]
        dx, dy = g["dx"], g["dy"]
        if m["live"]:  # (the page drawn, unseen: a widget's paintable is what it last drew, nothing till it has)
            self.snapshot_child(self.stack, Gtk.Snapshot())
        if m["list"] is not None:  # (opening: the list as it was)
            draw_list = lambda: self._paint(snap, *m["list"])
        else:
            inner = Gtk.Snapshot()
            self.snapshot_child(self.stack, inner)
            node = inner.to_node()
            draw_list = lambda: node is not None and snap.append_node(node)

        def piece(clip, x=0.0, y=0.0, sx=1.0, sy=1.0):
            """The list's clip (where it is in the list), drawn moved by x, y (and scaled by sx, sy from its corner)."""
            snap.save()
            snap.translate(Graphene.Point().init(x + clip[0], y + clip[1]))
            snap.scale(sx, sy)
            snap.translate(Graphene.Point().init(-clip[0], -clip[1]))
            snap.push_clip(_graphene(clip))
            draw_list()
            snap.pop()
            snap.restore()

        big = 1e5
        # the list less the row: what's above it (its edges beside the row too) pushed up off the page with the
        # header, what's below pushed down off it by the square
        up = head[1] - row[1]  # (on the header's top: off the page as that gets to the page's)
        piece((-big, -big, 2 * big, big + row[1]), y=up)
        piece((-big, row[1], big + row[0], row[3]), y=up - row[3] * p)  # (the row's own edges beside it)
        piece((row[0] + row[2], row[1], big, row[3]), y=up - row[3] * p)
        cut = g["cut"]  # (the row's bottom; with a body, the body's: the row's edge under it goes down with the rest)
        down = sq[1] + sq[3] - cut + (self.get_height() - end[1] - end[3]) * p
        piece((-big, cut, 2 * big, big), y=max(0.0, down))
        # the header: the row's background (the card's edges with it), as wide as the square, fading to the page's.
        # Its top and bottom edges go with the header's; between them a line of it from above its text, repeated
        snap.push_clip(_graphene(head))
        lead = g["lead"]
        line = g["start"]  # (the row's line: all of it, or over its body)
        if line[3] > 6 and p < 1:
            fade = p > 0  # (none at its start: not even an opacity of 1, which rounds)
            if fade:
                snap.push_opacity(1 - p)
            sx = head[2] / line[2] if line[2] else 1.0
            for y0, h, at, size in ((line[1], 2, head[1], 2), (line[1] + 3, 1, head[1] + 2, head[3] - 4),
                                    (line[1] + line[3] - 2, 2, head[1] + head[3] - 2, 2)):
                if size <= 0:
                    continue
                snap.push_repeat(_graphene((head[0], at, head[2], size)), _graphene((head[0], at, head[2], h)))
                piece((line[0], y0, line[2], h), x=head[0] - line[0], y=at - y0, sx=sx)
                snap.pop()
            if fade:
                snap.pop()
        # what only the row has, going out to the right; what only the page has, coming in from the left
        for part in m["leave"]:  # (its own drawing: the row's background, see-through, isn't drawn on twice)
            if (r := part["rect"]) is not None:
                self._paint(snap, part["paint"], (r[0] + (head[0] + head[2] - r[0]) * p, r[1] + head[1] - row[1],
                                                  r[2], r[3]))
        for part in m["enter"]:
            if (r := part["rect"]) is not None:
                self._paint(snap, part["paint"], (r[0] - (r[0] - end[0] + r[2]) * (1 - p), r[1] + dy, r[2], r[3]))
        # the row's parts turning into the header's
        for pair in m["pairs"]:
            a, b = pair["a"], pair["b"]
            if a is None or b is None:
                if b is not None:
                    self._paint(snap, pair["paint"], (b["x"] + dx, b["y"] + dy, b["w"], b["h"]))
            elif "text" in a and "text" in b:
                self._morph(snap, a, b, p)
            elif pair["flip"]:  # (one drawing all the way, flipped across its middle as it goes: a › into a ‹, its
                # point going across. Opening, the row's as it was; closing, the page's: each drawn, neither just shown)
                img, size, sx = ((pair["paint_from"], a, 1 - 2 * p) if m["live"] else (pair["paint"], b, 2 * p - 1))
                cx = _lerp(a["x"] + a["w"] / 2, b["x"] + b["w"] / 2, p)
                cy = _lerp(a["y"] + a["h"] / 2, b["y"] + b["h"] / 2, p)
                if abs(sx) > 1e-3:
                    snap.save()
                    snap.translate(Graphene.Point().init(cx, cy))
                    snap.scale(sx, 1)
                    self._paint(snap, img, (-size["w"] / 2, -size["h"] / 2, size["w"], size["h"]))
                    snap.restore()
            else:  # (the row's drawing of it the first half of the way, the header's the second: the same, but for
                # a spinner's turn; and one just shown hasn't drawn yet)
                self._paint(snap, pair["paint"] if p >= 0.5 else pair["paint_from"],
                            (_lerp(a["x"] + (a["w"] - b["w"]) / 2, b["x"], p),
                             _lerp(a["y"] + (a["h"] - b["h"]) / 2, b["y"], p), b["w"], b["h"]))
        snap.pop()
        # beside a body: the row's card (its edge with it), as wide as there's still room, as tall as the square
        body = m.get("body")
        if body is not None and p < 1 and sq[3] > 0:
            mid = round(body[1] + body[3] / 2)  # (a line of the card beside the body, repeated down the square)
            for x0, w0, x1, w1 in ((row[0], body[0] - row[0], head[0], sq[0] - head[0]),
                                   (body[0] + body[2], row[0] + row[2] - body[0] - body[2], sq[0] + sq[2],
                                    head[0] + head[2] - sq[0] - sq[2])):
                if w0 <= 0 or w1 <= 0:
                    continue
                snap.push_repeat(_graphene((x1, sq[1], w1, sq[3])), _graphene((x1, sq[1], w1, 1)))
                piece((x0, mid, w0, 1), x=x1 - x0, y=sq[1] - mid, sx=w1 / w0)
                snap.pop()
        # the square, opening under the header, and what's in it, coming with the header, as far as it's got
        if sq[3] >= 0.5:  # (not yet, at the row: it's allocated a pixel at least, but there's none of it to draw)
            self.snapshot_child(self.square, snap)
        inside = m["parts"].get("inside") or m["parts"]["square"]
        rides = [(ride, self._ride_at(ride, p)) for ride in m["rides"]]
        rides = [(ride, at) for ride, at in rides if at is not None]
        top = min([at[1] - RIDE_GROW for _r, at in rides], default=sq[1] + sq[3] - 1)  # (what's in it, over what rides)
        if (r := inside["rect"]) is not None and sq[3] > 2:  # (across, on the square's own edge: never cut off by it)
            snap.push_clip(_graphene((sq[0] + 1, sq[1] + 1, max(0.0, sq[2] - 2), max(0.0, min(sq[3] - 2,
                                                                                                top - sq[1] - 1)))))
            # (opening, its first frame drawn now: there's nothing it last drew, the page not drawn till now; after
            # that, what it last drew, not drawn twice a frame)
            paint = _frozen(inside["widget"]) if m["live"] and not m.get("drawn") else inside["paint"]
            m["drawn"] = True
            # (from a body, with the square's top, from the body's; else with the header, as its title moves)
            down = (m["body"][1] - end[1]) * (1 - p) if m.get("body") is not None else dy
            self._paint(snap, paint, (r[0] + sq[0] - end[0], r[1] + down, r[2], r[3]))
            snap.pop()
        if sq[3] > 2 and rides:  # (what goes with the square from the row's place to the page's: the row's, then the
            # page's, on the square, not out of it)
            snap.push_clip(_graphene((sq[0] + 1, sq[1] + 1, max(0.0, sq[2] - 2), sq[3] - 2)))
            for ride, at in rides:
                first = p < 0.5
                img = (ride["paint_from"] if m["live"] else _frozen(ride["from"], RIDE_GROW)) if first else \
                    (_frozen(ride["to"], RIDE_GROW) if m["live"] else ride["paint"])
                grow = RIDE_GROW
                self._paint(snap, img, (at[0] - grow, at[1] - grow, at[2] + 2 * grow, at[3] + 2 * grow))
            snap.pop()

    def _whole(self, widget, within):
        """Whether all of widget's seen (within: the scrolled window it's in): a ride goes from its place only then."""
        r = _rect(widget, self) if widget.get_mapped() else None
        seen = self._seen(widget, within) if r is not None else None
        return seen is not None and seen[3] >= round(r[3]) - 1

    @staticmethod
    def _ride_at(ride, p):
        """Where a ride is at p (its bottom going with the square's), at the row's size the first half of the way, the
        page's the second; None if it isn't in both."""
        a, b = ride["a"], ride["b"]
        if a is None or b is None:
            return None
        size = a if p < 0.5 else b
        return (_lerp(a[0], b[0], p), _lerp(a[1] + a[3], b[1] + b[3], p) - size[3], size[2], size[3])

    def _morph(self, snap, a, b, p):
        """One label's text, a's (the row's) turning into b's (the header's) at p: its place, size, weight, colour
        and width between theirs, wrapped as the nearer one's is."""
        font = b["font"].copy()
        fa, fb = a["font"], b["font"]
        size = _lerp(fa.get_size(), fb.get_size(), p)
        if fb.get_size_is_absolute():
            font.set_absolute_size(size)
        else:
            font.set_size(round(size))
        font.set_variations(f"wght={_lerp(int(fa.get_weight()), int(fb.get_weight()), p):.1f}")
        near = a if p < 0.5 else b  # (laid out as the nearer one is: a row's wrapped title isn't one line till the end)
        layout = near["layout"].copy()  # (as the label lays it out: its attributes, its spacing)
        layout.set_font_description(font)
        layout.set_width(round(_lerp(a["w"], b["w"], p) * Pango.SCALE))
        if near["wrap"]:
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        else:
            layout.set_ellipsize(Pango.EllipsizeMode.END)
        ca, cb = a["color"], b["color"]
        color = Gdk.RGBA()
        color.red, color.green = _lerp(ca.red, cb.red, p), _lerp(ca.green, cb.green, p)
        color.blue, color.alpha = _lerp(ca.blue, cb.blue, p), _lerp(ca.alpha, cb.alpha, p)
        snap.save()  # (its baseline between theirs: their lines' spacing needn't be the same)
        y = _lerp(a["y"] + a["baseline"], b["y"] + b["baseline"], p) - layout.get_baseline() / Pango.SCALE
        snap.translate(Graphene.Point().init(_lerp(a["x"], b["x"], p), y))
        oa, ob = a["alpha"], b["alpha"]  # (as each is drawn: a dim one under its opacity; one not known, the other's)
        oa, ob = (oa if oa is not None else ob or 1.0), (ob if ob is not None else oa or 1.0)
        snap.push_opacity(_lerp(oa, ob, p))
        snap.append_layout(layout, color)
        snap.pop()
        snap.restore()

    @staticmethod
    def _paint(snap, paintable, rect):
        if paintable is None or rect is None:
            return
        snap.save()
        snap.translate(Graphene.Point().init(rect[0], rect[1]))
        paintable.snapshot(snap, rect[2], rect[3])
        snap.restore()

    def do_dispose(self):
        if self.anim is not None:
            self.anim.pause()
            self.anim = None
        self.move = None
        for child in (self.stack, self.square):
            if child is not None and child.get_parent() is self:
                child.unparent()
        Gtk.Widget.do_dispose(self)


def natural_height(scroller):
    """scroller's child at its natural height, kept so for each child it's given later (each gets a viewport of its
    own). At its minimum, GTK's default once it's taller than the view, every code block in it is squashed to what its
    own scroller needs at least, a line or less, its text cut off at the bottom: as soon as the text's size (Settings
    → Size, the zoom) makes what's there outgrow the pane. Returns scroller."""
    def natural(*_):
        if isinstance(child := scroller.get_child(), Gtk.Scrollable):
            child.set_vscroll_policy(Gtk.ScrollablePolicy.NATURAL)
    natural()
    if not getattr(scroller, "natural_height", False):
        scroller.natural_height = True
        scroller.connect("notify::child", natural)
    return scroller


def markdown_scroller(child, **kw):
    """The scrolled window markdown (MarkdownView) is read in: Baude's chat, Record's Read and its Record's answers.
    Its child at its natural height (natural_height), nothing scrolled sideways, and a long code block a pane of at
    most 2/3 of what's visible (CodeBlock.set_max_height)."""
    scroller = natural_height(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=child, **kw))
    scroller.get_vadjustment().connect("notify::page-size", lambda adj, _p: adj.get_page_size() > 0 and
                                       CodeBlock.set_max_height(adj.get_page_size() * 2 / 3))
    return scroller


def scroll_place(scroller):
    """Where a scrolled window has been read to, for hold_place to put back: None if it's never been laid out (not
    shown yet: nothing to put back)."""
    adj = scroller.get_vadjustment()
    if adj.get_page_size() <= 0:
        return None
    return {"value": adj.get_value(), "at_end": adj.get_value() + adj.get_page_size() >= adj.get_upper() - 40}


def hold_place(scroller, place, seconds=2.0):
    """A scrolled window put back where it was (scroll_place), and held there as what's in it lays out, a few frames or
    in a long chat more: for seconds from when it's first shown, or till it's scrolled by hand, or till it's held somewhere
    else. scroller.held says so, for what would take it to the end as it fills (a chat's follow)."""
    if not place:
        return
    if (earlier := getattr(scroller, "let_go", None)) is not None:
        earlier()
    adj = scroller.get_vadjustment()
    hand = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL,
                                     propagation_phase=Gtk.PropagationPhase.CAPTURE)
    scroller.held = True

    def put(*_):
        adj.set_value(adj.get_upper() if place["at_end"] else place["value"])

    def let_go(*_):
        if scroller.let_go is let_go:  # (once: its timer may come after it's been let go)
            scroller.held, scroller.let_go = False, None
            adj.disconnect(changed)
            scroller.remove_controller(hand)
            if mapped[0] is not None:
                scroller.disconnect(mapped[0])
                mapped[0] = None
        return False

    def shown(*_):
        if mapped[0] is not None:
            scroller.disconnect(mapped[0])
            mapped[0] = None
        put()
        GLib.timeout_add(int(seconds * 1000), let_go)
    scroller.let_go = let_go
    changed = adj.connect("changed", put)
    hand.connect("scroll", lambda *_: let_go())
    scroller.add_controller(hand)
    mapped = [None]
    if scroller.get_mapped():
        shown()
    else:
        mapped[0] = scroller.connect("map", shown)


class ScrollGlide:
    """A scrolled window's own scrolling: a wheel's or touchpad's scrolls, and the glide after a fling, each a step from
    where the view is now. GTK's own glide puts the view where its reckoning of the fling says each frame, and takes no
    notice of it being moved meanwhile: a chunk of a chat's history drawn above mid-glide, the view moved by its height
    to keep what's read where it was, was undone the next frame, the view thrown thousands of pixels, and the glide went
    on from there. Its scrolls are taken after a code block's own scroller's (bubbling up to the viewport), and Ctrl's
    (zoom) and Shift's (sideways) are left to others. more(): whether there's more to draw above, a glide that reaches
    the top waiting a moment for it. A wheel's click glides there over WHEEL_S rather than jumping a fifth of a screen in
    one frame (clicks in a row add up), and a touchpad's pixels are moved by once a frame, together: they come at their
    own rate, two in one frame and none in the next. rested(): a glide or a wheel's click has come to rest by itself.
    moved(): it's moved the view (the reader's scroll, as against the view being put somewhere). sideways: it scrolls
    left and right instead (a tab strip): scrolls either way move it, Shift's too, a touchpad's swipe the way it
    began."""

    FRICTION = 4.0  # (GTK's: the speed falls by e each quarter second)
    STOP = 10.0  # px/s
    WAIT = 0.5  # s at the top, for what's drawn above it
    WHEEL_S = 0.12  # s a wheel's click takes to get there (most of the way: it eases out)

    def __init__(self, scroller, more=None, rested=None, moved=None, sideways=False):
        self.scroller, self.more, self.rested = scroller, more or (lambda: False), rested or (lambda: None)
        self.moved, self.sideways, self.axis = moved or (lambda: None), sideways, None  # (axis: a swipe's way, x or y)
        self.v, self.tick, self.last, self.waited = 0.0, None, None, 0.0
        self.wheel = self.pan = 0.0  # (px a wheel's clicks have still to go; a touchpad's, since the last frame)
        scroller.set_kinetic_scrolling(False)  # (its glide off: this one's)
        flags = Gtk.EventControllerScrollFlags
        ctl = Gtk.EventControllerScroll(flags=(flags.BOTH_AXES if sideways else flags.VERTICAL) | flags.KINETIC)
        ctl.connect("scroll-begin", self._begin)
        ctl.connect("scroll", self._scroll)
        ctl.connect("decelerate", self._decelerate)
        scroller.get_child().add_controller(ctl)  # (the viewport: after what's in it, before the scroller's own)
        press = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", lambda *_: self.stop())  # (a click or the scrollbar taken: the glide's over)
        scroller.add_controller(press)

    def _adj(self):
        return self.scroller.get_hadjustment() if self.sideways else self.scroller.get_vadjustment()

    def _detent(self, ctl):
        """What a wheel's click scrolls, as GTK has it; 1 for a touchpad's pixels."""
        if ctl.get_unit() != Gdk.ScrollUnit.WHEEL:
            return 1.0
        return max(1.0, self._adj().get_page_size()) ** (2 / 3)

    def _begin(self, _ctl):
        self.axis = None  # (a new swipe: its way is its first move's)
        self.stop()

    def _scroll(self, ctl, dx, dy):
        state, wheel = ctl.get_current_event_state(), ctl.get_unit() == Gdk.ScrollUnit.WHEEL
        if self.sideways:  # (a wheel's click goes its own way; a touchpad's swipe, the way it began)
            if state & Gdk.ModifierType.CONTROL_MASK:
                return False
            self.axis = way = (not wheel and self.axis) or ("x" if abs(dx) >= abs(dy) else "y")
            dy = dx if way == "x" else dy
        elif dy == 0 or state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
            return False
        if wheel:
            self.wheel_by(dy * self._detent(ctl))
        else:
            self.pan_by(dy)
        return True

    def _decelerate(self, ctl, vx, vy):
        self.glide((vx if self.sideways and self.axis == "x" else vy) * self._detent(ctl))

    def step(self, dy):
        adj = self._adj()
        before = adj.get_value()
        adj.set_value(before + dy)
        if adj.get_value() != before:
            self.moved()

    def wheel_by(self, dy):
        """A wheel's click: glide dy px over WHEEL_S, on from where the clicks before it have still to go."""
        self.v, self.pan = 0.0, 0.0  # (a glide's over)
        self.wheel += dy
        self._run()

    def pan_by(self, dy):
        """A touchpad's dy px, moved by in the next frame with any others that come before it."""
        self.v, self.wheel = 0.0, 0.0
        self.pan += dy
        self._run()

    def glide(self, v):
        """Glide on at v px/s (> 0 down, or right), slowing as GTK's does (a touchpad's pixels not moved by yet, moved
        by first)."""
        pan = self.pan
        self.stop()
        self.pan = pan
        if abs(v) >= self.STOP:
            self.v = v
        if self.v or self.pan:
            self._run()

    def _run(self):
        if self.tick is None:
            self.last, self.waited = None, 0.0  # (its first frame's time: _first_dt's)
            self.tick = self.scroller.add_tick_callback(self._frame)

    def _first_dt(self, clock, now):
        """The time a first frame moves it by: since the frame before (a frame's, after a while with none), less what a
        touchpad's last pixels moved by in it took at the glide's speed. (The clock's time between frames is the next
        frame's once that's nearer: a glide begun then stood still for a frame as the fingers left the touchpad.)"""
        before = clock.get_timings(clock.get_frame_counter() - 1)
        dt = now - before.get_frame_time() / 1e6 if before is not None else 0.0
        if not 0.0 < dt < 0.1:
            dt = clock.get_refresh_info(0)[0] / 1e6
        return max(0.0, dt - abs(self.pan / self.v)) if self.pan and self.v else dt

    def gliding(self):
        return self.tick is not None

    def stop(self):
        self.v = self.wheel = self.pan = 0.0
        if self.tick is not None:
            self.scroller.remove_tick_callback(self.tick)
            self.tick = None

    def _frame(self, _widget, clock):
        now = clock.get_frame_time() / 1e6
        dt = min(max(now - self.last, 0.0), 0.1) if self.last is not None else self._first_dt(clock, now)
        self.last = now
        moving = bool(self.v or self.wheel)  # (what comes to rest by itself, as against a touchpad's pixels)
        dy, self.pan = self.pan, 0.0
        if self.wheel:
            part = self.wheel * (1 - math.exp(-3 * dt / self.WHEEL_S))  # (95% of the way in WHEEL_S)
            if abs(self.wheel - part) < 0.5:
                part = self.wheel
            self.wheel -= part
            dy += part
        glided = 0.0
        if self.v:
            decay = math.exp(-self.FRICTION * dt)
            glided, self.v = self.v * (1 - decay) / self.FRICTION, self.v * decay
            dy += glided
        adj = self._adj()
        before = adj.get_value()
        if dy:
            adj.set_value(before + dy)
            if adj.get_value() != before:
                self.moved()
        if adj.get_value() == before and dy:  # (at an end)
            self.wheel = 0.0
            self.waited += dt
            if not (glided < 0 and self.more()) or self.waited > self.WAIT:
                self.v = 0.0
        if abs(self.v) < self.STOP:
            self.v = 0.0
        if self.v or self.wheel or self.pan:
            return GLib.SOURCE_CONTINUE
        self.tick = None
        if moving:
            self.rested()
        return GLib.SOURCE_REMOVE
