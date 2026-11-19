"""A Bot's browser, on the side panel's Browser page: its own Emission (emission.py), on its chat's host or any other it
uses, watched and driven. Its frames come as the page changes: from an Emission of this machine's own host process
straight from it (its door), the pixels in shared memory, no picture made or copied through anything between; from one
elsewhere through its host process (wire's emission_watch), each frame a JPEG the size the page is shown at. What you
do with it (click, drag, type, keys, scroll, its tabs, its address, the prompts a page waits on) goes to it in the order
it's done, as the Bot's tools do (real input there). Read-only: watched, not driven (kept for each Bot, as a setting)."""
import json
import queue
import socket
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

import widgets  # noqa: E402
from baudelib import emissionlink, wire  # noqa: E402

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
        """This machine's own Emission: its door, frames in shared memory."""
        pid = emissionlink.bot_profile_id(self.bot["id"])
        for attempt in range(2):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(emissionlink.door_path(pid))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                sock.close()
                if attempt:
                    raise OSError("its Emission didn't open")
                got = self.remote.call({"op": "emission", "bot": self.bot, "call": {"op": "tabs"}}, timeout=90)
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
        """A line to Emission (here: its door; there: through its host)."""
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
                if remote.mine:  # (straight to its door: no host between)
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


class BrowserPane(Gtk.Box):
    """The Browser page: the open Bot's browser on a host (its chat's, or another it uses), watched while shown."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.sid, self.remote, self.home, self.name, self.stream = win, None, None, None, None, None
        self.frame_size, self.view_size, self.state, self.prompt_ids = (0, 0), None, {}, None
        self.typed, self.type_source, self.scroll_due, self.scroll_source = "", None, [0.0, 0.0], None
        self.hover_source, self.size_source, self.pointer, self.drag = None, None, None, None
        self.asks, self.hosts = Asks(), []
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
        self.host_pick = Gtk.DropDown(tooltip_text="Host")
        self.host_pick.connect("notify::selected", lambda *_: self.host_picked())
        bar.append(self.host_pick)
        self.read_only = Gtk.ToggleButton(icon_name="changes-prevent-symbolic", tooltip_text="Read-only")
        self.read_only.add_css_class("flat")
        self.read_only.connect("toggled", lambda b: self.read_only_set(b.get_active()))
        bar.append(self.read_only)
        self.append(bar)
        strip = Gtk.Box(spacing=2, margin_start=6, margin_end=6)
        self.tabs = Gtk.Box(spacing=2)
        strip.append(Gtk.ScrolledWindow(hexpand=True, vscrollbar_policy=Gtk.PolicyType.NEVER, child=self.tabs))
        new = Gtk.Button(icon_name="tab-new-symbolic", tooltip_text="New tab")
        new.add_css_class("flat")
        new.connect("clicked", lambda *_: self.ask({"op": "new_tab", "url": "about:blank"}))
        strip.append(new)
        self.append(strip)
        self.prompts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=6, margin_end=6)
        self.append(self.prompts)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True,
                                   focusable=True)
        self.status = Adw.StatusPage(icon_name="web-browser-symbolic", title="Browser", vexpand=True,
                                     description="No pages open")
        self.body = Gtk.Stack(vexpand=True)
        self.body.add_named(self.status, "status")
        self.body.add_named(self.picture, "page")
        self.append(self.body)
        self.said = Gtk.Label(wrap=True, xalign=0, visible=False, margin_start=8, margin_end=8, margin_bottom=4)
        self.said.add_css_class("dim-label")
        self.append(self.said)
        self.drives = [self.back, self.fwd, self.reload, self.address, new, self.tabs, self.prompts]
        self.input()
        self.connect("map", lambda *_: self.watch_if_shown())  # (shown: watched; hidden: let go of, till it's back)
        self.connect("unmap", lambda *_: self.stop(keep=True))
        self.size_timer = GLib.timeout_add(500, self.size_check)
        self.connect("destroy", lambda *_: (GLib.source_remove(self.size_timer), self.stop()))

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
        self.sid, self.home = sid, info.get("remote") if bot else None
        self.remote = self.home
        self.name = (info.get("bot") or {}).get("name") if bot else None
        held = (widgets.load_settings().get("browser_read_only") or {}).get(sid or "", False)
        self.read_only.set_active(bool(held))
        self.read_only_set(bool(held))
        self.list_hosts()
        self.set_state({})
        self.watch_if_shown()

    def list_hosts(self):
        """The hosts that are there (the chat's first): the Bot has a browser of its own on each it's used."""
        hosts = [self.home] if self.home is not None else []
        if self.home is not None:
            hosts += [r for r in self.home.network.alive() if r is not self.home]
        self.hosts = hosts
        self.host_pick.set_model(Gtk.StringList.new([r.info.get("name") or r.host for r in hosts]))
        self.host_pick.set_selected(0)
        self.host_pick.set_visible(len(hosts) > 1)

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
        if self.sid is None or self.stream is not None:
            return
        self.say_status("Connecting…", "")
        self.stream = Stream(self.remote, {"id": self.sid, "name": self.name}, self.wanted_size(), self.got_frame,
                             self.set_state, self.ended)

    def stop(self, keep=False):
        """No more watching (keep: the same Bot's, watched again once the page is shown again)."""
        if self.stream is not None:
            self.stream.stop()
            self.stream = None
        if not keep:
            self.picture.set_paintable(None)
            self.body.set_visible_child_name("status")

    def ended(self, why):
        self.stream = None
        self.say_status("The browser stopped", "It restarts when you open this page again.")
        self.said.set_label("Can't connect to the browser.")
        self.said.set_tooltip_text(why or None)
        self.said.set_visible(True)

    def say_status(self, title, text):
        self.status.set_title(title)
        self.status.set_description(text)
        if self.picture.get_paintable() is None:
            self.body.set_visible_child_name("status")

    # (what comes)

    def got_frame(self, texture, w, h, view, shown):
        self.frame_size = (w, h)
        if view and min(view) > 0:
            self.view_size = tuple(view)
        self.picture.set_paintable(texture)
        self.body.set_visible_child_name("page")
        if self.said.get_label().startswith("Can't connect"):
            self.said.set_visible(False)
        shown()  # (on screen: the next may come)

    def set_state(self, state):
        self.state = state or {}
        view = self.state.get("view")
        if view and min(view) > 0:  # (a page not laid out yet says 0 by 0: not taken)
            self.view_size = tuple(view)
        tabs = self.state.get("tabs") or []
        while (c := self.tabs.get_first_child()) is not None:
            self.tabs.remove(c)
        for t in tabs:
            b = Gtk.ToggleButton(active=t.get("selected", False), tooltip_text=t.get("url") or "")
            label = Gtk.Label(label=(t.get("title") or t.get("url") or "New tab")[:28], ellipsize=3)
            box = Gtk.Box(spacing=4)
            box.append(label)
            close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close tab")
            close.add_css_class("flat")
            close.connect("clicked", lambda _b, tid=t["tab"]: self.ask({"op": "close_tab", "tab": tid}))
            box.append(close)
            b.set_child(box)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, tid=t["tab"]: self.ask({"op": "select_tab", "tab": tid}))
            self.tabs.append(b)
        now = next((t for t in tabs if t.get("selected")), None)
        if now is not None and not self.address.get_delegate().has_focus():
            self.address.set_text(now.get("url") or "")
        self.reload.set_icon_name("process-stop-symbolic" if now and now.get("loading") else "view-refresh-symbolic")
        prompts = self.state.get("prompts") or []
        if [p.get("id") for p in prompts] != self.prompt_ids:  # (made again only when they change: what's typed kept)
            self.prompt_ids = [p.get("id") for p in prompts]
            self.show_prompts(prompts)

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
        machine straight to its door, elsewhere through its host. Nothing, while it's read-only."""
        if self.sid is None or self.remote is None or self.read_only.get_active():
            return
        sid, remote = self.sid, self.remote

        def answered(r):
            if sid != self.sid or remote is not self.remote:
                return
            why = r.get("error") if isinstance(r, dict) else None
            self.said.set_label("Couldn't reach the browser" if why else "")
            self.said.set_tooltip_text(why or None)
            self.said.set_visible(bool(why))
            if done:
                done(r)
        self.asks.put(remote, {"id": sid, "name": self.name}, call, answered)

    def read_only_set(self, on):
        for w in self.drives:
            w.set_sensitive(not on)
        if self.sid is None:
            return
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
                self.ask({"op": "drag", "tab": self.tab(), "path": points, "css": True})
            return
        x, y = path[0]
        at = self.to_page(x, y)
        if at is None:
            return
        when, where, count = self.last_click
        near = where is not None and abs(where[0] - x) < 6 and abs(where[1] - y) < 6
        n = count + 1 if near and time.monotonic() - when < 0.45 else 1  # (pressed again, quickly, in one place)
        self.last_click = (time.monotonic(), (x, y), n)
        self.ask({"op": "click", "tab": self.tab(), "x": at[0], "y": at[1], "css": True, "press": n,
                  "button": {3: "right", 2: "middle"}.get(press["button"], "left"), "modifiers": press["mods"]})

    def moved(self, _c, x, y):
        self.pointer = (x, y)
        if self.hover_source is not None:
            GLib.source_remove(self.hover_source)
        self.hover_source = GLib.timeout_add(HOVER_MS, self.hover)

    def hover(self):
        self.hover_source = None
        at = self.to_page(*self.pointer) if self.pointer and self.drag is None else None
        if at is not None and not self.asks.pending:  # (only with nothing else waiting: never queued behind)
            self.ask({"op": "hover", "tab": self.tab(), "x": at[0], "y": at[1], "css": True})
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
            self.ask({"op": "scroll", "tab": self.tab(), "x": at[0], "y": at[1], "css": True, "dx": dx, "dy": dy})
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
        """The frames asked for at the size the page is shown at, once it's settled there."""
        if self.stream is not None:
            size = self.wanted_size()
            if size != self.stream.size and self.size_source is None:
                self.size_source = GLib.timeout_add(SIZE_MS, self.size_send)
        return True

    def size_send(self):
        self.size_source = None
        if self.stream is not None:
            self.stream.resize(self.wanted_size())
        return False
