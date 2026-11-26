"""scenehost — a scene theme drawn on the desktop (a prototype): SceneView, a widget that draws a theme's scene.json
(baudelib.themepacks) in GL with lights and bloom (scenegl) and puts Baude's things in it as live widgets: each panel
is a real widget (a chat, a host, a Meta card, a window's content), allocated, clicked and typed into as in a window,
and drawn in the scene from a picture of it kept until it redraws.

How a panel stays live (cards3d's container, #454):
- Each panel's widget is SceneView's child, allocated at its size with a 2D transform fitted to its true projection at
  the point under the pointer (cards3d.fitted's maths): GTK 4's picking through a perspective transform skips the
  perspective divide; through an affine one it's exact, so a click lands on the button drawn under it.
- GTK never draws the panels itself: GL does, each from its picture (the widget rendered by the window's GSK renderer,
  read back and given to GL), made again only after the widget redraws (its WidgetPaintable's invalidate-contents).
- Depth is honoured for input as for drawing: on each pointer move the pointer's ray is met with every panel and every
  pickable mesh; only the nearest panel takes events (can_target), so a panel behind a wall can't be clicked.

What the scene is given is SceneObject.json's things (World): chats with their state, hosts, Meta cards, windows. A
node's panel shows one; slots make a panel (and their `with` nodes) for each that fits. A theme's desktop code
(desktop/scene.py) is run only once trusted (themepacks.trusted), and gets a SceneAPI, nothing else of the app's."""
import importlib.util
import math
import os
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

import scenegl  # noqa: E402
from baudelib import themepacks  # noqa: E402

CSS = """
.scene-panel { background-color: @view_bg_color; color: @view_fg_color; border-radius: 10px; }
"""
PICTURE_SCALE = 2  # (a panel's picture, px per widget px)
CLAIM = 6  # (px a press on the scene moves before it turns the view)
STATES = {"working": "working", "waiting": "waiting", "idle": "done", "stopped": "inactive"}  # (wire → the dot's)


def load_css():
    display = Gdk.Display.get_default()
    if display is not None and not getattr(display, "scene_css", None):
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        display.scene_css = css


# ---- what a scene is given

class World:
    """Baude's things as a scene has them (SceneObject.json's), and who's told as they change."""

    def __init__(self, objects=()):
        self.objects, self.listeners = [dict(o) for o in objects], []

    def set(self, objects):
        self.objects = [dict(o) for o in objects]
        self.changed()

    def update(self, oid, **fields):
        for o in self.objects:
            if o["id"] == oid:
                o.update(fields)
        self.changed()

    def changed(self):
        for fn in list(self.listeners):
            fn()

    def get(self, oid):
        return next((o for o in self.objects if o["id"] == oid), None)

    def query(self, slots, around=None):
        """The things slots are for, in their order. around: the thing the slots are in (a host's, for slots in a host's
        slot), whose fields a where's $field names (a chat's host $id)."""
        where = {k: ((around or {}).get(v[1:]) if isinstance(v, str) and v.startswith("$") else v)
                 for k, v in (slots.get("where") or {}).items()}
        out = [o for o in self.objects if o["kind"] == slots["of"] and
               all(str(o.get(k)).lower() == str(v).lower() for k, v in where.items())]
        if slots.get("sort") == "name":
            out.sort(key=lambda o: (o.get("name") or "").lower())
        else:
            out.sort(key=lambda o: -(o.get("used") or 0))
        return out[:slots["limit"]] if slots.get("limit") else out

    @staticmethod
    def from_info(info):
        """The mesh's hosts as a host's info has them (schema/types/Info.json): itself and the members it reaches, up;
        those reached lately and not now (its mesh's gone), down."""
        mesh = info.get("mesh") or {}
        out = [{"kind": "host", "id": info["id"], "name": info.get("name") or info["id"], "state": "up"}]
        seen = {info["id"]}
        for m, state in [(m, "up") for m in mesh.get("members") or ()] + [(m, "down") for m in mesh.get("gone") or ()]:
            if m["id"] not in seen:
                seen.add(m["id"])
                out.append({"kind": "host", "id": m["id"], "name": m.get("name") or m["id"], "state": state})
        return out

    @staticmethod
    def from_sessions(host, sessions, bots=None):
        """A host's sessions (schema/types/Session.json, as its sessions answer has them) as the scene's chats, a Bot's
        named as it is (its session's bot, BotListing); bots: a name by session id over that."""
        bots = bots or {}
        return [{"kind": "chat", "id": f"{host}/{s['id']}", "host": host, "state": s.get("state", "stopped"),
                 "name": bots.get(s["id"]) or (s.get("bot") or {}).get("name") or s.get("title") or "",
                 "bot": s["id"] in bots or bool(s.get("bot")),
                 "used": s.get("said") or s.get("updated") or 0} for s in sessions if not s.get("archived")]


