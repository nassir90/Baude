#!/usr/bin/env python3
"""baude — a tiny GTK4 front-end for the `claude` CLI.

It spawns one long-lived `claude -p` process speaking the stream-json
protocol on stdin/stdout (the same protocol the Agent SDK uses) and renders
the events: streamed text, thinking, tool calls + results, permission
prompts, cost/usage. Everything "agentic" (tools, context, compaction,
MCP, hooks, CLAUDE.md...) is done by the CLI; baude is only a view.

    python3 baude.py [--cwd DIR] [--model MODEL] [--resume SESSION_ID]
    python3 baude.py --install | --uninstall   # all four apps in the app menu (per user), and ~/.local/bin's commands

Keys: Ctrl+Enter (⌘Enter) sends, Enter adds a line, Esc stops, Ctrl +/-/0 (⌘ too on macOS, or Ctrl+scroll)
zooms the text, Ctrl+V pastes images/files as attachments, Ctrl+N starts a new session, Ctrl+, (⌘,) opens Settings,
Ctrl+. (⌘.) Send Feedback over whatever's open, Ctrl+M (Control on macOS too) shows Meta, its box focused, or
closes it. Images and PDFs can also be attached with the paperclip or dropped onto the window.

Set BAUDE_CLAUDE=/path/to/claude to use a specific claude binary (else claude on PATH, else, on macOS,
the Claude desktop app's newest). One that's gone (an old desktop build, removed) is passed over, said on stderr.
"""
import argparse
import json
import os
import sys
import threading
import time
from macos import full_redraws, ns_string, objc_send
from baudelib import claude as claudelib  # (its ClaudeProc: Baude's is it, on the GTK main loop)
import baudelib.bins  # (the commands in ~/.local/bin, kept up to date as it starts: see baudelib/bins.py)
import baudelib.notifier  # (its notifications on macOS, posted by an app of its own: see baudelib/notifier.py)
import baudelib.client  # noqa: E402  (servers on the network and their sessions: see baudelib/client.py)
import baudelib.hosts  # noqa: E402  (this machine's host process, started when it's wanted: see baudelib/hosts.py)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)


def macos_fonts():
    """The fonts set up before Pango's loaded: text through FontConfig and FreeType on macOS, the code font Settings ->
    Code Font says, and the fonts bundled beside Baude (fontsetup)."""
    import fontsetup  # (no GTK: beside this file)
    fontsetup.setup()


def macos_app_name():
    """On macOS, the menu bar says Baude rather than Python: it names the app after its bundle (Python.app's),
    read when the app starts, so this renames the bundle in memory before GTK starts it (the CFBundleName of
    +[NSBundle mainBundle]'s info, as PyObjC apps do)."""
    import ctypes
    if sys.platform != "darwin":
        return
    try:
        bundle = objc_send(b"NSBundle", b"mainBundle")
        for which in (b"infoDictionary", b"localizedInfoDictionary"):
            if info := objc_send(bundle, which):
                objc_send(info, b"setObject:forKey:", ns_string("Baude"), ns_string("CFBundleName"),
                          types=(ctypes.c_void_p, ctypes.c_void_p))
    except Exception as e:
        print("baude: still named Python:", e, file=sys.stderr)


macos_fonts()
macos_app_name()
FULL_REDRAWS = full_redraws()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402
import pointers  # noqa: E402  (the pointer's shape: Record's too)
import update  # noqa: E402  (a new version from git, restarted onto once idle: Record's too)
baudelib.wire.HEAD_COMMIT = update.STARTED  # (said to the host processes it reaches: logged by one on other code)
baudelib.wire.HEAD_CLIENT = "baude"  # (listed as Baude among the apps connected to a host)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
import memreport  # noqa: E402  (what it holds, in its debug report)
import pickers  # noqa: E402
import theming  # noqa: E402
from widgets import ensure_icons  # noqa: E402,F401
from theming import macos_appearance, CSS  # noqa: E402,F401
from startup import (  # noqa: E402
    APP_ID, LAUNCHER, TRACE, follow_accent, launch_command, logo_icon, macos_dock_icon, restart_state_path,
    take_restart_state, trace,
)
from sessionlist import THIS_MACHINE, _NETWORK, local_host, save_info_cache  # noqa: E402
from baudewindow import BaudeWindow  # noqa: E402


