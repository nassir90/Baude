"""imageviews — pictures in a chat: a view's fade while it loads (Fade), the refresh button's turn (Turning,
RefreshButton), a picture at its own size (FixedPicture) and decoded only near what's shown (PictureWatch), an
image's widget and its link, a web image fetched, and an image's click and menu (ImageActions, FileImageActions,
make_image_interactive).
"""
import hashlib
import html
import math
import os
import weakref
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402,F401

import pointers  # noqa: E402
from baudelib import wording  # noqa: E402
from widgets import IMAGE_POOL, fit_size, image_dims, scaled_texture, screen_scale, toast  # noqa: E402


class Fade:
    """Claude's fade, for swapping what a view shows (the chat; a cluster, a kind, a namespace; a database, a schema):
    the widgets go lighter at once and stay so until the new content is in, then come back at full strength. Asked
    again while they're light, they stay light: no new fade, no flicker. hold() … done() is for a swap read in the
    background (its stale results are the caller's to drop); swap(action) for one that's instant, run after the fade
    so it's seen. Either way the last one asked for wins. Never back sooner than MS after it went, so a quick read
    is still seen to swap (at once with reduced motion, which GTK's transitions skip too). Only these widgets fade:
    the controls beside them stay as they are, to pick another while it reads."""
    MS = 130

    def __init__(self, *widgets):
        self.widgets, self.since, self.timer, self.next = widgets, None, None, None
        self.reading = self.swapping = False
        for w in widgets:
            w.add_css_class("switchable")

    @property
    def faded(self):
        return self.since is not None

    @staticmethod
    def animated():
        return Gtk.Settings.get_default().get_property("gtk-enable-animations")

    def out(self):
        if self.timer is not None:  # (on its way back: stays)
            GLib.source_remove(self.timer)
            self.timer = None
        if self.since is None:
            self.since = time.monotonic()
            for w in self.widgets:
                w.add_css_class("switching")

    def hold(self):
        """Faded until done(): a swap read in the background. An instant swap still waiting is dropped (this one's
        later)."""
        self.reading, self.next = True, None
        self.out()

    def done(self):
        """The new content is in (or its error is): back, unless an instant swap is still to run."""
        self.reading = False
        self.back()

    def swap(self, action):
        """Fade, run action (an instant swap) once faded, and come back. Asked again before it ran, or while it ran
        (a slow chat loading), the last one runs next, still faded."""
        self.next = action
        self.out()
        if not self.swapping:
            self.swapping = True
            GLib.timeout_add(self.MS if self.animated() else 1, self._swap)

    def _swap(self):
        action, self.next = self.next, None
        if action is not None:
            action()
        GLib.idle_add(self._swapped)  # (after the clicks that arrived while it was busy)
        return False

    def _swapped(self):
        if self.next is not None:  # another one was asked for meanwhile: swap again, still faded
            GLib.timeout_add(1, self._swap)
            return False
        self.swapping = False
        self.back()
        return False

    def back(self):
        if self.reading or self.swapping or self.since is None or self.timer is not None:
            return
        left = self.MS - (time.monotonic() - self.since) * 1000
        if left >= 1 and self.animated():
            self.timer = GLib.timeout_add(int(left), self._back)
        else:
            self._back()

    def _back(self):
        self.since = self.timer = None
        for w in self.widgets:
            w.remove_css_class("switching")
        return False


