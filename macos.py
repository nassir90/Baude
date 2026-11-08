"""Objective-C through the runtime, with ctypes (there's no PyObjC), for what Baude and Record do on macOS.
No GTK here: Baude calls it before it loads GTK (macos_app_name), and both call full_redraws then."""
import os
import sys


def full_redraws():
    """On macOS, every frame draws all of the window (GSK_DEBUG=full-redraw), set before GTK is loaded. GTK's macOS
    backend shows a window as tiles, each given a frame's new picture only where that frame changed something; some
    keep an old one (a dialog opening over a maximized window, a resize) and show it until their part of the window
    changes again. Drawn whole, every tile gets every frame. It costs only while something moves: a window that isn't
    changing draws nothing. GSK_DEBUG set already (GSK_DEBUG= for none): left as it is. True if they're on."""
    if sys.platform == "darwin":
        os.environ.setdefault("GSK_DEBUG", "full-redraw")
    return "full-redraw" in os.environ.get("GSK_DEBUG", "").replace(":", " ").split()


def stay_offscreen():
    """On macOS, this process's windows never reach the screen, though GTK lays them out and maps them as it would
    (rendered by hand: GSK_RENDERER=cairo and a Gtk.WidgetPaintable). No Dock icon or menu bar, nothing raised over
    anyone's work, the app you're in still in front. Called before GTK is loaded: GTK brings an app to the front as its
    display opens, and orders a window in after its first frame; here those calls do nothing, for good. Elsewhere,
    nothing (an X server of its own does it there: Xvfb)."""
    if sys.platform != "darwin":
        return
    import ctypes
    nsapp = objc_send(b"NSApplication", b"sharedApplication")
    objc_send(nsapp, b"setActivationPolicy:", 2, types=(ctypes.c_long,), ret=ctypes.c_bool)  # (2: Prohibited)
    # (no windows brought back: killed while it had some, as a render that takes too long is, the next run's AppKit asks in
    # a dialog whether to reopen them, here on no screen, and waits for an answer for good)
    objc_send(objc_send(b"NSUserDefaults", b"standardUserDefaults"), b"registerDefaults:",
              objc_send(b"NSDictionary", b"dictionaryWithObject:forKey:",
                        objc_send(b"NSNumber", b"numberWithBool:", True, types=(ctypes.c_bool,)),
                        ns_string("ApplePersistenceIgnoreState"), types=(ctypes.c_void_p, ctypes.c_void_p)),
              types=(ctypes.c_void_p,))
    rt = objc_send.rt
    rt.class_getInstanceMethod.restype = rt.method_setImplementation.restype = ctypes.c_void_p
    rt.class_getInstanceMethod.argtypes = rt.method_setImplementation.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    for cls, sel, ret, args in ((b"NSApplication", b"activateIgnoringOtherApps:", None, (ctypes.c_bool,)),
                                (b"NSApplication", b"activate", None, ()),
                                (b"NSApplication", b"setActivationPolicy:", ctypes.c_bool, (ctypes.c_long,)),
                                (b"NSWindow", b"makeKeyAndOrderFront:", None, (ctypes.c_void_p,)),
                                (b"NSWindow", b"orderFront:", None, (ctypes.c_void_p,)),
                                (b"NSWindow", b"orderFrontRegardless", None, ()),
                                (b"NSWindow", b"orderWindow:relativeTo:", None, (ctypes.c_long, ctypes.c_long)),
                                (b"NSWindow", b"makeKeyWindow", None, ()),
                                (b"NSWindow", b"makeMainWindow", None, ())):
        method = rt.class_getInstanceMethod(rt.objc_getClass(cls), rt.sel_registerName(sel))
        if method:  # (kept: the runtime calls it from now on)
            nothing = ctypes.CFUNCTYPE(ret, ctypes.c_void_p, ctypes.c_void_p, *args)(lambda *_: 0 if ret else None)
            stay_offscreen.__dict__.setdefault("kept", []).append(nothing)
            rt.method_setImplementation(method, ctypes.cast(nothing, ctypes.c_void_p).value)


def objc_send(obj, sel, *args, types=(), ret=None):
    """[obj sel...] through the Objective-C runtime (there's no PyObjC): obj a class name (bytes) or an object;
    types the arguments' ctypes types."""
    import ctypes
    rt = objc_send.__dict__.get("rt")
    if rt is None:
        rt = objc_send.rt = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
        rt.objc_getClass.restype = rt.sel_registerName.restype = ctypes.c_void_p
        rt.objc_getClass.argtypes = rt.sel_registerName.argtypes = [ctypes.c_char_p]
    if isinstance(obj, bytes):
        obj = rt.objc_getClass(obj)
    f = ctypes.CFUNCTYPE(ret or ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, *types)(
        ctypes.cast(rt.objc_msgSend, ctypes.c_void_p).value)
    return f(obj, rt.sel_registerName(sel), *args)


def ns_string(text):
    import ctypes
    return objc_send(b"NSString", b"stringWithUTF8String:", text.encode(), types=(ctypes.c_char_p,))


def _rect():
    import ctypes

    class Rect(ctypes.Structure):  # (NSRect: its origin, then its size, in points)
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]
    _rect.cls = getattr(_rect, "cls", None) or Rect
    return _rect.cls


def native_window(window):
    """A shown (or realized) GTK window's NSWindow, through GTK's gdk_macos_surface_get_native_window; None if it
    has none (not realized, not macOS)."""
    import ctypes
    surface = window.get_surface() if sys.platform == "darwin" else None
    if surface is None:
        return None
    get = ctypes.CDLL(None).gdk_macos_surface_get_native_window
    get.restype, get.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
    pointer = ctypes.pythonapi.PyCapsule_GetPointer
    pointer.restype, pointer.argtypes = ctypes.c_void_p, [ctypes.py_object, ctypes.c_char_p]
    return get(pointer(surface.__gpointer__, None)) or None


