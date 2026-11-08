"""☰ → Ongoing Feedback (Baude's and Record's), a tab of the main area: all the feedback and where each stands, on the
left: the issues on Baude's repository (fixes.py reads them from GitHub), and the notes saved on this machine
(feedback_items) that aren't one of those: never filed, or filed where it can't be read now. On the right one's note,
what's been said on it and each round of its fix, with the images before and after; a note filed as an issue is shown
with it, its captures being only here. What can be done goes by where an issue stands: open, Fix here (a round starts on
this machine); ready for review, Merge or Discard, or ask for changes."""
import json
import os
import re
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

import feedback  # noqa: E402
import fixes  # noqa: E402
import pointers  # noqa: E402
from widgets import (  # noqa: E402
    MarkdownView, RefreshButton, discard, make_image_interactive, markdown_scroller, paned_grip, retime, say_time,
    time_tip,
)

POLL_S = 60  # (read again this often while it's shown)
POLL_WORKING_S = 15  # (and this often while a round's running)
LIST_WIDTH = 300


def status_pill(status):
    return Gtk.Label(label=fixes.LABELS[status], css_classes=["fix-status", status], valign=Gtk.Align.CENTER)


def dim(text, **kw):
    return Gtk.Label(label=text, xalign=0, css_classes=["dim-label", "caption"], **kw)


def said(text):
    """What someone wrote, as Markdown, its images as links: MarkdownView would fetch a web one (telling its host who's
    reading) and draw a file of this machine's, on the word of whoever wrote the issue."""
    return MarkdownView(re.sub(r"!(\[[^\]]*\]\()", r"\1", text))


def note_item(note):
    """A note saved on this machine (feedback_items) with no issue read for it, as an item of the list: never filed, filed
    (where GitHub can't say more now) or resolved (its resolution.md). Its number is its folder."""
    text, issue = "" if note["text"] == "(no text)" else note["text"], note["issue"]
    try:
        when = time.mktime(time.strptime(note["when"], "%Y-%m-%d %H:%M"))
    except ValueError:
        when = os.path.getmtime(note["folder"])
    return {"number": note["folder"], "title": feedback.issue_title(text, note["captures"]),
            "body": text.partition("\n")[2].strip(), "author": "", "url": (issue or {}).get("url") or "",
            "created": when, "updated": when, "pr": None, "thread": [], "rounds": 0, "working": None, "here": False,
            "error": None, "notes": [note],
            "status": fixes.RESOLVED if note["resolution"] is not None else fixes.FILED if issue else fixes.SAVED}


def with_notes(issues, notes, repo):
    """All the feedback, in the order it's listed: the issues, each with the notes here that were filed as it, and the
    other notes as items of their own."""
    items = [{**issue, "notes": []} for issue in issues]
    by_number = {item["number"]: item for item in items}
    for note in notes:
        filed = note["issue"]
        item = by_number.get(filed.get("number")) if filed and filed.get("repo", repo) == repo else None
        if item is not None:
            item["notes"].append(note)
        else:
            items.append(note_item(note))
    return sorted(items, key=fixes.sort_key)


def where_it_is(item):
    """What a row and a heading call it: its issue's number. None yet, nothing: its status says so."""
    number = item["number"]
    filed = next((n["issue"] for n in item["notes"] if n["issue"]), None)
    return f"#{number}" if isinstance(number, int) else f"#{filed['number']}" if filed and filed.get("number") \
        else ""


def web(win, url):
    """A web page opened as links are, in your browser."""
    Gtk.UriLauncher(uri=url).launch(win, None, None)


def open_github(win, listed):
    """A pull request or an issue the @ list or ⌘K has (lookup.GITHUB) opened: an issue on Baude's own repository is
    feedback, shown on this tab, with what's been done about it; anything else, its page on GitHub."""
    if listed["pr"] or listed["repo"] != fixes.issue_repo():
        return web(win, listed["url"])
    win.open_fixes().show_issue(listed["number"], listed["url"])


