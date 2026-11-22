"""lightbox — a chat's pictures opened large over the window (Lightbox), zoomed and moved (ZoomPicture).
"""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402,F401

import pointers  # noqa: E402
from baudelib import wording  # noqa: E402
from widgets import IMAGE_POOL, screen_scale, view_bar, walk  # noqa: E402
from imageviews import FileImageActions  # noqa: E402


def image_description(data, tex):
    kb = (data if isinstance(data, int) else len(data)) / 1024
    size = wording.say("images.size_mb", mb=f"{kb / 1024:.1f}") if kb > 1024 else \
        wording.say("images.size_kb", kb=f"{kb:.0f}")
    return f"{tex.get_width()}×{tex.get_height()} · {size}"


class ZoomPicture(Gtk.Picture):
    """The large view's picture: the image fitted into it (zoom 1, in the middle), or zoom times that and moved by
    (pan_x, pan_y) from the middle, past the picture's own edges as far as its parent lets it. On its way in or out
    (flight: Lightbox.fly) it's painted between there and where it came from: the decoded image moved and scaled, each
    frame, nothing decoded or rescaled for it."""

    FROM_MIDDLE = 0.08  # (with nowhere to come from: this much smaller, as it starts to show)
    PIXELS_FROM = 3  # (zoomed in this far, screen pixels to one of the image's: its pixels shown sharp, not blurred)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.zoom, self.pan_x, self.pan_y = 1.0, 0.0, 0.0
        self.flight = None  # (how far in, 0 to 1, and where from: Lightbox.where_from)
        self.set_overflow(Gtk.Overflow.VISIBLE)  # (a Gtk.Picture clips to itself: its parent does here)

    def fitted(self):
        """The image's size fitted (it's letterboxed; a small one isn't blown up): (width, height)."""
        tex, W, H = self.get_paintable(), self.get_width(), self.get_height()
        iw, ih = tex.get_intrinsic_width(), tex.get_intrinsic_height()
        k = min(W / iw, H / ih)
        if self.get_content_fit() == Gtk.ContentFit.SCALE_DOWN:
            k = min(k, 1.0)
        return iw * k, ih * k

    def rect(self):
        """Where the image is painted: (x, y, width, height), in the picture's own coordinates."""
        w, h = self.fitted()
        w, h = w * self.zoom, h * self.zoom
        return (self.get_width() - w) / 2 + self.pan_x, (self.get_height() - h) / 2 + self.pan_y, w, h

    def flown(self):
        """Where it is at this point of its flight, in the picture's own coordinates: the image's (x, y, width, height),
        the part of that that shows (it's cut off where it was, and by what's around the picture where it's going), and
        its opacity. Worked out as it's painted: the picture's place isn't known until it's laid out."""
        rect = x, y, w, h = self.rect()
        start, top = self.get_margin_start(), self.get_margin_top()
        room = (-start, -top, self.get_width() + start + self.get_margin_end(),
                self.get_height() + top + self.get_margin_bottom())
        t, source = self.flight
        if source is None:  # (out of the middle, fading in)
            k = 1 - self.FROM_MIDDLE * (1 - t)
            return (x + w * (1 - k) / 2, y + h * (1 - k) / 2, w * k, h * k), room, t
        _ok, at = self.compute_point(self.get_root(), Graphene.Point().init(0, 0))

        def between(there, here):  # (there: in the window)
            there = there[0] - at.x, there[1] - at.y, there[2], there[3]
            return tuple(a + (b - a) * t for a, b in zip(there, here))
        return between(source[0], rect), between(source[1], room), 1.0

    def do_snapshot(self, snap):
        tex = self.get_paintable()
        if tex is None or not self.get_width() or not self.get_height():
            return
        (x, y, w, h), room, opacity = self.flown() if self.flight is not None else (self.rect(), None, 1.0)
        if room is not None:
            snap.push_clip(Graphene.Rect().init(*room))
            snap.push_opacity(opacity)
        if isinstance(tex, Gdk.Texture):  # (filtered for how far it's scaled, as a viewer does)
            k = w / tex.get_width() * screen_scale(self)
            how = (Gsk.ScalingFilter.TRILINEAR if k < 1 else Gsk.ScalingFilter.NEAREST if k >= self.PIXELS_FROM
                   else Gsk.ScalingFilter.LINEAR)
            snap.append_scaled_texture(tex, how, Graphene.Rect().init(x, y, w, h))
        else:
            snap.save()
            snap.translate(Graphene.Point().init(x, y))
            tex.snapshot(snap, w, h)
            snap.restore()
        if room is not None:
            snap.pop()
            snap.pop()


