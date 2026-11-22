"""startup — Baude as it starts: its app id, start-up milestones (trace, BAUDE_TRACE), the state a restart leaves
for the next process (take_restart_state), the app's sizes (apply_scales), and Baude in the platform's list of apps:
its logo (logo, in the accent), the command that starts it again (launch_command) and the launchers (LAUNCHER), which
start ~/.local/bin/baude (baudelib/bins.py).
"""
import json
import os
import sys
import time

from macos import objc_send
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

import theming  # noqa: E402  (the logo in the accent: follow_accent, theme_logo)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
from widgets import Ticker  # noqa: E402,F401
from widgets import _settings_path  # noqa: E402,F401
from pickers import _data_home  # noqa: E402,F401

from baudelib import applist  # noqa: E402  (the logo's PNGs and .icns, and its launcher's drawn again: Record's too)
from baudelib import bins  # noqa: E402  (the commands in ~/.local/bin the launchers start)
from baudelib.applist import exec_quote as _exec_quote  # noqa: E402  (Record's and Emission's entries quoted so too)


APP_ID = "dev.baude.Baude"


def _process_age():
    """Seconds since this process was started (interpreter start-up included), from /proc."""
    try:
        with open("/proc/self/stat") as f:
            start_ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        return uptime - start_ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return 0.0


TRACE = os.environ.get("BAUDE_TRACE")
_TRACE_BASE = time.monotonic() - _process_age()  # monotonic time of the process's start
_traced = set()


def trace(what, once=True):
    """BAUDE_TRACE=1: when start-up milestones happen, in ms since the process started."""
    if TRACE and not (once and what in _traced):
        _traced.add(what)
        cpu = time.process_time() * 1000  # (CPU used so far: unlike wall time, not inflated by a busy machine)
        print(f"baude-trace {(time.monotonic() - _TRACE_BASE) * 1000:7.0f} ms {cpu:7.0f} cpu  {what}",
              file=sys.stderr, flush=True)
        if TRACE == "exit" and {"sidebar filled", "first frame", "claude ready"} <= _traced:
            GLib.idle_add(lambda: Gio.Application.get_default().quit())


# (MODELS, the fallback model list: baudelib.claude)
# (APPLE_MODEL, apple_exe, PI_CLAUDE, LOCAL_MODELS, local_models and model_program: baudelib.models)


# (MODES and EFFORT_LABELS: baudelib.claude)


# The app's own sizes (Settings → Size), the same in every window and pane: the text's and the icons'. 1.0 is the usual.


def restart_state_path():
    return os.path.join(os.path.dirname(_settings_path()), "restart.json")


def take_restart_state(path):
    """The state a restarting baude left for this one (read once, then removed): {windows: [each window's
    restart_state], front, updated} (BaudeApp.restart), or one window's from before. A window's own, already read: as
    it is."""
    if isinstance(path, dict):
        return path
    if not path:
        return None
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        os.remove(path)
    except OSError:
        pass
    return state if isinstance(state, dict) else None


def record_item():
    """The menu's item for Record (record.py)."""
    return "Open in Record…"


# the display's font resolution as it was (None: not changed), the icons' CSS with what it holds, and the sizes for
# what Baude draws itself: the text's, and an icon's in px (16 at 1.0)
_scales = widgets.scales  # (one dict: widgets' RefIcon is the icons' size too)
_scales.update({"dpi": None, "icons": None, "icons_css": "", "text": 1.0, "icon_px": 16})


def apply_scales(display):
    """widgets.apply_scales (Settings → Size), Baude's spinners resized with the logos."""
    widgets.apply_scales(display, Ticker.spinning)


# ------------------------------------------------------- desktop launcher --
# Baude in the platform's list of apps, per user: Launcher, below.

def logo():
    """The logo (schema/icons/apps/baude.svg) in the accent picked in Settings: widgets.logo's."""
    return widgets.logo("baude")


def macos_icon_png(size=1024, svg=None):
    """The logo (svg: as it was in the accent when asked) as a PNG the way macOS draws app icons: with the margin its
    own icons have (the tile 824 of 1024)."""
    return applist.icon_png(svg or logo(), size)


