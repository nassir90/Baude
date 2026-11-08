"""A real mouse for a window on macOS, on or off the screen: NSEvents posted to the app as AppKit would deliver them, so
that GTK's gestures, propagation and all run as they do under your hand. Elsewhere, available() is False."""
import ctypes
import sys
import time


def available():
    return sys.platform == "darwin"


LEFT_DOWN, LEFT_UP, RIGHT_DOWN, RIGHT_UP, MOVED, LEFT_DRAGGED = 1, 2, 3, 4, 5, 6
MOUSE_EXITED = 9
KEY_DOWN, KEY_UP = 10, 11
SHIFT, CONTROL, OPTION, COMMAND = 1 << 17, 1 << 18, 1 << 19, 1 << 20
RIGHT_ARROW, LEFT_ARROW = (124, "\uf703"), (123, "\uf702")
UP_ARROW, DOWN_ARROW = (126, "\uf700"), (125, "\uf701")
DOWN_ARROW, UP_ARROW = (125, "\uf701"), (126, "\uf700")
RETURN, TAB, BACKSPACE, ESCAPE = (36, "\r"), (48, "\t"), (51, "\x7f"), (53, "\x1b")
# (what a key on a US keyboard types: its code; with Shift, the second row's)
_TYPED = dict(zip("asdfhgzxcvbqwertyuiopljk;'\\,./nm`1234567890-=[] ",
                  (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 32, 34, 31, 35, 37, 38, 40, 41, 39, 42, 43, 47,
                   44, 45, 46, 50, 18, 19, 20, 21, 23, 22, 26, 28, 25, 29, 27, 24, 33, 30, 49)))
_SHIFTED = dict(zip('!@#$%^&*()_+{}:"|<>?~', "1234567890-=[];'\\,./`"))


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _Rect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