def window_frame(window):
    """Where a GTK window is on macOS: its NSWindow's frame [x, y, width, height] in the screens' points (which
    display, where on it, how big), or None (not shown, not macOS)."""
    if (ns := native_window(window)) is None:
        return None
    r = objc_send(ns, b"frame", ret=_rect())
    return [r.x, r.y, r.w, r.h]


def set_window_frame(window, frame):
    """A GTK window put at frame (window_frame's), if some display shows part of it (one unplugged since: left where
    it is). True if it was."""
    import ctypes
    if (ns := native_window(window)) is None or not frame:
        return False
    x, y, w, h = frame
    screens = objc_send(b"NSScreen", b"screens")
    for i in range(objc_send(screens, b"count", ret=ctypes.c_ulong)):
        s = objc_send(objc_send(screens, b"objectAtIndex:", i, types=(ctypes.c_ulong,)), b"frame", ret=_rect())
        if x < s.x + s.w and s.x < x + w and y < s.y + s.h and s.y < y + h:
            objc_send(ns, b"setFrame:display:", _rect()(x, y, w, h), True, types=(_rect(), ctypes.c_bool))
            return True
    return False


# (what GTK's macOS backend uses for these names too, so the two never take turns; the rest are left to it)
CURSORS = {"pointer": b"pointingHandCursor", "text": b"IBeamCursor", "default": b"arrowCursor",
           "grab": b"openHandCursor", "grabbing": b"closedHandCursor", "crosshair": b"crosshairCursor",
           "ew-resize": b"resizeLeftRightCursor", "col-resize": b"resizeLeftRightCursor",
           "ns-resize": b"resizeUpDownCursor", "row-resize": b"resizeUpDownCursor"}
SYSTEM_CURSORS = "/System/Library/Frameworks/ApplicationServices.framework/Versions/A/Frameworks/HIServices.framework" \
    "/Versions/A/Resources/cursors"


def show_cursor(name):
    """macOS's cursor for GTK's name shown now (pointers.show: nothing else calls this). Elsewhere, nothing."""
    if sys.platform != "darwin":
        return
    if name == "zoom-in":
        cursor = getattr(make_zoom_cursor, "cursor", None)
    else:
        cursor = objc_send(b"NSCursor", CURSORS[name]) if name in CURSORS else None
    if cursor and cursor != objc_send(b"NSCursor", b"currentCursor"):
        objc_send(cursor, b"set")


def make_zoom_cursor():
    """The zoom-in cursor (an image's) is the magnifying glass: GTK asks macOS for it by a private cursor number
    (+[gdkCoreCursor gdkZoomInCursor], _coreCursorType 42) that shows the arrow now. That method is made to return the
    magnifying glass macOS keeps as a file, vectors and its hot spot (the lens's centre, from its info.plist). GTK loaded
    first: the method's in it."""
    import ctypes
    import plistlib
    folder = os.path.join(SYSTEM_CURSORS, "zoomin")
    with open(os.path.join(folder, "info.plist"), "rb") as f:
        info = plistlib.load(f)

    class NSPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]
    image = objc_send(objc_send(b"NSImage", b"alloc"), b"initWithContentsOfFile:",
                      ns_string(os.path.join(folder, "cursor.pdf")), types=(ctypes.c_void_p,))
    if not image:
        return
    cursor = objc_send(objc_send(b"NSCursor", b"alloc"), b"initWithImage:hotSpot:", image,
                       NSPoint(info.get("hotx", 12), info.get("hoty", 11)), types=(ctypes.c_void_p, NSPoint))
    make_zoom_cursor.cursor = cursor  # (show_cursor's)
    rt = objc_send.rt
    rt.object_getClass.restype, rt.object_getClass.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
    rt.class_replaceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
    if not (core := rt.objc_getClass(b"gdkCoreCursor")):
        return
    # (kept: GTK calls it for as long as the app runs)
    make_zoom_cursor.imp = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(lambda *_: cursor)
    rt.class_replaceMethod(rt.object_getClass(core), rt.sel_registerName(b"gdkZoomInCursor"),
                           ctypes.cast(make_zoom_cursor.imp, ctypes.c_void_p), b"@@:")


def frontmost_pid():
    """The pid of the app in front, the one you're in (NSWorkspace's frontmostApplication), or None."""
    import ctypes
    app = objc_send(objc_send(b"NSWorkspace", b"sharedWorkspace"), b"frontmostApplication")
    return objc_send(app, b"processIdentifier", ret=ctypes.c_int) if app else None


def give_front_to(pid):
    """While this app's in front, the front given to pid's (the app you were in before this one restarted: GTK brings
    an app to the front as it starts). Asked as macOS 14's cooperative activation has it: this app yields, the other
    activates. True if it was asked."""
    import ctypes
    nsapp = objc_send(b"NSApplication", b"sharedApplication")
    other = objc_send(b"NSRunningApplication", b"runningApplicationWithProcessIdentifier:", pid, types=(ctypes.c_int,))
    if not other or not objc_send(nsapp, b"isActive", ret=ctypes.c_bool):
        return False
    yield_sel = objc_send.rt.sel_registerName(b"yieldActivationToApplication:")
    if objc_send(nsapp, b"respondsToSelector:", yield_sel, types=(ctypes.c_void_p,), ret=ctypes.c_bool):
        objc_send(nsapp, b"yieldActivationToApplication:", other, types=(ctypes.c_void_p,))
    return bool(objc_send(other, b"activateWithOptions:", 0, types=(ctypes.c_ulong,), ret=ctypes.c_bool))