trace("imported")


class BaudeApp(Adw.Application):
    def __init__(self, opts):
        # a traced run (a benchmark) is its own app: never handed to the baude you're using
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE if TRACE else Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.opts = opts
        self.updater = None  # (update.Updater: started with the first window)

    def do_shutdown(self):
        save_info_cache(force=True)
        if _NETWORK:
            _NETWORK[0].save()  # (the network's sessions as last seen: dead ones are shown from it next time)
        Adw.Application.do_shutdown(self)

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name("Baude")
        if sys.platform == "darwin":
            for fn in (macos_dock_icon, macos_appearance):
                try:
                    fn()
                except Exception as e:  # (Python's Dock tile isn't worth not starting over)
                    print(f"baude: {fn.__name__}:", e, file=sys.stderr)
            widgets.notify_as(app="Baude", opened=self.open_notified)
        else:
            widgets.notify_as(logo_icon())
        follow_accent()  # (the logo in the accent as it's picked: Settings → Accent Colour)
        ensure_icons()
        pointers.start()
        install_debug_hooks(self)
        widgets.free_closed_windows(self)
        pickers.press_opens_dropdowns()  # (a dropdown's list open as the mouse goes down on it)
        theming.theme_css()  # (whose stylesheet Baude's look is drawn over: libadwaita's, or a GTK_THEME's)

    def do_activate(self):
        trace("activate")
        Gtk.Window.set_default_icon_name(APP_ID)
        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        restore, self.opts.restore = take_restart_state(getattr(self.opts, "restore", None)), None  # (once)
        if restore is not None and "windows" not in restore:
            restore = {"windows": [restore]}  # (one window's, from before every window came back)
        BaudeWindow.restore_drafts((restore or {}).get("drafts"))  # (before the windows: each takes its chat's)
        for i, state in enumerate((restore or {}).get("windows") or [None]):
            opts = argparse.Namespace(**vars(self.opts))
            if state:
                opts.cwd, opts.restore = state.get("cwd") or opts.cwd, state
            win = BaudeWindow(self, opts)
            trace("window built")
            if TRACE and i == 0:
                win.connect("map", lambda *_: trace("window mapped"))
                clock_id = []

                def painted(clock):
                    trace("first frame")
                    clock.disconnect(clock_id[0])
                win.connect("realize", lambda w: clock_id.append(
                    w.get_frame_clock().connect("after-paint", painted)))
            update.show(win, restore)  # (in the order they were in: the last one you were in, last, on top)
        theming.flat_popovers(Gdk.Display.get_default())  # (X11 under picom: its shadow is round the whole popover)
        if restore:
            update.restarted(win, restore, "Baude")
        if self.updater is None and not TRACE:
            self.updater = update.Updater("baude", self.idle, self.restart, blocked=self.update_waiting)
        # (whether this claude knows --chrome, looked up now, off the main loop: a chat starts without waiting on it)
        threading.Thread(target=lambda: claudelib.has_flag(claudelib.claude_exe(), "--chrome"), daemon=True).start()

    def windows(self):
        """Baude's windows, the one you were in last first."""
        return [w for w in self.get_windows() if isinstance(w, BaudeWindow)]

    def open_notified(self, args):
        """A click on a notification this process didn't post (one from before a restart, or another Baude's): the
        window you were in last brought up with its chat open, from the arguments it was posted with (--resume)."""
        from baudelib.transcripts import find_session
        if not (wins := self.windows()):
            return
        win = wins[0]
        win.present()
        sid = args[args.index("--resume") + 1] if "--resume" in args[:-1] else None
        if sid is None or sid == win.session_id or (info := find_session(sid)) is None:
            win.show_chat()
            return
        win.opening_remote = None  # (as a click on its row in the sidebar opens it: Sidebar.on_activate)
        win.switch_to(lambda: win.resume_session(info))

    def idle(self):
        """Every window idle (BaudeWindow.idle): a new version can be restarted onto."""
        return all(w.idle() for w in self.windows())

    # ---- Update: the sidebars' foot while a new version waits, Baude's (on its windows: restart_blockers)
    # or This Mac's host process's (on its commands: info's update)
    def update_waiting(self, waiting):
        self.update_held = waiting
        self.show_update_bars()

    def update_reasons(self):
        """What the updates waiting wait for, said; None if none is waiting."""
        why, waiting = [], False
        if getattr(self, "update_held", False) and self.updater is not None and self.updater.ready:
            waiting = True
            why += [w for win in self.windows() for w in win.restart_blockers()]
        host = local_host()
        held = (host.info.get("update") if host is not None else None) or None
        if held:
            waiting = True
            why += [f"{THIS_MACHINE}'s host process: {c.get('what') or 'a command'}"
                    for c in held.get("waiting") or []] or [f"{THIS_MACHINE}'s host process"]
        return list(dict.fromkeys(why)) if waiting else None

    def show_update_bars(self):
        why = self.update_reasons()
        for w in self.windows():
            if (bar := getattr(w.sidebar, "update_bar", None)) is not None:
                bar.say(why)

    def update_now(self, win):
        """The bar clicked: restarted now. Nothing running: straight away; what's running, stopped: said first, and
        asked."""
        why = [w for w in (self.update_reasons() or []) if w != wording.say("window.blocker_using")]

        def go():
            host = local_host()
            if host is not None and host.info.get("update"):
                host.request({"op": "quit", "restart": True})  # (it comes back as a chat wants it: local_server)
            if self.updater is not None and self.updater.ready:
                u = self.updater
                self.restart({"from": u.started, "to": u.ready, "commits": update.new_commits(u.started, u.ready, u.root)})
        if not why:
            return go()
        dlg = Adw.AlertDialog(heading=wording.say("window.restart_now_heading"), default_response="cancel",
                              close_response="cancel",
                              body="The new version is picked up now. These are stopped, and their chats can be "
                                   "continued after:\n" + "\n".join(f"• {w}" for w in why))
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("go", wording.say("window.restart_now"))
        dlg.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "go" and go())
        dlg.present(win)

    def restart(self, updated=None):
        """This process replaced by a fresh baude (a new version picked up) that puts back every window as it was
        (restart_state), giving the front back to the app you were in (update.restarted): ☰ → Restart Baude, or a
        new version found (update.Updater; updated, what by). A session being watched isn't touched, and one of
        baude's own is resumed after (a turn it was running is cut short, as Ctrl+C would)."""
        wins = self.windows()[::-1]  # (the one you were in last, last: put back last, on top)
        path = restart_state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"windows": [w.restart_state() for w in wins], "drafts": BaudeWindow.drafts_state(),
                       "front": update.front_app(), "updated": updated}, f)
        for w in wins:
            if w.proc:
                w.proc.on_exit = lambda *_: None
                w.proc.kill()
            for p in list(w.background.values()):  # (chats left mid-turn: their turns end here)
                w.let_go_background(p)
        save_info_cache(force=True)  # (as quitting does: exec skips do_shutdown)
        if _NETWORK:
            _NETWORK[0].save()
        os.execv(sys.executable, launch_command("--cwd", wins[-1].run_cwd if wins else self.opts.cwd, "--restore", path))


