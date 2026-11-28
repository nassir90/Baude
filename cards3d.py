"""cards3d — a prototype (not for merging): cards in a 3D room. Stage3D holds real card widgets (Meta's tool cards and
change boxes, the chat's tool and subagent cards), each allocated with a 3D transform (GSK perspective and rotate_3d),
so GTK draws them tilted and picks clicks through the same transform: their buttons stay live. The view orbits slowly;
a card is grabbed (press and drag past a few pixels, so a click still reaches its buttons) and thrown, then slides with
friction, bounces off the room's edges and pushes the cards it runs into; the one last grabbed is on top.

GLCards draws the same cards as textures (each card rendered to a GdkTexture, again only when it redraws) on quads in a
Gtk.GLArea, with picking of its own: the comparison the prototype measures. Neither is used by Baude or Record.
"""
import math
import random
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

CSS = """
.card3d { background-color: @window_bg_color; border-radius: 10px; padding: 8px;
          box-shadow: 0 6px 18px alpha(black, 0.28); }
.card3d.held { box-shadow: 0 16px 36px alpha(black, 0.40); }
"""
CARD_W = 340  # (a card's width at most; narrower if its content is)
PERSPECTIVE = 1300  # (the camera's distance, px)
FRICTION = 2.2  # (velocity lost per second, as a rate: v *= exp(-FRICTION * dt))
BOUNCE = 0.6  # (speed kept off a wall)
LAYER = 3.0  # (px between stacked cards in z)
CLAIM = 6  # (px a press moves before it's a grab, not a click)
SHADOW = 40  # (px round a cached card's picture for its shadow)


class Body:
    """A card's place in the room: centre (x, y) on the floor, z above it, in-plane turn, and how they're changing."""

    def __init__(self, child, x, y, spin):
        self.child, self.x, self.y, self.z, self.spin = child, x, y, 0.0, spin
        self.vx = self.vy = self.vspin = 0.0
        self.w = self.h = 0
        self.held, self.samples = False, []


def _y_axis():
    return Graphene.Vec3.y_axis()


def _x_axis():
    return Graphene.Vec3.x_axis()


class Camera:
    """The view: tilted back (pitch), turning a little either way (yaw) and drifting sideways (pan), slowly."""

    def __init__(self):
        self.t, self.orbit = 0.0, True
        self.pitch, self.yaw, self.pan = 30.0, 0.0, 0.0

    def step(self, dt):
        if self.orbit:
            self.t += dt
        self.yaw = 22 * math.sin(self.t * 0.35)
        self.pitch = 30 + 8 * math.sin(self.t * 0.23)
        self.pan = 40 * math.sin(self.t * 0.13)

    def room(self, w, h):
        """Room → stage: the stage's centre, the camera's perspective, its turn."""
        t = Gsk.Transform().translate(Graphene.Point().init(w / 2 + self.pan, h / 2))
        t = t.perspective(PERSPECTIVE)
        t = t.rotate_3d(self.pitch, _x_axis()).rotate_3d(self.yaw, _y_axis())
        return t

    def card(self, room, b):
        t = room.translate_3d(Graphene.Point3D().init(b.x, b.y, b.z))
        return t.rotate(b.spin).translate(Graphene.Point().init(-b.w / 2, -b.h / 2))


