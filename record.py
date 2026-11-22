#!/usr/bin/env python3
"""record — notes, and Meta beside them, which reads them, and AI-written prose, with you.

Each notebook is a glossary, kept as notes: wiki pages (Markdown, [[links]] between them, @name for a page, a notebook
or anything Baude's ⌘K finds) of the things you already understand, in your own words. Meta, in the right bar, is
the helper that operates Record (meta.py: Claude Code, run as `claude -p`, Haiku unless Settings → Meta says another),
one conversation for the window, whichever notebook's open:
  * it reads the notebook's pages and folders, and whatever's @-referenced in what you ask (a page, a notebook, a file,
    a chat: its text goes with your message), and remembers the conversation;
  * a passage selected on a page, or picked in a chat read, is quoted with what you ask: asked to fill it in or change
    it, it proposes the change, shown on the page as a diff for you to accept or reject. It never writes your notes;
  * it adds, deletes and moves pages, renames chats and changes settings, each an action you can undo, the pages it
    changed marked in the pages' list; asked about a setting, it shows the setting's row;
  * asked what a chat's passage means, it explains it through your notes, and names what it would need to know when
    they aren't enough.

Records are listed on the left. Each has its own notes and folders, whose names @ and ⌘K know. Read shows the
Claude Code chat last opened (an @chat in the notes, ⌘K, Open a Chat…, Baude's Open in Record; any transcript
Claude Code keeps, live ones included), whichever notebook's open: you select a passage and ask about it.

    python3 record.py
    python3 record.py --chat TRANSCRIPT.jsonl   # reading that chat (Baude's menu → Open in Record does this)
    python3 record.py --install | --uninstall   # app-menu launcher with the logo (per user), and ~/.local/bin's commands

Record is part of Baude: it lives beside baude.py and searches the chat read's folder with lookup.py, the engine behind
Baude's ⌘K, for @ in the diary, for the names linked in the chat, and for what its questions say those names are.
One Record runs at a time: started again, it hands what it was asked to the one running.

Meta's session is `claude -p` speaking stream-json (baudelib's ClaudeProc), its tools Read, Grep and Glob and its own
(metaagent.SHARED_TOOLS, RECORD_TOOLS), in Claude Code's default permission mode. Set RECORD_CLAUDE=/path/to/claude to use a specific claude
binary (else claude on PATH, else, on macOS, the Claude desktop app's). Records are saved in
$XDG_DATA_HOME/record/records.json (macOS: ~/Library/Application Support/Record; RECORD_DATA=dir puts them somewhere
else).

Its parts: the notebooks, without a window, in recordstore.py; what Meta is told and proposes in recordmeta.py; the
pieces under the window in recordui.py; a page of notes in diaryeditor.py (⌘F and @ in diaryfind.py); the left column
and the chat read in recordreader.py; Meta's column in recordchat.py (its tools in recordtools.py); the window in
recordwindow.py (its hosts in recordhosts.py).
"""
import json
import os
import sys
import threading

import macos  # (no GTK: before it's loaded)

macos.full_redraws()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402
import pointers  # noqa: E402  (the pointer's shape: beside this file)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; beside this file)
import pickers  # noqa: E402
import theming  # noqa: E402
from baudelib import wording  # noqa: E402  (its words, Baude's: schema/copy)
import fontsetup  # noqa: E402  (its fonts, as Baude's: set up before Pango's first used)
fontsetup.setup()
import baudelib.client  # noqa: E402  (the hosts, as Baude has them: another's notebooks, through its host process)
import baudelib.hosts  # noqa: E402  (this machine's host process started again: Restart Daemon)
import baudelib.serverstate  # noqa: E402  (the sessions this machine's host process keeps: bot_started)
from baudelib import applist  # noqa: E402  (Record in the list of apps, as Emission is)
from baudelib import bins  # noqa: E402  (the commands in ~/.local/bin, kept up to date as it starts)
from baudelib import notes  # noqa: E402  (this machine's notebooks, as its host process gives them to another's Record)
from baudelib.notes import data_dir, socket_path  # noqa: E402,F401
import update  # noqa: E402  (a new version from git, restarted onto once idle, as Baude's: beside this file)
baudelib.wire.HEAD_COMMIT = update.STARTED  # (said to the host processes it reaches: logged by one on other code)
baudelib.wire.HEAD_CLIENT = "record"  # (listed as Record among the apps connected to a host)
from recordstore import APP_ID, apps_place, install, logo, noun, restart_path, uninstall  # noqa: E402
from recordui import CSS, UNEXPLAINED  # noqa: E402
from recordwindow import Window  # noqa: E402


# ---------------------------------------------------------------- the logo

def icon_png(size=1024, macos=True, svg=None):
    """The logo (in the accent; svg: as it was when asked) as a PNG; for macOS with the margin its own icons have (the
    tile 824 of 1024)."""
    return applist.icon_png(svg or logo(), size, macos)


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


