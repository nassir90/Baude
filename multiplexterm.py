"""multiplexterm — a Multiplex terminal (Terminal: VTE's, and the glow round its edges while a Bot's at its tools in
it), and what lets a Bot reach every one this process has (Terminals: its socket, baudelib.multiplexlink's). Multiplex's
window (multiplex.py) has its tabs of them; Baude's and Record's Kubernetes view a pod's shell.
"""
import json
import os
import re
import signal
import socket
import sys
import time

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
try:
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte  # noqa: E402
except (ValueError, ImportError):
    Vte = None
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk, Pango  # noqa: E402
from baudelib import clearrelay, deps, multiplexlink, tokens, wording  # noqa: E402

SCROLLBACK = 10000  # (lines kept above the screen)
SETTLE_S = 0.4  # (no output this long: what was typed has had its answer, as far as a Bot's told)
SETTLE_MOST_S = 5  # (and no longer waited for, after typing)
READ_MOST = 2000  # (lines read at once, the screen's and those above it)
GLOW_IN_S, GLOW_OUT_S, GLOW_HOLD_S = (tokens.seconds(f"pointer.{k}") for k in ("glow-in", "glow-out", "glow-hold"))
GLOW_TEXTURES = {}

KEYS = {"enter": "\r", "return": "\r", "tab": "\t", "escape": "\x1b", "esc": "\x1b", "backspace": "\x7f",
        "delete": "\x1b[3~", "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D", "home": "\x1b[H",
        "end": "\x1b[F", "pageup": "\x1b[5~", "pagedown": "\x1b[6~", "insert": "\x1b[2~", "space": " ",
        "f1": "\x1bOP", "f2": "\x1bOQ", "f3": "\x1bOR", "f4": "\x1bOS", "f5": "\x1b[15~", "f6": "\x1b[17~",
        "f7": "\x1b[18~", "f8": "\x1b[19~", "f9": "\x1b[20~", "f10": "\x1b[21~", "f11": "\x1b[23~", "f12": "\x1b[24~"}


def missing():
    """VTE (a Dep: baudelib.deps'), if its GTK 4 build isn't here to draw terminals with; else None."""
    return deps.missing("vte", here=Vte is not None)


def available():
    """Why there are no terminals here, in a line (Multiplex's, on stderr), or None."""
    dep = missing()
    return None if dep is None else deps.say(dep, "multiplex.terminal_menu")


def shortcuts(widget, actions):
    prefix = "<Meta>" if sys.platform == "darwin" else "<Control><Shift>"
    keys = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
    for key, action in actions.items():
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(prefix + key),
                                           Gtk.CallbackAction.new(lambda *_, action=action: (action(), True)[1])))
    widget.add_controller(keys)


def key_bytes(name):
    """A key as a keyboard sends it to the program in the terminal: Enter, Up, ctrl+c, alt+b, a character."""
    parts = name.split("+")
    base, mods = parts[-1], {m.lower() for m in parts[:-1]}
    low = base.lower()
    if not base:
        raise ValueError(f"no key in {name!r}")
    if low in KEYS:
        out = KEYS[low]
        if "shift" in mods and low == "tab":
            out = "\x1b[Z"
    elif len(base) == 1:
        out = base.upper() if "shift" in mods else base
    else:
        raise ValueError(f"no key {base!r} (Enter, Tab, Escape, Backspace, Delete, Up, Down, Left, Right, Home, End, "
                         "PageUp, PageDown, F1–F12, a character)")
    if mods & {"ctrl", "control"}:
        if len(out) != 1:
            raise ValueError(f"ctrl+{base} isn't a key a terminal is sent")
        c = out.upper()
        out = chr(ord(c) & 0x1f) if "@" <= c <= "_" else {"?": "\x7f", " ": "\0", "2": "\0"}.get(c, out)
    if mods & {"alt", "meta", "option"}:
        out = "\x1b" + out
    return out


