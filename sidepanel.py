"""sidepanel — the side panel (SidePanel) and its Signal page (SignalPage).
"""
import html
import json
import os
import shutil
import weakref
import time

from baudelib import providers  # noqa: E402  (every app's model names)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from lookup import REF_KINDS, ref_date

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

import meta  # noqa: E402  (Meta, the helper in the side panel: Record's too; see meta.py)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
import refkinds  # noqa: E402
import codespans  # noqa: E402
import composerview  # noqa: E402
import panels  # noqa: E402
import chatfind  # noqa: E402
from chatparts import fmt_tokens  # noqa: E402,F401
from widgets import Spinner  # noqa: E402,F401
from widgets import (  # noqa: E402,F401
    discard, empty_label, globe_icon, load_settings, pretty_path, save_settings, themed_icon, walk,
)
from markdownview import CodeBlock, MarkdownView, sel_label  # noqa: E402,F401
from panels import natural_height  # noqa: E402,F401
from pickers import TreeList  # noqa: E402,F401
import botmemory  # noqa: E402  (a Bot's memory, the side panel's Memory page: beside this file)
import browserpane  # noqa: E402  (a Bot's browser, the side panel's Browser page: beside this file)
import routinespage  # noqa: E402  (a Bot's routines, the side panel's Routines page: beside this file)
from codeview import FilesPage  # noqa: E402,F401
from gitview import GitPage  # noqa: E402,F401

from signalrefs import SKILLS, SUBAGENTS, TOOL_DOCS_PAGE  # noqa: E402
from signalcode import lsp_commands  # noqa: E402
from toolcards import (  # noqa: E402
    ArtifactChip, ProcessRow, StatusSlot, ToolCard, paint_command, read_tail, unpaint_command,
)
from sidequestion import SideQuestion  # noqa: E402
from baudemeta import BaudeMeta  # noqa: E402
from refpopovers import (  # noqa: E402
    kube_running_rows, name_markup, ref_busy, ref_kind_label, ref_known_text, ref_location_buttons, ref_run_names,
    ref_status_text, ref_status_tip,
)


