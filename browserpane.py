"""A Bot's browser, on the side panel's Browser page: its own Emission (emission.py), wherever its host is, watched and
driven. Its frames come as the page changes: from an Emission on this machine straight from it (its door), the pixels
in shared memory, no picture made or copied through anything between; from one elsewhere through its host process
(wire's emission_watch), each frame a JPEG the size the page is shown at. What you do with it (click, type, keys,
scroll, its tabs, its address, the prompts a page waits on) goes to it as the Bot's tools do (real input there).
Read-only: watched, not driven (kept for each Bot, as a setting)."""
import json
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
KEYS = {"Return": "Enter", "KP_Enter": "Enter", "BackSpace": "Backspace", "Delete": "Delete", "Escape": "Escape",
        "Tab": "Tab", "ISO_Left_Tab": "Tab", "Left": "Left", "Right": "Right", "Up": "Up", "Down": "Down",
        "Home": "Home", "End": "End", "Page_Up": "PageUp", "Page_Down": "PageDown"}


class Stream:
    """A watch of a Bot's Emission, read on a thread of its own: frame(texture, w, h, view) and state(state) are run on
    the main loop as they come, ended() once it's over."""

    def __init__(self, remote, bot, size, frame, state, ended):
        self.remote, self.bot, self.size = remote, bot, size
        self.on_frame, self.on_state, self.on_ended = frame, state, ended
        self.stopped, self.lock, self.sock, self.ch = False, threading.Lock(), None, None
        self.memories = {}  # (shared memory Emission writes frames in, by name: opened once)
        threading.Thread(target=self.run, daemon=True, name="browser-watch").start()

    def run(self):
        try:
            (self.run_here if self.remote.local else self.run_there)()
        except Exception as e:  # (said in the pane: it can be tried again)
            why = f"{type(e).__name__}: {e}"
            if not self.stopped:
                GLib.idle_add(lambda: self.on_ended(why) and False)
            return
        if not self.stopped:
            GLib.idle_add(lambda: self.on_ended(None) and False)

    def run_here(self):
        """This machine's Emission: its door, frames in shared memory."""
        pid = emissionlink.bot_profile_id(self.bot["id"])
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(emissionlink.door_path(pid))
        except (FileNotFoundError, ConnectionRefusedError):
            sock.close()
            got = self.remote.call({"op": "emission", "bot": self.bot, "call": {"op": "tabs"}}, timeout=60)
            if got.get("op") == "error":  # (it's started by its host, as the Bot's tools would start it)
                raise OSError(got.get("error") or "its Emission didn't start")
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(emissionlink.door_path(pid))
        self.sock = sock
        self.say({"op": "watch", "shm": True, "max_w": self.size[0], "max_h": self.size[1]})
        buffer = b""
        while not self.stopped and (part := sock.recv(1 << 20)):
            buffer += part
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                self.took(json.loads(line))

    def run_there(self):
        """Another host's Emission: through that host process, frames as JPEG."""
        ch, _info = wire.reach(self.remote.where, timeout=30)
        self.ch = ch
        ch.send({"op": "emission_watch", "req": 1, "bot": self.bot, "max_w": self.size[0], "max_h": self.size[1]})
        threading.Thread(target=self.ping, daemon=True, name="browser-ping").start()
        ch.sock.settimeout(None)
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
            if not self.stopped and not self.ch.send({"op": "ping"}):
                return

    def took(self, msg):
        if "state" in msg:
            state = msg["state"]
            GLib.idle_add(lambda: self.on_state(state) and False)
            return
        if "frame" not in msg:
            return
        w, h = int(msg["w"]), int(msg["h"])
        if msg.get("shm"):
            from multiprocessing import shared_memory
            name = msg["shm"]
            if name not in self.memories:
                for old in self.memories.values():
                    old.close()
                self.memories.clear()
                try:
                    self.memories[name] = shared_memory.SharedMemory(name, track=False)
                except TypeError:  # (a Python before 3.13)
                    self.memories[name] = shared_memory.SharedMemory(name)
            stride = int(msg["stride"])
            data = GLib.Bytes.new(bytes(self.memories[name].buf[:stride * h]))
            self.say({"ack": msg["frame"]})  # (copied: Emission may write the next)
            made = lambda: Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED, data, stride)  # noqa
        else:
            import base64
            data = GLib.Bytes.new(base64.b64decode(msg["jpeg"]))
            self.say({"ack": msg["frame"]})
            made = lambda: Gdk.Texture.new_from_bytes(data)  # noqa: E731
        view = msg.get("view")
        GLib.idle_add(lambda: self.on_frame(made(), w, h, view) and False)

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

    def stop(self):
        self.stopped = True
        for closing in (self.sock, self.ch):
            try:
                if closing is not None:
                    closing.close()
            except OSError:
                pass
        for mem in self.memories.values():
            try:
                mem.close()
            except (OSError, BufferError):
                pass
        self.memories.clear()