class Turning:
    """How something turns while a load runs and comes to rest when it ends (RefreshButton's icon), as a function of the
    time: at(now) says where it is, in turns (a whole number is upright), and how fast it's going, worked out from the
    legs it's on with nothing added up frame by frame, so a late frame or a while hidden costs it nothing. Each leg eases
    from one speed to another, with no jolt at either end, and the next starts where it ended, so whenever start and stop
    come there's no jump in where it is or how fast it's going. A load that ends part way round carries on to where it
    can slow down and stop upright; one that's over at once still gets up to speed and goes round once; one that starts
    while it's slowing picks up speed from there."""
    SPEED = 1.25  # (turns a second, while it reads)
    UP_S, DOWN_S = 0.25, 0.6  # (from standing to that speed; from that speed to standing)

    def __init__(self):
        self.loading, self.legs, self.rest = False, [], 0  # (a leg: when it starts, where, its two speeds, how long)

    def at(self, now):
        """(turns, turns a second) at time now, in seconds: a function of the time and the legs, and of nothing else."""
        for start, turns, v0, v1, seconds in self.legs:
            if now < start + seconds:
                dt = max(now - start, 0.0)
                u = dt / seconds  # (the speed goes by smoothstep; where it is, by what that adds up to)
                return turns + v0 * dt + (v1 - v0) * dt * u * u * (1 - u / 2), v0 + (v1 - v0) * u * u * (3 - 2 * u)
        return self.rest, 0.0

    def over(self, now):
        """At rest by then, with nothing more to draw."""
        return not self.loading and (not self.legs or now >= self.legs[-1][0] + self.legs[-1][4])

    def plan(self, start, turns, *legs):
        """Legs (speed at its start, at its end, how long) one after the other, from there."""
        self.legs = []
        for v0, v1, seconds in legs:
            if seconds > 0:
                self.legs.append((start, turns, v0, v1, seconds))
                start, turns = start + seconds, turns + seconds * (v0 + v1) / 2

    def start(self, now):
        if not self.loading:
            self.loading = True
            turns, speed = self.at(now)
            self.plan(now, turns, (speed, self.SPEED, self.UP_S * (1 - speed / self.SPEED)),
                      (self.SPEED, self.SPEED, math.inf))

    def stop(self, now):
        if self.loading:
            self.loading = False
            *up, (start, turns, *_) = self.legs  # (still getting up to speed: that goes on as it was)
            if now > start:
                up, start, turns = [], now, self.at(now)[0]
            slowing = self.SPEED * self.DOWN_S / 2  # (how far it goes while it slows)
            self.rest = math.ceil(turns + slowing)
            self.plan(start, turns, (self.SPEED, self.SPEED, (self.rest - turns - slowing) / self.SPEED),
                      (self.SPEED, 0.0, self.DOWN_S))
            self.legs[:0] = up


def turned_path(path, turned, x, y):
    """A path turned about (x, y), by that much of a turn: its own points moved, so it's drawn afresh as it now lies, as
    sharp at any angle and scale as it is upright. (A transform round it has the renderer draw it upright and turn the
    picture: softer, differently at each angle, so the circle of an icon shimmers.)"""
    cos, sin = math.cos(turned * math.tau), math.sin(turned * math.tau)
    builder = Gsk.PathBuilder.new()
    ways = {Gsk.PathOperation.LINE: builder.line_to, Gsk.PathOperation.QUAD: builder.quad_to,
            Gsk.PathOperation.CUBIC: builder.cubic_to}

    def moved(points):
        return [v for p in points for v in (x + (p.x - x) * cos - (p.y - y) * sin, y + (p.x - x) * sin + (p.y - y) * cos)]

    def add(op, points, _n, _weight, *_):
        if op == Gsk.PathOperation.CLOSE:
            builder.close()
        elif op == Gsk.PathOperation.MOVE:
            builder.move_to(*moved(points[:1]))
        else:
            ways[op](*moved(points[1:]))
        return True
    path.foreach(Gsk.PathForeachFlags.QUAD | Gsk.PathForeachFlags.CUBIC, add)
    return builder.to_path()