class Glow(Gtk.Widget):
    """Over the terminal: its edges in the accent while a Bot's at its tools there (as Emission's page glows), fading
    in, held a while after, fading out. Never takes a click."""

    def __init__(self):
        super().__init__(can_target=False, hexpand=True, vexpand=True)
        self.level, self.until, self.last, self.ticking = 0.0, 0.0, None, None

    def on(self, hold=GLOW_HOLD_S):
        """Glowing, for hold seconds more at least (each request to the terminal holds it on again)."""
        self.until = max(self.until, time.monotonic() + hold)
        if self.ticking is None:
            self.last = time.monotonic()
            self.ticking = self.add_tick_callback(self.tick)

    def glowing(self):
        return time.monotonic() < self.until

    def tick(self, *_):
        now = time.monotonic()
        dt, self.last = now - (self.last or now), now
        want = 1.0 if now < self.until else 0.0
        self.level = min(want, self.level + dt / GLOW_IN_S) if want > self.level else \
            max(want, self.level - dt / GLOW_OUT_S)
        self.queue_draw()
        if self.level == want == 0.0:
            self.ticking = None
            return False
        return True

    def do_snapshot(self, snapshot):
        if self.level <= 0:
            return
        import emissionpointer
        w, h = self.get_width(), self.get_height()
        k = self.get_scale_factor() or 1
        c = accent_rgba()
        key = (round(w * k), round(h * k), (c.red, c.green, c.blue), k)
        if key[0] <= 0 or key[1] <= 0:
            return
        if key not in GLOW_TEXTURES:
            if len(GLOW_TEXTURES) > 4:
                GLOW_TEXTURES.clear()
            GLOW_TEXTURES[key] = Gdk.MemoryTexture.new(key[0], key[1], Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED,
                                                       GLib.Bytes.new(emissionpointer.glow_frame(*key)), key[0] * 4)
        snapshot.push_opacity(self.level)
        snapshot.append_texture(GLOW_TEXTURES[key], Graphene.Rect().init(0, 0, w, h))
        snapshot.pop()


def accent_rgba():
    """The accent: Baude's pick in Baude and Record (widgets'), the system's in Multiplex's own window."""
    if "widgets" in sys.modules:
        return sys.modules["widgets"].accent_rgba()
    return Adw.AccentColor.to_rgba(Adw.StyleManager.get_default().get_accent_color())