class SidePanel(Gtk.Box):
    """Right-hand pane: Meta (BaudeMeta), subagents (each with its own transcript) and background processes
    (status, live output tail, stop)."""
    live = weakref.WeakSet()  # (every window's, while it's there: their commands' colours follow the theme)
    styled = False

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("side-panel")
        self.win, self.agent_rows, self.agent_boxes, self.proc_rows = win, {}, {}, {}
        self.stack = Adw.ViewStack(vexpand=True)  # without this the pages get 0px of height
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.NARROW,
                                    margin_top=6, margin_bottom=6, halign=Gtk.Align.CENTER)
        self.append(switcher)  # (no rule under it: the frame has none: theming.CSS)
        self.append(self.stack)

        # Meta, Baude's helper: first, its spanner
        self.meta = BaudeMeta(win)
        page = self.stack.add_titled(self.meta, "meta", meta.NAME)
        page.set_icon_name("baude-meta-symbolic")
        # a Bot's memory (botmemory.BotMemory): there while the open chat's a Bot
        self.memory = botmemory.BotMemory(win)
        self.memory_page = self.stack.add_titled(self.memory, "memory", wording.say("bot_memory.title"))
        self.memory_page.set_icon_name("document-edit-symbolic")
        self.memory_page.set_visible(False)
        # a Bot's browser (browserpane.BrowserPane): its own Emission, watched and driven, while the open chat's a Bot
        self.browser = browserpane.BrowserPane(win)
        self.browser_page = self.stack.add_titled(self.browser, "browser", browserpane.PAGE)
        self.browser_page.set_icon_name("web-browser-symbolic")
        self.browser_page.set_visible(False)
        # a Bot's routines (routinespage.RoutinesPage): work it's given on a schedule, while the open chat's a Bot
        self.routines = routinespage.RoutinesPage(win)
        self.routines_page = self.stack.add_titled(self.routines, "routines", routinespage.PAGE)
        self.routines_page.set_icon_name("alarm-symbolic")
        self.routines_page.set_visible(False)

        # subagents: list -> detail, a subagent's opening out of its row (its title the header, the row the square its
        # chat's in) and closing back into it
        self.agent_expand = panels.ExpandStack(vexpand=True)
        self.agent_stack = self.agent_expand.stack
        self.agent_list = TreeList(line_x=8, selection_mode=Gtk.SelectionMode.NONE)
        self.agent_list.add_css_class("boxed-list")
        self.agent_list.set_placeholder(self._empty(wording.say("side_panel.no_subagents")))
        self.agent_list.connect("row-activated", lambda _l, row: self.open_agent(row.tool_use_id))
        self.agent_scroll = self._scrolled(self.agent_list)
        # (as wide as a subagent's square, edge to edge with it: opening, its row grows only down, never wider)
        self.agent_list.set_margin_start(0)
        self.agent_list.set_margin_end(widgets.INSIDE_GAP)
        self.agent_stack.add_named(self.agent_scroll, "list")
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # (each part where its row has it across: the status, the title, the › and back on the same x, so a row opening
        # into this only goes up)
        top = Gtk.Box(spacing=6, margin_start=15, margin_end=12, margin_top=6, margin_bottom=6)
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=wording.say("side_panel.all_subagents"),
                          valign=Gtk.Align.CENTER)
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self.close_agent())
        self.agent_back = back
        # its row, as its header: its status, its title, and under that what it's done (the row's line), and back at
        # the end, where the row's › is; so as it opens the row goes up to be this, each part where it is in both
        self.detail_status = StatusSlot()
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER,
                         margin_start=6)
        self.detail_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.detail_title.add_css_class("heading")
        self.detail_info = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.detail_info.add_css_class("dim-label")
        self.detail_info.add_css_class("caption")
        titles.append(self.detail_title)
        titles.append(self.detail_info)
        top.append(self.detail_status)
        top.append(titles)
        top.append(back)
        detail.append(top)
        # its chat set into the panel as the main chat is into the window (the inside: widgets.inside), the back
        # button, title and caption above it on the panel's own background. No gap of its own on the main chat's
        # side: the main chat's is the one between them, as thin as the window's edges are
        # (and ⌘F / Ctrl+F in the subagent shown, its find bar inside it: its box, the holder's child)
        self.detail_holder = natural_height(Gtk.ScrolledWindow(  # (each subagent's code unsquashed)
            vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.agent_find = chatfind.ChatFind(
            lambda: getattr(self.detail_holder.get_child(), "get_child", lambda: None)(), self.detail_holder)
        self.detail_inside = widgets.inside(self.agent_find.wrap(), start=False)
        self.detail_inside.set_margin_top(6)
        detail.append(self.detail_inside)
        self.agent_stack.add_named(detail, "detail")
        page = self.stack.add_titled(self.agent_expand, "agents", wording.say("side_panel.subagents"))
        page.set_icon_name("system-users-symbolic")

        # background processes: rows as wide and tall as subagents', each opening under its line or filling the
        # page, out of its row, as a side question's does (list -> detail: ExpandStack)
        self.proc_expand = panels.ExpandStack(vexpand=True)
        self.proc_stack = self.proc_expand.stack
        self.proc_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.proc_list.add_css_class("boxed-list")
        self.proc_list.set_placeholder(self._empty(wording.say("side_panel.no_processes")))
        self.proc_scroll = self._scrolled(self.proc_list)
        self.proc_list.set_margin_start(0)  # (edge to edge with a process's square, as the subagents' list is)
        self.proc_list.set_margin_end(widgets.INSIDE_GAP)
        self.proc_stack.add_named(self.proc_scroll, "list")
        proc_detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # its row, as its header: its status, what it is and under that how it stands, stop while it runs, and back
        # at the end, where the row's square is, the row's ⌄ going out
        top = Gtk.Box(spacing=6, margin_start=15, margin_end=15 + widgets.INSIDE_GAP, margin_top=6, margin_bottom=6)
        self.proc_status = StatusSlot()
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER,
                         margin_start=6)
        self.proc_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.proc_info = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.proc_info.add_css_class("dim-label")
        self.proc_info.add_css_class("caption")
        titles.append(self.proc_title)
        titles.append(self.proc_info)
        self.proc_stop = Gtk.Button(icon_name="media-playback-stop-symbolic",
                                    tooltip_text=wording.say("side_panel.stop_process"),
                                    valign=Gtk.Align.CENTER)
        self.proc_stop.add_css_class("flat")
        self.proc_stop.connect("clicked", lambda *_: self.proc_shown and self.win.stop_task(self.proc_shown.task_id))
        self.proc_back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=wording.say("side_panel.all_processes"),
                                    valign=Gtk.Align.CENTER)
        self.proc_back.add_css_class("flat")
        self.proc_back.add_css_class("side-question-full")  # (the size of the button it's in place of)
        self.proc_back.connect("clicked", lambda *_: self.close_process())
        for w in (self.proc_status, titles, self.proc_stop, self.proc_back):
            top.append(w)
        proc_detail.append(top)
        self.proc_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, margin_top=6)
        proc_detail.append(self.proc_holder)
        self.proc_stack.add_named(proc_detail, "detail")
        self.proc_shown = None
        page = self.stack.add_titled(self.proc_expand, "processes", wording.say("side_panel.processes"))
        page.set_icon_name("utilities-terminal-symbolic")
        SidePanel.live.add(self)
        if not SidePanel.styled:  # (one hook for every window's panel: none kept alive by it once closed)
            SidePanel.styled = True
            Adw.StyleManager.get_default().connect("notify::dark", lambda *_: [
                panel.repaint_commands() for panel in list(SidePanel.live)])

        # artifacts published (or linked) in this session: rows as wide and tall as subagents'
        self.artifact_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.artifact_list.add_css_class("boxed-list")
        self.artifact_list.set_placeholder(self._empty(wording.say("side_panel.no_artifacts")))
        self.artifact_rows = {}
        # /btw side questions, newest first: rows titled with their questions, each opening under its line or
        # filling the page, out of its row as a subagent's does (list -> detail: ExpandStack)
        self.btw_expand = panels.ExpandStack(vexpand=True)
        self.btw_stack = self.btw_expand.stack
        self.btw_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.btw_list.add_css_class("boxed-list")
        # (none asked: no placeholder, and the list hidden: empty, its outline would draw a line under the box)
        self.btw_list.set_visible(False)
        btw_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # (over them, a pill of its own: the box a new one's asked in; Enter and its send button off while it's
        # empty)
        self.btw_ask = composerview.Composer(send_tip=wording.say("side_questions.ask_tip", key=composerview.SEND_KEY),
                                        follow_text=True)
        self.btw_ask.add_css_class("side-question-ask")  # (Search sessions' fill: theming.CSS)
        # (at least as tall as the other pages' empty lists, whatever the theme and text size: over one, unseen, the
        # box filling it; taller as more's typed. Its text fills the box, so a click anywhere in it is in the text)
        self.btw_ask.input.get_parent().set_vexpand(True)
        ghost = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, opacity=0, can_target=False,
                            accessible_role=Gtk.AccessibleRole.PRESENTATION)  # (not read out)
        ghost.add_css_class("boxed-list")
        ghost.set_placeholder(self._empty(""))
        ask_holder = Gtk.Overlay(child=ghost, vexpand=False)  # (not the page's to grow: the list's under it)
        ask_holder.add_overlay(self.btw_ask)
        ask_holder.set_measure_overlay(self.btw_ask, True)
        ask_holder.set_margin_start(0)  # (edge to edge with the list under it)
        ask_holder.set_margin_end(widgets.INSIDE_GAP)
        ask_holder.set_margin_top(10)
        self.btw_ask.gate("empty", True)
        self.btw_ask.input.get_buffer().connect("changed", lambda *_: self.btw_ask.gate(
            "empty", not self.btw_ask.text().strip()))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._ask_key)
        self.btw_ask.input.add_controller(keys)
        self.btw_ask.send.connect("clicked", lambda *_: self.ask_typed())
        btw_page.append(ask_holder)
        self.btw_scroll = self._scrolled(self.btw_list)
        # (as wide as a side question's square, edge to edge with it, as the subagents' list is with theirs)
        self.btw_list.set_margin_start(0)
        self.btw_list.set_margin_end(widgets.INSIDE_GAP)
        btw_page.append(self.btw_scroll)
        self.btw_stack.add_named(btw_page, "list")
        btw_detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # its row, as its header: each part where the row has it across (the list's edge, 1px, counted in), so a row
        # opening into this only goes up; back at the end, where the row's square is, the row's ⌄ going out
        top = Gtk.Box(spacing=12, margin_start=13, margin_end=7 + widgets.INSIDE_GAP, margin_top=8, margin_bottom=8)
        self.btw_status = StatusSlot()
        self.btw_status.set_visible(False)
        self.btw_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)  # (not bold: its row's)
        self.btw_back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=wording.say("side_questions.all"),
                                   valign=Gtk.Align.CENTER)
        self.btw_back.add_css_class("flat")
        self.btw_back.add_css_class("side-question-full")  # (the size of the button it's in place of)
        self.btw_back.connect("clicked", lambda *_: self.close_side_question())
        top.append(self.btw_status)
        top.append(self.btw_title)
        top.append(self.btw_back)
        btw_detail.append(top)
        # (the shown one's thread set into the panel as a subagent's is: detail_holder)
        self.btw_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, margin_top=6)
        btw_detail.append(self.btw_holder)
        self.btw_stack.add_named(btw_detail, "detail")
        self.btw_shown = None
        page = self.stack.add_titled(self.btw_expand, "btw", wording.say("side_panel.side_questions"))
        page.set_icon_name("dialog-question-symbolic")
        artifact_scroll = self._scrolled(self.artifact_list)
        self.artifact_list.set_margin_start(0)  # (edge to edge, as the subagents' list is)
        self.artifact_list.set_margin_end(widgets.INSIDE_GAP)
        page = self.stack.add_titled(artifact_scroll, "artifacts", wording.say("side_panel.artifacts"))
        page.set_icon_name(globe_icon())
        # (code shown in Baude is a tab of its own in the main area: BaudeWindow.show_code)
        # folders: a tree, opened down to the one clicked (not the file manager)
        self.files_page = FilesPage(win)
        page = self.stack.add_titled(self.files_page, "files", wording.say("lookup.files"))
        page.set_icon_name("folder-symbolic")
        # git: a branch's history, its upstream and parent, its commits' changes (see "git view")
        self.git_page = GitPage(win)
        page = self.stack.add_titled(self.git_page, "git", wording.say("git.title"))
        page.set_icon_name(themed_icon("vcs-branch-symbolic", "media-playlist-shuffle-symbolic"))
        self.stack.connect("notify::visible-child-name", lambda *_: (
            self.stack.get_visible_child_name() == "files" and self.files_page.ensure(),
            self.stack.get_visible_child_name() == "git" and self.git_page.ensure()))
        # Signal's names
        self.signal_page = SignalPage(win)
        page = self.stack.add_titled(self.signal_page, "refs", wording.say("side_panel.signal"))
        page.set_icon_name(themed_icon("insert-link-symbolic", "emblem-symbolic-link-symbolic"))
        self.poll_source = GLib.timeout_add(1000, self.poll_output)

    @staticmethod
    def _empty(text):
        lbl = Gtk.Label(label=text, margin_top=24, margin_bottom=24)
        lbl.add_css_class("dim-label")
        return lbl

    @staticmethod
    def _scrolled(child):
        child.set_margin_start(10)
        child.set_margin_end(10)
        child.set_margin_top(10)
        child.set_margin_bottom(10)
        child.set_valign(Gtk.Align.START)
        # (at its natural height: a page's chat, its code, isn't squashed as it outgrows the panel: natural_height)
        return natural_height(Gtk.ScrolledWindow(child=child, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))

    def show_page(self, name):
        self.stack.set_visible_child_name(name)

    def add_side_question(self, sq):
        """sq at the top, open under its line."""
        if self.btw_shown is not None:  # (back to the list, where it is)
            self.close_side_question()
        self.btw_list.prepend(sq)
        self.btw_list.set_visible(True)
        sq.set_expanded(True)

    def _ask_key(self, _c, keyval, _code, state):
        """Ctrl+Enter (⌘Enter) asks what's typed in the box over the list (nothing, while it's empty); Enter is a
        new line: as in a side question's own box."""
        if composerview.leave_wrapped(self.btw_ask.input.get_buffer(), keyval, state):
            return True
        if composerview.tab_key(self.btw_ask.markdown, keyval, state):
            return True  # (a list item in or out a level, as on a Record page)
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            markdown = self.btw_ask.markdown
            if not composerview.sends(state):
                return composerview.newline_key(markdown, state)  # (a list's next item; Shift: a plain line break)
            self.ask_typed()
            return True
        return False

    def ask_typed(self):
        """The question typed over the list: a side question of its own, at the top, open under its line; the box
        emptied for the next. Kept there if it couldn't be asked (said in the window's note)."""
        question = self.btw_ask.text().strip()
        if not question:
            return None
        sq = SideQuestion(self.win)
        self.add_side_question(sq)
        if not sq.start(question):
            self.drop_side_question(sq)
            return None
        self.btw_ask.input.get_buffer().set_text("")
        return sq

    def side_questions(self):
        return [w for w in walk(self.btw_list) if isinstance(w, SideQuestion)]

    def open_side_question(self, sq):
        """sq's thread filling the page, opening out of its row (its thread under it, if it's open there, the square
        it opens from), back (close_side_question) into it."""
        self.btw_expand.finish()  # (one opening or closing: at its end, before the page's changed under it)
        if self.btw_shown is not None:
            self.btw_shown.put_inside_row()
        self.btw_shown = sq
        self.side_question_titled(sq)
        self.side_question_status(sq)
        if self.btw_stack.get_visible_child_name() == "list" and self.stack.get_visible_child_name() == "btw":
            self.btw_expand.open("detail", sq, sq.inside, **self.btw_parts(sq), within=self.btw_scroll,
                                 across=self.btw_list)
        else:
            self.btw_stack.set_visible_child_name("detail")
        self.btw_holder.append(sq.take_inside())  # (after: the row's picture taken with it in it)
        self.show_page("btw")

    def btw_parts(self, sq):
        """How a side question's row turns into its page's header (ExpandStack): its title and status into the
        header's, its square into back (where it is), its ⌄ going out."""
        body = sq.inside if sq.opened() else None  # (its thread under its line, if it's all the way open)
        return {"pairs": [(sq.title, self.btw_title), (sq.status, self.btw_status),
                          (sq.full.get_child(), self.btw_back.get_child())],
                "leave": [sq.toggle], "body": body,
                "rides": [(sq.comp, sq.comp)] if body is not None else []}  # (its box, and its meter on the page)

    def close_side_question(self, animate=True):
        """Back to the list, the side question shown closing into its row (into its thread under it, if it was open
        there)."""
        self.btw_expand.finish()
        sq, self.btw_shown = self.btw_shown, None
        if sq is not None and animate and sq.get_parent() is self.btw_list:
            self.btw_expand.close("list", sq, sq.inside, **self.btw_parts(sq), within=self.btw_scroll,
                                  across=self.btw_list)
        else:
            self.btw_stack.set_visible_child_name("list")
        if sq is not None:
            sq.put_inside_row()  # (after: the page's picture taken with it in it)

    def side_question_titled(self, sq):
        if sq is self.btw_shown:
            self.btw_title.set_text(sq.title.get_label())

    def side_question_status(self, sq):
        """The header's status as the shown one's row has it."""
        if sq is self.btw_shown:
            self.btw_status.set_state(sq.status.state)
            self.btw_status.set_visible(sq.status.get_visible())

    def drop_side_question(self, sq):
        if sq is self.btw_shown:
            self.close_side_question(animate=False)
        if sq.get_parent() is self.btw_list:
            self.btw_list.remove(sq)
            self.btw_list.set_visible(self.btw_list.get_row_at_index(0) is not None)

    def update_artifact(self, info):
        old = self.artifact_rows.pop(info["url"], None)
        chip = ArtifactChip(info)
        if old is not None:
            self.artifact_list.insert(chip, old.get_index())
            self.artifact_list.remove(old)
            discard(old)
        else:
            self.artifact_list.prepend(chip)
        self.artifact_rows[info["url"]] = chip

    def bot_changed(self, info):
        """The open chat (info: the sidebar's, or None) a Bot or not: its Memory page shown, and read, or hidden."""
        bot = bool(info and info.get("bot"))
        if not bot and self.stack.get_visible_child_name() in ("memory", "browser", "routines"):
            self.stack.set_visible_child_name("meta")
        self.memory_page.set_visible(bot)
        self.browser_page.set_visible(bot)
        self.routines_page.set_visible(bot)
        self.routines.show(info)
        self.browser.show(info)
        self.memory.show(info)  # (a Bot's memory read; the same chat made a chat again: its memory as last saved)

    def reset(self):
        self.agent_expand.finish()  # (a subagent opening or closing: not drawn on over what's gone)
        self.proc_expand.finish()
        if self.proc_shown is not None:  # (its command and output go with its row)
            self.proc_shown.put_inside_row()
            self.proc_shown = None
        self.proc_stack.set_visible_child_name("list")
        for chip in self.artifact_rows.values():
            self.artifact_list.remove(chip)
            discard(chip)
        self.artifact_rows.clear()
        for lst in (self.agent_list, self.proc_list):
            while (row := lst.get_row_at_index(0)) is not None:
                lst.remove(row)
                discard(row)
        self.agent_rows.clear()
        if isinstance(shown := self.detail_holder.get_child(), Gtk.Viewport):
            shown.set_child(None)  # (the subagent shown: its transcript goes with the rest)
        for box in self.agent_boxes.values():
            discard(box)
        self.agent_boxes.clear()
        self.proc_rows.clear()
        self.agent_stack.set_visible_child_name("list")

    # ---- subagents
    def agent_view(self, tool_use_id):
        """The transcript box for a subagent's messages, or None if it isn't a tracked subagent."""
        return self.agent_boxes.get(tool_use_id)

    def link_card(self, card, task):
        """A subagent's tool card: View, beside its Prompt where its Output was, opens its transcript here (the side
        panel's Subagents, on it) rather than folding open what it said. Its Output's kept only for an Error."""
        if getattr(card, "view_btn", None) is None:
            tid = task.get("tool_use_id") or card.tool_id
            btn = Gtk.ToggleButton(label=wording.say("common.view"), valign=Gtk.Align.CENTER, focus_on_click=False,
                                   tooltip_text=wording.say("tool_calls.view_tip"))
            btn.add_css_class("tool-toggle")  # (as Prompt is, beside it)
            btn.connect("toggled", lambda b: b.get_active() and (b.set_active(False), self.view_agent(tid)))
            card.toggles.append(btn)
            card.view_btn = btn
        if card.state != "error":
            card.output_toggle.set_visible(False)
        card.toggles.set_visible(True)

    def update_task(self, task):
        if task.get("task_type") == "local_agent":
            self.update_agent(task)
        elif task.get("task_type"):
            self.update_process(task)

    @staticmethod
    def describe(task, marked=False):
        """A task's state and what it's done so far. marked: for a list row, whose spinner, ✓ or grey ✕ already says
        running, completed or interrupted, so only the other endings (failed, killed) are said in words."""
        status = task.get("status", "running")
        usage = task.get("usage") or {}
        bits = [] if marked and status in ("running", "completed", "interrupted") else [status]
        if task.get("subagent_type"):
            bits.insert(0, task["subagent_type"])
        if usage.get("tool_uses"):
            bits.append(wording.say("side_panel.tool_uses", n=usage["tool_uses"]))
        if usage.get("total_tokens"):
            bits.append(wording.say("side_panel.tokens", n=fmt_tokens(usage["total_tokens"])))
        if usage.get("duration_ms"):
            bits.append(wording.say("time.seconds", seconds=f"{usage['duration_ms'] / 1000:.1f}"))
        if status == "running" and task.get("activity"):
            bits.append(task["activity"])
        return " · ".join(bits)

    def update_agent(self, task):
        tid = task.get("tool_use_id") or task["task_id"]
        row = self.agent_rows.get(tid)
        if row is None:
            row = Adw.ActionRow(activatable=True)
            row.tool_use_id = tid
            row.chevron = Gtk.Image.new_from_icon_name("go-next-symbolic")
            row.add_suffix(row.chevron)
            # an agent started by another agent sits under it, indented like a file in a folder
            parent = self.agent_rows.get(getattr(self.win, "tool_parent", {}).get(tid))
            row.depth = parent.depth + 1 if parent is not None else 0
            row.status = StatusSlot()
            row.add_prefix(row.status)
            row.tree_parent, row.tree_anchor = parent, row.status
            if row.depth:  # indented; the list draws the line to it (a later prefix goes further left)
                row.add_prefix(Gtk.Box(width_request=row.depth * TreeList.INDENT - 6))
                row.add_css_class("nested-agent")
            self.agent_rows[tid] = row
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12, margin_end=12,
                          margin_top=12, margin_bottom=12)  # (in its cutout, as far in from the top as the chat's)
            box.add_css_class("zoomable")
            if task.get("prompt"):
                box.append(self.win.user_bubble(task["prompt"], []))
            self.agent_boxes[tid] = box
            if parent is not None:  # right under its parent (newest first, like the top level)
                self.agent_list.insert(row, parent.get_index() + 1)
            else:
                self.agent_list.prepend(row)
        row.set_title(html.escape(task.get("description") or wording.say("side_panel.subagent")))
        row.set_subtitle(html.escape(self.describe(task, marked=True)))
        row.status.set_state({"running": "running", "completed": "done",
                              "interrupted": "interrupted"}.get(task.get("status"), "error"))
        if self.agent_stack.get_visible_child_name() == "detail" and self.current == tid:
            self.set_detail_info(task)
            self.detail_status.set_state(row.status.state)

    current = None

    def open_agent(self, tool_use_id, from_row=True):
        box = self.agent_boxes.get(tool_use_id)
        if box is None:
            return
        self.agent_expand.finish()  # (one opening or closing: at its end, before its page is changed under it)
        self.win.subagent_opened(tool_use_id)
        self.current = tool_use_id
        task = next((t for t in self.win.tasks.values() if t.get("tool_use_id") == tool_use_id), {})
        self.detail_title.set_text(task.get("description") or wording.say("side_panel.subagent"))
        self.set_detail_info(task)
        row = self.agent_rows.get(tool_use_id)
        if row is not None:
            self.detail_status.set_state(row.status.state)
        if isinstance(old := self.detail_holder.get_child(), Gtk.Viewport):
            old.set_child(None)  # release the previous subagent's box so it can be shown again
        self.detail_holder.set_child(box)
        self.agent_find.refresh()
        if from_row and self.agent_stack.get_visible_child_name() == "list" and row is not None:  # (from its row, seen)
            self.agent_expand.open("detail", row, self.detail_inside, **self.expand_parts(row), within=self.agent_scroll,
                                   across=self.agent_list)
        else:
            self.agent_stack.set_visible_child_name("detail")
        self.show_page("agents")

    def set_detail_info(self, task):
        """What the subagent shown has done, as its row has it (the status beside it); nothing, no line."""
        text = self.describe(task, marked=True)
        self.detail_info.set_text(text)
        self.detail_info.set_visible(bool(text))

    def view_agent(self, tool_use_id):
        """A subagent's View: its page, opened from its row only if the list's already being looked at (shown just
        now, its row's never been seen where it is)."""
        seen = self.agent_list.get_mapped() and self.agent_stack.get_visible_child_name() == "list"
        self.win.show_panel(True, "agents")
        self.open_agent(tool_use_id, from_row=seen)

    def expand_parts(self, row):
        """How a subagent's row turns into its page's header (ExpandStack): its title, its line and its status each
        into the header's, and its › into back (flipped: ‹)."""
        labels = {cls: next((w for w in walk(row) if isinstance(w, Gtk.Label) and w.has_css_class(cls)), None)
                  for cls in ("title", "subtitle")}
        pairs = [(labels["title"], self.detail_title), (labels["subtitle"], self.detail_info),
                 (row.status, self.detail_status), (row.chevron, self.agent_back.get_child(), True)]
        return {"pairs": [pair for pair in pairs if pair[0] is not None]}

    def close_agent(self):
        """Back to the list, the subagent shown closing into its row."""
        row = self.agent_rows.get(self.current)
        if row is None:
            self.agent_stack.set_visible_child_name("list")
            return
        self.agent_expand.close("list", row, self.detail_inside, **self.expand_parts(row), within=self.agent_scroll,
                                across=self.agent_list)

    # ---- background processes
    def update_process(self, task):
        tid = task["task_id"]
        row = self.proc_rows.get(tid)
        if row is None:
            row = ProcessRow(self, tid)
            self.proc_rows[tid] = row
            self.proc_list.prepend(row)
        card = self.win.tool_cards.get(task.get("tool_use_id"))
        cmd = (card.input.get("command") if card is not None else None) or ""
        said = (card.input.get("description") if card is not None else None) or ""
        desc = task.get("description") or ""
        row.set_command(cmd)
        if said.strip() or (desc.strip() and desc.strip() != cmd.strip() and not cmd):
            row.head.set_title(html.escape(said or desc))  # Claude's words for it
            unpaint_command(row.title)
        else:  # no description: the command itself, as code (never our own summary of it), highlighted
            first = (cmd or desc or tid).strip().splitlines()[0] if (cmd or desc or tid).strip() else tid
            row.head.set_title(f"<tt>{html.escape(first[:300])}</tt>")
            if not cmd:
                unpaint_command(row.title)
            elif row.title.get_text() != getattr(row.title, "command_text", None):
                paint_command(row.title, row.title.get_text())
        # (no tooltip: it was the whole command, and a command's often far too long for one; opened, it's all there)
        row.head.set_subtitle(html.escape(self.describe(task, marked=True)))
        running = task.get("status", "running") == "running"
        row.stop.set_visible(running)
        row.status.set_state("running" if running else {"completed": "done", "interrupted": "interrupted"}
                             .get(task.get("status"), "error"))
        if row is self.proc_shown:
            self.process_header(row)
        self.refresh_output(row, task)

    def process_header(self, row):
        """The page's header as the shown process's row has it."""
        self.proc_title.set_markup(row.head.get_title())
        if getattr(row.title, "command_text", None):
            if self.proc_title.get_text() != getattr(self.proc_title, "command_text", None):
                paint_command(self.proc_title, self.proc_title.get_text())
        else:
            unpaint_command(self.proc_title)
        text = row.head.get_subtitle()
        self.proc_info.set_markup(text)
        self.proc_info.set_visible(bool(text))
        self.proc_status.set_state(row.status.state)
        self.proc_stop.set_visible(row.stop.get_visible())

    def repaint_commands(self):
        """The commands drawn as rows' titles in the code's colours, light or dark as the theme is now."""
        for label in [row.title for row in self.proc_rows.values()] + [self.proc_title]:
            text, spans = getattr(label, "command_spans", None) or (None, None)
            if spans and text == getattr(label, "command_text", None):  # (not another's, its own still coming)
                label.set_attributes(codespans.code_attrs(text, spans, Adw.StyleManager.get_default().get_dark()))

    def open_process(self, row):
        """row's command and output filling the page, opening out of its row (out of them under it, if they're open
        there), back (close_process) into it."""
        self.proc_expand.finish()  # (one opening or closing: at its end, before the page's changed under it)
        if self.proc_shown is not None:
            self.proc_shown.put_inside_row()
        self.proc_shown = row
        self.process_header(row)
        if self.proc_stack.get_visible_child_name() == "list" and self.stack.get_visible_child_name() == "processes":
            self.proc_expand.open("detail", row, row.inside, **self.proc_parts(row), within=self.proc_scroll,
                                  across=self.proc_list)
        else:
            self.proc_stack.set_visible_child_name("detail")
        self.proc_holder.append(row.take_inside())  # (after: the row's picture taken with it in it)
        self.show_page("processes")

    def proc_parts(self, row):
        """How a process's row turns into its page's header (ExpandStack): its title, line, status and stop into the
        header's, its square into back (where it is), its ⌄ going out."""
        return {"pairs": [(row.title, self.proc_title), (row.subtitle, self.proc_info), (row.status, self.proc_status),
                          (row.stop, self.proc_stop), (row.full.get_child(), self.proc_back.get_child())],
                "leave": [row.toggle], "body": row.inside if row.opened() else None}

    def close_process(self, animate=True):
        """Back to the list, the process shown closing into its row (into what's open under it, if it was open
        there)."""
        self.proc_expand.finish()
        row, self.proc_shown = self.proc_shown, None
        if row is not None and animate and row.get_parent() is self.proc_list:
            self.proc_expand.close("list", row, row.inside, **self.proc_parts(row), within=self.proc_scroll,
                                   across=self.proc_list)
        else:
            self.proc_stack.set_visible_child_name("list")
        if row is not None:
            row.put_inside_row()  # (after: the page's picture taken with it in it)

    def refresh_output(self, row, task):
        text = read_tail(task.get("output_file"), 4000)
        if text and text != row.output.code:
            row.output.code = text
            row.output.label.set_text(text)

    def poll_output(self):
        """Live output for running processes while the panel is open."""
        if self.get_visible():
            for tid, row in self.proc_rows.items():
                task = self.win.tasks.get(tid, {})
                if task.get("status", "running") == "running":
                    self.refresh_output(row, task)
        return True