class Physics:
    """Bodies sliding on the floor: friction, walls, and the moving ones pushing what they meet (resting cards may
    overlap: they're stacked)."""

    def __init__(self):
        self.bodies, self.room_w, self.room_h, self.top = [], 800, 600, 0.0

    def place(self, rng):
        """Cards not placed yet spread over the floor in a jittered grid (all of them, by their order)."""
        if all(b.x is not None for b in self.bodies):
            return
        n = len(self.bodies)
        cols = max(1, round(math.sqrt(n * self.room_w / self.room_h / 2.2)))
        rows = math.ceil(n / cols)
        for i, b in enumerate(self.bodies):
            if b.x is None:
                cx, cy = (i % cols + 0.5) / cols - 0.5, (i // cols + 0.5) / rows - 0.5
                b.x = cx * (self.room_w - CARD_W) + rng.uniform(-20, 20)
                b.y = cy * (self.room_h - 120) + rng.uniform(-15, 15)

    def raise_(self, b):
        self.top += LAYER
        b.z = self.top

    def step(self, dt):
        if dt <= 0:
            return False
        moving = False
        hw, hh = self.room_w / 2, self.room_h / 2
        keep = math.exp(-FRICTION * dt)
        for b in self.bodies:
            if b.held:
                moving = True
                continue
            if abs(b.vx) + abs(b.vy) + abs(b.vspin) < 2:
                b.vx = b.vy = b.vspin = 0.0
                continue
            moving = True
            b.x += b.vx * dt
            b.y += b.vy * dt
            b.spin += b.vspin * dt
            b.vx, b.vy, b.vspin = b.vx * keep, b.vy * keep, b.vspin * keep
            rx, ry = b.w / 2, b.h / 2
            if b.x - rx < -hw:
                b.x, b.vx = -hw + rx, abs(b.vx) * BOUNCE
            elif b.x + rx > hw:
                b.x, b.vx = hw - rx, -abs(b.vx) * BOUNCE
            if b.y - ry < -hh:
                b.y, b.vy = -hh + ry, abs(b.vy) * BOUNCE
            elif b.y + ry > hh:
                b.y, b.vy = hh - ry, -abs(b.vy) * BOUNCE
            self.collide(b)
        return moving

    def collide(self, b):
        """b (moving) against every other card: overlapping boxes pushed apart along the shallower axis, b's speed
        along it shared with the other (a loose, inelastic knock)."""
        for o in self.bodies:
            if o is b or o.held:
                continue
            dx, dy = o.x - b.x, o.y - b.y
            px = (b.w + o.w) / 2 - abs(dx)
            py = (b.h + o.h) / 2 - abs(dy)
            if px <= 0 or py <= 0:
                continue
            if px < py:
                s = 1 if dx >= 0 else -1
                b.x -= s * px / 2
                o.x += s * px / 2
                if (b.vx - o.vx) * s > 0:
                    share = (b.vx - o.vx) * 0.5
                    b.vx -= share
                    o.vx += share
            else:
                s = 1 if dy >= 0 else -1
                b.y -= s * py / 2
                o.y += s * py / 2
                if (b.vy - o.vy) * s > 0:
                    share = (b.vy - o.vy) * 0.5
                    b.vy -= share
                    o.vy += share
            o.vspin += (random.random() - 0.5) * 20


class Stage3D(Gtk.Widget):
    """Cards (real widgets) in a 3D room, each drawn and clicked through its own 3D transform."""

    def __init__(self, seed=1, cached=False):
        super().__init__()
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.camera, self.physics, self.rng = Camera(), Physics(), random.Random(seed)
        self.bodies = {}  # (wrapper → Body)
        # cached: each card's picture kept as a texture, drawn again only when the card redraws (its WidgetPaintable's
        # invalidate-contents): GSK otherwise renders every card through an offscreen every frame (a perspective
        # transform isn't one it can draw text or borders under directly)
        self.cached, self.renders = cached, 0
        self.last, self.grab, self.running, self.now = None, None, True, 0.0
        self.frames = []  # (each frame's (start, physics+layout ms)), for measuring
        drag = Gtk.GestureDrag()
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)  # (sees the press first; claims only on a drag)
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", self._end)
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", lambda _c, x, y: self.point_at(x, y))
        self.add_controller(motion)
        self.pointer = None  # (stage coords: where each card's input transform is fitted)
        self.add_tick_callback(self._tick)

    # ---- cards
    def add(self, card):
        """card (any widget) put in the room, in a frame of its own (the card's face), at a free-ish place."""
        face = Gtk.Box()
        face.add_css_class("card3d")
        face.append(card)
        face.set_parent(self)
        b = Body(face, None, None, self.rng.uniform(-8, 8))
        self.physics.raise_(b)
        self.bodies[face] = b
        if self.cached:
            b.paintable, b.texture = Gtk.WidgetPaintable.new(face), None
            b.paintable.connect("invalidate-contents", lambda *_: setattr(b, "texture", None))
        self.physics.bodies.append(b)
        self.queue_allocate()
        return face

    def body_of(self, widget):
        while widget is not None and widget.get_parent() is not self:
            widget = widget.get_parent()
        return self.bodies.get(widget)

    def to_top(self, b):
        self.physics.raise_(b)
        b.child.insert_before(self, None)  # (last child: drawn last, picked first)

    # ---- layout: each card at its natural size (CARD_W at most), placed by its transform
    def do_measure(self, orientation, for_size):
        return 200, 600, -1, -1

    def do_size_allocate(self, w, h, baseline):
        self.physics.room_w, self.physics.room_h = w * 1.0, h * 1.15
        self.physics.place(self.rng)
        room = self.camera.room(w, h)
        for face, b in self.bodies.items():
            _m, nat, _a, _b = face.measure(Gtk.Orientation.HORIZONTAL, -1)
            b.w = max(min(nat, CARD_W), face.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
            b.h = face.measure(Gtk.Orientation.VERTICAL, b.w)[1]
            b.drawn = self.camera.card(room, b)
            b.fitted = fitted(b.drawn, b.w, b.h, self.pointer)
            face.allocate(b.w, b.h, -1, b.fitted)

    def point_at(self, x, y):
        self.pointer = (x, y)
        self.queue_allocate()

    def do_snapshot(self, s):
        w, h = self.get_width(), self.get_height()
        s.save()
        s.transform(self.camera.room(w, h))
        rw, rh = self.physics.room_w, self.physics.room_h
        floor = Graphene.Rect().init(-rw / 2, -rh / 2, rw, rh)
        s.append_color(Gdk.RGBA(red=0.5, green=0.55, blue=0.65, alpha=0.10), floor)
        line = Gdk.RGBA(red=0.5, green=0.55, blue=0.65, alpha=0.25)
        step = 80
        x = -rw / 2
        while x <= rw / 2:
            s.append_color(line, Graphene.Rect().init(x, -rh / 2, 1, rh))
            x += step
        y = -rh / 2
        while y <= rh / 2:
            s.append_color(line, Graphene.Rect().init(-rw / 2, y, rw, 1))
            y += step
        s.restore()
        child = self.get_first_child()
        while child is not None:
            b = self.bodies[child]
            s.save()
            s.transform(b.drawn)  # (drawn in true perspective: the input transform it's allocated with undone)
            if self.cached:
                tex = self.picture(b)
                if tex is not None:
                    s.append_texture(tex, Graphene.Rect().init(-SHADOW, -SHADOW, b.w + 2 * SHADOW, b.h + 2 * SHADOW))
            else:
                s.transform(b.fitted.invert())
                self.snapshot_child(child, s)
            s.restore()
            child = child.get_next_sibling()

    def picture(self, b):
        """b's card as a texture (2x), made again only after it redrew."""
        if b.texture is None or b.texture_size != (b.w, b.h):
            sn = Gtk.Snapshot()
            sn.scale(2, 2)
            sn.transform(b.fitted.invert())  # (the card's own node, at its origin: what snapshot_child would draw)
            self.snapshot_child(b.child, sn)
            node = sn.to_node()
            native = self.get_native()
            if node is None or native is None:
                return None
            m = SHADOW * 2  # (room round it for its shadow)
            b.texture = native.get_renderer().render_texture(node, Graphene.Rect().init(-m, -m, b.w * 2 + 2 * m,
                                                                                         b.h * 2 + 2 * m))
            b.texture_size = (b.w, b.h)
            self.renders += 1
        return b.texture

    # ---- time
    def _tick(self, _widget, clock):
        now = clock.get_frame_time() / 1e6
        dt, self.last = (0.0 if self.last is None else min(now - self.last, 0.05)), now
        if self.running:
            self.advance(dt)
        return True

    def advance(self, dt):
        t0 = time.perf_counter()
        self.now += dt
        self.camera.step(dt)
        self.physics.step(dt)
        self.queue_allocate()
        self.frames.append((t0, (time.perf_counter() - t0) * 1000))

    # ---- grab and throw
    def _begin(self, gesture, x, y):
        picked = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        b = self.body_of(picked)
        if b is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        ok, local = self.compute_point(b.child, Graphene.Point().init(x, y))
        self.grab = (b, x, y, local.x, local.y, False)

    def _update(self, gesture, ox, oy):
        if self.grab is None:
            return
        b, sx, sy, gx, gy, claimed = self.grab
        self.pointer = (sx + ox, sy + oy)
        if not claimed:
            if math.hypot(ox, oy) < CLAIM:
                return
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)  # (now a grab: the card's own buttons let go)
            b.held, b.vx, b.vy = True, 0.0, 0.0
            b.child.add_css_class("held")
            self.to_top(b)
            self.grab = (b, sx, sy, gx, gy, True)
        self.move_held(sx + ox, sy + oy)

    def move_held(self, px, py):
        """The held card moved so the point it was grabbed by is under (px, py) (stage coords): that point, seen through
        the card's transform, mapped back onto its face and turned into the room's plane."""
        b, _sx, _sy, gx, gy, _c = self.grab
        self.do_size_allocate(self.get_width(), self.get_height(), -1)  # (its transform as drawn now)
        ok, local = self.compute_point(b.child, Graphene.Point().init(px, py))
        if not ok:
            return
        lx, ly = local.x - gx, local.y - gy
        r = math.radians(b.spin)
        b.x += lx * math.cos(r) - ly * math.sin(r)
        b.y += lx * math.sin(r) + ly * math.cos(r)
        now = self.now  # (the stage's clock: frames' time, so a recording stepped by hand throws as one live does)
        b.samples = [s for s in b.samples if now - s[0] < 0.1] + [(now, b.x, b.y)]
        self.queue_allocate()

    def _end(self, gesture, ox, oy):
        if self.grab is None:
            return
        b, *_rest, claimed = self.grab
        self.grab = None
        if not claimed:
            return
        self.release(b)

    def release(self, b):
        b.held = False
        b.child.remove_css_class("held")
        if len(b.samples) >= 2:
            (t0, x0, y0), (t1, x1, y1) = b.samples[0], b.samples[-1]
            if t1 > t0:
                b.vx, b.vy = (x1 - x0) / (t1 - t0), (y1 - y0) / (t1 - t0)
                b.vspin = max(-120, min(120, b.vx * 0.05))
        b.samples = []


