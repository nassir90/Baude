"""A Bot's routines, on the side panel's Routines page: work it's given on a schedule (baudelib.routines), kept with it
on its host, which gives it a turn with what to do as each is due, Baude open or not. Each one: its name, when it
runs, when it ran last and runs next; paused or not; run now, changed, or deleted. Each run shows in the Bot's chat as
a turn of its own."""
import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from baudelib import wire, wording  # noqa: E402

PAGE = wording.say("routines.title")
DAY_NAMES = (wording.say("routines.mon"), wording.say("routines.tue"), wording.say("routines.wed"),
             wording.say("routines.thu"), wording.say("routines.fri"), wording.say("routines.sat"),
             wording.say("routines.sun"))


def when(r):
    """How a routine's schedule is said: every 2 hours; weekdays at 09:00."""
    if r.get("every"):
        m = r["every"]
        return wording.say("routines.every_hours", n=m // 60) if m % 60 == 0 and m >= 120 else (
            wording.say("routines.every_hour") if m == 60 else wording.say("routines.every_minutes", n=m))
    days = r.get("days") or []
    said = {(0, 1, 2, 3, 4): wording.say("routines.weekdays"), (5, 6): wording.say("routines.weekends"),
            (0, 1, 2, 3, 4, 5, 6): wording.say("routines.every_day")}.get(
        tuple(days), ", ".join(DAY_NAMES[d] for d in days))
    return wording.say("routines.days_at", days=said, time=r.get('at'))


def stamp(t, clock=None):
    """A time as the host's clock has it (clock: its zone and offset; none known: this machine's)."""
    if not t:
        return wording.say("routines.never")
    zone = datetime.timezone(datetime.timedelta(seconds=clock["offset"])) if clock else None
    d = datetime.datetime.fromtimestamp(t, zone)
    today = datetime.datetime.now(zone).date()
    return d.strftime("%H:%M") if d.date() == today else d.strftime("%a %d %b %H:%M")