def debug_report_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "baude", f"debug-{os.getpid()}.txt")


def window_state(win):
    """What a window is doing, for a debug report: enough to tell a stuck UI from a locked session."""
    now, proc = time.time(), getattr(win, "proc", None)
    alive = proc is not None and proc.p.poll() is None
    try:
        live = win.live_elsewhere()
    except Exception as e:  # noqa: BLE001  (a report must not fail)
        live = f"error: {e}"
    last = win.tail.last_written or 0
    history = getattr(win, "history", None)
    return {
        "title": win.get_title(), "session_id": win.session_id, "cwd": win.cwd, "run_cwd": win.run_cwd,
        "history_path": getattr(win, "history_path", None),
        "viewing read-only (resume_pending)": getattr(win, "resume_pending", None),
        "locked: live elsewhere": live,
        "other program last wrote, s ago": round(now - last, 1) if last else None,
        "its turn open (tail.turn_open)": win.tail.turn_open,
        "banner shown": win.banner.get_revealed(), "text box shown": win.composer.get_visible(),
        "busy": win.busy, "stick_bottom": win.stick_bottom,
        "claude": {"pid": proc.p.pid if proc else None, "alive": alive,
                   "args": proc.p.args if proc else None, "exit code": None if alive or not proc else proc.p.poll()},
        "pending control requests": list(win.pending),
        "tasks": {k: {f: t.get(f) for f in ("task_type", "status", "description")} for k, t in win.tasks.items()},
        "history items not drawn yet": history.count() if history else None,
        "tool cards": len(win.tool_cards), "zoom": win.zoom,
    }