def project(m, x, y):
    """(x, y) on a card's face → stage coords, through its full 3D matrix (with the perspective divide)."""
    v = m.transform_vec4(Graphene.Vec4().init(x, y, 0.0, 1.0))
    return v.get_x() / v.get_w(), v.get_y() / v.get_w()


def unproject(m, inv, x, y):
    """Stage (x, y) → the point on the card's face under it: the pointer's ray met with the face's plane."""
    a, b = _unproject(inv, x, y, -1.0), _unproject(inv, x, y, 1.0)
    if a is None or b is None or abs(b[2] - a[2]) < 1e-9:
        return None
    t = -a[2] / (b[2] - a[2])
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def fitted(drawn, w, h, pointer):
    """The 2D (affine) transform a card is allocated with, for GTK's input: equal to its 3D one (drawn) at the face point
    under the pointer, to first order. GTK 4's picking and event coordinates through a perspective transform skip the
    perspective divide (a click lands up to a quarter of the card away); through an affine one they're exact. Drawing
    uses drawn (do_snapshot undoes this one), so what's seen is true perspective."""
    m = drawn.to_matrix()
    lx, ly = w / 2, h / 2
    if pointer is not None:
        ok, inv = m.inverse()
        hit = unproject(m, inv, *pointer) if ok else None
        if hit is not None and -w <= hit[0] <= 2 * w and -h <= hit[1] <= 2 * h:
            lx, ly = hit
    px, py = project(m, lx, ly)
    ax, ay = project(m, lx + 1, ly)
    bx, by = project(m, lx, ly + 1)
    j11, j21, j12, j22 = ax - px, ay - py, bx - px, by - py  # (the Jacobian there: columns d/dx, d/dy)
    theta = math.atan2(j21, j11)
    a = math.hypot(j11, j21)
    c, s_ = math.cos(theta), math.sin(theta)
    b = c * j12 + s_ * j22
    d = -s_ * j12 + c * j22
    t = Gsk.Transform().translate(Graphene.Point().init(px, py)).rotate(math.degrees(theta))
    t = t.skew(math.degrees(math.atan2(b, d)), 0).scale(a, d)
    return t.translate(Graphene.Point().init(-lx, -ly))


