"""The rig's pieces: an offscreen window, real cards (Meta's and the chat's), a GL renderer for frames."""
import os, sys, time, json
W = os.environ.get("W") or sys.argv[1]
sys.path.insert(0, W)
import macos
macos.stay_offscreen()
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk
Adw.init()
import theming, toolcards, cards3d
from metacards import MetaCards

theming.load_css()
cards3d.load_css()


class FakeMeta(MetaCards):
    """MetaCards with the data it reads stubbed: its actions, the left bar's paints."""
    def __init__(self):
        self.painted, self.acts, self.clicked = {}, [], []
    def actions(self):
        return self.acts
    def find_chat(self, ref):
        return None
    def keep(self, done):
        self.clicked.append(("keep", [a["id"] for a in done]))
    def undo_many(self, batch, *_):
        self.clicked.append(("undo", [a["id"] for a in batch]))
    def redo_many(self, batch, *_):
        self.clicked.append(("redo", [a["id"] for a in batch]))
    def open_mark(self, kind, key, how, action):
        self.clicked.append(("open", key))


META = FakeMeta()


def make_card(i):
    """The i-th card: Meta's tool card, Meta's change box, the chat's Bash card, a subagent card, a question form."""
    kind = i % 5
    if kind == 0:
        aid = f"a{i}"
        META.acts.append({"id": aid, "label": f"Moved page Plan {i}", "state": "done",
                          "marks": [["page", f"Notes/Plan {i}", "moved"]]})
        return "meta-tool", META.tool_card({"name": "mcp__meta__move_pages", "state": "done", "actions": [aid],
                                            "input": {"pages": [f"Notes/Plan {i}"], "to": "Archive"},
                                            "result": "Moved 1 page"})
    if kind == 1:
        aid = f"b{i}"
        a = {"id": aid, "label": f"Added page Ideas {i}", "state": "done",
             "marks": [["page", f"Notes/Ideas {i}", "added"], ["page", f"Notes/Todo {i}", "archived"]]}
        META.acts.append(a)
        return "meta-change", META.change_box([a])
    if kind == 2:
        c = toolcards.ToolCard("Bash", f"t{i}")
        c.set_input({"command": f"pytest tests/test_{i}.py -q"})
        c.set_result(f"{i} passed in 0.{i % 9}s", False)
        c.set_state("done")
        return "chat-tool", c
    if kind == 3:
        c = toolcards.ToolCard("Agent", f"g{i}")
        c.set_input({"description": f"Find the flaky test #{i}", "prompt": "Look through the test logs"})
        return "chat-subagent", c
    f = toolcards.QuestionForm([{"question": f"Which build {i}?", "header": "Build",
                                 "options": [{"label": "Debug"}, {"label": "Release"}]}],
                               lambda *_: META.clicked.append(("answer", i)), lambda *_: None)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.add_css_class("tool-card")
    box.append(f)
    return "form", box


def window(child, w=1250, h=760, dark=False):
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
    win = Gtk.Window(default_width=w, default_height=h)
    win.set_child(child)
    win.present()
    pump(0.3)
    return win


def pump(seconds=0.0):
    ctx, end = GLib.MainContext.default(), time.monotonic() + seconds
    while True:
        n = 0
        while ctx.pending() and n < 50:
            ctx.iteration(False)
            n += 1
        if time.monotonic() >= end:
            return
        time.sleep(0.002)


_R = {}


def renderer():
    if "gl" not in _R:
        r = Gsk.GLRenderer()
        r.realize_for_display(Gdk.Display.get_default())
        _R["gl"] = r
    return _R["gl"]


def frame(widget, scale=1):
    """widget drawn now, by GSK's GL renderer: a GdkTexture."""
    w, h = widget.get_width(), widget.get_height()
    for _ in range(20):
        s = Gtk.Snapshot()
        s.scale(scale, scale)
        Gtk.WidgetPaintable.new(widget).snapshot(s, w, h)
        node = s.to_node()
        if node is not None:
            break
        print("(no picture yet: again)", flush=True)
        pump(0.02)
    return renderer().render_texture(node, Graphene.Rect().init(0, 0, w * scale, h * scale))


def fake_pointer(win, hand):
    """GDK asks AppKit where the pointer is after each relayout (to send enter/leave): here, where the hand is."""
    import ctypes
    lib = ctypes.cdll.LoadLibrary(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakeloc.dylib"))
    lib.fakeloc_set.argtypes = [ctypes.c_double] * 4
    lib.fakeloc_install()
    post = hand.post

    class R(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]

    def posted(kind, x, y, *a, **k):
        ns = macos.native_window(win)
        rt = macos.objc_send.rt
        frame = ctypes.cast(rt.objc_msgSend, ctypes.CFUNCTYPE(R, ctypes.c_void_p, ctypes.c_void_p))(
            ns, rt.sel_registerName(b"frame"))
        view = macos.objc_send(ns, b"contentView")
        bounds = ctypes.cast(rt.objc_msgSend, ctypes.CFUNCTYPE(R, ctypes.c_void_p, ctypes.c_void_p))(
            view, rt.sel_registerName(b"bounds"))
        wy = bounds.h - y
        lib.fakeloc_set(frame.x + x, frame.y + wy, x, wy)
        return post(kind, x, y, *a, **k)
    hand.post = posted
