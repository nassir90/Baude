"""What Baude and Record tell their machine's host of chats as it happens (baudelib.telemetry, the telemetry op; the
phone's Telemetry.kt the same), and Settings → Telemetry, which shows what a host keeps. Recorder, one an app: while
that host's telemetry is on (its Info's telemetry, followed as it changes), each chat opened, coming in front or
leaving it, each turn sent and each turn's end (how long it took, its model and effort, its tokens), by its host's id
and its session's id, told at once with its own time; and line profiles of what the host says this app profiles
(lineprofile), every PROFILE_S. While it's off nothing is looked at or sent. Nothing is sent anywhere but this
machine's host. watch(win, app, network) is the one hook a window needs; look, turn_sent and turn_ended are the
chat's."""
import datetime
import os
import time
import weakref

from gi.repository import Adw, GLib, Gtk

from baudelib import lineprofile, providers, wording

PROFILE_S = 30  # (how often what's profiled here is told)
QUEUE_MAX = 1000  # (events kept for a host that isn't there yet)
APP_NAMES = {"host": "telemetry.host_process", "baude": "mesh_versions.baude", "record": "mesh_versions.record",
             "emission": "mesh_versions.emission", "multiplex": "deps.multiplex", "phone": "mesh_versions.baude"}
APP_LOGOS = {"host": "baude", "baude": "baude", "record": "record", "emission": "emission", "multiplex": "multiplex",
             "phone": "baude"}  # (each process's app's logo: schema/icons/baude's baude-app-<logo>-symbolic)
LOGO_PX = 16  # (as big as a model's logo)
PROFILED = ("host", "baude", "record")
WEEKS = 53  # (the tokens' grid: a year, a column a week)
LINES_SHOWN = 10
REFRESH_S = 5  # (the machine's rows asked for again while the page is shown)
_RECORDER = []


def chat_of(win):
    """(host id, session id) of the chat a Baude window shows, or None (a new chat nothing's been sent in)."""
    sid = getattr(win, "session_id", None)
    if not sid or not _RECORDER:
        return None
    r = getattr(win, "remote", None)
    if not (r is not None and sid in r.sessions or getattr(win, "chat_sent", False)
            or getattr(win, "session_title", None)):
        return None
    host = (r.info.get("id") or r.key) if r is not None else _RECORDER[0].local_id()
    return (host, sid) if host else None


def chats_in(win):
    """The chats open in win (a set of (host, session)) and the one in front, if win is: Baude's its one chat, shown
    on its Chat tab; Record's each chat's tab."""
    tabs = getattr(win, "tabs", None)
    if tabs is None:
        return set(), None
    if hasattr(win, "session_id"):
        chat = chat_of(win)
        front = chat if chat and win.is_active() and tabs.current() is tabs.get("chat") else None
        return ({chat} if chat else set()), front
    rec = _RECORDER[0] if _RECORDER else None
    open_ = {}
    for key in list(tabs.pages):
        if isinstance(key, tuple) and key[:1] == ("chat",) and isinstance(key[1], str) and rec is not None:
            if (chat := rec.chat_at(key[1])) is not None:
                open_[key] = chat
    shown = tabs.current()
    key = getattr(shown, "tab_key", None)
    front = open_.get(key) if win.is_active() and key in open_ else None
    return set(open_.values()), front