def _unproject(inv, x, y, z):
    v = inv.transform_vec4(Graphene.Vec4().init(x, y, z, 1.0))
    if abs(v.get_w()) < 1e-12:
        return None
    return v.get_x() / v.get_w(), v.get_y() / v.get_w(), v.get_z() / v.get_w()


def load_css():
    display = Gdk.Display.get_default()
    if display is not None and not getattr(display, "cards3d_css", None):
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        display.cards3d_css = css


GLib  # (kept: rigs import it from here)


# ---- (b) the cards as textures on quads in a Gtk.GLArea: OpenGL through libepoxy (GTK's own GL loader), by ctypes

class _GL:
    """The few GL calls the quads need, from libepoxy's dispatch pointers."""

    SIGS = {
        "glCreateShader": ("u", "u"), "glShaderSource": (None, "u", "i", "pp", "p"), "glCompileShader": (None, "u"),
        "glCreateProgram": ("u",), "glAttachShader": (None, "u", "u"), "glLinkProgram": (None, "u"),
        "glUseProgram": (None, "u"), "glGetUniformLocation": ("i", "u", "c"), "glUniformMatrix4fv": (None, "i", "i", "u8", "f16"),
        "glUniform2f": (None, "i", "f", "f"), "glUniform4f": (None, "i", "f", "f", "f", "f"), "glUniform1i": (None, "i", "i"),
        "glGenVertexArrays": (None, "i", "pu"), "glBindVertexArray": (None, "u"), "glGenBuffers": (None, "i", "pu"),
        "glBindBuffer": (None, "u", "u"), "glBufferData": (None, "u", "l", "f8", "u"),
        "glEnableVertexAttribArray": (None, "u"), "glVertexAttribPointer": (None, "u", "i", "u", "u8", "i", "p"),
        "glGenTextures": (None, "i", "pu"), "glBindTexture": (None, "u", "u"), "glDeleteTextures": (None, "i", "pu"),
        "glTexImage2D": (None, "u", "i", "i", "i", "i", "i", "u", "u", "p"), "glTexParameteri": (None, "u", "u", "i"),
        "glActiveTexture": (None, "u"), "glDrawArrays": (None, "u", "i", "i"), "glClearColor": (None, "f", "f", "f", "f"),
        "glClear": (None, "u"), "glEnable": (None, "u"), "glBlendFunc": (None, "u", "u"), "glViewport": (None, "i", "i", "i", "i"),
        "glGetShaderiv": (None, "u", "u", "pi"), "glGetShaderInfoLog": (None, "u", "i", "p", "p"), "glFinish": (None,),
        "glBindAttribLocation": (None, "u", "u", "c"), "glPixelStorei": (None, "u", "i"),
    }

    def __init__(self):
        import ctypes as C
        lib = C.CDLL("libepoxy.0.dylib") if not __import__("os").path.exists("/opt/homebrew/lib/libepoxy.0.dylib") \
            else C.CDLL("/opt/homebrew/lib/libepoxy.0.dylib")
        kinds = {"u": C.c_uint, "i": C.c_int, "f": C.c_float, "l": C.c_ssize_t, "p": C.c_void_p, "c": C.c_char_p,
                 "u8": C.c_ubyte, "pp": C.POINTER(C.c_char_p), "pu": C.POINTER(C.c_uint), "pi": C.POINTER(C.c_int),
                 "f16": C.POINTER(C.c_float), "f8": C.POINTER(C.c_float), None: None}
        for name, (ret, *args) in self.SIGS.items():
            ptr = C.c_void_p.in_dll(lib, "epoxy_" + name)
            proto = C.CFUNCTYPE(kinds[ret], *[kinds[a] for a in args])
            setattr(self, name[2:], (lambda ptr, proto: lambda *a: proto(ptr.value)(*a))(ptr, proto))
        self.C = C