class Terminal(Gtk.Overlay):
    """A terminal: argv (your shell if none) run in cwd under VTE, with the glow over it. title: what it's called till
    its program names it. on_exit(terminal, status) once its program has ended; on_title(terminal) as its title
    changes; on_close(terminal) to take it from its window when it's closed once its program has ended. Terminals.add gives it its id, by which a Bot's tools reach it."""

    def __init__(self, argv=None, cwd=None, title=None, env=None, on_exit=None, on_title=None,
                 on_close=None):
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("multiplex-terminal")
        self.argv = list(argv) if argv else [os.environ.get("SHELL") or "/bin/sh", "-l"]
        self.cwd = cwd or os.path.expanduser("~")
        self.named, self.on_exit, self.on_title, self.on_close = title, on_exit, on_title, on_close
        self.closing = False  # (closed by a Bot or its window: not kept to be read when its program ends)
        self.pid, self.status, self.id, self.changed, self.started = None, None, None, time.monotonic(), time.time()
        self.bot = None  # (the Bot at its tools here last: {id, name})
        self.vte = Vte.Terminal(hexpand=True, vexpand=True, scrollback_lines=SCROLLBACK, scroll_on_output=False,
                                scroll_on_keystroke=True, pointer_autohide=True, allow_hyperlink=True)
        shortcuts(self, {"c": lambda: self.vte.copy_clipboard_format(Vte.Format.TEXT), "v": self.vte.paste_clipboard})
        self.vte.set_font(Pango.FontDescription.from_string(MONO_FONT))
        styled()
        self.vte.connect("contents-changed", self._changed)
        self.vte.connect("child-exited", self._exited)
        for sig in ("window-title-changed", "notify::window-title"):
            try:
                self.vte.connect(sig, lambda *_: self.on_title and self.on_title(self))
                break
            except TypeError:
                continue
        self.scroll = Gtk.ScrolledWindow(child=self.vte, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.set_child(self.scroll)
        self.glow = Glow()
        self.add_overlay(self.glow)
        self.connect("notify::root", lambda *_: self.colours())
        from widgets import watch_style
        watch_style(self, "notify::dark", Terminal.colours)
        # (its program started once VTE's drawn at its size: what it writes before then is lost; one never shown is
        # started a moment on anyway)
        self.spawned = False
        self.vte.connect("map", lambda *_: GLib.idle_add(self.spawn, env))
        GLib.timeout_add(1500, self.spawn, env)

    def spawn(self, env=None):
        if self.spawned:
            return False
        self.spawned = True
        envs = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", "TERM_PROGRAM": "Multiplex",
                **(env or {})}
        envv = [f"{k}={v}" for k, v in envs.items()]
        # (run through clearrelay, which does its clears without a screen of blank rows in the scrollback)
        self.vte.spawn_async(Vte.PtyFlags.DEFAULT, self.cwd if os.path.isdir(self.cwd) else None,
                             clearrelay.relayed(self.argv, envs.get("PATH")), envv, GLib.SpawnFlags.SEARCH_PATH,
                             None, None, -1, None, self._spawned)
        return False

    def _spawned(self, _vte, pid, error, *_):
        if error is not None or pid in (None, -1):
            self.status = -1
            self.vte.feed(f"\r\n{wording.say('multiplex.not_started', error=getattr(error, 'message', error))}\r\n"
                          .encode())
            if self.on_exit:
                self.on_exit(self, -1)
            return
        self.pid = pid

    def _changed(self, *_):
        self.changed = time.monotonic()

    def _exited(self, _vte, status):
        self.status, self.pid = status, None
        if self.on_exit:
            self.on_exit(self, status)

    def colours(self):
        """The window's own colours: its text, on its background (light or dark as it is)."""
        dark = Adw.StyleManager.get_default().get_dark()
        fg, bg = Gdk.RGBA(), Gdk.RGBA()
        fg.parse("#e8e6e3" if dark else "#1f1f1f")
        bg.parse("#1e1e1e" if dark else "#ffffff")
        self.vte.set_colors(fg, bg, None)

    def stop(self):
        """Its program ended (SIGHUP, as a terminal closed sends it)."""
        if self.pid:
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGHUP)
            except OSError:
                try:
                    os.kill(self.pid, signal.SIGHUP)
                except OSError:
                    pass

    def close(self):
        """Closed: its program stopped, and (one that ended is in its window till now) it's gone from there."""
        self.closing = True
        if self.status is not None and self.on_close:
            self.on_close(self)
        else:
            self.stop()

    # ---- what a Bot's tools read and do
    def title(self):
        try:
            said = self.vte.get_window_title()
        except (AttributeError, TypeError):
            said = None
        return said or self.named or os.path.basename(self.argv[0])

    def where(self):
        """Its program's folder as it says (OSC 7), else the one it started in."""
        try:
            uri = self.vte.get_current_directory_uri()
        except (AttributeError, TypeError):
            uri = None
        if uri:
            try:
                return GLib.filename_from_uri(uri)[0]
            except GLib.Error:
                pass
        return self.cwd

    def info(self, app):
        out = {"id": self.id, "title": self.title(), "cwd": self.where(), "command": self.argv, "app": app,
               "columns": self.vte.get_column_count(), "rows": self.vte.get_row_count(),
               "running": self.status is None}
        if self.status is not None:
            out["exit_status"] = self.status
        return out

    def rows(self):
        """(the first row of the buffer still kept, the screen's first, the row after the last)."""
        adj = self.vte.get_vadjustment()
        end = int(adj.get_upper())
        return int(adj.get_lower()), max(int(adj.get_lower()), end - self.vte.get_row_count()), end

    def lines(self, start, end):
        """Rows start to end (not including it) as text, a line each (trailing spaces gone)."""
        if end <= start:
            return []
        got = self.vte.get_text_range_format(Vte.Format.TEXT, start, 0, end - 1, self.vte.get_column_count())
        text = got[0] if isinstance(got, tuple) else got
        out = [line.rstrip() for line in (text or "").split("\n")]
        return out[:end - start]

    def screen(self, above=0):
        """What's on its screen (above: that many lines before it too), and where the cursor is on it."""
        first, top, end = self.rows()
        above = max(0, min(int(above or 0), READ_MOST - (end - top), top - first))
        col, row = self.vte.get_cursor_position()
        lines = self.lines(top - above, end)
        while len(lines) > above + 1 and not lines[-1] and row - (top - above) < len(lines) - 1:
            lines.pop()  # (the empty rows under the last one written: left out)
        out = {"screen": "\n".join(lines), "cursor": {"row": row - top + 1, "column": col + 1}}
        if above:
            out["lines_above_screen"] = above
        if top > first + above:
            out["more_above"] = top - first - above
        return out

    def matching(self, words):
        """The lines kept (screen's and scrollback's) that have every word of words in them (case aside)."""
        first, _top, end = self.rows()
        want = [w.lower() for w in words.split()]
        found = []
        for line in self.lines(max(first, end - READ_MOST), end):
            if all(w in line.lower() for w in want):
                found.append(line)
        return found[-20:]

    def type(self, text):
        if self.status is not None:
            raise ValueError(f"{self.id}'s program has ended (exit status {self.status})")
        self.vte.feed_child(text.replace("\r\n", "\r").replace("\n", "\r").encode())

    def keys(self, keys):
        for k in keys.split():
            self.type(key_bytes(k))