class Recorder:
    """An app's: what it tells its machine's host of chats, and its own line profiling, as that host says."""

    def __init__(self, app, name, network):
        self.app, self.name, self.network = app, name, network
        self.on, self.host_pid, self.host_id = False, None, None
        self.queue, self.sending = [], False
        self.windows = weakref.WeakSet()
        self.opened = weakref.WeakKeyDictionary()  # (a window: the chats open in it, as last looked)
        self.front = None  # (the chat in front, in which window: (win ref, chat))
        self.sent_at = {}  # (a chat: when its turn was sent here)
        self.profiler, self.profile_timer, self.told_error = lineprofile.Profiler(name), None, None
        network.listen(self.heard)
        app.connect("shutdown", lambda *_: self.quit())
        self.heard()

    def local_id(self):
        here = self.network.here_alive()
        if here is not None and here.info.get("id"):
            self.host_id = here.info["id"]
        return self.host_id

    def chat_at(self, path):
        """A transcript's chat, Record's tab of it: (its host's id, its id). One under the network's mirrors is
        that host's; any other, this machine's."""
        sid = os.path.basename(path).removesuffix(".jsonl")
        home = os.path.join(self.network.home, "")
        if path.startswith(home):
            return path[len(home):].split(os.sep, 1)[0], sid
        host = self.local_id()
        return (host, sid) if host else None

    def heard(self):
        """The hosts changed: this machine's host's telemetry followed (switched on, off; what this app profiles)."""
        here = self.network.here_alive()
        if here is None:
            return
        self.host_id = here.info.get("id") or self.host_id
        t = here.info.get("telemetry") or {}
        on, pid = bool(t.get("on")), here.info.get("pid")
        if on != self.on:
            self.on = on
            if on:
                self.hello()
                for win in list(self.windows):
                    self.look(win)
            else:
                self.queue.clear()
                self.opened.clear()
                self.front, self.sent_at = None, {}
        elif on and pid != self.host_pid:
            self.hello()  # (a host started again: its samples of this process begin again)
        self.host_pid = pid
        functions = next((p.get("functions") or [] for p in t.get("profiling") or [] if p.get("app") == self.name), [])
        if functions != self.profiler.functions:
            self.profile(functions)

    def watch(self, win):
        self.windows.add(win)
        GLib.idle_add(lambda: self.look(win) and False)
        win.connect("notify::is-active", lambda w, _p: self.look(w))
        if (tabs := getattr(win, "tabs", None)) is not None:
            tabs.view.connect("notify::selected-page", lambda *_: self.look(win))
            for sig in ("page-attached", "page-detached"):
                tabs.view.connect(sig, lambda *_: GLib.idle_add(lambda: self.look(win) and False))
        win.connect("destroy", lambda w: self.look(w, gone=True))

    # ---- chats
    def look(self, win, gone=False):
        """win looked at again: a chat opened in it since, and the chat in front, if it's changed, told."""
        if not self.on:
            return
        open_, front = (set(), None) if gone else chats_in(win)
        before = self.opened.get(win, set())
        for chat in open_ - before:
            self.event("chat_opened", chat)
        self.opened[win] = open_
        was = self.front
        if front is not None:
            if was is None or was[1] != front:
                if was is not None:
                    self.event("chat_left", was[1])
                self.event("chat_front", front)
                self.front = (weakref.ref(win), front)
        elif was is not None and was[0]() in (win, None):
            self.event("chat_left", was[1])
            self.front = None

    def turn_sent(self, win):
        if not self.on:
            return
        self.look(win)
        if (chat := chat_of(win)) is not None:
            self.sent_at[chat] = time.time()
            self.event("turn_sent", chat)
        else:
            self.sent_at[("", id(win))] = time.time()  # (a new chat's: its id comes with its first message)

    def turn_ended(self, win, msg, model=None, effort=None):
        """A turn's result: how long it took (from its sending here, else as it says), its model and effort, its
        tokens."""
        if not self.on or (chat := chat_of(win)) is None:
            return
        self.look(win)
        sent = self.sent_at.pop(chat, None) or self.sent_at.pop(("", id(win)), None)
        r = getattr(win, "remote", None)
        model = model or ((r.sessions.get(chat[1]) or {}).get("model") if r is not None else None)  # (its default's,
        # as its host lists it)
        u = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
        took = time.time() - sent if sent else (msg["duration_ms"] / 1000 if isinstance(
            msg.get("duration_ms"), (int, float)) else None)
        self.event("turn_ended", chat, seconds=round(took, 3) if took is not None else None,
                   turn=msg.get("uuid") if isinstance(msg.get("uuid"), str) else None,
                   model=model if isinstance(model, str) else None, effort=effort if isinstance(effort, str) else None,
                   tokens_in=sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                              "cache_creation_input_tokens")),
                   tokens_out=int(u.get("output_tokens") or 0))

    def event(self, kind, chat, **more):
        e = {"at": round(time.time(), 3), "kind": kind, "host": chat[0], "session": chat[1],
             **{k: v for k, v in more.items() if v is not None}}
        self.queue = (self.queue + [e])[-QUEUE_MAX:]
        if not self.sending:
            self.sending = True
            GLib.idle_add(self.tell)

    # ---- telling the host
    def hello(self):
        """The host told this app's process id (sampled from now), with nothing else."""
        self.send({}, lambda _ok: None)

    def tell(self):
        events, self.queue = self.queue, []
        if not events:
            self.sending = False
            return False

        def done(ok):
            if not ok:  # (kept, for when the host's there)
                self.queue = (events + self.queue)[-QUEUE_MAX:]
            self.sending = False
            if ok and self.queue:
                self.sending = True
                GLib.idle_add(self.tell)
        self.send({"events": events}, done)
        return False

    def send(self, more, done, wait=False):
        msg = {"op": "telemetry", "app": self.name, "pid": os.getpid(), **more}
        if self.profiler.error != self.told_error:
            msg["profile_error"] = self.told_error = self.profiler.error
        here = self.network.here_alive()
        if here is None:
            return done(False)
        if wait:
            return done(here.call(msg, timeout=1).get("op") == "telemetry_kept")
        here.request(msg, lambda r: done(r.get("op") == "telemetry_kept"))

    # ---- profiling
    def profile(self, functions):
        last = self.profiler.set(functions)
        if self.profile_timer is not None:
            GLib.source_remove(self.profile_timer)
            self.profile_timer = None
        if functions:
            self.profile_timer = GLib.timeout_add_seconds(PROFILE_S, self.profiled)
        self.send({"profile": last} if last else {}, lambda _ok: None)

    def profiled(self):
        if (p := self.profiler.snapshot()) is not None or self.profiler.error != self.told_error:
            self.send({"profile": p} if p else {}, lambda _ok: None)
        return True

    def quit(self):
        """As the app quits: what's left told (the host waited for a moment at most)."""
        for win in list(self.windows):
            self.look(win, gone=True)
        last = self.profiler.set([])
        events, self.queue = self.queue, []
        if self.on and (events or last):
            try:
                self.send({"events": events, **({"profile": last} if last else {})}, lambda _ok: None, wait=True)
            except (OSError, ValueError):
                pass