class FixesPage(Gtk.Box):
    """The tab. Everything's read off the main loop (refresh) while it's shown; what's done (run) too, then read again.
    win: its toasts, for what happened; claude: the claude a round runs (baude.claude_exe's)."""

    def __init__(self, win, claude=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.win, self.claude = win, claude
        self.repo, self.issues, self.items, self.can_write, self.selected, self.show_all = None, [], [], False, None, False
        self.loading, self.busy, self.poll, self.again = False, False, None, None
        self.wanted = None  # (an issue asked for, not read yet: its number, and its page on GitHub: show_issue)
        self.read_at, self.drawn, self.rows_drawn = 0.0, None, None
        split = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=LIST_WIDTH, shrink_start_child=False,
                                     shrink_end_child=False, resize_start_child=False, resize_end_child=True))
        split.set_start_child(self.make_list())  # (both there from the first: what's read comes into them)
        self.stack = Gtk.Stack(hexpand=True, vexpand=True)  # (the right: the one selected, or why there's nothing)
        self.stack.add_named(self.make_detail(), "main")
        self.status = Adw.StatusPage(icon_name="mail-send-symbolic")
        self.stack.add_named(self.status, "status")
        split.set_end_child(self.stack)
        self.append(split)
        self.connect("map", FixesPage._shown)
        self.connect("unmap", FixesPage._hidden)

    # ---- the list
    def make_list(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=240)
        head = Gtk.Box(spacing=6, margin_start=10, margin_end=8, margin_top=8, margin_bottom=8)
        self.ongoing_btn = Gtk.ToggleButton(label="Ongoing", active=True)
        self.all_btn = Gtk.ToggleButton(label="All", group=self.ongoing_btn)
        self.ongoing_btn.connect("toggled", lambda b: self.set_show_all(not b.get_active()))
        switch = Gtk.Box(css_classes=["linked"], hexpand=True, halign=Gtk.Align.START)
        switch.append(self.ongoing_btn)
        switch.append(self.all_btn)
        head.append(switch)
        self.send_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Send Feedback…", css_classes=["flat"])
        self.send_btn.connect("clicked", lambda *_: self.send_feedback())
        head.append(self.send_btn)
        self.refresh_btn = RefreshButton(tooltip_text="Read it again")
        self.refresh_btn.connect("clicked", lambda *_: self.refresh())
        head.append(self.refresh_btn)
        box.append(head)
        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["navigation-sidebar"])
        self.rows.connect("row-selected", lambda _l, row: row is not None and self.select(row.number))
        self.empty_note = Gtk.Label(label="Loading issues…", margin_top=24, css_classes=["dim-label"])
        self.rows.set_placeholder(self.empty_note)  # (till it's been read: then, "Nothing ongoing")
        box.append(Gtk.ScrolledWindow(child=self.rows, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.note = dim("", margin_start=12, margin_end=12, margin_top=6, margin_bottom=8, ellipsize=Pango.EllipsizeMode.END)
        box.append(self.note)
        return box

    def send_feedback(self):
        """The + : Send Feedback…, its note listed here as it's saved (a moment later too: filing it takes a little).
        Let go of as it closes: the window keeps its last dialog, which would keep this page."""
        dialog = self.win.open_feedback()

        def closed(*_):
            dialog.disconnect(handler)
            self.ran(None, None)
        handler = dialog.connect("closed", closed)

    def set_show_all(self, on):
        self.show_all = on
        if not on and (item := self.current()) is not None and item["status"] not in fixes.ONGOING:
            self.selected = None  # (asked for: what's finished goes)
        self.fill_list()

    def listed(self):
        """What's ongoing (or all of it), and the one selected, even if it's finished since: it stays what's shown, so
        nothing typed or pressed lands on another, until you move on."""
        return [i for i in self.items if self.show_all or i["status"] in fixes.ONGOING or i["number"] == self.selected]

    def fill_list(self):
        """The list as it is now (left alone if it looks the same: where it's scrolled to stays), what's selected kept;
        with nothing selected, the first."""
        listed = self.listed()
        looks = [(i["number"], i["title"], i["status"], i["updated"], where_it_is(i), sum(n["captures"] for n in i["notes"]))
                 for i in listed]
        if looks != self.rows_drawn:
            self.rows_drawn = looks
            while (row := self.rows.get_row_at_index(0)) is not None:  # (remove_all would take its placeholder too)
                self.rows.remove(row)
            for item in listed:
                self.rows.append(self.row(item))
        else:
            retime(self.rows)  # (only how long ago's moved on)
        at = next((n for n, i in enumerate(listed) if i["number"] == self.selected), 0)
        keep = self.rows.get_row_at_index(at)
        if keep is not None:
            self.rows.select_row(keep)
        else:
            self.select(None)

    @staticmethod
    def row(item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4, margin_bottom=4)
        box.append(Gtk.Label(label=item["title"], xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                             ellipsize=Pango.EllipsizeMode.END,
                             max_width_chars=1, hexpand=True))
        meta = Gtk.Box(spacing=8)
        meta.append(status_pill(item["status"]))
        captures = sum(n["captures"] for n in item["notes"])
        more = f" · {captures} capture{'s' if captures != 1 else ''}" if captures else ""
        meta.append(say_time(dim("", ellipsize=Pango.EllipsizeMode.END, hexpand=True), item["updated"],
                             "".join(f"{w} · " for w in [where_it_is(item)] if w), more))
        box.append(meta)
        row = Gtk.ListBoxRow(child=box)
        row.number = item["number"]
        return row

    def show_issue(self, number, url):
        """An issue named elsewhere selected, as soon as it's read (finished, it's listed while it's selected); if it
        turns out not to be here, its page on GitHub, what's selected left as it was."""
        if any(i["number"] == number for i in self.items):
            self.select(number)
            return self.fill_list()
        self.wanted = (number, url)
        return self.refresh()

    # ---- one of them
    def make_detail(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=420, hexpand=True)
        self.clamp = Adw.Clamp(maximum_size=980, tightening_threshold=900)
        self.scroller = markdown_scroller(self.clamp, vexpand=True)
        box.append(self.scroller)
        self.bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=14, margin_end=14,
                           margin_top=8, margin_bottom=10, css_classes=["fix-bar"])
        self.hint = dim("", wrap=True)
        self.bar.append(self.hint)
        line = Gtk.Box(spacing=8)
        self.reply_entry = Gtk.Entry(hexpand=True, placeholder_text="Ask for changes…")
        self.reply_entry.connect("activate", lambda *_: self.send_reply())
        self.reply_entry.connect("changed", lambda *_: self.sync_bar())
        self.reply_btn = Gtk.Button(label="Send", tooltip_text="Say it on the issue, and fix it again")
        self.reply_btn.connect("clicked", lambda *_: self.send_reply())
        self.gap = Gtk.Box(hexpand=True)
        self.spinner = Adw.Spinner(visible=False)
        self.stop_btn = Gtk.Button(label="Stop", tooltip_text="Stop the fix running on this machine")
        self.stop_btn.connect("clicked", lambda *_: self.run(fixes.stop, self.current()["number"]))
        self.reopen_btn = Gtk.Button(label="Reopen", tooltip_text="Open the issue again")
        self.reopen_btn.connect("clicked", lambda *_: self.run(fixes.reopen, self.repo, self.current()))
        self.discard_btn = Gtk.Button(label="Discard", css_classes=["destructive-action"],
                                      tooltip_text="Discard the fix: its PR closed, its branch deleted, the feedback still open")
        self.discard_btn.connect("clicked", lambda *_: self.discard())
        self.merge_btn = Gtk.Button(label="Merge", css_classes=["suggested-action"])
        self.merge_btn.connect("clicked", lambda *_: self.merge())
        self.fix_btn = Gtk.Button(label="Fix here", css_classes=["suggested-action"])
        self.fix_btn.connect("clicked", lambda *_: self.run(fixes.start_fix, self.current()["number"], None, self.claude))
        for w in (self.reply_entry, self.reply_btn, self.gap, self.spinner, self.stop_btn, self.reopen_btn,
                  self.discard_btn, self.merge_btn, self.fix_btn):
            line.append(w)
        self.bar.append(line)
        self.bar_rule = Gtk.Separator()
        box.append(self.bar_rule)
        box.append(self.bar)
        return box

    def current(self):
        return next((i for i in self.items if i["number"] == self.selected), None)

    def select(self, number):
        if number != self.selected:
            self.reply_entry.set_text("")  # (what was typed was about the other one)
        self.selected = number
        self.draw_detail()
        self.sync_bar()

    def draw_detail(self):
        """The one selected drawn, if it's changed since it was (what's read every minute mostly hasn't: where it's
        scrolled to stays)."""
        item = self.current()
        drawn = json.dumps(item, sort_keys=True, default=str)
        if drawn == self.drawn:
            retime(self.clamp)
            return
        self.drawn = drawn
        old = self.clamp.get_child()
        self.clamp.set_child(self.detail(item) if item else None)
        if old is not None:
            discard(old)

    def detail(self, item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_start=18, margin_end=18, margin_top=14,
                      margin_bottom=18)
        box.append(Gtk.Label(label=item["title"], xalign=0, wrap=True, selectable=True, css_classes=["title-2"]))
        meta = Gtk.Box(spacing=10)
        meta.append(status_pill(item["status"]))
        said_by = [f"from {item['author']}"] if item["author"] else []
        meta.append(say_time(dim("", ellipsize=Pango.EllipsizeMode.END, hexpand=True), item["created"],
                             "".join(f"{w} · " for w in [where_it_is(item), *said_by] if w)))
        if item["url"]:
            meta.append(self.link("Issue", item["url"]))
        if item["pr"]:
            meta.append(self.link(f"PR #{item['pr']['number']}", item["pr"]["url"]))
        box.append(meta)
        if item["body"]:
            box.append(said(item["body"]))
        for entry in item["thread"]:
            if (entry["mark"] or {}).get("state") != "working" and (entry["mark"] or entry["text"]):
                box.append(self.entry(entry))  # (a round running is said by the bar, and only while it is)
        for note in item["notes"]:
            if note["resolution"] is not None or note["captures"]:  # (nothing else to show of a note: no empty card)
                box.append(self.note_card(note))
        return box

    def note_card(self, note):
        """A note kept on this machine: how it was resolved (its resolution.md) and its captures (gh can't upload them:
        they're only here)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["fix-round"])
        if note["resolution"] is not None:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(note["resolved_at"]))
            box.append(Gtk.Label(label=f"Resolved {when}", xalign=0, css_classes=["heading"]))
            box.append(said(note["resolution"] or "(no note)"))
        names = [os.path.join(note["folder"], f"capture-{n}{m}.png") for n in range(1, note["captures"] + 1)
                 for m in (("-marked",) if os.path.exists(os.path.join(note["folder"], f"capture-{n}-marked.png")) else ("",))]
        for at in range(0, len(names), 2):  # (two to a line, as a fix's before and after are)
            line = Gtk.Box(spacing=12, homogeneous=True)
            for path in names[at:at + 2]:
                line.append(self.image_slot(path, self.read_file))
            if len(names[at:at + 2]) == 1:
                line.append(Gtk.Box())
            box.append(line)
        return box

    def image_slot(self, path, read):
        """A place for an image, shown once read(path, slot) has it off the main loop."""
        slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, height_request=120)
        slot.append(Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, vexpand=True))
        threading.Thread(target=read, args=(path, slot), daemon=True).start()
        return slot

    def link(self, label, url):
        btn = pointers.hand(Gtk.Button(label=label, tooltip_text=url, css_classes=["flat"], valign=Gtk.Align.CENTER))
        btn.connect("clicked", lambda *_: Gtk.UriLauncher(uri=url).launch(self.win, None, None))
        return btn

    def entry(self, entry):
        """A round of the fix, or something someone said."""
        mark = entry["mark"]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        if mark:
            box.add_css_class("fix-round")
            box.add_css_class(mark["state"])
        head = Gtk.Box(spacing=8)
        title = fixes.HEADINGS[mark["state"]].capitalize() if mark else entry["author"]
        head.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"], ellipsize=Pango.EllipsizeMode.END))
        place = f" · {mark['machine']}" if mark and mark.get("machine") else ""
        head.append(say_time(dim(""), entry["at"], after=place))
        box.append(head)
        if entry["text"]:
            box.append(said(entry["text"]))
        for shot in (mark or {}).get("shots") or ():
            box.append(self.shot(shot))
        return box

    def shot(self, shot):
        """One thing shown, before and after, side by side at whatever width the tab gives them, each read off the main
        loop. Click one to see it large."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if shot["label"]:
            box.append(Gtk.Label(label=shot["label"], xalign=0, wrap=True))
        sides = Gtk.Box(spacing=12, homogeneous=True)
        for side, path in (("Before", shot["before"]), ("After", shot["after"])):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            column.append(dim(side))
            if path is None:
                column.append(dim("Not there before this fix" if side == "Before" else "None", wrap=True))
            else:
                column.append(self.image_slot(path, self.read_shot))
            sides.append(column)
        box.append(sides)
        return box

    @staticmethod
    def read_shot(path, slot):
        data = fixes.shot_bytes(path)
        GLib.idle_add(FixesPage.show_shot, path, slot, data)

    @staticmethod
    def read_file(path, slot):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            data = None
        GLib.idle_add(FixesPage.show_shot, path, slot, data)

    @staticmethod
    def show_shot(path, slot, data):
        if slot.get_root() is None:
            return False  # (redrawn or closed since)
        slot.remove(slot.get_first_child())
        slot.set_size_request(-1, -1)
        if data:
            pic = Gtk.Picture(paintable=Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)), can_shrink=True,
                              content_fit=Gtk.ContentFit.CONTAIN, css_classes=["fix-shot"])
            make_image_interactive(pic, data, path=path if os.path.isabs(path) else None, name=path)
            slot.append(pic)
        else:
            slot.append(dim("This image can't be read", wrap=True))
        return False

    # ---- what can be done with it
    def sync_bar(self):
        item = self.current()
        status = item["status"] if item else None
        writes = self.can_write and not self.busy
        self.spinner.set_visible(self.busy)
        replies = status == fixes.READY and self.can_write and fixes.ours(item)
        self.reply_entry.set_visible(replies)
        self.reply_btn.set_visible(replies)
        self.gap.set_visible(not replies)
        self.reply_entry.set_sensitive(writes)
        self.reply_btn.set_sensitive(writes and bool(self.reply_entry.get_text().strip()))
        self.fix_btn.set_visible(self.can_write and status == fixes.OPEN)
        self.merge_btn.set_visible(self.can_write and status == fixes.READY)
        self.discard_btn.set_visible(self.can_write and status == fixes.READY)
        self.reopen_btn.set_visible(self.can_write and status == fixes.REJECTED)
        self.stop_btn.set_visible(self.can_write and status == fixes.WORKING and item["here"])
        for btn in (self.fix_btn, self.merge_btn, self.discard_btn, self.reopen_btn, self.stop_btn):
            btn.set_sensitive(writes)
        if status == fixes.READY:
            self.merge_btn.set_tooltip_text(f"Merge PR #{item['pr']['number']}: every running Baude and Record updates to it")
        elif status == fixes.OPEN:
            self.fix_btn.set_tooltip_text(f"Fix it on this machine, on branch fix/{item['number']}")
        self.hint.set_label(self.hint_for(item) if item else "")
        self.hint.set_visible(bool(self.hint.get_label()))
        shown = self.hint.get_visible() or self.busy or replies or any(
            b.get_visible() for b in (self.fix_btn, self.merge_btn, self.discard_btn, self.reopen_btn, self.stop_btn))
        self.bar.set_visible(shown)  # (nothing to say or do, as on a merged one: no empty bar)
        self.bar_rule.set_visible(shown)

    def hint_for(self, item):
        status, working = item["status"], item["working"]
        if status == fixes.FILED:
            return "Filed as an issue, but it can't be read from GitHub here just now, so this is only the note saved here."
        if status == fixes.WORKING:
            if working:
                return f"#{item['number']} is running on {working['machine'] or 'another machine'}"
            return f"PR #{item['pr']['number']} is a draft: in progress."
        if not self.can_write:
            return f"Only people with write access to {self.repo} can fix or merge." \
                if status in (fixes.OPEN, fixes.READY) else ""
        if item["error"] and status in (fixes.OPEN, fixes.READY):
            return f"The last fix here didn't go ahead: {item['error']}"
        return ""

    def merge(self):
        item = self.current()
        self.confirm(f"Merge the fix for #{item['number']}?",
                     f"PR #{item['pr']['number']} goes into main, and every running Baude and Record updates itself "
                     "from main.", "Merge", False, lambda: self.run(fixes.merge, self.repo, item))

    def discard(self):
        item = self.current()
        self.confirm(f"Discard the fix for #{item['number']}?",
                     f"PR #{item['pr']['number']} is closed and its branch deleted. The feedback stays open.",
                     "Discard", True, lambda: self.run(fixes.discard, self.repo, item))

    def send_reply(self):
        text, item = self.reply_entry.get_text().strip(), self.current()
        if text and item and self.can_write and not self.busy and item["status"] == fixes.READY and fixes.ours(item):
            self.run(fixes.reply, self.repo, item, text, self.claude, done=lambda: self.reply_entry.set_text(""))

    def confirm(self, heading, body, verb, destructive, then):
        dlg = Adw.AlertDialog(heading=heading, body=body, default_response="cancel", close_response="cancel")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("go", verb)
        dlg.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE if destructive
                                    else Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _d, r: r == "go" and then())
        dlg.present(self.win)
        return dlg

    def run(self, fn, *args, done=None):
        """Something done, off the main loop; then what's on GitHub read again, a moment later too (a round's first
        comment takes a few seconds). done: on the main loop, if it worked."""
        self.busy = True
        self.sync_bar()

        def work():
            try:
                fn(*args)
                GLib.idle_add(self.ran, None, done)
            except Exception as e:  # noqa: BLE001 (whatever it was: said, and the buttons work again)
                GLib.idle_add(self.ran, str(e) or type(e).__name__, None)
        threading.Thread(target=work, daemon=True).start()

    def ran(self, error, done):
        self.busy = False
        if self.get_root() is None:
            return False  # (closed since)
        if error:
            self.win.toasts.add_toast(Adw.Toast(title=(error.splitlines() or [""])[0][:160], timeout=10))
        elif done is not None:
            done()
        self.sync_bar()
        self.refresh()
        if self.again is None:
            self.again = GLib.timeout_add_seconds(6, self.read_again)
        return False

    def read_again(self):
        self.again = None
        self.refresh()
        return False

    # ---- a restart
    def state(self):
        """What's selected and what's been typed, as a restart keeps it."""
        return {"selected": self.selected, "all": self.show_all, "reply": self.reply_entry.get_text()}

    def restore(self, state):
        self.selected = state.get("selected")
        self.all_btn.set_active(bool(state.get("all")))
        self.reply_entry.set_text(state.get("reply") or "")

    # ---- reading it
    @staticmethod
    def _shown(page):
        page.show_last_read()
        page.refresh()
        page.poll = GLib.timeout_add_seconds(POLL_WORKING_S, page.tick)

    @staticmethod
    def _hidden(page):
        if page.poll is not None:
            GLib.source_remove(page.poll)
            page.poll = None
        if page.again is not None:
            GLib.source_remove(page.again)
            page.again = None

    def tick(self):
        working = any(i["status"] == fixes.WORKING for i in self.items)
        if working or time.monotonic() - self.read_at >= POLL_S:
            self.refresh()
        return True

    def show_last_read(self):
        """Opened, before anything's been read: what was read last time and the notes saved here, as the page reads it
        again, so what's in it comes at once; with nothing kept, the list says it's loading."""
        if self.read_at or self.items:
            return
        last = fixes.last_list() or {}
        self.repo, self.issues, self.can_write = last.get("repo"), last.get("items", []), bool(last.get("can_write"))
        self.items = with_notes(self.issues, feedback.feedback_items(), self.repo)
        self.fill_list()
        if last.get("read"):
            say_time(self.note, last["read"], f"{self.repo} · read ")
        self.draw_detail()
        self.sync_bar()
        GLib.idle_add(self.count_in_tab)  # (it's shown before its tab's told which it is)

    def refresh(self):
        if self.loading or self.get_root() is None:
            return
        self.loading = True
        self.refresh_btn.set_loading(True)
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        repo, data, error = None, None, None
        try:
            repo = feedback.issues_repo()
            if repo is not None:
                data = fixes.load(repo)
                fixes.save_list(repo, data)  # (for the next time it opens: show_last_read)
        except Exception as e:  # noqa: BLE001 (whatever it was: said, and read again next time)
            error = str(e) or type(e).__name__
        GLib.idle_add(self.was_read, repo, data, feedback.feedback_items(), error)

    def was_read(self, repo, data, notes, error):
        """What was read shown: with GitHub out of reach, the last issues read, or none if it isn't connected at all, and
        the notes saved here in any case."""
        self.loading = False
        if self.get_root() is None:
            return False  # (closed since)
        self.refresh_btn.set_loading(False)
        self.read_at = time.monotonic()
        time_tip(self.note, None)  # (read now: it says the clock's time)
        self.empty_note.set_label("Nothing ongoing")
        self.repo = repo
        if data is not None:
            self.issues, self.can_write = data["items"], data["can_write"]
        elif repo is None:
            self.issues, self.can_write = [], False
        self.items = with_notes(self.issues, notes, repo)
        if self.wanted is not None:
            (number, url), self.wanted = self.wanted, None
            if any(i["number"] == number for i in self.items):
                self.select(number)
            else:
                web(self.win, url)
        if not self.items and data is None:
            self.status.set_title("GitHub isn't connected" if repo is None else "Couldn't read the feedback")
            self.status.set_description("Feedback lives on GitHub. Sign the gh CLI in (gh auth login) on a checkout of "
                                        "Baude from GitHub, and it shows here." if repo is None else error or "")
            self.stack.set_visible_child_name("status")
            self.fill_list()
            self.note.set_label("")
            self.sync_bar()
            return False
        self.fill_list()
        self.note.set_label("GitHub isn't connected: only what's saved here" if repo is None else
                            f"Couldn't read it just now: {error.splitlines()[0]}" if error else
                            f"{repo} · read {time.strftime('%H:%M')}")
        self.note.set_tooltip_text(error)
        self.stack.set_visible_child_name("main")
        self.draw_detail()
        self.sync_bar()
        self.count_in_tab()
        return False

    def count_in_tab(self):
        """The tab says how many are waiting for you."""
        waiting = sum(i["status"] == fixes.READY for i in self.items)
        tabs = getattr(self.win, "tabs", None)
        if tabs is not None and getattr(self, "tab_key", None) is not None:
            tabs.retitle(self.tab_key, f"Feedback ({waiting})" if waiting else "Feedback")