class HostedTerminal(Terminal):
    """A terminal on a host (where: wire.reach's; this machine's host process too), run there by its host process
    (wire's terminal op: baudelib.hostterminal) and drawn here: its bytes come over its own connection, what's typed
    goes back. attach: one running there seen again. A Bot here reaches it as it does any other."""

    def __init__(self, where, argv=None, cwd=None, title=None, attach=None, on_exit=None, on_title=None,
                 on_close=None, host_name=None):
        self.host_where, self.wanted, self.wanted_cwd, self.attach_to = where, argv, cwd, attach
        self.host_name, self.ch, self.host_id, self.size = host_name, None, None, (0, 0)
        self.stopped = False  # (closed here: what its host still sends isn't drawn)
        super().__init__(argv or ["sh"], cwd, title, on_exit=on_exit, on_title=on_title, on_close=on_close)
        self.argv = list(argv or [])  # (its host's login shell, if none)
        self.vte.connect("commit", lambda _v, text, _n: self.send({"op": "terminal_in", "data": _b64(
            text.encode() if isinstance(text, str) else text)}))
        self.resizing = GLib.timeout_add(300, self._resized)

    def spawn(self, env=None):
        if self.spawned:
            return False
        self.spawned = True
        import threading
        threading.Thread(target=self._run, daemon=True, name="hosted-terminal").start()
        return False

    def _run(self):
        import base64
        from baudelib import hosts, wire
        try:
            if self.host_where.get("via") == "local" and not hosts.start_here():  # (this machine's: started if need be)
                raise OSError(wording.say("host_status.none_started", log=hosts.log_path()))
            ch, _info = wire.reach(self.host_where, timeout=30)
        except (OSError, ValueError) as e:
            return GLib.idle_add(self._failed, str(e))
        ch.keep_alive()  # (the host takes SILENT_S without a word for gone: a terminal no one's typing in is still here)
        self.ch = ch
        if self.stopped:  # (closed while its host was reached)
            return ch.close()
        cols, rows = self.vte.get_column_count(), self.vte.get_row_count()
        self.size = (cols, rows)
        ch.send({"op": "terminal", "req": 1, "columns": cols, "rows": rows,
                 **({"attach": self.attach_to} if self.attach_to else
                    {k: v for k, v in (("argv", self.wanted), ("cwd", self.wanted_cwd), ("title", self.named)) if v})})
        try:
            while (r := ch.reply()) is not None:
                op = r.get("op")
                if op == "terminal_out":
                    GLib.idle_add(self._out, base64.b64decode(r.get("data") or ""))
                elif op == "terminal_opened":
                    self.host_id = r.get("id")
                elif op == "terminal_end":
                    return GLib.idle_add(self._ended, r.get("status"), r.get("said"))
                elif op == "error":
                    return GLib.idle_add(self._failed, wire.said(r))
        except (OSError, ValueError) as e:
            return GLib.idle_add(self._failed, str(e))
        GLib.idle_add(self._failed, wording.say("multiplex.host_gone"))

    def _out(self, data):
        if not self.stopped:
            self.vte.feed(data)
        return False

    def _failed(self, why):
        if self.status is None:
            if not self.stopped:
                self.vte.feed(f"\r\n{wording.say('multiplex.not_started', error=why)}\r\n".encode())
            self._ended(-1, None)
        return False

    def _ended(self, status, said):
        if self.status is not None:
            return False
        self.status = status if status is not None else -1
        if said and not self.stopped:
            self.vte.feed(f"\r\n{said}\r\n".encode())
        if self.ch is not None:
            self.ch.close()
        if self.on_exit:
            self.on_exit(self, self.status)
        return False

    def _resized(self):
        if self.status is not None or self.stopped:
            self.resizing = None
            return False
        now = (self.vte.get_column_count(), self.vte.get_row_count())
        if self.ch is not None and now != self.size:
            self.size = now
            self.send({"op": "terminal_resize", "columns": now[0], "rows": now[1]})
        return True

    def send(self, msg):
        if self.ch is not None and self.status is None:
            try:
                self.ch.send(msg)
            except (OSError, ValueError):
                pass

    def stop(self):
        self.send({"op": "terminal_close"})
        self.stopped = self.spawned = True  # (not started later on, if it wasn't yet)
        if self.ch is not None:  # (its host ends it without a word back: its reader let go of now)
            self.ch.close()
        if self.resizing is not None:
            GLib.source_remove(self.resizing)
            self.resizing = None

    def type(self, text):
        if self.status is not None:
            raise ValueError(f"{self.id}'s program has ended (exit status {self.status})")
        self.send({"op": "terminal_in", "data": _b64(text.replace("\r\n", "\r").replace("\n", "\r").encode())})

    def title(self):
        try:
            said = self.vte.get_window_title()
        except (AttributeError, TypeError):
            said = None
        return said or self.named or self.host_name or wire_said(self.host_where)

    def where(self):
        return self.wanted_cwd or ""

    def info(self, app):
        return {**super().info(app), "host": self.host_name or wire_said(self.host_where)}


