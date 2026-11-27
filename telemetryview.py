"""What Baude and Record tell their machine's host of chats as it happens (baudelib.telemetry, the telemetry op; the
phone's Telemetry.kt the same), and Settings → Telemetry, which shows what a host keeps. Recorder, one an app: while
that host's telemetry is on (its Info's telemetry, followed as it changes), each chat opened, coming in front or
leaving it and each turn sent (a turn's end is its host's to keep: one source for it), by its host's id and its
session's id, told at once with its own time; its errors and its counts (uitrack's); and line profiles of what the host says this app profiles
(lineprofile), every PROFILE_S, while its line profiling is on too. While it's off nothing is looked at or sent.
Nothing is sent anywhere but this machine's host. watch(win, app, network) is the one hook a window needs; look and
turn_sent are the chat's."""
import datetime
import os
import time
import weakref

from gi.repository import Adw, GLib, Gtk, Pango

from baudelib import counters, errorbook, funccount, lineprofile, providers, telemetry, uiindex, wire, wording
import uitrack
import widgets

PROFILE_S = 30  # (how often what's profiled here is told)
QUEUE_MAX = 1000  # (events kept for a host that isn't there yet)
FRONT_IDLE_S = 300  # (no input for this long, or the screen locked, or the display asleep: nobody's at the chat in front)
AWAY_LOOK_S = 5  # (how often whether anyone's still at the chat in front is looked at)
APP_NAMES = {"host": "telemetry.host_process", "baude": "mesh_versions.baude", "record": "mesh_versions.record",
             "emission": "mesh_versions.emission", "multiplex": "deps.multiplex", "phone": "mesh_versions.baude"}
APP_LOGOS = {"host": "baude", "baude": "baude", "record": "record", "emission": "emission", "multiplex": "multiplex",
             "phone": "baude"}  # (each process's app's logo: schema/icons/baude's baude-app-<logo>-symbolic)