class RefreshButton(Gtk.Button):
    """A flat button with the refresh icon, for something read in the background: set_loading(True) as the read starts
    and the icon turns, in its full colour (it's still a button: a press meanwhile is the caller's to make harmless);
    set_loading(False) as it ends and it comes to rest upright (Turning). The icon's the theme's, and at rest it's just
    the button's own, drawn by GTK as any other is, to the pixel. A tick callback only while it's moving and mapped: at
    rest or hidden nothing runs, and shown again it's where the time says. With animations off it still turns, as a
    spinner does (it's what says something's happening), but it's upright at once when it ends, as it is if nobody's
    there to see it slow."""
    MIDDLE = 7.5, 8.5  # (in the 16 an icon's drawn in: the centre of Adwaita's circle, which so stays still, no wobble)

    def __init__(self, **kw):
        super().__init__(icon_name="view-refresh-symbolic", **kw)
        self.add_css_class("flat")
        self.turning, self.tick = Turning(), None
        self.connect("map", RefreshButton._mapping)
        self.connect("unmap", RefreshButton._mapping)

    def now(self):
        """The time as the frames have it, in seconds (with no frames to go by, the same clock's)."""
        clock = self.get_frame_clock()
        return (clock.get_frame_time() if clock is not None else GLib.get_monotonic_time()) / 1e6

    def set_loading(self, on):
        now = self.now()
        if on:
            self.turning.start(now)
        elif self.get_mapped() and Fade.animated():
            self.turning.stop(now)
        else:
            self.turning = Turning()
        self.sync(now)

    def sync(self, now):
        """Ticking if it's moving and can be seen, and not otherwise."""
        moving = self.get_mapped() and not self.turning.over(now)
        if moving and self.tick is None:
            self.tick = self.add_tick_callback(RefreshButton._tick)
        elif not moving and self.tick is not None:
            self.remove_tick_callback(self.tick)
            self.tick = None
        self.queue_draw()

    @staticmethod
    def _mapping(button):
        button.sync(button.now())

    @staticmethod
    def _tick(button, clock):
        button.frame(clock.get_frame_time() / 1e6)
        return GLib.SOURCE_CONTINUE  # (frame removes it, when that's it)

    def frame(self, now):
        """A frame at time now (a tick's whole job: a test can call it with a time of its own): drawn, the last if it's
        at rest by then."""
        self.queue_draw()
        if self.turning.over(now):
            self.sync(now)

    def do_snapshot(self, snapshot):
        turned = self.turning.at(self.now())[0] % 1
        if not turned:
            Gtk.Button.do_snapshot(self, snapshot)
            return
        icon = Gtk.Snapshot()
        Gtk.Button.do_snapshot(self, icon)
        if (node := icon.to_node()) is not None:
            self.draw_turned(snapshot, node, turned)

    def draw_turned(self, snapshot, node, turned):
        """What the icon draws, turned: its paths (turned_path), in what's round them, but not its clip, the square it's
        drawn in, which the circle touches and which would cut into it at an angle. Anything else (an icon that's a
        picture) is turned as it is."""
        if isinstance(node, Gsk.ClipNode):
            self.draw_turned(snapshot, node.get_child(), turned)
        elif isinstance(node, Gsk.TransformNode):
            snapshot.save()
            snapshot.transform(node.get_transform())
            self.draw_turned(snapshot, node.get_child(), turned)
            snapshot.restore()
        elif isinstance(node, Gsk.ContainerNode):
            for i in range(node.get_n_children()):
                self.draw_turned(snapshot, node.get_child(i), turned)
        elif isinstance(node, Gsk.FillNode):
            snapshot.append_fill(turned_path(node.get_path(), turned, *self.MIDDLE), node.get_fill_rule(),
                                 self.get_child().get_color())
        else:
            x, y = self.MIDDLE
            snapshot.save()
            snapshot.translate(Graphene.Point().init(x, y))
            snapshot.rotate(turned * 360)
            snapshot.translate(Graphene.Point().init(-x, -y))
            snapshot.append_node(node)
            snapshot.restore()


