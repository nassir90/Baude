#!/usr/bin/env python3
"""Emission: Baude's browser, a GTK window of its own (and, later, one of Baude's side panel's pages): tabs, back and
forward, reload, an address that's a search too, and profiles, each its own cookies and logins kept (a login made once,
stored, as a Bot's identity will use it). Its pages are WebKit's: on macOS Apple's WKWebView (MacWeb), put in the
window through the Objective-C runtime (macos.objc_send: there's no PyObjC); on Linux WebKitGTK 6.0 (GtkWeb), a GTK
widget of its own. On a Linux host with no display it starts one of its own (xvfb-run).

Its parts: emissioncore.py (what they share), emissionmac.py (macOS's pages), emissiongtk.py (Linux's, a tab,
the bars), emissionpointer.py (the Bot's pointer), emissioncontrol.py (what the Bot's tools and watchers ask),
emissionwindow.py (the window). Here: its logo, its launcher, the app.

    python3 emission.py [URL] [--profile NAME]
    python3 emission.py --install | --uninstall   # app-menu launcher with the logo (per user)
"""
import argparse
import ctypes
import os
import shutil
import sys

import macos  # noqa: E402  (no GTK: before it's loaded)
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from baudelib import applist  # noqa: E402  (Emission in the list of apps, as Record is)
from baudelib import bins  # noqa: E402  (~/.local/bin/emission, which its launcher starts)
from baudelib import icons  # noqa: E402  (its logo: schema/icons)
from baudelib import wording  # noqa: E402  (its words, Baude's browser's: schema/copy)
from baudelib.emissionlink import (  # noqa
    DEFAULT, HELD, add_profile, bot_profile, clear_leftovers, data_home, hand_over, lock_profile, profiles,
)
from emissioncore import AGENT, APP_ID, HOME_PAGE, SCREEN  # noqa: E402
from emissionmac import MacWeb  # noqa: E402
from emissionwindow import Window, engine  # noqa: E402


# ---- its logo: Baude's and Record's tile, in a muted yellow (their terracotta's and teal's lightness and saturation),
# with a cream page on it (their bubble's, without its tail) and a globe cut out of the page through a mask, so every
# cut is the tile itself: a meridian and two parallels. At 16 px, the same a pixel wide, on whole pixels (ICON_SVG_16).

ICON_SVG = icons.svg("apps", "emission")  # (schema/icons/apps)
ICON_SVG_16 = icons.svg("apps", "emission-16")


def icon_png(size=1024, mac=True):
    """The logo as a PNG; mac: with the margin macOS's own icons have (the tile 824 of 1024)."""
    return applist.icon_png(ICON_SVG, size, mac)


def dock_icon():
    """macOS: the Dock shows the logo, not Python's rocket (GTK's icon names don't reach a Python process's tile)."""
    png = icon_png()
    send = macos.objc_send
    data = send(b"NSData", b"dataWithBytes:length:", png, len(png), types=(ctypes.c_char_p, ctypes.c_ulong))
    image = send(send(b"NSImage", b"alloc"), b"initWithData:", data, types=(ctypes.c_void_p,))
    send(send(b"NSApplication", b"sharedApplication"), b"setApplicationIconImage:", image, types=(ctypes.c_void_p,))


def themed_icon():
    """Elsewhere, windows take the logo from the icon theme by name (APP_ID): it's put in an icon folder of
    Emission's own (16 px's its own drawing), found there whether or not Emission's in the application menu. Once a
    display (each window asks). Its path (None: not written)."""
    display = Gdk.Display.get_default()
    if display is not None and getattr(display, "emission_icon", None):
        return display.emission_icon
    root, path = os.path.join(data_home(), "icons"), None
    try:
        for size, svg in (("scalable", ICON_SVG), ("16x16", ICON_SVG_16)):
            there = os.path.join(root, "hicolor", size, "apps", f"{APP_ID}.svg")
            if not os.path.exists(there) or open(there).read() != svg:
                os.makedirs(os.path.dirname(there), exist_ok=True)
                with open(there, "w") as f:
                    f.write(svg)
            path = path or there
    except OSError:
        return None
    if display is not None:
        Gtk.IconTheme.get_for_display(display).add_search_path(root)
        display.emission_icon = path
    return path


