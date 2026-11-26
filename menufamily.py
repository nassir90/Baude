"""☰'s views and programs on a host (Baude's and Record's): Kubernetes, Docker, Databases, Terminal and Browser, each
opened on the host picked beside New session. Browser asks that host for Emission's window on its own screen
(browser_open: emissionlink.open_window), brought to the front or started; what went wrong said in a toast."""
import threading

from gi.repository import Adw, GLib

from baudelib import wire, wording


def ask_host(where, msg, done):
    """msg (a request) to the host process at where, on a connection of its own: done(reply) on the main thread, an
    error's as {"error": why}."""
    def run():
        from baudelib import hosts
        try:
            if where.get("via") == "local" and not hosts.start_here():
                raise OSError(wording.say("host_status.none_started", log=hosts.log_path()))
            ch, _info = wire.reach(where, timeout=10)
        except (OSError, ValueError) as e:
            return GLib.idle_add(lambda: done({"error": str(e) or wording.say("host_status.no_answer")}) and False)
        reply = None
        try:
            ch.sock.settimeout(wire.SILENT_S)
            ch.send({**msg, "req": 1})
            for m in ch:
                if m.get("req") == 1:
                    reply = {"error": wire.said(m) or wording.say("host_status.no_answer")} if m.get("op") == "error" \
                        else m
                    break
        except OSError:
            pass
        finally:
            ch.close()
        got = reply or {"error": wording.say("host_status.connection_dropped")}
        GLib.idle_add(lambda: done(got) and False)
    threading.Thread(target=run, daemon=True, name="menu-family").start()


def open_browser(win, where=None, name=None):
    """☰ → Browser: Emission's window on the host (where, name; the one picked beside New session if not given) brought
    to the front, else started there."""
    if where is None:
        where, name = win.terminal_host()

    def done(reply):
        if reply.get("error"):
            toasts = getattr(win, "toasts", None)
            if isinstance(toasts, Adw.ToastOverlay):
                toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(reply["error"]), timeout=4))
    ask_host(where, {"op": "browser_open"}, done)