class BrowserPane(Gtk.Box):
    """The Browser page: the open Bot's browser, watched while the page is shown."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.sid, self.remote, self.name, self.stream = win, None, None, None, None
        self.frame_size, self.view_size, self.state = (0, 0), None, {}
        self.typed, self.type_source, self.scroll_due, self.scroll_source = "", None, [0.0, 0.0, 0, 0], None
        self.hover_source, self.size_source, self.shown_for = None, None, None
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
        self.read_only = Gtk.ToggleButton(icon_name="changes-prevent-symbolic", tooltip_text="Read-only: watch, "
                                          "don't drive (the Bot still does)")
        self.read_only.add_css_class("flat")
        self.read_only.connect("toggled", lambda b: self.read_only_set(b.get_active()))
        bar.append(self.read_only)
        self.append(bar)
        strip = Gtk.Box(spacing=2, margin_start=6, margin_end=6)
        self.tabs = Gtk.Box(spacing=2)
        tabs_scroll = Gtk.ScrolledWindow(hexpand=True, vscrollbar_policy=Gtk.PolicyType.NEVER, child=self.tabs)
        strip.append(tabs_scroll)
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
                                     description="The Bot's own browser shows here, as it uses it.")
        self.body = Gtk.Stack(vexpand=True)
        self.body.add_named(self.status, "status")
        self.body.add_named(self.picture, "page")
        self.append(self.body)
        self.input()
        self.said = Gtk.Label(wrap=True, xalign=0, visible=False, margin_start=8, margin_end=8)
        self.said.add_css_class("dim-label")
        self.append(self.said)
        self.drives = [self.back, self.fwd, self.reload, self.address, new]  # (off while it's read-only)
        self.connect("notify::mapped", lambda *_: self.watch_if_shown())
        GLib.timeout_add(500, self.size_check)

    # (whose browser)

    def show(self, info):
        """The open chat (the sidebar's info, or None): a Bot's browser watched while this page is shown."""
        info = info or {}
        bot = bool(info.get("bot")) and info.get("id") and info.get("remote") is not None
        sid = info.get("id") if bot else None
        if sid == self.sid and (not bot or info.get("remote") is self.remote):
            return
        self.stop()
        self.sid, self.remote = sid, info.get("remote") if bot else None
        self.name = (info.get("bot") or {}).get("name") if bot else None
        held = (widgets.load_settings().get("browser_read_only") or {}).get(sid or "", False)
        self.read_only.set_active(bool(held))
        self.set_state({})
        self.watch_if_shown()

    def watch_if_shown(self):
        if self.sid is None or self.stream is not None or not self.get_mapped():
            if not self.get_mapped():
                self.stop(keep=True)
            return
        self.say_status("Connecting", f"{self.name or 'The Bot'}'s browser, on its host.")
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
        self.say_status("Not showing", why or "Its Emission went: it's started again when you come back here.")

    def say_status(self, title, text):
        self.status.set_title(title)
        self.status.set_description(text)
        if self.picture.get_paintable() is None:
            self.body.set_visible_child_name("status")

    # (what comes)

    def got_frame(self, texture, w, h, view):
        self.frame_size = (w, h)
        self.picture.set_paintable(texture)
        self.body.set_visible_child_name("page")

    def set_state(self, state):
        self.state = state or {}
        self.view_size = tuple(self.state.get("view") or ()) or None
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
        if now is not None and not self.address.has_focus() and not self.address.get_delegate().has_focus():
            self.address.set_text(now.get("url") or "")
        self.reload.set_icon_name("process-stop-symbolic" if now and now.get("loading") else "view-refresh-symbolic")
        self.show_prompts(self.state.get("prompts") or [])

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

    # (what's done here)

    def tab(self):
        return self.state.get("selected")

    def ask(self, call, done=None):
        """call to the Bot's Emission (as its tools' are: real input there), its answer to done: on this machine
        straight to its door, elsewhere through its host. Nothing, while it's read-only."""
        if self.sid is None or self.remote is None or self.read_only.get_active():
            return
        sid = self.sid

        def answered(r):
            if sid != self.sid:
                return
            why = r.get("error") if isinstance(r, dict) and (r.get("op") == "error" or r.get("error")) else None
            self.said.set_label(f"The browser said: {why}" if why else "")
            self.said.set_visible(bool(why))
            if done:
                done(r)
        if self.remote.local:
            def there():
                try:
                    got = emissionlink.call(emissionlink.bot_profile_id(sid), call, timeout=60)
                except (OSError, ValueError) as e:
                    got = {"error": str(e)}
                GLib.idle_add(lambda: answered(got) and False)
            threading.Thread(target=there, daemon=True, name="browser-ask").start()
        else:
            self.remote.request({"op": "emission", "bot": {"id": sid, "name": self.name}, "call": call}, answered)

    def read_only_set(self, on):
        for w in self.drives:
            w.set_sensitive(not on)
        self.prompts.set_sensitive(not on)
        self.tabs.set_sensitive(not on)
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
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self.clicked)
        self.picture.add_controller(click)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self.scrolled)
        self.picture.add_controller(scroll)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.moved)
        self.picture.add_controller(motion)
        self.pointer = None
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.keyed)
        self.picture.add_controller(keys)

    def clicked(self, gesture, n, x, y):
        self.picture.grab_focus()
        at = self.to_page(x, y)
        if at is None:
            return
        self.flush_typing()
        mods = self.mods(gesture.get_current_event_state())
        self.ask({"op": "click", "tab": self.tab(), "x": at[0], "y": at[1], "css": True, "count": n,
                  "button": "right" if gesture.get_current_button() == 3 else "left", "modifiers": mods})

    def moved(self, _c, x, y):
        self.pointer = (x, y)
        if self.hover_source is not None:
            GLib.source_remove(self.hover_source)
        self.hover_source = GLib.timeout_add(HOVER_MS, self.hover)

    def hover(self):
        self.hover_source = None
        at = self.to_page(*self.pointer) if self.pointer else None
        if at is not None:
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
        self.scroll_due[:2] = [0.0, 0.0]
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
        if ch and ch.isprintable() and not mods:  # (text: typed, a few characters at once)
            self.typed += ch
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
            self.ask({"op": "type", "tab": self.tab(), "text": text})
        return False

    # (its size)

    def wanted_size(self):
        scale = self.get_scale_factor() or 1
        return (max(200, (self.picture.get_width() or 800) * scale), max(200, (self.picture.get_height() or 600) * scale))

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