VERT = b"""#version 150
in vec2 pos;
uniform mat4 mvp; uniform vec2 size; uniform vec2 view;
out vec2 uv;
void main() {
    uv = pos;
    vec4 p = mvp * vec4(pos * size, 0.0, 1.0);
    gl_Position = vec4(p.x * 2.0 / view.x - p.w, p.w - p.y * 2.0 / view.y, 0.0, p.w);
}
"""
FRAG = b"""#version 150
in vec2 uv; uniform sampler2D tex; uniform vec4 tint; out vec4 color;
void main() { color = texture(tex, uv) * tint; }
"""
GL_TEXTURE_2D, GL_RGBA, GL_UNSIGNED_BYTE, GL_TRIANGLE_STRIP = 0x0DE1, 0x1908, 0x1401, 0x0005
GL_ARRAY_BUFFER, GL_STATIC_DRAW, GL_FLOAT, GL_BLEND, GL_ONE, GL_ONE_MINUS_SRC_ALPHA = 0x8892, 0x88E4, 0x1406, 0x0BE2, 1, 0x0303
GL_COLOR_BUFFER_BIT, GL_LINEAR, GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER = 0x4000, 0x2601, 0x2801, 0x2800
GL_VERTEX_SHADER, GL_FRAGMENT_SHADER, GL_UNPACK_ROW_LENGTH = 0x8B31, 0x8B30, 0x0CF2