def macos_app_name():
    """The menu bar says Record rather than Python: the bundle's name is read when the app
    starts, so it's renamed in memory before GTK starts it (and stays what it was then)."""
    import ctypes

    def ns(text):
        return objc_send(b"NSString", b"stringWithUTF8String:", text.encode(), types=(ctypes.c_char_p,))
    bundle = objc_send(b"NSBundle", b"mainBundle")
    for which in (b"infoDictionary", b"localizedInfoDictionary"):
        if info := objc_send(bundle, which):
            objc_send(info, b"setObject:forKey:", ns(noun()), ns("CFBundleName"),
                      types=(ctypes.c_void_p, ctypes.c_void_p))


def macos_dock_image(svg=None):
    """The logo as an NSImage. No GTK and no main-thread AppKit call: it runs on a worker thread."""
    import ctypes
    png = icon_png(svg=svg)
    data = objc_send(b"NSData", b"dataWithBytes:length:", png, len(png), types=(ctypes.c_char_p, ctypes.c_ulong))
    return objc_send(objc_send(b"NSImage", b"alloc"), b"initWithData:", data, types=(ctypes.c_void_p,))


def set_dock_image(image):
    """The Dock's tile an NSImage, on the main thread."""
    import ctypes
    objc_send(objc_send(b"NSApplication", b"sharedApplication"), b"setApplicationIconImage:", image,
              types=(ctypes.c_void_p,))


def macos_dock_icon():
    """The Dock shows the logo instead of Python's rocket (GTK's icon names don't reach a Python process's tile)."""
    set_dock_image(macos_dock_image())


def themed_icon():
    """Elsewhere, windows take the logo from the icon theme: it's put in an icon folder of Record's own
    (theming.theme_logo)."""
    path = theming.theme_logo(os.path.join(data_dir(), "icons"), APP_ID, logo())
    if path is not None:
        Gtk.Window.set_default_icon_name(APP_ID)
    return path


def redraw_logo():
    """The logo drawn again in the accent as it's picked (theming.follow_accent): the Dock's (drawn off the main
    thread) or the windows', and the launcher's if Record's in the list of apps."""
    svg = logo()
    if sys.platform == "darwin":
        theming.off_main("dock", lambda: macos_dock_image(svg), set_dock_image)
    else:
        try:
            themed_icon()
        except Exception as e:  # (an icon isn't worth the accent's change)
            print("record: logo:", e, file=sys.stderr)
    launcher_logo(svg)


def launcher_logo(svg=None):
    """The launcher's logo drawn again in the accent on a worker thread, where it isn't already
    (applist.redraw_logo)."""
    svg = svg or logo()
    theming.off_main("launcher", lambda: applist.redraw_logo(APP_ID, "Record", svg))
    return False


def hand_over(request, path=None):
    """Record is running already: hand it the request ({"chat": path} or {}). False if none is. (path: another
    socket's)"""
    import socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.settimeout(2)
            c.connect(path or socket_path())
            c.sendall(json.dumps(request).encode() + b"\n")
            return c.recv(16) == b"ok\n"
    except OSError:
        return False


def listen(on_request):
    """Be the Record that's running: a later start's request goes to on_request(dict), on the main loop. (Two would
    each save records.json over the other's.) So does a change to this machine's notebooks from another's Record
    ({"notes": changes}, from the host process here: notes.write): answered "notes" once on_request has made it (it
    says True), so it's in records.json when the host process next lists them; "no" if it couldn't be, in time."""
    import socket
    path = socket_path()
    if os.path.exists(path):
        os.remove(path)  # (left by a Record that's gone: hand_over couldn't reach it)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(4)

    def serve():
        while True:
            conn, _ = server.accept()
            with conn:
                try:
                    request = json.loads(conn.makefile().readline() or "{}")
                    if not isinstance(request, dict):
                        continue
                    if "notes" in request:
                        made, ready = [], threading.Event()
                        if notes.changes_ok(request["notes"]):
                            GLib.idle_add(lambda r=request: (made.append(on_request(r)), ready.set()) and False)
                        ready.wait(4)  # (within notes.write's wait: told no, it's sent again later)
                        conn.sendall(b"notes\n" if made and made[0] else b"no\n")
                        continue
                    conn.sendall(b"ok\n")
                except (OSError, ValueError):
                    continue
            GLib.idle_add(lambda r=request: on_request(r) and False)
    threading.Thread(target=serve, daemon=True, name="record-listen").start()
    return server


