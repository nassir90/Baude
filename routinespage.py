"""A Bot's routines, on the side panel's Routines page: work it's given on a schedule (baudelib.routines), kept with it
on its host, which gives it a turn with what to do as each is due, Baude open or not. Each one: its name, when it
runs, when it ran last and runs next; paused or not; run now, changed, or deleted. Each run shows in the Bot's chat as
a turn of its own."""
import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

PAGE = "Routines"
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def when(r):
    """How a routine's schedule is said: every 2 hours; weekdays at 09:00."""
    if r.get("every"):
        m = r["every"]
        return f"every {m // 60} hours" if m % 60 == 0 and m >= 120 else (
            "every hour" if m == 60 else f"every {m} minutes")
    days = r.get("days") or []
    said = {(0, 1, 2, 3, 4): "weekdays", (5, 6): "weekends", (0, 1, 2, 3, 4, 5, 6): "every day"}.get(
        tuple(days), ", ".join(DAY_NAMES[d] for d in days))
    return f"{said} at {r.get('at')}"


def stamp(t):
    if not t:
        return "never"
    d = datetime.datetime.fromtimestamp(t)
    today = datetime.date.today()
    return d.strftime("%H:%M") if d.date() == today else d.strftime("%a %d %b %H:%M")


class RoutinesPage(Gtk.Box):
    """The Routines page: the open Bot's routines, read from its host and set there."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.sid, self.remote, self.routines, self.asked = win, None, None, [], 0
        head = Gtk.Box(spacing=6, margin_start=10, margin_end=10, margin_top=8, margin_bottom=4)
        self.title = Gtk.Label(label="Routines", xalign=0, hexpand=True)
        self.title.add_css_class("heading")
        head.append(self.title)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New routine")
        add.add_css_class("flat")
        add.connect("clicked", lambda *_: self.edit(None))
        head.append(add)
        self.append(head)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=10, margin_end=10)
        self.list.add_css_class("boxed-list")
        self.empty = Adw.StatusPage(icon_name="alarm-symbolic", title="No routines yet", vexpand=True,
                                    description="Give this Bot work to do on a schedule: it's given a turn with it as "
                                                "each comes due, on its host, whether Baude's open or not.")
        self.said = Gtk.Label(wrap=True, xalign=0, visible=False, margin_start=10, margin_end=10, margin_top=6)
        self.said.add_css_class("dim-label")
        self.append(Gtk.ScrolledWindow(vexpand=True, child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.append(self.empty)
        self.append(self.said)
        GLib.timeout_add_seconds(30, self.tick)  # (next and last, said again as time goes)

    def show(self, info):
        """The open chat (the sidebar's info, or None): a Bot's routines read from its host."""
        info = info or {}
        bot = bool(info.get("bot")) and info.get("id") and info.get("remote") is not None
        self.sid, self.remote = (info.get("id"), info.get("remote")) if bot else (None, None)
        self.routines = []
        self.draw()
        if self.sid is not None:
            self.ask({"op": "routines", "session": self.sid})

    def refresh(self):
        if self.sid is not None:
            self.ask({"op": "routines", "session": self.sid})

    def tick(self):
        if self.sid is not None and self.get_mapped():
            self.refresh()
        return True

    def ask(self, msg):
        self.asked += 1
        asked, sid = self.asked, self.sid

        def got(r):
            if sid != self.sid or asked != self.asked:
                return
            if r.get("op") == "error":
                self.said.set_label(f"Its host said: {r.get('error')}")
                self.said.set_visible(True)
                return
            self.said.set_visible(False)
            self.routines = r.get("routines") or []
            self.draw()
        self.remote.request(msg, got)

    def save(self, routines):
        self.ask({"op": "routines", "session": self.sid, "routines": routines})

    def draw(self):
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        for r in self.routines:
            row = Adw.ActionRow(title=GLib.markup_escape_text(r.get("name") or "Routine"),
                                subtitle=GLib.markup_escape_text(
                                    f"{when(r)} · " + ("paused" if r.get("paused") else f"next {stamp(r.get('next'))}")
                                    + f" · last {stamp(r.get('last'))}"))
            on = Gtk.Switch(active=not r.get("paused"), valign=Gtk.Align.CENTER, tooltip_text="On, or paused")
            on.connect("notify::active", lambda sw, _p, rid=r["id"]: self.save(
                [{**x, "paused": not sw.get_active()} if x["id"] == rid else x for x in self.routines]))
            row.add_suffix(on)
            for icon, tip, fn in (("media-playback-start-symbolic", "Run now",
                                   lambda rid=r["id"]: self.ask({"op": "run_routine", "session": self.sid, "id": rid})),
                                  ("document-edit-symbolic", "Change", lambda x=r: self.edit(x)),
                                  ("user-trash-symbolic", "Delete", lambda rid=r["id"]: self.save(
                                      [x for x in self.routines if x["id"] != rid]))):
                b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER)
                b.add_css_class("flat")
                b.connect("clicked", lambda _b, f=fn: f())
                row.add_suffix(b)
            self.list.append(row)
        self.list.set_visible(bool(self.routines))
        self.empty.set_visible(not self.routines)

    def edit(self, r):
        """A routine made, or (r) changed: what to do, and when."""
        r = r or {}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        name = Gtk.Entry(placeholder_text="Name", text=r.get("name") or "")
        box.append(name)
        prompt = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=6, left_margin=6,
                              right_margin=6)
        prompt.get_buffer().set_text(r.get("prompt") or "")
        frame = Gtk.ScrolledWindow(min_content_height=110, child=prompt)
        frame.add_css_class("card")
        box.append(Gtk.Label(label="What the Bot's to do", xalign=0))
        box.append(frame)
        how = Gtk.DropDown.new_from_strings(["Every", "At a time of day"])
        how.set_selected(1 if r.get("at") else 0)
        box.append(how)
        every = Gtk.Box(spacing=6)
        amount = Gtk.SpinButton.new_with_range(1, 999, 1)
        unit = Gtk.DropDown.new_from_strings(["minutes", "hours"])
        m = r.get("every") or 60
        amount.set_value(m // 60 if m % 60 == 0 else m)
        unit.set_selected(1 if m % 60 == 0 else 0)
        every.append(amount)
        every.append(unit)
        box.append(every)
        daily = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        at = Gtk.Entry(placeholder_text="HH:MM", text=r.get("at") or "09:00", max_width_chars=6)
        daily.append(at)
        days_row = Gtk.Box(spacing=2)
        days = []
        for i, d in enumerate(DAY_NAMES):
            t = Gtk.ToggleButton(label=d, active=i in (r.get("days") if r.get("days") is not None else range(5)))
            days.append(t)
            days_row.append(t)
        daily.append(days_row)
        box.append(daily)

        def shown(*_):
            every.set_visible(how.get_selected() == 0)
            daily.set_visible(how.get_selected() == 1)
        how.connect("notify::selected", shown)
        shown()
        fits = Gtk.ScrolledWindow(child=box, propagate_natural_height=True, max_content_height=440,
                                  hscrollbar_policy=Gtk.PolicyType.NEVER)  # (a short window: its fields scrolled to)
        dlg = Adw.AlertDialog(heading="Change Routine" if r else "New Routine", extra_child=fits,
                              default_response="save", close_response="cancel")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("save", "Save")
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, response):
            if response != "save":
                return
            buf = prompt.get_buffer()
            made = {"id": r.get("id"), "name": name.get_text(), "paused": r.get("paused", False),
                    "prompt": buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)}
            if how.get_selected() == 0:
                made["every"] = int(amount.get_value()) * (60 if unit.get_selected() == 1 else 1)
            else:
                made["at"], made["days"] = at.get_text().strip(), [i for i, t in enumerate(days) if t.get_active()]
            rest = [x for x in self.routines if x["id"] != r.get("id")] if r.get("id") else list(self.routines)
            self.save(rest + [made] if not r.get("id") else [made if x["id"] == r["id"] else x for x in self.routines])
        dlg.connect("response", answered)
        dlg.present(self.win)
