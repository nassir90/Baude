"""A Bot's browser, on the side panel's Browser page: its own Emission (emission.py), on its chat's host or any other it
uses, watched and driven. A Bot of this Mac's: its Emission in this process (Here), its pages drawn in the page itself,
as big as it is, nothing streamed (it's the Bot's again, in a process of its own, once Baude's gone). Elsewhere, its
frames come as the page changes: from an Emission of this machine's own host process straight from it (its socket), the
pixels in shared memory; from one elsewhere through its host process (wire's emission_watch), each frame a JPEG the
size the page is shown at, the page laid out at the side panel's size. What you do with a streamed one (click, drag,
type, keys, scroll, its tabs, its address, the prompts a page waits on) goes to it in the order it's done, as the Bot's
tools do (real input there). Read-only: watched, not driven (kept for each Bot, as a setting). Out in a window of its
own (BrowserWindow: an Emission window's bars, the Bot's browser still), the page says so instead, and the Bot's browser
is that window's till it's closed. Over the page, never in it: the Bot's pointer as its tools move it, and its edges
glowing in the accent while the Bot's turn is at its tools (BotPointer, drawn by Presence, or MacOverlay hosted)."""
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

import widgets  # noqa: E402
from baudelib import emissionlink, wire  # noqa: E402
from baudelib.client import UNREACHED  # noqa: E402

PAGE = "Browser"
PING_S = 5  # (a host process takes a connection that's said nothing in wire.SILENT_S for gone)
TYPE_MS = 60  # (characters typed this close together: sent as one)
SCROLL_MS = 50  # (a wheel's or a trackpad's turns this close together: one scroll)
HOVER_MS = 300  # (the pointer still this long over the page: it hovers there)
SIZE_MS = 250  # (the page shown at a new size: the frames asked for at it this long after)
DRAG_PX = 5  # (a press moved this far before it's let go: a drag, not a click)
KEYS = {"Return": "Enter", "KP_Enter": "Enter", "BackSpace": "Backspace", "Delete": "Delete", "Escape": "Escape",
        "Tab": "Tab", "ISO_Left_Tab": "Tab", "Left": "Left", "Right": "Right", "Up": "Up", "Down": "Down",
        "Home": "Home", "End": "End", "Page_Up": "PageUp", "Page_Down": "PageDown", "space": "Space"}


def shared(name):
    """Shared memory Emission made, opened (not tracked: it's Emission's to unlink, not this process's)."""
    from multiprocessing import shared_memory
    try:
        return shared_memory.SharedMemory(name, track=False)
    except TypeError:  # (a Python before 3.13: its resource tracker told it isn't this process's)
        mem = shared_memory.SharedMemory(name)
        try:
            from multiprocessing import resource_tracker
            resource_tracker.unregister(mem._name, "shared_memory")
        except (ImportError, AttributeError, KeyError):
            pass
        return mem


class Stream:
    """A watch of a Bot's Emission, read on a thread of its own: frame(texture, w, h, view, shown), state(state) and
    ended(why) run on the main loop as they come, never after stop(); shown() acks the frame once it's on screen."""

    def __init__(self, remote, bot, size, frame, state, ended):
        self.remote, self.bot, self.size = remote, bot, size
        self.on_frame, self.on_state, self.on_ended = frame, state, ended
        self.stopped, self.lock, self.sock, self.ch = False, threading.Lock(), None, None
        self.memories, self.mem_lock = {}, threading.Lock()  # (shared memory Emission writes frames in, by name)
        threading.Thread(target=self.run, daemon=True, name="browser-watch").start()

    def later(self, fn):
        GLib.idle_add(lambda: (None if self.stopped else fn()) and False)

    def run(self):
        why = None
        try:
            (self.run_here if self.remote.mine else self.run_there)()
        except Exception as e:  # (said in the pane: it can be tried again)
            why = str(e) if isinstance(e, (OSError, ValueError)) else f"{type(e).__name__}: {e}"
        finally:
            self.close()
        self.later(lambda: self.on_ended(why))

    def run_here(self):
        """This machine's own Emission: its socket, frames in shared memory."""
        pid = emissionlink.bot_profile_id(self.bot["id"])
        for attempt in range(2):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(emissionlink.profile_socket(pid))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                sock.close()
                if attempt:
                    raise OSError("its Emission didn't open")
                got = self.remote.call({"op": "emission", "bot": self.bot, "call": {"op": "tabs", "from_pane": True}},
                                       timeout=90)
                if got.get("op") == "error":  # (started by its host, as the Bot's tools would start it)
                    raise OSError(got.get("error") or "its Emission didn't start")
        self.sock = sock
        sock.settimeout(None)
        self.say({"op": "watch", "shm": True, "max_w": self.size[0], "max_h": self.size[1]})
        buffer = b""
        while not self.stopped and (part := sock.recv(1 << 20)):
            buffer += part
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                self.took(json.loads(line))

    def run_there(self):
        """Another host's Emission: through that host process, frames as JPEG."""
        ch, _info = wire.reach(self.remote.reach_where(), timeout=30)
        self.ch = ch
        if self.stopped:
            return
        ch.send({"op": "emission_watch", "req": 1, "bot": self.bot, "max_w": self.size[0], "max_h": self.size[1]})
        threading.Thread(target=self.ping, daemon=True, name="browser-ping").start()
        ch.sock.settimeout(wire.SILENT_S)  # (it pings: silent this long, it's gone without saying so)
        while not self.stopped and (msg := ch.reply()) is not None:
            if msg.get("op") == "error":
                raise OSError(msg.get("error") or "its host said no")
            if msg.get("op") == "emission_watch_end":
                return
            if msg.get("op") == "emission_frame":
                self.took(msg)

    def ping(self):
        while not self.stopped and self.ch is not None:
            time.sleep(PING_S)
            if self.stopped or not self.ch.send({"op": "ping"}):
                return

    def took(self, msg):
        if self.stopped:
            return
        if msg.get("error"):  # (Emission said no: why, in the pane)
            raise ValueError(msg["error"])
        if "state" in msg:
            state = msg["state"]
            self.later(lambda: self.on_state(state))
            return
        if "frame" not in msg:
            return
        w, h, seq = int(msg["w"]), int(msg["h"]), msg["frame"]
        if msg.get("shm"):
            with self.mem_lock:
                name = msg["shm"]
                if name not in self.memories:
                    for old in self.memories.values():
                        old.close()
                    self.memories = {name: shared(name)}
                stride = int(msg["stride"])
                data = GLib.Bytes.new(bytes(self.memories[name].buf[:stride * h]))
            made = lambda: Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED, data, stride)  # noqa
        else:
            import base64
            data = GLib.Bytes.new(base64.b64decode(msg["jpeg"]))
            made = lambda: Gdk.Texture.new_from_bytes(data)  # noqa: E731
        view = msg.get("view")
        self.later(lambda: self.on_frame(made(), w, h, view, lambda: self.say({"ack": seq})))

    def say(self, line):
        """A line to Emission (here: its socket; there: through its host)."""
        with self.lock:
            try:
                if self.sock is not None:
                    self.sock.sendall((json.dumps(line) + "\n").encode())
                elif self.ch is not None:
                    self.ch.send(line if line.get("op") else {"op": "emission_watch_say", "line": line})
            except OSError:
                pass

    def resize(self, size):
        self.size = size
        self.say({"max_w": size[0], "max_h": size[1]})

    def close(self):
        for closing in (self.sock, self.ch.sock if self.ch is not None else None):
            if closing is not None:
                try:
                    closing.shutdown(socket.SHUT_RDWR)  # (a recv waiting on it woken, the other end told)
                except OSError:
                    pass
                try:
                    closing.close()
                except OSError:
                    pass
        with self.mem_lock:
            for mem in self.memories.values():
                try:
                    mem.close()
                except (OSError, BufferError):
                    pass
            self.memories = {}

    def stop(self):
        self.stopped = True
        self.close()


class Asks:
    """What's done in the pane, sent to the Bot's Emission one after another, in the order it's done (each answered
    before the next goes), on a thread of its own: answered(reply) on the main loop."""

    def __init__(self):
        self.q, self.pending = queue.Queue(), 0  # (pending: asked, not answered yet)
        threading.Thread(target=self.run, daemon=True, name="browser-asks").start()

    def put(self, remote, bot, call, answered):
        self.pending += 1

        def done(reply):
            self.pending -= 1
            answered(reply)
        self.q.put((remote, bot, call, done))

    def run(self):
        while True:
            remote, bot, call, answered = self.q.get()
            try:
                got = None
                if remote.mine:  # (straight to its socket: no host between)
                    try:
                        got = emissionlink.call(emissionlink.bot_profile_id(bot["id"]), call, timeout=90)
                    except (FileNotFoundError, ConnectionRefusedError):
                        got = None  # (its Emission isn't running yet: its host starts it, as for the Bot's tools)
                if got is None:
                    r = remote.call({"op": "emission", "bot": bot, "call": call}, timeout=120)
                    got = r.get("result") if r.get("op") == "emission" else {"error": r.get("error") or "no answer"}
            except (OSError, ValueError) as e:
                got = {"error": str(e)}
            GLib.idle_add(lambda g=got, a=answered: a(g) and False)


