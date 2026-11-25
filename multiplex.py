#!/usr/bin/env python3
"""Multiplex: Baude's terminal, a GTK window of its own: tabs of terminals (multiplexterm.Terminal, VTE's), each one a
Bot can find, read and type into with its terminal tools (baudelib.multiplextools), its edges glowing while it does.
A Bot's open starts it if it isn't running; on a Linux host with no display it starts one of its own (xvfb-run).

    python3 multiplex.py [--cwd DIR] [--title TITLE] [-- COMMAND ...]
    python3 multiplex.py --install | --uninstall   # all four apps in the app menu, with their logos (per user)
"""
import argparse
import ctypes
import os
import sys

import macos  # noqa: E402  (no GTK: before it's loaded)
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402
from baudelib import applist, icons, multiplexlink, wording  # noqa: E402
import multiplexterm  # noqa: E402
from multiplexterm import Terminal, Terminals  # noqa: E402

APP_ID = "dev.baude.Multiplex"
ICON_SVG = icons.svg("apps", "multiplex")  # (schema/icons/apps: Emission's tile in green, a prompt cut from its page)
ICON_SVG_16 = icons.svg("apps", "multiplex-16")
SCREEN = (1280, 800)  # (a display of its own's size, on a host with none)


def data_home():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "multiplex")


def dock_icon():
    """macOS: the Dock shows the logo, not Python's rocket."""
    png = applist.icon_png(ICON_SVG, 1024, True)
    send = macos.objc_send
    data = send(b"NSData", b"dataWithBytes:length:", png, len(png), types=(ctypes.c_char_p, ctypes.c_ulong))
    image = send(send(b"NSImage", b"alloc"), b"initWithData:", data, types=(ctypes.c_void_p,))
    send(send(b"NSApplication", b"sharedApplication"), b"setApplicationIconImage:", image, types=(ctypes.c_void_p,))


def themed_icon():
    """Elsewhere, the logo by name (APP_ID) from an icon folder of Multiplex's own."""
    root = os.path.join(data_home(), "icons")
    try:
        for size, svg in (("scalable", ICON_SVG), ("16x16", ICON_SVG_16)):
            there = os.path.join(root, "hicolor", size, "apps", f"{APP_ID}.svg")
            if not os.path.exists(there) or open(there).read() != svg:
                os.makedirs(os.path.dirname(there), exist_ok=True)
                with open(there, "w") as f:
                    f.write(svg)
    except OSError:
        return False
    if (display := Gdk.Display.get_default()) is not None:
        Gtk.IconTheme.get_for_display(display).add_search_path(root)
    return True


def install():
    """Baude, Record, Emission and Multiplex in the platform's list of apps, for this user, together
    (applist.install_all), each starting its command in ~/.local/bin, written with the others (bins.install). What it
    wrote."""
    import widgets  # (the accent Baude's and Record's logos take)
    return applist.install_all(widgets.load_settings().get("accent"))


def uninstall():
    """All four apps' launchers removed: what was there."""
    return applist.uninstall_all()


class Window(Adw.ApplicationWindow):
    """Multiplex's window: its tabs, a terminal each; a program's ending closes its tab, unless a Bot's at it: that stays to be read
    till it's closed."""

    def __init__(self, app):
        super().__init__(application=app, title="Multiplex", default_width=900, default_height=580)
        self.tabs = Adw.TabView(hexpand=True, vexpand=True)
        self.tabs.connect("close-page", self._closing)
        self.tabs.connect("notify::selected-page", lambda *_: self._focus())
        bar = Adw.TabBar(view=self.tabs, autohide=True)  # (one tab: no strip, as Emission's window)
        head = Adw.HeaderBar()
        add = Gtk.Button(icon_name="tab-new-symbolic", tooltip_text=wording.say("browser.new_tab"))
        add.connect("clicked", lambda *_: self.open())
        head.pack_start(add)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)  # (its terminals set into the frame, as Emission's pages)
        body.append(self.tabs)
        import widgets
        widgets.inside(body, under_bar=True)
        view = Adw.ToolbarView(content=body)
        view.add_css_class("baude-frame")
        view.add_top_bar(head)
        view.add_top_bar(bar)
        self.set_content(view)
        prefix = "<Meta>" if sys.platform == "darwin" else "<Control><Shift>"
        for name, keys, fn in (("new-tab", [prefix + "t"], lambda: self.open()),
                               ("close-tab", [prefix + "w"], self.close_tab)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, fn=fn: fn())
            self.add_action(act)
            app.set_accels_for_action(f"win.{name}", keys)
        self.connect("close-request", self._close_all)

    def open(self, argv=None, cwd=None, title=None, show=True):
        """A new tab running argv (your shell if none) in cwd: its terminal."""
        term = Terminal(argv, cwd, title, on_exit=self._ended, on_title=self._titled,
                    on_close=self._finish)
        Terminals.get().add(term)
        page = self.tabs.append(term)
        page.set_title(self.label(term))
        page.set_tooltip(term.id)
        self.tabs.set_selected_page(page)
        term.vte.grab_focus()
        if show:
            self.present()
        return term

    def page_of(self, term):
        for i in range(self.tabs.get_n_pages()):
            if self.tabs.get_nth_page(i).get_child() is term:
                return self.tabs.get_nth_page(i)
        return None

    def label(self, term):
        return term.title() + (f" ({wording.say('multiplex.ended')})" if term.status is not None else "")

    def _titled(self, term):
        if (page := self.page_of(term)) is not None:
            page.set_title(self.label(term))

    def _ended(self, term, _status):
        if term.closing or term.bot is None:
            self._finish(term)
        else:
            self._titled(term)

    def _finish(self, term):
        if (page := self.page_of(term)) is not None:
            self.tabs.close_page(page)

    def _focus(self):
        if (page := self.tabs.get_selected_page()) is not None:
            page.get_child().vte.grab_focus()

    def _closing(self, tabs, page):
        term = page.get_child()
        term.stop()
        Terminals.get().remove(term)
        tabs.close_page_finish(page, True)
        if tabs.get_n_pages() == 0:
            self.get_application().quit()
        return True

    def close_tab(self):
        if (page := self.tabs.get_selected_page()) is not None:
            self.tabs.close_page(page)

    def _close_all(self, *_):
        for term in list(Terminals.get().terminals.values()):
            term.stop()
        Terminals.get().close()
        self.get_application().quit()
        return False