def watch(win, name, network):
    """win's chats told to this machine's host (name: the app's, "baude" or "record"; network: its hosts)."""
    if not _RECORDER:
        _RECORDER.append(Recorder(win.get_application(), name, network))
    _RECORDER[0].watch(win)


def look(win):
    if _RECORDER:
        _RECORDER[0].look(win)


def turn_sent(win):
    if _RECORDER:
        _RECORDER[0].turn_sent(win)


def turn_ended(win, msg, model=None, effort=None):
    if _RECORDER:
        _RECORDER[0].turn_ended(win, msg, model, effort)


# ---- Settings → Telemetry
def said_size(n):
    if n is None:
        return "–"
    if n >= 1 << 30:
        return wording.say("telemetry.size_gb", gb=f"{n / (1 << 30):.1f}")
    return wording.say("images.size_mb", mb=f"{n / (1 << 20):.0f}")


def said_pressure(s):
    if s.get("memory_pressure"):
        key = f"telemetry.pressure_{s['memory_pressure']}"
        return wording.say(key) if wording.has(key) else str(s["memory_pressure"])
    return wording.say("telemetry.stalled", percent=f"{s['memory_stall']:.1f}")


def tokens(n):
    from chatparts import fmt_tokens
    return fmt_tokens(int(n))


def levels(days):
    """Each day's level in the grid, 0 (none) to 4, by its share of the busiest day's tokens."""
    most = max((d["tokens"] for d in days), default=0)
    return {d["day"]: (0 if not d["tokens"] or not most else min(4, 1 + int(4 * d["tokens"] / (most + 1))))
            for d in days}