class FixedPicture(Gtk.Picture):
    """A picture that is always exactly `size` (logical pixels). A plain Gtk.Picture is
    height-for-width: inside a vertical box it is asked for its height at the box's full
    width and scales up to fill it, and its minimum size is 0x0, which some layouts hand it.
    Its texture is made by load(scale) at the screen's pixel density (so it's sharp on a scaled
    display), and made again if the window moves to a denser screen. In a scroller it holds that
    texture only while it's near what's shown (PictureWatch: a long chat's pictures are all
    mapped, wherever it's scrolled to); elsewhere it's made when the picture is first shown."""

    def __init__(self, size, load):
        super().__init__()
        self.size, self.factory, self.pending, self.loaded_scale, self.loading = size, load, True, 0.0, False
        self.wanted, self.watch, self.spot, self.surface_watch = False, None, None, None
        # (plain functions, given the picture by the signal: a closure over it would be a cycle PyGObject can't free)
        self.connect("map", FixedPicture._mapped)
        self.connect("unmap", FixedPicture._unmapped)
        self.connect("notify::scale-factor", FixedPicture.rescale)
        self.connect("realize", FixedPicture._watch_surface)
        self.connect("unrealize", FixedPicture._unwatch_surface)

    def _mapped(self):
        self.watch = PictureWatch.of(self)
        if self.watch is not None:
            self.watch.add(self)  # (decoded once it's found near the view)
        else:
            self.ensure_soon()

    def _unmapped(self):
        if self.watch is not None:
            self.watch.pictures.discard(self)
            self.watch = None

    def _watch_surface(self):
        native = self.get_native()
        surface = native.get_surface() if native is not None else None
        if surface is not None and surface.find_property("scale") is not None:  # fractional scales
            me = weakref.ref(self)  # (the window outlives its pictures: it mustn't keep them, and their pixels)
            self.surface_watch = surface, surface.connect("notify::scale", lambda *_: (p := me()) and p.rescale())

    def _unwatch_surface(self):
        if self.surface_watch is not None:
            surface, handler = self.surface_watch
            self.surface_watch = None
            surface.disconnect(handler)

    def rescale(self, *_):
        if not self.pending and screen_scale(self) > self.loaded_scale + 0.01:
            self.pending = True
            if self.get_mapped() and self.wanted:
                self.ensure_soon()

    def ensure_soon(self):
        """Decode in the background (a big screenshot takes ~60ms), then show it."""
        self.wanted = True
        if not self.pending or self.loading:
            return
        self.loading, scale = True, screen_scale(self)

        def work():
            if not self.wanted:  # (a fling carried it past before its turn came)
                GLib.idle_add(done, None, False)
                return
            try:
                tex = self.factory(scale)
            except GLib.Error:
                tex = None
            GLib.idle_add(done, tex, True)

        def done(tex, tried):
            self.loading = False
            if not (self.pending and self.wanted):  # loaded synchronously meanwhile, or gone far away again
                return False
            if tried:
                self.pending, self.loaded_scale = False, scale
                if tex is not None:
                    Gtk.Picture.set_paintable(self, tex)
            else:
                self.ensure_soon()  # (skipped, but it's back)
            return False
        IMAGE_POOL.submit(work)

    def release(self):
        """Far out of view: give the pixels back (decoded again when it comes near). Its size stays,
        so nothing around it moves."""
        self.wanted = False
        if Gtk.Picture.get_paintable(self) is not None:
            self.pending, self.loaded_scale = True, 0.0
            Gtk.Picture.set_paintable(self, None)

    def ensure(self):
        if self.pending:
            scale = screen_scale(self)
            self.pending, self.loaded_scale = False, scale
            Gtk.Picture.set_paintable(self, self.factory(scale))

    def get_paintable(self):
        self.ensure()
        return Gtk.Picture.get_paintable(self)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, orientation, for_size):
        n = self.size[0] if orientation == Gtk.Orientation.HORIZONTAL else self.size[1]
        return n, n, -1, -1


