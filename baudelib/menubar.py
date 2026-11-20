"""The host process's item in macOS's menu bar (baude_headless.py serve): a few words of what it's doing, its sessions
at a turn or asking, and Quit. No Dock icon, no window, no GTK: AppKit through the Objective-C runtime, with ctypes,
as macos.py has it. AppKit's run loop has the main thread, and sleeps till there's something to show."""
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from macos import ns_string, objc_send  # noqa: E402
from baudelib import wording  # noqa: E402
from baudelib.client import stopping_line  # noqa: E402

NS_VARIABLE_LENGTH = -1.0
USER_INITIATED_ALLOWING_SLEEP = 0x00FFFFFF  # (NSActivityUserInitiatedAllowingIdleSystemSleep: no App Nap)
TERMINATE_CANCEL = 0
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
    listing = [s for s in listing if not s.get("draft")]  # (a new chat not written in yet: not one to count)
    running = [s for s in listing if s.get("state") in ("working", "waiting", "idle")]
    asking = [s for s in running if s.get("state") == "waiting"]
    working = [s for s in running if s.get("state") == "working"]
    title = wording.say("host_menu.title_asking", n=len(asking)) if asking else \
        wording.say("host_menu.title_working", n=len(working)) if working else "Baude"
    n = len(listing)
    line = wording.say("host_menu.sessions_running", n=n, running=len(running)) if running else \
        wording.say("host_menu.sessions_none_running", n=n)
    shown = sorted(running, key=lambda s: ({"waiting": 0, "working": 1}.get(s["state"], 2), -(s.get("said") or 0)))
    words = {"waiting": wording.say("host_menu.asking"), "working": wording.say("host_menu.working"),
             "idle": wording.say("host_menu.waiting_for_you")}
    return title, [line], [((s.get("title") or wording.say("host_menu.untitled"))[:48], words[s["state"]])
                           for s in shown[:12]]


def say_stopping(quitting):
    """(title, line) while it's stopping (Server.quitting; {} for Quit picked, before it's started on that): how many
    of its claudes are still to stop."""
    restart = bool(quitting.get("restart"))
    return wording.say("host_menu.title_restarting") if restart else wording.say("host_menu.title_stopping"), \
        stopping_line(wording.say("host_menu.daemon"), restart, quitting.get("left"), quitting.get("total"))


class MenuBar:
    """The item, its menu built again as what's said changes (changed, from any thread: done on the main thread).
    quit(): Quit picked (on the main thread). quitting(): how far along stopping it is (Server.quitting), else None:
    said in place of its sessions, from the moment Quit's picked, so the pick shows it's taken."""

    def __init__(self, listing, quit, quitting=lambda: None):
        self.listing, self.quitting, self.picked = listing, quitting, False

        def picked():
            if self.picked:  # (picked again while it's stopping: it's on it already)
                return
            self.picked = True
            self.refresh()
            quit()
        self.quit = picked
        rt = self.rt = _runtime()
        self.app = objc_send(b"NSApplication", b"sharedApplication")
        objc_send(self.app, b"setActivationPolicy:", ACCESSORY, types=(ctypes.c_long,), ret=ctypes.c_bool)
        self.target = objc_send(objc_send(_target_class(self), b"alloc"), b"init")
        # (AppKit's own quit, at logout say, asked of it: its Quit's, so its claudes end as they should)
        objc_send(self.app, b"setDelegate:", self.target, types=(ctypes.c_void_p,))
        # (no windows brought back after a crash: AppKit would ask in an alert, at the screen, and wait)
        objc_send(objc_send(b"NSUserDefaults", b"standardUserDefaults"), b"registerDefaults:",
                  objc_send(b"NSDictionary", b"dictionaryWithObject:forKey:",
                            objc_send(b"NSNumber", b"numberWithBool:", True, types=(ctypes.c_bool,)),
                            ns_string("ApplePersistenceIgnoreState"), types=(ctypes.c_void_p, ctypes.c_void_p)),
                  types=(ctypes.c_void_p,))
        self.activity = None  # (while sessions run: macOS not to nap it, which would hold up what claude says)
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
        listing = self.listing()
        self.awake(any(s.get("state") in ("working", "waiting", "idle") for s in listing))
        title, lines, sessions = say(listing)
        quitting = self.quitting()
        if quitting is not None or self.picked:
            title, line = say_stopping(quitting or {})
            lines, sessions = [line], []
        objc_send(self.button, b"setTitle:", ns_string(title), types=(ctypes.c_void_p,))
        menu = objc_send(objc_send(b"NSMenu", b"alloc"), b"init")
        objc_send(menu, b"setAutoenablesItems:", False, types=(ctypes.c_bool,))
        for text in lines:
            self._add(menu, text)
        for name, state in sessions:
            self._add(menu, f"{name} — {state}", indent=1)
        objc_send(menu, b"addItem:", objc_send(b"NSMenuItem", b"separatorItem"), types=(ctypes.c_void_p,))
        quit = self._add(menu, wording.say("host_menu.quit"), action=b"quit:", key="q")
        if quitting is not None or self.picked:  # (on it already: shown so, not picked again)
            objc_send(quit, b"setEnabled:", False, types=(ctypes.c_bool,))
        objc_send(self.item, b"setMenu:", menu, types=(ctypes.c_void_p,))
        objc_send(menu, b"release")

    def awake(self, running):
        """(main thread) macOS's App Nap held off while a session's running (an activity), let be when none is."""
        if running and self.activity is None:
            self.activity = objc_send(objc_send(objc_send(b"NSProcessInfo", b"processInfo"),
                                                b"beginActivityWithOptions:reason:", USER_INITIATED_ALLOWING_SLEEP,
                                                ns_string("Claude sessions running"),
                                                types=(ctypes.c_ulonglong, ctypes.c_void_p)), b"retain")
        elif not running and self.activity is not None:
            objc_send(objc_send(b"NSProcessInfo", b"processInfo"), b"endActivity:", self.activity,
                      types=(ctypes.c_void_p,))
            objc_send(self.activity, b"release")
            self.activity = None

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
    asked = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    _target_class.imps = [imp(lambda _s, _c, _o: bar.refresh()), imp(lambda _s, _c, _o: bar.quit()),
                          asked(lambda _s, _c, _o: (bar.quit(), TERMINATE_CANCEL)[1])]  # (it goes once it's done)
    name = b"BaudeHostMenuTarget%d" % os.getpid()
    cls = rt.objc_allocateClassPair(rt.objc_getClass(b"NSObject"), name, 0)
    if not cls:  # (made already, in this process: a second bar's, say)
        return rt.objc_getClass(name)
    for sel, fn, kinds in zip((b"refresh:", b"quit:", b"applicationShouldTerminate:"), _target_class.imps,
                              (b"v@:@", b"v@:@", b"Q@:@")):
        rt.class_addMethod(cls, rt.sel_registerName(sel), ctypes.cast(fn, ctypes.c_void_p), kinds)
    rt.objc_registerClassPair(cls)
    return cls