HERE = {}  # (a Bot's chat id: its Here: this Mac's Bots' Emissions in this process, kept while Baude runs)
IDLE_TAKE_S = 5  # (a Bot's own Emission taken into Baude only once it's been this long without a request)
RELEASED = {}  # (a profile's id: when Baude let go of it, for someone's Emission window: not taken back till it's had it)
RELEASED_S = 15
BUSY_RETRY_S = 10  # (a Bot busy in its own Emission, watched streamed: tried here again this often)
OFF_S = 60  # (a Bot whose browser couldn't be had here: streamed, and tried here again after this long)
RETRY_S = 3  # (a BrowserWindow's browser that's stopped, the window in front: watched again after this long)
HOME = {"app": None}  # (Baude's application, once its home socket's open: where this Mac's Bots' browsers are made)


def open_home(app, ours):
    """This Mac's Bots' browsers made in Baude while it runs: its home socket (emissionlink.call_bot asks it first, as a
    Bot's tool reaches for a browser it hasn't got) takes {"op": "host", "bot", "name"}, answered once it's here.
    ours(bot): whether that's a Bot whose chat is on this Mac (another's Bot, using this Mac's browser, has an Emission
    of its own, as before)."""
    if HOME["app"] is not None or sys.platform != "darwin":
        return
    import emission
    HOME["app"] = app

    def handle(_pid, msg, answer, _conn=None):
        if msg.get("op") != "host" or not isinstance(msg.get("bot"), str) or not msg["bot"]:
            return answer({"error": "the home socket makes a Bot's browser: host {bot, name}"})
        if not ours(msg["bot"]):
            return answer({"error": "not a Bot of this Mac's"})
        here = HERE.get(msg["bot"])
        if here is None:
            here = HERE[msg["bot"]] = Here(app, msg["bot"], msg.get("name"))
        here.when_ready(lambda h: answer({"ok": True} if h.win is not None else {"error": h.why or "not here"}))
    emission.ProfileSocket.listen("home", handle)


def can_host(remote):
    """Whether a Bot on this host can have its browser here, in this process: this Mac's own host process's (on
    Linux an agent's input is the display's own, so it keeps a display of its own, and its pages are streamed)."""
    return sys.platform == "darwin" and remote is not None and remote.mine


class Here:
    """A Bot of this Mac's browser in this process: its profile taken from the Emission it had (its pages opened here),
    its socket this process's, so the Bot's tools reach it here. ready(here) on the main loop once it's there (win), or
    it couldn't be (why)."""

    def __init__(self, app, sid, name, force=False):
        """force: taken from the Bot's own Emission busy or not (Restart as Native: its pages reloaded, the Bot told)."""
        self.app, self.sid, self.name, self.pid, self.win, self.why, self.waiting = app, sid, name, None, None, None, []
        self.force = force
        threading.Thread(target=self.take, daemon=True, name="browser-here").start()

    def when_ready(self, fn):
        if self.win is not None or self.why is not None:
            fn(self)
        else:
            self.waiting.append(fn)

    def take(self):
        try:
            pid = emissionlink.bot_profile(self.sid, self.name)
            since = time.monotonic() - RELEASED.get(pid, -1e9)  # (just given to someone's Emission: not taken
            if since < RELEASED_S and not (self.force and since > 3):  # back unasked, nor before it's had it)
                raise OSError("it was just let go of")
            urls = []
            try:
                info = emissionlink.call(pid, {"op": "info"}, timeout=3)
                if not info.get("agent"):  # (someone's Emission window has it: watched there instead)
                    raise OSError("it's open in an Emission window")
                if info.get("idle_s", IDLE_TAKE_S) < IDLE_TAKE_S and not self.force:  # (the Bot's using it: not
                    # taken mid-task)
                    raise OSError("busy")
                tabs = emissionlink.call(pid, {"op": "tabs", "from_pane": True}, timeout=10).get("tabs") or []
                urls = [t["url"] for t in tabs if t.get("url") and t["url"] != "about:blank"]  # (Baude's asking,
                emissionlink.call(pid, {"op": "quit", "from_pane": True}, timeout=3)  # not the Bot: its note kept)
            except (FileNotFoundError, ConnectionRefusedError):
                pass  # (none running)
            end = time.monotonic() + 10
            while not emissionlink.lock_profile(pid):
                if time.monotonic() > end:
                    raise OSError("its Emission didn't let go of its profile")
                time.sleep(0.1)
            GLib.idle_add(lambda: self.made(pid, urls) and False)
        except (OSError, ValueError) as e:
            why = str(e)  # (e's gone once this block's done: the idle call's after)
            GLib.idle_add(lambda: self.done(why) and False)

    def made(self, pid, urls):
        try:
            import emission
            profile = next(p["name"] for p in emissionlink.profiles() if p["id"] == pid)
            win = emission.Window(self.app, urls[0] if urls else None, profile, agent=True, hosted=True)
            if win.Engine is None or win.status is not None:
                why = win.status.get_description() if win.status is not None else "no page engine here"
                win.closing_window()
                win.destroy()
                emission.ProfileSocket.close(pid)
                emissionlink.unlock_profile(pid)
                return self.done(why)
            for url in urls[1:]:
                win.open(url)
            win.released = self.release  # (someone's Emission asking for its profile: let go of)
            if urls:  # (what the Bot's told, its next request: its pages moved, reloaded)
                win.note = ((win.note + " ") if win.note else "") + (
                    "This browser moved into Baude: its pages were opened again from their addresses (what was "
                    "typed in them, and their history, is gone): " + ", ".join(urls[:10]))
                emission.pending_note(pid, win.note)
            win.get_content().set_content(None)  # (its pages, its downloads' banner: the Browser page's to show)
            if not HOME.get("awake"):
                HOME["awake"] = True
                emission.MacWeb.stay_awake()  # (a page of a Bot's runs on while Baude's out of sight)
            self.pid, self.win = pid, win
        except Exception as e:  # (streamed instead: its window and socket, if it got that far, let go of)
            try:
                import emission
                if "win" in locals():
                    win.closing_window()
                    win.destroy()
                emission.ProfileSocket.close(pid)
            except Exception:
                sys.excepthook(*sys.exc_info())
            emissionlink.unlock_profile(pid)
            return self.done(f"{type(e).__name__}: {e}")
        self.watching = GLib.timeout_add_seconds(60, self.idle_check)
        self.done(None)

    def idle_check(self):
        """Gone unused (by the Bot) this long, and not shown: let go of, as an agent's Emission goes when idle."""
        import emission
        win = self.win
        if win is None:
            return False
        if not win.body.get_mapped() and (GLib.get_monotonic_time() - win.asked_at) / 1e6 > emission.AGENT_IDLE_S:
            emission.open_pages(self.pid, win.open_said or [], why="idle")
            self.release()
            return False
        return True

    def release(self):
        """Its browser closed, its profile let go of (another Emission's to have; the Bot's next one tells it what
        it had open), and nothing left of it here."""
        import emission
        win, self.win = self.win, None
        HERE.pop(self.sid, None)
        if win is None:
            return
        if win.body.get_parent() is not None:
            win.body.get_parent().remove(win.body)
        win.closing_window()
        win.destroy()
        emission.ProfileSocket.close(self.pid)
        pid = self.pid
        RELEASED[pid] = time.monotonic()
        GLib.timeout_add(2000, lambda: pid not in emission.ProfileSocket.open and emissionlink.unlock_profile(pid)
                         and False)
        # (WebKit's store let go of first; not if it's been taken here again meanwhile)

    def done(self, why):
        self.why = why
        if why is not None:  # (asked again next time: a busy Bot's, once it's let it be)
            HERE.pop(self.sid, None)
        waiting, self.waiting = self.waiting, []
        for fn in waiting:
            fn(self)

    def call(self, call, answered):
        """call to its Emission, here (answered(reply) on the main loop)."""
        try:
            self.win.handle(self.pid, call, answered)
        except Exception as e:  # (said, as an error of the call's)
            answered({"error": f"{type(e).__name__}: {e}"})


def guard_keys(root):
    """A window of Baude's kept from keys that aren't its own (a page's that has them; a Bot's, handed back by its
    page, or the text they made): MacWeb.page_has_keys. GTK's own widgets clicked: the keys theirs again. Once."""
    if getattr(root, "keys_back", False):
        return
    root.keys_back = True
    import emission
    clicks = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
    clicks.connect("pressed", lambda *_: emission.MacWeb.keys_to_gtk(root))
    root.add_controller(clicks)
    keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
    for signal in ("key-pressed", "key-released"):
        keys.connect(signal, lambda c, kv, *_: emission.MacWeb.page_has_keys(root, c.get_current_event_time(), kv))
    root.add_controller(keys)


def guard_windows(app):
    """Each of the app's windows guarded (guard_keys), those opened later too: a Bot's keys, and their text, can come
    to whichever has the keys. Once, on macOS (where a page is hosted in Baude)."""
    if sys.platform != "darwin" or app is None or getattr(app, "keys_guarded", False):
        return
    app.keys_guarded = True
    for w in app.get_windows():
        guard_keys(w)
    app.connect("window-added", lambda _a, w: guard_keys(w))