class PictureWatch:
    """Which of a scroller's pictures are near what it shows. A picture is decoded once it's within
    NEAR screens of the visible part and gives its pixels back past FAR (in between it stays as it
    is, so one at the edge isn't decoded over and over). One per scroller, joined by its pictures
    while they're mapped. It follows the scroller's vadjustment, and looks once a frame at most,
    after layout: scrolled, the positions it has are still good (they're in the content's
    coordinates); the content changed size, they're measured again. Never a walk of the widgets."""

    NEAR, FAR = 1.0, 3.0  # screens

    def __init__(self, scroller):
        self.scroller, self.pictures = weakref.ref(scroller), weakref.WeakSet()
        self.queued = self.moved = False
        adj = scroller.get_vadjustment()
        adj.connect("value-changed", self.queue)
        adj.connect("changed", self.relayout)  # (upper, page size: something grew, shrank, or the window did)

    @classmethod
    def of(cls, widget):
        """The watch of the scroller widget is in (made on first use), or None: in none, or in one
        whose child places its own children (a list or text view: positions in view coordinates,
        stale after each scroll). One that only scrolls sideways (a wide table) is looked past."""
        scroller = widget.get_ancestor(Gtk.ScrolledWindow)
        while scroller is not None and scroller.get_policy()[1] == Gtk.PolicyType.NEVER:
            parent = scroller.get_parent()
            scroller = parent.get_ancestor(Gtk.ScrolledWindow) if parent is not None else None
        if scroller is None or not isinstance(scroller.get_child(), Gtk.Viewport):
            return None
        watch = getattr(scroller, "picture_watch", None)
        if watch is None:
            watch = scroller.picture_watch = cls(scroller)
        return watch

    def add(self, pic):
        pic.spot = None  # (where it is: measured at the next look)
        self.pictures.add(pic)
        self.queue()

    def relayout(self, *_):
        self.moved = True
        self.queue()

    def queue(self, *_):
        scroller = self.scroller()
        if not self.queued and scroller is not None:
            self.queued = True
            scroller.add_tick_callback(self._frame)

    def _frame(self, *_):
        GLib.idle_add(self.look)  # (queued at the start of a frame, it runs once the frame is laid out)
        return GLib.SOURCE_REMOVE

    def look(self):
        self.queued = False
        scroller = self.scroller()
        view = scroller.get_child() if scroller is not None else None
        content = view.get_child() if isinstance(view, Gtk.Viewport) else None
        adj = scroller.get_vadjustment() if content is not None else None
        if adj is None or adj.get_page_size() <= 0:
            return False
        moved, self.moved = self.moved, False
        top, page = adj.get_value(), adj.get_page_size()
        for pic in list(self.pictures):
            if moved or pic.spot is None:
                ok, r = pic.compute_bounds(content)
                pic.spot = (r.get_y(), r.get_y() + r.get_height()) if ok and pic.get_width() else None
            if pic.spot is None:  # (not laid out, or not under the content after all: shown, as outside a scroller)
                pic.ensure_soon()
                continue
            away = max(top - pic.spot[1], pic.spot[0] - top - page)  # past the view's edge (< 0: in it)
            if away < page * self.NEAR:
                pic.ensure_soon()
            elif away > page * self.FAR:
                pic.release()
        return False


def image_widget(data, max_edge=480, path=None, name=None, url=None):
    """A picture whose natural size is bounded (a bare Gtk.Picture grows to the image size).
    Click to view it large; right-click to copy it or open it / its folder. Where it's from opens it as a link
    (IMAGE_LINK, given by whoever shows that: a Read's header, the line over a markdown image)."""
    dims = image_dims(data)
    if not dims:  # (a format whose header image_dims can't read: decode it once to learn its size)
        tex = scaled_texture(data, 100_000)
        dims = (tex.get_width(), tex.get_height())
    # sized from the header now, decoded when shown (in a scroller: while near what's shown)
    pic = FixedPicture(size=fit_size(*dims, max_edge), load=lambda scale: scaled_texture(data, max_edge, scale))
    pic.add_css_class("md-image")
    acts = make_image_interactive(pic, data, path, name, url)
    box = Gtk.Overlay(child=pic, halign=Gtk.Align.START)
    box.image_actions, box.picture = acts, pic
    return box


IMAGE_LINK = "baude-image:open"  # (a link that opens its image as ImageActions.launch does)


