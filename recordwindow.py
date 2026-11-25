"""recordwindow — Record's window (Window) and its Settings: the notebooks, the tabs, sizes, zoom, the launcher
and feedback, and what's open. Another machine's notebooks, through its host process, are in recordhosts.py
(RecordHosts).
"""
import html
import json
import os
import sys
import threading
import time
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; beside this file)
import markdownview  # noqa: E402
import chatparts  # noqa: E402
import panels  # noqa: E402
import chatfind  # noqa: E402
import pickers  # noqa: E402
import theming  # noqa: E402
import meshui  # noqa: E402
from baudelib import wording  # noqa: E402  (its words, Baude's: schema/copy)
from baudelib.transcripts import transcript_model  # noqa: E402
import feedback  # noqa: E402  (Send Feedback…, as Baude's: beside this file)
import fixview  # noqa: E402  (☰ → Ongoing Feedback, as Baude's)
import viewers  # noqa: E402  (Baude's viewers, in Record's window: beside this file)
from codeview import find_in_focus  # noqa: E402
from macos import window_frame  # noqa: E402
import update  # noqa: E402  (a new version from git, restarted onto once idle, as Baude's: beside this file)
import meta  # noqa: E402  (Meta, the helper in the right bar: Baude's too, beside this file)
import metalook  # noqa: E402
import spelling  # noqa: E402  (the pages' spelling and grammar, as they're written: beside this file)
from recordstore import (  # noqa: E402
    HERE_HOST, HOME, Store, THIS_MACHINE, apps_place, book, file_part, full_name, install, installed,
    local_remote, network, noun, parse_citation, restart_path, session_info, session_path, short_dir, short_page,
    uninstall,
)
from recordmeta import paned_grip  # noqa: E402
from recordui import (  # noqa: E402
    DiaryPlace, NotesNames, ZOOM_CSS, backup_zip, chat_names, export_zip, set_zoom_class, size_said, zip_sizes,
    zoom_class,
)
from diaryeditor import DiaryEditor  # noqa: E402
from recordreader import ColumnSlide, FixedWidth, PagesList, Reader, markup_row  # noqa: E402
from recordchat import RecordChat  # noqa: E402
from recordhosts import RecordHosts  # noqa: E402


_HOST_APART = []  # (meshui.HostApart: this machine's host process on other code than Record's, said in the banner)


def host_apart(app):
    """The app's meshui.HostApart, made as it's first wanted: what it says, in its windows' banner."""
    if not _HOST_APART:
        _HOST_APART.append(meshui.HostApart(app, update.STARTED, lambda: [
            w.show_host_apart() for w in app.get_windows() if isinstance(w, Window)]))
    return _HOST_APART[0]


# ---------------------------------------------------------------- the window

class Settings(Adw.PreferencesDialog):
    """☰ → Settings… (⌘, on macOS, Ctrl+, elsewhere), as Baude's: Meta (the model it thinks with, Haiku unless another's
    chosen, and Code mode), Full-width text, Code Font, Accent Colour, Show in Applications, and the sizes, 1.0 the usual: the
    text's and the icons' (Baude's settings: the same in both, Code Font and Accent Colour too), and the zoom Ctrl/⌘ 0 goes back to;
    Export Notebook… and Export All…, each saying what its zip comes to; Send Feedback… and the feedback sent. Each
    takes at once (Code Font from the next start) and is kept."""

    def __init__(self, win):
        super().__init__(title=wording.say("window.settings_title"))
        page = widgets.settings_page()
        page.add(meta.settings_group(win.chat))  # (Baude's too, and the same settings)
        group = Adw.PreferencesGroup()
        group.add(theming.settings_switch(wording.say("meta_settings.full_width"), None, "win.fullwidth"))
        group.add(markup_row(win))
        group.add(theming.settings_code_font(win, "Record"))  # (Baude's too, and the same setting)
        group.add(theming.settings_accent())  # (Baude's too, and the same setting)
        group.add(update.settings_row())  # (Baude's too: the one setting)
        group.add(theming.notifications_row())  # (Baude's too)
        self.version_row = update.version_row()
        group.add(self.version_row)
        spell = theming.settings_switch("Check Spelling", spelling.about(), "win.spellcheck")
        spelling.when_made(lambda: spell.set_subtitle(spelling.about()))  # (said once it's known)
        group.add(spell)
        group.add(theming.settings_switch("Include glossary and chats in backup",
                                          backup_zip().replace(os.path.expanduser("~"), "~", 1),
                                          "win.backupall"))
        # (one setting for all four apps: what Baude's Settings, or an --install, did since shown)
        win.lookup_action("launcher").set_state(GLib.Variant.new_boolean(installed()))
        group.add(theming.settings_switch(wording.say("window.show_in", where=apps_place()), None, "win.launcher"))
        page.add(group)
        sizes = Adw.PreferencesGroup(title=wording.say("window.size"))  # (the text's and the icons' are Baude's too)
        sizes.add(theming.settings_size(wording.say("meta_settings.text_scale"), None,
                                        widgets.scale_setting("text_scale"),
                                        lambda s: win.set_scale("text_scale", s), key="text_scale"))
        sizes.add(theming.settings_size(wording.say("meta_settings.icon_scale"), None,
                                        widgets.scale_setting("icon_scale"),
                                        lambda s: win.set_scale("icon_scale", s), key="icon_scale"))
        sizes.add(theming.settings_size(wording.say("meta_settings.global_scale"), None,
                                        win.store.zoom_default,  # (the zoom ⌘0 goes back to)
                                        win.set_zoom_default, widgets.ZOOM_MIN, widgets.ZOOM_MAX, key="zoom_default"))
        page.add(sizes)

        def go(row, action):  # (this goes first: a capture is of the window, not of this over it)
            row.get_ancestor(Adw.Dialog).close()
            win.activate_action(action, None)
        exports = [(f"Export {book()}…", "win.export", [win.current])] if win.current else []
        exports.append((f"Export All {book(plural=True)}…", "win.exportall", win.store.notebooks))
        group = Adw.PreferencesGroup()
        self.export_rows = []  # (each says its sizes: _sized)
        for title, action, _notebooks in exports:
            row = Adw.ActionRow(title=title, subtitle="Working out size…", activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _row, chosen: win.activate_action(chosen, None), action)  # (this stays: choose_zip)
            group.add(row)
            self.export_rows.append(row)
        page.add(group)
        group = Adw.PreferencesGroup()
        for title, action in ((wording.say("fixes.send_feedback"), "win.feedback"),
                              (wording.say("window.ongoing_feedback"), "win.fixes")):
            row = Adw.ButtonRow(title=title, end_icon_name="go-next-symbolic")
            row.connect("activated", go, action)
            group.add(row)
        page.add(group)
        self.add(page)
        copies, stop = json.loads(json.dumps([notebooks for _title, _action, notebooks in exports])), threading.Event()
        self.connect("closed", lambda *_: stop.set())  # (nothing to say them to: no more reading large chats)
        threading.Thread(target=Settings._work_out_sizes, args=([self], copies, stop), daemon=True).start()

    @staticmethod
    def _work_out_sizes(holding, copies, stop):
        """A thread. This in a list, let go of on the main loop: the last reference to a widget mustn't go off it. (Not a
        weak one: nothing else in Python need have hold of this, once it's presented.)"""
        for i, notebooks in enumerate(copies):
            try:
                sizes = zip_sizes(notebooks, stop)
                if sizes is None:
                    break
                alone, whole = sizes
                said = f"{size_said(alone)}; with glossary and chats, not known" if whole is None else \
                    f"{size_said(alone)}, or {size_said(whole)} with glossary and chats"
            except Exception as e:  # (a thread. The export says too, if it's still so)
                print(f"Record: an export's size: {e!r}", file=sys.stderr)
                said = "Size not known"
            GLib.idle_add(Settings._sized, holding, i, said)
        GLib.idle_add(holding.clear)

    @staticmethod
    def _sized(holding, i, said):
        settings = holding[0]
        if settings.get_realized():  # (closed, or its window gone: left alone)
            settings.export_rows[i].set_subtitle(said)
        return False