class TokenGrid(Gtk.DrawingArea):
    """A year of tokens, a square a day, a column a week (Sunday at the top), today's the last; each as dark as its
    day's share of the busiest day's, in the accent colour; none, faint. Its tooltip says the day's."""

    GAP = 3

    def __init__(self):
        super().__init__(hexpand=True, has_tooltip=True, margin_top=12, margin_bottom=12, margin_start=12,
                         margin_end=12)
        self.days, self.level = {}, {}
        self.set_draw_func(self.draw)
        self.connect("query-tooltip", self.tip)
        self.set_content_height(7 * 14)

    def show(self, days):
        self.days = {d["day"]: d for d in days}
        self.level = levels(days)
        self.queue_draw()

    def cell(self):
        return max(4, min(14, (self.get_width() - (WEEKS - 1) * self.GAP) // WEEKS))

    def first(self):
        today = datetime.date.today()
        return today - datetime.timedelta(days=(WEEKS - 1) * 7 + (today.weekday() + 1) % 7)

    def draw(self, _area, cr, width, _height):
        size, start, today = self.cell(), self.first(), datetime.date.today()
        if self.get_content_height() != 7 * (size + self.GAP) - self.GAP:  # (as tall as its squares, at this width)
            GLib.idle_add(lambda: self.set_content_height(7 * (size + self.GAP) - self.GAP) and False)
        accent = Adw.StyleManager.get_default().get_accent_color_rgba()
        fg = self.get_color()
        left = max(0, (width - (WEEKS * size + (WEEKS - 1) * self.GAP)) // 2)
        for i in range(WEEKS * 7):
            day = start + datetime.timedelta(days=i)
            if day > today:
                break
            level = self.level.get(day.isoformat(), 0)
            if level:
                cr.set_source_rgba(accent.red, accent.green, accent.blue, (0.25, 0.45, 0.7, 1.0)[level - 1])
            else:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.08)
            x, y = left + i // 7 * (size + self.GAP), i % 7 * (size + self.GAP)
            cr.rectangle(x, y, size, size)
            cr.fill()

    def tip(self, _w, x, y, _kb, tooltip):
        size = self.cell()
        left = max(0, (self.get_width() - (WEEKS * size + (WEEKS - 1) * self.GAP)) // 2)
        col, row = int((x - left) // (size + self.GAP)), int(y // (size + self.GAP))
        if not (0 <= col < WEEKS and 0 <= row < 7):
            return False
        day = self.first() + datetime.timedelta(days=col * 7 + row)
        if day > datetime.date.today():
            return False
        d = self.days.get(day.isoformat()) or {"tokens": 0}
        tooltip.set_text(wording.say("telemetry.day_tokens", day=day.strftime("%a %-d %b %Y"), tokens=tokens(d["tokens"])))
        return True


def effort_said(effort):
    key = f"mode_effort.effort_{effort}"
    return wording.say(key) if effort and wording.has(key) else effort or "–"


def model_row(value, models, *values):
    """A row named for a model, as every app names it (providers.model_name), its slug on hover."""
    r = row(providers.model_name(value, models) or "–", *values)
    r.set_tooltip_text(providers.model_slug(value, models) if isinstance(value, str) else None)
    return r


def logo(app):
    """The logo of a process's app (APP_LOGOS), LOGO_PX square, in the row's text colour as the model logos are."""
    import widgets
    widgets.ensure_icons()
    return Gtk.Image(icon_name=f"baude-app-{APP_LOGOS.get(app, 'baude')}-symbolic", pixel_size=LOGO_PX)


def proc_rows(procs):
    """The rows of procs (sorted): each's app's logo, its name (numbered where the app has several: "Emission (2)"),
    and its CPU and memory after them; then a bold Total row, its CPU and memory the processes' added up."""
    count, seen = {}, {}
    for p in procs:
        count[p.get("app")] = count.get(p.get("app"), 0) + 1
    rows = []
    for p in procs:
        app = p.get("app")
        name = wording.say(APP_NAMES[app]) if app in APP_NAMES else str(app)
        if count[app] > 1:
            seen[app] = seen.get(app, 0) + 1
            name = wording.say("telemetry.process_n", name=name, n=seen[app])
        r = row(name, wording.say(
            "telemetry.cpu_rss", cpu="–" if p.get("cpu") is None else f"{p['cpu']:.1f}", rss=said_size(p.get("rss"))))
        r.add_prefix(logo(app))
        rows.append(r)
    cpus = [p["cpu"] for p in procs if p.get("cpu") is not None]
    rsses = [p["rss"] for p in procs if p.get("rss") is not None]
    rows.append(row(wording.say("telemetry.total"), wording.say(
        "telemetry.cpu_rss", cpu=f"{sum(cpus):.1f}" if cpus else "–", rss=said_size(sum(rsses) if rsses else None)),
        bold=True))
    return rows


def row(title, *values, sensitive=True, mono=False, bold=False):
    esc = GLib.markup_escape_text(title)
    r = Adw.ActionRow(title=f"<b>{esc}</b>" if bold else esc, sensitive=sensitive)
    if mono:
        r.add_css_class("monospace")
    for v in values:
        if v:
            r.add_suffix(Gtk.Label(label=v, css_classes=["numeric"] if bold else ["dim-label", "numeric"]))
    return r


class Page(Adw.PreferencesPage):
    """Settings → Telemetry: the host picked beside New session (as Dependencies has it), Collect Telemetry, then what
    that host keeps: its tokens by day over a year with the most used model and effort and each model's, its machine
    now, its processes now (each with its app's logo, its CPU and memory), and line profiling (which process, what's
    profiled, its top lines)."""

    def __init__(self, win):
        super().__init__(title=wording.say("telemetry.title"), icon_name="document-open-recent-symbolic")
        from widgets import walk
        for clamp in walk(self):  # (as widgets.settings_page: rows as wide as the dialog)
            if isinstance(clamp, Adw.Clamp):
                clamp.set_maximum_size(100_000)
                clamp.set_tightening_threshold(100_000)
        self.win = win
        self.where, host = win.terminal_host()
        top = Adw.PreferencesGroup()
        name = Adw.ActionRow(title=wording.say("new_session.host"))
        name.add_suffix(Gtk.Label(label=host, css_classes=["dim-label"]))
        top.add(name)
        self.collect = Adw.SwitchRow(title=wording.say("telemetry.collect"), sensitive=False)
        self.collect.connect("notify::active", lambda *_: self.switched())
        top.add(self.collect)
        self.add(top)
        self.state, self.taking, self.groups, self.shown_app = None, False, {}, "host"
        self.deps_for, self.missing_shown, self.lines_shown, self.sampled, self.ticks = (), None, None, None, 0
        self.grid = TokenGrid()
        self.refresh = GLib.timeout_add_seconds(REFRESH_S, self.tick)
        self.connect("destroy", lambda *_: GLib.source_remove(self.refresh))
        self.ask()

    def tick(self):
        if self.get_mapped() and self.state and self.state.get("on"):
            now = time.time()
            self.read({"from": max(self.sampled or 0, now - 3 * REFRESH_S), "what": ["metrics"], "limit": 1},
                      self.took_metrics)
            self.ticks += 1
            if self.ticks % (PROFILE_S // REFRESH_S) == 0 and any(
                    p.get("functions") for p in self.state.get("profiling") or []):
                self.read({"from": now - 3600, "what": ["profiles"], "limit": 500}, self.took_profiles)
        return True

    def read(self, msg, done):
        import depsview
        depsview.ask(self.where, done, {"op": "telemetry_read", **msg})

    def set(self, msg):
        import depsview
        depsview.ask(self.where, lambda r: r.get("state") and self.took_state(r), {"op": "telemetry_set", **msg})

    def ask(self):
        now = time.time()
        self.read({"from": now - (WEEKS * 7 + 7) * 86400, "what": ["usage"]}, self.took_usage)
        self.read({"from": now - 60, "what": ["metrics"], "limit": 1}, self.took_metrics)
        self.read({"from": now - 3600, "what": ["profiles"], "limit": 500}, self.took_profiles)

    def group(self, key, title, rows=(), at=None):
        """The group key (made, or emptied), titled, with rows."""
        g = self.groups.get(key)
        if g is not None:
            self.remove(g)
        g = Adw.PreferencesGroup(title=GLib.markup_escape_text(title) if title else None)
        for r in rows:
            g.add(r)
        self.groups[key] = g
        self.add(g)
        order = ["error", "tokens", "models", "machine", "processes", "profiling", "lines"]
        for k in order[order.index(key) + 1:]:  # (kept in their order however they're answered)
            if (later := self.groups.get(k)) is not None:
                self.remove(later)
                self.add(later)
        return g

    def drop(self, key):
        """The group key taken out, if it's there."""
        if (g := self.groups.pop(key, None)) is not None:
            self.remove(g)

    def failed(self, reply):
        if reply.get("error"):
            self.group("error", None, [row(reply["error"], sensitive=False)])
            return True
        return False

    def took_state(self, reply):
        self.state = reply.get("state") or self.state
        if self.state is None:
            return False
        self.taking = True
        self.collect.set_active(bool(self.state.get("on")))
        self.collect.set_sensitive(True)
        self.taking = False
        if not self.state.get("on"):
            self.group("machine", None, [row(wording.say("telemetry.off"), sensitive=False)])
            self.drop("processes")
        self.show_profiling()
        return False

    def switched(self):
        if not self.taking:
            self.collect.set_sensitive(False)
            self.set({"on": self.collect.get_active()})

    def took_usage(self, reply):
        if self.failed(reply):
            return False
        self.took_state(reply)
        u = reply.get("usage") or {"days": [], "models": [], "combos": []}
        self.grid.show(u["days"])
        models = self.host_models()
        g = self.group("tokens", wording.say("telemetry.tokens_by_day"))
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        grid_row = Gtk.ListBoxRow(activatable=False, child=self.grid)
        if self.grid.get_parent() is not None:
            self.grid.get_parent().set_child(None)
            grid_row.set_child(self.grid)
        box.append(grid_row)
        if u["combos"]:
            c = u["combos"][0]
            most = row(wording.say("telemetry.most_used"),
                       wording.say("telemetry.model_effort", model=providers.model_name(c["model"], models) or "–",
                                   effort=effort_said(c.get("effort"))))
            most.set_tooltip_text(providers.model_slug(c["model"], models) if isinstance(c["model"], str) else None)
            box.append(most)
        g.add(box)
        self.group("models", wording.say("telemetry.models") if u["models"] else None,
                   [model_row(m["model"], models,
                              wording.say("telemetry.turns_tokens", turns=m["turns"], tokens=tokens(m["tokens"])))
                    for m in u["models"]])
        return False

    def host_models(self):
        r = self.host_remote()
        return (r.info.get("models") or []) if r is not None else []

    def host_remote(self):
        net = client_network(self.win)
        if net is None:
            return None
        if self.where.get("via") == "local":
            return net.here_alive()
        return next((r for r in net.alive() if r.key == self.where.get("host")), None)

    def took_metrics(self, reply):
        if self.failed(reply):
            return False
        self.took_state(reply)
        if not self.state or not self.state.get("on"):
            return False
        samples = reply.get("metrics") or []
        if not samples:
            return False
        s = samples[-1]
        self.sampled = s.get("at")
        rows = []
        if s.get("load"):
            rows.append(row(wording.say("telemetry.load"), " · ".join(f"{x:.2f}" for x in s["load"])))
        if s.get("memory_total"):
            rows.append(row(wording.say("bot_memory.title"), wording.say(
                "telemetry.memory_of", used=said_size(s.get("memory_used")), total=said_size(s["memory_total"]))))
        if s.get("swap_total"):
            rows.append(row(wording.say("telemetry.swap"), wording.say(
                "telemetry.memory_of", used=said_size(s.get("swap_used")), total=said_size(s["swap_total"]))))
        if s.get("memory_pressure") or s.get("memory_stall") is not None:
            rows.append(row(wording.say("telemetry.memory_pressure"), said_pressure(s)))
        self.group("machine", wording.say("telemetry.machine"), rows)
        procs = sorted((p for p in s.get("procs") or [] if p.get("app") in APP_NAMES),
                       key=lambda p: (list(APP_NAMES).index(p["app"]), p.get("pid") or 0))
        if procs:
            self.group("processes", wording.say("side_panel.processes"), proc_rows(procs))
        else:
            self.drop("processes")
        return False

    def took_profiles(self, reply):
        if self.failed(reply):
            return False
        self.profiles = reply.get("profiles") or []
        self.took_state(reply)
        return False

    def show_profiling(self):
        if self.state is None:
            return
        if not self.state.get("line_profiler"):
            if self.deps_for != (self.where.get("host"), False):  # (asked again only as line_profiler comes or goes)
                self.deps_for = (self.where.get("host"), False)
                import depsview
                depsview.ask(self.where, self.took_deps)
            return
        self.deps_for, self.missing_shown = (self.where.get("host"), True), None
        on = bool(self.state.get("on"))
        if "profiling" in self.groups and getattr(self, "profiling_shown", None) == (
                self.shown_app, repr(self.state.get("profiling")), on):
            return self.show_lines()
        self.profiling_shown = (self.shown_app, repr(self.state.get("profiling")), on)
        mine = next((p for p in self.state.get("profiling") or [] if p["app"] == self.shown_app), {"functions": []})
        process = Adw.ComboRow(title=wording.say("telemetry.process"), model=Gtk.StringList.new(
            [wording.say(APP_NAMES[a]) for a in PROFILED]), selected=PROFILED.index(self.shown_app))
        process.connect("notify::selected", lambda r, _p: self.picked(PROFILED[r.get_selected()]))
        entry = Adw.EntryRow(title=wording.say("telemetry.functions"), text=", ".join(mine["functions"]),
                             show_apply_button=True)
        entry.connect("apply", lambda e: self.set({"app": self.shown_app, "functions": [
            f.strip() for f in e.get_text().split(",") if f.strip()]}))
        collecting = on
        on = Adw.SwitchRow(title=wording.say("telemetry.line_profiling"), active=collecting and bool(mine["functions"]),
                           sensitive=collecting and bool(mine["functions"] or entry.get_text().strip()))
        entry.connect("changed", lambda e: on.set_sensitive(
            collecting and bool(mine["functions"] or e.get_text().strip())))
        on.connect("notify::active", lambda r, _p: r.get_active() != bool(mine["functions"]) and self.set({
            "app": self.shown_app, "functions": [f.strip() for f in entry.get_text().split(",") if f.strip()]
            if r.get_active() else []}))
        rows = [on, process, entry]
        if mine.get("error"):
            rows.append(row(mine["error"], sensitive=False))
        self.group("profiling", None, rows)
        self.show_lines()

    def picked(self, app):
        self.shown_app = app
        GLib.idle_add(lambda: self.show_profiling() and False)

    def show_lines(self):
        """The process picked's lines profiled in the last hour, added up, the most time first."""
        lines = {}
        for p in getattr(self, "profiles", []):
            if p.get("app") != self.shown_app:
                continue
            for x in p.get("lines") or []:
                k = (x["file"], x["line"])
                got = lines.setdefault(k, {**x, "hits": 0, "seconds": 0.0})
                got["hits"] += x["hits"]
                got["seconds"] += x["seconds"]
        top = sorted(lines.values(), key=lambda x: -x["seconds"])[:LINES_SHOWN]
        if "lines" in self.groups and self.lines_shown == (self.shown_app, repr(top)):
            return
        self.lines_shown = (self.shown_app, repr(top))
        g = self.group("lines", wording.say("telemetry.top_lines") if top else None)
        for x in top:
            r = row(f"{os.path.basename(x['file'])}:{x['line']}  {x.get('code') or x['function']}",
                    wording.say("telemetry.hits_seconds", hits=f"{x['hits']:,}", seconds=f"{x['seconds']:.3f}"),
                    mono=True)
            r.set_title_lines(1)
            r.set_tooltip_text(f"{x['file']}:{x['line']} ({x['function']})")
            g.add(r)

    def took_deps(self, reply):
        import depsview
        dep = next((d for d in reply.get("deps") or [] if d.get("dep") == "line_profiler"), None)
        if dep is not None and not dep.get("present"):  # (as every feature without what it needs shows it)
            if self.missing_shown == repr(dep) and "profiling" in self.groups:
                return False
            self.missing_shown = repr(dep)
            missing = depsview.Missing(again=self.again)
            missing.set_vexpand(False)
            missing.set_propagate_natural_height(True)
            missing.set_min_content_height(260)  # (its whole box: a scroller in the page gets none of its own)
            missing.show(dep, "telemetry.line_profiling")
            self.group("profiling", None, [missing])
            self.group("lines", None)
            self.lines_shown = None
        return False

    def again(self):
        """Missing's Check again: line_profiler looked for again there, and everything asked again."""
        self.deps_for = ()
        self.ask()


def client_network(win):
    """The window's app's hosts (Baude's sessionlist.network, Record's recordstore.network)."""
    try:
        if hasattr(win, "session_id"):
            import sessionlist
            return sessionlist.network()
        import recordstore
        return recordstore.network()
    except (ImportError, AttributeError):
        return None
