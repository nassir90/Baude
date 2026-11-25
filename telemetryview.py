"""What Baude and Record record of their own use, and show of it (baudelib.telemetry, the telemetry op; the phone's
Telemetry.kt the same): Recorder, one an app, adding up the time each place is on screen (counted only while one of
its windows is in front and input came in the last IDLE_S) and the uses of each Gio action (the window's and the
app's, however they're activated: a menu item, a button with an action name, a shortcut), told to this machine's
host about once a minute and as the app quits. Nothing is sent anywhere else. watch(win, app) is the one hook a
window needs. Page is Settings → Telemetry: the host picked beside New session's, added up over a period."""
import os
import threading
import time
import weakref

from gi.repository import Adw, GLib, Gio, Gtk

from baudelib import telemetry, wire, wording

TICK_S = 5  # (how often the place on screen is looked at)
IDLE_S = 60  # (no input this long: nobody's looking, and nothing's counted)
TELL_S = 60  # (how often what's added up is told to the host)
OVERLAYS = (("lightbox", "images.untitled"), ("terminal", "multiplex.terminal_menu"), ("kube", "window.kubernetes"),
            ("schema", "window.database_schemas"))  # (views over the whole window, the topmost first)
TABS = {"chat": "chats.chat", "note": "telemetry.notes", "gone": "telemetry.notes", "code": "meta.code",
        "fixes": "window.ongoing_feedback"}  # (a main tab, by its key's kind: panels.MainTabs)
APP_NAMES = {"baude": "Baude", "record": "Record", "phone": "Baude"}
PERIODS = (("sidebar.today", 1), ("telemetry.last_7_days", 7), ("telemetry.last_30_days", 30))
_RECORDER = []


def places(win):
    """The places of win on screen (schema/copy keys: telemetry.PLACES): a dialog over it alone; else a view over it
    alone; else the main tab's and the side panel's page."""
    dialog = win.get_visible_dialog() if isinstance(win, Adw.ApplicationWindow) else None
    if dialog is not None and (key := wording.key_of(dialog.get_title() or "")) in telemetry.PLACES:
        return [key]
    for attr, key in OVERLAYS:
        if (w := getattr(win, attr, None)) is not None and w.get_mapped():
            return [key]
    out = []
    tabs = getattr(win, "tabs", None)
    if tabs is not None and tabs.get_mapped() and (shown := tabs.current()) is not None:
        kind = getattr(shown, "tab_key", None)
        if (key := TABS.get(kind if isinstance(kind, str) else (kind or (None,))[0])) is not None:
            out.append(key)
    for stack in (getattr(getattr(win, "panel", None), "stack", None), getattr(win, "right_stack", None)):
        if isinstance(stack, Adw.ViewStack) and stack.get_mapped() and (page := stack.get_visible_child()) is not None:
            if (key := wording.key_of(stack.get_page(page).get_title() or "")) in telemetry.PLACES and key not in out:
                out.append(key)
    return out


def _labels(model, out):
    """Each action a menu model's items name, its item's words (the first one's, for an action with targets)."""
    for i in range(model.get_n_items()):
        action = model.get_item_attribute_value(i, "action", None)
        label = model.get_item_attribute_value(i, "label", None)
        if action is not None and label is not None:
            out.setdefault(action.get_string(), label.get_string())
        for link in ("section", "submenu"):
            if (inner := model.get_item_link(i, link)) is not None:
                _labels(inner, out)