LOGO_PX = 16  # (as big as a model's logo)
PROFILED = ("host", "baude", "record")
WEEKS = 53  # (the tokens' grid: a year, a column a week)
LINES_SHOWN = 10
GROUPS = ("error", "tokens", "models", "machine", "processes", "used", "slowest", "errors", "profiling", "lines")  # (the page's, in
# this order)
ERRORS_SHOWN = 20  # (the latest to happen)
COUNTS_SHOWN = 10  # (Most Used's and Slowest's rows)
COUNTS_DAYS = 7
USED_KINDS = ("part", "menu", "screen")  # (what the user used: Most Used)
TIMED_KINDS = ("part", "menu", "op", "callback")  # (what's run for them: Slowest, by its time each)
ERRORS_DAYS = 7
REFRESH_S = 5  # (the machine's rows asked for again while the page is shown)
PROCS_BACK_S = 150  # (as far back as the page first looks for a sample with its processes: they're taken every 2
# minutes at the most, with nobody at the machine)
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
        self.profiler, self.profile_timer, self.told_error = lineprofile.Profiler(name), None, None
        self.picked = []  # (the counts that picked what's profiled now)
        self.away_timer = None  # (looking at whether anyone's at the chat in front, while it's on)
        self.count_timer = None  # (what's counted told every counters.FLUSH_S, while it's on)
        self.errors_due, self.known = False, False  # (this process's errors to be told in a moment; whether its
        # host's telemetry is known: errors before it held, not dropped)
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
        self.known = True
        t = here.info.get("telemetry") or {}
        on, pid = bool(t.get("on")), here.info.get("pid")
        if on != self.on:
            self.on = on
            if on:
                uitrack.counts_now()  # (what was counted till now: not kept)
                self.hello()
                self.errors_soon()
                for win in list(self.windows):
                    self.look(win)
                self.away_timer = GLib.timeout_add_seconds(AWAY_LOOK_S, self.look_away)
                self.count_timer = GLib.timeout_add_seconds(counters.FLUSH_S, self.tell_counts)
            else:
                self.queue.clear()
                errorbook.BOOK.take()
                self.opened.clear()
                self.front = None
                if self.away_timer is not None:
                    GLib.source_remove(self.away_timer)
                    self.away_timer = None
                if self.count_timer is not None:
                    GLib.source_remove(self.count_timer)
                    self.count_timer = None
                uitrack.counts_now()  # (what was counted till now: not kept)
        elif on and pid != self.host_pid:
            self.hello()  # (a host started again: its samples of this process begin again)
            if self.host_pid is not None:  # (and the chat in front told it again: it knows none)
                self.front = None
                for win in list(self.windows):
                    self.look(win)
        self.host_pid = pid
        mine = next((p for p in t.get("profiling") or [] if p.get("app") == self.name), {})
        functions = (mine.get("functions") or []) if on and t.get("line_profiling") else []
        funccount.switch(bool(on and t.get("line_profiling")))  # (counted only to aim line profiling)
        if functions != self.profiler.functions:
            self.profile(functions, mine.get("picked") or [])

    def watch(self, win):
        self.windows.add(win)
        uitrack.watch(win)
        GLib.idle_add(lambda: self.look(win) and False)
        win.connect("notify::is-active", lambda w, _p: self.look(w))
        if (tabs := getattr(win, "tabs", None)) is not None:
            tabs.view.connect("notify::selected-page", lambda *_: self.look(win))
            for sig in ("page-attached", "page-detached"):
                tabs.view.connect(sig, lambda *_: GLib.idle_add(lambda: self.look(win) and False))
        win.connect("destroy", lambda w: self.look(w, gone=True))

    # ---- chats
    def look(self, win, gone=False):
        """win looked at again: a chat opened in it since, and the chat in front, if it's changed, told. In front
        means with someone at the machine (widgets.user_present, within FRONT_IDLE_S): what its host tells every head
        of the mesh, which notifies of no chat in front anywhere (Info's front)."""
        if not self.on:
            return
        open_, front = (set(), None) if gone else chats_in(win)
        if front is not None and widgets.user_present(FRONT_IDLE_S) is False:
            front = None
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

    def look_away(self):
        """Every AWAY_LOOK_S while it's on: the window in front looked at again, for whether anyone's still at it."""
        if not self.on:
            self.away_timer = None
            return False
        held = self.front[0]() if self.front is not None else None
        for win in list(self.windows):
            if win is held or win.is_active():
                self.look(win)
        return True

    def turn_sent(self, win):
        if not self.on:
            return
        self.look(win)
        if (chat := chat_of(win)) is not None:
            self.event("turn_sent", chat)

    def tell_counts(self):
        """Every counters.FLUSH_S: what this process counted since, told as a row."""
        if not self.on:
            self.count_timer = None
            return False
        uitrack.look()
        if (row := uitrack.counts_now()) is not None:
            self.send({"counters": {**row, "app": self.name}}, lambda ok: ok or counters.COUNTERS.give_back(row))
        return True

    def errors_soon(self):
        """(any thread) This process has an error: told in a moment, with any meanwhile."""
        if not self.errors_due:
            self.errors_due = True
            GLib.timeout_add(1000, self.tell_errors)

    def tell_errors(self):
        self.errors_due = False
        if not self.on:
            if self.known:
                errorbook.BOOK.take()
        elif not self.sending:
            self.sending = True
            self.tell()
        return False

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
        errors = errorbook.BOOK.take()
        if not events and not errors:
            self.sending = False
            return False

        def done(ok):
            if not ok:  # (kept, for when the host's there)
                self.queue = (events + self.queue)[-QUEUE_MAX:]
                errorbook.BOOK.give_back(errors)
            self.sending = False
            if ok and self.queue:
                self.sending = True
                GLib.idle_add(self.tell)
        self.send({"events": events, **({"errors": errors} if errors else {})}, done)
        return False

    def send(self, more, done, wait=False):
        msg = {"op": "telemetry", "app": self.name, "pid": os.getpid(), "commit": (wire.HEAD_COMMIT or "")[:12] or None,
               "wire": wire.VERSION, **more}
        if self.profiler.error != self.told_error:
            msg["profile_error"] = self.told_error = self.profiler.error
        here = self.network.here_alive()
        if here is None:
            return done(False)
        if wait:
            return done(here.call(msg, timeout=1).get("op") == "telemetry_kept")
        here.request(msg, lambda r: done(r.get("op") == "telemetry_kept"))

    # ---- profiling
    def profile(self, functions, picked=()):
        """Profiling these from now (picked: the counts that picked them, aim's), what was profiled till now told."""
        last = self.profiler.set(functions)
        if last:
            last["picked"] = self.picked
        self.picked = list(picked)
        if self.profile_timer is not None:
            GLib.source_remove(self.profile_timer)
            self.profile_timer = None
        if functions:
            self.profile_timer = GLib.timeout_add_seconds(PROFILE_S, self.profiled)
        self.send({"profile": last} if last else {}, lambda _ok: None)

    def profiled(self):
        if (p := self.profiler.snapshot()) is not None:
            p["picked"] = self.picked
        if p is not None or self.profiler.error != self.told_error:
            self.send({"profile": p} if p else {}, lambda _ok: None)
        return True

    def quit(self):
        """As the app quits: what's left told (the host waited for a moment at most)."""
        for win in list(self.windows):
            self.look(win, gone=True)
        last = self.profiler.set([])
        events, self.queue = self.queue, []
        errors = errorbook.BOOK.take()
        counted = uitrack.counts_now() if self.on else None
        if self.on and (events or last or errors or counted):
            try:
                self.send({"events": events, **({"profile": last} if last else {}),
                           **({"errors": errors} if errors else {}), **({"counters": {**counted, "app": self.name}} if counted else {})},
                          lambda _ok: None, wait=True)
            except (OSError, ValueError):
                pass