# ---- the widgets a panel shows (the app gives its own: providers)

class ChatPanel(Gtk.Box):
    """The sample's chat panel, made of Baude's own parts: the chat's dot and title (the sidebar's StatusDot), its
    messages (a user bubble, a reply, a tool card: toolcards.ToolCard) and a box to write in, live (Send puts the text
    in as a message). The app gives a chat's own page in its place."""

    def __init__(self, obj, transcript=()):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        from sessionsidebar import StatusDot
        from widgets import WrapLabel
        self.obj = obj
        for side in ("top", "bottom", "start", "end"):
            getattr(self, f"set_margin_{side}")(12)
        head = Gtk.Box(spacing=6)
        self.dot = StatusDot(STATES.get(obj.get("state"), "inactive"))
        title = Gtk.Label(label=obj.get("name") or "", xalign=0, hexpand=True, ellipsize=3)
        title.add_css_class("heading")
        head.append(self.dot)
        head.append(title)
        self.append(head)
        self.messages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, vexpand=True)
        self.append(self.messages)
        for kind, what in transcript:
            if kind == "user":
                self.said(what)
            elif kind == "reply":
                lab = WrapLabel(label=what, xalign=0)
                lab.set_wrap(True)
                self.messages.append(lab)
            elif kind == "tool":
                self.messages.append(what)
        box = Gtk.Box(spacing=6)
        box.add_css_class("composer-box")
        self.entry = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, hexpand=True, top_margin=8, bottom_margin=8,
                                  left_margin=10, right_margin=10)
        self.send = Gtk.Button(icon_name="go-up-symbolic", valign=Gtk.Align.END, margin_end=4, margin_bottom=4)
        self.send.add_css_class("circular")
        self.send.add_css_class("suggested-action")
        self.send.connect("clicked", lambda *_: self.sent())
        box.append(self.entry)
        box.append(self.send)
        self.append(box)

    def said(self, text):
        bubble = Gtk.Label(label=text, wrap=True, xalign=0, halign=Gtk.Align.END)
        bubble.add_css_class("user-bubble")
        self.messages.append(bubble)

    def sent(self):
        buf = self.entry.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        if text:
            self.said(text)
            buf.set_text("")

    def show_state(self, state):
        self.dot.set_state(STATES.get(state, "inactive"))


class HostPanel(Gtk.Box):
    """A host: its name and whether it's up, and its chats' rows (dot and name)."""

    def __init__(self, obj, world):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        from sessionsidebar import StatusDot
        for side in ("top", "bottom", "start", "end"):
            getattr(self, f"set_margin_{side}")(12)
        name = Gtk.Label(label=obj.get("name") or obj["id"], xalign=0)
        name.add_css_class("title-4")
        self.append(name)
        for c in world.query({"of": "chat", "where": {"host": obj["id"]}, "limit": 8}):
            row = Gtk.Box(spacing=6)
            row.append(StatusDot(STATES.get(c.get("state"), "inactive")))
            row.append(Gtk.Label(label=c.get("name") or "", xalign=0, ellipsize=3))
            self.append(row)


class WindowPanel(Gtk.Box):
    """A window's content, taken into the scene while it's shown there and given back as the panel goes."""

    def __init__(self, window):
        super().__init__()
        self.window, self.content = window, window.get_child()
        if self.content is not None:
            window.set_child(None)
            self.append(self.content)

    def give_back(self):
        if self.content is not None and self.content.get_parent() is self:
            self.remove(self.content)
            self.window.set_child(self.content)


def default_providers(world, windows=None, transcripts=None):
    """show → (thing → widget): the sample's. An app passes its own (its chat pages, its Meta cards)."""
    windows, transcripts = windows or {}, transcripts or {}
    return {"chat": lambda o: ChatPanel(o, transcripts.get(o["id"], ())),
            "host": lambda o: HostPanel(o, world),
            "window": lambda o: WindowPanel(windows[o["id"]])}


# ---- the scene's nodes

