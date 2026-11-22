"""startup — Baude as it starts: its app id, start-up milestones (trace, BAUDE_TRACE), the state a restart leaves
for the next process (take_restart_state), the app's sizes (apply_scales), and Baude in the platform's list of apps:
its logo (ICON_SVG), the command that starts it (launch_command) and the launchers (LAUNCHER).
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import time

from macos import objc_send
from baudelib import icons  # noqa: E402  (its logo: schema/icons)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
from widgets import Ticker  # noqa: E402,F401
from widgets import _settings_path  # noqa: E402,F401
from pickers import _data_home  # noqa: E402,F401

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

ICON_SVG = icons.svg("apps", "baude")  # (schema/icons/apps)


def macos_icon_png(size=1024):
    """The logo (ICON_SVG) as a PNG the way macOS draws app icons: with the margin its own icons have (the tile 824
    of 1024)."""
    k = 112 * 1024 / 824  # (the tile is 112 of the SVG's 128)
    svg = ICON_SVG.replace('viewBox="0 0 128 128"', f'viewBox="{64 - k / 2} {64 - k / 2} {k} {k}"')
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(size, size)
    loader.write(svg.encode())
    loader.close()
    return loader.get_pixbuf().save_to_bufferv("png", [], [])[1]


def macos_dock_icon():
    """On macOS, the Dock shows the logo (ICON_SVG) instead of Python's rocket: a Python process's Dock tile is
    Python.app's, and GTK's icon names don't reach it. Drawn at 1024 px (macos_icon_png), and handed to AppKit
    (-[NSApplication setApplicationIconImage:]) through the Objective-C runtime, as there's no PyObjC."""
    import ctypes
    png = macos_icon_png()
    data = objc_send(b"NSData", b"dataWithBytes:length:", png, len(png), types=(ctypes.c_char_p, ctypes.c_ulong))
    image = objc_send(objc_send(b"NSImage", b"alloc"), b"initWithData:", data, types=(ctypes.c_void_p,))
    objc_send(objc_send(b"NSApplication", b"sharedApplication"), b"setApplicationIconImage:", image,
              types=(ctypes.c_void_p,))


def logo_icon():
    """Elsewhere, windows take the logo (ICON_SVG) from the icon theme by name (APP_ID): it's put in an icon folder of
    Baude's own, as Record's is. The user's theme has it only once Baude's in the application menu, and without it a
    title bar that shows the window's icon (KDE's, at the start of the header bar) had the theme's missing-image icon."""
    root = os.path.join(_data_home(), "baude", "icons")
    path = os.path.join(root, "hicolor", "scalable", "apps", f"{APP_ID}.svg")
    try:
        if not os.path.exists(path) or open(path).read() != ICON_SVG:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(ICON_SVG)
    except OSError:
        return None
    Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(root)
    return path


def launch_command(*args):
    """argv that starts baude: imported as a module, so Python uses its cached bytecode instead of
    compiling all of baude.py on every start (~150 ms; a script run directly is never cached)."""
    here = os.path.dirname(os.path.realpath(__file__))
    # (PYTHONDONTWRITEBYTECODE, inherited from some hosts, would leave the cache stale forever)
    boot = (f"import sys; sys.dont_write_bytecode = False; sys.path.insert(0, {here!r}); "
            "import baude; sys.exit(baude.main())")
    return [sys.executable, "-c", boot, *args]


class Launcher:
    """Baude in the platform's list of apps, for this user: LAUNCHER is this platform's. paths() are the files it
    writes, the first the one install() returns; where names the list."""
    where = ""

    def paths(self):
        raise NotImplementedError

    def write(self):
        raise NotImplementedError

    def refresh(self):
        """Tells the platform the files changed (optional: it notices anyway)."""

    def installed(self):
        return os.path.exists(self.paths()[0])

    def install(self):
        self.write()
        self.refresh()
        return self.paths()[0]

    def uninstall(self):
        removed = []
        for path in self.paths():
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
            else:
                continue
            removed.append(path)
        self.refresh()
        return removed