def watch(win, name, network):
    """win's chats told to this machine's host (name: the app's, "baude" or "record"; network: its hosts)."""
    if not _RECORDER:
        _RECORDER.append(Recorder(win.get_application(), name, network))
        uitrack.install(name, _RECORDER[0].errors_soon)
    _RECORDER[0].watch(win)


def look(win):
    if _RECORDER:
        _RECORDER[0].look(win)


def turn_sent(win):
    if _RECORDER:
        _RECORDER[0].turn_sent(win)


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


KINDS = {"input": "meta.input", "cache_write": "telemetry.cache_write", "cache_read": "telemetry.cache_read",
         "output": "meta.output", "input_with_cache": "telemetry.input_with_cache"}  # (usage's tokens, each kind apart,
# in the order they're shown, and their names)


def said_tokens(t):
    """A day's or a model's tokens, each kind apart ("1.2M In · 30M Cache Reads · 50k Out"): those it has; an older
    turn's input, cache among it, as that."""
    return " · ".join(wording.say(f"telemetry.day_{k}", tokens=tokens(t[k]))
                      for k in ("input_with_cache", "input", "cache_write", "cache_read", "output")
                      if t.get(k) or k == "output")


def token_rows(t):
    """A row each kind of token, t's count of it after its name: input, cache writes, cache reads, output, and (only
    where there are some) older turns' input with their cache in it."""
    return [row(wording.say(name), tokens(t.get(k) or 0)) for k, name in KINDS.items()
            if k != "input_with_cache" or t.get(k)]


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
        d = self.days.get(day.isoformat()) or {}
        tooltip.set_text(wording.say("telemetry.day_tokens", day=day.strftime("%a %-d %b %Y"), tokens=said_tokens(d)))
        return True


def effort_said(effort):
    key = f"mode_effort.effort_{effort}"
    return wording.say(key) if effort and wording.has(key) else effort or "–"