def _b64(data):
    import base64
    return base64.b64encode(data).decode()


def wire_said(where):
    from baudelib import wire
    return wire.said_where(where)


MONO_FONT = "Menlo 12" if sys.platform == "darwin" else "Monospace 11"
CSS = "vte-terminal { padding: 6px 10px; }"


def styled():
    """The terminals' own look, once for the display."""
    display = Gdk.Display.get_default()
    if display is None or getattr(display, "multiplex_css", False):
        return
    prov = Gtk.CssProvider()
    prov.load_from_string(CSS) if hasattr(prov, "load_from_string") else prov.load_from_data(CSS, -1)
    Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    display.multiplex_css = True


class Terminals:
    """This process's terminals, and its socket (multiplexlink.socket_path) for a Bot's tools: each request a line of
    JSON, answered with one (a wait's, once there's an answer). app: whose they are ("multiplex", "baude", "record");
    opener(argv, cwd, title, show): a new terminal in a window, for open (Multiplex's window has one; the Kubernetes
    view doesn't)."""
    made = None

    def __init__(self, app, opener=None):
        self.app, self.opener, self.terminals, self.n, self.server = app, opener, {}, 0, None

    @classmethod
    def get(cls, app="baude", opener=None):
        if cls.made is None:
            cls.made = cls(app, opener)
        elif opener is not None:
            cls.made.app, cls.made.opener = app, opener
        return cls.made

    def add(self, term):
        self.n += 1
        term.id = f"{os.getpid()}.{self.n}"
        self.terminals[term.id] = term
        self.listen()
        return term

    def remove(self, term):
        self.terminals.pop(term.id, None)

    # ---- the socket
    def listen(self):
        if self.server is not None:
            return
        path = multiplexlink.socket_path()
        try:
            os.unlink(path)
        except OSError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(8)
        srv.setblocking(False)
        self.server = (srv, GLib.io_add_watch(srv.fileno(), GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN, self._came))

    def close(self):
        if self.server is not None:
            srv, source = self.server
            GLib.source_remove(source)
            srv.close()
            self.server = None
            try:
                os.unlink(multiplexlink.socket_path())
            except OSError:
                pass

    def _came(self, *_):
        try:
            conn, _addr = self.server[0].accept()
        except OSError:
            return True
        conn.settimeout(10)
        try:
            raw = b""
            while not raw.endswith(b"\n") and len(raw) < 1 << 22:
                part = conn.recv(1 << 16)
                if not part:
                    break
                raw += part
            msg = json.loads(raw or b"{}")
            if not isinstance(msg, dict):
                raise ValueError("not a request")
        except (OSError, ValueError):
            conn.close()
            return True
        done = []

        def answer(reply):
            if done:
                return
            done.append(True)
            try:
                conn.settimeout(30)
                conn.sendall((json.dumps(reply) + "\n").encode())
            except (OSError, TypeError, ValueError):
                pass
            finally:
                conn.close()
        try:
            self.take(msg, answer)
        except Exception as e:  # (said to whoever asked, and in the log)
            sys.excepthook(*sys.exc_info())
            answer({"error": str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"})
        return True

    def find(self, tid):
        term = self.terminals.get(tid)
        if term is None:
            raise ValueError(f"no terminal {tid} (list_terminals: the ones there are)")
        return term

    def take(self, msg, answer):
        op = msg.get("op")
        if op == "info":
            return answer({"pid": os.getpid(), "app": self.app, "opens": self.opener is not None})
        if op == "list":
            query = (msg.get("query") or "").strip()
            out = []
            for term in self.terminals.values():
                info = term.info(self.app)
                if query:
                    about = " ".join([info["title"], info["cwd"], " ".join(info["command"])]).lower()
                    lines = term.matching(query)
                    if not lines and not all(w in about for w in query.lower().split()):
                        continue
                    if lines:
                        info["matching_lines"] = lines
                out.append(info)
            return answer({"terminals": out})
        if op == "open":
            if self.opener is None:
                raise ValueError("terminals are opened in Multiplex's window")
            argv = msg.get("argv") or ([os.environ.get("SHELL") or "/bin/sh", "-lc", msg["command"]]
                                       if msg.get("command") else None)
            if argv is not None and (not isinstance(argv, list) or not all(isinstance(a, str) for a in argv)):
                raise ValueError("argv is a list of strings")
            cwd = os.path.expanduser(msg["cwd"]) if isinstance(msg.get("cwd"), str) and msg["cwd"] else None
            if cwd is not None and not os.path.isdir(cwd):
                raise ValueError(f"no folder {cwd}")
            term = self.opener(argv, cwd, msg.get("title") if isinstance(msg.get("title"), str) else None,
                               bool(msg.get("show")))
            self.touched(term, msg)
            return self.settled(term, lambda: {**term.info(self.app), **term.screen()}, answer, start=True)
        term = self.find(msg.get("terminal"))
        self.touched(term, msg)
        if op == "read":
            return answer({**term.info(self.app), **term.screen(msg.get("lines_above") or 0)})
        if op == "type":
            text = msg.get("text")
            if not isinstance(text, str):
                raise ValueError("type needs text")
            term.type(text + ("\r" if msg.get("enter") else ""))
            return self.settled(term, lambda: term.screen(), answer)
        if op == "key":
            if not isinstance(msg.get("keys"), str) or not msg["keys"].strip():
                raise ValueError("key needs keys")
            term.keys(msg["keys"])
            return self.settled(term, lambda: term.screen(), answer)
        if op == "wait":
            return self.wait(term, msg, answer)
        if op == "close":
            answer({"closed": term.id})
            return term.close()
        raise ValueError(f"no op {op!r}")

    def touched(self, term, msg):
        """A Bot's at its tools in term: it glows (held on while it waits)."""
        if isinstance(msg.get("bot"), dict):
            term.bot = msg["bot"]
            term.glow.on()

    def settled(self, term, said, answer, start=False):
        """answer(said()) once term's output has stopped a moment (SETTLE_S) or SETTLE_MOST_S on: what typing did."""
        began = time.monotonic()

        def check():
            now = time.monotonic()
            quiet = now - max(term.changed, began) >= SETTLE_S and (not start or term.changed > began)
            if quiet or now - began > SETTLE_MOST_S or term.status is not None:
                answer(said())
                return False
            term.glow.on()
            return True
        GLib.timeout_add(50, check)

    def wait(self, term, msg, answer):
        """Till pattern (a regex) is on the screen, or the output's been quiet for quiet seconds, or its program
        ends, or timeout: the screen then, and which it was."""
        try:
            pattern = re.compile(msg["pattern"]) if msg.get("pattern") else None
        except re.error as e:
            raise ValueError(f"pattern: {e}")
        quiet = float(msg.get("quiet") or (0 if pattern else 2))
        timeout = min(float(msg.get("timeout") or 30), multiplexlink.WAIT_MOST_S)
        began = time.monotonic()
        seen = term.screen()["screen"]  # (a match there already counts only once the screen's changed)
        already = bool(pattern and pattern.search(seen))

        def check():
            now = time.monotonic()
            got = term.screen()
            why = None
            if pattern is not None and (m := pattern.search(got["screen"])) and (not already or got["screen"] != seen):
                why = {"matched": m.group(0)}
            if why is None and quiet and now - max(term.changed, began) >= quiet:
                why = {"quiet_for_s": quiet}
            if why is None and term.status is not None:
                why = {"exited": term.status}
            if why is None and now - began >= timeout:
                why = {"timed_out_after_s": timeout}
            if why is None:
                term.glow.on()
                return True
            answer({**why, **got})
            return False
        GLib.timeout_add(100, check)
