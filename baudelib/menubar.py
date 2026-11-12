"""The host process's item in macOS's menu bar (baude_headless.py serve): a few words of what it's doing, its sessions
at a turn or asking, and Quit. No Dock icon, no window, no GTK: AppKit through the Objective-C runtime, with ctypes,
as macos.py has it. AppKit's run loop has the main thread, and sleeps till there's something to show."""
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from macos import ns_string, objc_send  # noqa: E402

NS_VARIABLE_LENGTH = -1.0
ACCESSORY = 1  # (NSApplicationActivationPolicyAccessory: a menu bar item's, no Dock icon)


def can_show():
    """Is there a menu bar to be in: macOS, in a session at its screen (not one ssh started, which has none)?"""
    if sys.platform != "darwin" or os.environ.get("SSH_CONNECTION") or os.environ.get("BAUDE_MENU_BAR") == "0":
        return False
    try:
        cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        session = cg.CGSessionCopyCurrentDictionary()
    except OSError:
        return False
    if not session:
        return False
    cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease(session)
    return True


def say(listing):
    """(title, [lines], [sessions]) for what a host process has (its listing): the title a few words; the lines, how
    many and how many running; each session at a turn, asking or between turns (claude running) as (title, state)."""
    running = [s for s in listing if s.get("state") in ("working", "waiting", "idle")]
    asking = [s for s in running if s.get("state") == "waiting"]
    working = [s for s in running if s.get("state") == "working"]
    title = "Baude" + (f" · {len(asking)} asking" if asking else f" · {len(working)} working" if working else "")
    n = len(listing)
    line = f"{n} session{'s' if n != 1 else ''}" + (f", {len(running)} running" if running else ", none running")
    shown = sorted(running, key=lambda s: ({"waiting": 0, "working": 1}.get(s["state"], 2), -(s.get("said") or 0)))
    words = {"waiting": "asking", "working": "working", "idle": "waiting for you"}
    return title, [line], [((s.get("title") or "(untitled)")[:48], words[s["state"]]) for s in shown[:12]]


class MenuBar:
    """The item, its menu built again as what's said changes (changed, from any thread: done on the main thread).
    quit(): Quit picked (on the main thread)."""

    def __init__(self, listing, quit):
        self.listing, self.quit = listing, quit
        rt = self.rt = _runtime()
        self.app = objc_send(b"NSApplication", b"sharedApplication")
        objc_send(self.app, b"setActivationPolicy:", ACCESSORY, types=(ctypes.c_long,), ret=ctypes.c_bool)
        self.target = objc_send(objc_send(_target_class(self), b"alloc"), b"init")
        bar = objc_send(b"NSStatusBar", b"systemStatusBar")
        self.item = objc_send(objc_send(bar, b"statusItemWithLength:", NS_VARIABLE_LENGTH, types=(ctypes.c_double,)),
                              b"retain")
        self.button = objc_send(self.item, b"button")
        self.refresh_sel = rt.sel_registerName(b"refresh:")
        self.refresh()

    def changed(self):
        objc_send(self.target, b"performSelectorOnMainThread:withObject:waitUntilDone:", self.refresh_sel, None, False,
                  types=(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool))

    def refresh(self):
        """(main thread) Its title and menu as the host process's sessions are now."""
        pool = objc_send(objc_send(b"NSAutoreleasePool", b"alloc"), b"init")
        try:
            self._refresh()
        finally:
            objc_send(pool, b"drain")

    def _refresh(self):
        title, lines, sessions = say(self.listing())
        objc_send(self.button, b"setTitle:", ns_string(title), types=(ctypes.c_void_p,))
        menu = objc_send(objc_send(b"NSMenu", b"alloc"), b"init")
        objc_send(menu, b"setAutoenablesItems:", False, types=(ctypes.c_bool,))
        for text in lines:
            self._add(menu, text)
        for name, state in sessions:
            self._add(menu, f"{name} — {state}", indent=1)
        objc_send(menu, b"addItem:", objc_send(b"NSMenuItem", b"separatorItem"), types=(ctypes.c_void_p,))
        self._add(menu, "Quit Baude's Host Process", action=b"quit:", key="q")
        objc_send(self.item, b"setMenu:", menu, types=(ctypes.c_void_p,))
        objc_send(menu, b"release")

    def _add(self, menu, text, action=None, key="", indent=0):
        item = objc_send(menu, b"addItemWithTitle:action:keyEquivalent:", ns_string(text),
                         self.rt.sel_registerName(action) if action else None, ns_string(key),
                         types=(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
        if action:
            objc_send(item, b"setTarget:", self.target, types=(ctypes.c_void_p,))
        else:  # (what it says: read, not picked)
            objc_send(item, b"setEnabled:", False, types=(ctypes.c_bool,))
        if indent:
            objc_send(item, b"setIndentationLevel:", indent, types=(ctypes.c_long,))
        return item

    def run(self):
        """AppKit's run loop, on the main thread, till the process ends."""
        objc_send(self.app, b"run")


def _runtime():
    objc_send(b"NSObject", b"class")  # (the runtime and AppKit loaded)
    rt = objc_send.rt
    rt.objc_allocateClassPair.restype = ctypes.c_void_p
    rt.objc_allocateClassPair.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    rt.objc_registerClassPair.argtypes = [ctypes.c_void_p]
    rt.class_addMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
    return rt


def _target_class(bar):
    """An NSObject subclass made through the runtime, as there's no PyObjC: what the menu's Quit and the main thread's
    refresh call (kept, with what it calls, for as long as the process runs)."""
    rt = bar.rt
    imp = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    _target_class.imps = [imp(lambda _s, _c, _o: bar.refresh()), imp(lambda _s, _c, _o: bar.quit())]
    name = b"BaudeHostMenuTarget%d" % os.getpid()
    cls = rt.objc_allocateClassPair(rt.objc_getClass(b"NSObject"), name, 0)
    if not cls:  # (made already, in this process: a second bar's, say)
        return rt.objc_getClass(name)
    for sel, fn in zip((b"refresh:", b"quit:"), _target_class.imps):
        rt.class_addMethod(cls, rt.sel_registerName(sel), ctypes.cast(fn, ctypes.c_void_p), b"v@:@")
    rt.objc_registerClassPair(cls)
    return cls