class Mouse:
    """win's pointer, its coordinates (y down)."""

    held = 0
    kept = None

    def __init__(self, win):
        import macos
        self.win, self.macos = win, macos
        macos.objc_send(b"NSApplication", b"sharedApplication")
        self.rt = macos.objc_send.rt
        self.rt.objc_getClass.restype = ctypes.c_void_p
        if Mouse.kept is None:  # (GDK asks NSEvent which buttons are down: the ones this holds, not the real mouse's)
            rt = self.rt
            rt.class_getClassMethod.restype = rt.method_setImplementation.restype = ctypes.c_void_p
            rt.class_getClassMethod.argtypes = rt.method_setImplementation.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            method = rt.class_getClassMethod(rt.objc_getClass(b"NSEvent"), rt.sel_registerName(b"pressedMouseButtons"))
            Mouse.kept = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(lambda *_: Mouse.held)
            rt.method_setImplementation(method, ctypes.cast(Mouse.kept, ctypes.c_void_p).value)
            # (an event made this way says button 0, whichever it's of)
            rt.class_getInstanceMethod.restype = ctypes.c_void_p
            rt.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            method = rt.class_getInstanceMethod(rt.objc_getClass(b"NSEvent"), rt.sel_registerName(b"buttonNumber"))
            kind = self._msg(ctypes.c_ulong)
            Mouse.kept_button = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(
                lambda event, _sel: 1 if kind(event, rt.sel_registerName(b"type")) in (RIGHT_DOWN, RIGHT_UP) else 0)
            rt.method_setImplementation(method, ctypes.cast(Mouse.kept_button, ctypes.c_void_p).value)

    def _msg(self, restype, *argtypes):
        return ctypes.cast(self.rt.objc_msgSend, ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes))

    def post(self, kind, x, y, clicks=1, flags=0):
        sel = self.rt.sel_registerName
        window = self.macos.native_window(self.win)
        number = self._msg(ctypes.c_long)(window, sel(b"windowNumber"))
        height = self._msg(_Rect)(self._msg(ctypes.c_void_p)(window, sel(b"contentView")), sel(b"bounds")).h
        make = self._msg(ctypes.c_void_p, ctypes.c_ulong, _Point, ctypes.c_ulong, ctypes.c_double, ctypes.c_long,
                         ctypes.c_void_p, ctypes.c_long, ctypes.c_long, ctypes.c_float)
        event = make(self.rt.objc_getClass(b"NSEvent"), sel(b"mouseEventWithType:location:modifierFlags:timestamp:"
                                                            b"windowNumber:context:eventNumber:clickCount:pressure:"),
                     kind, _Point(x, height - y), flags, time.monotonic(), number, None, 0, clicks, 1.0)
        app = self.macos.objc_send(b"NSApplication", b"sharedApplication")
        self._msg(None, ctypes.c_void_p, ctypes.c_bool)(app, sel(b"postEvent:atStart:"), event, False)

    def key(self, code, chars, flags=0):
        """A key pressed and let go: code its macOS key code, chars what it types (an arrow's are private-use ones)."""
        sel = self.rt.sel_registerName
        window = self.macos.native_window(self.win)
        number = self._msg(ctypes.c_long)(window, sel(b"windowNumber"))
        make = self._msg(ctypes.c_void_p, ctypes.c_ulong, _Point, ctypes.c_ulong, ctypes.c_double, ctypes.c_long,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool, ctypes.c_ushort)
        app = self.macos.objc_send(b"NSApplication", b"sharedApplication")
        for kind in (KEY_DOWN, KEY_UP):
            text = self.macos.ns_string(chars)  # (each its own: the last is gone once the main loop's turned)
            event = make(self.rt.objc_getClass(b"NSEvent"),
                         sel(b"keyEventWithType:location:modifierFlags:timestamp:windowNumber:context:characters:"
                             b"charactersIgnoringModifiers:isARepeat:keyCode:"),
                         kind, _Point(0, 0), flags, time.monotonic(), number, None, text, text, False, code)
            self._msg(None, ctypes.c_void_p, ctypes.c_bool)(app, sel(b"postEvent:atStart:"), event, False)
            self.settle()

    def type(self, text):
        """Keys pressed for text, one each (on a US keyboard's layout: Shift for what's over a key)."""
        for ch in text:
            plain = _SHIFTED.get(ch) or ch.lower()
            self.key(_TYPED[plain], ch, flags=SHIFT if plain != ch else 0)

    def settle(self, seconds=0.04):
        """Time for GTK to take what's been posted, so that what's held down when it asks is what was when it was posted."""
        from gi.repository import GLib
        ctx, end = GLib.MainContext.default(), time.monotonic() + seconds
        while time.monotonic() < end:
            ctx.iteration(False)
            time.sleep(0.002)

    def down(self, x, y, clicks=1, flags=0):
        self.post(MOVED, x, y, flags=flags)
        self.settle()
        Mouse.held = 1
        self.post(LEFT_DOWN, x, y, clicks=clicks, flags=flags)
        self.settle()

    def drag(self, x, y):
        self.post(LEFT_DRAGGED, x, y)
        self.settle()

    def up(self, x, y, clicks=1, flags=0):
        self.post(LEFT_UP, x, y, clicks=clicks, flags=flags)
        self.settle()
        Mouse.held = 0

    def right_click(self, x, y):
        self.post(MOVED, x, y)
        self.settle()
        self.post(RIGHT_DOWN, x, y)
        self.settle()
        self.post(RIGHT_UP, x, y)
        self.settle()

    def lost(self):
        """GDK told the pointer has left this window, though moves go on coming, as it goes once a popover or a menu has come
        and gone: GDK shows none of GTK's cursors now, whichever way this run's real pointer lies."""
        sel = self.rt.sel_registerName
        window = self.macos.native_window(self.win)
        view = self._msg(ctypes.c_void_p)(window, sel(b"contentView"))
        make = self._msg(ctypes.c_void_p, ctypes.c_ulong, _Point, ctypes.c_ulong, ctypes.c_double, ctypes.c_long,
                         ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)
        event = make(self.rt.objc_getClass(b"NSEvent"),
                     sel(b"enterExitEventWithType:location:modifierFlags:timestamp:windowNumber:context:eventNumber:"
                         b"trackingNumber:userData:"),
                     MOUSE_EXITED, _Point(0, 0), 0, time.monotonic(), self._msg(ctypes.c_long)(window, sel(b"windowNumber")),
                     None, 0, self._msg(ctypes.c_void_p)(view, sel(b"trackingArea")), None)
        app = self.macos.objc_send(b"NSApplication", b"sharedApplication")
        self._msg(None, ctypes.c_void_p, ctypes.c_bool)(app, sel(b"postEvent:atStart:"), event, False)
        self.settle()

    def hover(self, widget, x, y, want):
        """The pointer moved onto (x, y) of widget (its coordinates): macOS comes to show want (GTK's name). (GDK puts in
        moves of its own, from where the real pointer is: so again, till the last move's this one.)"""
        from gi.repository import Graphene
        ok, p = widget.compute_point(self.win, Graphene.Point().init(x, y))
        assert ok, widget
        tx, ty = self.win.get_surface_transform()
        for _ in range(30):
            self.post(MOVED, p.x + tx, p.y + ty)
            self.settle()
            if pointer_shown() == want:
                return
        raise AssertionError(f"over {type(widget).__name__} at ({x:.0f}, {y:.0f}): {pointer_shown()}, not {want}")

    def hover_middle(self, widget, want):
        self.hover(widget, widget.get_width() / 2, widget.get_height() / 2, want)


def pointer_shown():
    """What macOS is showing as the pointer now, by GTK's name for it (None: another)."""
    import macos
    now = macos.objc_send(b"NSCursor", b"currentCursor")
    if now == getattr(macos.make_zoom_cursor, "cursor", None):
        return "zoom-in"
    return next((name for name, sel in macos.CURSORS.items() if now == macos.objc_send(b"NSCursor", sel)), None)


def label_spot(label, words):
    """The middle of words' first letter in a label, in its coordinates."""
    from gi.repository import Pango
    layout, (ox, oy) = label.get_layout(), label.get_layout_offsets()
    at = layout.get_text().encode().index(words.encode())
    p = layout.index_to_pos(at)
    return p.x / Pango.SCALE + ox + 1, p.y / Pango.SCALE + oy + p.height / Pango.SCALE / 2