class SignalPage(panels.ExpandStack):
    """The side panel's Signal page: the names this chat's replies mention, by kind, with how far
    each lookup got; a name opens its card (locations, a commit's PRs, ...), sliding in; the reader's row opens its
    chat out of the row, as a subagent's does (ExpandStack)."""

    ICONS = {"resolved": ("object-select-symbolic", "success"), "ambiguous": ("view-more-symbolic", None),
             "pending": ("content-loading-symbolic", "dim-label"), "not_found": ("dialog-question-symbolic", "dim-label"),
             "stale": ("dialog-warning-symbolic", "warning")}

    def __init__(self, win):
        super().__init__(vexpand=True)
        self.win, self.current, self.soon, self.status_soon = win, None, None, None
        self.reader_to_end = False  # (opened: at its chat's end once it's laid out)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        bar = Gtk.Box(spacing=6)
        self.search = Gtk.SearchEntry(placeholder_text=wording.say("signal.filter"), hexpand=True)
        self.search.connect("search-changed", lambda *_: self.rebuild())
        bar.append(self.search)
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic",
                                      tooltip_text=wording.say("signal.search_again"))
        self.refresh_btn.connect("clicked", lambda *_: self.refresh_not_found())
        bar.append(self.refresh_btn)
        top.append(bar)
        # what it may use: toggle buttons (pressed: on)
        options = Gtk.Box(spacing=6)
        lsp = load_settings().get("signal_lsp")  # (unanswered, servers already installed are used: nothing to fetch)
        for attr, label, active, tip, fn in (
                ("sonnet", providers.model_name(load_settings().get("signal_model") or "sonnet"),
                 bool(load_settings().get("signal_reader", True)),
                 wording.say("signal.reader_tip"),
                 self.set_sonnet),
                ("lsp", wording.say("signal.lsp"), lsp is True or (lsp is None and bool(lsp_commands())),
                 wording.say("signal.lsp_tip"), self.set_lsp),
                ("tools", wording.say("lookup.tools"), bool(load_settings().get("signal_tool_schemas")),
                 wording.say("signal.tools_tip"),
                 self.set_tool_schemas)):
            btn = Gtk.ToggleButton(label=label, active=active, tooltip_text=tip)
            btn.add_css_class("ref-option")
            btn.connect("toggled", lambda b, fn=fn: fn(b.get_active()))
            options.append(btn)
            setattr(self, attr, btn)
        # Signal for this chat, on or off: the header's Activate Signal switch (off, the reader, the language servers and
        # the lookups waiting stop at once; its links stay)
        self.master = Gtk.Switch(hexpand=True, halign=Gtk.Align.END, valign=Gtk.Align.CENTER,
                                 tooltip_text=wording.say("signal.this_chat"))
        if getattr(win, "signal_run", None) is not None:
            win.signal_run.bind_property("active", self.master, "active",
                                         GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        options.append(self.master)  # (in line with what it may use)
        top.append(options)
        self.summary = Gtk.Label(xalign=0, wrap=True)
        self.summary.add_css_class("dim-label")
        self.summary.add_css_class("caption")
        top.append(self.summary)
        # what it's doing: now, and a log of every lookup (newest first)
        now = Gtk.Box(spacing=8)
        self.spinner = Spinner(visible=False)
        now.append(self.spinner)
        self.now = Gtk.Label(xalign=0, wrap=True, hexpand=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.now.add_css_class("ref-now")
        now.append(self.now)
        top.append(now)
        # the reader, an agent as a subagent is: its row (its model, what it's doing), opening its chat on a page of its
        # own, set into the panel as Meta's and a subagent's are (the inside: widgets.inside)
        self.reader = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, visible=False)
        self.reader.add_css_class("boxed-list")
        self.reader_row = Adw.ActionRow(activatable=True, title="Reader", subtitle_lines=2)
        self.reader_status = StatusSlot("done")
        self.reader_row.add_prefix(self.reader_status)
        self.reader_chevron = Gtk.Image.new_from_icon_name("go-next-symbolic")
        self.reader_row.add_suffix(self.reader_chevron)
        self.reader.append(self.reader_row)
        self.reader.connect("row-activated", lambda *_: self.show_reader_page())
        top.append(self.reader)
        # its chat: the parts it's given, what it says, the searches and lookups it runs, what it adds
        self.reader_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12, margin_end=12,
                                  margin_top=12, margin_bottom=12)  # (as far in as a subagent's chat is in its own)
        self.reader_box.add_css_class("zoomable")
        self.reader_scroll = widgets.inside(natural_height(Gtk.ScrolledWindow(  # (its code unsquashed)
            child=self.reader_box, vexpand=True, margin_top=6, hscrollbar_policy=Gtk.PolicyType.NEVER)), start=False)
        self.reader_scroll.get_vadjustment().connect("changed", self.reader_laid_out)
        self.reader_shown, self.reader_cards = None, {}  # (the reader_chat drawn, and how much; its tool cards)
        self.empty = empty_label("")
        self.empty.set_wrap(True)
        self.empty.set_justify(Gtk.Justification.CENTER)
        top.append(self.empty)
        self.groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        top.append(self.groups)
        page = self.list_scroll = SidePanel._scrolled(top)
        self.add_named(page, "list")
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(spacing=6, margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=wording.say("signal.all_names"))
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self.show_list())
        self.detail_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        self.detail_title.add_css_class("heading")
        head.append(back)
        head.append(self.detail_title)
        detail.append(head)
        self.detail_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        detail.append(SidePanel._scrolled(self.detail_body))
        self.add_named(detail, "detail")
        # the reader's page: its row as its header (status, its model, what it's doing; back where the row's › is),
        # its chat under that
        reading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(spacing=6, margin_start=15, margin_end=12, margin_top=6, margin_bottom=6)
        self.reader_page_status = StatusSlot("done")
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER,
                         margin_start=6)
        self.reader_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.reader_title.add_css_class("heading")
        self.reader_info = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                                     ellipsize=Pango.EllipsizeMode.END)  # (two lines, as its row's)
        self.reader_info.add_css_class("dim-label")
        self.reader_info.add_css_class("caption")
        titles.append(self.reader_title)
        titles.append(self.reader_info)
        back = self.reader_back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=wording.say("signal.all_names"),
                                             valign=Gtk.Align.CENTER)
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self.close_reader_page())
        head.append(self.reader_page_status)
        head.append(titles)
        head.append(back)
        reading.append(head)
        reading.append(self.reader_scroll)
        self.add_named(reading, "reader")
        self.rebuild()

    # (its pages as a stack's: a name's card slides in; the reader's page opens out of its row: show_reader_page)
    def add_named(self, child, name):
        self.stack.add_named(child, name)

    def get_visible_child_name(self):
        return self.stack.get_visible_child_name()

    def set_visible_child_name(self, name):
        self.finish()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_visible_child_name(name)

    def reader_parts(self):
        """How the reader's row turns into its page's header (ExpandStack): its title, its line and its status each
        into the header's, and its › into back (flipped: ‹)."""
        labels = {cls: next((w for w in walk(self.reader_row) if isinstance(w, Gtk.Label) and w.has_css_class(cls)),
                            None) for cls in ("title", "subtitle")}
        pairs = [(labels["title"], self.reader_title), (labels["subtitle"], self.reader_info),
                 (self.reader_status, self.reader_page_status), (self.reader_chevron, self.reader_back.get_child(), True)]
        return {"pairs": [pair for pair in pairs if pair[0] is not None], "within": self.list_scroll,
                "across": self.reader}

    def show_reader_page(self):
        """The reader's chat on its own page, opening out of its row, at its end."""
        self.reader_to_end = True
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        if self.get_visible_child_name() == "list":
            self.open("reader", self.reader_row, self.reader_scroll, **self.reader_parts())
        else:
            self.stack.set_visible_child_name("reader")
        self.reader_laid_out(self.reader_scroll.get_vadjustment())

    def close_reader_page(self):
        """Back to the names, the reader's page closing into its row."""
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.close("list", self.reader_row, self.reader_scroll, **self.reader_parts())

    def reader_laid_out(self, adj):
        """The reader's chat laid out (again): just opened, at its end."""
        if self.reader_to_end and adj.get_upper() > adj.get_page_size() > 0:
            self.reader_to_end = False
            adj.set_value(adj.get_upper() - adj.get_page_size())

    def status_changed(self):
        """What Signal is doing changed: update the status, the log and the skipped names (once a frame)."""
        if self.status_soon is None:
            self.status_soon = GLib.idle_add(self._status_soon)

    def _status_soon(self):
        self.status_soon = None
        ref = self.win.signal
        for w in (self.spinner, self.now):
            w.set_visible(ref is not None)
        self.reader.set_visible(ref is not None and bool(ref.reader_chat))
        if (ref is None or not ref.reader_chat) and self.get_visible_child_name() == "reader":
            self.show_list()  # (another folder's, whose reader hasn't read: nothing to show)
        if ref is None:
            return False
        busy = ref_busy(ref) and self.win in ref.runners  # (this chat's: another window's chat may run it)
        self.spinner.set_visible(busy)
        self.now.set_label(ref.status_text())
        for slot in (self.reader_status, self.reader_page_status):
            slot.set_state(self.reader_state(ref, busy))
        line = self.reader_line_text(ref)
        self.reader_row.set_subtitle(html.escape(line))
        self.reader_info.set_text(line)
        self.reader_info.set_visible(bool(line))
        self.show_reader(ref)
        return False

    def show_reader(self, ref):
        """The reader's chat, the lines it's added since it was last drawn (kept at the bottom if it was there)."""
        chat, box = ref.reader_chat, self.reader_box
        name = providers.model_name(ref.agent.model) if ref.agent is not None else "Reader"
        self.reader_row.set_title(name)
        self.reader_title.set_text(name)
        self.reader_title.set_tooltip_text(ref.agent.model if ref.agent is not None else None)
        drawn = self.reader_shown
        if drawn is None or drawn[0] is not chat or drawn[1] > len(chat) or \
                (drawn[1] and chat[drawn[1] - 1][0] != drawn[2]):  # (another chat, or older lines dropped)
            while (c := box.get_first_child()) is not None:
                box.remove(c)
                discard(c)
            drawn, self.reader_cards = (chat, 0, None), {}
        if drawn[1] == len(chat):
            return
        adj = self.reader_scroll.get_vadjustment()
        at_end = adj.get_value() + adj.get_page_size() >= adj.get_upper() - 4
        for when, kind, text in chat[drawn[1]:]:
            if kind == "assistant":
                self.reader_message(text)
            elif kind == "results":
                for c in text:
                    if isinstance(c, dict) and c.get("type") == "tool_result" and \
                            (card := self.reader_cards.get(c.get("tool_use_id"))) is not None:
                        card.set_result(c.get("content"), c.get("is_error", False))
            else:
                box.append(self.reader_line(kind, text, when))
        self.reader_shown = (chat, len(chat), chat[-1][0])
        while box.observe_children().get_n_items() > 400:
            first = box.get_first_child()
            box.remove(first)
            discard(first)
        if at_end:
            GLib.idle_add(lambda: adj.set_value(adj.get_upper() - adj.get_page_size()) and False)

    @staticmethod
    def reader_state(ref, busy):
        """The reader's mark, as a subagent's is: reading (a spinner); its last call refused (✗); stopped partway, by
        Activate Signal or its spending cap, with replies left to read (stopped); else done (✓)."""
        ai, last = ref.agent, (ref.reader_chat[-1][1] if ref.reader_chat else None)
        if busy and ai is not None and ai.busy():
            return "running"
        if last == "refused":
            return "error"
        if last not in ("done", None) or ref.waiting or (ai is not None and ai.cost >= ai.budget and ai.part):
            return "interrupted"
        return "done"

    @staticmethod
    def reader_line_text(ref):
        """What the reader's row says under its name: the part it's on, else the last thing it did or said."""
        ai = ref.agent
        if ai is not None and ai.busy() and ai.part:
            return f"Reading replies {ai.part['a']} to {ai.part['b']}"
        for _when, kind, text in reversed(ref.reader_chat):
            if kind in ("part", "done", "refused") and isinstance(text, str):
                return text
            if kind == "assistant":
                said = " ".join(c.get("text", "") for c in text if isinstance(c, dict) and c.get("type") == "text")
                if said.strip():
                    return " ".join(said.split())
        return ""

    def reader_message(self, content):
        """A message of the reader's, drawn as a subagent's is in its side-panel view: its words as a reply, its tool
        calls as cards (their results come with the next entry). Its words aren't looked up by Signal: they're
        Signal's own."""
        for c in content:
            if c.get("type") == "text" and c.get("text", "").strip():
                self.reader_box.append(MarkdownView(c["text"].strip()))
            elif c.get("type") == "tool_use" and c.get("id") not in self.reader_cards:
                card = self.reader_cards[c["id"]] = ToolCard(c.get("name", ""), c["id"])
                card.set_input(c.get("input") or {})
                self.reader_box.append(card)

    @staticmethod
    def reader_line(kind, text, when):
        """One of Baude's lines in the reader's chat (a part given, what the checks kept, a part done), quieter
        than the reader's own messages."""
        e = html.escape
        if kind == "glossary":
            rows, parts = text["rows"], []
            links = [r for r in rows if r["verdict"] == "link"]
            if links:
                parts.append("Linked " + ", ".join(f"<tt>{e(r['surface'])}</tt>" + (f" ({e(r['kind'])})" if r["kind"]
                                                                                    else "") for r in links[:30]) +
                             (f" and {len(links) - 30} more" if len(links) > 30 else ""))
            absent = [r for r in rows if r["verdict"] == "not_in_code"]
            if absent:
                parts.append("Not in the code: " + ", ".join(f"<tt>{e(r['surface'])}</tt>" for r in absent[:20]))
            plain = [r for r in rows if r["verdict"] == "plain"]
            if plain:
                parts.append("Plain words: " + ", ".join(e(r["surface"]) for r in plain[:20]))
            if text["rejected"]:
                parts.append(f"{text['rejected']} rejected by the checks")
            label = Gtk.Label(xalign=0, wrap=True, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                              use_markup=True, label="\n".join(parts))
            label.add_css_class("reader-glossary")
        else:
            label = Gtk.Label(xalign=0, wrap=True, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=text)
            label.add_css_class("reader-refused" if kind == "refused" else "reader-part")
        label.set_tooltip_text(time.strftime("%H:%M:%S", time.localtime(when)))
        return label

    def changed(self):
        """Something changed: rebuild once, before the next frame; while the page isn't shown, as it's next shown
        (its list is every name the chat has, and Signal says it changed with each name it finds)."""
        if not self.get_mapped():
            if getattr(self, "due", None) is None:
                self.due = self.connect("map", SignalPage._shown_due)
            return
        if self.soon is None:
            self.soon = GLib.idle_add(self._rebuild_soon)

    def _shown_due(self):
        self.disconnect(self.due)
        self.due = None
        self.rebuild()

    def _rebuild_soon(self):
        self.soon = None
        self.rebuild()
        return False

    def shown(self):
        """The refs listed now: this chat's names, filtered (plain-text guesses that came to nothing aren't names)."""
        ref, want = self.win.signal, self.search.get_text().strip().lower()
        if ref is None:
            return []
        out = []
        linked = {s for (_m, s), d in ref.decisions.items() if d["verdict"] == "link"}  # (the reader's own names too)
        for s in list(self.win.ref_surfaces) + sorted(linked - set(self.win.ref_surfaces)):
            r = ref.refs.get(s)
            if r is None or (r.status in ("not_found", "pending") and s not in ref.strong) or r.data.get("plain") or \
                    refkinds.ref_cloud_provider(s):  # (never a link: not a name to list either)
                continue
            if want and want not in s.lower() and want not in r.canonical.lower():
                continue
            out.append(r)
        return out

    def rebuild(self):
        while (c := self.groups.get_first_child()) is not None:
            self.groups.remove(c)
            discard(c)
        self.status_changed()
        if self.win.signal is None:
            self.empty.set_label(wording.say("signal.off"))
            self.empty.set_visible(True)
            self.summary.set_visible(False)
            return
        refs = self.shown()
        counts = {}
        for r in refs:
            counts[r.status] = counts.get(r.status, 0) + 1
        parts = [said(counts[k]) for k, said in (
            ("resolved", lambda n: wording.say("signal.linked", n=n)),
            ("ambiguous", lambda n: wording.say("signal.several", n=n)),
            ("pending", lambda n: wording.say("signal.looking_up", n=n)),
            ("not_found", lambda n: wording.say("signal.not_found", n=n)),
            ("stale", lambda n: wording.say("signal.stale", n=n))) if counts.get(k)]
        self.summary.set_label(" · ".join(parts) + "\n" +
                               wording.say("signal.glossary", path=pretty_path(self.win.signal.dir)))
        self.summary.set_visible(True)
        self.empty.set_label(wording.say("signal.empty"))
        self.empty.set_visible(not refs)
        self.refresh_btn.set_sensitive(bool(counts.get("not_found")))
        def group(r):  # (a name being looked up may be anything; a table is a table, not a code symbol)
            if r.status == "pending":
                return "pending"
            label = ref_kind_label(r)
            return "db" if label in (wording.say("databases.database_table"), wording.say("signal.database_column"),
                                     "Database collection",
                                     wording.say("databases.database")) else r.kind
        groups = [("pending", wording.say("signal.looking_up_group"))] + [(k, many) for k, (_one, many) in REF_KINDS.items()]
        groups.insert(groups.index(("symbol", REF_KINDS["symbol"][1])) + 1,
                      ("db", wording.say("signal.database_tables")))
        for kind, many in groups:
            mine = [r for r in refs if group(r) == kind]
            if not mine:
                continue
            heading = Gtk.Label(label=many, xalign=0)
            heading.add_css_class("ref-kind")
            self.groups.append(heading)
            lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            lst.add_css_class("boxed-list")
            lst.connect("row-activated", lambda _l, row: self.show_ref(row.surface))
            for r in mine:
                lst.append(self.row(r))
            self.groups.append(lst)
        if self.current and self.get_visible_child_name() == "detail":
            self.show_ref(self.current)

    def row(self, r):
        row = Gtk.ListBoxRow(activatable=True)
        row.surface = r.surface
        box = Gtk.Box(spacing=8, margin_top=5, margin_bottom=5, margin_start=8, margin_end=8)
        icon, css = self.ICONS.get(r.status, self.ICONS["pending"])
        img = Gtk.Image.new_from_icon_name(icon)
        if css:
            img.add_css_class(css)
        box.append(img)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        name = Gtk.Label(label=r.surface, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        name.add_css_class("mono")
        sub = Gtk.Label(label=r.summary(), xalign=0, ellipsize=Pango.EllipsizeMode.END)
        sub.add_css_class("dim-label")
        sub.add_css_class("caption")
        text.append(name)
        text.append(sub)
        box.append(text)
        row.set_child(box)
        row.set_tooltip_text(r.summary())
        return row

    def show_list(self):
        self.current = None
        self.set_visible_child_name("list")

    def show_ref(self, surface):
        """A name's card: what it is, how it was found, its locations (each opens), Refresh."""
        r = self.win.signal.refs.get(surface) if self.win.signal else None
        if r is None:
            return
        self.current = surface
        self.detail_title.set_text(surface)
        body = self.detail_body
        while (c := body.get_first_child()) is not None:
            body.remove(c)
            discard(c)
        body.append(sel_label(f'<span alpha="65%">{REF_KINDS.get(r.kind, ("Name",))[0]}</span>  ' +
                              name_markup(r.canonical)))
        if not (r.status == "resolved" and (r.kind in ("commit", "branch", "pr", "tool", "subagent", "skill") or
                                            r.data.get("context"))):  # (their facts say it all)
            status = ref_status_tip(Gtk.Label(label=ref_status_text(r), xalign=0, wrap=True), r)
            status.add_css_class("warning" if r.status == "stale" else "dim-label")
            body.append(status)
        for markup in self.facts(r):
            body.append(sel_label(markup))
        if known := ref_known_text(self.win, surface):
            body.append(sel_label(html.escape(known), css="dim-label"))
        if r.kind == "tool" and r.status == "resolved":
            self.tool_card(r, body)
        if r.locations:
            heading = Gtk.Label(label=wording.say("signal.locations"), xalign=0)
            heading.add_css_class("ref-kind")
            body.append(heading)
            locs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            ref_location_buttons(self.win, r, locs)
            body.append(locs)
            first = r.locations[0]
            if first.get("state"):
                body.append(sel_label('<span alpha="60%">' + html.escape(
                    wording.say("signal_card.read_from", state=first["state"])
                    + (wording.say("signal_card.read_from_at", sha=(first.get("commit_sha") or "")[:9])
                       if first.get("commit_sha") else "")
                    + (wording.say("signal_card.read_from_on", branch=first["branch"]) if first.get("branch") else ""))
                                      + "</span>", css="caption"))
        if names := ref_run_names(r):
            kube_running_rows(self.win, names, body)
        refresh = Gtk.Button(label=wording.say("signal.refresh"), tooltip_text=wording.say("signal.refresh_tip"))
        refresh.connect("clicked", lambda *_: self.win.signal.refresh([surface], self.win.touched_paths()))
        actions = Gtk.Box(spacing=6)
        actions.append(refresh)
        if r.kind in ("commit", "branch") and r.status == "resolved":
            git = Gtk.Button(label=wording.say("signal.show_in_git"), tooltip_text=wording.say("signal.show_in_git_tip"))
            git.connect("clicked", lambda *_: self.win.show_git_ref(r))
            actions.append(git)
        body.append(actions)
        self.set_visible_child_name("detail")

    def tool_card(self, r, body):
        """A tool's inputs (a table), its output type, and this chat's calls to it."""
        d, name = r.data, r.data.get("tool") or r.canonical
        fields, e = d.get("input"), html.escape
        if fields:  # (a list, not a table: the panel is narrow and the descriptions matter most)
            heading = Gtk.Label(label=wording.say("signal_card.inputs"), xalign=0)
            heading.add_css_class("ref-kind")
            body.append(heading)
            box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            box.add_css_class("boxed-list")
            for f in fields:
                line = f"<tt><b>{e(f['name'])}</b></tt>" + (f"  <tt>{e(f['type'][:200])}</tt>" if f.get("type") else "") \
                    + (f'  <span alpha="60%">{wording.say("signal_card.required")}</span>' if f.get("required") else "") \
                    + (f'\n<span alpha="75%">{e(f["doc"])}</span>' if f.get("doc") else "")
                lbl = sel_label(line)
                for side in ("top", "bottom"):
                    getattr(lbl, f"set_margin_{side}")(6)
                for side in ("start", "end"):
                    getattr(lbl, f"set_margin_{side}")(10)
                box.append(lbl)
            body.append(box)
        elif fields is not None:
            body.append(sel_label(f'<span alpha="60%">{wording.say("signal_card.no_inputs")}</span>'))
        if d.get("output_text"):
            out = Gtk.Expander(label=wording.say("meta.output"))
            out.set_child(CodeBlock(d["output_text"], "ts"))
            body.append(out)
        calls = [c for c in self.win.tool_cards.values() if c.name == name and c.input]
        if calls:
            body.append(sel_label(f"<b>{wording.say('signal_card.called', n=len(calls))}</b>"
                                  + (wording.say("signal_card.last_ones") if len(calls) > 1 else ":")))
            for c in calls[-2:]:
                body.append(CodeBlock(json.dumps(c.input, indent=2, ensure_ascii=False)[:1500], "json"))
        if name in ("Agent", "Task") and SUBAGENTS:
            types = ", ".join(f"{n} ({'built in' if i.get('built_in') else 'defined here'})" for n, i in SUBAGENTS.items())
            body.append(sel_label(f'<span alpha="70%">Subagent types: {html.escape(types)}</span>'))
        if name == "Skill" and SKILLS:
            body.append(sel_label(f'<span alpha="70%">Skills: {html.escape(", ".join(SKILLS))}</span>'))

    @staticmethod
    def facts(r):
        """Lines of markup about a commit, branch or pull request (GitHub links, dates, LOC, PRs), or a tool,
        subagent type or skill (what it is, where its schema came from)."""
        d, e, out = r.data, html.escape, []
        link = lambda url, text: f'<a href="{e(url)}">{e(text)}</a>'  # noqa: E731
        if r.status != "resolved":
            return out
        if r.kind == "tool":
            src = d.get("source")
            if d.get("description"):
                out.append(e(d["description"]))
            if "permission" in d:
                out.append(wording.say("signal_card.asks_yes" if d["permission"] else "signal_card.asks_no"))
            if src == "anthropic":
                where = wording.say("signal_card.claude_code", version=d.get("cli") or "").strip() + (
                    wording.say("signal_card.sdk_types", version=d["sdk"]) if d.get("sdk") and d.get("input") is not None
                    else "")
                docs = TOOL_DOCS_PAGE + (f"#{d['anchor']}" if d.get("anchor") else "")
                out.append(f'<span alpha="70%">{e(where)} · </span>{link(docs, wording.say("signal_card.docs"))}')
            elif src == "mcp":
                out.append(f'<span alpha="70%">{e(wording.say("signal_card.from_mcp", server=d.get("server", "")))}</span>')
            elif d.get("server"):
                out.append(f'<span alpha="70%">{e(wording.say("signal_card.desktop_mcp", server=d["server"]))}</span>')
            else:
                out.append(f'<span alpha="70%">{e(wording.say("signal_card.no_schema"))}</span>')
            return out
        if d.get("context"):  # (a kubeconfig context: what kubectl would use, never its credentials)
            out.append(f"{wording.say('lookup.kubeconfig_cluster' if d.get('cluster_only') else 'lookup.kubeconfig_context')}"
                       f" <b>{e(d['context'])}</b>" +
                       (e(wording.say("signal_card.current_one")) if d.get("current") else ""))
            for key, label in (("cluster", wording.say("signal_card.cluster")), ("server", wording.say("signal_card.server")),
                               ("namespace", wording.say("signal_card.namespace")), ("user", wording.say("report.user"))):
                if d.get(key):
                    out.append(f"{label}: <tt>{e(str(d[key]))}</tt>")
            kubeconfig = pretty_path(d.get("kubeconfig") or "")
            out.append('<span alpha="70%">' + e(wording.say("signal_card.in_kubeconfig_line", path=kubeconfig, line=d["line"])
                                                 if d.get("line") else wording.say("signal_card.in_kubeconfig", path=kubeconfig))
                       + "</span>")
            return out
        if r.kind == "k8s" and d.get("kind"):
            out.append(f"<b>{e(d['kind'])}</b> <tt>{e(d.get('name') or '')}</tt>" +
                       (e(wording.say("signal_card.in_namespace", namespace="\0")).replace(
                           "\0", f"<tt>{e(d['namespace'])}</tt>") if d.get("namespace") else ""))
            if d.get("chart") is not None:
                out.append(e(wording.say("signal_card.helm_chart", chart="\0")).replace(
                    "\0", f"<tt>{e(d['chart'] or '.')}</tt>") +
                           (e(wording.say("signal_card.helm_release", release="\0")).replace(
                               "\0", f"<tt>{e(d['release'])}</tt>") if d.get("release") else ""))
            if d.get("overlay"):
                out.append(e(wording.say("signal_card.kustomize", overlay="\0")).replace(
                    "\0", f"<tt>{e(d['overlay'])}</tt>"))
            if d.get("how"):
                out.append(f'<span alpha="70%">{e(d["how"])}</span>')
            return out
        if r.kind in ("subagent", "skill"):
            if d.get("description"):
                out.append(e(d["description"]))
            for key, label in (("model", wording.say("common.model")), ("tools", wording.say("lookup.tools"))):
                if d.get(key):
                    out.append(f"{label}: <tt>{e(str(d[key]))}</tt>")
            if d.get("built_in"):
                out.append(f'<span alpha="70%">{e(wording.say("signal_card.built_in"))}</span>')
            return out
        if r.kind == "commit":
            out.append(f"<b>{e(d.get('subject', ''))}</b>")
            if d.get("range"):
                out.append(e(wording.say("signal_card.commits_range", n=d.get("count", 0), **{
                    "from": "\0", "to": "\1"})).replace("\0", f"<tt>{e(d['range'][:9])}").replace(
                        "\1", f"{e(d.get('sha', '')[:9])}</tt>"))
            if d.get("repo"):
                out.append(e(wording.say("signal_card.in_repo", repo="\0")).replace(
                    "\0", f"<tt>{e(d['repo'].replace(os.path.expanduser('~'), '~', 1))}</tt>"))
            out.append(f"{e(d.get('author', ''))} · {e(ref_date(d.get('date')))}")
            out.append(wording.say("signal_card.changed", added=d.get("added", 0), removed=d.get("removed", 0),
                                   n=d.get("files", 0)))
            out.append(link(d["url"], wording.say("signal_card.open_github")) if d.get("url") else
                       wording.say("signal_card.local_only"))
            if d.get("rewritten"):
                rep = e(wording.say("signal_card.replaced_by", sha="\0")).replace(
                    "\0", f"<tt>{e(d['successor'][:9])}</tt>") if d.get("successor") else ""
                out.append(e(wording.say("signal_card.rewritten")) + rep)
            elif d.get("branches") or d.get("remotes"):
                on = (d.get("branches") or []) + (d.get("remotes") or [])
                out.append(e(wording.say("git.on_branches_more", branches=", ".join(on[:8]), n=len(on) - 8)
                             if len(on) > 8 else wording.say("git.on_branches", branches=", ".join(on[:8]))))
        elif r.kind == "branch":
            out.append(f"<b>{e(d.get('subject', ''))}</b>")
            out.append(e(wording.say("signal_card.last_commit", date=ref_date(d.get("date")), sha="\0")).replace(
                "\0", f"<tt>{e(d.get('sha', '')[:9])}</tt>"))
            if "ahead" in d:
                out.append(e(wording.say("signal_card.ahead_behind", ahead=d["ahead"], behind=d["behind"],
                                         base=d.get("base", ""))))
            if d.get("upstream"):
                out.append(e(wording.say("signal_card.tracks", upstream=d["upstream"])))
            out.append(link(d["url"], wording.say("signal_card.open_github")) if d.get("url") else
                       wording.say("signal_card.not_pushed"))
        elif r.kind == "pr":
            out.append(f"<b>{e(d.get('title') or '#' + str(d.get('number')))}</b>")
            if d.get("state"):
                dates = [wording.say("signal_card.opened", date=ref_date(d.get("created")))]
                if d.get("merged"):
                    dates.append(wording.say("signal_card.merged", date=ref_date(d["merged"])))
                elif d.get("closed"):
                    dates.append(wording.say("signal_card.closed", date=ref_date(d["closed"])))
                out.append(f"{e(d['state'].capitalize())}: " + e(", ".join(dates)))
            if d.get("additions") is not None:
                out.append(wording.say("signal_card.changed", added=d["additions"], removed=d["deletions"],
                                       n=d.get("changed_files", 0))
                           + (f" · {e(d.get('head') or '')} → {e(d.get('base') or '')}" if d.get("head") else ""))
            out.append(link(d["url"], wording.say("signal_card.open_github")))
        if r.kind in ("commit", "branch"):
            if d.get("prs"):
                out.append(f"<b>{wording.say('lookup.pull_requests')}</b>")
                for p in d["prs"]:
                    when = wording.say("signal_card.merged", date=ref_date(p["merged"])) if p.get("merged") else \
                        f"{p.get('state', '')}, " + wording.say("signal_card.opened", date=ref_date(p.get("created")))
                    out.append(f"{link(p['url'], '#' + str(p['number']))} {e(p.get('title', ''))} ({e(when)})")
            elif d.get("pushed") and "prs" in d:
                out.append(wording.say("signal_card.no_prs"))
            elif d.get("pushed") and not shutil.which("gh"):
                out.append(f'<span alpha="60%">{wording.say("signal_card.install_gh")}</span>')
        return out

    def set_sonnet(self, on):
        save_settings(signal_reader=on)
        if self.win.signal is not None:
            self.win.signal.set_agent(on)

    def set_tool_schemas(self, on):
        if load_settings().get("signal_tool_schemas") is on:
            return
        if self.win.signal is not None:
            self.win.signal.set_tool_schemas(on)
        else:
            save_settings(signal_tool_schemas=on)

    def set_lsp(self, on):
        if load_settings().get("signal_lsp") is on:
            return
        if self.win.signal is not None:
            self.win.signal.set_lsp(on)
        else:
            save_settings(signal_lsp=on)

    def refresh_not_found(self):
        ref = self.win.signal
        if ref is not None:
            ref.refresh([r.surface for r in self.shown() if r.status == "not_found"], self.win.touched_paths())