class Lightbox(Gtk.Box):
    """Full-window image view over a dimmed window, Baude's and Record's alike (win: image_source, what its images are
    in; input, what has the focus when it closes). Click anywhere outside the image (or press
    Esc) to close; ←/→ (or the side arrows) step through every image in the conversation. Zoomed in (scrolled, pinched, a
    double click, + and −; 0 fits it again), smoothly, around where you're pointing: dragged, scrolled on a trackpad or
    moved with the arrow keys to go around it, the arrows no longer stepping between images. It opens out of the image
    clicked and closes back into it (fly), while what's behind it dims and comes back."""

    FLY_MS = 240  # (on its way in or out. The tests set it to 0: test_lightbox_flies has it fly)
    SHADE = 0.94  # (how far what's behind it is dimmed)
    ZOOM_MS = 160
    ZOOM_KEY = 1.5  # (+ and −: this much at a time)
    ZOOM_WHEEL = 1.25  # (a mouse wheel's notch)
    MOST_PIXELS = 8  # (zoomed in as far as this many screen pixels to one of the image's)
    STRIP = 56  # (a folder's pictures along the bottom: this tall)
    STRIP_ITEM = STRIP + 10  # (and this wide each, with its padding and margins: widgets.CSS's .lightbox-strip)
    PREVIEW_PX = 512  # (a folder's picture, till it's read: its thumbnail this big)

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False, focusable=True)
        self.add_css_class("lightbox")
        self.win, self.items, self.index = win, [], 0
        self.shown, self.flying, self.closing, self.source, self.hidden = 0.0, None, False, None, None
        bar = view_bar(spacing=8, margin_start=16, margin_end=12, margin_top=10, margin_bottom=6)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True)
        self.title.add_css_class("heading")
        self.info = Gtk.Label()
        self.info.add_css_class("dim-label")
        bar.append(self.title)
        bar.append(self.info)
        self.buttons = {}
        for key, label, tip in (("copy", wording.say("common.copy"), wording.say("images.copy_image")),
                                ("folder", wording.say("images.show_in_folder"), wording.say("images.open_its_folder")),
                                ("open", wording.say("common.open"),
                                 wording.say("images.open_with_the_default_app")),
                                ("browser", wording.say("images.open_in_browser"),
                                 wording.say("images.open_original_in_browser"))):
            btn = Gtk.Button(label=label, tooltip_text=tip)
            btn.connect("clicked", lambda *_, k=key: self.act(k))
            self.buttons[key] = btn
            bar.append(btn)
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=wording.say("images.close"))
        close.add_css_class("circular")
        close.connect("clicked", lambda *_: self.close())
        bar.append(close)
        handle = Gtk.WindowHandle(child=bar)  # (dragged, it moves the window; double-clicked, maximizes)
        self.append(handle)
        stage = self.stage = Gtk.Overlay(vexpand=True, hexpand=True)
        stage.set_overflow(Gtk.Overflow.HIDDEN)  # (zoomed in, it stops at the bar above it and the window's edges)
        self.picture = ZoomPicture(can_shrink=True, margin_start=56, margin_end=56, margin_bottom=40, margin_top=8)
        stage.set_child(self.picture)
        self.target, self.anim, self.pointer = (1.0, 0.0, 0.0), None, None  # (where it's going: zoom, pan_x, pan_y)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self.on_scroll)
        stage.add_controller(scroll)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: setattr(self, "pointer", (x, y)))
        stage.add_controller(motion)
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag)
        stage.add_controller(drag)
        pinch = Gtk.GestureZoom()
        pinch.connect("begin", lambda *_: setattr(self, "pinched_from", self.target[0]))
        pinch.connect("scale-changed", self.on_pinch)
        stage.add_controller(pinch)
        self.prev_btn = self._arrow("go-previous-symbolic", Gtk.Align.START, -1)
        self.next_btn = self._arrow("go-next-symbolic", Gtk.Align.END, 1)
        stage.add_overlay(self.prev_btn)
        stage.add_overlay(self.next_btn)
        self.append(stage)
        # a folder's pictures (open_files): all of them along the bottom, the one shown picked, a click going to one
        self.strip_paths = Gtk.StringList()
        self.strip_pick = Gtk.SingleSelection(model=self.strip_paths, autoselect=False)
        self.strip_pick.connect("notify::selected", self.on_strip_pick)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._strip_setup)
        factory.connect("bind", self._strip_bind)
        self.strip_view = Gtk.ListView(model=self.strip_pick, factory=factory, orientation=Gtk.Orientation.HORIZONTAL,
                                       single_click_activate=False)
        self.strip_view.add_css_class("lightbox-strip")
        self.strip = Gtk.ScrolledWindow(child=self.strip_view, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                        hscrollbar_policy=Gtk.PolicyType.EXTERNAL, visible=False,
                                        height_request=self.STRIP + 16, margin_bottom=8, halign=Gtk.Align.CENTER)
        self.append(self.strip)
        self.thumb = None  # (open_files': a thumbnailer, thumbs.thumbnail)
        self.chrome = (handle, self.prev_btn, self.next_btn, self.strip)  # (what fades as it flies)
        twice = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        twice.connect("pressed", self.on_press)
        self.picture.add_controller(twice)
        click = Gtk.GestureClick()
        click.connect("released", self.on_click)
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

    def _arrow(self, icon, align, step):
        btn = Gtk.Button(icon_name=icon, halign=align, valign=Gtk.Align.CENTER, margin_start=10, margin_end=10)
        btn.add_css_class("circular")
        btn.add_css_class("osd")
        btn.add_css_class("lightbox-arrow")
        btn.connect("clicked", lambda *_: self.step(step))
        return btn

    def open(self, widget):
        """Show `widget`'s image; the others where it is (win.image_source: a chat, the Feedback tab) become the ←/→
        sequence."""
        self.items = [w for w in walk(self.win.image_source())
                      if getattr(w, "image_actions", None) is not None and not isinstance(w, Gtk.Overlay)]
        if widget not in self.items:
            self.items.append(widget)
        self.index = self.items.index(widget)
        self.strip.set_visible(False)
        self.strip_paths.splice(0, self.strip_paths.get_n_items(), [])
        self.show_current()
        self.set_visible(True)
        self.grab_focus()
        self.fly(True)

    def open_files(self, paths, index, shown_in, thumb):
        """A folder's pictures (paths), from paths[index]: each read from its file as it's shown (and the ones either
        side of it, to go on to at once), with all of them along the bottom. shown_in(path): the widget it's in on the
        page, if it's in one; thumb(path, px, done): its thumbnail."""
        self.thumb, self.items = thumb, []
        for path in paths:
            holder = Gtk.Picture()  # (stands for it: the lightbox's items are widgets)
            acts = holder.image_actions = FileImageActions(self, path, lambda p=path: shown_in(p))
            self.items.append(holder)
        self.index = index
        acts = self.items[index].image_actions
        tile = shown_in(paths[index])  # (its tile's thumbnail at once: it flies out of it, and shows till it's read)
        acts.preview = tile.get_paintable() if tile is not None else None
        if acts.preview is None:
            def made(tex, acts=acts):
                if tex is not None and acts.preview is None:
                    acts.preview = tex
                    if self.items and self.current() is acts and acts.full is None:
                        self.picture.set_paintable(tex)
                        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
                        self.picture.queue_draw()
            thumb(paths[index], round(self.PREVIEW_PX * screen_scale(self)), made)
        self.strip_paths.splice(0, self.strip_paths.get_n_items(), paths)
        self.strip.set_visible(len(paths) > 1)
        wide = len(paths) * self.STRIP_ITEM  # (centred while they fit; past that, the whole width to scroll along)
        self.strip.set_halign(Gtk.Align.CENTER if wide < 900 else Gtk.Align.FILL)
        self.strip.set_size_request(wide if wide < 900 else -1, self.STRIP + 16)
        self.show_current()
        self.set_visible(True)
        self.grab_focus()
        self.fly(True)

    def _strip_setup(self, _factory, item):
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, width_request=self.STRIP, height_request=self.STRIP,
                          can_shrink=True)
        pic.add_css_class("lightbox-thumb")
        item.set_child(pic)

    def _strip_bind(self, _factory, item):
        pic, path = item.get_child(), item.get_item().get_string()
        pic.path = path
        pic.set_paintable(None)
        acts = self.items[item.get_position()].image_actions if item.get_position() < len(self.items) else None

        def made(tex, pic=pic, path=path):
            if tex is not None and pic.path == path:
                pic.set_paintable(tex)
                if acts is not None and acts.preview is None:
                    acts.preview = tex
        if self.thumb is not None:
            self.thumb(path, round(self.STRIP * 2 * screen_scale(self)), made, lambda: pic.path == path)

    def on_strip_pick(self, *_):
        pos = self.strip_pick.get_selected()
        if pos != Gtk.INVALID_LIST_POSITION and pos != self.index and pos < len(self.items):
            self.step(pos - self.index)

    def do_snapshot(self, snap):
        """What's behind it dimmed, here and not in the CSS: it fades by itself, the image over it at full strength."""
        shade = Gdk.RGBA()
        shade.parse("black")
        shade.alpha = self.SHADE * self.shown
        snap.append_color(shade, Graphene.Rect().init(0, 0, self.get_width(), self.get_height()))
        Gtk.Box.do_snapshot(self, snap)

    def source_widget(self):
        """The widget the image shown is in on the page (a folder's picture: the gallery's tile showing it now)."""
        if not self.items:
            return None
        widget = self.items[self.index]
        shown_in = getattr(widget.image_actions, "shown_in", None)
        return shown_in() if shown_in is not None else widget

    def where_from(self):
        """Where the image shown sits in the window, to fly out of and back into: the (x, y, width, height) of the image
        as it's painted there and of the part of it in sight. None if none of it is (scrolled away, or on a page not
        shown), or it's gone."""
        tex, widget = self.picture.get_paintable(), self.source_widget()
        if tex is None or widget is None or not widget.get_mapped() or widget.get_root() is not self.win:
            return None
        ok, bounds = widget.compute_bounds(self.win)
        if not ok or bounds.get_width() <= 0 or bounds.get_height() <= 0:
            return None
        x, y, w, h = bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height()
        left, top, right, bottom = x, y, x + w, y + h
        around = widget.get_parent()
        while around is not None:  # (a scroller cuts it off)
            ok, bounds = around.compute_bounds(self.win)
            if ok:
                left, right = max(left, bounds.get_x()), min(right, bounds.get_x() + bounds.get_width())
                top, bottom = max(top, bounds.get_y()), min(bottom, bounds.get_y() + bounds.get_height())
            around = around.get_parent()
        if right <= left or bottom <= top:
            return None
        iw, ih = tex.get_intrinsic_width(), tex.get_intrinsic_height()
        # (an attachment's thumbnail is cropped; a picture's letterboxed)
        k = (max if isinstance(widget, Gtk.Image) else min)(w / iw, h / ih)
        return (x + (w - iw * k) / 2, y + (h - ih * k) / 2, iw * k, ih * k), (left, top, right - left, bottom - top)

    def fly(self, opening):
        """On its way in, or out, from wherever it's got to (turned back part way, it goes back the way it came). Out of
        the image it's of and back into it, if that's in sight (hidden meanwhile: it's the one image that moves); or out
        of the middle, fading. Going, nothing's waiting for it: the focus has gone back and clicks go through to what's
        behind it, a click on an image bringing it back. With animations off (Adw's: it goes straight to its end), at
        once."""
        if self.flying is not None:
            self.flying.pause()
            self.flying = None
        self.closing = not opening
        self.set_can_target(opening)
        self.show_source()
        self.source = self.where_from()
        if self.source is not None:
            widget = self.source_widget()
            holder = widget.get_parent()  # (image_widget's overlay)
            self.hidden = holder if getattr(holder, "image_actions", None) is not None else widget
            self.hidden.set_opacity(0.0)
        self.stage.set_overflow(Gtk.Overflow.VISIBLE)  # (it starts where it was, not only inside here)
        self.fly_frame(self.shown)
        if not self.FLY_MS or not Adw.get_enable_animations(self):
            self.landed()
            return
        flying = self.flying = Adw.TimedAnimation.new(self, self.shown, 1.0 if opening else 0.0, self.FLY_MS,
                                                      Adw.CallbackAnimationTarget.new(self.fly_frame))
        flying.set_easing(Adw.Easing.EASE_IN_OUT_CUBIC)
        flying.connect("done", self.landed)

        def start(*_):  # (next frame: it counts from a frame's time, so a large image's decoding would come off it)
            if self.flying is flying:
                flying.play()
            return GLib.SOURCE_REMOVE
        self.add_tick_callback(start)

    def fly_frame(self, t):
        self.shown = t
        if self.source is not None:  # (the chat scrolled, or the window resized, on the way: to where it is now)
            self.source = self.where_from() or self.source
        self.picture.flight = (t, self.source)
        for widget in self.chrome:
            widget.set_opacity(t * t)  # (late: not read over the window's own bar while that's still showing)
        self.picture.queue_draw()
        self.queue_draw()

    def show_source(self):
        if self.hidden is not None:
            self.hidden.set_opacity(1.0)
            self.hidden = None

    def landed(self, *_):
        self.flying = None
        self.fly_frame(0.0 if self.closing else 1.0)
        self.picture.flight = self.source = None
        self.stage.set_overflow(Gtk.Overflow.HIDDEN)
        self.show_source()
        if self.closing:
            self.closing = False
            self.set_can_target(True)
            self.set_visible(False)
            if self.anim is not None:
                self.anim.pause()
                self.anim = None
            self.picture.set_paintable(None)  # (a full-size image: let it go)
            self.let_go()
            self.items = []
            self.strip_paths.splice(0, self.strip_paths.get_n_items(), [])

    def current(self):
        return self.items[self.index].image_actions

    def show_current(self):
        """The image as it is here at once, then its original at full size if there is one (ImageActions.original),
        read off the main thread and put in its place: the same picture, so it stays where it is, only sharper. A
        folder's picture (open_files): its thumbnail, then the file read (load_near)."""
        acts = self.current()
        if isinstance(acts, FileImageActions):  # (a folder's: read as it's shown, its thumbnail meanwhile)
            tex = acts.full or acts.preview
            self.load_near()
        else:
            self.let_go(acts)
            tex = acts.full[1] if acts.full else acts.texture()
        self.picture.set_paintable(tex)
        self.move_to(1.0, 0.0, 0.0, animate=False)  # (each image from fitted)
        if isinstance(acts, FileImageActions) and acts.full is None:
            self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)  # (its thumbnail, filling it as a photo will)
        elif tex is not None:
            self.fit(tex)
        if not isinstance(acts, FileImageActions) and acts.full is None and acts.path and not acts.no_original \
                and not acts.looking:
            acts.looking = True

            def done(found, acts=acts):
                acts.looking = False
                acts.no_original = found is None
                if found and self.get_visible() and self.items and self.current() is acts:
                    acts.full = found
                    self.sharpen(found[1])
                return GLib.SOURCE_REMOVE
            IMAGE_POOL.submit(lambda: GLib.idle_add(done, acts.original()))
        if self.strip.get_visible() and self.strip_pick.get_selected() != self.index:
            self.strip_pick.set_selected(self.index)
            self.strip_view.scroll_to(self.index, Gtk.ListScrollFlags.NONE, None)
        self.show_title(acts)

    def let_go(self, keep=None):
        """The originals read for the images not shown (each tens of megabytes decoded): read again if shown again."""
        for widget in self.items:
            if widget.image_actions is not keep:
                widget.image_actions.full = None

    def fit(self, tex):
        # big images fill the window; small ones aren't blown up into a blur
        big = max(tex.get_width(), tex.get_height()) >= 400
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN if big else Gtk.ContentFit.SCALE_DOWN)

    def sharpen(self, tex):
        """tex, the original, where the smaller one was: zoomed and moved as that was (a small one that was shown at its
        own size, now big, is fitted)."""
        fitted = self.picture.get_width() and self.picture.fitted()
        self.picture.set_paintable(tex)
        self.fit(tex)
        if fitted and self.picture.fitted() != fitted:
            self.move_to(1.0, 0.0, 0.0, animate=False)
        self.picture.queue_draw()
        self.zoom_shown()

    def show_title(self, acts):
        self.title.set_text(os.path.basename(acts.path) if acts.path else
                            (acts.name or acts.url or wording.say("images.untitled")))
        self.buttons["browser"].set_visible(bool(acts.url))
        self.zoom_shown()

    def near(self, acts):
        """Whether acts is the picture shown or one either side of it (read at its own size, to step to at once)."""
        n = len(self.items)
        if not n or self.closing:
            return False
        return any(self.items[(self.index + d) % n].image_actions is acts for d in (0, 1, -1))

    def load_near(self):
        """A folder's pictures read at their own size: the one shown and those either side (stepped to next, they're
        there at once); the others let go (a folder of photos, each tens of megabytes decoded). Stepped past before
        it's read, one isn't (the latest asked for is read next)."""
        for widget in self.items:
            acts = widget.image_actions
            if not self.near(acts):
                acts.full = None
                acts._data = None
                acts.full_is_thumb = False
            elif acts.full is None and not getattr(acts, "loading", False):
                acts.loading = True

                def done(tex, acts=acts):
                    acts.loading = False
                    if tex is None and not self.near(acts):
                        return GLib.SOURCE_REMOVE
                    if tex is None:  # (no decoder here for it: the system's thumbnail of it, large)
                        acts.undecodable = True
                        if self.thumb is not None:
                            self.thumb(acts.path, round(2048 * screen_scale(self)),
                                       lambda t, acts=acts: self.shown_full(acts, t, True))
                        return GLib.SOURCE_REMOVE
                    self.shown_full(acts, tex, False)
                    return GLib.SOURCE_REMOVE

                def work(acts=acts, done=done):
                    if not self.near(acts) or acts.undecodable:  # (stepped past already, or there's no reading it)
                        GLib.idle_add(done, None)
                        return
                    GLib.idle_add(done, acts.decode())
                IMAGE_POOL.submit(work)

    def shown_full(self, acts, tex, thumb):
        """acts read (tex; thumb: the system's thumbnail of it, all there is), in place of its thumbnail if it's
        shown: the same picture, so where it was. Let go if it's been stepped away from meanwhile."""
        if tex is None or not self.near(acts):
            if tex is None and self.items and self.current() is acts:
                self.zoom_shown()
            return
        acts.full, acts.full_is_thumb = tex, thumb
        if self.current() is acts:
            fitted = self.picture.get_paintable() is not None and self.picture.get_width() and self.picture.fitted()
            self.picture.set_paintable(tex)
            self.fit(tex)
            if not fitted or self.picture.fitted() != fitted:
                self.move_to(1.0, 0.0, 0.0, animate=False)
            self.picture.queue_draw()
            self.zoom_shown()

    def zoom_shown(self):
        """What says how far it's zoomed: the size in the bar, the arrows (they step between images, so not while it's
        zoomed in) and the pointer."""
        n, zoom, acts = len(self.items), self.target[0], self.current()
        if isinstance(acts, FileImageActions):  # (its own size, not its thumbnail's; nothing until it's read)
            kb = acts.size() / 1024
            failed = acts.undecodable and not getattr(acts, "loading", False)
            what = (image_description(acts.size(), acts.full) if not acts.full_is_thumb else
                    wording.say("images.size_mb", mb=f"{kb / 1024:.1f}") if kb > 1024 else
                    wording.say("images.size_kb", kb=f"{kb:.0f}")) if acts.full is not None else \
                wording.say("images.cannot_show") if failed else ""
        else:  # (not decoded again)
            what = image_description(acts.full[0] if acts.full else acts.data, self.picture.get_paintable())
        self.info.set_text("  ·  ".join(part for part in (
            what,
            f"{self.index + 1} / {n}" if n > 1 else "", f"{round(zoom * 100)}%" if self.zoomed() else "") if part))
        self.prev_btn.set_visible(n > 1 and not self.zoomed())
        self.next_btn.set_visible(n > 1 and not self.zoomed())
        pointers.set_pointer(self.picture, "grab" if self.zoomed() else None)

    def zoomed(self):
        return self.target[0] > 1.001

    def most_zoom(self):
        tex = self.picture.get_paintable()
        if tex is None or not self.picture.get_width():
            return 1.0
        return max(2.0, self.MOST_PIXELS * tex.get_intrinsic_width() / self.picture.fitted()[0])

    @staticmethod
    def kept_in(size, view, before, after, pan):
        """How far an image of size can be moved from the middle of a picture of view, with margins before and after it
        that it can use too: its edges never come inside what's shown, and it stays in the middle if it all fits."""
        if size <= view + before + after:
            return 0.0
        middle = (view - size) / 2
        return min(max(pan, view + after - size - middle), -before - middle)

    def move_to(self, zoom, x, y, animate=True):
        """The image zoom times its fitted size, moved by (x, y): as far as it can go that way, smoothly unless it's
        following your hand."""
        pic = self.picture
        zoom = max(1.0, min(zoom, self.most_zoom()))
        if pic.get_paintable() is not None and pic.get_width():
            w, h = pic.fitted()
            x = self.kept_in(w * zoom, pic.get_width(), pic.get_margin_start(), pic.get_margin_end(), x)
            y = self.kept_in(h * zoom, pic.get_height(), pic.get_margin_top(), pic.get_margin_bottom(), y)
        self.target = (zoom, x, y)
        if self.anim is not None:
            self.anim.pause()
            self.anim = None
        if not animate or not pic.get_mapped():
            pic.zoom, pic.pan_x, pic.pan_y = zoom, x, y
            pic.queue_draw()
        else:
            start = (pic.zoom, pic.pan_x, pic.pan_y)

            def frame(t):
                pic.zoom = start[0] * (zoom / start[0]) ** t  # (evenly, as it looks: each step the same ratio)
                pic.pan_x, pic.pan_y = start[1] + (x - start[1]) * t, start[2] + (y - start[2]) * t
                pic.queue_draw()
            self.anim = Adw.TimedAnimation.new(pic, 0.0, 1.0, self.ZOOM_MS, Adw.CallbackAnimationTarget.new(frame))
            self.anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
            self.anim.play()
        if self.items:
            self.zoom_shown()

    def zoom_to(self, zoom, at=None, animate=True):
        """Zoom times its fitted size, what's under at (in the picture; its middle if there's none) staying there."""
        pic = self.picture
        now, x, y = self.target
        zoom = max(1.0, min(zoom, self.most_zoom()))
        ax, ay = at if at is not None else (pic.get_width() / 2, pic.get_height() / 2)
        ax, ay = ax - pic.get_width() / 2, ay - pic.get_height() / 2  # (from the middle, as the pan is)
        self.move_to(zoom, ax - (ax - x) * zoom / now, ay - (ay - y) * zoom / now, animate)

    def in_picture(self, widget, x, y):
        ok, point = widget.compute_point(self.picture, Graphene.Point().init(x, y))
        return (point.x, point.y) if ok else None

    def on_scroll(self, controller, dx, dy):
        """A wheel zooms, around the pointer. A trackpad goes around the image while it's zoomed in, and zooms while it
        isn't, or with Ctrl or ⌘ held."""
        at = self.in_picture(self.stage, *self.pointer) if self.pointer else None
        held = controller.get_current_event_state() & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK)
        if controller.get_unit() == Gdk.ScrollUnit.WHEEL:
            self.zoom_to(self.target[0] * self.ZOOM_WHEEL ** -dy, at)
        elif self.zoomed() and not held:
            self.move_to(self.target[0], self.target[1] - dx, self.target[2] - dy, animate=False)
        elif dy:
            self.zoom_to(self.target[0] * 1.01 ** -dy, at, animate=False)
        return True

    def on_drag_begin(self, _gesture, _x, _y):
        self.dragged_from = self.target

    def on_drag(self, _gesture, dx, dy):
        if self.dragged_from[0] > 1.001:
            self.move_to(self.dragged_from[0], self.dragged_from[1] + dx, self.dragged_from[2] + dy, animate=False)

    def on_pinch(self, gesture, scale):
        ok, x, y = gesture.get_bounding_box_center()
        self.zoom_to(self.pinched_from * scale, self.in_picture(self.stage, x, y) if ok else None, animate=False)

    def on_press(self, _gesture, n, x, y):
        """Twice on the image: zoomed to twice its size around there, or fitted again."""
        if n == 2 and self.on_image(self.picture, x, y):
            self.zoom_to(1.0 if self.zoomed() else 2.0, (x, y))

    def step(self, delta):
        if len(self.items) > 1:
            if self.flying is not None:  # (flying out of one image: there, before it's another)
                self.flying.skip()
            self.index = (self.index + delta) % len(self.items)
            self.show_current()

    def act(self, key):
        acts = self.current()
        {"copy": acts.copy, "folder": acts.open_folder, "open": acts.open, "browser": acts.open_in_browser}[key]()

    def close(self):
        if not self.closing:  # (already going: it goes)
            self.fly(False)
            self.win.input.grab_focus()

    def image_rect(self):
        """Where the image is actually painted inside the picture widget (it's letterboxed, or zoomed)."""
        return self.picture.rect()

    def on_image(self, widget, x, y):
        point = self.in_picture(widget, x, y)
        if point is None:
            return False
        ix, iy, iw, ih = self.image_rect()
        return ix <= point[0] <= ix + iw and iy <= point[1] <= iy + ih

    def on_click(self, gesture, _n, x, y):
        """A click that isn't on the image (or a button, or the pictures along the bottom, which handle their own)
        closes the view."""
        hit = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if hit is not None and (hit is self.strip or hit.is_ancestor(self.strip)):
            return
        if not self.on_image(self, x, y):
            self.close()

    def on_key(self, _ctl, keyval, _code, _state):
        if self.closing:  # (going: they're the window's, if the focus didn't get back there)
            return False
        zoom, x, y = self.target
        nudge = {Gdk.KEY_Left: (1, 0), Gdk.KEY_KP_Left: (1, 0), Gdk.KEY_Right: (-1, 0), Gdk.KEY_KP_Right: (-1, 0),
                 Gdk.KEY_Up: (0, 1), Gdk.KEY_KP_Up: (0, 1), Gdk.KEY_Down: (0, -1), Gdk.KEY_KP_Down: (0, -1)}.get(keyval)
        if keyval == Gdk.KEY_Escape:
            self.close()
        elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self.zoom_to(zoom * self.ZOOM_KEY)
        elif keyval in (Gdk.KEY_minus, Gdk.KEY_underscore, Gdk.KEY_KP_Subtract):
            self.zoom_to(zoom / self.ZOOM_KEY)
        elif keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
            self.zoom_to(1.0)
        elif nudge is not None and self.zoomed():  # (a fifth of what's shown at a time; the image moves the other way)
            self.move_to(zoom, x + nudge[0] * self.picture.get_width() / 5, y + nudge[1] * self.picture.get_height() / 5)
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left):
            self.step(-1)
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
            self.step(1)
        else:
            return False
        return True