class Window(RecordHosts, Adw.ApplicationWindow, feedback.FeedbackHost, viewers.ViewerHost, theming.AccentFade):
    """Record's window, as Baude's is laid out: on the left the notebooks and, under them, the open one's pages
    (PagesList); in the middle what's open, a tab each (panels.MainTabs: a page of notes, a chat to read, a file's
    code), their bar at the top of the inside, as Baude's are over its chat; on the right Meta (your conversation
    with it), Files and Git, as Baude's side panel has them. Either side shown or hidden at once (Ctrl+B,
    Ctrl+Alt+B); Ctrl+M shows Meta, its box focused, or closes it."""

    def do_snapshot(self, snapshot):
        self.snapshot_fading(snapshot, Adw.ApplicationWindow)  # (theming.AccentFade: the accent changed, fading)

    def __init__(self, app):
        super().__init__(application=app, title=noun(), default_width=1320, default_height=860)
        self.store, self.current = Store(), None
        self.notes = NotesNames(self.store, lambda: self.current)  # (the notebooks and their pages, for @ and ⌘K)
        self.card, self.card_going = None, None  # (the name's card showing: ref_card; its going: hover_left)
        self.switching = False  # (another notebook's tabs coming in: _show)
        self.shown_order = []  # (the tabs as they were shown, the last last: reader, diary, code_page)
        split = Gtk.Box()
        # left: the notebooks, New Notebook at the top as Baude's New session is, and under them the open one's pages,
        # which fold away; its toggle in the window's bar, by the window's buttons, as Baude's sessions' is
        side = self.books = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT, width_request=FixedWidth.LEAST)
        side.add_css_class("record-books")
        side.add_css_class("baude-frame")  # (the frame, as the bars and the right bar are: theming.CSS)
        side_toggle = self._sidebar_toggle()
        books = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        books.add_css_class("session-sidebar")  # (Baude's look: its group headers, its compact rows)
        self.pages = PagesList(self)  # (first: pages are gone to more often than notebooks)
        # (over it all: what's in the way of the host picked, as Baude's banner over a host's chats says it)
        self.host_note = chatparts.HostState()
        books.append(self.host_note)
        books.append(self.pages)
        self.books_head = markdownview.group_header(book(plural=True), self.store.books_folded,
                                               lambda: self.fold_books(not self.store.books_folded),
                                               plus=("list-add-symbolic", f"New {book()} (Ctrl+N)", self.new_notebook))
        self.books_head.set_margin_start(12)
        self.books_head.set_margin_end(8)
        self.books_head.set_margin_top(10)
        # (beside it, before its +: the host whose notebooks are listed, by its name (this machine's local), as Baude's
        # New session has its hosts; kept in records.json, shown once that host's there: pick_host, on_network)
        self.host_dd = pickers.ellipsize_dropdown(pickers.ChoiceDropDown(
            [(HERE_HOST, THIS_MACHINE)], f"The host whose {book(plural=True).lower()} are listed", self.pick_host), 12)
        self.host_dd.add_css_class("flat")
        self.host_dd.add_css_class("notes-host")
        self.host_dd.set_valign(Gtk.Align.CENTER)
        if (popup := next((w for w in widgets.walk(self.host_dd) if isinstance(w, Gtk.Popover)), None)) is not None:
            popup.connect("show", lambda *_: (self.start_network(), network().look()))  # (opened: the hosts looked
            # for, one started a moment ago offered)
        self.books_head.insert_child_after(self.host_dd, self.books_head.arrow.get_next_sibling())
        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)  # (right-click: Deactivate: host_menu)
        right.connect("pressed", lambda _g, _n, x, y: not (popup and popup.get_visible()) and self.host_menu(x, y))
        self.host_dd.add_controller(right)
        self.books_head.name.set_ellipsize(Pango.EllipsizeMode.NONE)  # (Notebooks whole: the host's name gives way)
        self.sends, self.sent = {}, {}  # (by host: changes sent it, not answered yet; sent it at all, this run)
        self.list_again, self.asked = set(), set()  # (hosts to list once what's sent is answered; being listed)
        self.host_up, self.net_on = {}, False  # (by host: there, as last seen; the hosts looked for: start_network)
        self.notes_seen = {}  # (by host: its notebooks as last listed here, its info's notes: on_network)
        self.listed_at, self.list_later = {}, set()  # (by host: when it was last asked; hosts to ask in a moment)
        self.list_whole = set()  # (hosts to list whole next, not only what's changed: list_notes)
        self.edits_seen = {}  # (by host: its info's edits as its edits were last asked for: fetch_edits)
        self.handed = set()  # (hosts handed the edits this Record kept for them: hand_edits)
        self.deciding = set()  # (edits whose decision's being sent their host: send_unsent)
        books.append(self.books_head)
        self.notebook_list = Gtk.ListBox()
        self.notebook_list.add_css_class("navigation-sidebar")
        self.notebook_list.add_css_class("notebook-sidebar")
        self.notebook_list.connect("row-selected", self._selected)
        keys = Gtk.EventControllerKey()  # (F2, as Baude's list renames a chat)
        keys.connect("key-pressed", lambda _c, kv, _k, _s: (kv == Gdk.KEY_F2 and self.rename()) or kv == Gdk.KEY_F2)
        self.notebook_list.add_controller(keys)
        self.books_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.notebook_list,
                                               propagate_natural_height=True)
        self.books_reveal = Gtk.Revealer(child=self.books_scroll, transition_type=Gtk.RevealerTransitionType.NONE,
                                         reveal_child=not self.store.books_folded)
        books.append(self.books_reveal)
        side.set_content(books)
        # (at the foot, under a rule from the notebooks: Update, while a new version waits: update_waiting)
        self.update_bar = chatparts.UpdateBar(self.update_now)
        side.add_bottom_bar(self.update_bar)
        self.left_sizes()
        # (held at its width: as it'd lay itself out, a long notebook's or page's name in full makes it wider; a name
        # too long ends in …. Not an Adw.Clamp: one takes the width the column asks for and centres it in that)
        column = FixedWidth(side, self.store.books_width or self.BOOKS_WIDTH)
        column.resized = self._books_resized
        split.append(column)
        # the middle: what's open, a tab each, their bar at the top of it, in the inside with what they show: the tabs
        # and the page they're over one island set into the frame, as Baude's chat has its tabs
        # (⌥⌘← ⌥⌘→: back and forward through them)
        self.tabs = panels.MainTabs(closed=self._tab_closed, reopen=self.reopen_tab, copy_links=True,
                                     rename=(lambda c: isinstance(c, Reader) and c.chat, self.ask_rename_chat))
        self.tabs.view.connect("notify::selected-page", lambda *_: self._tab_shown())
        self.tabs.prepend(self.tabs.bar)
        main = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT, hexpand=True)
        main.add_css_class("record-content")
        main.add_css_class("baude-frame")
        # the window's bar, all the way across over the three, as Baude's: the notebooks' toggle at its start, by the
        # window's buttons, the notebook open in its middle (as Baude's has the chat's name), and the right bar's toggle
        # and ☰ at its end. Nothing a side bar's dragged over hides them
        head = Adw.HeaderBar()
        head.pack_start(side_toggle)
        # (the name in the text's own weight, as Baude's chat's is: .title for a GTK theme's title bar)
        self.title_label = Gtk.Label(max_width_chars=48, ellipsize=Pango.EllipsizeMode.END,
                                     css_classes=["title", "baude-title"])
        head.set_title_widget(self.title_label)
        self.show_title()
        menu = Gio.Menu()
        menu.append("Open a Chat…", "win.openchat")
        menu.append(markdownview.COPY_LINK, "win.copylink")
        menu.append(wording.say("window.copy_transcript"), "win.copyreport")
        view = Gio.Menu()
        view.append(wording.say("meta_settings.full_width"), "win.fullwidth")
        menu.append_section(None, view)
        tools = Gio.Menu()  # (Baude's views over the window; Files and Git are in the right bar, code in tabs)
        for title, page in ((wording.say("window.kubernetes"), "kube"),
                            (wording.say("window.database_schemas"), "db")):
            tools.append(title, f"win.viewer::{page}")
        tools.append(wording.say("multiplex.terminal_menu"), "win.viewer::terminal")
        menu.append_section(None, tools)
        app_menu = Gio.Menu()
        item = Gio.MenuItem.new(wording.say("window.settings"), "win.settings")
        item.set_attribute_value("accel", GLib.Variant.new_string(widgets.SETTINGS_KEY))
        app_menu.append_item(item)
        app_menu.append(wording.say("changelog.title"), "win.changelog")
        app_menu.append(wording.say("mesh_versions.title"), "win.mesh")  # (Baude's too)
        item = Gio.MenuItem.new(wording.say("fixes.send_feedback"), "win.feedback")
        item.set_attribute_value("accel", GLib.Variant.new_string(widgets.FEEDBACK_KEY))
        app_menu.append_item(item)
        app_menu.append(wording.say("window.ongoing_feedback"), "win.fixes")
        app_menu.append(wording.say("code_apart.restart_app", app=noun()), "win.restart")
        app_menu.append(wording.say("code_apart.restart_daemon"), "win.restartdaemon")
        menu.append_section(None, app_menu)
        menu_btn = self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        for name, fn in (("openchat", self.choose_chat), ("copylink", self.tabs.copy_link), ("copyreport", self.copy_report),
                         ("rename", self.rename),
                         ("export", lambda: self.current and self.ask_export([self.current], self.current["name"])),
                         ("exportall", lambda: self.ask_export(self.store.notebooks, book(plural=True))),
                         ("delete", self.delete),
                         ("new", lambda *_: self.new_notebook()), ("restart", lambda: self.restart()),
                         ("restartdaemon", lambda: self.restart_host()),
                         ("settings", lambda: Settings(self).present(self)), ("feedback", lambda: self.open_feedback()),
                         ("fixes", lambda: self.open_fixes()),
                         ("changelog", lambda: update.changelog_dialog().present(self)),
                         ("mesh", lambda: meshui.mesh_dialog(network()).present(self))):
            a = Gio.SimpleAction(name=name)
            a.connect("activate", lambda _a, _p, fn=fn: fn())
            self.add_action(a)
        wide = Gio.SimpleAction.new_stateful("fullwidth", None, GLib.Variant.new_boolean(self.store.full_width))
        wide.connect("change-state", Window._full_width_toggled, weakref.ref(self))  # (no closure over the window)
        self.add_action(wide)
        spell = Gio.SimpleAction.new_stateful("spellcheck", None, GLib.Variant.new_boolean(self.store.spell_check))
        spell.connect("change-state", Window._spell_check_toggled, weakref.ref(self))
        self.add_action(spell)
        back_up = Gio.SimpleAction.new_stateful("backupall", None, GLib.Variant.new_boolean(self.store.backup_all))
        back_up.connect("change-state", Window._backup_all_toggled, weakref.ref(self))
        self.add_action(back_up)
        viewer = Gio.SimpleAction.new("viewer", GLib.VariantType.new("s"))
        viewer.connect("activate", lambda _a, page, ref: (w := ref()) and w.open_viewer(page.get_string()),
                       weakref.ref(self))
        self.add_action(viewer)
        off = Gio.SimpleAction.new("deactivate_host", GLib.VariantType.new("(sb)"))
        off.connect("activate", lambda _a, v, ref: (w := ref()) and w.deactivate_host(*v.unpack()), weakref.ref(self))
        self.add_action(off)
        app.set_accels_for_action("win.new", ["<primary>n"])
        app.set_accels_for_action("win.copylink", [markdownview.COPY_LINK_KEY])
        launcher = Gio.SimpleAction.new_stateful("launcher", None, GLib.Variant.new_boolean(installed()))
        launcher.connect("change-state", Window._launcher_toggled)
        self.add_action(launcher)
        self.meta_from = None  # (where the focus was last outside the right bar: close_meta)
        keys = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)  # (as Baude's: its keys for the same things)
        # (as Baude's, before the focused widget, as macOS's text boxes take Ctrl+B / Ctrl+F as Emacs's back / forward;
        # ⌘F too, as Ctrl+F: the find bar of the text the focus is in first, else what's over it, else the tab's)
        find = lambda: find_in_focus(self) or self.kube.find() or self.schema.start_find() or \
            ((chat := chatfind.ChatFind.for_focus(self)) is not None and chat.open()) or self.find_in_code()
        widgets.first_keys(self, (("<Control>b F9", lambda: self.left_slide.toggle()),
                                  (" ".join(m + "f" for m in widgets.ZOOM_MODS), find)))
        for triggers, fn in (("<Control><Alt>b", lambda: self.right_slide.toggle()),
                             (widgets.META_KEY, lambda: self.toggle_meta()),
                             (widgets.zoom_keys("w"), lambda: self.tabs.close_current()),
                             (widgets.NAV_KEYS["back"], lambda: self.tabs.back()),  # (⌥⌘ ← → ↑: a browser's
                             (widgets.NAV_KEYS["forward"], lambda: self.tabs.forward()),  # back, forward, up)
                             (widgets.NAV_KEYS["up"], lambda: self.go_up()),
                             (widgets.SETTINGS_KEY, lambda: Settings(self).present(self)),
                             (pickers.PALETTE_KEY, lambda: self.open_palette()),
                             (widgets.zoom_keys("plus", "<Shift>plus", "equal", "KP_Add"),
                              lambda: self.zoom_code(+1) or self.set_zoom(self.store.zoom + widgets.ZOOM_STEP)),
                             (widgets.zoom_keys("minus", "underscore", "KP_Subtract"),
                              lambda: self.zoom_code(-1) or self.set_zoom(self.store.zoom - widgets.ZOOM_STEP)),
                             (widgets.zoom_keys("0", "KP_0"),
                              lambda: self.zoom_code(0) or self.set_zoom(self.store.zoom_default))):
            for trigger in triggers.split():
                keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(trigger),
                                                   Gtk.CallbackAction.new(lambda *_, fn=fn: fn() or True)))
        self.add_controller(keys)
        self.zoom_css = Gtk.CssProvider()  # (its sizes, a class each: the notes' page, the chat read, the conversation)
        self.zoom_css.load_from_string(ZOOM_CSS)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self.zoom_css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        self.zoom_wait = self.zoom_saving = None  # (the frame to zoom in; its saving: set_zoom)
        self.rolling, self.zipping = GLib.timeout_add_seconds(Store.BACKUP_EVERY, self.roll_backup), False  # (roll_backup)
        self.scale_wait, self.text_scaled = None, False  # (the sizes applied in a moment; the text's among them: set_scale)
        # right: Meta (your conversation with it), Files and Git, as Baude's side panel has them
        self.chat = RecordChat(self)
        self.apply_zoom()
        self.right_stack = Adw.ViewStack(vexpand=True)
        widgets.ensure_icons()  # (baude-meta-symbolic, baude-claude-symbolic: Baude's own icons)
        self.right_stack.add_titled_with_icon(self.chat, "meta", meta.NAME, "baude-meta-symbolic")
        self.add_viewer_pages(self.right_stack, code=False)  # (Files and Git; code opens in a tab: show_code)
        right = self.right = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        right.add_css_class("baude-frame")
        # (its pages' switcher at the top of it, under the window's bar, as Baude's side panel has its own)
        right.add_top_bar(Adw.ViewSwitcher(stack=self.right_stack, policy=Adw.ViewSwitcherPolicy.NARROW,
                                           margin_top=6, margin_bottom=6, halign=Gtk.Align.CENTER))
        right.set_content(self.right_stack)
        right.set_size_request(340, -1)
        self.right_toggle = Gtk.ToggleButton(icon_name="sidebar-show-right-symbolic", active=True,
                                             tooltip_text=f"{meta.NAME}, Files and Git (Ctrl+Alt+B)")
        head.pack_end(menu_btn)
        head.pack_end(self.right_toggle)
        self.content_stack = Gtk.Stack()
        none = Adw.StatusPage(icon_name="system-users-symbolic", title=f"Make a {book()}",
                              description=f"A {book()} is your notes on a subject; {meta.NAME}, in the right bar, "
                                          "reads them and AI prose with you, and organises them.")
        b = Gtk.Button(label=f"New {book()}", halign=Gtk.Align.CENTER)
        b.add_css_class("pill")
        b.add_css_class("suggested-action")
        b.connect("clicked", lambda *_: self.new_notebook())
        none.set_child(b)
        self.content_stack.add_named(widgets.inside(none, under_bar=True), "none")
        self.content_stack.add_named(widgets.inside(Gtk.Box(), under_bar=True), "empty")
        self.content_stack.add_named(widgets.inside(self.tabs, under_bar=True), "tabs")
        beside = Gtk.Overlay(child=self.content_stack)  # (the left edge's grip goes on past the column: FixedWidth.grip_past)
        beside.add_overlay(column.grip_past(split))
        main.set_content(beside)
        main.add_bottom_bar(self.make_capture_bar())  # (feedback.FeedbackHost: Capture while one's pending)
        # (the right bar's rule stops where the notes are at their least: it never goes over them, nor does the
        # column's edge, dragged, push them under it: FixedWidth.limited)
        self.paned = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=main, end_child=right,
                                          resize_start_child=True, resize_end_child=False, shrink_start_child=False,
                                          shrink_end_child=False, hexpand=True, position=100_000))
        self.paned.add_css_class("main-split")  # (its rule unseen: the inside's edge is the line; still dragged)
        self.paned.add_css_class("inside-before")
        split.append(self.paned)
        self.right_slide = panels.PaneSlide(self.paned, right, [self.right_toggle])
        self.connect("notify::focus-widget", Window._focus_moved)
        self.left_slide = ColumnSlide(column, side, [side_toggle])
        column.slide = self.left_slide
        self.left_slide.moving = self.right_slide.moving = self._side_moving
        framed = Adw.ToolbarView(content=split)
        framed.add_css_class("baude-frame")  # (the bar on the frame, as the columns are: theming.CSS)
        framed.add_top_bar(head)
        # (this machine's host process on other code than Record's: show_host_apart)
        self.apart_banner = Adw.Banner(revealed=False)
        self.apart_banner.connect("button-clicked", lambda *_: self.act_host_apart())
        framed.add_top_bar(self.apart_banner)
        self.toasts = Adw.ToastOverlay(child=self.viewer_overlays(framed))  # (Kubernetes, Databases: over it all)
        self.set_content(self.toasts)
        self.fill_sidebar()
        chats = list(self.store.chats)  # (taken first: showing a notebook keeps what's open, and none is yet)
        first = self.notebook_list.get_row_at_index(0)
        if first:
            self.notebook_list.select_row(first)
        else:
            self._show(None)
            self.chat.show(None)  # (Meta's conversation, as it was left)
        for chat in chats:  # (the chats open when it last closed, back in their tabs)
            if os.path.exists(chat.get("path") or ""):
                self.open_chat(chat["path"], select=False, present=False)
        self.apply_full_width(self.store.full_width)
        self.connect("close-request", lambda *_: self.store.save_now() and False)
        self.connect("notify::is-active", Window._activated)  # (a plain function: no closure over the window)
        self.store.sender = self.send_notes
        self.host_dd.set_choices(self.note_hosts())
        self.host_dd.select(self.store.picked)
        self.start_network()  # (this machine's host process too: the edits waiting on its notebooks' pages are its)
        self.start_host_here()  # (started if it isn't running, and its edits fetched once it's there)
        if self.store.picked != HERE_HOST:  # (another host picked when it last closed: its notebooks, there or not)
            self.show_host(self.store.picked)
        meshui.watch_github(self, Window.github_folders, Window._github_changed)
        update.watch_input(self)  # (what update.quiet reads: a new version waits while you're at it)
        theming.warm_shaders(self)  # (after its first frame)

    BOOKS_WIDTH = DiaryEditor.PAGES_WIDTH  # the notebooks' column, in px: the pages' column's width

    def _side_moving(self, on):
        """A side bar's started moving (sliding, or dragged), or it's still again. A chat read in the tab shown (Reader)
        is all of it laid out again at each new width, and a long one took a frame or more for each: so while the bar
        moves its messages far from the view are hidden (ZoomPlace, as for the zoom), and what's in view is all that's laid
        out again; once it's still they come back, a few a frame, nearest first, its place kept. (A page of notes lays out
        only what's in view already.)"""
        place = getattr(self.tabs.current(), "place", None)
        if place is None or isinstance(place, DiaryPlace):
            return
        if on:
            place.through()
        place.paused = on

    def _books_resized(self, width):
        """The notebooks' column dragged wider or narrower: kept."""
        self.store.books_width = width
        self.store.save()

    def fold_books(self, folded):
        """The notebooks' list folded away (its header's arrow), or shown again; kept."""
        self.store.books_folded = folded
        self.books_reveal.set_reveal_child(not folded)
        self.books_head.set_collapsed(folded)
        self.store.save()
        self.left_sizes()

    def left_sizes(self):
        """The left column's height shared: the pages take what's spare while they're shown, the notebooks their
        own height, up to a few rows' (more scroll); with the pages folded, the notebooks what's spare."""
        if not hasattr(self, "books_scroll"):
            return
        pages = not self.store.pages_folded
        self.books_scroll.set_max_content_height(240 if pages else -1)
        self.books_scroll.set_vexpand(not pages)
        self.books_reveal.set_vexpand(not pages and not self.store.books_folded)

    @staticmethod
    def _sidebar_toggle():
        """The notebooks shown or hidden (both toggles, the notebooks' bar's and the content's, are the one switch)."""
        return Gtk.ToggleButton(icon_name="sidebar-show-symbolic", active=True, tooltip_text=book(plural=True))

    def state(self):
        """What a window made again puts back (restart): the Record shown, its tab, the window's
        size, the question being written with its quote and its caret, on a page of notes (its text is kept as it's
        written) its caret and a new title not yet taken, and where each open page and chat and the Record's own
        conversation have been read to."""
        shown = self.tabs.current()
        note = shown if isinstance(shown, DiaryEditor) else None
        focus = self.get_focus()
        return {"record": self.current["id"] if self.current else None,
                "tab": list(shown.tab_key) if shown is not None and shown.tab_key[0] != "code" else None,
                "size": [self.get_width(), self.get_height()], "maximized": self.is_maximized(),
                "frame": window_frame(self),  # (macOS: the display it's on, where; theming.put_back)
                "right": self.paned.get_width() - self.paned.get_position() if self.right.get_visible() else None,
                "right_page": self.right_stack.get_visible_child_name(),
                "fixes": self.tabs.get(("fixes",)).state() if self.tabs.get(("fixes",)) else None,
                "composer": widgets.typed_text(self.chat.composer), "composer_caret": widgets.caret_of(self.chat.composer),
                "quote": self.chat.quote, "note_caret": widgets.caret_of(note.view) if note else None,
                "note_title": note.title.get_text() if note else None,
                "focus": "composer" if focus is self.chat.composer else "note" if note and focus is note.view else None,
                "places": [[list(c.tab_key), c.place.saved() if hasattr(c, "place") else panels.scroll_place(c.scroll)]
                           for c in self.tabs.children()
                           if isinstance(c, (DiaryEditor, Reader))],
                "chat_place": panels.scroll_place(self.chat.scroll)}

    def restore(self, state):
        if state.get("size") and all(state["size"]):
            self.set_default_size(*state["size"])
        theming.put_back(self, state.get("frame"), state.get("maximized"))  # (where it was, on macOS)
        if state.get("right"):  # (the right bar's width, once the window's laid out)
            GLib.idle_add(lambda: self.paned.set_position(max(0, self.paned.get_width() - state["right"])) and False)
        if state.get("right_page") and self.right_stack.get_child_by_name(state["right_page"]) is not None:
            self.right_stack.set_visible_child_name(state["right_page"])  # (a page there's no longer: Meta's left)
        if state.get("record") and any(t["id"] == state["record"] for t in self.store.notebooks):
            self.select(state["record"])
            if state.get("fixes"):
                self.open_fixes(select=False).restore(state["fixes"])
            if state.get("tab"):
                self.tabs.show(tuple(state["tab"]))
            self.chat.composer.get_buffer().set_text(state.get("composer") or "")
            widgets.put_caret(self.chat.composer, state.get("composer_caret"))
            self.chat.set_quote(state.get("quote") or "")
            note = self.tabs.current()
            if isinstance(note, DiaryEditor):
                widgets.put_caret(note.view, state.get("note_caret"))
                if state.get("note_title"):
                    note.title.set_text(state["note_title"])
            for key, place in state.get("places") or ():
                if isinstance(tab := self.tabs.get(tuple(key)), (DiaryEditor, Reader)):
                    panels.hold_place(tab.scroll, place)
            panels.hold_place(self.chat.scroll, state.get("chat_place"))
            focus = self.chat.composer if state.get("focus") == "composer" else \
                note.view if state.get("focus") == "note" and isinstance(note, DiaryEditor) else None
            if focus is not None:  # (once it's shown: you go on typing)
                GLib.idle_add(lambda: focus.grab_focus() and False)

    def idle(self):
        """Nothing a restart would cut short or take from under you (update.Updater): restart_blockers."""
        return not self.restart_blockers()

    def restart_blockers(self):
        """What a restart waits for, said (the Update bar's tooltip), [] for none: a question being answered,
        a dialog or capture open, and you at it (the window you're in, something typed, clicked or scrolled in it in the
        last minute)."""
        why = ["a question being answered"] if self.chat.ask is not None else []
        bar = getattr(self, "capture_bar", None)
        if self.get_visible_dialog() is not None or (bar is not None and bar.get_reveal_child()):
            why.append(wording.say("window.blocker_dialog"))
        if not update.quiet(self):
            why.append(wording.say("window.blocker_using"))
        return why

    def update_waiting(self, waiting):
        """(update.Updater's) A new version found and waiting on idle, or not: the bar at the notebooks' foot."""
        self.update_bar.say(self.restart_blockers() if waiting else None)

    def update_now(self):
        """The bar clicked: restarted onto the new version now (a question being answered stopped, said and asked)."""
        u = getattr(self.get_application(), "updater", None)
        if u is None or not u.ready:
            return
        go = lambda: self.restart({"from": u.started, "to": u.ready,  # noqa: E731
                                   "commits": update.new_commits(u.started, u.ready, u.root)})
        if self.chat.ask is None:
            return go()
        dlg = Adw.AlertDialog(heading=wording.say("window.restart_now_heading"),
                              body="The question being answered is stopped.", default_response="cancel",
                              close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("go", wording.say("window.restart_now"))
        dlg.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "go" and go())
        dlg.present(self)

    def restart(self, updated=None):
        """☰ → Restart Record, as Baude's, or a new version found (update.Updater; updated, what by): this process
        replaced by a fresh Record (a new version picked up), which puts back what this one showed (restart_path;
        state) and gives the front back to the app you were in (update.restarted)."""
        self.store.save_now()
        with open(restart_path(), "w") as f:
            json.dump({**self.state(), "front": update.front_app(), "updated": updated}, f)
        app = self.get_application()
        if app is not None and app.server is not None:
            app.server.close()  # (the new one listens in its place)
        script = os.path.join(os.path.dirname(os.path.realpath(__file__)), "record.py")  # (beside this file)
        os.execv(sys.executable, [sys.executable, script])

    # ---- sizes (Settings…), zoom (Ctrl/⌘ + − 0), the launcher, feedback
    ZOOM_SAVE_MS = 1000  # (the zoom written this long after the last step: a save writes every notebook)

    def set_zoom(self, zoom):
        """The notes', the chat's and the conversation's text this much bigger (1.0 the usual), kept. It's applied as
        the next frame begins: however many key presses there were since the last one, it's one change of size."""
        zoom = round(min(widgets.ZOOM_MAX, max(widgets.ZOOM_MIN, zoom)), 2)
        if zoom == self.store.zoom:
            return
        self.store.zoom = zoom
        if self.zoom_saving is not None:
            GLib.source_remove(self.zoom_saving)
        self.zoom_saving = GLib.timeout_add(self.ZOOM_SAVE_MS, self._zoom_saved)
        clock = self.get_frame_clock()
        if clock is None:  # (not on screen: no frame to wait for)
            self.apply_zoom()
        elif self.zoom_wait is None:
            self.zoom_wait = clock.connect("before-paint", self._zoom_frame)
            clock.request_phase(Gdk.FrameClockPhase.BEFORE_PAINT)

    def _zoom_frame(self, clock):
        clock.disconnect(self.zoom_wait)
        self.zoom_wait = None
        self.apply_zoom()

    def _zoom_saved(self):
        self.zoom_saving = None
        self.store.save()
        return False

    def set_markup(self, mode):
        """Settings → Markup visibility: the notes' markup shown on the caret's line, never, or always (but a link's); every
        page open drawn so, and it kept."""
        self.store.markup = mode
        self.store.save()
        for child in self.tabs.children():
            if isinstance(child, DiaryEditor):
                child._highlight()

    def set_zoom_default(self, zoom):
        """Settings → Global Scale (Baude's too, settings.json's): what Ctrl/⌘ 0 goes back to; and the zoom now."""
        self.store.zoom_default = zoom
        widgets.save_settings(zoom_default=zoom)
        self.store.save()  # (the zoom may be at it already)
        self.set_zoom(zoom)

    def settings_changed(self, keys, _before):
        """Shared settings changed in another app here (theming.watch_settings): the text's and the icons' sizes; the
        Global Scale, the zoom with it if it was at the one before (one zoomed apart stays as it is). (The accent's
        taken already; Meta's are meta.settings_changed's.)"""
        if keys & {"text_scale", "icon_scale"}:
            self.text_scaled = self.text_scaled or "text_scale" in keys
            if self.scale_wait is not None:
                GLib.source_remove(self.scale_wait)
            self.scale_wait = GLib.timeout_add(150, self._scales_changed)
        if "zoom_default" in keys:
            zoom = round(min(widgets.ZOOM_MAX, max(widgets.ZOOM_MIN,
                                                   float(widgets.load_settings().get("zoom_default") or 1.0))), 2)
            following = self.store.zoom == self.store.zoom_default
            self.store.zoom_default = zoom
            self.store.save()
            if following:
                self.set_zoom(zoom)

    def apply_zoom(self):
        """The zoom on the conversation and on the tab shown, each keeping its place. A tab not shown has its turn as
        it's shown (_tab_shown), before its first frame: until then nothing in it is styled or laid out again."""
        name = zoom_class(self.store.zoom)
        self.chat.place.zoom(name)
        set_zoom_class(self.chat.composer, name)
        set_zoom_class(self.chat.quote_box, name)
        self.zoom_tab(self.tabs.current())

    def zoom_tab(self, tab):
        if isinstance(tab, (DiaryEditor, Reader)):
            tab.place.zoom(zoom_class(self.store.zoom))

    def set_scale(self, key, size):
        """Settings → Size: the text's or the icons' (Baude's settings.json, so Baude's too), applied app-wide a moment
        after the last change (the text's restyles every window: once for a few quick clicks)."""
        widgets.save_settings(**{key: size})
        self.text_scaled = self.text_scaled or key == "text_scale"
        if self.scale_wait is not None:
            GLib.source_remove(self.scale_wait)
        self.scale_wait = GLib.timeout_add(150, self._scales_changed)

    def _scales_changed(self):
        """Every widget's size changes with the text's (widgets.apply_scales: the display's font resolution), not only
        what has the zoom's class: what's shown keeps its place, and the tabs not shown wait till they are."""
        self.scale_wait = None
        if self.text_scaled:
            self.text_scaled = False
            shown = self.tabs.current()
            for tab in self.tabs.children():
                if isinstance(tab, (DiaryEditor, Reader)):
                    tab.place.through() if tab is shown else tab.place.put_away()
            self.chat.place.through()
        widgets.apply_scales(self.get_display())
        return False

    @staticmethod
    def _launcher_toggled(action, value):
        """Settings → Show in Applications: all four apps' launchers made, or taken away."""
        try:
            install() if value.get_boolean() else uninstall()
        except OSError as e:
            print("record: the launcher:", e, file=sys.stderr)
        action.set_state(GLib.Variant.new_boolean(installed()))

    def capture_areas(self):
        """The window's parts, by name, for the text a feedback capture records."""
        areas = {self.notebook_list: "notebooks", self.pages.index: "pages", self.chat: "conversation",
                 self.kube: "Kubernetes", self.schema: "databases", self.lightbox: "image",
                 self.terminal: "terminal"}
        for child in self.tabs.children():
            if isinstance(child, DiaryEditor):
                areas[child.view] = f"notes: {child.page}"
            elif isinstance(child, Reader):
                areas[child] = "chat"
        return areas

    def feedback_context(self):
        """What a note sent from Record says it came from (feedback.save_feedback): the notebook, the chat read, its
        folder, and Record."""
        t, folders, chat = self.current or {}, self.read_folders(), self.chat_read() or {}
        return [f"- session: {os.path.splitext(os.path.basename(chat.get('path') or '-'))[0]} "
                f"({book()} {full_name(t) if t else '-'})",
                wording.say("report.folder_line", folder=folders[0] if folders else '-'),
                f"- Record: {os.path.join(os.path.dirname(os.path.realpath(__file__)), 'record.py')}"]

    def feedback_transcripts(self):
        return feedback.report_lines([(wording.say("report.the_chat"), self.report), ("Meta", self.chat.report)])

    def report(self, most=None):
        """The chat open as text for an issue (Copy Transcript for an Issue; a note sent with Send Feedback): its model
        and folder, then its transcript, each tool call with its input and result (meta.transcript_report); None if no
        chat's open."""
        chat = (self.reader.chat if self.reader is not None else None) or {}
        if not (path := chat.get("path")) or not os.path.exists(path):
            return None
        head = [(wording.say("report.app"), "Record"),
                (wording.say("chats.chat"),
                 f"{os.path.splitext(os.path.basename(path))[0]} ({chat.get('title') or '-'})"),
                (wording.say("common.model"), transcript_model(path) or ""),
                (wording.say("common.folder"), chat.get("cwd") or ""),
                (wording.say("report.copied"), time.strftime("%Y-%m-%d %H:%M:%S %z"))]
        return meta.transcript_report(head, path, most=most)

    def copy_report(self):
        if (text := self.report()) is None:
            return widgets.toast(self, "No chat open")
        self.get_clipboard().set(text)
        widgets.toast(self, wording.say("window.transcript_copied"))

    def apply_full_width(self, on):
        """☰ → Full-width text, as Baude's: the chat read and the diary across the whole pane, not a column. (Adw.Clamp
        can't be switched off, so on is a limit wider than any screen.)"""
        for child in self.tabs.children():
            if isinstance(child, (Reader, DiaryEditor)):
                child.clamp.set_maximum_size(100_000 if on else child.WIDTH)
                child.clamp.set_tightening_threshold(100_000 if on else 400)

    @staticmethod
    def _spell_check_toggled(action, value, ref):
        action.set_state(value)
        if (win := ref()) is not None:
            win.store.spell_check = value.get_boolean()
            win.store.save_now()
            for child in win.tabs.children():
                if isinstance(child, DiaryEditor):
                    child.spell.refresh()

    @staticmethod
    def _backup_all_toggled(action, value, ref):
        action.set_state(value)
        if (win := ref()) is not None:
            win.store.backup_all = value.get_boolean()
            win.store.save_now()

    def ask_export(self, notebooks, name):
        """Settings → Export…: whether the glossary and the chats go too, then where the zip goes. Over Settings, which is
        still there if either's cancelled."""
        d = Adw.AlertDialog(heading=f"Export {name}")
        extras = Gtk.CheckButton(label="Include glossary and chats", tooltip_text=(
            "What's shown of the chats named in its notes, and what's been looked up in their folders"))
        d.set_extra_child(extras)
        d.add_response("cancel", wording.say("common.cancel"))
        d.add_response("ok", "Export…")
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")
        d.connect("response", lambda _d, r: r == "ok" and self.choose_zip(notebooks, name, extras.get_active()))
        d.present(self)

    def choose_zip(self, notebooks, name, extras):
        def chosen(dialog, result):
            try:
                path = dialog.save_finish(result).get_path()
            except GLib.Error:  # (cancelled)
                return
            if isinstance(settings := self.get_visible_dialog(), Settings):  # (it's said over the window: export)
                settings.close()
            self.export(notebooks, path, extras)
        Gtk.FileDialog(title="Export", modal=True, initial_name=f"{file_part(name)}.zip").save(self, None, chosen)

    def export(self, notebooks, path, extras, done=None):
        """A zip made off the main loop (export_zip), of the notebooks as they are now. done(error): when it's made, or else
        said here."""
        copy = json.loads(json.dumps(notebooks))

        def said(error):
            if done is not None:
                done(error)
            else:
                self.toast(html.escape(f"Couldn't export: {error}" if error else f"Exported to {path}"))  # (a toast's markup)
            return False

        def run():
            error = None
            try:
                export_zip(path, copy, extras)
            except Exception as e:  # (a thread: said, whatever it was)
                error = str(e) or repr(e)
            GLib.idle_add(said, error)
        threading.Thread(target=run, daemon=True).start()

    def roll_backup(self):
        """Every BACKUP_EVERY, all the notebooks in the one zip in place of the last (backup_zip), if they've changed. With the
        glossary and the chats (Settings) they may have whether or not the notebooks have. With nothing written anywhere it's
        left as it is: that's more likely notes that couldn't be read (Store) than notes that are gone."""
        written = any(text.strip() for notebook in self.store.local for text in notebook["pages"].values())
        if written and not self.zipping and (self.store.unzipped or self.store.backup_all):
            self.zipping, self.store.unzipped = True, False

            def done(error):
                self.zipping = False
                if error:
                    self.store.unzipped = True  # (the next time)
                    print(f"Record: the backup: {error}", file=sys.stderr)
            self.export(self.store.local, backup_zip(), self.store.backup_all, done)
        return True

    @staticmethod
    def _full_width_toggled(action, value, ref):
        action.set_state(value)
        if (win := ref()) is not None:
            win.store.full_width = value.get_boolean()
            win.store.save_now()
            win.apply_full_width(value.get_boolean())

    @staticmethod
    def _activated(win, _pspec):
        if win.is_active() and win.store.host != HERE_HOST:  # (come back to: another host's notebooks listed again)
            win.list_notes(win.store.host)
        if not win.is_active():
            win.store.flush()  # (gone to another window: what's typed is on the disk)

    def github_folders(self):
        """What GitHub's lists are read for (lookupgit.GitHub.look), as Baude's: the repository the chat read's folder
        is in."""
        return self.read_folders()

    @staticmethod
    def _github_changed(win):
        """GitHub's lists have changed: the pull requests' and issues' logos in what's open, where they stand now."""
        for child in win.tabs.children():
            if isinstance(child, (DiaryEditor, Reader)):
                child.github_changed()
        win.git_page.pulls_changed()

    # sidebar
    def _row(self, t):
        row = Gtk.ListBoxRow()
        row.notebook = t
        box = Gtk.Box(spacing=6, margin_start=6, margin_end=4)  # (a line, as a page's in the pages' list)
        row.name = pickers.FadeLabel(xalign=0, hexpand=True)  # (too long: it fades where it's cut)
        box.append(row.name)
        row.set_child(box)
        self._fill_row(row)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)  # (right-click: Rename…, Delete…, as Baude's chats)
        click.connect("pressed", lambda _g, _n, x, y: self.book_menu(row, x, y))
        row.add_controller(click)
        return row

    def book_menu(self, row, x, y):
        """A notebook's menu (right-click on it): Rename… and Delete…, for the one clicked (it's selected first)."""
        self.notebook_list.select_row(row)
        menu = Gio.Menu()
        menu.append(wording.say("common.rename_ellipsis"), "win.rename")
        menu.append(wording.say("common.delete_ellipsis"), "win.delete")
        pop = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        pop.set_parent(row)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    def _fill_row(self, row):
        row.name.set_text(full_name(row.notebook))
        row.name.set_attributes(None)
        chat = getattr(self, "chat", None)
        if chat is not None and (mark := chat.marks("notebook").get(row.notebook.get("id"))) is not None:
            metalook.mark_row(row, ("notebook", row.notebook.get("id")), mark, chat, row.name, inline=False)

    def mark_books(self):
        """The notebooks' rows as Meta's marks have them now (one it made, green, till it's kept or undone)."""
        i = 0
        while (r := self.notebook_list.get_row_at_index(i)) is not None:
            self._fill_row(r)
            i += 1

    def books_changed(self):
        """The notebooks' list as the store has them now (Meta made one, or took it away again): rows added and gone
        in place, so the one open stays selected; their marks again."""
        rows, i = {}, 0
        while (r := self.notebook_list.get_row_at_index(i)) is not None:
            rows[id(r.notebook)] = r
            i += 1
        keep = {id(t) for t in self.store.notebooks}
        for key, r in rows.items():
            if key not in keep:
                self.notebook_list.remove(r)
        for i, t in enumerate(self.store.notebooks):
            if id(t) not in rows:
                self.notebook_list.insert(self._row(t), i)
        self.mark_books()

    def fill_sidebar(self):
        while (r := self.notebook_list.get_row_at_index(0)) is not None:
            self.notebook_list.remove(r)
        for t in self.store.notebooks:
            self.notebook_list.append(self._row(t))

    def refresh_sidebar_row(self, notebook):
        i = 0
        while (r := self.notebook_list.get_row_at_index(i)) is not None:
            if r.notebook is notebook:
                self._fill_row(r)
            i += 1
        if notebook is self.current:  # (renamed here: the bar's name too)
            self.show_title()

    def show_title(self):
        """The window's bar's middle: the notebook open, by its name; none open, nothing (the app's name says
        nothing there)."""
        self.title_label.set_text(full_name(self.current) if self.current else "")
        self.title_label.set_tooltip_text(
            wording.say("window.rename_tip", thing=book().lower()) if self.current else None)

    def _selected(self, _lb, row):
        self._show(row.notebook if row else None)

    def _show(self, notebook):
        self.store.flush()  # (the notebook left is on the disk)
        if self.current is not None and self.current is not notebook and Store.is_blank(self.current):
            GLib.idle_add(self.drop_blank, self.current)  # (left blank: not kept; once the selection has settled)
        changed = notebook is not self.current
        self.current = notebook
        self.show_title()
        if changed:
            self.git_page.chat_changed()  # (the Git page's state is the notebook's, git_chat; its folder's the same)
        if changed:  # (the notebook left's pages' tabs, deleted ones too, closed: its own come in, as it had them open)
            self.switching = True
            try:
                for key in [k for k in list(self.tabs.pages) if k[0] in ("note", "gone")]:
                    self.tabs.close(key)
            finally:
                self.switching = False
        if not notebook:
            self.pages.show(None)
            self.content_stack.set_visible_child_name("none")
            if changed:
                self.chat.show(None)  # (Meta's tools on no notebook)
            return
        self.pages.show(notebook)
        if changed:
            shown = notebook.get("page") if notebook.get("page") in notebook["pages"] else HOME
            for name in [n for n in notebook.get("open") or [] if n in notebook["pages"] and n != shown] + [shown]:
                self.open_note(name, select=name == shown)
        for child in self.tabs.children():  # (the chats open: their nouns marked again, by this notebook's notes)
            if isinstance(child, Reader) and child.chat:
                child.show(child.chat, self.read_folders(child.chat))
        if self.reader is not None:
            self.reader.activate()
        self.chat.show(notebook)
        self._tab_shown()
        self.chat.composer.grab_focus()

    def check_host_code(self):
        """This machine's host process on other code than Record (meshui.HostApart, as Baude's): looked at, and said
        in the banner under the header once it's lasted, with the restart that puts them on the same code."""
        if (app := self.get_application()) is not None:
            host_apart(app).look(local_remote())

    def show_host_apart(self):
        """The banner as HostApart says it now (nothing, or what an Update bar says already: Record's, the host's)."""
        said = self.host_apart_said()
        if said is not None:
            self.apart_banner.set_title(said[1])
            self.apart_banner.set_button_label(said[2])
        self.apart_banner.set_revealed(said is not None)

    def host_apart_said(self):
        app = self.get_application()
        if app is None or not _HOST_APART:
            return None
        u = getattr(app, "updater", None)
        return _HOST_APART[0].now(local_remote(), "Record", u is not None and bool(u.ready))

    def act_host_apart(self):
        if (said := self.host_apart_said()) is not None:
            _HOST_APART[0].act(said[0], self.restart, self.restart_host)

    def reading_changed(self):
        """Another chat read (Store.reading): if its folder's another, the viewers, the names in the notes and
        GitHub's lists are that one's now, the notes' @names drawn again once it's catalogued."""
        folders = self.read_folders()
        if folders != getattr(self, "read_from", None):
            self.viewer_folder_changed()
            Window._github_changed(self)
            if (waiting := getattr(self, "names_wait", None)) is not None:
                GLib.source_remove(waiting)
            self.names_wait = self.space().refresh(self._names_in)
            if self.names_wait is None:
                self._names_in()
        self.read_from = folders

    # ---- what's open: a tab each (a page of notes, a chat, a file's code)
    def open_pages(self):
        """The notebook's pages open, in their tabs' order."""
        return [c.page for c in self.tabs.children() if isinstance(c, DiaryEditor)]

    def open_note(self, name, select=True):
        """A page of the notebook's (made if there's none yet: a [[link]] followed, New page) in a tab of its own,
        shown (select); one open already, shown. Returns its editor (None without a notebook)."""
        notebook = self.current
        if notebook is None:
            return None
        name = (name or "").strip() or HOME
        self.store.flush()  # (the page left is on the disk)
        if name not in notebook["pages"]:
            notebook["pages"][name] = ""
            self.store.save()
        editor = self.tabs.open(("note", name), lambda: self._note(notebook, name), short_page(name),
                                icon="accessories-dictionary-symbolic", tooltip=name, select=select)
        self._keep_open()
        self.pages.fill()
        return editor

    def _note(self, notebook, name):
        editor = DiaryEditor(self, notebook, name)
        set_zoom_class(editor, zoom_class(self.store.zoom))
        on = self.store.full_width
        editor.clamp.set_maximum_size(100_000 if on else editor.WIDTH)
        editor.clamp.set_tightening_threshold(100_000 if on else 400)
        return editor

    def open_chat(self, path, select=True, present=True):
        """A chat to read (an @chat in the notes, ⌘K, Open a Chat…, record.py --chat from Baude's Open in Record) in a
        tab of its own, whichever notebook's open, kept open for the next time Record starts; one open already,
        shown. Nothing's made or attached for it: a notebook has no chat. Returns its Reader."""
        path = os.path.realpath(path)
        title, cwd = session_info(path)
        chat = {"path": path, "title": title or "Untitled chat", "cwd": cwd}
        reader = self.tabs.open(("chat", path), lambda: self._reader(), chat["title"],
                                icon="baude-claude-symbolic", tooltip=f"{chat['title']} · {short_dir(cwd or path)}",
                                select=select)
        reader.show(chat, self.read_folders(chat))
        if select:
            self.store.reading = chat
            self.reading_changed()
        self._keep_open()
        self.store.save_now()  # (open for the next start, whatever happens next)
        if present:
            self.present()
        return reader

    def ask_rename_chat(self, reader):
        """Rename… on a chat's tab (right-click): its title, to type in (rename_chat)."""
        chat = reader.chat
        d = Adw.AlertDialog(heading="Rename chat")
        e = Gtk.Entry(text=chat.get("title") or "", activates_default=True, hexpand=True)
        d.set_extra_child(e)
        d.add_response("cancel", wording.say("common.cancel"))
        d.add_response("ok", wording.say("common.rename"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")
        d.connect("response", lambda _d, r: r == "ok" and (title := " ".join(e.get_text().split())) and
                  title != chat.get("title") and self.rename_chat(chat["path"], title))
        d.present(self)
        e.grab_focus()

    def rename_chat(self, path, title):
        """A chat renamed as Claude Code renames one: a custom-title line at its transcript's end; its tab and the
        chats' list say so."""
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": RecordChat.chat_id(path)},
                               separators=(",", ":")) + "\n")
        names = chat_names()
        names.at = 0
        names.refresh()
        if (reader := self.tabs.get(("chat", path))) is not None and reader.chat:
            reader.chat["title"] = title
            self.tabs.retitle(("chat", path), title, tooltip=f"{title} · {short_dir(reader.chat.get('cwd') or path)}")
        for c in self.store.chats:
            if c.get("path") == path:
                c["title"] = title
        self.store.save()

    def open_citation(self, url):
        """A citation link followed from the notes: its chat open, at its passage (Reader.show_passage)."""
        cite = parse_citation(url)
        if (path := session_path(cite["session"]) if cite else None) is None:
            return self.toast("That chat isn't on this computer")
        self.open_chat(path).show_passage(cite)

    def _reader(self):
        reader = Reader(self)
        set_zoom_class(reader.box, zoom_class(self.store.zoom))
        on = self.store.full_width
        reader.clamp.set_maximum_size(100_000 if on else reader.WIDTH)
        reader.clamp.set_tightening_threshold(100_000 if on else 400)
        return reader

    def show_code(self, path, line=None, text=None, note=None):
        """A file (or a version's text) in a tab of its own (one open already: shown, at line), as Baude shows code.
        False if it isn't something to read here (a folder, a binary or very big file): its opener then opens it in
        the editor."""
        if text is None:
            if os.path.isdir(path):
                return False
            try:
                with open(path, "rb") as f:
                    data = f.read(viewers.CODE_LIMIT + 1)
            except OSError:
                return False
            if len(data) > viewers.CODE_LIMIT or b"\0" in data[:8192]:
                return False
            text = data.decode("utf-8", "replace")
        name = os.path.basename(path) + (f" ({note})" if note else "")
        page = self.tabs.open(("code", os.path.realpath(path), note or ""), lambda: viewers.CodePage(self), name,
                              icon=widgets.themed_icon("text-x-script-symbolic", "text-x-generic-symbolic"),
                              tooltip=path, quiet=True)
        page.show(path, text, line, note)
        self.tabs.remember()  # (shown at another line: a step back and forward go through)
        self.present()
        return True

    def image_source(self):
        """What the large image view goes through with ← and →: the tab shown (a chat, a fix's images in the Feedback tab)."""
        return self.tabs.current() or self.chat

    def open_fixes(self, select=True):
        """☰ → Ongoing Feedback, as Baude's: the feedback on Baude and Record and where each stands, in a tab of its own
        (fixview.FixesPage), opened or shown."""
        return self.tabs.open(("fixes",), lambda: fixview.FixesPage(self, os.environ.get("RECORD_CLAUDE")),
                              wording.say("feedback.title"), icon="mail-send-symbolic",
                              tooltip=wording.say("window.feedback_tab_tip"),
                              select=select)

    def reopen_tab(self, key, line):
        """What was shown, gone back or forward to (MainTabs.back, forward), opened again if its tab's closed: a
        page of this notebook's, a chat, the feedback, a file's code at its line. False if it can't be (gone, another
        notebook's)."""
        if key[0] == "fixes":
            return bool(self.open_fixes())
        if key[0] == "note":
            return self.current is not None and key[1] in self.current["pages"] and self.open_note(key[1]) is not None
        if key[0] == "chat":
            return os.path.exists(key[1]) and self.open_chat(key[1], present=False) is not None
        if key[0] == "code":
            if key[2]:  # (a version's text: only while its tab's open)
                return self.tabs.show(key)
            return os.path.exists(key[1]) and self.show_code(key[1], line)
        return False

    def go_up(self):
        """Up (⌥⌘↑): from a sub-page, the page it's under; from a file's code, its folder on the Files page, the
        file selected. False from anything else."""
        page = self.tabs.current()
        if isinstance(page, DiaryEditor) and ":" in page.page:
            return self.open_note(page.page.rpartition(":")[0]) is not None
        if isinstance(page, viewers.CodePage) and page.path and os.path.exists(page.path):
            return self.show_folder(os.path.dirname(page.path), page.path)
        return False

    def show_folder(self, path, select=None):
        self.right_slide.set_shown(True)
        return viewers.ViewerHost.show_folder(self, path, select)

    def show_git(self, rev=None, sha=None, folder=None):
        self.right_slide.set_shown(True)
        return viewers.ViewerHost.show_git(self, rev, sha, folder)

    def find_in_code(self):
        """⌘F / Ctrl+F in a code tab: find in its file; on a page of notes: in its text. False if neither's shown."""
        page = self.tabs.current()
        if isinstance(page, viewers.CodePage) and page.code.get_visible():
            page.code.open_search()
            return True
        if isinstance(page, DiaryEditor):
            page.open_search()
            return True
        if isinstance(page, Reader):
            return page.chat_find.open()
        return False

    def zoom_code(self, steps):
        """Ctrl/⌘ + − 0 in a code tab (the focus anywhere but the conversation, whose text they size), or with the
        focus on code (the Kubernetes view's YAML): the code's own size, as Baude's, steps up or down (0: 1.0 again).
        False otherwise, where they size the notes, the chat read and the conversation."""
        focus = self.get_focus()
        in_chat = focus is not None and (focus is self.chat or focus.is_ancestor(self.chat))
        if in_chat or not (isinstance(self.tabs.current(), viewers.CodePage) or viewers.in_code(focus)):
            return False
        viewers.CodeView.set_zoom(viewers.CodeView.zoom + steps * widgets.ZOOM_STEP if steps else 1.0)
        return True

    def _last(self, kind):
        """The tab of a kind shown, else the one shown last of it, else None."""
        current = self.tabs.current()
        if isinstance(current, kind):
            return current
        seen = [c for c in self.shown_order if isinstance(c, kind) and c in self.tabs.children()]
        return seen[-1] if seen else next((c for c in reversed(self.tabs.children()) if isinstance(c, kind)), None)

    @property
    def reader(self):
        """The chat shown, else the one shown last (its Reader); None with none open."""
        return self._last(Reader)

    @property
    def code_page(self):
        """The code tab shown, else the one shown last (viewers.CodePage); None with none open."""
        return self._last(viewers.CodePage)

    @property
    def diary(self):
        """The page of notes shown, else the one shown last, else Home, opened (its DiaryEditor); None without a
        notebook."""
        return self._last(DiaryEditor) or self.open_note(HOME, select=False)

    def diary_shown(self):
        """The page of notes shown, else the one shown last (no page opened for it); None."""
        return self._last(DiaryEditor)

    def _tab_shown(self):
        """A tab shown: what's in it the one shown (a chat's names put in place; a page remembered, lit in the list);
        nothing open, so said."""
        child = self.tabs.current()
        if child is not None:
            self.shown_order = [c for c in self.shown_order if c is not child][-40:] + [child]
        self.zoom_tab(child)  # (it may have been left at another size: at this one in its first frame)
        if isinstance(child, Reader):
            child.activate()
            if child.chat:
                self.store.reading = child.chat
                self.reading_changed()
        elif isinstance(child, DiaryEditor) and self.current is not None and child.notebook is self.current:
            self.current["page"] = child.page
            if self.pages.notebook is self.current:
                self.pages.fill()
        if self.current is None and not isinstance(child, fixview.FixesPage):  # (the feedback needs no notebook)
            self.content_stack.set_visible_child_name("none")
        else:
            self.content_stack.set_visible_child_name("tabs" if self.tabs.children() else "empty")

    def _tab_closed(self, key, child):
        """A tab closed: what's in it stopped (a chat's reading, a page's timer), and what's open kept."""
        self.shown_order = [c for c in self.shown_order if c is not child]
        if hasattr(child, "stop"):
            child.stop()
        if self.switching:  # (another notebook's: what was open is kept as it was)
            return
        if isinstance(child, DiaryEditor):
            self.pages.prune()
            self.pages.fill()
        if isinstance(child, Reader) and (reader := self.reader) is not None and reader.chat:
            self.store.reading = reader.chat  # (the chat read now: the one shown before it)
            self.reading_changed()
        self._keep_open()
        self._tab_shown()

    def _keep_open(self):
        """What's open kept (records.json): the notebook's pages, and the chats, in their tabs' order."""
        if self.switching:
            return
        if self.current is not None:
            self.current["open"] = self.open_pages()
        self.store.chats = [c.chat for c in self.tabs.children() if isinstance(c, Reader) and c.chat]
        self.store.save()

    def delete_page(self, name):
        """A page deleted (its menu in the list), asked first: its tab closed; links to it stay, and make it again
        when followed."""
        notebook = self.current
        if notebook is None or name == HOME:
            return
        d = Adw.AlertDialog(heading=f"Delete {short_page(name)}?")
        d.add_response("cancel", wording.say("common.cancel"))
        d.add_response("delete", wording.say("common.delete"))
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, r):
            if r == "delete" and name in notebook["pages"]:
                self.tabs.close(("note", name))
                notebook["pages"].pop(name, None)
                self.store.save_now()  # (its host told it's gone first)
                for p in self.chat.pending(notebook):  # (the edits waiting on it: gone with it, said so to its host)
                    if p["page"] == name:
                        p["status"] = "rejected"
                        self.decide_edit(p, False)
                self.pages.fill()
                self.chat.show_pending_bar()
                self.chat.edit_cards_changed()
        d.connect("response", done)
        d.present(self)

    def note_renamed(self, moved):
        """Pages renamed ({old: new}, a page and its sub-pages: DiaryEditor.rename_page): their tabs with them, every
        page open shown again (its links renamed), the list again."""
        for old, new in moved.items():
            if (editor := self.tabs.get(("note", old))) is not None:
                editor.page = new
                self.tabs.retitle(("note", old), short_page(new), new_key=("note", new), tooltip=new)
                editor.load()
        for child in self.tabs.children():
            if isinstance(child, DiaryEditor):
                child.reload()
        if self.current is not None and self.current.get("page") in moved:
            self.current["page"] = moved[self.current["page"]]
        self._keep_open()
        self.pages.fill()

    def let_go(self):
        """Before the window goes (a window made again in its place): saved, and its timers stopped (each holds the window, and the
        reader's would read the chat every 1.2s for good)."""
        self.store.save_now()
        Gtk.StyleContext.remove_provider_for_display(self.get_display(), self.zoom_css)  # (the new window's own)
        for wait in ("zoom_saving", "scale_wait", "rolling"):
            if getattr(self, wait) is not None:
                GLib.source_remove(getattr(self, wait))
                setattr(self, wait, None)
        if self.zoom_wait is not None:
            self.get_frame_clock().disconnect(self.zoom_wait)
            self.zoom_wait = None
        self.chat.place.stop()
        self.chat.close_agent()  # (its session too)
        for child in self.tabs.children():  # (a chat's reading, a page's timer)
            if hasattr(child, "stop"):
                child.stop()

    def select(self, tid):
        i = 0
        while (row := self.notebook_list.get_row_at_index(i)) is not None and row.notebook["id"] != tid:
            i += 1
        if row is not None:
            self.notebook_list.select_row(row)

    def _toast(self, toast):
        self.toasts.add_toast(toast)