def refuse_tab_drops(bar, ours):
    """A tab dragged onto bar (an Adw.TabBar) refused unless ours(where its drag began: a surface, or None) says
    it's its own: a Bot's tab never moves its page (and its profile's store) into another's view, nor another's
    into a Bot's, nor a chat's in or out. Taken first (before the bar's own drop targets), and given nowhere."""
    drop = Gtk.DropTarget.new(Adw.TabPage, Gdk.DragAction.MOVE)
    drop.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def foreign(_target, d):
        drag = d.get_drag() if d is not None else None
        return not ours(drag.get_surface() if drag is not None else None)
    drop.connect("accept", foreign)
    for signal in ("enter", "motion"):
        drop.connect(signal, lambda *_: Gdk.DragAction.MOVE)  # (taken, so the bar beneath never sees it)
    drop.connect("drop", lambda *_: False)
    bar.add_controller(drop)


PANES = set()  # (the side panels' Browser pages)
ALL = set()  # (every Browser page, the side panels' and BrowserWindows')
LISTENING = []  # (the network its listings come from, once it's listened to: presence_check's)


def listen(network):
    """Each Browser page shown told as listings change (a turn at its tools or not: presence_check). Once."""
    if network is None or LISTENING:
        return
    LISTENING.append(network)
    network.listen(lambda: [pane.presence_check() for pane in list(ALL)])
DISEMBODIED = {}  # (a Bot's chat id: its BrowserWindow, its browser out of the side panel)
RESTORE = {"left": None}  # (the chats whose browsers were out as Baude last ran: out again once they're listed)
RESTORE_S = 60


def remember():
    """The chats whose browsers are out, kept (browser_out): out again as Baude next starts."""
    widgets.save_settings(browser_out=sorted(set(DISEMBODIED) | set(RESTORE["left"] or ())))


def baude_window(app, sid=None):
    """The Baude window a Bot's browser window belongs to: the one with its chat open, else the one in front, else
    the first (None: none's left)."""
    shown = [w for w in (app.get_windows() if app is not None else []) if getattr(w, "sidebar", None) is not None
             and not getattr(w, "let_go_done", False)]
    return next((w for w in shown if sid is not None and getattr(w, "session_id", None) == sid), None) or \
        (app.get_active_window() if app.get_active_window() in shown else None) or (shown[0] if shown else None)


def restore_windows(win):
    """The browsers out as Baude last ran, out again (in windows of their own, behind what's in front), each once
    its chat's listed as a Bot's on a host that's up, with the Baude window it belongs to (baude_window); those
    never listed in RESTORE_S, forgotten. Once for the process."""
    if RESTORE["left"] is not None:
        return
    RESTORE["left"] = {sid for sid in widgets.load_settings().get("browser_out") or [] if isinstance(sid, str)}
    tries, app = [0], win.get_application()

    def look():
        tries[0] += 1
        for sid in list(RESTORE["left"]):
            main = baude_window(app, sid)
            if main is None:
                continue
            sidebar = getattr(main, "sidebar", None)
            info = sidebar.find_info(sid) if sidebar is not None else None
            r = (info or {}).get("remote")
            if sid in DISEMBODIED or info and info.get("bot") and r is not None and r.state == "alive":
                RESTORE["left"].discard(sid)
                if sid not in DISEMBODIED:
                    BrowserWindow(main, info, restored=True)
        if RESTORE["left"] and tries[0] < RESTORE_S:
            return True
        if RESTORE["left"]:
            RESTORE["left"] = set()
            remember()
        return False
    if RESTORE["left"]:
        GLib.timeout_add_seconds(1, look)


class BrowserWindow(Adw.ApplicationWindow):
    """A Bot's browser out of the side panel (Disembody), in a window of its own that's an Emission window to look at
    (its bars: emission.Chrome), the Bot's browser still: the same pages and tabs, its profile, hosted or streamed as
    it was, nothing reloaded. Its Bot's, whatever chat's open (several out at once, each its own). Its chat's side
    panel page says it's out; closed, it's back there. Out as Baude last ran: out again (restore_windows). main: the
    Baude window it came from (gone, this goes too, to come back next time)."""

    def __init__(self, main, info, restored=False):
        """restored: out again as Baude starts (shown behind the window in front, not taking the keys)."""
        super().__init__(application=main.get_application(), title="Emission", default_width=1180,
                         default_height=820)
        self.main, self.sid, self.done = main, info["id"], False
        DISEMBODIED[self.sid] = self
        remember()
        for pane in list(PANES):  # (its chat's side panel page: out, now)
            if pane.sid == self.sid:
                pane.show_out()
        self.pane = BrowserPane(main, bound=self)
        self.set_content(self.pane)
        keys = Gtk.ShortcutController()  # (Emission's own)
        for accel, fn in (("<Meta>t|<Control>t", lambda: self.pane.ask({"op": "new_tab", "url": "about:blank"})),
                          ("<Meta>w|<Control>w", lambda: self.pane.tab() and self.pane.ask(
                              {"op": "close_tab", "tab": self.pane.tab()})),
                          ("<Meta>l|<Control>l", lambda: (self.pane.address.grab_focus(), self.keys_to_gtk())),
                          ("<Meta>r|<Control>r", lambda: self.pane.ask({"op": "navigate", "url": "reload",
                                                                         "tab": self.pane.tab()}))):
            keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(accel),
                                               Gtk.CallbackAction.new(lambda *_a, fn=fn: (fn(), True)[1])))
        self.add_controller(keys)
        refuse_tab_drops(self.pane.tabs, lambda surface: surface is not None and surface == self.get_surface())
        self.connect("close-request", lambda *_: self.put_back(closing=True) or False)
        self.connect("notify::is-active", lambda *_: self.is_active() and self.pane.watch_again())  # (stopped:
        # started again, come back to)
        self.gone_id = self.get_application().connect("window-removed", lambda _a, w: w is self.main and self.gone())
        if restored:
            self.show_behind()
        else:
            self.present()
        self.pane.show(info)

    def show_behind(self):
        """Shown, but behind the Baude window it belongs to, which keeps the keys (put there once it's drawn: GTK
        brings a window it shows to the front as it draws it first)."""
        def behind(*_):
            front = macos.native_window(self.main) if self.main is not None and self.main.get_mapped() else None
            ns = macos.native_window(self)
            if front and ns:
                macos.objc_send(ns, b"orderWindow:relativeTo:", -1, macos.objc_send(front, b"windowNumber",
                                                                                     ret=ctypes.c_long),
                                types=(ctypes.c_long, ctypes.c_long))  # (-1: below it)
                macos.objc_send(front, b"makeKeyWindow")
            return False
        if sys.platform == "darwin":
            import ctypes
            import macos
            frames = [0]

            def tick(*_):  # (its second frame: drawn by then)
                frames[0] += 1
                return frames[0] < 2 or behind()
            self.connect("map", lambda *_: self.add_tick_callback(tick))
        self.set_visible(True)
        if sys.platform != "darwin" and self.main is not None:  # (the Baude window in front again, once it's shown)
            GLib.timeout_add(100, lambda: self.main is not None and self.main.present() and False)

    def keys_to_gtk(self):
        if sys.platform == "darwin":
            import emission
            emission.MacWeb.keys_to_gtk(self)

    def put_back(self, closing=False):
        """Back in the side panel (its window closed, or asked from there)."""
        if self.done:
            return
        self.drop(closing)
        remember()
        for pane in list(PANES):
            if pane.sid == self.sid:
                pane.came_back()

    def gone(self):
        """The Baude window it came from closed: gone with it, and out again as Baude next starts (kept), unless
        another Baude window's side panel shows its Bot's browser, which has it again then."""
        main, taken = self.main, False
        self.drop()
        for pane in list(PANES):
            if pane.sid == self.sid and pane.win is not main and not getattr(pane.win, "let_go_done", False):
                pane.came_back()
                taken = True
        if taken:  # (back in a side panel: not out as Baude next starts)
            remember()

    def drop(self, closing=False):
        """Gone: its page let go of (for the side panel's to have), the window too unless it's closing itself."""
        if self.done:
            return
        self.done = True
        if DISEMBODIED.get(self.sid) is self:
            DISEMBODIED.pop(self.sid)
        if self.main is not None:
            if (app := self.main.get_application() or self.get_application()) is not None:
                app.disconnect(self.gone_id)
            self.main = None
        self.pane.stop()
        if not closing:
            self.destroy()