def image_link_markup(text, acts):
    """text as the link that opens an image (acts: its ImageActions): a web one in the browser, a file in its app."""
    tip = wording.say("images.open_in_browser") if acts.url else wording.say("images.open_with_default_app")
    return f'<a href="{IMAGE_LINK}" title="{tip}">{html.escape(text, quote=False)}</a>'


def with_image_link(image, text):
    """image (image_widget's) under a line with where it's from (text: its path or URL), a link that opens it."""
    link = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
    link.set_markup(image_link_markup(text, image.image_actions))
    link.add_css_class("image-link")
    link.connect("activate-link", lambda _l, uri: uri == IMAGE_LINK and (image.image_actions.launch() or True))
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.START)
    box.append(link)
    box.append(image)
    box.link = link
    return box


# ------------------------------------------------------------ web images --
# Markdown can point at images on the web. They are downloaded (once per URL, off the main
# thread) and shown like local ones; until then, or if that fails, a link is shown.

WEB_IMAGE_MAX = 15_000_000


_web_images = {}    # url -> bytes, or an error message


_web_waiting = {}   # url -> callbacks waiting for it


def fetch_web_image(url, done):
    """done(data_or_None, error_or_None) on the main loop."""
    if url in _web_images:
        hit = _web_images[url]
        done(hit, None) if isinstance(hit, bytes) else done(None, hit)
        return
    if url in _web_waiting:
        _web_waiting[url].append(done)
        return
    _web_waiting[url] = [done]

    def work():
        import urllib.error  # (only now: http/ssl/email cost ~25 ms at start-up)
        import urllib.request
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "baude"})
            with urllib.request.urlopen(req, timeout=15) as r:
                kind = r.headers.get_content_type()
                data = r.read(WEB_IMAGE_MAX + 1)
            if not kind.startswith("image/"):
                raise ValueError(wording.say("images.not_an_image", kind=kind))
            if len(data) > WEB_IMAGE_MAX:
                raise ValueError(wording.say("images.too_large"))
            result, error = data, None
        except (OSError, ValueError, urllib.error.URLError) as e:
            result, error = None, str(e)
        GLib.idle_add(deliver, result, error)

    def deliver(result, error):
        _web_images[url] = result if result is not None else error
        for cb in _web_waiting.pop(url, []):
            cb(result, error)
        return False

    threading.Thread(target=work, daemon=True).start()


# ----------------------------------------------------------- image actions --