def model_expander(m, models):
    """A model's row, named as every app names it (providers.model_name), its slug on hover, its turns after it;
    opened, its tokens, a row each kind (token_rows)."""
    value = m["model"]
    r = Adw.ExpanderRow(title=GLib.markup_escape_text(providers.model_name(value, models) or "–"))
    r.set_tooltip_text(providers.model_slug(value, models) if isinstance(value, str) else None)
    r.add_suffix(Gtk.Label(label=wording.say("telemetry.turns", turns=m["turns"]), css_classes=["dim-label", "numeric"]))
    for t in token_rows(m):
        r.add_row(t)
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


def where_said(screen, part):
    """A schema/ui entity and its part, as an error's screen and part say them."""
    return " · ".join(x for x in (screen, part) if x) or "–"


def most_used(rows):
    """What the user used most (USED_KINDS, in the apps), the most times first."""
    return sorted((c for c in rows if c.get("kind") in USED_KINDS and c.get("app") != "host" and c.get("count")),
                  key=lambda c: (-c["count"], -c["seconds"]))[:COUNTS_SHOWN]


def slowest(rows):
    """What took longest each time it ran (TIMED_KINDS), the longest first."""
    return sorted((c for c in rows if c.get("kind") in TIMED_KINDS and c.get("count")),
                  key=lambda c: -c["seconds"] / c["count"])[:COUNTS_SHOWN]


def count_title(c):
    """A count's name as a row says it: a part's entity and part ("Composer.region · send button"), else its name."""
    name = c.get("name") or ""
    if c.get("kind") in ("part", "menu") and "|copy:" in name:  # (the phone's, where it knows no part: its words' key)
        entity, _, key = name.partition("|copy:")
        try:
            part = uiindex.part_by_copy(entity, key) or wording.say(key)
        except (KeyError, ValueError, TypeError):
            part = key
        return f"{entity} · {part}"
    if c.get("kind") in ("part", "menu") and "/" in name:
        entity, _, part = name.partition("/")
        return f"{entity} · {part}"
    return name