class Node:
    """A node as the scene has it now: scene.json's, or one a slot or the theme's code made. Its fields may be changed
    by the theme's code (SceneAPI) between frames."""

    def __init__(self, spec, obj=None, parent=None):
        self.id = spec["id"]
        self.parent = parent if parent is not None else spec.get("parent")
        self.position = list(spec.get("position") or (0, 0, 0))
        self.rotation = list(spec.get("rotation") or (0, 0, 0))
        self.scale = list(spec.get("scale") or (1, 1, 1))
        self.mesh, self.material = spec.get("mesh"), spec.get("material")
        self.states, self.panel = dict(spec.get("states") or {}), spec.get("panel")
        self.slots, self.hidden, self.pick = spec.get("slots"), bool(spec.get("hidden")), spec.get("pick", "open")
        self.obj, self.face, self.widget = obj, None, None
        self.tex, self.dirty, self.paintable, self.texsize = None, True, None, None
        self.alloc = self.alloc_size = None  # (the 2D transform its widget's allocated with, and its px)
        self.placed = None  # (its world matrix, by where it and its parents are, and what's worked out from it)
        self.made_by = None  # (the slots node it was made for, if any)

    def local(self):
        sx, sy, sz = self.scale
        m = Graphene.Matrix().init_scale(sx, sy, sz)
        for angle, axis in zip(self.rotation, (Graphene.Vec3.x_axis(), Graphene.Vec3.y_axis(), Graphene.Vec3.z_axis())):
            if angle:
                m = m.multiply(Graphene.Matrix().init_rotate(angle, axis))
        return m.multiply(Graphene.Matrix().init_translate(Graphene.Point3D().init(*self.position)))


class SceneAPI:
    """What a theme's desktop code is given (its setup(scene)): the scene's nodes, lights, materials and camera to change
    between frames, the things in it, and callbacks. It's a contract, not a sandbox: code a user trusted runs as the
    app does (themepacks)."""

    def __init__(self, view):
        self._v = view

    @property
    def time(self):
        return self._v.t

    @property
    def lights(self):
        return self._v.scene.setdefault("lights", [])

    @property
    def materials(self):
        return self._v.scene.setdefault("materials", {})

    @property
    def camera(self):
        return self._v.scene["camera"]

    @property
    def world(self):
        return [dict(o) for o in self._v.world.objects]

    def node(self, nid):
        return self._v.nodes[nid]

    def nodes(self):
        return list(self._v.nodes.values())

    def add(self, spec, obj=None):
        return self._v.add_node(spec, obj)

    def remove(self, nid):
        self._v.remove_node(nid)

    def show(self, nid, shown=True):
        self._v.nodes[nid].hidden = not shown
        self._v.queue_allocate()

    def open(self, obj):
        self._v.open(obj)

    def asset(self, path):
        return self._v.asset(path)

    def on_frame(self, fn):
        self._v.frame_hooks.append(fn)

    def on_pick(self, fn):
        self._v.pick_hooks.append(fn)

    def on_world(self, fn):
        self._v.world_hooks.append(fn)


def _vec(m, x, y, z, w=1.0):
    v = m.transform_vec4(Graphene.Vec4().init(x, y, z, w))
    return v.get_x(), v.get_y(), v.get_z(), v.get_w()


def fitted(m, w, h, pointer):
    """The 2D transform a panel's widget is allocated with (cards3d.fitted, for a Graphene.Matrix m taking the panel's
    px to the view's px with the perspective divide): equal to m, to first order, at the panel's point under the
    pointer, so GTK's input through it is exact there."""
    import cards3d
    lx, ly = w / 2, h / 2
    if pointer is not None:
        ok, inv = m.inverse()
        hit = cards3d.unproject(m, inv, *pointer) if ok else None
        if hit is not None and -w <= hit[0] <= 2 * w and -h <= hit[1] <= 2 * h:
            lx, ly = hit
    px, py = cards3d.project(m, lx, ly)
    ax, ay = cards3d.project(m, lx + 1, ly)
    bx, by = cards3d.project(m, lx, ly + 1)
    j11, j21, j12, j22 = ax - px, ay - py, bx - px, by - py
    theta = math.atan2(j21, j11)
    a = math.hypot(j11, j21)
    c, s_ = math.cos(theta), math.sin(theta)
    b = c * j12 + s_ * j22
    d = -s_ * j12 + c * j22
    t = Gsk.Transform().translate(Graphene.Point().init(px, py)).rotate(math.degrees(theta))
    t = t.skew(math.degrees(math.atan2(b, d)), 0).scale(a, d)
    return t.translate(Graphene.Point().init(-lx, -ly))