def install():
    """Emission in the platform's list of apps, for this user (applist): macOS's ~/Applications/Emission.app, Linux's
    .desktop entry and logo, which start ~/.local/bin/emission, written with the other commands (bins.install). What
    it wrote."""
    return bins.install() + applist.install(APP_ID, "Emission", ICON_SVG, [bins.path("emission"), "%u"],
                           "public.app-category.productivity",
                           ["GenericName=Web Browser", "Comment=Baude's browser", "Categories=Network;WebBrowser;GTK;"],
                           small=ICON_SVG_16)


def uninstall():
    """Emission's launcher removed: what was there."""
    return applist.uninstall(APP_ID, "Emission")


class App(Adw.Application):
    def __init__(self, url, profile, agent=False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.url, self.profile, self.agent = url, profile, agent

    def do_startup(self):
        Adw.Application.do_startup(self)
        if self.agent:  # (never seen: no logo, no look)
            return
        GLib.set_application_name("Emission")
        import theming
        theming.load_css()  # (Baude's look: the frame, the inside set into it)
        theming.theme_css()  # (whose stylesheet that's drawn over: libadwaita's, or a GTK_THEME's)
        if sys.platform == "darwin":
            try:
                dock_icon()
            except Exception as e:  # (Python's Dock tile isn't worth not starting over)
                print("emission: dock_icon:", e, file=sys.stderr)
        elif themed_icon() is not None:
            Gtk.Window.set_default_icon_name(APP_ID)

    def do_activate(self):
        Window(self, self.url, self.profile, self.agent).present()


def with_display(own=False):
    """On Linux with no display (or, own, whatever there is: an agent's Emission isn't put on anyone's screen), this
    run again under a virtual one of its own (xvfb-run), or why it can't be."""
    if sys.platform == "darwin" or os.environ.get("EMISSION_VIRTUAL_DISPLAY") or not own and (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    if shutil.which("xvfb-run") is None:
        return "There's no display here, and xvfb-run (package xvfb) isn't installed to make one."
    env = {**os.environ, "EMISSION_VIRTUAL_DISPLAY": "1", "GDK_BACKEND": "x11"}
    os.execvpe("xvfb-run", ["xvfb-run", "--auto-servernum", f"--server-args=-screen 0 {SCREEN[0]}x{SCREEN[1]}x24", sys.executable,
                            os.path.abspath(__file__), *sys.argv[1:]], env)


def main():
    p = argparse.ArgumentParser(description="Emission, Baude's browser")
    p.add_argument("url", nargs="?")
    p.add_argument("--profile", default=DEFAULT, help="whose cookies and logins (a profile of its own each)")
    p.add_argument("--bot", help="a Bot's chat id: its own profile (made if it has none)")
    p.add_argument("--name", help="the Bot's name, with --bot")
    p.add_argument("--agent", action="store_true", help="for the Bot's tools: out of sight, gone when idle")
    inst = p.add_mutually_exclusive_group()
    where = wording.say("install.applications") if sys.platform == "darwin" else \
        wording.say("install.application_menu")
    inst.add_argument("--install", action="store_true",
                      help=wording.say("install.install_help", app="Emission", where=where))
    inst.add_argument("--uninstall", action="store_true",
                      help=wording.say("install.uninstall_help", app="Emission", where=where))
    a = p.parse_args()
    if a.install:
        print("\n".join(wording.say("install.installed", path=path) for path in install()))
        return 0
    if a.uninstall:
        print("\n".join(wording.say("install.removed", path=path) for path in uninstall())
              or wording.say("install.not_installed", app="Emission"))
        return 0
    if a.agent and not a.bot:
        p.error("--agent is for a Bot's Emission: --bot too")
    if (why := with_display(own=a.agent)) is not None:
        print(why, file=sys.stderr)
        return 1
    if a.bot:
        pid = bot_profile(a.bot, a.name)
        a.profile = next(p["name"] for p in profiles() if p["id"] == pid)
        if a.agent:
            if engine()[0] is None:
                print(engine()[1], file=sys.stderr)
                return 2
            if not lock_profile(pid):
                return 0  # (another Emission has it already: the tools reach that one)
            AGENT["on"] = True
            if sys.platform == "darwin":
                macos.stay_offscreen()
                MacWeb.stay_awake()
            return App(None, a.profile, agent=True).run([sys.argv[0]])
    if not a.profile.strip():
        p.error("a profile needs a name")
    pid = add_profile(a.profile.strip())
    clear_leftovers()
    if pid not in HELD and not lock_profile(pid) and hand_over(pid, [a.url or HOME_PAGE]):
        print(f"Opened in the Emission that has the profile “{a.profile.strip()}”.")
        return 0
    return App(a.url, a.profile.strip()).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