class RoutinesPage(Gtk.Box):
    """The Routines page: the open Bot's routines, read from its host and set there."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.sid, self.remote, self.routines, self.asked, self.clock = win, None, None, [], 0, None
        head = Gtk.Box(spacing=6, margin_start=10, margin_end=10, margin_top=8, margin_bottom=4)
        head.append(Gtk.Box(hexpand=True))  # (the page's name is the switcher's: New routine at the end)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=wording.say("routines.new_routine"))
        add.add_css_class("flat")
        add.connect("clicked", lambda *_: self.edit(None))
        head.append(add)
        self.append(head)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=10, margin_end=10,
                                valign=Gtk.Align.START)
        self.list.add_css_class("boxed-list")
        empty = Gtk.Label(label=wording.say("routines.no_routines"), margin_top=24,
                          margin_bottom=24)  # (as the side panel's other lists say so: _empty)
        empty.add_css_class("dim-label")
        self.list.set_placeholder(empty)
        self.said = Gtk.Label(wrap=True, xalign=0, visible=False, margin_start=10, margin_end=10, margin_top=6)
        self.said.add_css_class("dim-label")
        self.append(Gtk.ScrolledWindow(vexpand=True, child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.append(self.said)
        self.timer = GLib.timeout_add_seconds(30, self.tick)  # (next and last, said again as time goes)
        self.connect("destroy", lambda *_: GLib.source_remove(self.timer))

    def show(self, info):
        """The open chat (the sidebar's info, or None): a Bot's routines read from its host."""
        info = info or {}
        bot = bool(info.get("bot")) and info.get("id") and info.get("remote") is not None
        sid, remote = (info.get("id"), info.get("remote")) if bot else (None, None)
        if sid == self.sid and remote is self.remote:
            return  # (shown already: the list as it was, kept current by tick and refresh)
        self.sid, self.remote, self.clock = sid, remote, None
        self.routines = []
        self.said.set_visible(False)
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
        doing = "run" if msg.get("op") == "run_routine" else "save" if "set" in msg else "delete" if "delete" in msg \
            else "load"

        def got(r):
            if sid != self.sid or asked != self.asked:
                return
            if r.get("op") == "error":
                self.said.set_label(wording.say("routines.couldnt", doing=doing))
                self.said.set_tooltip_text(wire.said(r) or None)
                self.said.set_visible(True)
                return
            self.said.set_visible(False)
            self.routines = r.get("routines") or []
            self.clock = r.get("clock") or self.clock
            self.draw()
        self.remote.request(msg, got)

    def save(self, routine):
        """One routine made or changed on its host (as it is there: none of the others touched)."""
        self.ask({"op": "routines", "session": self.sid, "set": routine})

    def delete(self, rid):
        self.ask({"op": "routines", "session": self.sid, "delete": rid})

    def draw(self):
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        for r in self.routines:
            row = Adw.ActionRow(title=GLib.markup_escape_text(r.get("name") or wording.say("routines.routine")),
                                subtitle=GLib.markup_escape_text(
                                    f"{when(r)} · " + (wording.say("routines.paused") if r.get("paused") else
                                                       wording.say("routines.next",
                                                                   time=stamp(r.get('next'), self.clock)))
                                    + (" · " + wording.say("routines.last", time=stamp(r.get('last'), self.clock))
                                       if r.get("last") else "")
                                    + (f" · {r['error']}" if r.get("error") else "")))
            on = Gtk.Switch(active=not r.get("paused"), valign=Gtk.Align.CENTER,
                            tooltip_text=wording.say("routines.turn_off"))
            on.connect("notify::active", lambda sw, _p, x=r: self.save({**x, "paused": not sw.get_active()}))
            row.add_suffix(on)
            for icon, tip, fn in (("media-playback-start-symbolic", wording.say("routines.run_now"),
                                   lambda rid=r["id"]: self.ask({"op": "run_routine", "session": self.sid, "id": rid})),
                                  ("document-edit-symbolic", wording.say("routines.change"), lambda x=r: self.edit(x)),
                                  ("user-trash-symbolic", wording.say("common.delete"),
                                   lambda rid=r["id"]: self.delete(rid))):
                b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER)
                b.add_css_class("flat")
                b.connect("clicked", lambda _b, f=fn: f())
                row.add_suffix(b)
            self.list.append(row)

    def edit(self, r):
        """A routine made, or (r) changed: what to do, and when."""
        r = r or {}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        name = Gtk.Entry(placeholder_text=wording.say("lookup.name"), text=r.get("name") or "")
        box.append(name)
        prompt = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=6, left_margin=6,
                              right_margin=6)
        prompt.get_buffer().set_text(r.get("prompt") or "")
        frame = Gtk.ScrolledWindow(min_content_height=110, child=prompt)
        frame.add_css_class("card")
        box.append(Gtk.Label(label=wording.say("routines.task"), xalign=0))
        box.append(frame)
        how = Gtk.DropDown.new_from_strings([wording.say("routines.every"), wording.say("routines.at_a_time")])
        how.set_selected(1 if r.get("at") else 0)
        box.append(how)
        every = Gtk.Box(spacing=6)
        amount = Gtk.SpinButton.new_with_range(5, 999, 1)
        unit = Gtk.DropDown.new_from_strings([wording.say("routines.minutes"), wording.say("routines.hours")])
        unit.connect("notify::selected", lambda u, _p: amount.set_range(*((1, 744) if u.get_selected() else (5, 999))))
        m = r.get("every") or 60
        unit.set_selected(1 if m % 60 == 0 else 0)
        amount.set_value(m // 60 if m % 60 == 0 else m)
        every.append(amount)
        every.append(unit)
        box.append(every)
        daily = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        at = Gtk.Entry(placeholder_text="HH:MM", text=r.get("at") or "09:00", max_width_chars=6)
        daily.append(Gtk.Label(label=wording.say("routines.times_in", zone=self.clock['zone'])
                               if self.clock and self.clock.get("zone") else wording.say("routines.host_clock"),
                               xalign=0))
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
        dlg = Adw.AlertDialog(heading=wording.say("routines.change_heading") if r else
                              wording.say("routines.new_heading"), extra_child=fits,
                              default_response="save", close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("save", wording.say("common.save"))
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
            self.save(made)
        dlg.connect("response", answered)
        dlg.present(self.win)
