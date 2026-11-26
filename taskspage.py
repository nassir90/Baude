"""A Bot's tasks, on the side panel's Tasks page (schema/ui/Tasks.panel.json, docs/tasks-design.md): scripts its host
keeps and runs (baudelib.tasks), by hand or as their triggers fire; their scripts are the Bot's to write (its set_task
tool). The list of them; a task's page (its triggers, inputs and runs); a run's page (its log, its asks of the Bot and
the Bot's answers). Each read from the Bot's host and set there."""
import datetime
import json
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from baudelib import wire, wording  # noqa: E402
from baudelib.tasks import LOG_SHOWN, MAX_EVERY, MIN_EVERY  # noqa: E402
from markdownview import CodeBlock  # noqa: E402
from panels import natural_height  # noqa: E402
from widgets import Spinner, themed_icon  # noqa: E402

PAGE = wording.say("tasks.title")
DAY_NAMES = tuple(wording.say(f"tasks.{d}") for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"))
TYPES = (("text", "tasks.text"), ("number", "tasks.number"), ("bool", "tasks.yes_no"), ("choice", "tasks.choice"))
KINDS = (("schedule", "tasks.schedule"), ("git_push", "tasks.git_push"))
PAYLOAD = {"schedule": ("time",), "git_push": ("repo", "branch", "before", "after", "commit", "message", "author")}
STATUS_ICONS = {"done": "object-select-symbolic", "failed": "dialog-error-symbolic", "stopped": "process-stop-symbolic",
                "skipped": "go-next-symbolic"}
GOING = ("running", "asking")


def branch_icon():
    return themed_icon("vcs-branch-symbolic", "media-playlist-shuffle-symbolic")


def kind_icon(kind):
    return {"schedule": "alarm-symbolic", "git_push": branch_icon()}.get(kind, "media-playback-start-symbolic")


def when(t):
    """What fires a trigger, said: every 2 hours; weekdays at 09:00; push to main in ~/site."""
    if t.get("kind") == "git_push":
        repo = t.get("repo") or ""
        home = os.path.expanduser("~")
        if repo == home or repo.startswith(home + os.sep):
            repo = "~" + repo[len(home):]
        return wording.say("tasks.push_to", branch=t.get("branch") or "", repo=repo)
    if t.get("every"):
        m = t["every"]
        return wording.say("tasks.every_hours", n=m // 60) if m % 60 == 0 and m >= 120 else (
            wording.say("tasks.every_hour") if m == 60 else wording.say("tasks.every_minutes", n=m))
    days = t.get("days") or []
    said = {(0, 1, 2, 3, 4): wording.say("tasks.weekdays"), (5, 6): wording.say("tasks.weekends"),
            (0, 1, 2, 3, 4, 5, 6): wording.say("tasks.every_day")}.get(
        tuple(days), ", ".join(DAY_NAMES[d] for d in days))
    return wording.say("tasks.days_at", days=said, time=t.get("at"))


def stamp(t, clock=None, short=False):
    """A time as the host's clock has it: HH:MM today, else its day too (short: without the weekday)."""
    if not t:
        return wording.say("tasks.never")
    zone = datetime.timezone(datetime.timedelta(seconds=clock["offset"])) if clock else None
    d = datetime.datetime.fromtimestamp(t, zone)
    return d.strftime("%H:%M") if d.date() == datetime.datetime.now(zone).date() else \
        d.strftime("%d %b %H:%M" if short else "%a %d %b %H:%M")


def ran_at(run, clock=None, short=False):
    """When a run started, as its host's clock had it then (its own offset), else as it has it now."""
    offset = run.get("offset")
    return stamp(run.get("started"), {"offset": offset} if isinstance(offset, int) else clock, short)


def took(seconds):
    s = int(round(seconds or 0))
    return wording.say("tasks.seconds", n=s) if s < 60 else wording.say("tasks.minutes_took", m=s // 60, s=s % 60)


def status_icon(run):
    """A run's state, as an icon (a spinner while it's going; nothing for none)."""
    if run is None:
        return Gtk.Box(width_request=16)
    if run.get("status") in GOING:
        w = Spinner(width_request=16, height_request=16, valign=Gtk.Align.CENTER)
    else:
        w = Gtk.Image(icon_name=STATUS_ICONS.get(run.get("status"), "object-select-symbolic"))
        if run.get("status") == "failed":
            w.add_css_class("error")
        elif run.get("status") == "done":
            w.add_css_class("success")
    w.set_tooltip_text(wording.say("tasks.status", status=run.get("status") or ""))
    return w


def flat(icon, tip, fn):
    b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER)
    b.add_css_class("flat")
    b.connect("clicked", lambda *_: fn())
    return b


def dim(text, **kw):
    lbl = Gtk.Label(label=text, xalign=0, **kw)
    lbl.add_css_class("dim-label")
    return lbl


def dropdown(pairs, picked):
    """A drop-down of (value, copy key) pairs, picked's selected: (it, its values)."""
    dd = Gtk.DropDown.new_from_strings([wording.say(k) for _v, k in pairs])
    values = [v for v, _k in pairs]
    if picked in values:
        dd.set_selected(values.index(picked))
    return dd, values


def alert(win, heading, child, responses, done):
    """An alert dialog: child, scrolled if it's tall; responses [(id, label, suggested)]; done(id) for each but
    cancel."""
    fits = Gtk.ScrolledWindow(child=child, propagate_natural_height=True, max_content_height=520,
                              hscrollbar_policy=Gtk.PolicyType.NEVER)
    dlg = Adw.AlertDialog(heading=heading, extra_child=fits, close_response="cancel",
                          default_response=responses[-1][0])
    for rid, label, suggested in responses:
        dlg.add_response(rid, label)
        if suggested:
            dlg.set_response_appearance(rid, Adw.ResponseAppearance.SUGGESTED)
    dlg.connect("response", lambda _d, r: r != "cancel" and done(r))
    dlg.present(win)
    return dlg


class TasksPage(Gtk.Box):
    """The Tasks page: the open Bot's tasks, read from its host and set there; a task's page; a run's."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.sid, self.remote, self.asked, self.clock = win, None, None, 0, None
        self.tasks, self.task, self.runs, self.run, self.log, self.bot = [], None, [], None, [], None
        self.drawn, self.tail = None, None  # (the run drawn: (its id, how many lines, its last); the block last drawn)
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.append(self.stack)
        # the list
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=10, margin_end=10, margin_top=10,
                                valign=Gtk.Align.START)
        self.list.add_css_class("boxed-list")
        empty = Gtk.Label(label=wording.say("tasks.no_tasks"), margin_top=24, margin_bottom=24)
        empty.add_css_class("dim-label")
        self.list.set_placeholder(empty)
        self.list.connect("row-activated", lambda _l, row: self.open_task(row.task_id))
        box.append(Gtk.ScrolledWindow(vexpand=True, child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.stack.add_named(box, "list")
        # a task's page and a run's: their header rows, and what scrolls under them
        self.task_box, self.task_title, self.task_body = self._sub("list")
        self.run_box, self.run_title, self.run_body = self._sub("task")
        self.stack.add_named(self.task_box, "task")
        self.stack.add_named(self.run_box, "run")
        self.said = Gtk.Label(wrap=True, xalign=0, visible=False, margin_start=10, margin_end=10, margin_top=6,
                              margin_bottom=6)
        self.said.add_css_class("dim-label")
        self.append(self.said)
        self.ticks = 0
        self.timer = GLib.timeout_add_seconds(1, self.tick)
        self.connect("destroy", lambda *_: GLib.source_remove(self.timer))
        self.connect("map", lambda *_: self.refresh())  # (switched to: read as it is now)

    def _sub(self, back_to):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(spacing=4, margin_start=6, margin_end=6, margin_top=6, margin_bottom=4)
        back = Gtk.Button(icon_name="go-previous-symbolic")
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self.go(back_to))
        head.append(back)
        title = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("heading")
        head.append(title)
        box.append(head)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_start=10, margin_end=10,
                       margin_bottom=12)
        box.append(natural_height(Gtk.ScrolledWindow(vexpand=True, child=body,
                                                     hscrollbar_policy=Gtk.PolicyType.NEVER)))
        box.head, box.back = head, back
        return box, title, body

    # ---- the host
    def show(self, info):
        """The open chat (the sidebar's info, or None): a Bot's tasks read from its host."""
        info = info or {}
        bot = bool(info.get("bot")) and info.get("id") and info.get("remote") is not None
        sid, remote = (info.get("id"), info.get("remote")) if bot else (None, None)
        if sid == self.sid and remote is self.remote:
            return
        self.sid, self.remote, self.clock = sid, remote, None
        self.bot = (info.get("bot") or {}).get("name") if bot else None
        self.tasks, self.task, self.run = [], None, None
        self.said.set_visible(False)
        self.stack.set_visible_child_name("list")
        self.draw_list()
        self.refresh()

    def refresh(self):
        """What's shown read again: the list, and the task or run open."""
        if self.sid is None:
            return
        self.ask({"op": "tasks", "session": self.sid})
        shown = self.stack.get_visible_child_name()
        if shown == "task" and self.task:
            self.ask({"op": "task", "session": self.sid, "id": self.task["id"]})
        if shown == "run" and self.run:
            self.ask({"op": "task_run", "session": self.sid, "run": self.run["id"]})

    def tick(self):
        self.ticks += 1
        if self.sid is None or not self.get_mapped():
            return True
        shown = self.stack.get_visible_child_name()
        going = any((t.get("last") or {}).get("status") in GOING for t in self.tasks)
        if shown == "run" and self.run and self.run.get("status") in GOING:
            self.ask({"op": "task_run", "session": self.sid, "run": self.run["id"]})
        if self.ticks % (5 if going else 30) == 0:
            self.ask({"op": "tasks", "session": self.sid})
            if shown == "task" and self.task and going:
                self.ask({"op": "task", "session": self.sid, "id": self.task["id"]})
        return True

    def ask(self, msg):
        self.asked += 1
        sid = self.sid
        op = msg.get("op")
        doing = "run" if op == "run_task" else "stop" if op == "task_stop" else "save" if "set" in msg else \
            "delete" if "delete" in msg else "load"

        def got(r):
            if sid != self.sid:
                return
            if r.get("op") == "error":
                self.said.set_label(wording.say("tasks.couldnt", doing=doing))
                self.said.set_tooltip_text(wire.said(r) or None)
                self.said.set_visible(True)
                return
            self.said.set_visible(False)
            self.took(r)
        self.remote.request(msg, got)

    def took(self, r):
        op = r.get("op")
        self.clock = r.get("clock") or self.clock
        if op == "tasks":
            self.tasks = r.get("tasks") or []
            self.draw_list()
            if self.task and not any(t["id"] == self.task["id"] for t in self.tasks):
                self.task = None
                self.go("list")
        elif op == "task":
            self.task, self.runs = r.get("task"), r.get("runs") or []
            self.draw_task()
        elif op in ("task_run", "task_started"):
            self.run = r.get("run")
            if op == "task_run":
                self.log, self.bot = r.get("log") or [], r.get("bot") or self.bot
                self.draw_run()
            else:
                self.ask({"op": "tasks", "session": self.sid})
                if self.task and self.task["id"] == self.run["task"]:
                    self.ask({"op": "task", "session": self.sid, "id": self.task["id"]})

    def go(self, name):
        self.stack.set_visible_child_name(name)
        self.refresh()

    def save(self, task):
        self.ask({"op": "tasks", "session": self.sid, "set": task})

    def kept(self, task, **changed):
        """task as it's sent to be set again (its script left as it is: the Bot's to change), with changed."""
        return {**{k: task.get(k) for k in ("id", "name", "runtime", "form", "triggers", "fallback", "timeout_s",
                                            "paused")}, **changed}

    # ---- the list
    def draw_list(self):
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        for t in self.tasks:
            row = Adw.ActionRow(title=GLib.markup_escape_text(t.get("name") or wording.say("tasks.task")),
                                activatable=True)
            row.task_id = t["id"]
            row.add_prefix(status_icon(t.get("last")))
            on = Gtk.Switch(active=not t.get("paused"), valign=Gtk.Align.CENTER,
                            tooltip_text=wording.say("tasks.turn_off"))
            on.connect("notify::active", lambda sw, _p, x=t: self.save(self.kept(x, paused=not sw.get_active())))
            row.add_suffix(on)
            row.add_suffix(flat("media-playback-start-symbolic", wording.say("tasks.run"), lambda x=t: self.start(x)))
            self.list.append(row)

    def start(self, task):
        """task run now: its inputs filled first, if it has any."""
        if task.get("form"):
            return self.run_form(task)
        self.ask({"op": "run_task", "session": self.sid, "id": task["id"]})

    # ---- a task's page
    def open_task(self, tid):
        self.task = next((t for t in self.tasks if t["id"] == tid), None)
        self.runs = []
        self.draw_task()
        self.stack.set_visible_child_name("task")
        self.ask({"op": "task", "session": self.sid, "id": tid})

    def group(self, title, *suffix):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head = Gtk.Box(spacing=6)
        lbl = Gtk.Label(label=title, xalign=0, hexpand=True)
        lbl.add_css_class("heading")
        head.append(lbl)
        for w in suffix:
            head.append(w)
        box.append(head)
        self.task_body.append(box)
        return box

    def listbox(self, into):
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lb.add_css_class("boxed-list")
        lb.add_css_class("tasks-rows")
        into.append(lb)
        return lb

    def draw_task(self):
        while (c := self.task_body.get_first_child()) is not None:
            self.task_body.remove(c)
        head = self.task_box.head
        while (c := head.get_last_child()) is not self.task_title:
            head.remove(c)
        t = self.task
        if t is None:
            return
        self.task_title.set_label(t.get("name") or wording.say("tasks.task"))
        self.task_box.back.set_tooltip_text(PAGE)
        head.append(flat("media-playback-start-symbolic", wording.say("tasks.run"), lambda: self.start(t)))
        head.append(flat("document-edit-symbolic", wording.say("tasks.change"), lambda: self.change(t)))
        head.append(flat("user-trash-symbolic", wording.say("common.delete"), lambda: self.delete(t)))
        # triggers
        g = self.group(wording.say("tasks.triggers"),
                       flat("list-add-symbolic", wording.say("tasks.add_trigger"), lambda: self.add_trigger(t)))
        if not t.get("triggers"):
            g.append(dim(wording.say("tasks.by_hand")))
        else:
            lb = self.listbox(g)
            for tr in t["triggers"]:
                row = Adw.ActionRow(title=GLib.markup_escape_text(when(tr)))
                row.add_prefix(Gtk.Image(icon_name=kind_icon(tr["kind"])))
                if tr.get("error"):
                    warn = Gtk.Image(icon_name="dialog-warning-symbolic", tooltip_text=tr["error"])
                    warn.add_css_class("warning")
                    row.add_suffix(warn)
                row.add_suffix(flat("list-remove-symbolic", wording.say("tasks.remove_trigger"),
                                    lambda x=tr: self.save(self.kept(t, triggers=[y for y in t["triggers"]
                                                                                  if y["id"] != x["id"]]))))
                lb.append(row)
        # inputs
        g = self.group(wording.say("signal_card.inputs"),
                       flat("document-edit-symbolic", wording.say("tasks.change"), lambda: self.edit_form(t)))
        if not t.get("form"):
            g.append(dim(wording.say("signal_card.no_inputs")))
        else:
            lb = self.listbox(g)
            for f in t["form"]:
                row = Adw.ActionRow(title=GLib.markup_escape_text(f.get("label") or f["name"]))
                row.add_suffix(dim(wording.say(dict(TYPES)[f["type"]])))
                lb.append(row)
        # runs
        g = self.group(wording.say("tasks.runs"))
        if not self.runs:
            g.append(dim(wording.say("tasks.no_runs")))
        else:
            lb = self.listbox(g)
            lb.connect("row-activated", lambda _l, row: self.open_run(row.run))
            for r in self.runs:
                title = ran_at(r, self.clock, True) + (f" · {wording.say('tasks.dry_tag')}" if r.get("dry") else "")
                row = Adw.ActionRow(title=GLib.markup_escape_text(title), activatable=True)
                row.run = r
                row.add_prefix(status_icon(r))
                row.add_suffix(Gtk.Image(icon_name=kind_icon(r.get("kind"))))
                v = dim(r.get("version") or "")
                v.add_css_class("mono")
                row.add_suffix(v)
                lb.append(row)

    # ---- a run's page
    def open_run(self, run):
        self.run, self.log, self.drawn = run, [], None
        self.draw_run()
        self.stack.set_visible_child_name("run")
        self.ask({"op": "task_run", "session": self.sid, "run": run["id"]})

    def draw_run(self):
        """The run's header and facts drawn again, and its log's lines since it was last drawn added (all of it
        drawn again only for another run, or a log cut at its start)."""
        head = self.run_box.head
        while (c := head.get_last_child()) is not self.run_title:
            head.remove(c)
        r = self.run
        if r is None:
            while (c := self.run_body.get_first_child()) is not None:
                self.run_body.remove(c)
            self.drawn = None
            return
        self.run_title.set_label(f"{r.get('name') or wording.say('tasks.task')} · {ran_at(r, self.clock)}")
        self.run_box.back.set_tooltip_text(r.get("name") or wording.say("tasks.task"))
        if r.get("status") in GOING:
            head.append(flat("media-playback-stop-symbolic", wording.say("common.stop"),
                             lambda: self.ask({"op": "task_stop", "session": self.sid, "run": r["id"]})))
        kind = {"manual": "tasks.by_hand", "schedule": "tasks.schedule", "git_push": "tasks.git_push"}
        facts = [r.get("error") or wording.say("tasks.status", status=r["status"]),
                 wording.say(kind.get(r.get("kind"), "tasks.by_hand"))]
        if r.get("ended"):
            facts.append(took(r.get("seconds")))
        if r.get("dry"):
            facts.append(wording.say("tasks.dummy_answers"))
        facts.append(r.get("version") or "")
        line = Gtk.Box(spacing=6)
        line.append(status_icon(r))
        line.append(dim(" · ".join(f for f in facts if f), wrap=True))
        drawn, log = self.drawn, self.log
        n = drawn[1] if drawn and drawn[0] == r["id"] and drawn[1] <= len(log) < LOG_SHOWN and \
            (drawn[1] == 0 or log[drawn[1] - 1] == drawn[2]) else None
        if n is None:
            while (c := self.run_body.get_first_child()) is not None:
                self.run_body.remove(c)
            self.tail, n = None, 0
        else:
            self.run_body.remove(self.run_body.get_first_child())
        self.run_body.prepend(line)
        for x in log[n:]:
            self.log_line(x)
        self.drawn = (r["id"], len(log), log[-1] if log else None)

    def log_line(self, x):
        """One line of the run's log added: output and errors run together in a code block of their own (errors in
        the error colour), each ask of the Bot and its answer a card, the rest plain lines."""
        k, text = x.get("kind"), x.get("text") or ""
        if k in ("out", "err"):
            if self.tail is not None and self.tail[0] == k:
                self.tail[2].append(text)
                self.tail[1].set_code("\n".join(self.tail[2]))
                return
            block = CodeBlock(text)
            if k == "err":
                block.label.add_css_class("error")
            self.run_body.append(block)
            self.tail = (k, block, [text])
            return
        self.tail = None
        if k in ("ask", "answer"):
            bot = self.bot or wording.say("sidebar.bot")
            head_text = wording.say("tasks.bot_answered", bot=bot) if k == "answer" else \
                wording.say("tasks.handed_bot", bot=bot) if x.get("error") else \
                wording.say("tasks.asked_bot", bot=bot)
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.add_css_class("card")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=10, margin_end=10,
                            margin_top=8, margin_bottom=8)
            h = Gtk.Label(label=head_text, xalign=0)
            h.add_css_class("heading")
            inner.append(h)
            said = json.dumps(x["values"], ensure_ascii=False) if x.get("values") else text
            inner.append(Gtk.Label(label=said, xalign=0, wrap=True, selectable=True,
                                   wrap_mode=Pango.WrapMode.WORD_CHAR))
            card.append(inner)
            self.run_body.append(card)
            return
        self.run_body.append(Gtk.Label(label=text, xalign=0, wrap=True, selectable=True,
                                       wrap_mode=Pango.WrapMode.WORD_CHAR))

    # ---- dialogs
    def change(self, t):
        """t changed: its name, and whether its failures go to the Bot."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        name = Gtk.Entry(placeholder_text=wording.say("common.name"), text=t.get("name") or "")
        box.append(name)
        fallback = Adw.SwitchRow(title=wording.say("tasks.fallback"), active=t.get("fallback", True))
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lb.add_css_class("boxed-list")
        lb.append(fallback)
        box.append(lb)
        alert(self.win, wording.say("tasks.change_heading"), box,
              [("cancel", wording.say("common.cancel"), False), ("save", wording.say("common.save"), True)],
              lambda _r: self.save(self.kept(t, name=name.get_text(), fallback=fallback.get_active())))

    def delete(self, t):
        """t deleted, once that's confirmed: its runs and scripts too."""
        name = t.get("name") or wording.say("tasks.task")
        dlg = Adw.AlertDialog(heading=wording.say("tasks.delete_heading", name=name), close_response="cancel",
                              default_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("delete", wording.say("common.delete"))
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "delete" and self.ask(
            {"op": "tasks", "session": self.sid, "delete": t["id"]}, lambda _r: self.go("list")))
        dlg.present(self.win)
        return dlg

    def field_widget(self, f):
        """(a control for an input, its value as it's read)"""
        if f["type"] == "bool":
            w = Gtk.Switch(active=bool(f.get("default")), halign=Gtk.Align.START)
            return w, w.get_active
        if f["type"] == "number":
            w = Gtk.SpinButton.new_with_range(-1e9, 1e9, 1)
            w.set_value(float(f.get("default") or 0))
            return w, lambda: w.get_value()
        if f["type"] == "choice":
            w = Gtk.DropDown.new_from_strings(f.get("choices") or [""])
            if f.get("default") in (f.get("choices") or []):
                w.set_selected(f["choices"].index(f["default"]))
            return w, lambda: (f.get("choices") or [""])[w.get_selected()]
        w = Gtk.Entry(text=str(f.get("default") or ""))
        return w, w.get_text

    def run_form(self, t):
        """Run t, its inputs filled here first: or run it dry."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        read = {}
        for f in t.get("form") or []:
            box.append(Gtk.Label(label=f.get("label") or f["name"], xalign=0))
            w, read[f["name"]] = self.field_widget(f)
            box.append(w)
        alert(self.win, wording.say("tasks.run_heading", name=t.get("name") or ""), box,
              [("cancel", wording.say("common.cancel"), False), ("dry", wording.say("tasks.dry_run"), False),
               ("run", wording.say("tasks.run"), True)],
              lambda r: self.ask({"op": "run_task", "session": self.sid, "id": t["id"],
                                  "inputs": {k: fn() for k, fn in read.items()}, "dry": r == "dry"}))

    def add_trigger(self, t):
        """A trigger added to t: a schedule or a git push, and which of its payload's keys fills each field."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        kind, kinds = dropdown(KINDS, "schedule")
        box.append(kind)
        sched = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        how = Gtk.DropDown.new_from_strings([wording.say("tasks.every"), wording.say("tasks.at_a_time")])
        sched.append(how)
        every = Gtk.Box(spacing=6)
        amount = Gtk.SpinButton.new_with_range(1, MAX_EVERY // 60, 1)
        amount.set_value(1)
        unit = Gtk.DropDown.new_from_strings([wording.say("tasks.minutes"), wording.say("tasks.hours")])
        unit.set_selected(1)
        unit.connect("notify::selected", lambda *_: amount.set_range(*((1, MAX_EVERY // 60) if unit.get_selected()
                                                                       else (MIN_EVERY, MAX_EVERY))))
        every.append(amount)
        every.append(unit)
        sched.append(every)
        daily = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        daily.append(Gtk.Label(label=wording.say("tasks.times_in", zone=self.clock["zone"])
                               if self.clock and self.clock.get("zone") else wording.say("tasks.host_clock"), xalign=0))
        at = Gtk.Entry(placeholder_text="HH:MM", text="09:00", max_width_chars=6)
        daily.append(at)
        days_row, days = Gtk.Box(spacing=2), []
        for i, d in enumerate(DAY_NAMES):
            b = Gtk.ToggleButton(label=d, active=i < 5)
            days.append(b)
            days_row.append(b)
        daily.append(days_row)
        sched.append(daily)
        box.append(sched)
        push = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        repo = Gtk.Entry(placeholder_text=wording.say("tasks.repo"))
        branch = Gtk.Entry(placeholder_text=wording.say("lookup.branch"), text="main")
        push.append(Gtk.Label(label=wording.say("tasks.repo"), xalign=0))
        push.append(repo)
        push.append(Gtk.Label(label=wording.say("lookup.branch"), xalign=0))
        push.append(branch)
        box.append(push)
        fills_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(fills_box)
        fills = {}

        def shown(*_):
            k = kinds[kind.get_selected()]
            sched.set_visible(k == "schedule")
            push.set_visible(k == "git_push")
            every.set_visible(how.get_selected() == 0)
            daily.set_visible(how.get_selected() == 1)
            while (c := fills_box.get_first_child()) is not None:
                fills_box.remove(c)
            fills.clear()
            if t.get("form"):
                fills_box.append(Gtk.Label(label=wording.say("tasks.fills"), xalign=0, css_classes=["heading"]))
            keys = PAYLOAD[k]
            for f in t.get("form") or []:
                row = Gtk.Box(spacing=8)
                row.append(Gtk.Label(label=wording.say("tasks.fills_field", field=f.get("label") or f["name"]),
                                     xalign=0, hexpand=True))
                dd = Gtk.DropDown.new_from_strings([wording.say("tasks.nothing"), *keys])
                if f["name"] in keys:
                    dd.set_selected(keys.index(f["name"]) + 1)
                fills[f["name"]] = (dd, keys)
                row.append(dd)
                fills_box.append(row)
        for w in (kind, how):
            w.connect("notify::selected", shown)
        shown()

        def done(_r):
            k = kinds[kind.get_selected()]
            tr = {"kind": k, "fills": {n: keys[dd.get_selected() - 1] for n, (dd, keys) in fills.items()
                                       if dd.get_selected() > 0}}
            if k == "git_push":
                tr.update(repo=repo.get_text().strip(), branch=branch.get_text().strip() or "main")
            elif how.get_selected() == 0:
                tr["every"] = int(amount.get_value()) * (60 if unit.get_selected() == 1 else 1)
            else:
                tr.update(at=at.get_text().strip(), days=[i for i, b in enumerate(days) if b.get_active()])
            self.save(self.kept(t, triggers=[*(t.get("triggers") or []), tr]))
        return alert(self.win, wording.say("tasks.trigger_heading"), box,
                     [("cancel", wording.say("common.cancel"), False), ("save", wording.say("common.save"), True)],
                     done), kind

    def edit_form(self, t):
        """t's inputs changed: each a name, a type, its choices and its default."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        rows = []

        def add(f):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            line = Gtk.Box(spacing=6)
            name = Gtk.Entry(placeholder_text=wording.say("common.name"), text=f.get("name") or "",
                             hexpand=True)
            kind, kinds = dropdown(TYPES, f.get("type") or "text")
            line.append(name)
            line.append(kind)
            choices = Gtk.Entry(placeholder_text=wording.say("tasks.choices"), text=", ".join(f.get("choices") or []))
            default = Gtk.Entry(placeholder_text=wording.say("meta_settings.effort_default"), text="" if f.get("default") is None else str(f["default"]))
            item = (card, name, kind, kinds, choices, default)
            line.append(flat("list-remove-symbolic", wording.say("tasks.remove_input"),
                             lambda: (rows.remove(item), fields.remove(card))))
            card.append(line)
            card.append(choices)
            card.append(default)
            kind.connect("notify::selected", lambda *_: choices.set_visible(kinds[kind.get_selected()] == "choice"))
            choices.set_visible(kinds[kind.get_selected()] == "choice")
            rows.append(item)
            fields.append(card)
        fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(fields)
        for f in t.get("form") or []:
            add(f)
        more = Gtk.Button(label=wording.say("tasks.add_input"), halign=Gtk.Align.START)
        more.connect("clicked", lambda *_: add({}))
        box.append(more)

        def done(_r):
            form = []
            for _card, name, kind, kinds, choices, default in rows:
                f = {"name": name.get_text().strip(), "type": kinds[kind.get_selected()]}
                if f["type"] == "choice":
                    f["choices"] = [c.strip() for c in choices.get_text().split(",") if c.strip()]
                if default.get_text():
                    f["default"] = default.get_text()
                form.append(f)
            self.save(self.kept(t, form=form))
        alert(self.win, wording.say("tasks.inputs_heading"), box,
              [("cancel", wording.say("common.cancel"), False), ("save", wording.say("common.save"), True)], done)