class Presence(Gtk.Widget):
    """Over a streamed page (its frames, in the Browser page or a BrowserWindow): the Bot's pointer (emission's
    BotPointer, played from what the Bot's Emission tells its watchers) and the glow, drawn as emission's are, drawn
    over the frame and never taking a click. Hosted, the page's own view is over everything GTK draws: MacOverlay
    draws them there instead."""

    def __init__(self, pane):
        import emission
        super().__init__(can_target=False, hexpand=True, vexpand=True)
        self.pane, self.pointer, self.ticking = pane, emission.BotPointer(), None
        self.connect("unmap", lambda *_: self.ticking is not None and (self.remove_tick_callback(self.ticking),
                                                                       setattr(self, "ticking", None)))

    def draw_glow(self, snapshot, x, y, w, h, rgb):
        """The glow over the frame at x, y, w by h: macOS's overlay's picture of it (emission.glow_pixels), its
        corners as they are, its sides stretched along the frame's edges, nothing in the middle."""
        import emission
        size, reach = emission.GLOW_REACH * 2 + 4, emission.GLOW_REACH
        key = (size, rgb)
        if key not in GLOW_TEXTURES:
            GLOW_TEXTURES.clear()  # (the accent's changed: the last one's no more use)
            GLOW_TEXTURES[key] = Gdk.MemoryTexture.new(size * 2, size * 2, Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED,
                                                       GLib.Bytes.new(emission.glow_pixels(size, rgb, 2)), size * 8)
        texture, r = GLOW_TEXTURES[key], min(reach, w / 2, h / 2)
        cuts = ((0, reach), (reach, size - reach), (size - reach, size))
        xs = ((x, x + r), (x + r, x + w - r), (x + w - r, x + w))
        ys = ((y, y + r), (y + r, y + h - r), (y + h - r, y + h))
        for i in range(3):
            for j in range(3):
                if i == j == 1 or xs[i][1] <= xs[i][0] or ys[j][1] <= ys[j][0]:
                    continue
                kx = (xs[i][1] - xs[i][0]) / (cuts[i][1] - cuts[i][0])
                ky = (ys[j][1] - ys[j][0]) / (cuts[j][1] - cuts[j][0])
                snapshot.push_clip(Graphene.Rect().init(xs[i][0], ys[j][0], xs[i][1] - xs[i][0], ys[j][1] - ys[j][0]))
                snapshot.append_texture(texture, Graphene.Rect().init(xs[i][0] - cuts[i][0] * kx,
                                                                      ys[j][0] - cuts[j][0] * ky, size * kx, size * ky))
                snapshot.pop()

    def accent_changed(self):
        """(widgets.apply_accent's) Drawn again in the new accent, hosted too."""
        self.pane.presence_check()
        self.queue_draw()

    def kick(self):
        """Drawn each frame while anything moves."""
        if self.ticking is None and self.get_mapped() and self.pointer.busy():
            self.ticking = self.add_tick_callback(self.tick)

    def tick(self, *_):
        self.queue_draw()
        if not self.pointer.busy():
            self.ticking = None
            return False
        return True

    def do_snapshot(self, snapshot):
        pane = self.pane
        if pane.stream is None or pane.body.get_visible_child_name() != "page":
            return
        box = pane.frame_rect(self)
        if box is None:
            return
        x, y, w, h, sx, sy = box
        look = self.pointer.look()
        c = widgets.accent_rgba()
        rgb = (c.red, c.green, c.blue)
        snapshot.push_clip(Graphene.Rect().init(x, y, w, h))
        if look["glow"] > 0:
            snapshot.push_opacity(look["glow"])
            self.draw_glow(snapshot, x, y, w, h, rgb)
            snapshot.pop()
        if hasattr(Gsk, "PathBuilder"):  # (GTK 4.14's: paths drawn, filled and stroked)
            self.draw_pointer(snapshot, look, rgb, x, y, sx, sy)
        snapshot.pop()

    @staticmethod
    def rgba(rgb, a):
        c = Gdk.RGBA()
        c.red, c.green, c.blue, c.alpha = *rgb, max(0.0, min(1.0, a))
        return c

    @staticmethod
    def path(lines, closed=False, circle=None):
        b = Gsk.PathBuilder.new()
        if circle is not None:
            b.add_circle(Graphene.Point().init(circle[0], circle[1]), circle[2])
        for line in lines:
            b.move_to(*line[0])
            for p in line[1:]:
                b.line_to(*p)
            if closed:
                b.close()
        return b.to_path()

    def draw_pointer(self, snapshot, look, rgb, ox, oy, sx, sy):
        """look's rings, chevrons and pointer (emission's shapes), the page's point (x, y) at (ox + x * sx,
        oy + y * sy)."""
        import emission
        a, white, winding = look["alpha"], (1, 1, 1), Gsk.FillRule.WINDING

        def stroke(width):
            st = Gsk.Stroke.new(width)
            st.set_line_join(Gsk.LineJoin.ROUND)
            st.set_line_cap(Gsk.LineCap.ROUND)
            return st
        for x, y, r, ra in look["rings"]:
            ring = self.path([], circle=(ox + x * sx, oy + y * sy, r))
            snapshot.append_fill(ring, winding, self.rgba(rgb, 0.22 * ra * a))
            snapshot.append_stroke(ring, stroke(emission.RING_EDGE), self.rgba(rgb, ra * a))
        for x, y, dx, dy, k, ha in look["hints"]:
            (ux, uy), (cx, cy) = emission.hint_at(ox + x * sx, oy + y * sy, dx, dy, k)
            lines = self.path(emission.chevrons(cx, cy, ux, uy))
            snapshot.append_stroke(lines, stroke(5), self.rgba(white, ha * a))
            snapshot.append_stroke(lines, stroke(2.8), self.rgba(rgb, ha * a))
        if look["x"] is not None and a > 0:
            tx, ty = ox + look["x"] * sx, oy + look["y"] * sy
            dx, dy = emission.ARROW_SHADOW
            snapshot.append_fill(self.path([emission.arrow_at(tx + dx, ty + dy, look["scale"])], closed=True), winding,
                                 self.rgba((0, 0, 0), 0.28 * a))
            arrow = self.path([emission.arrow_at(tx, ty, look["scale"])], closed=True)
            snapshot.append_fill(arrow, winding, self.rgba(rgb, a))
            snapshot.append_stroke(arrow, stroke(emission.ARROW_EDGE), self.rgba(white, a))


GLOW_TEXTURES = {}  # ((its size, the accent): the glow's texture, made once for each)


DOING = {"click": "click", "close_tab": "close the tab", "drag": "drag", "hover": "hover", "key": "press the key",
         "navigate": "open the page", "new_tab": "open a tab", "scroll": "scroll", "select_tab": "switch tabs",
         "type": "type", "answer_prompt": "answer"}  # (what a call's error says it couldn't do)