class SceneView(Gtk.Widget):
    """A scene (Scene.json) with Baude's things in it: see the module's notes. world: a World; providers: show → (thing
    → widget); theme: the theme's folder (for its models, textures and code); trusted: whether its code may run;
    open: called with a thing a click opens."""

    def __init__(self, scene, world, providers, theme=None, trusted=False, open=None):
        super().__init__()
        load_css()
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.scene, self.world, self.providers, self.theme = scene, world, providers, theme
        self.opened = open or (lambda obj: None)
        self.t, self.last, self.running = 0.0, None, True
        self.nodes, self.order = {}, []
        self.frame_hooks, self.pick_hooks, self.world_hooks = [], [], []
        self.pointer, self.target, self.drag = None, None, None
        self.yaw, self.pitch, self.dolly = 0.0, 0.0, 1.0  # (the user's turn of the view, on top of its orbit)
        self.stats = {"uploads": 0, "upload_ms": 0.0, "frames": [], "render_ms": [], "layout_ms": [], "code": None}
        self.area = Gtk.GLArea(can_target=False)
        self.area.set_required_version(3, 2)
        self.area.set_has_depth_buffer(False)
        self.area.connect("realize", self._realize)
        self.area.connect("render", self._render)
        self.area.set_parent(self)
        self.gl = None
        for spec in scene["nodes"]:
            self.add_node(spec)
        world.listeners.append(self.sync)
        self.sync()
        motion = Gtk.EventControllerMotion()
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", lambda _c, x, y: self.point_at(x, y))
        motion.connect("leave", lambda _c: self.left())
        self.add_controller(motion)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", lambda _c, _dx, dy: self._zoom(dy))
        self.add_controller(scroll)
        self.add_tick_callback(self._tick)
        if theme is not None:
            self.load_code(trusted)

    # ---- nodes
    def add_node(self, spec, obj=None, parent=None):
        n = Node(spec, obj, parent)
        if n.id in self.nodes:
            raise ValueError(f"there's a node {n.id} already")
        self.nodes[n.id] = n
        self.order.append(n.id)
        if n.panel is not None and n.obj is None and n.panel.get("of"):
            n.obj = self.world.get(n.panel["of"]) if n.panel["of"] != "first" else None
        self.queue_allocate()
        return n

    def remove_node(self, nid):
        for child in [k for k, n in self.nodes.items() if n.parent == nid]:
            self.remove_node(child)
        n = self.nodes.pop(nid)
        self.order.remove(nid)
        self._drop_face(n)

    def _drop_face(self, n):
        if n.face is not None:
            if isinstance(n.widget, WindowPanel):
                n.widget.give_back()
            n.face.unparent()
            n.face = n.widget = n.paintable = None
            if n.tex and self.gl is not None:
                self.area.make_current()
                self.gl.gl.delete_texture(n.tex)
            n.tex = None

    def obj_of(self, n):
        while n is not None:
            if n.obj is not None:
                return n.obj
            n = self.nodes.get(n.parent)
        return None

    def sync(self):
        """Each slots node's panels made again for the things that fit now (a thing's panel kept, live, while it still
        fits), and each panel's thing as the world has it now."""
        at = 0
        while at < len(self.order):  # (slots made for a slot's thing, a host's chats, are met as they're made)
            sid, at = self.order[at], at + 1
            s = self.nodes.get(sid)
            if s is None or not s.slots:
                continue
            fits = self.world.query(s.slots, self.obj_of(s))
            had = {n.obj["id"]: n for n in self.nodes.values() if n.made_by == sid and n.parent == sid}
            keep = {o["id"] for o in fits}
            for oid, n in had.items():
                if oid not in keep:
                    self.remove_node(n.id)
            groups = []
            for i, o in enumerate(fits):
                g = had.get(o["id"])
                if g is None:
                    gid = f"{sid}/{len(self.order)}"
                    g = self.add_node({"id": gid}, o, parent=sid)
                    g.made_by = sid
                    if s.slots.get("panel") is not None:
                        self.add_node({"id": f"{gid}/panel", "panel": s.slots["panel"],
                                       "hidden": bool(s.slots.get("hidden"))}, None, parent=gid).made_by = sid
                    for w in s.slots.get("with") or ():
                        wn = self.add_node({**w, "id": f"{gid}/{w['id']}"}, None,
                                           parent=f"{gid}/{w['parent']}" if w.get("parent") else gid)
                        wn.made_by = sid
                g.obj = o
                groups.append(g)
            self._lay_out(s, groups)
        for n in self.nodes.values():
            if n.obj is not None:
                n.obj = self.world.get(n.obj["id"]) or n.obj
        for n in self.nodes.values():
            if n.widget is not None and hasattr(n.widget, "show_state") and self.obj_of(n) is not None:
                n.widget.show_state(self.obj_of(n).get("state"))
        for fn in self.world_hooks:
            fn(SceneAPI(self).world)
        self.queue_allocate()

    def _lay_out(self, s, groups):
        p = s.slots.get("panel") or {}
        w, h = (p.get("size") or (1.6, 1.0))
        gap, layout = float(s.slots.get("spacing", 0.2)), s.slots.get("layout", "row")
        n = len(groups)
        for i, g in enumerate(groups):
            if layout == "ring":
                a = 2 * math.pi * i / max(n, 1)
                g.position, g.rotation = [gap * math.sin(a), 0, gap * math.cos(a)], [0, math.degrees(a), 0]
            elif layout == "grid":
                cols = s.slots.get("columns") or max(1, round(math.sqrt(n)))
                r, c = divmod(i, cols)
                g.position = [(c - (cols - 1) / 2) * (w + gap), -r * (h + gap), 0]
            elif layout == "stack":
                g.position = [0, i * (h + gap), 0]
            else:
                g.position = [(i - (n - 1) / 2) * (w + gap), 0, 0]

    def world_matrix(self, n):
        """n's space → the world: its own place, then each parent's; kept till one of them moves."""
        chain, at = [], n
        while at is not None:
            chain.append((tuple(at.position), tuple(at.rotation), tuple(at.scale)))
            at = self.nodes.get(at.parent)
        key = tuple(chain)
        if n.placed is None or n.placed[0] != key:
            m = n.local()
            p = self.nodes.get(n.parent)
            while p is not None:
                m = m.multiply(p.local())
                p = self.nodes.get(p.parent)
            n.placed = (key, m, {})
        return n.placed[1]

    def drawn(self, n, kind, make):
        """Something of n's worked out from where it is (its model matrix as GL takes it, its centre): kept with it."""
        self.world_matrix(n)
        kept = n.placed[2]
        if kind not in kept:
            kept[kind] = make()
        return kept[kind]

    def shown(self, n):
        while n is not None:
            if n.hidden:
                return False
            n = self.nodes.get(n.parent)
        return True

    def material(self, n):
        mats = self.scene.get("materials") or {}
        obj, at = self.obj_of(n), n
        while at is not None:  # (a state's material, its own or a parent's; else its material, or a parent's)
            if obj is not None and obj.get("state") in at.states:
                return mats.get(at.states[obj["state"]], {})
            at = self.nodes.get(at.parent)
        at = n
        while at is not None:
            if at.material:
                return mats.get(at.material, {})
            at = self.nodes.get(at.parent)
        return {}

    # ---- panels: their widgets and pictures
    def face(self, n):
        """n's panel's widget, made as it's first shown (in a .scene-panel, at the panel's px)."""
        if n.face is None:
            obj = self.obj_of(n)
            make = self.providers.get(n.panel["show"])
            if obj is None or make is None:
                return None
            n.widget = make(obj)
            face = Gtk.Box()
            face.add_css_class("scene-panel")
            n.widget.set_hexpand(True)
            n.widget.set_vexpand(True)
            face.append(n.widget)
            face.set_parent(self)
            n.face = face
            n.paintable = Gtk.WidgetPaintable.new(face)
            n.paintable.connect("invalidate-contents", lambda *_: self._dirty(n))
            n.dirty = True
        return n.face

    def _dirty(self, n):
        n.dirty = True
        self.area.queue_render()

    def panel_px(self, n):
        return [int(x) for x in (n.panel.get("px") or (480, 360))]

    def panel_model(self, n):
        """The panel's face (its px, y down) → the world, metres."""
        pw, ph = self.panel_px(n)
        sw, sh = n.panel.get("size") or (1.6, 1.2)
        f = Graphene.Matrix().init_scale(sw / pw, -sh / ph, 1).multiply(
            Graphene.Matrix().init_translate(Graphene.Point3D().init(-sw / 2, sh / 2, 0)))
        return f.multiply(self.world_matrix(n))

    # ---- the camera
    def eye(self):
        cam = self.scene["camera"]
        tx, ty, tz = cam["target"]
        px, py, pz = cam["position"]
        dx, dy, dz = px - tx, py - ty, pz - tz
        r = math.sqrt(dx * dx + dy * dy + dz * dz) * self.dolly
        yaw = math.atan2(dx, dz)
        pitch = math.asin(max(-1, min(1, dy / (r / self.dolly or 1))))
        orbit = cam.get("orbit")
        if orbit:
            yaw += math.radians(orbit.get("yaw", 15)) * math.sin(2 * math.pi * self.t / max(orbit.get("period", 30), 1))
        yaw += math.radians(self.yaw)
        pitch = max(-1.4, min(1.4, pitch + math.radians(self.pitch)))
        return (tx + r * math.cos(pitch) * math.sin(yaw), ty + r * math.sin(pitch), tz + r * math.cos(pitch) * math.cos(yaw))

    def matrices(self, w, h):
        """view, projection, and the projection's clip → the view's px (y down)."""
        cam = self.scene["camera"]
        e = self.eye()
        view = Graphene.Matrix().init_look_at(Graphene.Vec3().init(*e), Graphene.Vec3().init(*cam["target"]),
                                              Graphene.Vec3.y_axis())
        fov = cam.get("fov", 45)  # (vertical where the view's wider than tall, else across)
        if w < h:
            fov = math.degrees(2 * math.atan(math.tan(math.radians(fov / 2)) * h / max(w, 1)))
        proj = Graphene.Matrix().init_perspective(fov, w / max(h, 1), 0.05, 200)
        px = Graphene.Matrix().init_scale(w / 2, -h / 2, 1).multiply(
            Graphene.Matrix().init_translate(Graphene.Point3D().init(w / 2, h / 2, 0)))
        return view, proj, px, e

    # ---- layout and input
    def do_measure(self, orientation, for_size):
        return 200, 800, -1, -1

    def do_size_allocate(self, w, h, baseline):
        t0 = time.perf_counter()
        self.area.allocate(w, h, -1, None)
        view, proj, px, _e = self.matrices(w, h)
        vpp = view.multiply(proj).multiply(px)
        for nid in self.order:
            n = self.nodes[nid]
            if n.panel is None:
                continue
            show = self.shown(n)
            face = self.face(n) if show else n.face
            if face is None:
                continue
            face.set_child_visible(show)
            if not show:
                continue
            pw, ph = self.panel_px(n)
            live = self.target is n
            if live or n.alloc is None or n.alloc_size != (pw, ph):
                if n.alloc_size != (pw, ph):
                    face.measure(Gtk.Orientation.HORIZONTAL, -1)
                    face.measure(Gtk.Orientation.VERTICAL, pw)
                n.alloc = fitted(self.stage_matrix(n, vpp), pw, ph, self.pointer if live else None)
                n.alloc_size = (pw, ph)
            face.allocate(pw, ph, -1, n.alloc)  # (unchanged but for the panel under the pointer: GTK skips the rest)
            face.set_can_target(live)
        self.stats["layout_ms"].append((time.perf_counter() - t0) * 1000)

    def stage_matrix(self, n, vpp=None):
        """n's panel's px → the view's px, through the camera as it is now (with the perspective divide)."""
        if vpp is None:
            view, proj, px, _e = self.matrices(self.get_width(), self.get_height())
            vpp = view.multiply(proj).multiply(px)
        return self.panel_model(n).multiply(vpp)

    def ray(self, x, y):
        """The pointer's ray in the world: its origin and direction."""
        w, h = self.get_width(), self.get_height()
        view, proj, px, _e = self.matrices(w, h)
        ok, inv = view.multiply(proj).multiply(px).inverse()
        a, b = _vec(inv, x, y, -1), _vec(inv, x, y, 1)
        a, b = [c / a[3] for c in a[:3]], [c / b[3] for c in b[:3]]
        d = [b[i] - a[i] for i in range(3)]
        ln = math.sqrt(sum(c * c for c in d)) or 1
        return a, [c / ln for c in d]

    def hit(self, x, y):
        """The nearest node under (x, y): a panel (its px point) or a mesh: (node, distance, (px, py) or None)."""
        o, d = self.ray(x, y)
        best = (None, math.inf, None)
        for n in self.nodes.values():
            if not self.shown(n) or (n.panel is None and (n.mesh is None or n.pick == "none")):
                continue
            m = self.panel_model(n) if n.panel is not None else self.world_matrix(n)
            ok, inv = m.inverse()
            if not ok:
                continue
            lo, ld = _vec(inv, *o), _vec(inv, *d, 0.0)
            if n.panel is not None:
                if abs(ld[2]) < 1e-9:
                    continue
                t = -lo[2] / ld[2]
                hx, hy = lo[0] + ld[0] * t, lo[1] + ld[1] * t
                pw, ph = self.panel_px(n)
                if t > 0 and 0 <= hx <= pw and 0 <= hy <= ph and t < best[1]:
                    best = (n, t, (hx, hy))
            else:
                lo3, hi3 = self._bounds(n)
                t0, t1 = -math.inf, math.inf
                for i in range(3):
                    if abs(ld[i]) < 1e-12:
                        if not lo3[i] <= lo[i] <= hi3[i]:
                            t0 = math.inf
                        continue
                    a, b = (lo3[i] - lo[i]) / ld[i], (hi3[i] - lo[i]) / ld[i]
                    t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
                if t0 <= t1 and t1 > 0 and max(t0, 0) < best[1]:
                    best = (n, max(t0, 0), None)
        return best

    def _bounds(self, n):
        if self.gl is not None and n.mesh in self.gl.meshes:
            return self.gl.meshes[n.mesh][2]
        return (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)

    def point_at(self, x, y):
        self.pointer = (x, y)
        self.target = self.hit(x, y)[0]
        if self.target is not None and self.target.panel is None:
            self.target = False  # (a mesh: no panel takes it)
        self.queue_allocate()

    def left(self):
        self.pointer = self.target = None
        self.queue_allocate()

    def _drag_begin(self, gesture, x, y):
        self.drag = (x, y, self.yaw, self.pitch, False)

    def _drag_update(self, gesture, ox, oy):
        if self.drag is None:
            return
        x, y, yaw, pitch, claimed = self.drag
        if not claimed and math.hypot(ox, oy) < CLAIM:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.drag = (x, y, yaw, pitch, True)
        self.yaw, self.pitch = yaw - ox * 0.25, pitch + oy * 0.2
        self.queue_allocate()

    def _drag_end(self, gesture, ox, oy):
        if self.drag is not None and not self.drag[4]:
            self.picked(*self.drag[:2])
        self.drag = None

    def _zoom(self, dy):
        self.dolly = max(0.3, min(3.0, self.dolly * (1.1 ** dy)))
        self.queue_allocate()
        return True

    def picked(self, x, y):
        """A click on the scene (not on a widget in it): the node under it does what its pick says."""
        n = self.hit(x, y)[0]
        if n is None or n.panel is not None:
            return
        for fn in self.pick_hooks:
            if fn(n, self.obj_of(n)):
                return
        if n.pick == "reveal":
            n.hidden = True
            for k in self.nodes.values():
                if k.parent == n.parent and k is not n:
                    k.hidden = False
        elif n.pick == "open" and self.obj_of(n) is not None:
            self.open(self.obj_of(n))
        self.queue_allocate()

    def open(self, obj):
        self.opened(obj)

    def asset(self, path):
        if self.theme is None:
            raise ValueError("no theme")
        themepacks.check_path(path)
        with open(os.path.join(self.theme, path), "rb") as fh:
            return fh.read()

    # ---- the theme's code
    def load_code(self, trusted):
        """The theme's desktop/scene.py run (its setup(SceneAPI)), if it has one and it's trusted as it is."""
        try:
            with open(os.path.join(self.theme, themepacks.MANIFEST), encoding="utf-8") as fh:
                import json
                code = ((json.load(fh).get("scene") or {}).get("desktop") or {})
        except (OSError, ValueError):
            return
        if not code.get("python"):
            return
        if not trusted:
            self.stats["code"] = "not trusted: the scene is drawn as scene.json has it"
            return
        path = os.path.join(self.theme, themepacks.check_path(code["python"]))
        try:
            spec = importlib.util.spec_from_file_location(f"baude_theme_{abs(hash(self.theme))}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.setup(SceneAPI(self))
            self.stats["code"] = "running"
        except Exception as e:  # noqa: BLE001 (a theme's code gone wrong: the scene's still drawn, without it)
            self.stats["code"] = f"failed: {type(e).__name__}: {e}"
            self.frame_hooks.clear()
            self.pick_hooks.clear()

    # ---- time
    def _tick(self, _w, clock):
        now = clock.get_frame_time() / 1e6
        dt, self.last = (0.0 if self.last is None else min(now - self.last, 0.05)), now
        if self.running:
            self.advance(dt)
        return True

    def advance(self, dt):
        t0 = time.perf_counter()
        self.t += dt
        for fn in list(self.frame_hooks):
            try:
                fn(self.t, dt)
            except Exception as e:  # noqa: BLE001
                self.stats["code"] = f"failed: {type(e).__name__}: {e}"
                self.frame_hooks.remove(fn)
        if self.pointer is not None:  # (what's under a still pointer changes as the view turns)
            self.point_at(*self.pointer)
        self.queue_allocate()  # (laid out once, in the frame's layout phase)
        self.area.queue_render()
        self.stats["frames"].append((time.perf_counter() - t0) * 1000)

    # ---- drawing
    def do_snapshot(self, s):
        self.snapshot_child(self.area, s)  # (the panels are drawn by GL, from their pictures)

    def _realize(self, area):
        area.make_current()
        if area.get_error() is not None:
            print("scene: GL:", area.get_error().message, flush=True)
            return
        self.gl = scenegl.Renderer()
        for name, make in (("plane", scenegl.plane), ("box", scenegl.box), ("cylinder", scenegl.cylinder),
                           ("sphere", scenegl.sphere)):
            self.gl.mesh(name, make())

    def _mesh(self, name):
        if name not in self.gl.meshes:
            self.gl.mesh(name, scenegl.obj(self.asset(f"models/{name}").decode("utf-8", "replace")))
        return name

    def picture(self, n):
        """n's panel's picture in GL, made again if its widget redrew since."""
        pw, ph = self.panel_px(n)
        if not n.dirty and n.tex is not None and n.texsize == (pw, ph):
            return n.tex
        t0 = time.perf_counter()
        sn = Gtk.Snapshot()
        sn.scale(PICTURE_SCALE, PICTURE_SCALE)
        sn.transform(n.alloc.invert())
        self.snapshot_child(n.face, sn)
        node = sn.to_node()
        native = self.get_native()
        if node is None or native is None:
            return n.tex
        tex = native.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, pw * PICTURE_SCALE, ph * PICTURE_SCALE))
        d = Gdk.TextureDownloader.new(tex)
        d.set_format(Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED)
        data, stride = d.download_bytes()
        tw, th = tex.get_width(), tex.get_height()
        raw = data.get_data()
        if stride != tw * 4:
            raw = b"".join(raw[r * stride:r * stride + tw * 4] for r in range(th))
        self.area.make_current()
        n.tex = self.gl.gl.texture(raw, tw, th, n.tex, mipmap=True)
        n.texsize, n.dirty = (pw, ph), False
        self.stats["uploads"] += 1
        self.stats["upload_ms"] += (time.perf_counter() - t0) * 1000
        return n.tex

    def _render(self, area, _ctx):
        if self.gl is None:
            return True
        t0 = time.perf_counter()
        w, h = self.get_width(), self.get_height()
        sc = area.get_scale_factor()
        view, proj, _px, eye = self.matrices(w, h)
        draws = []
        for nid in self.order:
            n = self.nodes[nid]
            if not self.shown(n):
                continue
            if n.mesh is not None:
                tex = None
                mat = self.material(n)
                if mat.get("texture"):
                    tex = self._texture(mat["texture"])
                model = self.drawn(n, "model", lambda: scenegl.floats(self.world_matrix(n)))
                draws.append((self._mesh(n.mesh), model, mat, tex, None))
            elif n.panel is not None and n.face is not None and n.alloc is not None:
                tex = self.picture(n)
                if tex is None:
                    continue
                sw, sh = n.panel.get("size") or (1.6, 1.2)
                model, c = self.drawn(n, ("panel", sw, sh), lambda: (lambda m: (scenegl.floats(m), _vec(m, 0, 0, 0)))(
                    Graphene.Matrix().init_scale(sw, sh, 1).multiply(self.world_matrix(n))))
                draws.append(("plane", model, self.material(n), tex, math.dist(c[:3], eye)))
        area.make_current()
        self.gl.frame(w * sc, h * sc, view, proj, eye, self.scene, draws)
        if os.environ.get("SCENE_FINISH"):  # (measuring: the GPU's work done before the frame's timed as over)
            self.gl.gl.Finish()
        self.stats["render_ms"].append((time.perf_counter() - t0) * 1000)
        return True

    def _texture(self, name):
        if name not in self.gl.textures:
            t = Gdk.Texture.new_from_bytes(GLib.Bytes.new(self.asset(f"textures/{name}")))
            d = Gdk.TextureDownloader.new(t)
            d.set_format(Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED)
            data, stride = d.download_bytes()
            self.gl.textures[name] = self.gl.gl.texture(data.get_data(), t.get_width(), t.get_height(), mipmap=True,
                                                        wrap=scenegl.REPEAT)
        return self.gl.textures[name]

    def do_unroot(self):
        for n in self.nodes.values():
            if isinstance(n.widget, WindowPanel):
                n.widget.give_back()
        Gtk.Widget.do_unroot(self)