class GLCards(Gtk.Overlay):
    """(b): the cards (real widgets, laid out under the GL view where they're covered) each rendered to a GdkTexture,
    again whenever it redraws (its Gtk.WidgetPaintable's invalidate-contents), uploaded to a GL texture and drawn on
    a quad by a Gtk.GLArea with the same camera and physics. Picking is our own (the pointer's ray met with each quad,
    top first); a click is found, not delivered: the card's own buttons don't get it."""

    SCALE = 2

    def __init__(self, renderer_for, seed=1):
        super().__init__()
        self.holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
        clamp = Adw.Clamp(maximum_size=CARD_W, tightening_threshold=CARD_W, halign=Gtk.Align.START, child=self.holder)
        self.set_child(Gtk.ScrolledWindow(child=clamp, vscrollbar_policy=Gtk.PolicyType.EXTERNAL))
        self.area = Gtk.GLArea(hexpand=True, vexpand=True)
        self.area.set_required_version(3, 2)
        self.area.connect("realize", self._realize)
        self.area.connect("render", self._render)
        self.add_overlay(self.area)
        self.camera, self.physics, self.rng = Camera(), Physics(), random.Random(seed)
        self.cards, self.renderer_for = [], renderer_for  # ([face, Body, paintable, gl id or None, dirty])
        self.uploads, self.upload_ms, self.frames, self.now, self.bytes = 0, 0.0, [], 0.0, 0
        self.gl, self.grab = None, None
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", lambda *_: self._end())
        self.area.add_controller(drag)
        self.last, self.running = None, True
        self.add_tick_callback(self._tick)

    def _tick(self, _widget, clock):
        now = clock.get_frame_time() / 1e6
        dt, self.last = (0.0 if self.last is None else min(now - self.last, 0.05)), now
        if self.running:
            self.advance(dt)
        return True

    def _begin(self, gesture, x, y):
        face, lx, ly = self.pick_card(x, y)
        if face is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        b = next(c[1] for c in self.cards if c[0] is face)
        self.physics.raise_(b)
        b.held, b.vx, b.vy, b.samples = True, 0.0, 0.0, []
        self.grab = (b, x, y, lx, ly)

    def _update(self, gesture, ox, oy):
        if self.grab is None:
            return
        b, sx, sy, gx, gy = self.grab
        room = self.camera.room(self.area.get_width(), self.area.get_height())
        m = self.camera.card(room, b).to_matrix()
        ok, inv = m.inverse()
        hit = unproject(m, inv, sx + ox, sy + oy) if ok else None
        if hit is None:
            return
        lx, ly = hit[0] - gx, hit[1] - gy
        r = math.radians(b.spin)
        b.x += lx * math.cos(r) - ly * math.sin(r)
        b.y += lx * math.sin(r) + ly * math.cos(r)
        b.samples = [s for s in b.samples if self.now - s[0] < 0.1] + [(self.now, b.x, b.y)]
        self.area.queue_render()

    def _end(self):
        if self.grab is None:
            return
        b = self.grab[0]
        self.grab = None
        b.held = False
        if len(b.samples) >= 2 and b.samples[-1][0] > b.samples[0][0]:
            (t0, x0, y0), (t1, x1, y1) = b.samples[0], b.samples[-1]
            b.vx, b.vy = (x1 - x0) / (t1 - t0), (y1 - y0) / (t1 - t0)

    def add(self, widget):
        face = Gtk.Box()
        face.add_css_class("card3d")
        face.append(widget)
        self.holder.append(face)
        b = Body(face, None, None, self.rng.uniform(-8, 8))
        self.physics.raise_(b)
        self.physics.bodies.append(b)
        p = Gtk.WidgetPaintable.new(face)
        c = [face, b, p, None, True]
        p.connect("invalidate-contents", lambda *_: c.__setitem__(4, True) or self.area.queue_render())
        self.cards.append(c)
        return face

    def advance(self, dt):
        t0 = time.perf_counter()
        self.now += dt
        self.camera.step(dt)
        self.physics.step(dt)
        self.area.queue_render()
        self.frames.append((t0, (time.perf_counter() - t0) * 1000))

    def _realize(self, area):
        area.make_current()
        if area.get_error() is not None:
            print("GLArea:", area.get_error())
            return
        gl = self.gl = _GL()
        C = gl.C
        prog = gl.CreateProgram()
        for kind, src in ((GL_VERTEX_SHADER, VERT), (GL_FRAGMENT_SHADER, FRAG)):
            sh = gl.CreateShader(kind)
            arr = (C.c_char_p * 1)(src)
            gl.ShaderSource(sh, 1, arr, None)
            gl.CompileShader(sh)
            ok = C.c_int(0)
            gl.GetShaderiv(sh, 0x8B81, C.byref(ok))
            if not ok.value:
                log = C.create_string_buffer(2000)
                gl.GetShaderInfoLog(sh, 2000, None, log)
                print("shader:", log.value.decode())
            gl.AttachShader(prog, sh)
        gl.BindAttribLocation(prog, 0, b"pos")
        gl.LinkProgram(prog)
        self.prog = prog
        self.loc = {n: gl.GetUniformLocation(prog, n.encode()) for n in ("mvp", "size", "view", "tex", "tint")}
        vao, vbo = C.c_uint(), C.c_uint()
        gl.GenVertexArrays(1, C.byref(vao))
        gl.BindVertexArray(vao.value)
        gl.GenBuffers(1, C.byref(vbo))
        gl.BindBuffer(GL_ARRAY_BUFFER, vbo.value)
        quad = (C.c_float * 8)(0, 0, 1, 0, 0, 1, 1, 1)
        gl.BufferData(GL_ARRAY_BUFFER, C.sizeof(quad), quad, GL_STATIC_DRAW)
        gl.EnableVertexAttribArray(0)
        gl.VertexAttribPointer(0, 2, GL_FLOAT, 0, 0, None)
        self.white = self._texture(b"\xff\xff\xff\xff", 1, 1)

    def _texture(self, data, w, h, tid=None):
        gl, C = self.gl, self.gl.C
        if tid is None:
            t = C.c_uint()
            gl.GenTextures(1, C.byref(t))
            tid = t.value
        gl.BindTexture(GL_TEXTURE_2D, tid)
        gl.TexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        gl.TexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        buf = C.create_string_buffer(bytes(data), len(data))
        gl.TexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, buf)
        return tid

    def upload(self, c):
        """A card drawn to a GdkTexture (GSK's GL renderer), read back and given to GL as its quad's texture."""
        face, b, p = c[0], c[1], c[2]
        t0 = time.perf_counter()
        b.w, b.h = max(face.get_width(), 1), max(face.get_height(), 1)
        s = Gtk.Snapshot()
        s.scale(self.SCALE, self.SCALE)
        p.snapshot(s, b.w, b.h)
        node = s.to_node()
        if node is None:
            return
        tex = self.renderer_for().render_texture(node, Graphene.Rect().init(0, 0, b.w * self.SCALE, b.h * self.SCALE))
        d = Gdk.TextureDownloader.new(tex)
        d.set_format(Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED)
        data, stride = d.download_bytes()
        tw, th = tex.get_width(), tex.get_height()
        raw = data.get_data()
        if stride != tw * 4:
            raw = b"".join(raw[r * stride:r * stride + tw * 4] for r in range(th))
        self.area.make_current()
        if c[3] is None:
            self.bytes += tw * th * 4
        c[3] = self._texture(raw, tw, th, c[3])
        c[4] = False
        self.uploads += 1
        self.upload_ms += (time.perf_counter() - t0) * 1000

    def _render(self, area, context):
        if self.gl is None:
            return True
        gl = self.gl
        w, h = self.area.get_width(), self.area.get_height()
        self.physics.room_w, self.physics.room_h = w * 1.0, h * 1.15
        self.physics.place(self.rng)
        for c in self.cards:
            if c[4] or c[3] is None:
                self.upload(c)
        area.make_current()
        sc = area.get_scale_factor()
        gl.Viewport(0, 0, w * sc, h * sc)
        gl.ClearColor(0.98, 0.98, 0.98, 1)
        gl.Clear(GL_COLOR_BUFFER_BIT)
        gl.Enable(GL_BLEND)
        gl.BlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        gl.UseProgram(self.prog)
        gl.Uniform2f(self.loc["view"], w, h)
        gl.Uniform1i(self.loc["tex"], 0)
        gl.ActiveTexture(0x84C0)
        room = self.camera.room(w, h)
        rw, rh = self.physics.room_w, self.physics.room_h
        gl.BindTexture(GL_TEXTURE_2D, self.white)
        self._quad(room.translate(Graphene.Point().init(-rw / 2, -rh / 2)), rw, rh, (0.05, 0.055, 0.065, 0.10))
        for x in range(0, int(rw) + 1, 80):
            self._quad(room.translate(Graphene.Point().init(-rw / 2 + x, -rh / 2)), 1, rh, (0.125, 0.14, 0.16, 0.25))
        for y in range(0, int(rh) + 1, 80):
            self._quad(room.translate(Graphene.Point().init(-rw / 2, -rh / 2 + y)), rw, 1, (0.125, 0.14, 0.16, 0.25))
        for c in sorted(self.cards, key=lambda c: c[1].z):
            if c[3] is None:
                continue
            b = c[1]
            gl.BindTexture(GL_TEXTURE_2D, c[3])
            self._quad(self.camera.card(room, b), b.w, b.h, (1, 1, 1, 1))
        return True

    def _quad(self, t, w, h, tint):
        gl = self.gl
        m = t.to_matrix().to_float()
        gl.UniformMatrix4fv(self.loc["mvp"], 1, 0, (gl.C.c_float * 16)(*m))
        gl.Uniform2f(self.loc["size"], w, h)
        gl.Uniform4f(self.loc["tint"], *tint)
        gl.DrawArrays(GL_TRIANGLE_STRIP, 0, 4)

    def pick_card(self, x, y):
        """The top card under (x, y) (the area's coords) and the point on its face, or (None, 0, 0)."""
        room = self.camera.room(self.area.get_width(), self.area.get_height())
        for c in sorted(self.cards, key=lambda c: -c[1].z):
            b = c[1]
            m = self.camera.card(room, b).to_matrix()
            ok, inv = m.inverse()
            hit = unproject(m, inv, x, y) if ok else None
            if hit is not None and 0 <= hit[0] <= b.w and 0 <= hit[1] <= b.h:
                return c[0], hit[0], hit[1]
        return None, 0, 0