class App(Adw.Application):
    def __init__(self, chat=None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        GLib.set_application_name(noun())
        self.chat, self.server, self.win = chat, None, None
        self.updater = None  # (update.Updater: started with the window)

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.do_startup_look()
        widgets.ensure_icons()
        pointers.start()
        pickers.press_opens_dropdowns()  # (a dropdown's list open as the mouse goes down on it)
        theming.theme_css(CSS)  # (whose stylesheet Baude's look is drawn over: libadwaita's, or a GTK_THEME's)
        import signal  # (told to stop, as logging out or `kill` does: what's typed is saved, then it stops)
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            try:
                gi.require_version("GLibUnix", "2.0")
                from gi.repository import GLibUnix
                GLibUnix.signal_add(GLib.PRIORITY_HIGH, sig, self._stop)
            except (ImportError, ValueError):  # older GLib
                GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, self._stop)

    def do_startup_look(self):
        """Light or dark as macOS is, as Baude follows it (theming.macos_appearance), and Baude's sizes."""
        if sys.platform == "darwin":
            try:
                theming.macos_appearance()
            except Exception as e:  # noqa: BLE001 (the look isn't worth not starting over)
                print("record: light and dark won't follow macOS:", e, file=sys.stderr)

    def _stop(self, *_):
        if self.win is not None:
            self.win.store.save_now()
        self.quit()
        return False

    def do_shutdown(self):
        if self.win is not None:
            self.win.store.save_now()  # (⌘Q, or the last window closed: what's typed is kept)
        UNEXPLAINED.stop()
        Adw.Application.do_shutdown(self)

    def settings_changed(self, keys, before):
        if self.win is not None:
            self.win.settings_changed(keys, before)

    def do_activate(self):
        theming.load_css()  # (Baude's: its compact lists, its viewers; its classes aren't Record's)
        widgets.apply_scales(Gdk.Display.get_default())  # (Settings → Size: the text's and the icons', Baude's)
        theming.apply_accent(Gdk.Display.get_default())  # (Settings → Accent Colour, Baude's)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if sys.platform == "darwin":
            try:
                macos_dock_icon()
            except Exception as e:
                print("record: no Dock icon:", e, file=sys.stderr)
        else:
            widgets.notify_as(themed_icon())
        theming.follow_accent(redraw_logo)  # (Settings → Accent Colour: the logo in it)
        GLib.idle_add(launcher_logo, priority=GLib.PRIORITY_LOW)  # (picked in Baude since Record last ran)
        try:  # (first: a change from another machine's Record waits for the window, not written under it: notes.write)
            self.server = listen(self.request)
        except OSError as e:
            print("record: another start won't find this one:", e, file=sys.stderr)
        self.win = Window(self)
        theming.flat_popovers(Gdk.Display.get_default())  # (X11 under picom: its shadow is round the whole popover)
        state = None
        try:  # (restarted, ☰ → Restart Record or a new version: what the one before showed)
            with open(restart_path()) as f:
                state = json.load(f)
            os.remove(restart_path())
            self.win.restore(state)
        except (OSError, ValueError):
            pass
        update.show(self.win, state if isinstance(state, dict) else None)
        if isinstance(state, dict):
            update.restarted(self.win, state, noun())
        theming.watch_settings(self.settings_changed)  # (changed in Baude, or another Record here)
        if self.updater is None:
            self.updater = update.Updater("record", lambda: self.win is None or self.win.idle(),
                                          lambda updated: self.win.restart(updated),
                                          blocked=lambda waiting: self.win is not None and self.win.update_waiting(waiting))
        if self.chat:
            self.win.open_chat(self.chat)

    def request(self, r):
        """A later start's request (listen), or another machine's change to the notebooks here: True if it's made."""
        if self.win is None:
            return False
        if "notes" in r:
            return self.win.notes_here(r["notes"])
        if r.get("chat"):
            self.win.open_chat(r["chat"])
        else:
            self.win.present()
        return True


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="record", description="Read AI prose with a peer who knows what you know.")
    ap.add_argument("--chat", help="a Claude Code transcript (.jsonl) to read")
    ap.add_argument("--install", action="store_true",
                    help=wording.say("install.install_help", app="Record", where=apps_place()))
    ap.add_argument("--uninstall", action="store_true",
                    help=wording.say("install.uninstall_help", app="Record", where=apps_place()))
    args = ap.parse_args()
    if args.install:
        print("\n".join(wording.say("install.installed", path=p) for p in [*map(bins.path, bins.NAMES), install()]))
        return 0
    if args.uninstall:
        print("\n".join(wording.say("install.removed", path=path) for path in uninstall())
              or wording.say("install.not_installed", app="Record"))
        return 0
    bins.refreshed(lambda text: print(f"record: {text}", file=sys.stderr))
    chat = os.path.realpath(args.chat) if args.chat else None
    request = {"chat": chat} if chat else {}
    if hand_over(request):
        return 0
    if sys.platform == "darwin":
        try:
            macos_app_name()
        except Exception as e:
            print("record: still named Python:", e, file=sys.stderr)
    return App(chat).run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