def said_span(seconds):
    s = int(seconds)
    if s >= 3600:
        return wording.say("time.hours_minutes", hours=s // 3600, minutes=s % 3600 // 60)
    if s >= 60:
        return wording.say("time.minutes_seconds", minutes=s // 60, seconds=s % 60)
    return wording.say("time.seconds", seconds=s)


def count_row(c, slow=False):
    """A count: its process's logo, its name, how many times (and a screen's time in front; slow: its time each)."""
    values = [wording.say("telemetry.error_times", count=c["count"])]
    if slow:
        values.insert(0, wording.say("telemetry.average_ms", ms=f"{1000 * c['seconds'] / c['count']:.1f}"))
    elif c.get("kind") == "screen" and c.get("seconds"):
        values.insert(0, said_span(c["seconds"]))
    r = fit_row(count_title(c), *values, mono=c.get("kind") in ("op", "callback", "function", "subprocess"))
    r.add_prefix(logo(c.get("app")))
    r.set_tooltip_text(f"{c.get('kind')}: {c.get('name')}")
    return r


def fit_row(title, *values, **kw):
    return row(title, *values, fit=True, **kw)


class ErrorRow(Adw.ExpanderRow):
    """An error: its process's logo, its type and message, how long ago it last happened and how many times; opened,
    where it was raised, what was on screen, the user's last actions before it, and the code it ran. set(e) brings it
    up to date where it is (opened stays opened)."""

    def __init__(self, e):
        super().__init__()
        self.set_title_lines(1)
        self.add_prefix(logo(e.get("app")))
        self.ago, self.times = (Gtk.Label(css_classes=["dim-label", "numeric"]) for _ in range(2))
        self.add_suffix(self.ago)
        self.add_suffix(self.times)
        self.details, self.shown = [], None
        self.set(e)

    def set(self, e):
        self.set_title(GLib.markup_escape_text(f"{e['type']}: {e['message']}" if e.get("message") and
                                               e["message"] != e["type"] else e["type"]))
        self.ago.set_label(widgets.time_ago(e.get("last") or 0))
        self.times.set_label(wording.say("telemetry.error_times", count=e.get("count") or 1))
        self.set_tooltip_text(e.get("message") or None)
        lines = error_lines(e)
        if lines != self.shown:
            self.shown = lines
            for r in self.details:
                self.remove(r)
            self.details = [fit_row(*line, mono=mono) for *line, mono in lines]
            for r in self.details:
                self.add_row(r)


def error_lines(e):
    """What an error says of itself opened: ([title, values...], monospace) for each row."""
    out = []
    if e.get("file"):
        out.append((wording.say("telemetry.error_where"),
                    f"{e['file']}:{e.get('line')}" + (f" {e['function']}" if e.get("function") else ""), True))
    if e.get("wire_op"):
        out.append((wording.say("telemetry.error_op"), str(e["wire_op"]) + (
            f" · {e['code']}" if e.get("code") is not None else ""), False))
    elif e.get("code") is not None:
        out.append((wording.say("telemetry.error_code"), str(e["code"]), False))
    if e.get("screen") or e.get("part"):
        out.append((wording.say("telemetry.error_screen"), where_said(e.get("screen"), e.get("part")), False))
    for c in reversed(e.get("crumbs") or []):
        key = f"telemetry.action_{c.get('action')}"
        out.append((wording.say(key) if wording.has(key) else str(c.get("action")),
                    where_said(c.get("screen"), c.get("part") or c.get("widget")), widgets.time_ago(c["at"]), False))
    if e.get("commit") or e.get("wire") is not None:
        out.append((wording.say("telemetry.error_version"), wording.say(
            "telemetry.version_of", commit=e.get("commit") or "–", wire=str(e.get("wire") or "–")), True))
    if (first := e.get("first")) and e.get("count", 1) > 1:
        out.append((wording.say("telemetry.error_first"), widgets.time_ago(first), False))
    return out


def row(title, *values, sensitive=True, mono=False, bold=False, visible=True, fit=False):
    """A row: its title, its values after it (dim); fit: each value cut short with … where the row hasn't room."""
    esc = GLib.markup_escape_text(title)
    r = Adw.ActionRow(title=f"<b>{esc}</b>" if bold else esc, sensitive=sensitive, visible=visible)
    if mono:
        r.add_css_class("monospace")
    for v in values:
        if v:
            label = Gtk.Label(label=v, css_classes=["numeric"] if bold else ["dim-label", "numeric"])
            if fit:
                label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                label.set_max_width_chars(60)
                label.set_tooltip_text(v)
            r.add_suffix(label)
    return r


class Page(Adw.PreferencesPage):
    """Settings → Telemetry: the host picked beside New session (as Dependencies has it), Collect Telemetry, then what
    that host keeps: its tokens by day over a year with the most used model and effort and each model's, its machine
    now, its processes now (each with its app's logo, its CPU and memory), and line profiling (switched on or off; what
    each process profiles is Baude's own pick, lineprofile.DEFAULTS, or what telemetry_set named for it) with every
    process's top lines."""

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
        self.made, self.rows = {}, {}
        for key in GROUPS:  # (made once, in their order, shown as they're answered: never taken out and put back,
            # so a row being typed in keeps its place, and the page where it's scrolled to)
            self.made[key] = Adw.PreferencesGroup(visible=False)
            self.add(self.made[key])
        self.state, self.taking, self.groups, self.profiling_rows = None, False, {}, None
        self.deps_for, self.missing_shown, self.lines_shown, self.sampled, self.ticks = (), None, None, None, 0
        self.errors_shown = self.counts_shown = None
        self.error_rows = {}  # (the Errors group's rows, by app and fingerprint)
        self.grid = TokenGrid()
        self.refresh = GLib.timeout_add_seconds(REFRESH_S, self.tick)
        self.connect("destroy", lambda *_: GLib.source_remove(self.refresh))
        self.ask()

    def tick(self):
        if self.get_mapped() and self.state and self.state.get("on"):
            now = time.time()
            self.read({"from": max(self.sampled or 0, now - 3 * REFRESH_S), "what": ["metrics"], "limit": 20},
                      self.took_metrics)
            self.ticks += 1
            if self.ticks % (PROFILE_S // REFRESH_S) == 0:
                self.read_errors()
                self.read_counts()
                if self.state.get("line_profiling"):
                    self.read({"from": now - 3600, "what": ["profiles"], "limit": 500}, self.took_profiles)
        return True

    def read_counts(self):
        self.read({"from": time.time() - COUNTS_DAYS * 86400, "what": ["counted"], "limit": 2000}, self.took_counts)

    def took_counts(self, reply):
        if self.failed(reply):
            return False
        self.took_state(reply)
        rows = reply.get("counted") or []
        if "used" in self.groups and self.counts_shown == repr(rows):
            return False
        self.counts_shown = repr(rows)
        used, slow = most_used(rows), slowest(rows)
        for key, title, these, is_slow in (("used", "telemetry.most_used", used, False),
                                           ("slowest", "telemetry.slowest", slow, True)):
            if these:
                self.group(key, wording.say(title), [count_row(c, slow=is_slow) for c in these])
            else:
                self.drop(key)
        return False

    def read_errors(self):
        self.read({"from": time.time() - ERRORS_DAYS * 86400, "what": ["errors"], "limit": ERRORS_SHOWN},
                  self.took_errors)

    def took_errors(self, reply):
        if self.failed(reply):
            return False
        self.took_state(reply)
        errors = reply.get("errors") or []
        self.show_errors(errors)
        return False

    def show_errors(self, errors):
        """The Errors group's rows brought up to date in place, by app and fingerprint: those that are still there are
        kept (opened stays opened, the page stays where it's scrolled), the new added, the gone removed."""
        g, have, title = self.made["errors"], self.error_rows, wording.say("telemetry.errors")
        if not errors:
            have.clear()
            self.group("errors", title, [row(wording.say("telemetry.no_errors"), sensitive=False)])
            return
        if not have:  # (what was there was the placeholder, or nothing)
            self.group("errors", title)
        keys = [f"{e.get('app')}:{e.get('fingerprint')}" for e in errors]
        for k in [k for k in have if k not in keys]:
            g.remove(have.pop(k))
        for k, e in zip(keys, errors):
            if k in have:
                have[k].set(e)
            else:
                have[k] = ErrorRow(e)
        want = [have[k] for k in keys]
        shown = [r for r in self.rows["errors"] if r in want]
        if shown != want:
            for r in shown:
                g.remove(r)
            for r in want:
                g.add(r)
        self.rows["errors"] = want

    def read(self, msg, done):
        import depsview
        depsview.ask(self.where, done, {"op": "telemetry_read", **msg})

    def set(self, msg):
        import depsview
        depsview.ask(self.where, lambda r: r.get("state") and self.took_state(r), {"op": "telemetry_set", **msg})

    def ask(self):
        now = time.time()
        self.read({"from": now - (WEEKS * 7 + 7) * 86400, "what": ["usage"]}, self.took_usage)
        self.read({"from": now - PROCS_BACK_S, "what": ["metrics"], "limit": 60}, self.took_metrics)
        self.read({"from": now - 3600, "what": ["profiles"], "limit": 500}, self.took_profiles)
        self.read_errors()
        self.read_counts()

    def group(self, key, title, rows=()):
        """The group key shown, titled, with rows (in place of those it had)."""
        g = self.made[key]
        for r in self.rows.get(key, ()):
            g.remove(r)
        self.rows[key] = list(rows)
        for r in rows:
            g.add(r)
        g.set_title(GLib.markup_escape_text(title) if title else "")
        g.set_visible(True)
        self.groups[key] = g
        return g

    def drop(self, key):
        """The group key emptied and hidden, if it's shown."""
        if self.groups.pop(key, None) is not None:
            for r in self.rows.pop(key, ()):
                self.made[key].remove(r)
            self.made[key].set_visible(False)

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
        for r in token_rows({k: sum(d.get(k) or 0 for d in u["days"]) for k in KINDS}):
            box.append(r)
        self.group("tokens", wording.say("telemetry.tokens_by_day"), [box])
        self.group("models", wording.say("telemetry.models") if u["models"] else None,
                   [model_expander(m, models) for m in u["models"]])
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
        with_procs = next((x for x in reversed(samples) if "procs" in x), None)
        if with_procs is None:  # (the latest with its processes: they're taken less often than the machine)
            return False
        procs = sorted((p for p in with_procs.get("procs") or [] if p.get("app") in APP_NAMES),
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
        if self.profiling_rows is None:
            self.make_profiling()
        switch, errors = self.profiling_rows[0], dict(zip(PROFILED, self.profiling_rows[1:]))
        on = bool(self.state.get("on"))
        self.taking = True
        switch.set_active(on and bool(self.state.get("line_profiling")))
        self.taking = False
        switch.set_sensitive(on)
        for p in self.state.get("profiling") or []:
            if (error := errors.get(p.get("app"))) is not None:
                error.set_title(GLib.markup_escape_text(p.get("error") or ""))
                error.set_visible(bool(p.get("error")))
        if self.rows.get("profiling") != self.profiling_rows:
            self.group("profiling", None, self.profiling_rows)
        self.show_lines()

    def make_profiling(self):
        """Line Profiling and each process's error (shown while it has one): made once, then told what the host
        says."""
        switch = Adw.SwitchRow(title=wording.say("telemetry.line_profiling"))
        switch.connect("notify::active", lambda r, _p: self.taking or self.set({"line_profiling": r.get_active()}))
        errors = []
        for app in PROFILED:
            error = row("", sensitive=False, visible=False)
            error.add_prefix(logo(app))
            errors.append(error)
        self.profiling_rows = [switch, *errors]

    def show_lines(self):
        """Every process's lines profiled in the last hour, added up, the most time first, each with its app's logo."""
        lines, picks = {}, {}
        for p in getattr(self, "profiles", []):
            if p.get("app") not in PROFILED:
                continue
            for k in p.get("picked") or []:  # (by its function's file and Class.method, as line_profiler says it)
                module, _, qual = k["function"].partition(":")
                picks[(p["app"], module.replace(".", "/") + ".py", qual)] = k
            for x in p.get("lines") or []:
                k = (p["app"], x["file"], x["line"])
                got = lines.setdefault(k, {**x, "app": p["app"], "hits": 0, "seconds": 0.0})
                got["hits"] += x["hits"]
                got["seconds"] += x["seconds"]
        top = sorted(lines.values(), key=lambda x: -x["seconds"])[:LINES_SHOWN]
        if "lines" in self.groups and self.lines_shown == repr(top):
            return
        self.lines_shown = repr(top)
        rows = []
        for x in top:
            r = row(f"{os.path.basename(x['file'])}:{x['line']}  {x.get('code') or x['function']}",
                    wording.say("telemetry.hits_seconds", hits=f"{x['hits']:,}", seconds=f"{x['seconds']:.3f}"),
                    mono=True)
            r.add_prefix(logo(x["app"]))
            r.set_title_lines(1)
            tip = f"{wording.say(APP_NAMES[x['app']])}: {x['file']}:{x['line']} ({x['function']})"
            if (k := picks.get((x["app"], x["file"], x["function"]))) is not None:  # (aimed at by its counts: which)
                tip += "\n" + wording.say("telemetry.picked_by", seconds=f"{k['seconds']:.1f}")
                if (w := k.get("within")) is not None:
                    tip += "\n" + wording.say("telemetry.picked_within", name=count_title(w),
                                               ms=f"{1000 * w['each']:.0f}")
            r.set_tooltip_text(tip)
            rows.append(r)
        self.group("lines", wording.say("telemetry.top_lines") if top else None, rows)

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