def install_debug_hooks(app):
    """Inspect a running Baude without a debugger (ptrace is usually off limits):
       kill -USR1 <pid>   every thread's Python stack, written from the signal handler itself, so it
                          comes even when the main thread is stuck
       kill -USR2 <pid>   each window's state (session, lock, busy, claude, tasks), and what the process holds
                          (memreport: its memory, Python's objects, and with PYTHONTRACEMALLOC=25 where they were
                          allocated), from the main loop
    Both append to ~/.cache/baude/debug-<pid>.txt."""
    import faulthandler
    import signal
    path = debug_report_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        report = open(path, "a", buffering=1)  # (kept open: faulthandler writes to its descriptor)
    except OSError:
        return None
    faulthandler.register(signal.SIGUSR1, file=report, all_threads=True)

    def state():
        report.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  Baude state (pid {os.getpid()})\n")
        for win in app.get_windows():
            if isinstance(win, BaudeWindow):
                report.write(json.dumps(window_state(win), indent=2, default=str) + "\n")
        memreport.write(report, path)
        return True  # (keep listening)
    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix
        GLibUnix.signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR2, state)
    except (AttributeError, ImportError, ValueError):  # older GLib
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR2, state)
    return path


def main():
    ap = argparse.ArgumentParser(prog="Baude", description=__doc__.splitlines()[0])
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--model", help="alias (opus, sonnet, ...) or full model name; default: the CLI's")
    ap.add_argument("--resume", help="session id to resume")
    ap.add_argument("--restore", help=argparse.SUPPRESS)  # state left by "Restart Baude"
    ap.add_argument("--no-theme", action="store_true",
                    help="start in Default's look, whatever theme Settings has (BAUDE_NO_THEME=1)")
    inst = ap.add_mutually_exclusive_group()
    inst.add_argument("--install", action="store_true",
                      help=wording.say("install.install_help", where=LAUNCHER.where))
    inst.add_argument("--uninstall", action="store_true",
                      help=wording.say("install.uninstall_help", where=LAUNCHER.where))
    opts = ap.parse_args()
    if opts.no_theme:
        os.environ["BAUDE_NO_THEME"] = "1"
    if opts.install:
        LAUNCHER.install()
        baudelib.notifier.build_all(widgets.load_settings().get("accent"))  # (macOS, once it can be signed)
        print("\n".join(wording.say("install.installed", path=p)
                        for p in [*map(baudelib.bins.path, baudelib.bins.NAMES), *LAUNCHER.paths()]))
        return 0
    if opts.uninstall:
        removed = LAUNCHER.uninstall()
        print("\n".join(wording.say("install.removed", path=p) for p in removed) or
              wording.say("install.not_installed"))
        return 0
    baudelib.bins.refreshed(lambda text: print(f"baude: {text}", file=sys.stderr))
    return BaudeApp(opts).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