def image_cache_path(data):
    """Images that only exist in memory (pasted, or returned by a tool) are written here on
    first use, so they can be opened by other apps and shown in a folder."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    path = os.path.join(base, "baude", "images", hashlib.sha1(data).hexdigest()[:16] + _image_ext(data))
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    return path


def _image_ext(data):
    for magic, ext in ((b"\x89PNG", ".png"), (b"\xff\xd8", ".jpg"), (b"GIF8", ".gif"), (b"RIFF", ".webp")):
        if data.startswith(magic):
            return ext
    return ".png"


def same_picture(small, data, w, h):
    """Whether data, scaled down to w×h, is the picture small is (its pixels within a little of small's, on a grid
    across it: a file rewritten since with another picture of the same size isn't)."""
    a = _pixbuf(small)
    b = _pixbuf(data, w, h)
    if b.get_width() != w or b.get_height() != h:
        b = b.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
    pa, pb = a.get_pixels(), b.get_pixels()
    na, nb, ra, rb = a.get_n_channels(), b.get_n_channels(), a.get_rowstride(), b.get_rowstride()
    total = count = 0
    for gy in range(24):
        y = min(h - 1, int((gy + 0.5) * h / 24))
        for gx in range(24):
            x = min(w - 1, int((gx + 0.5) * w / 24))
            ia, ib = y * ra + x * na, y * rb + x * nb
            total += sum(abs(pa[ia + c] - pb[ib + c]) for c in range(3))
            count += 3
    return total / count < 14


def _pixbuf(data, w=None, h=None):
    loader = GdkPixbuf.PixbufLoader()
    if w:
        loader.connect("size-prepared", lambda ld, _w, _h: ld.set_size(w, h))
    loader.write(data)
    loader.close()
    return loader.get_pixbuf()


class ImageActions:
    """What the image click / right-click menu does. One per image widget."""

    def __init__(self, widget, data, path=None, name=None, url=None):
        self.widget, self.data, self.name, self.url = widget, data, name, url
        self.path = path if path and os.path.isfile(path) else None
        self.full, self.no_original, self.looking = None, False, False  # (the lightbox's: Lightbox.show_current)

    def file(self):
        return Gio.File.new_for_path(self.path or image_cache_path(self.data))

    def texture(self):
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(self.data))

    def original(self):
        """The image from the file it came from, if that's a better copy of it than the bytes here (a Read's image and
        an attachment are scaled down, or recompressed, for the model): more pixels, or as many in more bytes, and
        still the same picture (scaled down to this one's size, it matches it). (bytes, texture), or None. Read and
        decoded here: call it off the main thread."""
        if not self.path or not image_dims(self.data):
            return None
        try:
            with open(self.path, "rb") as f:
                head = f.read(65536)
            sw, sh = image_dims(self.data)
            dims = image_dims(head)
            kind = Gio.content_type_get_mime_type(Gio.content_type_guess(self.path, None)[0]) or ""
            if dims is None and not kind.startswith("image/"):
                return None  # (a notebook's or a PDF's picture: the file isn't it)
            with open(self.path, "rb") as f:
                data = f.read()
            if data == self.data:
                return None
            dims = dims or image_dims(data)
            if dims and (dims[0] < sw or (dims[0] == sw and len(data) <= len(self.data))):
                return None
            if not same_picture(self.data, data, sw, sh):
                return None
            tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except (OSError, GLib.Error, ValueError, ZeroDivisionError):
            return None
        if tex.get_width() < sw or abs(tex.get_width() / tex.get_height() - sw / sh) > 0.02 * sw / sh:
            return None
        return data, tex

    def best(self, then):
        """then(texture): the original if there's a better one (ImageActions.original), else these bytes' own.
        Looked for once, off the main thread; kept only while the lightbox shows it (full)."""
        if self.full:
            then(self.full[1])
            return

        def done(found):
            then(found[1] if found else self.texture())
            return GLib.SOURCE_REMOVE
        IMAGE_POOL.submit(lambda: GLib.idle_add(done, None if self.no_original else self.original()))

    def view(self):
        root = self.widget.get_root()
        if hasattr(root, "lightbox"):
            root.lightbox.open(self.widget)
        else:  # (a window with no large view: a folder's viewers on their own)
            self.open()

    def copy(self):
        def put(tex):
            self.widget.get_clipboard().set(tex)
            toast(self.widget, wording.say("images.copied"))
        self.best(put)

    def open(self):
        Gtk.FileLauncher(file=self.file()).launch(self.widget.get_root(), None, None)

    def open_folder(self):
        Gtk.FileLauncher(file=self.file()).open_containing_folder(self.widget.get_root(), None, None)

    def open_in_browser(self):
        Gtk.UriLauncher(uri=self.url).launch(self.widget.get_root(), None, None)

    def launch(self):
        """Opened from where it's from (IMAGE_LINK): a web image in the browser, a file in its default app."""
        if self.url:
            self.open_in_browser()
        else:
            self.open()