class FreedesktopLauncher(Launcher):
    """Linux: a .desktop entry in $XDG_DATA_HOME/applications and an icon in the user's hicolor theme, as the
    freedesktop specs have it."""
    where = wording.say("install.application_menu")

    def paths(self):
        return [os.path.join(_data_home(), "applications", f"{APP_ID}.desktop"),
                os.path.join(_data_home(), "icons", "hicolor", "scalable", "apps", f"{APP_ID}.svg")]

    def write(self):
        entry = "\n".join([
            "[Desktop Entry]",
            "Type=Application",
            "Name=Baude",
            "GenericName=Claude Code client",
            "Comment=GTK front-end for the claude CLI",
            "Exec=" + " ".join(_exec_quote(a) for a in launch_command()),
            f"Icon={APP_ID}",
            "Terminal=false",
            "Categories=Development;GTK;",
            "Keywords=claude;ai;agent;chat;",
            "StartupNotify=true",
            f"StartupWMClass={APP_ID}",
            "",
        ])
        desktop, icon = self.paths()
        for path, data in ((icon, ICON_SVG), (desktop, entry)):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(data)

    def refresh(self):
        # (menus watch the folder anyway; this refreshes the MIME cache)
        if shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", os.path.dirname(self.paths()[0])],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


MACOS_LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework" \
    "/Support/lsregister"


class MacLauncher(Launcher):
    """macOS: an app bundle, ~/Applications/Baude.app, which Spotlight and the Finder list. Its
    executable is a script that starts baude (launch_command) through the user's login shell, as an app opened
    from the Finder gets launchd's bare PATH, without Homebrew's tools. Its icon is an .icns of PNGs (the
    format's ic07 to ic10 entries, 128 to 1024 px)."""
    where = wording.say("install.applications")

    def __init__(self, folder=None):
        self.folder = folder or os.path.expanduser("~/Applications")

    def paths(self):
        return [os.path.join(self.folder, "Baude.app")]

    @staticmethod
    def icns():
        entries = b"".join(kind + struct.pack(">I", 8 + len(png)) + png
                           for kind, png in ((k, macos_icon_png(n)) for k, n in
                                             ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024))))
        return b"icns" + struct.pack(">I", 8 + len(entries)) + entries

    def write(self):
        import plistlib
        import shlex
        contents = os.path.join(self.paths()[0], "Contents")
        os.makedirs(os.path.join(contents, "MacOS"), exist_ok=True)
        os.makedirs(os.path.join(contents, "Resources"), exist_ok=True)
        with open(os.path.join(contents, "Info.plist"), "wb") as f:
            plistlib.dump({
                "CFBundleName": "Baude",
                "CFBundleDisplayName": "Baude",
                "CFBundleIdentifier": APP_ID,
                "CFBundlePackageType": "APPL",
                "CFBundleExecutable": "Baude",
                "CFBundleIconFile": "Baude",
                "LSApplicationCategoryType": "public.app-category.developer-tools",
                "NSHighResolutionCapable": True,
            }, f)
        with open(os.path.join(contents, "Resources", "Baude.icns"), "wb") as f:
            f.write(self.icns())
        script = os.path.join(contents, "MacOS", "Baude")
        with open(script, "w") as f:
            f.write("#!/bin/sh\n"
                    'cd "$HOME"\n'  # (the Finder starts apps in /; Linux's launchers in your home)
                    'exec "${SHELL:-/bin/zsh}" -lc \'exec "$0" "$@"\' '
                    + " ".join(shlex.quote(a) for a in launch_command()) + ' "$@"\n')
        os.chmod(script, 0o755)

    def refresh(self):
        # (LaunchServices and Spotlight find the bundle in time, a minute or more; this lists it now, for ⌘Space too)
        path = self.paths()[0]
        if os.path.exists(MACOS_LSREGISTER):
            subprocess.run([MACOS_LSREGISTER, "-f" if os.path.exists(path) else "-u", path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if os.path.exists(path) and shutil.which("mdimport"):
            subprocess.run(["mdimport", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


LAUNCHER = MacLauncher() if sys.platform == "darwin" else FreedesktopLauncher()