class BrowserPane(Gtk.Box):
    """The Browser page: the open Bot's browser on a host (its chat's, or another it uses), watched while shown. bound:
    the BrowserWindow it's in instead (one Bot's, whatever chat's open: an Emission window's bars, not the panel's)."""

    def __init__(self, win, bound=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.sid, self.remote, self.home, self.name, self.stream = win, None, None, None, None, None
        self.bound, self.info, self.setting = bound, None, False  # (info: show's; setting: a toggle set, not clicked)
        self.frame_size, self.view_size, self.state, self.prompt_ids = (0, 0), None, {}, None
        self.typed, self.type_source, self.scroll_due, self.scroll_source = "", None, [0.0, 0.0], None
        self.hover_source, self.size_source, self.pointer, self.drag = None, None, None, None
        self.asks, self.hosts, self.sized_to = Asks(), [], None  # (sized_to: the size a streamed Bot's page was
        # last laid out at, here)
        self.here, self.here_poll, self.here_off, self.here_busy = None, None, {}, None  # (here: the Here shown;
        # here_off: Bots whose browser couldn't be had here, streamed instead; here_busy: one busy in its own
        # Emission, streamed till the page is shown again)
        self.pointer_seen = None  # (the last of the Bot's pointer's moves played, as its watch numbers them)
        open_home(win.get_application(), lambda bot: (
            (i := win.sidebar.find_info(bot)) is not None and i.get("remote") is not None and i["remote"].mine))
        self.read_only = Gtk.ToggleButton(icon_name="changes-prevent-symbolic", tooltip_text="Read-only")
        self.read_only.add_css_class("flat")
        self.read_only.connect("toggled", lambda b: self.read_only_set(b.get_active()))
        self.native = Gtk.Button(icon_name="video-display-symbolic", tooltip_text="Restart as Native (its pages reload)",
                                 visible=False)  # (a browser of this Mac's streamed: drawn in place instead, at once)
        self.native.add_css_class("flat")
        self.native.connect("clicked", lambda *_: self.restart_native())
        self.host_pick = Gtk.DropDown(tooltip_text="Host")
        self.host_pick.connect("notify::selected", lambda *_: self.host_picked())
        self.prompts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=6, margin_end=6)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True,
                                   focusable=True)
        self.status = Adw.StatusPage(icon_name="web-browser-symbolic", title="No pages open", vexpand=True)
        self.body = Gtk.Stack(vexpand=True)
        self.body.add_named(self.status, "status")
        self.body.add_named(self.picture, "page")
        out = Gtk.Label(label="Currently disembodied", valign=Gtk.Align.CENTER)  # (its browser's in a window of its
        out.add_css_class("dim-label")                                            # own: BrowserWindow)
        out.add_css_class("title-3")
        self.body.add_named(out, "out")
        self.presence = Presence(self)
        over = Gtk.Overlay(child=self.body, vexpand=True)
        over.add_overlay(self.presence)
        self.said = Gtk.Label(wrap=True, xalign=0, visible=False, margin_start=8, margin_end=8, margin_bottom=4)
        self.said.add_css_class("dim-label")
        if bound is None:
            self.panel_bars(over)
        else:
            self.window_bars(over)
        self.drives = [self.back, self.fwd, self.reload, self.address, self.new, self.native, self.tabs, self.prompts]
        self.input()
        self.connect("map", lambda *_: self.watch_if_shown())  # (shown: watched; hidden: let go of, till it's back)
        self.connect("unmap", lambda *_: self.stop(keep=True))
        self.size_timer = GLib.timeout_add(500, self.size_check)
        self.connect("destroy", lambda *_: (GLib.source_remove(self.size_timer), PANES.discard(self),
                                            ALL.discard(self), self.stop()))
        ALL.add(self)
        if bound is None:
            PANES.add(self)
            restore_windows(win)
            if (main_tabs := getattr(getattr(win, "tabs", None), "bar", None)) is not None:  # (Baude's own tabs
                refuse_tab_drops(main_tabs, lambda surface: surface is None or not any(  # take none of a Bot's
                    surface == w.get_surface() for w in DISEMBODIED.values()))           # browser's)

    def panel_bars(self, over):
        """The side panel's: one bar (back, forward, reload, the address, the host, a new tab, Restart as Native, out
        in a window, read-only), the tabs under it while there's more than one."""
        bar = Gtk.Box(spacing=4, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
        self.back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back")
        self.fwd = Gtk.Button(icon_name="go-next-symbolic", tooltip_text="Forward")
        self.reload = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Reload")
        for b, url in ((self.back, "back"), (self.fwd, "forward"), (self.reload, "reload")):
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, u=url: self.ask({"op": "navigate", "url": u, "tab": self.tab()}))
            bar.append(b)
        self.address = Gtk.Entry(hexpand=True, placeholder_text="Search or type an address")
        self.address.connect("activate", lambda e: e.get_text().strip() and self.ask(
            {"op": "navigate", "url": e.get_text().strip(), "tab": self.tab()}))
        bar.append(self.address)
        self.gap = Gtk.Box(hexpand=True, visible=False)  # (the address's room, while it's out)
        bar.append(self.gap)
        bar.append(self.host_pick)
        self.new = Gtk.Button(icon_name="tab-new-symbolic", tooltip_text="New tab")
        self.new.add_css_class("flat")
        self.new.connect("clicked", lambda *_: self.ask({"op": "new_tab", "url": "about:blank"}))
        bar.append(self.new)
        bar.append(self.native)
        self.out = Gtk.ToggleButton(icon_name="window-new-symbolic", tooltip_text="Move to Its Own Window",
                                    visible=False)
        self.out.add_css_class("flat")
        self.out.connect("toggled", lambda b: not self.setting and self.disembody(b.get_active()))
        bar.append(self.out)
        bar.append(self.read_only)
        self.append(bar)
        self.strip = Gtk.ScrolledWindow(hexpand=True, vscrollbar_policy=Gtk.PolicyType.NEVER, margin_start=6,
                                        margin_end=6, margin_bottom=6, visible=False)  # (the tabs, while there's more
        self.tabs, self.tabs_key = Gtk.Box(spacing=4), None                                 # than one)
        self.strip.set_child(self.tabs)
        self.append(self.strip)
        self.append(self.prompts)
        self.append(widgets.inside(over, start=False))  # (set into the frame, as the panel's other panes are)
        self.append(self.said)

    def window_bars(self, over):
        """A BrowserWindow's: Emission's own (emission.Chrome: back, forward, reload, the address, a new tab, the
        profile; the tabs' strip), Restart as Native and read-only beside them. Its tabs those of the Bot's page hosted
        here (their own view), or (streamed) a view of stand-ins for them (mirror), kept as its state says."""
        import emission
        self.chrome = chrome = emission.Chrome("")
        self.back, self.fwd, self.reload, self.address, self.new = chrome.back, chrome.fwd, chrome.reload, \
            chrome.entry, chrome.new
        for b, url in ((self.back, "back"), (self.fwd, "forward")):
            b.connect("clicked", lambda _b, u=url: self.ask({"op": "navigate", "url": u, "tab": self.tab()}))
        self.reload.connect("clicked", lambda *_: self.ask({"op": "navigate", "url": "reload", "tab": self.tab()}))
        self.address.connect("activate", lambda e: e.get_text().strip() and self.ask(
            {"op": "navigate", "url": e.get_text().strip(), "tab": self.tab()}))
        self.new.connect("clicked", lambda *_: self.ask({"op": "new_tab", "url": "about:blank"}))
        chrome.who.set_sensitive(False)  # (the Bot's own profile: shown, not changed here)
        chrome.bar.pack_end(self.read_only)
        chrome.bar.pack_end(self.native)
        self.mirror, self.mirrored, self.syncing = Adw.TabView(visible=False), {}, False  # (mirrored: tab id: page)
        self.mirror.set_shortcuts(Adw.TabViewShortcuts.NONE)
        self.mirror.connect("notify::selected-page", lambda *_: self.mirror_picked())
        self.mirror.connect("close-page", self.mirror_closing)
        self.mirror.connect("page-reordered", lambda *_: not self.syncing and GLib.idle_add(  # (moved here: back
            lambda: self.sync_mirror(self.state.get("tabs") or []) and False))              # where it is there)
        chrome.tab_bar.set_view(self.mirror)
        self.tabs, self.tabs_key, self.strip = chrome.tab_bar, None, chrome.strip
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.mirror)
        content.append(self.prompts)
        content.append(over)
        content.append(self.said)
        frame = Adw.ToolbarView(content=content, vexpand=True)
        frame.add_top_bar(chrome.bar)
        frame.add_top_bar(chrome.strip)
        self.append(frame)

    # (whose browser, where)

    def show(self, info):
        """The open chat (the sidebar's info, or None): a Bot's browser watched while this page is shown, on its
        chat's host to begin with."""
        info = info or {}
        bot = bool(info.get("bot")) and info.get("id") and info.get("remote") is not None
        sid = info.get("id") if bot else None
        if sid == self.sid and (not bot or info.get("remote") is self.home):
            return
        self.stop()
        import emission
        self.presence.pointer, self.pointer_seen = emission.BotPointer(), None  # (another Bot's: its own pointer)
        self.sid, self.home, self.info = sid, info.get("remote") if bot else None, info if bot else None
        self.remote = self.home
        self.name = (info.get("bot") or {}).get("name") if bot else None
        held = (widgets.load_settings().get("browser_read_only") or {}).get(sid or "", False)
        self.read_only.set_active(bool(held))
        self.read_only_set(bool(held))
        self.list_hosts()
        self.set_state({})
        self.show_out()
        listen(getattr(self.home, "network", None))
        self.watch_if_shown()
        self.presence_check()

    def list_hosts(self):
        """The hosts that are there (the chat's first): the Bot has a browser of its own on each it's used."""
        hosts = [self.home] if self.home is not None else []
        if self.home is not None:
            hosts += [r for r in self.home.network.alive() if r is not self.home]
        self.hosts = hosts
        self.host_pick.set_model(Gtk.StringList.new([r.info.get("name") or r.host for r in hosts]))
        self.host_pick.set_selected(0)
        self.host_pick.set_visible(len(hosts) > 1)

    def is_out(self):
        """Whether its Bot's browser is out in a window of its own (the side panel's page: not here, then)."""
        return self.bound is None and self.sid is not None and self.sid in DISEMBODIED

    def show_out(self):
        """The side panel's page as its Bot's browser is: out in its own window (the page says so, and only that),
        or here."""
        if self.bound is not None:
            return
        out = self.is_out()
        self.setting = True
        try:
            self.out.set_visible(self.sid is not None)
            self.out.set_active(out)
            self.out.set_tooltip_text("Put Back in the Side Panel" if out else "Move to Its Own Window")
        finally:
            self.setting = False
        for w in (self.back, self.fwd, self.reload, self.address, self.new, self.read_only):
            w.set_visible(not out)  # (its lock too: the window's, while it's out)
        self.gap.set_visible(out)
        self.host_pick.set_visible(not out and len(self.hosts) > 1)
        if out:
            self.stop()
            self.tabs_key = None  # (its tabs listed again once it's back)
            for w in (self.strip, self.prompts, self.said):
                w.set_visible(False)
            self.body.set_visible_child_name("out")
        else:
            self.prompts.set_visible(True)
            if self.body.get_visible_child_name() == "out":
                self.say_status("No pages open", "")
                self.body.set_visible_child_name("status")

    def disembody(self, on):
        """Its Bot's browser out in a window of its own (on), the same pages, nothing reloaded; or back here."""
        if not on:
            if (out := DISEMBODIED.get(self.sid)) is not None:
                out.put_back()
            return
        if self.sid is None or self.info is None or self.is_out():
            return self.show_out()
        info = self.info
        self.stop()  # (its page let go of here first: the window takes it)
        BrowserWindow(self.win, info)

    def came_back(self):
        """Its window closed: its Bot's browser here again, read-only as it was left there (the setting's)."""
        held = bool((widgets.load_settings().get("browser_read_only") or {}).get(self.sid or "", False))
        self.read_only.set_active(held)
        self.read_only_set(held)
        self.show_out()
        self.watch_if_shown()
        self.presence_check()

    def host_picked(self):
        i = self.host_pick.get_selected()
        if self.sid is None or not (0 <= i < len(self.hosts)) or self.hosts[i] is self.remote:
            return
        self.stop()
        self.remote = self.hosts[i]
        self.set_state({})
        self.watch_if_shown()

    def watch_if_shown(self):
        if not self.get_mapped():
            self.stop(keep=True)
            return
        if self.sid is None or self.stream is not None or self.is_out():
            return
        if self.here is not None and self.here.win is None and self.here.why is not None:  # (let go of meanwhile)
            self.here = None
        off = time.monotonic() - self.here_off.get(self.sid, -1e9) < OFF_S
        ours = self.home is self.remote  # (a Bot whose chat's on this Mac: hosted unasked; another's, when you ask)
        had = HERE.get(self.sid) if can_host(self.remote) else None  # (hosted already, or being: joined, whose)
        if had is not None and (had.win is not None or had.why is None) and not off:
            return self.show_here()
        if can_host(self.remote) and ours and not off and self.sid != self.here_busy:
            return self.show_here()
        self.native.set_visible(can_host(self.remote))
        self.say_status("Connecting…", "")
        self.pointer_seen = None  # (its moves before this watch: not played)
        self.stream = Stream(self.remote, {"id": self.sid, "name": self.name}, self.wanted_size(), self.got_frame,
                             self.set_state, self.ended)

    def stop(self, keep=False):
        """No more watching (keep: the same Bot's, watched again once the page is shown again)."""
        self.here_busy = None  # (shown again: a Bot busy before tried here again)
        self.sized_to = None  # (watched again, or another's: laid out at the panel's size again)
        if self.stream is not None:
            self.stream.stop()
            self.stream = None
        if self.here_poll is not None:
            GLib.source_remove(self.here_poll)
            self.here_poll = None
        if not keep:
            self.native.set_visible(False)  # (another Bot's, or none: offered again if it's hostable)
        if self.here is not None and self.here.win is not None:
            self.here.win.pointer.set_acting(False)  # (out of sight: its glow's gone, in again once it's shown)
        if not keep and self.here is not None:
            if self.here.win is not None and self.here.win.body.get_parent() is self.body:
                self.body.remove(self.here.win.body)
            self.here = None
            if self.bound is not None:  # (its tabs' strip on the stand-ins again)
                self.tabs.set_view(self.mirror)
        if not keep:
            self.picture.set_paintable(None)
            self.body.set_visible_child_name("status")

    def ended(self, why):
        self.stream = None
        again = "It restarts in a moment, or when you come back to this window." if self.bound is not None else \
            "It restarts when you open this page again."
        if self.bound is not None:  # (in front: tried again soon, as it is when it's come back to)
            GLib.timeout_add_seconds(RETRY_S, lambda: self.bound.is_active() and self.watch_again() and False)
        if why and self.picture.get_paintable() is None:  # (it never showed: why, in its place)
            self.say_status("Couldn't open the browser", why)
        else:
            self.say_status("The browser stopped", again)
        self.said.set_tooltip_text(why or None)
        self.said.set_label("The browser stopped. " + again)
        self.said.set_visible(self.picture.get_paintable() is not None)  # (the last page still shown: said under it,
        # as the status isn't)

    def watch_again(self):
        """A BrowserWindow's browser watched again if it had stopped (not while it's watched, or hosted)."""
        if self.stream is None and (self.here is None or self.here.win is None):
            self.watch_if_shown()

    # (a Bot of this Mac's: its pages here)

    def restart_native(self):
        """Restart as Native: this Mac's browser of the Bot's, streamed, made Baude's (taken from its own Emission,
        busy or not: its pages reloaded there, the Bot told)."""
        if self.sid is None or not can_host(self.remote) or self.here is not None and self.here.win is not None:
            return
        if self.stream is not None:
            self.stream.stop()
            self.stream = None
        self.here_off.pop(self.sid, None)
        self.here_busy = None
        self.native.set_visible(False)
        here = HERE.get(self.sid)
        if here is not None and here.win is None and here.why is None:  # (being taken already: that one, forced)
            here.force = True
        elif here is None or here.win is None:
            here = HERE[self.sid] = Here(self.win.get_application(), self.sid, self.name, force=True)
        self.say_status("Connecting…", "")
        sid = self.sid
        here.when_ready(lambda h: sid == self.sid and self.get_mapped() and self.here_ready(h))

    def show_here(self):
        sid = self.sid
        if self.here is not None and self.here.sid == sid and self.here.win is not None:
            return self.here_ready(self.here)
        here = HERE.get(sid)
        if here is None:
            here = HERE[sid] = Here(self.win.get_application(), sid, self.name)
        self.say_status("Connecting…", "")
        here.when_ready(lambda h: sid == self.sid and self.get_mapped() and self.here_ready(h))

    def here_ready(self, here):
        if here.force and here.why is not None:  # (Restart as Native asked: why it couldn't, said)
            self.said.set_label(f"Couldn't restart as native: {here.why}")
            self.said.set_visible(True)
        if here.why is not None or here.win is None:  # (couldn't be had here: streamed; a busy Bot's, this time only)
            if here.why == "busy":  # (tried again while it's watched: the Bot may have let it be by then)
                self.here_busy = here.sid
                GLib.timeout_add_seconds(BUSY_RETRY_S, lambda sid=here.sid: self.retry_here(sid) and False)
            else:
                self.here_off[here.sid] = time.monotonic()
            return self.watch_if_shown()
        body = here.win.body
        if body.get_parent() is not None and body.get_parent() is not self.body:  # (shown in another window's pane:
            self.here_off[here.sid] = time.monotonic()                              # streamed in this one)
            return self.watch_if_shown()
        if body.get_parent() is None:
            self.body.add_named(body, "here")
        if self.stream is not None:  # (a stream begun meanwhile: its frames aren't shown over the page)
            self.stream.stop()
            self.stream = None
        self.here = here
        self.native.set_visible(False)
        self.body.set_visible_child_name("here")
        here.win.blocked = self.read_only.get_active()
        if self.bound is not None:  # (its own tabs, in Emission's strip)
            self.tabs.set_view(here.win.view)
        if isinstance(self.get_root(), Gtk.Window):
            guard_keys(self.get_root())
        guard_windows(self.win.get_application())
        self.presence_check()
        self.here_state()
        if self.here_poll is None:
            self.here_poll = GLib.timeout_add(250, self.here_state)

    def retry_here(self, sid):
        if sid != self.sid or self.here_busy != sid or not self.get_mapped():
            return
        if self.stream is not None:
            self.stream.stop()
            self.stream = None
        self.here_busy = None
        self.watch_if_shown()

    def here_state(self):
        if self.here is not None and self.here.win is None:  # (let go of: streamed from wherever it is now)
            self.here, self.here_poll = None, None
            if self.bound is not None:
                self.tabs.set_view(self.mirror)
            self.here_off[self.sid] = time.monotonic()
            self.body.set_visible_child_name("status")
            self.watch_if_shown()
            return False
        if self.here is None or self.here.win is None or not self.get_mapped():
            self.here_poll = None
            return False
        state = self.here.win.watch_state()
        if state != self.state:
            self.set_state(state)
        return True

    def presence_check(self):
        """The glow as the Bot's turn is (its host's listing: acting, at its tools; not writing to you, not done),
        in the accent: given the pointer drawn over its page (hosted: its window's, drawn by MacOverlay; streamed:
        Presence's). As listings change (listen), and as its page is shown or its frames start."""
        if self.sid is None or not self.get_mapped():  # (nothing for a page out of sight, or a chat's)
            return False
        listed = (getattr(self.home, "sessions", None) or {}).get(self.sid) or {}
        acting = bool(listed.get("acting")) and listed.get("state") == "working" and not self.is_out() and \
            getattr(self.home, "state", "alive") == "alive"
        c = widgets.accent_rgba()
        rgb = (c.red, c.green, c.blue)
        if self.here is not None and self.here.win is not None and self.get_mapped():
            self.here.win.pointer.set_acting(acting)
            self.here.win.accent = rgb
            self.presence.pointer.set_acting(False)
        else:
            self.presence.pointer.set_acting(acting and self.stream is not None)
        self.presence.kick()
        return False

    def play_pointer(self, log):
        """The Bot's pointer's moves its Emission's told its watchers (a streamed one's), those not played yet,
        played over its page."""
        if self.stream is None:
            return
        top = max((e.get("seq") or 0 for e in log), default=0)
        if self.pointer_seen is None or top < self.pointer_seen:  # (a watch begun, or an Emission started again:
            self.pointer_seen = top                                # what it did before isn't played)
            return
        for ev in sorted(log, key=lambda e: e.get("seq") or 0):
            if (ev.get("seq") or 0) > self.pointer_seen:
                self.presence.pointer.play(ev)
        self.pointer_seen = top
        self.presence.kick()

    def frame_rect(self, widget):
        """Where a streamed page's frame is drawn, in widget's terms, and its CSS pixels' size there: (x, y, width,
        height, x scale, y scale), or None (none yet)."""
        fw, fh = self.frame_size
        pw, ph = self.picture.get_width(), self.picture.get_height()
        if not fw or not fh or not pw or not ph:
            return None
        s = min(pw / fw, ph / fh)
        ok, at = self.picture.compute_point(widget, Graphene.Point().init(0, 0))
        if not ok:
            return None
        vw, vh = self.view_size or (fw, fh)
        return (at.x + (pw - fw * s) / 2, at.y + (ph - fh * s) / 2, fw * s, fh * s, fw * s / vw, fh * s / vh)

    def say_status(self, title, text):
        self.status.set_title(title)
        self.status.set_description(text)
        if self.picture.get_paintable() is None and self.here is None:
            self.body.set_visible_child_name("status")

    # (what comes)

    def got_frame(self, texture, w, h, view, shown):
        first = self.picture.get_paintable() is None
        self.frame_size = (w, h)
        if view and min(view) > 0:
            self.view_size = tuple(view)
        self.picture.set_paintable(texture)
        self.presence.queue_draw()  # (drawn over the frame where it is now)
        if first:  # (watched from now: the glow as its turn is, a tool call under way already, say)
            self.presence_check()
        self.body.set_visible_child_name("page")
        if self.said.get_label().startswith("The browser stopped"):
            self.said.set_visible(False)
        shown()  # (on screen: the next may come)

    def set_state(self, state):
        self.state = state or {}
        view = self.state.get("view")
        if view and min(view) > 0:  # (a page not laid out yet says 0 by 0: not taken)
            if tuple(view) != self.view_size:
                self.presence.queue_draw()
            self.view_size = tuple(view)
            if self.stream is not None and self.state.get("agent"):
                self.fit_page()
        tabs = self.state.get("tabs") or []
        self.play_pointer(self.state.get("pointer") or [])
        if self.bound is not None:
            return self.window_state(tabs)
        key = [(t.get("tab"), t.get("title"), t.get("url"), bool(t.get("selected"))) for t in tabs]
        if key != self.tabs_key:  # (made again only when they change: a click on one isn't lost to its remaking)
            self.tabs_key = key
            while (c := self.tabs.get_first_child()) is not None:
                self.tabs.remove(c)
            for t in tabs:
                pick = Gtk.ToggleButton(active=t.get("selected", False), tooltip_text=t.get("url") or "")
                pick.set_child(Gtk.Label(label=(t.get("title") or t.get("url") or "New tab")[:28], ellipsize=3))
                pick.add_css_class("flat")
                pick.connect("clicked", lambda b, tid=t["tab"], on=bool(t.get("selected")): (
                    b.set_active(on), self.ask({"op": "select_tab", "tab": tid})))  # (shown picked once it is)
                close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close tab")
                close.add_css_class("flat")
                close.connect("clicked", lambda _b, tid=t["tab"]: self.ask({"op": "close_tab", "tab": tid}))
                tab = Gtk.Box()  # (its name, and its close button beside it)
                tab.add_css_class("linked")
                tab.append(pick)
                tab.append(close)
                self.tabs.append(tab)
            self.strip.set_visible(len(tabs) > 1)
        now = next((t for t in tabs if t.get("selected")), None)
        if now is not None and not self.address.get_delegate().has_focus():
            self.address.set_text(now.get("url") or "")
        self.reload.set_icon_name("process-stop-symbolic" if now and now.get("loading") else "view-refresh-symbolic")
        self.state_prompts()

    def state_prompts(self):
        prompts = self.state.get("prompts") or []
        if [p.get("id") for p in prompts] != self.prompt_ids:  # (made again only when they change: what's typed kept)
            self.prompt_ids = [p.get("id") for p in prompts]
            self.show_prompts(prompts)

    def window_state(self, tabs):
        """A BrowserWindow's bars as its state says: the page in front's address, back, forward, its progress
        (Emission's own, Chrome.show), the window's title; streamed, the stand-ins for its tabs."""
        now = next((t for t in tabs if t.get("selected")), None)
        shown = dict(self.state.get("now") or {"back": True, "forward": True})  # (an Emission before now said: both
        if now is not None:                                                      # offered)
            shown.update(url=now.get("url") or "", loading=bool(now.get("loading")))
        if now is not None or not tabs:
            self.chrome.show(shown, not self.address.get_delegate().has_focus())
        self.chrome.who.set_label(self.state.get("profile") or self.name or "")
        title = (now or {}).get("title") or (now or {}).get("url") or "Emission"
        self.bound.set_title(f"{title} — {self.name or 'Bot'}")
        if self.here is None:
            self.sync_mirror(tabs)
        self.state_prompts()

    def sync_mirror(self, tabs):
        """The stand-ins for a streamed Bot's tabs (in the strip): one for each, named, loading, in their order, the
        one in front picked, as they are there."""
        self.syncing = True
        try:
            ids = [t.get("tab") for t in tabs]
            for tid in [k for k in self.mirrored if k not in ids]:
                self.mirror.close_page(self.mirrored.pop(tid))
            for i, t in enumerate(tabs):
                page = self.mirrored.get(t.get("tab"))
                if page is None:
                    page = self.mirrored[t.get("tab")] = self.mirror.append(Gtk.Box())
                page.set_title(t.get("title") or t.get("url") or "New Tab")
                page.set_tooltip(t.get("url") or "")
                page.set_loading(bool(t.get("loading")))
                if self.mirror.get_page_position(page) != i:  # (in the order they're in there)
                    self.mirror.reorder_page(page, i)
                if t.get("selected") and self.mirror.get_selected_page() is not page:
                    self.mirror.set_selected_page(page)
        finally:
            self.syncing = False

    def mirror_picked(self):
        page = self.mirror.get_selected_page()
        if self.syncing or page is None:
            return
        tid = next((k for k, p in self.mirrored.items() if p is page), None)
        if tid is not None and tid != self.tab():
            self.ask({"op": "select_tab", "tab": tid})

    def mirror_closing(self, view, page):
        """A stand-in closed: here, as its strip's close was asked (its tab closed there, gone from here once it
        is); by sync_mirror, as it's gone there."""
        if self.syncing:
            view.close_page_finish(page, True)
            return True
        tid = next((k for k, p in self.mirrored.items() if p is page), None)
        view.close_page_finish(page, False)
        if tid is not None:
            self.ask({"op": "close_tab", "tab": tid})
        return True

    def show_prompts(self, prompts):
        """What the Bot's pages wait on, each a card: answered here, or by the Bot."""
        while (c := self.prompts.get_first_child()) is not None:
            self.prompts.remove(c)
        for p in prompts:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
            card.add_css_class("card")
            kind = p.get("kind")
            text = {"sign_in": f"{p.get('host') or 'A site'} asks to sign in" + (
                f" (“{p['realm']}”)" if p.get("realm") else ""), "files": "The page asks for files to upload"}.get(
                kind, p.get("message") or "")
            card.append(Gtk.Label(label=text, wrap=True, xalign=0, margin_start=8, margin_end=8, margin_top=6))
            fields = {}
            if kind == "prompt":
                fields["text"] = Gtk.Entry(text=p.get("default") or "")
            elif kind == "sign_in":
                fields["user"] = Gtk.Entry(placeholder_text="User name")
                fields["password"] = Gtk.PasswordEntry(placeholder_text="Password", show_peek_icon=True)
            for f in fields.values():
                f.set_margin_start(8)
                f.set_margin_end(8)
                card.append(f)
            row = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_end=8, margin_bottom=6)
            for label, accept in (("Cancel", False), ("OK", True)) if kind != "alert" else (("OK", True),):
                b = Gtk.Button(label=label)
                if accept:
                    b.add_css_class("suggested-action")
                b.connect("clicked", lambda _b, pid=p["id"], a=accept, fs=fields: self.ask(
                    {"op": "answer_prompt", "id": pid, "accept": a, **{k: f.get_text() for k, f in fs.items()}}))
                row.append(b)
            if kind == "files":
                row.get_last_child().set_sensitive(False)  # (files are the Bot's to give, from its host)
            card.append(row)
            self.prompts.append(card)

    # (what's done here: in order, one after another)

    def tab(self):
        return self.state.get("selected")

    def ask(self, call, done=None):
        """call to the Bot's Emission (as its tools' are: real input there), after what was asked before it: on this
        machine straight to its socket, elsewhere through its host. Nothing, while it's read-only."""
        if self.sid is None or self.remote is None or self.read_only.get_active():
            return
        sid, remote = self.sid, self.remote
        call = {**call, "from_pane": True}  # (not the Bot's: not its activity, nor what it's to be told)

        def answered(r):
            if sid != self.sid or remote is not self.remote:
                return
            why = r.get("error") if isinstance(r, dict) else None
            if why:
                self.said.set_label("Can't connect to the browser." if why in UNREACHED else
                                    f"Couldn't {DOING.get(call.get('op'), 'do that')}.")
                self.said.set_tooltip_text(why)
                self.said.set_visible(True)
            elif not self.said.get_label().startswith("The browser stopped"):
                self.said.set_visible(False)
            if done:
                done(r)
        if self.here is not None and self.here.win is not None:
            return self.here.call(call, lambda r: GLib.idle_add(lambda: answered(r) and False))
        self.asks.put(remote, {"id": sid, "name": self.name}, call, answered)

    def read_only_set(self, on):
        for w in self.drives:
            w.set_sensitive(not on)
        if self.bound is not None:
            self.chrome.who.set_sensitive(False)
        if self.here is not None and self.here.win is not None:
            self.here.win.blocked = on
        if self.sid is None:
            return
        if self.stream is not None and self.state.get("agent"):  # (driven again: fitted to the panel; read-only: the
            self.sized_to = None                                    # Bot's page at its own size again)
            if on:
                self.view_size = None  # (the page's size as it was fitted isn't what it'll be: fitted again after)
                self.asks.put(self.remote, {"id": self.sid, "name": self.name}, {"op": "resize", "restore": True, "from_pane": True},
                              lambda _r: None)
            else:
                self.fit_page()
        held = dict(widgets.load_settings().get("browser_read_only") or {})
        if bool(held.get(self.sid)) == on:
            return
        if on:
            held[self.sid] = True
        else:
            held.pop(self.sid, None)
        widgets.save_settings(browser_read_only=held)

    def to_page(self, x, y):
        """A point of the picture as the page's (CSS pixels), or None off it."""
        fw, fh = self.frame_size
        pw, ph = self.picture.get_width(), self.picture.get_height()
        if not fw or not fh or not pw or not ph:
            return None
        s = min(pw / fw, ph / fh)
        ox, oy = (pw - fw * s) / 2, (ph - fh * s) / 2
        fx, fy = (x - ox) / s, (y - oy) / s
        if not (0 <= fx <= fw and 0 <= fy <= fh):
            return None
        vw, vh = self.view_size or (fw, fh)
        return round(fx * vw / fw, 1), round(fy * vh / fh, 1)

    def input(self):
        press = Gtk.GestureDrag(button=0)  # (one gesture for press, move, let go: a click or a drag, as it turns out)
        press.connect("drag-begin", self.pressed)
        press.connect("drag-update", self.dragged)
        press.connect("drag-end", self.released)
        self.picture.add_controller(press)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self.scrolled)
        self.picture.add_controller(scroll)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.moved)
        self.picture.add_controller(motion)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.keyed)
        self.picture.add_controller(keys)
        self.last_click = (0.0, None, 0)  # (when, where, its count: a double click's second press counted from it)

    def pressed(self, gesture, x, y):
        self.picture.grab_focus()
        self.drag = {"path": [(x, y)], "button": gesture.get_current_button(),
                     "mods": self.mods(gesture.get_current_event_state())}

    def dragged(self, gesture, dx, dy):
        if self.drag is None:
            return
        x0, y0 = self.drag["path"][0]
        if len(self.drag["path"]) > 1 or abs(dx) > DRAG_PX or abs(dy) > DRAG_PX:
            self.drag["path"].append((x0 + dx, y0 + dy))

    def released(self, gesture, dx, dy):
        press, self.drag = self.drag, None
        if press is None:
            return
        path = press["path"]
        self.flush_typing()
        if len(path) > 1 and press["button"] == Gdk.BUTTON_PRIMARY:  # (moved while pressed: a drag)
            step = max(1, len(path) // 20)  # (at most about 20 points of it: the shape kept)
            points = [p for p in (self.to_page(*q) for q in path[::step] + [path[-1]]) if p is not None]
            if len(points) >= 2:
                self.ask({"op": "drag", "tab": self.tab(), "path": points, "css": True, "quick": True})
            return
        x, y = path[0]
        at = self.to_page(x, y)
        if at is None:
            return
        when, where, count = self.last_click
        near = where is not None and abs(where[0] - x) < 6 and abs(where[1] - y) < 6
        n = count + 1 if near and time.monotonic() - when < 0.45 else 1  # (pressed again, quickly, in one place)
        self.last_click = (time.monotonic(), (x, y), n)
        self.ask({"op": "click", "tab": self.tab(), "x": at[0], "y": at[1], "css": True, "press": n, "quick": True,
                  "button": {3: "right", 2: "middle"}.get(press["button"], "left"), "modifiers": press["mods"]})

    def moved(self, _c, x, y):
        self.pointer = (x, y)
        if self.hover_source is not None:
            GLib.source_remove(self.hover_source)
        self.hover_source = GLib.timeout_add(HOVER_MS, self.hover)

    def hover(self):
        self.hover_source = None
        if self.stream is None and self.here is None:  # (the last frame of one that's stopped: nothing to hover)
            return False
        at = self.to_page(*self.pointer) if self.pointer and self.drag is None else None
        if at is not None and not self.asks.pending:  # (only with nothing else waiting: never queued behind)
            self.ask({"op": "hover", "tab": self.tab(), "x": at[0], "y": at[1], "css": True, "quick": True})
        return False

    def scrolled(self, ctl, dx, dy):
        unit = 50 if ctl.get_unit() == Gdk.ScrollUnit.WHEEL else 1
        self.scroll_due[0] += dx * unit
        self.scroll_due[1] += dy * unit
        if self.scroll_source is None:
            self.scroll_source = GLib.timeout_add(SCROLL_MS, self.scroll_send)
        return True

    def scroll_send(self):
        self.scroll_source = None
        dx, dy = round(self.scroll_due[0]), round(self.scroll_due[1])
        self.scroll_due = [0.0, 0.0]
        at = self.to_page(*self.pointer) if self.pointer else None
        if (dx or dy) and at is not None:
            self.ask({"op": "scroll", "tab": self.tab(), "x": at[0], "y": at[1], "css": True, "dx": dx, "dy": dy,
                      "quick": True})
        return False

    @staticmethod
    def mods(state):
        out = []
        for flag, name in ((Gdk.ModifierType.SHIFT_MASK, "shift"), (Gdk.ModifierType.CONTROL_MASK, "ctrl"),
                           (Gdk.ModifierType.ALT_MASK, "alt"), (Gdk.ModifierType.META_MASK, "cmd"),
                           (Gdk.ModifierType.SUPER_MASK, "cmd")):
            if state & flag and name not in out:
                out.append(name)
        return out

    def keyed(self, _c, keyval, _code, state):
        mods = [m for m in self.mods(state) if m != "shift"]
        name = Gdk.keyval_name(keyval) or ""
        ch = chr(Gdk.keyval_to_unicode(keyval)) if Gdk.keyval_to_unicode(keyval) else ""
        if ch and ch.isprintable() and not mods and (name != "space" or self.typed):
            self.typed += ch  # (text: typed, a few characters at once; a space alone is the Space key)
            if self.type_source is not None:
                GLib.source_remove(self.type_source)
            self.type_source = GLib.timeout_add(TYPE_MS, self.flush_typing)
            return True
        key = KEYS.get(name) or (ch.lower() if ch and mods else None)
        if key is None:
            return False
        self.flush_typing()
        combo = "+".join([*self.mods(state), key]) if key in KEYS.values() else "+".join([*mods, key])
        self.ask({"op": "key", "tab": self.tab(), "keys": combo})
        return True

    def flush_typing(self):
        if self.type_source is not None:
            GLib.source_remove(self.type_source)
            self.type_source = None
        if self.typed:
            text, self.typed = self.typed, ""
            self.ask({"op": "type", "tab": self.tab(), "text": text, "anywhere": True})
        return False

    # (its size)

    def wanted_size(self):
        scale = self.get_scale_factor() or 1
        return (max(200, (self.picture.get_width() or 800) * scale),
                max(200, (self.picture.get_height() or 600) * scale))

    def size_check(self):
        """The frames asked for at the size the page is shown at, once it's settled there (and an agent's page laid
        out at it)."""
        if self.stream is not None and self.size_source is None:
            refit = self.state.get("agent") and self.sized_to is not None and self.page_size() != self.sized_to
            if self.wanted_size() != self.stream.size or refit:
                self.size_source = GLib.timeout_add(SIZE_MS, self.size_send)
        return True

    def size_send(self):
        self.size_source = None
        if self.stream is not None:
            if self.wanted_size() != self.stream.size:
                self.stream.resize(self.wanted_size())
            if self.state.get("agent"):
                self.fit_page()
        return False

    def page_size(self):
        """The side panel's page in CSS pixels: what a Bot's page is laid out at, to fill it."""
        return (max(200, self.picture.get_width() or self.body.get_width() or 400),
                max(200, self.picture.get_height() or self.body.get_height() or 600))

    def fit_page(self):
        """A streamed Bot's page (an agent's, out of sight: never someone's window) laid out at the panel's shape,
        once for each size it's shown at; not while it's read-only (watched, not disturbed). Its own size again once
        no one watches it (Emission puts it back)."""
        if self.read_only.get_active():
            return
        size = self.page_size()
        if size == self.sized_to:
            return
        self.sized_to = size
        if self.view_size and tuple(self.view_size) == size:
            return
        self.asks.put(self.remote, {"id": self.sid, "name": self.name},
                      {"op": "resize", "width": size[0], "height": size[1], "fit": True, "from_pane": True}, lambda _r: None)