class FileImageActions(ImageActions):
    """A picture in a folder (the Files page's gallery), as the lightbox steps through them: read from its file only
    when it's shown (data, full), with a thumbnail meanwhile (preview); shown_in, the widget it's in on the page now, if
    it's in one, to fly out of and back into."""

    def __init__(self, widget, path, shown_in=None):
        super().__init__(widget, None, path, os.path.basename(path))
        self.full, self.preview, self.shown_in, self._data = None, None, shown_in, None
        self.undecodable, self.full_is_thumb = False, False  # (no decoder here for it: the system's thumbnail, large)

    @property
    def data(self):
        if self._data is None:
            try:
                with open(self.path, "rb") as f:
                    self._data = f.read()
            except OSError:
                self._data = b""
        return self._data

    @data.setter
    def data(self, value):
        self._data = value

    def size(self):
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def texture(self):
        return self.full or self.decode() or self.preview

    def copy(self):
        tex = self.texture()
        if tex is not None:
            self.widget.get_clipboard().set(tex)
        toast(self.widget, wording.say("images.copied") if tex is not None else wording.say("images.cannot_copy"))

    def decode(self):
        """The picture at its own size (off the main thread), or None."""
        try:
            return Gdk.Texture.new_from_filename(self.path)
        except GLib.Error:
            try:
                return Gdk.Texture.new_for_pixbuf(GdkPixbuf.Pixbuf.new_from_file(self.path))
            except GLib.Error:
                return None


def shaded_picture(picture):
    """picture (an Adw.Avatar: someone's picture; a bot's; a Gtk.Picture...) set into the page: a shade over it, darker
    to its rim, as if pressed in (.picture-shade), so a photo isn't a flat cut-out on the grey. In a Gtk.Overlay
    (returned: what goes in the layout, and what's dimmed to dim it all) that clips it and its shade to one shape,
    round, or the theme's corners. Its place in the layout (alignment, margins, expanding) the frame's, so the shade
    covers the picture and no more. Over an avatar, the shade's there only while it shows a picture, not initials."""
    frame = Gtk.Overlay(css_classes=["picture-frame"], overflow=Gtk.Overflow.HIDDEN,
                        halign=picture.get_halign(), valign=picture.get_valign(),
                        hexpand=picture.get_hexpand(), vexpand=picture.get_vexpand())
    for side in ("top", "bottom", "start", "end"):
        getattr(frame, f"set_margin_{side}")(getattr(picture, f"get_margin_{side}")())
        getattr(picture, f"set_margin_{side}")(0)
    picture.set_halign(Gtk.Align.FILL)
    picture.set_valign(Gtk.Align.FILL)
    frame.set_child(picture)
    shade = Gtk.Box(css_classes=["picture-shade"], can_target=False)
    frame.add_overlay(shade)
    if isinstance(picture, Adw.Avatar):  # (its picture can come later, or go)
        shown = lambda *_: shade.set_visible(picture.get_custom_image() is not None)  # noqa: E731
        picture.connect("notify::custom-image", shown)
        shown()
    return frame


def make_image_interactive(widget, data, path=None, name=None, url=None):
    acts = widget.image_actions = ImageActions(widget, data, path, name, url)
    group = Gio.SimpleActionGroup()
    for action, fn in (("view", acts.view), ("copy", acts.copy), ("open", acts.open),
                       ("folder", acts.open_folder), ("browser", acts.open_in_browser)):
        a = Gio.SimpleAction.new(action, None)
        a.connect("activate", lambda *_, fn=fn: fn())
        group.add_action(a)
    widget.insert_action_group("img", group)
    menu = Gio.Menu()
    menu.append(wording.say("common.view"), "img.view")
    if url:
        menu.append(wording.say("images.open_in_browser"), "img.browser")
    menu.append(wording.say("images.copy_image"), "img.copy")
    menu.append(wording.say("images.open_with_default_app"), "img.open")
    menu.append(wording.say("images.open_in_folder"), "img.folder")
    popover = widget.image_menu = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
    popover.set_parent(widget)
    widget.connect("destroy", lambda *_: popover.unparent())

    def pressed(gesture, _n, x, y):
        if gesture.get_current_button() == Gdk.BUTTON_SECONDARY:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
            popover.popup()
        else:
            acts.view()
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    click = Gtk.GestureClick(button=0)  # any button
    click.connect("pressed", pressed)
    widget.add_controller(click)
    pointers.magnifier(widget)
    widget.set_tooltip_text(wording.say("images.click_tip", name=path or name or wording.say("images.unnamed")))
    return acts