class Recorder:
    """An app's: what it's been used for since it last told its host (seconds by place, uses by action's name)."""

    def __init__(self, app, name):
        self.app, self.name = app, name
        self.windows = weakref.WeakSet()
        self.hooked = weakref.WeakKeyDictionary()  # (an action group: the names of its actions counted)
        self.seconds, self.uses = {}, {}
        self.input_at, self.ticked, self.told = 0.0, time.monotonic(), time.monotonic()
        self.hook(app, "app")
        app.connect("shutdown", lambda *_: self.tell(wait=True))
        GLib.timeout_add_seconds(TICK_S, self.tick)

    def watch(self, win):
        self.windows.add(win)
        seen = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        seen.connect("event", self.heard)
        win.add_controller(seen)
        self.hook(win, "win")

    def heard(self, _controller, _event):
        self.input_at = time.monotonic()
        return False

    def hook(self, group, prefix):
        """group's actions (an app's, a window's) counted as they're activated, those added since too."""
        names = self.hooked.setdefault(group, set())
        for name in group.list_actions():
            if name not in names and isinstance(action := group.lookup_action(name), Gio.SimpleAction):
                names.add(name)
                action.connect("activate", self.used, f"{prefix}.{name}")

    def used(self, _action, _param, name):
        self.uses[name] = self.uses.get(name, 0) + 1

    def tick(self):
        now = time.monotonic()
        step, self.ticked = min(now - self.ticked, 2 * TICK_S), now
        shown = next((w for w in list(self.windows) if w.is_active()), None)
        if shown is not None and now - self.input_at < IDLE_S:
            for place in places(shown):
                self.seconds[place] = self.seconds.get(place, 0) + step
        for win in list(self.windows):
            self.hook(win, "win")
        self.hook(self.app, "app")
        if now - self.told >= TELL_S:
            self.tell()
        return True

    def names(self):
        """uses, each by its menu item's schema/copy key where it has one, else its action's name."""
        labels = {}
        if self.uses:
            for win in list(self.windows):
                from widgets import walk
                for w in walk(win):
                    if isinstance(w, (Gtk.MenuButton, Gtk.PopoverMenu)) and (model := w.get_menu_model()) is not None:
                        _labels(model, labels)
            if (bar := self.app.get_menubar()) is not None:
                _labels(bar, labels)
        out = {}
        for name, n in self.uses.items():
            key = wording.key_of(labels.get(name, "")) or name
            out[key] = out.get(key, 0) + n
        return out

    def tell(self, wait=False):
        """What's been added up, told to this machine's host (on a thread of its own; wait: here, as the app quits),
        and forgotten here once it's kept there. Kept here till then if the host isn't running."""
        self.told = time.monotonic()
        seconds = {p: round(s, 1) for p, s in self.seconds.items() if s >= 0.5}
        uses = self.names()
        if not seconds and not uses:
            return
        self.seconds, self.uses = {}, {}
        msg = {"op": "telemetry", "app": self.name, "seconds": seconds, "uses": uses}
        if wait:
            return self.send(msg)
        threading.Thread(target=self.send, args=(msg,), daemon=True, name="telemetry").start()

    def send(self, msg):
        kept = False
        try:
            if os.path.exists(wire.socket_path()):  # (not started for this: told once it's there)
                ch, _info = wire.reach({"via": "local"}, timeout=5)
                try:
                    ch.sock.settimeout(5)
                    ch.send({**msg, "req": 1})
                    kept = next((m.get("op") == "telemetry_kept" for m in ch if m.get("req") == 1), False)
                finally:
                    ch.close()
        except (OSError, ValueError):
            pass
        if not kept:
            GLib.idle_add(self.keep_back, msg)

    def keep_back(self, msg):
        """A telling the host didn't take: added back, to be told next time."""
        for p, s in msg["seconds"].items():
            self.seconds[p] = self.seconds.get(p, 0) + s
        for n, c in msg["uses"].items():
            self.uses[n] = self.uses.get(n, 0) + c
        return False


def watch(win, name):
    """win's use recorded (name: the app's, "baude" or "record"): its places, its actions, the app's."""
    if not _RECORDER:
        _RECORDER.append(Recorder(win.get_application(), name))
    _RECORDER[0].watch(win)


def said(seconds):
    s = int(round(seconds))
    if s < 60:
        return wording.say("time.seconds", seconds=s)
    if s < 3600:
        return wording.say("time.minutes_seconds", minutes=s // 60, seconds=s % 60)
    return wording.say("time.hours_minutes", hours=s // 3600, minutes=s % 3600 // 60)


def named(key):
    """A place's or use's words: its schema/copy message, else its name as it is (an action's)."""
    return wording.say(key) if wording.has(key) else key


class Page(Adw.PreferencesPage):
    """Settings → Telemetry: the host picked beside New session (as Dependencies has it), a Period, then for each app
    of that host's machine its time in each place and its uses of each item, the most first."""

    def __init__(self, win):
        super().__init__(title=wording.say("telemetry.title"), icon_name="document-open-recent-symbolic")
        from widgets import walk
        for clamp in walk(self):  # (as widgets.settings_page: rows as wide as the dialog)
            if isinstance(clamp, Adw.Clamp):
                clamp.set_maximum_size(100_000)
                clamp.set_tightening_threshold(100_000)
        self.where, host = win.terminal_host()
        top = Adw.PreferencesGroup()
        name = Adw.ActionRow(title=wording.say("new_session.host"))
        name.add_suffix(Gtk.Label(label=host, css_classes=["dim-label"]))
        top.add(name)
        self.period = Adw.ComboRow(title=wording.say("telemetry.period"),
                                   model=Gtk.StringList.new([wording.say(k) for k, _ in PERIODS]), selected=1)
        self.period.connect("notify::selected", lambda *_: self.ask())
        top.add(self.period)
        self.add(top)
        self.groups = []
        self.ask()

    def ask(self):
        import depsview
        days = PERIODS[self.period.get_selected()][1]
        depsview.ask(self.where, lambda reply: self.took(days, reply), {"op": "telemetry_read", "days": days})

    def took(self, days, reply):
        if PERIODS[self.period.get_selected()][1] != days:
            return False  # (another period picked since)
        for g in self.groups:
            self.remove(g)
        self.groups = []

        def group(title, rows):
            g = Adw.PreferencesGroup(title=GLib.markup_escape_text(title) if title else None)
            for words, value in rows:
                row = Adw.ActionRow(title=GLib.markup_escape_text(words), sensitive=value is not None)
                if value is not None:
                    row.add_suffix(Gtk.Label(label=value, css_classes=["dim-label", "numeric"]))
                g.add(row)
            self.add(g)
            self.groups.append(g)
        times, uses = reply.get("times") or [], reply.get("uses") or []
        if reply.get("error") or not times and not uses:
            group(None, [(reply.get("error") or wording.say("telemetry.nothing"), None)])
            return False
        for app in telemetry.APPS:
            app_name = APP_NAMES[app]
            if rows := [(named(t["place"]), said(t["seconds"])) for t in times if t["app"] == app]:
                group(wording.say("telemetry.screen_time", app=app_name), rows)
            if rows := [(named(u["name"]), str(u["count"])) for u in uses if u["app"] == app]:
                group(wording.say("telemetry.used", app=app_name), rows)
        return False