class App(Adw.Application):
    def __init__(self, argv, cwd, title, empty=False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.first, self.empty, self.window = (argv, cwd, title), empty, None

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name("Multiplex")
        import theming
        theming.load_css()
        theming.theme_css()
        if sys.platform == "darwin":
            try:
                dock_icon()
            except Exception as e:  # (Python's Dock tile isn't worth not starting over)
                print("multiplex: dock_icon:", e, file=sys.stderr)
        elif themed_icon():
            Gtk.Window.set_default_icon_name(APP_ID)
        self.hold()  # (started for a Bot with nothing in it yet: kept till its first tab, and its last's closing)
        Terminals.get("multiplex", self.opener).listen()
        if self.empty:  # (no tab asked for a minute on, after all: gone)
            GLib.timeout_add_seconds(60, lambda: (self.window is None and self.quit(), False)[1])

    def opener(self, argv, cwd, title, show):
        if self.window is None:
            self.window = Window(self)
            self.window.connect("destroy", lambda *_: setattr(self, "window", None))
        return self.window.open(argv, cwd, title, show=show or not self.window.get_visible())

    def do_activate(self):
        if not self.empty:
            self.opener(*self.first, True)


def with_display():
    """On Linux with no display, this run again under a virtual one of its own (xvfb-run), or why it can't be."""
    if sys.platform == "darwin" or os.environ.get("MULTIPLEX_VIRTUAL_DISPLAY") or \
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return None
    from baudelib import deps
    if (missing := deps.missing("xvfb")) is not None:  # (no display here, and nothing to make one with)
        return deps.say(missing, "multiplex.terminal_menu")
    env = {**os.environ, "MULTIPLEX_VIRTUAL_DISPLAY": "1", "GDK_BACKEND": "x11"}
    os.execvpe("xvfb-run", ["xvfb-run", "--auto-servernum", f"--server-args=-screen 0 {SCREEN[0]}x{SCREEN[1]}x24",
                            sys.executable, os.path.abspath(__file__), *sys.argv[1:]], env)


def main():
    p = argparse.ArgumentParser(description="Multiplex, Baude's terminal")
    p.add_argument("command", nargs="*", help="what to run (your shell if nothing)")
    p.add_argument("--cwd", help="the folder it starts in")
    p.add_argument("--title", help="its tab's title, till its program names it")
    p.add_argument("--empty", action="store_true", help="no tab yet (a Bot's open makes its first)")
    inst = p.add_mutually_exclusive_group()
    where = wording.say("install.applications") if sys.platform == "darwin" else \
        wording.say("install.application_menu")
    inst.add_argument("--install", action="store_true",
                      help=wording.say("install.install_help", where=where))
    inst.add_argument("--uninstall", action="store_true",
                      help=wording.say("install.uninstall_help", where=where))
    argv = sys.argv[1:]
    if "-ApplePersistenceIgnoreState" in argv:  # (macOS's, for AppKit: not Multiplex's)
        at = argv.index("-ApplePersistenceIgnoreState")
        del argv[at:at + 2]
    a = p.parse_args(argv)
    if a.install:
        print("\n".join(wording.say("install.installed", path=path) for path in install()))
        return 0
    if a.uninstall:
        print("\n".join(wording.say("install.removed", path=path) for path in uninstall())
              or wording.say("install.not_installed"))
        return 0
    if (why := multiplexterm.available()) is not None:
        print(why, file=sys.stderr)
        return 2
    if (why := with_display()) is not None:
        print(why, file=sys.stderr)
        return 1
    cwd = os.path.abspath(os.path.expanduser(a.cwd)) if a.cwd else os.getcwd()
    if not a.empty and multiplexlink.hand_over(a.command or None, cwd, a.title):
        return 0
    return App(a.command or None, cwd, a.title, empty=a.empty).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