def macos_dock_image(svg=None):
    """The logo drawn at 1024 px (macos_icon_png) as an NSImage, through the Objective-C runtime, as there's no PyObjC.
    No GTK and no main-thread AppKit call: it runs on a worker thread."""
    import ctypes
    png = macos_icon_png(svg=svg)
    data = objc_send(b"NSData", b"dataWithBytes:length:", png, len(png), types=(ctypes.c_char_p, ctypes.c_ulong))
    return objc_send(objc_send(b"NSImage", b"alloc"), b"initWithData:", data, types=(ctypes.c_void_p,))


def set_dock_image(image):
    """The Dock's tile an NSImage (-[NSApplication setApplicationIconImage:]), on the main thread."""
    import ctypes
    objc_send(objc_send(b"NSApplication", b"sharedApplication"), b"setApplicationIconImage:", image,
              types=(ctypes.c_void_p,))


def macos_dock_icon():
    """On macOS, the Dock shows the logo instead of Python's rocket: a Python process's Dock tile is Python.app's, and
    GTK's icon names don't reach it. Drawn and set at start; as the accent changes it's drawn on a worker thread
    (redraw_logo)."""
    set_dock_image(macos_dock_image())


def logo_icon():
    """Elsewhere, windows take the logo from the icon theme by name (APP_ID): it's put in an icon folder of Baude's
    own, as Record's is. The user's theme has it only once Baude's in the application menu, and without it a title bar
    that shows the window's icon (KDE's, at the start of the header bar) had the theme's missing-image icon. Called
    again as the accent changes (follow_accent): the theme's looked through again, and the windows take it anew."""
    return theming.theme_logo(os.path.join(_data_home(), "baude", "icons"), APP_ID, logo())


def follow_accent():
    """The logo in the accent as it's picked (theming.follow_accent: redraw_logo), and the launcher's now (the accent
    may have changed in Record since Baude last ran)."""
    theming.follow_accent(redraw_logo)
    GLib.idle_add(launcher_logo, priority=GLib.PRIORITY_LOW)


def redraw_logo():
    """The logo drawn again in the accent: the Dock's (macOS, drawn off the main thread) or the windows', and the
    launcher's (launcher_logo)."""
    svg = logo()
    if sys.platform == "darwin":
        theming.off_main("dock", lambda: macos_dock_image(svg), set_dock_image)
    else:
        try:
            logo_icon()
        except Exception as e:  # (an icon isn't worth the accent's change)
            print("baude: logo:", e, file=sys.stderr)
    launcher_logo()


def launcher_logo():
    """The launchers' logos that take the accent (Baude's and Record's, those installed) drawn again in it on a worker
    thread, where they aren't already."""
    where, accent = LAUNCHER.folder, widgets.load_settings().get("accent")
    theming.off_main("launcher", lambda: applist.redraw_logos(accent, where))
    return False


def launch_command(*args):
    """argv that starts baude: imported as a module, so Python uses its cached bytecode instead of
    compiling all of baude.py on every start (~150 ms; a script run directly is never cached)."""
    here = os.path.dirname(os.path.realpath(__file__))
    # (PYTHONDONTWRITEBYTECODE, inherited from some hosts, would leave the cache stale forever)
    boot = (f"import sys; sys.dont_write_bytecode = False; sys.path.insert(0, {here!r}); "
            "import baude; sys.exit(baude.main())")
    return [sys.executable, "-c", boot, *args]


class Launcher:
    """Baude, Record, Emission and Multiplex in the platform's list of apps, for this user, together (applist.APPS): on
    macOS ~/Applications/NAME.app each, which Spotlight and the Finder list, elsewhere a .desktop entry and the logo in
    the user's hicolor theme each. Each starts its command in ~/.local/bin (bins), written as they're installed. where
    names the list; folder is where it is when it isn't the user's (a test's or a rig's)."""
    where = applist.place()

    def __init__(self, folder=None):
        self.folder = folder

    def paths(self):
        """Each app's launcher, Baude's first."""
        return applist.all_paths(self.folder)

    def installed(self):
        return applist.all_installed(self.folder)

    def install(self):
        """All four in the list, the logos in the accent picked: Baude's launcher."""
        applist.install_all(widgets.load_settings().get("accent"), self.folder)
        return self.paths()[0]

    def uninstall(self):
        return applist.uninstall_all(self.folder)


LAUNCHER = Launcher()
