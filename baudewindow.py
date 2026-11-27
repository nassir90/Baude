"""baudewindow — a Baude window (BaudeWindow): what it's made of, a restart's state, Send Feedback, a turn a restart
cut short, and its settings (SettingsDialog; Repaint, after a dialog). The rest is in its mixins: WindowView,
WindowProc, WindowDrafts, WindowSignal and WindowRemote.
"""
import collections
import os
import subprocess
import sys
import time
from macos import full_redraws, window_frame
from baudelib.transcripts import find_session
from baudelib import providers as providerslib  # noqa: E402  (where the models besides Claude's run: their providers)
from baudelib import tokens  # noqa: E402  (Baude's measures: schema/tokens, the Android app's too)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango  # noqa: E402

import feedback  # noqa: E402,F401  (Menu → Send Feedback…: Record's too)
import livefeed  # noqa: E402  (a chat followed as it's written: Record's too)
import update  # noqa: E402  (a new version from git, restarted onto once idle: Record's too)
import meta  # noqa: E402  (Meta, the helper in the side panel: Record's too; see meta.py)
from feedback import FeedbackDraft, FeedbackHost  # noqa: E402,F401
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
import chatparts  # noqa: E402
import composerview  # noqa: E402
import panels  # noqa: E402
import lightbox  # noqa: E402
import chatfind  # noqa: E402
import pickers  # noqa: E402
import theming  # noqa: E402
import meshui  # noqa: E402
from composerview import Composer  # noqa: E402,F401
from pickers import ChoiceDropDown  # noqa: E402  (a dropdown over (value, label): Record's hosts too)
from widgets import (  # noqa: E402,F401
    SCALE_MAX, SCALE_MIN, SETTINGS_KEY, scale_setting, zoom_keys, ZOOM_MAX, ZOOM_MIN, ZOOM_STEP, _settings_path,
    load_settings, paned_grip, save_settings, themed_icon,
)
from imageviews import Fade  # noqa: E402,F401
from markdownview import ARTIFACT_TITLES  # noqa: E402,F401
from pickers import PALETTE_KEY, HostPicker  # noqa: E402,F401
from theming import settings_size, settings_switch  # noqa: E402,F401
from codeview import find_in_focus  # noqa: E402,F401
from kubeview import KubeView  # noqa: E402,F401
from dockerview import DockerView  # noqa: E402
import menufamily  # noqa: E402
import terminalpane  # noqa: E402  (☰ → Terminal: Record's too)
from schemaview import SchemaView  # noqa: E402,F401
from startup import LAUNCHER, apply_scales, record_item, take_restart_state  # noqa: E402
from toolcards import COMMAND_TOOLS, CUT_CONTINUE, command_what  # noqa: E402
from chatinput import Attachment, ChatRefCompleter, SlashCompleter  # noqa: E402
from sessionlist import THIS_MACHINE, background_proc, is_desktop, network, remote_info, remote_name  # noqa: E402
from sessionsidebar import SessionSidebar  # noqa: E402
from sidequestion import CompactMarks  # noqa: E402
from refpopovers import RefPalette  # noqa: E402
from sidepanel import SidePanel  # noqa: E402
from windowview import WindowView  # noqa: E402
from windowproc import WindowProc  # noqa: E402
from windowdrafts import WindowDrafts  # noqa: E402
from windowsignal import WindowSignal  # noqa: E402
from windowremote import WindowRemote  # noqa: E402


class SettingsDialog(Adw.PreferencesDialog):
    """Menu → Settings… (⌘, on macOS, Ctrl+, elsewhere): Meta's (its model, Code mode), full-width text, the code font,
    the accent colour, Baude in
    the platform's list of apps, feedback, and the sizes, 1.0 the usual: the text's and the icons', all of them in every
    window (apply_scales), and the chat's zoom that ⌘0 / Ctrl+0 goes back to. Each takes as it's changed (the code font
    from the next start) and is kept in settings.json. (The switches are the window's actions, full-width's the menu's
    too, so the two agree; the handlers are given the row, never a closure over this dialog: see discard)"""

    def __init__(self, win):
        super().__init__(title=wording.say("window.settings_title"), content_width=820)  # (its five pages named in full)
        page = widgets.settings_page()
        page.add(meta.settings_group(win.panel.meta))  # (Record's too, and the same settings)
        group = Adw.PreferencesGroup()
        group.add(self.switch(wording.say("meta_settings.full_width"), None, "win.fullwidth"))
        group.add(theming.settings_code_font(win, "Baude"))
        group.add(theming.settings_accent())
        import themelook
        group.add(themelook.settings_row())  # (Record's too: the one setting)
        # (one setting for all four apps: what Record's Settings, or an --install, did since shown)
        win.lookup_action("launcher").set_state(GLib.Variant.new_boolean(LAUNCHER.installed()))
        group.add(self.switch(wording.say("window.show_in", where=LAUNCHER.where), None, "win.launcher"))
        group.add(update.settings_row())  # (Record's too: the one setting)
        group.add(theming.notifications_row())  # (Record's too)
        self.version_row = update.version_row()
        group.add(self.version_row)
        page.add(group)
        sizes = Adw.PreferencesGroup(title=wording.say("window.size"))
        self.text_row = self.size_row(wording.say("meta_settings.text_scale"), None, scale_setting("text_scale"),
                                      lambda size: win.set_scale("text_scale", size), key="text_scale")
        self.icon_row = self.size_row(wording.say("meta_settings.icon_scale"), None, scale_setting("icon_scale"),
                                      lambda size: win.set_scale("icon_scale", size), key="icon_scale")
        # (the chat's zoom: what ⌘0 goes back to)
        self.zoom_row = self.size_row(wording.say("meta_settings.global_scale"), None, win.zoom_default,
                                      win.set_zoom_default, ZOOM_MIN, ZOOM_MAX, key="zoom_default")
        for row in (self.text_row, self.icon_row, self.zoom_row):
            sizes.add(row)
        page.add(sizes)
        group = Adw.PreferencesGroup()
        self.feedback_row = Adw.ButtonRow(title=wording.say("fixes.send_feedback"), end_icon_name="go-next-symbolic")
        # (this goes first: the captures are of the window, not of this over it)
        self.feedback_row.connect("activated", lambda row: (row.get_ancestor(Adw.Dialog).close(), win.open_feedback()))
        group.add(self.feedback_row)
        page.add(group)
        page.set_title(wording.say("subscriptions.general"))
        page.set_icon_name("emblem-system-symbolic")
        self.add(page)
        import subscriptionpage
        self.add(subscriptionpage.Page(win))
        import depsview
        self.add(depsview.Page(win))
        import telemetryview
        self.add(telemetryview.Page(win))
        import accessview
        self.add(accessview.Page(win))

    switch = staticmethod(settings_switch)  # (widgets': Record's Settings has them too)
    size_row = staticmethod(settings_size)


class Repaint(Gtk.Widget):
    """Over the whole window, drawing nothing but for a frame when asked (flash): a change across all of it, which makes
    all of that frame's picture new. GTK 4.24's macOS backend shows a window as tiles, each given the new picture only
    where a frame changed something; as a dialog's opening settles over a maximized window, some lose theirs, and the
    app beneath shows through everywhere but the dialog until the whole window changes (see dialog_shown). Not
    needed while every frame draws all of the window (macos.full_redraws, on by default)."""

    TINT = Gdk.RGBA()
    TINT.red = TINT.green = TINT.blue = 0.0
    TINT.alpha = 2 / 255  # (black, all but clear: not to be seen, but a change; 1/255 is about where GDK's clear ends)

    def __init__(self):
        super().__init__(can_target=False, can_focus=False)
        self.frames, self.flashes = None, 0  # (frames: the flash's so far, None while not lit)

    def flash(self):
        self.flashes += 1
        if self.frames is None:
            self.add_tick_callback(Repaint.tick)
        self.frames = 0
        self.queue_draw()

    @staticmethod
    def tick(self, _clock):
        """At the start of a frame: lit for the one after the flash (a tick comes before the frame's drawing), then
        not."""
        self.frames += 1
        if self.frames < 2:
            return True
        self.frames = None
        self.queue_draw()
        return False

    def do_snapshot(self, snap):
        if self.frames is not None:
            snap.append_color(Repaint.TINT, Graphene.Rect().init(0, 0, self.get_width(), self.get_height()))


class BaudeWindow(WindowView, WindowProc, WindowDrafts, WindowSignal, WindowRemote, CompactMarks, Adw.ApplicationWindow,
                  FeedbackHost, theming.AccentFade):
    def do_snapshot(self, snapshot):
        self.snapshot_fading(snapshot, Adw.ApplicationWindow)  # (theming.AccentFade: the accent changed, fading)

    def __init__(self, app, opts):
        super().__init__(application=app, title="Baude", default_width=900, default_height=800)
        self.cwd = self.run_cwd = None  # set through set_cwd() once the header button exists
        self.proc = None
        self.session_id = opts.resume
        self.remote = None  # the open chat's server, when it's a session on the network (open_remote)
        self.remote_syncing = {}  # (server key, session id) -> the fetch of its transcript under way (sync_remote)
        self.busy = False
        self.total_cost = 0.0
        self.always_allowed = {}  # (a chat's session id, None for a new one till it has one: the tools always allowed
        # in it, here and on its host)
        self.host_allowed = {}  # (a chat's session id: the tools its host said it allows always, as it was attached)
        self.attachments = []
        self.chat_sent, self.listed_begun = False, None  # (the sidebar lists the open chat once it's begun)
        self.pending = {}          # control request id -> (callback(response), refused(error) or None) (request)
        self.tasks = {}            # background task id -> latest info (commands and subagents)
        self.waiting_cards = set()  # permission prompts not answered yet
        self.background = {}  # session id -> the claude of a chat left mid-turn, going on with it (detach_proc)
        self.left_remote = {}  # session id -> [its Remote, its state as last listed]: a network chat left mid-turn
        self.desktop_chat = False  # (the chat's Claude Desktop's: its claude runs here, not in the host process)
        # a past session opens read-only: its file is shown (and tailed) but no claude touches it
        # until you send a message (even resuming without a message writes to the file)
        self.resume_pending = False
        self.view_path = None
        self.tail = livefeed.Tail(self.tail_lines)  # the viewed transcript, followed as it's written
        self.resumed_note = False   # a viewed session's history is shown: mark where you took it up
        self.own_mtimes = {}        # transcript path -> its mtime when baude's own claude let go of it
        self.turn_finished = False
        self.artifacts = {}        # artifact url -> info (title, icon, version, ...)
        self.signal, self.ref_checkout = None, None     # the chat folder's Signal while it's on
        self.ref_surfaces = {}     # names the replies shown here mentioned, in order (the Signal page)
        self.reply_views = []      # Claude's replies drawn here (relinked as Signal learns their names)
        self.pointer = (0.0, 0.0)  # where the pointer is (a Signal popover points at the link clicked)
        self.render_target = None  # where add() puts top-level widgets (None: the transcript)
        self.orphan_results = {}   # tool_use_id -> result whose call isn't drawn yet
        self.history = None        # a loaded session's items not drawn yet (HistoryIndex)
        self.chunk_slots = None    # the places of its earlier chunks not drawn yet (make_chunk_slots)
        self.reverting = None      # a revert not sent yet: {turn, hidden, draft, asked} (revert_to)
        self.send_after_result = None  # (a revert made mid-turn: what's in the box sent once the turn's ended: reverted)
        self.interrupting = None   # (proc, timer): an interrupt asked of it, its turn not over yet (interrupt)
        self.estimated_per = 0     # the px per item they're sized by (estimate_slots)
        self.history_refs = None   # its replies Signal's being given, a few at a time (queue_history_refs)
        self.history_path = None   # its transcript (subagents live in <it without .jsonl>/subagents/)
        self.sub_tails = {}        # agent id -> how far its transcript has been read
        ARTIFACT_TITLES.clear()    # titles belong to the conversation in this window
        self.models_info = {}      # model value -> initialize's model entry
        self.effort = None
        self.local_effort = "off"  # (a model on this Mac that thinks: its own level, Claude's effort apart; none at first)
        if not os.path.exists(_settings_path()):  # a new installation: full-width text from the start (save_settings)
            save_settings()
        self.settings = load_settings()
        self.zoom = min(ZOOM_MAX, max(ZOOM_MIN, float(load_settings().get("zoom", 1.0))))
        self.zoom_default = round(min(ZOOM_MAX, max(ZOOM_MIN, float(self.settings.get("zoom_default") or 1.0))), 2)  # (Ctrl+0's)
        self.zoom_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self.zoom_css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.reset_stream_state()

        # header
        header = Adw.HeaderBar()
        # None = let the CLI decide (settings / account default); shown once it reports back
        self.model = None if opts.model in (None, "default") else opts.model
        self.mode = "auto"  # until you pick another mode
        self.model_dd = pickers.ModelPicker([], wording.say("common.model"), self.on_model_changed)
        # (left of it: where the chat's model runs, each provider that has it by host: sync_providers)
        self.provider_dd = HostPicker(wording.say("chat_header.provider_tip"), self.on_provider_changed, 24)
        self.provider_dd.set_visible(False)
        self.mode_dd = ChoiceDropDown([], wording.say("chat_header.mode_tip"), self.on_mode_changed)
        self.effort_dd = ChoiceDropDown([], wording.say("chat_header.effort_tip"), self.on_effort_changed)
        self.effort_dd.set_sensitive(False)
        self.set_cwd(opts.cwd)
        self.sidebar_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic",
                                            tooltip_text="Sessions sidebar (Ctrl+B)")
        header.pack_start(self.sidebar_btn)
        # (the chat's name in the text's own weight: a .heading's bold was the one bold thing up here. .title, a header
        # bar's title, for a GTK theme's title bar (theming.theme_css): over libadwaita, still the text's own weight)
        self.title_label = Gtk.Label(label=wording.say("sidebar.new_session"), max_width_chars=48,
                                     ellipsize=Pango.EllipsizeMode.END,
                                     css_classes=["title", "baude-title"],
                                     tooltip_text=wording.say("window.rename_tip", thing="session"))
        header.set_title_widget(self.title_label)
        self.panel_btn = Gtk.ToggleButton(icon_name="sidebar-show-right-symbolic",
                                          tooltip_text=wording.say("side_panel.toggle_tip") + " (Ctrl+Alt+B)")
        # (in sections as Record's: the chat's, the view's, the views over the window, the app's)
        menu = Gio.Menu()
        menu.append(record_item(), "win.record")
        menu.append(wording.say("window.copy_transcript"), "win.copyreport")
        menu.append(wording.say("window.read_only"), "win.readonly")
        view = Gio.Menu()
        view.append(wording.say("meta_settings.full_width"), "win.fullwidth")
        menu.append_section(None, view)
        tools = Gio.Menu()  # (Kubernetes, Docker, Databases, Terminal, Browser: menufamily.attach)
        menu.append_section(None, tools)
        usage = Gio.Menu()  # (the usage of the subscription the chat runs under: subscriptionpage.attach_menu_usage)
        menu.append_section(None, usage)
        app_menu = Gio.Menu()
        item = Gio.MenuItem.new(wording.say("window.settings"), "win.settings")
        item.set_attribute_value("accel", GLib.Variant.new_string(SETTINGS_KEY))  # (its key, shown beside it)
        app_menu.append_item(item)
        app_menu.append(wording.say("changelog.title"), "win.changelog")
        app_menu.append(wording.say("mesh_versions.title"), "win.mesh")  # (Record's too)
        item = Gio.MenuItem.new(wording.say("fixes.send_feedback"), "win.feedback")
        item.set_attribute_value("accel", GLib.Variant.new_string(widgets.FEEDBACK_KEY))
        app_menu.append_item(item)
        app_menu.append(wording.say("window.ongoing_feedback"), "win.fixes")
        app_menu.append(wording.say("code_apart.restart_app", app="Baude"), "win.restart")
        app_menu.append(wording.say("code_apart.restart_daemon"), "win.restartdaemon")
        menu.append_section(None, app_menu)
        self.main_menu = menu
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                  tooltip_text=wording.say("chat_header.menu"))
        import subscriptionpage
        menufamily.attach(self, menu_btn, tools)  # (each on the host picked, or, from its caret, another; Record's too)
        subscriptionpage.attach_menu_usage(self, menu_btn, usage)  # (the chat's subscription's usage, Record's too)
        self.signal_btn = Gtk.MenuButton(icon_name=themed_icon("insert-link-symbolic", "emblem-symbolic-link-symbolic"),
                                         popover=self.signal_menu())
        header.pack_end(menu_btn)
        header.pack_end(self.panel_btn)
        header.pack_end(self.signal_btn)
        header.pack_end(self.mode_dd)
        header.pack_end(self.effort_dd)
        header.pack_end(self.model_dd)
        header.pack_end(self.provider_dd)
        prefs = Gio.SimpleAction.new("settings", None)
        prefs.connect("activate", lambda *_: self.open_settings())
        self.add_action(prefs)
        fullwidth = Gio.SimpleAction.new_stateful(
            "fullwidth", None, GLib.Variant.new_boolean(bool(self.settings.get("full_width"))))
        fullwidth.connect("change-state", self.on_fullwidth_toggled)
        self.add_action(fullwidth)
        launcher = Gio.SimpleAction.new_stateful("launcher", None,
                                                 GLib.Variant.new_boolean(LAUNCHER.installed()))
        launcher.connect("change-state", self.on_launcher_toggled)
        self.add_action(launcher)
        readonly = Gio.SimpleAction.new_stateful("readonly", None, GLib.Variant.new_boolean(False))
        readonly.connect("change-state", lambda a, v: self.set_read_only(v.get_boolean()))
        self.add_action(readonly)
        dbschema = Gio.SimpleAction.new("dbschema", None)
        dbschema.connect("activate", lambda *_: self.schema.open())
        self.add_action(dbschema)
        kube = Gio.SimpleAction.new("kube", None)
        kube.connect("activate", lambda *_: self.kube.open())
        self.add_action(kube)
        docker = Gio.SimpleAction.new("docker", None)
        docker.connect("activate", lambda *_: self.docker.open())
        self.add_action(docker)
        browser = Gio.SimpleAction.new("browser", None)  # (Emission's window on the host picked in the sidebar)
        browser.connect("activate", lambda *_: menufamily.open_browser(self))
        self.add_action(browser)
        terminal = Gio.SimpleAction.new("terminal", None)  # (a terminal on the host picked in the sidebar)
        terminal.connect("activate", lambda *_: self.terminal.open())
        self.add_action(terminal)
        record = Gio.SimpleAction.new("record", None)
        record.connect("activate", lambda *_: self.open_record())
        self.add_action(record)
        feedback = Gio.SimpleAction.new("feedback", None)
        feedback.connect("activate", lambda *_: self.open_feedback())
        self.add_action(feedback)
        report = Gio.SimpleAction.new("copyreport", None)
        report.connect("activate", lambda *_: self.copy_report())
        self.add_action(report)
        fixes_action = Gio.SimpleAction.new("fixes", None)
        fixes_action.connect("activate", lambda *_: self.open_fixes())
        self.add_action(fixes_action)
        changelog = Gio.SimpleAction.new("changelog", None)  # (☰ → Changelog, Record's too)
        changelog.connect("activate", lambda *_: update.changelog_dialog().present(self))
        self.add_action(changelog)
        mesh = Gio.SimpleAction.new("mesh", None)  # (☰ → Mesh, Record's too)
        mesh.connect("activate", lambda *_: meshui.mesh_dialog(network()).present(self))
        self.add_action(mesh)
        self.feedback_draft = None
        restart = Gio.SimpleAction.new("restart", None)
        restart.connect("activate", lambda *_: self.restart_app())
        self.add_action(restart)
        restart_daemon = Gio.SimpleAction.new("restartdaemon", None)  # (as the banner's Restart Daemon)
        restart_daemon.connect("activate", lambda *_: self.restart_host())
        self.add_action(restart_daemon)

        # transcript
        chat_h, chat_v = tokens.px("gap.chat-h"), tokens.px("gap.chat-v")
        self.transcript = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                                  margin_top=chat_v, margin_bottom=chat_v, margin_start=chat_h, margin_end=chat_h)
        self.transcript.add_css_class("zoomable")
        # (messages written while the agent works, not sent yet, under the conversation: queue_message)
        self.queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, visible=False,
                                 margin_bottom=chat_v, margin_start=chat_h, margin_end=chat_h)
        self.queue_box.add_css_class("zoomable")
        self.queued, self.queue_upto = [], None  # (QueuedTurns, oldest first; the last to send once the turn's ended)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(self.transcript)
        column.append(self.queue_box)
        over = Gtk.Overlay(child=column)
        self.clamp = Adw.Clamp(child=over)
        # (at its natural height, its code blocks at most 2/3 of it: panels.markdown_scroller, Record's Read's too)
        self.scroller = panels.markdown_scroller(self.clamp, vexpand=True)
        self.text_selection = chatfind.ChatSelection(self.transcript, self.scroller, over)  # (Record's Read's too)
        self.apply_full_width(bool(self.settings.get("full_width")))
        self.follow = livefeed.FollowEnd(self.scroller)  # (the newest in view, once it's read to: on_layout)
        self.restore_scroll = None  # (distance from the bottom, until) after a restart
        self.toggle_held = self.slack = None  # (a toggle clicked in the chat, till it's done sliding: hold_toggle)
        vadj = self.scroller.get_vadjustment()
        vadj.connect("value-changed", self.on_scrolled)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self.on_reader_scroll)
        self.scroller.add_controller(wheel)
        # (its own scrolling and glide, each a step from where the view is: GTK's glide undid the view's moving by a
        # chunk of history drawn above it, mid-glide)
        self.scroll_glide = panels.ScrollGlide(self.scroller, lambda: bool(self.history and self.history.more),
                                                rested=self.chat_still, moved=self.reader_moved)
        # (the reader's other ways of moving it, as against GTK clamping it, the view held, or the end followed: the
        # scrollbar while it's held, the keys while they're pressed and a moment after, as GTK eases to where they go)
        self.thumb_held, self.keyed_at = False, 0.0
        thumb = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        thumb.connect("event", self.on_scrollbar_event)
        self.scroller.get_vscrollbar().add_controller(thumb)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", lambda *_: setattr(self, "keyed_at", time.monotonic()) or False)
        self.scroller.add_controller(keys)
        self.connect("realize", lambda *_: (self.get_frame_clock().connect_after("layout", self.on_layout),
                                            self.get_frame_clock().connect_after("layout", self.place_earlier_spin)))
        # (while it's scrolled: see reader_moved; the earlier chunks drawn a little a frame: see fill_frame)
        self.moved_at, self.still_timer, self.cull_at = 0.0, None, None
        self.fill_jobs, self.fill_tick, self.fill_reveal, self.estimate_tick = collections.deque(), None, 1, None
        self.fill_held, self.looked_in = None, (None, set())  # (load_history; find_beyond)
        self.fill_sweeping, self.above_changed, self.place_at = False, False, None

        # composer (composerview.Composer: Record's too)
        self.attach_btn = Gtk.Button(icon_name="list-add-symbolic",
                                     tooltip_text=wording.say("attachments.attach_tip"))
        self.attach_btn.add_css_class("flat")
        self.attach_btn.connect("clicked", self.on_pick_attachments)
        box_overlay = self.composer_box = Composer(
            buttons=[self.attach_btn],
            send_tip=f"Send ({composerview.SEND_KEY})\nNew line: Enter\n"
                     "@: a chat, a file, a pull request…")
        self.input, self.composer_md, self.send_btn = box_overlay.input, box_overlay.markdown, box_overlay.send
        self.input.get_buffer().connect("changed", lambda *_: self.draft_changed())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.input.add_controller(keys)
        self.input.connect("paste-clipboard", self.on_paste)  # (⌘V, Ctrl+V, Shift+Insert, the menu's Paste)
        self.send_btn.connect("clicked", lambda *_: self.interrupt() if self.busy else self.on_send())
        # (the stop button, at a turn: stops it, a revert pending or not; Enter queues what's written: on_send)
        self.attach_row = Gtk.Box(spacing=6)
        self.attach_strip = Gtk.ScrolledWindow(child=self.attach_row, visible=False, vexpand=False,
                                               vscrollbar_policy=Gtk.PolicyType.NEVER,
                                               propagate_natural_height=True)
        self.completer = SlashCompleter(self.input, box_overlay)
        # (@'s list, as in Meta's box: the chats, and the names the chat's folder has)
        self.refs = ChatRefCompleter(lambda: self.panel.meta.names if getattr(self, "panel", None) else None,
                                     self.input, box_overlay, self.completer)
        composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                           margin_start=12, margin_end=12, margin_bottom=6)
        composer.append(self.attach_strip)
        composer.append(box_overlay)
        # (☰ → Read-only session hides it, as does the chat being archived or another program having it: show_composer)
        self.composer, self.read_only, self.locked, self.archived = composer, False, False, False
        self.set_send_button(busy=False)

        self.status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        ctx = self.ctx_box = chatparts.ContextUsage(window=self.shown_window)  # (Meta's too)
        self.ctx_label, self.ctx_bar = ctx.label, ctx.bar
        statusbar = Gtk.Box(spacing=12)
        statusbar.add_css_class("statusbar")
        self.working_dots = widgets.WorkingDots()
        statusbar.append(self.working_dots)
        # (left of the path: the host the chat lives on, by its name, verbatim: local for this machine)
        self.host_label = Gtk.Label(label=THIS_MACHINE, css_classes=["chat-host", "mono"])
        statusbar.append(self.host_label)
        statusbar.append(self.status)
        self.tasks_btn = Gtk.Button(visible=False, tooltip_text=wording.say("status_bar.tasks"))
        self.tasks_btn.add_css_class("flat")
        self.tasks_btn.add_css_class("tasks-indicator")
        self.tasks_btn.connect("clicked", lambda *_: self.show_panel(True))
        statusbar.append(self.tasks_btn)
        # (what the chat's spent, a label of its own, never cut short: the folder and the turn's figures give way,
        # however narrow the chat or long another host's path; beside the context count, the other running total)
        self.cost_label = Gtk.Label(xalign=1, tooltip_text=wording.say("status_bar.cost_tip"))
        statusbar.append(self.cost_label)
        statusbar.append(ctx)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.chat_column = root
        # (a press in it: the chat's under the pointer again at once, before the chat's own controllers hear it, so a
        # selection dragged from there finds its labels, and a click after this one lands where it's made)
        press = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", lambda *_: self.chat_still())
        root.add_controller(press)
        self.chat_fade = Fade(root)  # it fades lighter while the chat is swapped (switch_to)
        # the main area's tabs (panels.MainTabs, Record's too): the chat, pinned first, and code clicked in it, each
        # file a tab of its own (show_code); the tabs' bar only while there's code open
        # (⌥⌘← ⌥⌘→: back and forward through them)
        self.tabs = panels.MainTabs(shown_alone=False, reopen=self.reopen_tab)
        self.tabs.prepend(self.tabs.bar)
        self.tabs.open("chat", lambda: root, wording.say("chats.chat"), icon="baude-claude-symbolic",
                       pinned=True)  # (Claude's logo)
        # (the chat: the inside, set into the frame the bar, the sessions and the panel are)
        widgets.inside(self.tabs, under_bar=True)
        self.banner = Adw.Banner(revealed=False, use_markup=True)  # (its titles: banner_text's markup)
        self.banner.connect("button-clicked", lambda *_: self.new_chat_on_host() if self.banner_kind == "missing" else
                            self.act_host_apart() if self.banner_kind == "apart" else None)
        root.append(self.banner)
        self.chat_find = chatfind.ChatFind(self.transcript, self.scroller, pane=root)  # (⌘F / Ctrl+F: find_in_chat)
        self.chat_find.undrawn, self.chat_find.beyond = self.find_undrawn, self.find_beyond
        self.chat_find.drawn = lambda: None if self.history is None else (
            self.history.shown, sum(len(job.items) + len(job.hidden) for job in self.fill_jobs))
        root.append(self.chat_find.wrap())
        # (over the part of the chat in view that's still to come above, while it's read or drawn: place_earlier_spin)
        self.earlier_spin, self.earlier_spin_y = widgets.Spinner(can_target=False, visible=False), None
        self.scroller.get_parent().add_overlay(self.earlier_spin)
        self.scroller.get_parent().connect("get-child-position", self.earlier_spin_position)
        root.append(composer)
        # (more said than the model's window holds: said over the box, its send button off, till a bigger model or a
        # new chat)
        self.exhausted = chatparts.Exhausted(self.scroller, box_overlay)
        self.drop_running = self.drop_session = None  # (a host's connection dropped: whether its claude was running
        # then, and the chat: drop)
        self.drop_timer = self.resume_reason = None  # (resume_reason: (chat, why) the next message resumes it)
        self.retrying, self.banner_kind = None, None  # (show_retry; what the banner says: update_banner)
        self.missing_session = self.missing_reason = self.retaken = self.attach_said = None  # (a chat its host has
        # no session for, why it couldn't be taken up again, and one taken up again for that: session_missing; the
        # kind of attach failure last said: start_remote_proc)
        self.banner_timer, self.banner_dots = None, 0  # (its ellipsis going, while it's waiting something out)
        self.outgrown = False  # (a turn ended with claude saying the prompt's too long: exhausted, show_context)
        root.append(statusbar)
        self.panel = SidePanel(self)
        # a plain split: same background on both sides, a 1px rule between them, no shadow;
        # drag the rule to resize the panel. It stops where the chat's at its least (what it has, its cards' lines, its
        # composer): the panel never goes over the chat, nor does the sessions' rule push it under the panel
        self.panel.set_visible(False)
        self.panel.set_size_request(320, -1)
        self.split = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=self.tabs,
                                          end_child=self.panel, resize_start_child=True, resize_end_child=False,
                                          shrink_start_child=False, shrink_end_child=False, wide_handle=False))
        self.split.add_css_class("main-split")
        self.split.add_css_class("inside-before")
        # the width you drag the panel to is kept: when it opens again, and when it shows code (settings.json:
        # "panel_width"); only a drag of the rule sets it, not the window resizing nor Baude placing it
        self.panel_width, self.split_save = self.settings.get("panel_width"), None
        self.split.connect("notify::position", lambda *_: self.split_dragged())
        self.panel_slide = panels.PaneSlide(self.split, self.panel, [self.panel_btn])
        self.panel_slide.closed_by_drag = self.panel_closed_by_drag
        self.panel.connect("notify::visible", lambda *_: self.panel.get_visible() and self.place_split())
        self.sidebar = SessionSidebar(self)
        for pane in (self.sidebar.scroller, self.panel.meta.scroll):  # (see pane_moving)
            hand = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL,
                                             propagation_phase=Gtk.PropagationPhase.CAPTURE)
            hand.connect("scroll", BaudeWindow.pane_touched)
            pane.add_controller(hand)
            pane.get_vadjustment().connect("value-changed", BaudeWindow.pane_scrolled)
        network().listen(self.on_network)
        if BaudeWindow.forget_gone not in network().listeners:  # (once, for the app)
            network().listen(BaudeWindow.forget_gone)
        providerslib.listen(self.providers_found)  # (Ollama started, a model pulled, an endpoint added: the pickers)
        self.outer = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=self.sidebar,
                                          end_child=self.split, resize_start_child=False, resize_end_child=True,
                                          shrink_start_child=False, shrink_end_child=False, wide_handle=False))
        self.outer.add_css_class("main-split")
        self.outer.add_css_class("inside-after")
        # the binding copies the button's state on creation, so set the button first
        self.sidebar.set_visible(self.settings.get("sidebar", True))
        self.sidebar_slide = panels.PaneSlide(self.outer, self.sidebar, [self.sidebar_btn], end=False)
        self.sidebar.connect("notify::visible", lambda *_: save_settings(sidebar=self.sidebar.get_visible()))
        tv = Adw.ToolbarView()
        tv.add_css_class("baude-frame")  # (the bar, the sessions, the panel: theming.CSS)
        tv.add_top_bar(header)
        tv.set_content(self.outer)
        self.header_bar = header
        tv.add_bottom_bar(self.make_capture_bar())  # (feedback.FeedbackHost: Capture while one's pending)
        self.toasts = Adw.ToastOverlay(child=tv)
        self.zoom_toast = None
        self.zoom_timer = self.zoom_job = None
        self.zoom_wheel = 0.0  # Ctrl+scroll that hasn't added up to a step yet
        self.lightbox = lightbox.Lightbox(self)
        self.schema = SchemaView(self)
        self.kube = KubeView(self)
        self.docker = DockerView(self)
        self.terminal = terminalpane.TerminalView(self, self.terminal_host)  # (☰ → Terminal: over it, as Kubernetes)
        host = Gtk.Overlay(child=self.toasts)
        host.add_overlay(self.schema)
        host.add_overlay(self.kube)
        host.add_overlay(self.docker)
        host.add_overlay(self.terminal)
        host.add_overlay(self.lightbox)
        self.repaint, self.repaint_wait = Repaint(), None
        host.add_overlay(self.repaint)
        self.set_content(host)
        self.connect("notify::visible-dialog", BaudeWindow.dialog_shown)

        self.meta_from = None  # (where the focus was last outside the side panel: close_meta)
        self.connect("notify::focus-widget", BaudeWindow._focus_moved)
        sc = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
        for triggers, fn in (("<Control>n", lambda: self.sidebar.new_chat()), ("<Control>o", self.show_sessions),
                             # (Ctrl+Alt+B the panel on the right, as VS Code's secondary sidebar)
                             ("<Control><Alt>b", lambda: self.show_panel(not self.panel_slide.target)),
                             (widgets.META_KEY, self.toggle_meta),
                             # (⌘K / Ctrl+K: every name in the glossary, searched, as an editor's command palette)
                             (PALETTE_KEY, lambda: RefPalette(self).present(self)),
                             (SETTINGS_KEY, self.open_settings),
                             (zoom_keys("w"), lambda: self.tabs.close_current()),  # (a code tab: the chat stays)
                             (widgets.NAV_KEYS["back"], lambda: self.tabs.back()),  # (⌥⌘ ← → ↑: a browser's
                             (widgets.NAV_KEYS["forward"], lambda: self.tabs.forward()),  # back, forward, up)
                             (widgets.NAV_KEYS["up"], lambda: self.go_up()),
                             (zoom_keys("plus", "<Shift>plus", "equal", "KP_Add"),
                              lambda: self.zoom_schema(+1) or self.zoom_code(+1) or
                              self.set_zoom(self.zoom + ZOOM_STEP)),
                             (zoom_keys("minus", "underscore", "KP_Subtract"),
                              lambda: self.zoom_schema(-1) or self.zoom_code(-1) or
                              self.set_zoom(self.zoom - ZOOM_STEP)),
                             (zoom_keys("0", "KP_0"),
                              lambda: self.zoom_schema(0) or self.zoom_code(0) or self.set_zoom(self.zoom_default))):
            for t in triggers.split():
                sc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(t),
                                                 Gtk.CallbackAction.new(lambda *_, fn=fn: fn() or True)))
        self.add_controller(sc)
        # Ctrl+B the sessions on the left, as an editor's sidebar. Ctrl+F: the find bar of the code the focus is in
        # (the Kubernetes view's YAML or logs), else the Kubernetes view's filter while it's open (it covers the
        # window, so not the find bar of a Code page under it), else the Code page's find bar if that's showing. Both
        # before the focused widget: macOS's text boxes take Ctrl+B / Ctrl+F as Emacs's back / forward. ⌘F on a Mac
        # too; and past those, the chat the focus is in (Meta, a side question, a subagent), else the main chat
        widgets.first_keys(self, (("<Control>b F9", lambda: self.sidebar_slide.toggle()),
                                  (" ".join(m + "f" for m in widgets.ZOOM_MODS),
                                   lambda: find_in_focus(self) or self.kube.find() or self.docker.find() or
                                   self.schema.start_find() or
                                   self.find_in_chat())))
        import telemetryview
        telemetryview.watch(self, "baude", network())  # (its chats' activity, told to this machine's host)
        ctrl_scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        ctrl_scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctrl_scroll.connect("scroll", self.on_ctrl_scroll)
        ctrl_scroll.connect("scroll-end", lambda *_: setattr(self, "zoom_wheel", 0.0))  # a new swipe starts afresh
        self.add_controller(ctrl_scroll)
        drop = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY)
        drop.set_gtypes([Gdk.FileList, Gdk.Texture])
        drop.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)  # before the TextView's own text drop
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)
        self.apply_zoom()
        apply_scales(self.get_display())
        theming.apply_accent(self.get_display())  # (Settings → Accent Colour)
        theming.watch_settings(BaudeWindow.settings_changed)  # (and each as it's changed in Record, or another Baude)
        self.connect("close-request", lambda *_: (self.proc and self.proc.kill(),
                                                  [self.let_go_background(p) for p in list(self.background.values())],
                                                  [sq.close() for sq in self.panel.side_questions()],
                                                  self.panel.memory.stop(),  # (what's written in a Bot's memory saved)
                                                  self.panel.browser.stop(),  # (its watch let go of)
                                                  self.drop_signal(), GLib.idle_add(self.let_go)) and False)

        motion = Gtk.EventControllerMotion(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", self.on_pointer)
        motion.connect("leave", lambda *_: self.pointer_left())
        self.add_controller(motion)
        self.hover_soon, self.hovered, self.db_prefetched = None, None, (None, 0.0)  # (see hover_link)
        self.signal_pending = None  # (a new chat's switches, till it has an id to keep them under)
        on, links = self.chat_signal(self.session_id)
        self.signal_run.set_active(on)
        self.signal_show.set_active(links)
        self.set_signal(save=False)
        self.connect("notify::is-active", lambda *_: self.is_active() and self.links_on_focus())
        self.connect("notify::is-active", lambda *_: self.is_active() and self.seen_chat())
        self.tabs.view.connect("notify::selected-page", lambda *_: self.seen_chat())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)  # (a key, wherever: you're here)
        keys.connect("key-pressed", lambda *_: (self.acted(), False)[1])
        self.add_controller(keys)
        self.scroller.get_vadjustment().connect("value-changed", lambda *_: self.scrolled_chat())
        meshui.watch_github(self, BaudeWindow.github_folders, BaudeWindow._github_changed)
        self.signal_run_handler = self.signal_run.connect("notify::active", lambda sw, _p: self.set_signal(on=sw.get_active()))
        self.signal_show_handler = self.signal_show.connect("notify::active",
                                                            lambda sw, _p: self.set_signal(links=sw.get_active()))

        update.watch_input(self)  # (what quiet reads: a new version waits while you're at it)
        theming.warm_shaders(self)  # (the first window's: after its first frame)
        restore = take_restart_state(getattr(opts, "restore", None))
        if restore:
            self.session_id = restore.get("session_id") or self.session_id
            self.model, self.mode = restore.get("model") or self.model, restore.get("mode") or self.mode
        if restore and restore.get("remote") and (r := network().find(self.session_id)) is not None:
            self.open_remote(remote_info({**r.sessions[self.session_id], "server": r}))  # (its server's, again)
        elif self.session_id and (info := find_session(self.session_id)):
            self.view_session(info["path"])
        self.poll_source = GLib.timeout_add(1000, self.poll_tail)
        if restore:
            self.apply_chat_signal()  # (the restored chat's own switches)
            self.apply_restart_state(restore)
        if self.proc is None:  # (a network session's is its server's, attached to already: open_remote)
            self.start_proc()
        self.input.grab_focus()
        self.apply_read_only()  # (a session opened read-only before)

    # ---- restart, keeping what's on screen
    # ---- feedback (Menu → Send Feedback…)
    def terminal_host(self):
        """(where, name): the host picked in the sidebar's dropdown, for ☰ → Terminal (this machine's, if none)."""
        r = self.view_remote()
        if r is None:
            return {"via": "local"}, THIS_MACHINE
        return r.where, remote_name(r)

    def view_remote(self):
        """The host picked's Remote (None: this machine), for the Kubernetes and database views (kubehost.picked); while
        ☰ opens one on another host from its caret, that host's (family_host: menufamily.open_family)."""
        if getattr(self, "family_host", None) is not None:
            return menufamily.remote_of(self, self.family_host) or None
        return self.sidebar.shown_remote()

    def open_record(self):
        """Record (record.py, beside this file) reading this chat, on its Read tab in whichever notebook's open. One
        running already is handed the chat."""
        path = getattr(self, "history_path", None)
        record = os.path.join(os.path.dirname(os.path.realpath(__file__)), "record.py")
        subprocess.Popen([sys.executable, record] + (["--chat", path] if path and os.path.exists(path) else []),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)

    def feedback_context(self):
        """What a note sent from here says it came from (feedback.save_feedback)."""
        return [f"- session: {self.session_id or '-'} ({getattr(self, 'session_title', None) or 'untitled'})",
                wording.say("report.folder_line", folder=self.cwd),
                f"- Baude: {os.path.join(os.path.dirname(os.path.realpath(__file__)), 'baude.py')}"]

    def feedback_transcripts(self):
        return feedback.report_lines([(wording.say("report.the_chat"), self.report),
                                        ("Meta", self.panel.meta.report)])

    def capture_areas(self):
        """The window's parts, by name, for the text a capture records."""
        return {self.sidebar: "sidebar", self.scroller: "chat", self.panel: "side panel",
                self.composer_box: "input box", self.status.get_parent(): "status bar", self.header_bar: "header",
                self.banner: "banner", self.kube: "Kubernetes", self.docker: "Docker",
                self.schema: "databases", self.lightbox: "image",
                self.terminal: "terminal"}

    def restart_state(self):
        """What a restart brings back: the chat, where it was scrolled to, the window, and whatever's being written: the
        draft (its caret and attachments too), its queue, a side question typed over their list, a Feedback note put
        aside, the Feedback tab's reply. The other chats' drafts are every window's: BaudeApp.restart's."""
        adj = self.scroller.get_vadjustment()
        fixes_page = self.tabs.get(("fixes",))
        return {
            "cwd": self.run_cwd, "session_id": self.session_id, "viewing": self.resume_pending,
            "cut": bool(self.busy and self.remote is None and self.proc is not None),  # (Baude's claude: cut here)
            "remote": self.remote.key if self.remote is not None else None,  # (a network session's server)
            "at_bottom": self.stick_bottom,
            "from_bottom": adj.get_upper() - adj.get_page_size() - adj.get_value(),
            "chunk": None if self.stick_bottom else self.view_chunk(),  # (open_at)
            "draft": widgets.typed_text(self.input), "caret": widgets.caret_of(self.input),
            "btw_draft": widgets.typed_text(self.panel.btw_ask.input),  # (a side question typed, not yet asked)
            "attachments": [a.state() for a in self.attachments],
            "queue": self.queue_saved(q) if (q := self.queue_state()) else None,  # (the open chat's: put_queue)
            "feedback_draft": self.feedback_draft.state() if self.feedback_draft else None,
            "fixes": fixes_page.state() if fixes_page else None, "fixes_shown": self.tabs.current() is fixes_page,
            "model": self.model, "mode": self.mode, "cost": self.total_cost,
            "size": [self.get_width(), self.get_height()], "maximized": self.is_maximized(),
            "frame": window_frame(self),  # (macOS: the display it's on, where; put back by put_back)
            "panel": self.panel.get_visible(),
            "places": self.PLACES,  # (where the other chats were left: open_at)
        }

    def apply_restart_state(self, state):
        if state.get("cut"):  # (a Desktop chat's turn this restart cut short: said once it's drawn, show_cut)
            self.cut_pending = time.time()
        for sid, place in (state.get("places") or {}).items():
            self.PLACES.setdefault(sid, place)
        if widgets.typed_text(self.input) or self.attachments:  # (one its chat took as it opened, another window's:
            self.keep_draft(self.draft_key())                   # kept for it, this window's own put back below)
        if state.get("size") and not state.get("maximized"):
            self.set_default_size(*state["size"])
        theming.put_back(self, state.get("frame"), state.get("maximized"))  # (where it was, on macOS)
        if state.get("panel"):
            self.show_panel(True)
        if state.get("draft"):
            self.input.get_buffer().set_text(state["draft"])
            widgets.put_caret(self.input, state.get("caret"))
        if state.get("btw_draft"):
            self.panel.btw_ask.input.get_buffer().set_text(state["btw_draft"])
        for att in state.get("attachments") or ():
            self.add_attachment(Attachment.from_state(att))
        if state.get("queue"):
            self.put_queue(self.queue_loaded(state["queue"]))
        if state.get("feedback_draft"):
            self.feedback_draft = FeedbackDraft.from_state(state["feedback_draft"])
        if state.get("cost"):
            self.total_cost = max(self.total_cost, float(state["cost"]))
            self.update_status()
        if state.get("fixes"):
            self.open_fixes(select=bool(state.get("fixes_shown"))).restore(state["fixes"])
        if self.resume_pending and not state.get("viewing"):
            # baude itself was running this session: nobody else is writing it
            self.tail.last_written, self.tail.turn_open = 0.0, False
            self.update_banner()
        self.stick_bottom = bool(state.get("at_bottom", True))
        if not self.stick_bottom and not (self.history and state.get("chunk") and self.place_chunk(state["chunk"])):
            # hold the place (measured from the bottom) while the history lays out and images load
            self.restore_scroll = (float(state.get("from_bottom") or 0), time.monotonic() + 2.0)

    def restart_app(self):
        """☰ → Restart Baude: every window (BaudeApp.restart)."""
        self.get_application().restart()

    def idle(self):
        """Nothing a restart would cut short or take from under you (update.Updater): restart_blockers."""
        return not self.restart_blockers()

    def restart_blockers(self):
        """What a restart of Baude waits for in this window, said (the Update bar's tooltip), [] for none: a
        command of Baude's own claude's running (a Desktop chat's; a host's chats go on through a restart, commands and
        all), a side question's follow-up being written (that doesn't come back; what's typed elsewhere does:
        restart_state), a dialog or a capture open, and you at it (the window you're in, something typed, clicked or
        scrolled in it in the last minute). Not the model writing: a turn cut short there is resumed (show_cut)."""
        why = [f"running: {w}" for w in self.own_commands()]
        if any(sq.text().strip() and sq.writing.get_sensitive() for sq in self.panel.side_questions()):
            why.append("a side question's reply being written")
        bar = getattr(self, "capture_bar", None)
        if self.get_visible_dialog() is not None or (bar is not None and bar.get_reveal_child()):
            why.append(wording.say("window.blocker_dialog"))
        if not update.quiet(self):
            why.append(wording.say("window.blocker_using"))
        return why

    def own_commands(self):
        """The commands Baude's own claudes are running for this window (a Desktop chat's, here or left mid-turn): what a
        restart of Baude would stop. A host's chats' aren't: they go on through it."""
        out = []
        if self.proc is not None and self.remote is None:
            out += [command_what(c.input) for c in self.tool_cards.values()
                    if c.base in COMMAND_TOOLS and c.state == "running"]
            out += [t.get("description") or wording.say("task_notes.unnamed") for t in self.running_tasks()
                    if t.get("task_type") != "local_agent"]
        for p in self.background.values():
            out += list((getattr(p, "commands", None) or {}).values())
        return out

    # ---- a turn cut short by a restart (this chat's host's, or this Baude's for a Desktop chat): said at the chat's
    # end, with Continue, till something's sent to it
    def cut_state(self):
        """When a turn of this chat's was cut short by a restart, or None."""
        if self.remote is not None:
            return (self.remote.sessions.get(self.session_id) or {}).get("cut")
        return getattr(self, "cut_pending", None)

    def show_cut(self, cut=None):
        """The line at the chat's end that says a restart cut its last turn short (cut: when, as cut_state; None, gone),
        put last again if it's already there (what's drawn after it, a chunk of the chat, is above it)."""
        row = getattr(self, "cut_row", None)
        if row is not None and row.get_parent() is not None:
            row.get_parent().remove(row)
        self.cut_row = None
        if not cut:
            return
        row = Gtk.Box(spacing=8, halign=Gtk.Align.START)
        row.add_css_class("cut-note")
        going = getattr(self, "cut_continuing", False)  # (Continue pressed, the turn not started yet: said, not asked)
        said = Gtk.Label(label=wording.say("cut_short.continuing") if going else wording.say("cut_short.line"), xalign=0)
        said.add_css_class("dim-label")
        if going:
            row.append(widgets.Spinner())
            row.append(said)
        else:
            go = Gtk.Button(label=wording.say("cut_short.button"), tooltip_text=wording.say("cut_short.tip", message=CUT_CONTINUE))
            go.connect("clicked", lambda *_: self.continue_cut())
            row.append(said)
            row.append(go)
        self.transcript.append(row)
        self.cut_row = row

    def continue_cut(self):
        """Continue: the chat told to carry on, as you'd tell it (what's attached and typed left for what you send)."""
        if self.composer_box.gates:
            return
        self.cut_waiting = self.cut_state() or time.time()  # (its line again, if Continue can't be sent after all)
        self.cut_pending = None
        if self.resume_pending:
            self.begin_resume()
        self.cut_continuing = True
        self.send_text(CUT_CONTINUE)
        if not self.busy:  # (not sent yet: its host still starting, said as Continuing…; or it couldn't be: Continue back)
            self.show_cut(self.cut_waiting)

    def continue_failed(self, sid=None):
        """Continue's message couldn't be sent (its host refused the chat, or never came up): the line back as it was,
        its Continue to press again; sid: only if this window's still on that chat and it hasn't been sent since."""
        if not getattr(self, "cut_continuing", False) or sid is not None and self.session_id != sid or self.busy:
            return
        self.cut_continuing = False
        self.cut_pending = getattr(self, "cut_waiting", None) or time.time()
        self.show_cut(self.cut_pending)

    # ---- settings
    def open_settings(self):
        """Menu → Settings… (one at a time: the key again while it's open leaves it)."""
        if isinstance(self.get_visible_dialog(), SettingsDialog):
            return self.get_visible_dialog()
        dialog = SettingsDialog(self)
        dialog.present(self)
        return dialog

    # macOS: once a dialog has opened, the whole window drawn afresh (see Repaint), unless every frame already is
    REPAINT_AFTER_DIALOG = sys.platform == "darwin" and not full_redraws()  # (baude.py's FULL_REDRAWS, set before gi)

    def dialog_shown(self, _pspec):
        if self.REPAINT_AFTER_DIALOG and self.get_visible_dialog() is not None and self.repaint_wait is None:
            self.repaint_wait = [None, time.monotonic()]  # (the frame last seen, and since when this has waited)
            GLib.timeout_add(100, self.repaint_when_still)

    def repaint_when_still(self):
        """Every 100ms: once no frame has come since the last look (the dialog's opening done, which is when the tiles
        go), or after a second (something else animating), the window's flash."""
        clock = self.get_frame_clock()
        if clock is None:  # (closed meanwhile)
            self.repaint_wait = None
            return False
        frame = clock.get_frame_counter()
        if frame != self.repaint_wait[0] and time.monotonic() - self.repaint_wait[1] < 1.0:
            self.repaint_wait[0] = frame
            return True
        self.repaint_wait = None
        self.repaint.flash()
        return False

    def set_zoom_default(self, zoom):
        """Settings → Size → Global Scale: the zoom Ctrl+0 (⌘0) goes back to; the chat goes to it now, to show it, and
        every other window's that was at the one before (zoom_default_changed)."""
        zoom = round(min(ZOOM_MAX, max(ZOOM_MIN, zoom)), 2)
        save_settings(zoom_default=zoom)
        for win in Gtk.Window.list_toplevels():
            if isinstance(win, BaudeWindow) and win is not self:
                win.zoom_default_changed(zoom)
        self.zoom_default = zoom
        self.set_zoom(zoom)

    def zoom_default_changed(self, zoom):
        """The Global Scale changed elsewhere (another window, another app): Ctrl+0 (⌘0) goes back to zoom now, and a
        chat that was at the one before goes to it too (one zoomed apart stays as it is)."""
        zoom = round(min(ZOOM_MAX, max(ZOOM_MIN, zoom)), 2)
        following = self.zoom == self.zoom_default
        self.zoom_default = zoom
        if following:
            self.set_zoom(zoom)

    @staticmethod
    def settings_changed(keys, _before):
        """Shared settings changed in another app here (theming.watch_settings): the text's and the icons' sizes, and
        the Global Scale, in every window. (The accent's taken already; Meta's are meta.settings_changed's.)"""
        display = Gdk.Display.get_default()
        if keys & {"text_scale", "icon_scale"}:
            if BaudeWindow.scale_timer:
                GLib.source_remove(BaudeWindow.scale_timer)
            BaudeWindow.scale_timer = GLib.timeout_add(150, BaudeWindow.scales_changed, display)
        if "zoom_default" in keys:
            zoom = float(load_settings().get("zoom_default") or 1.0)
            for win in Gtk.Window.list_toplevels():
                if isinstance(win, BaudeWindow):
                    win.zoom_default_changed(zoom)

    scale_timer = None  # (one for the display, as the sizes are)

    def set_scale(self, key, size):
        """Settings → Size → Text or Icons ("text_scale", "icon_scale"): kept, and every window at it a moment after the
        last change (the text's restyles them all: once for a few quick clicks)."""
        save_settings(**{key: round(min(SCALE_MAX, max(SCALE_MIN, size)), 2)})
        if BaudeWindow.scale_timer:
            GLib.source_remove(BaudeWindow.scale_timer)
        BaudeWindow.scale_timer = GLib.timeout_add(150, BaudeWindow.scales_changed, self.get_display())

    @staticmethod
    def scales_changed(display):
        BaudeWindow.scale_timer = None
        for win in Gtk.Window.list_toplevels():  # (each chat keeps its place, as through a zoom)
            if isinstance(win, BaudeWindow) and win.get_display() == display:
                win.keep_place_through_restyle()
        apply_scales(display)
        return False

    def take_background(self, session_id):
        """The claude still at a turn in a chat left mid-turn, from whichever window left it: None if there's none."""
        if (p := background_proc(session_id, BaudeWindow)) is not None:
            p.owner.background.pop(p.key, None)
            p.owner.sidebar.restate()
        return p

    def destroy(self):
        self.drop_signal()  # (destroyed without being closed: its Signal is let go too)
        super().destroy()
        self.let_go()

    def live_elsewhere(self):
        """Is the viewed session one Claude Desktop runs, and is it writing it now? Desktop runs its chats itself, so a
        second writer here would interleave two conversations in its transcript: that's kept from it (update_banner).
        No other chat is: a host's session (this machine's or another's), one this machine's host hasn't taken up yet,
        one another window or a terminal's claude has open too. Several writers at once is how those are used, and
        what's sent mid-turn waits for the turn's end. Not Baude's own claude at a turn in a Desktop chat left
        mid-turn, which writing here takes over (begin_resume). A host's Desktop chat (another machine's, or one this
        machine's host has taken up): while its host says Desktop's writing it (elsewhere)."""
        if self.remote is not None:
            return bool((self.remote.sessions.get(self.session_id) or {}).get("elsewhere"))
        if not self.resume_pending:
            return False
        if not (self.desktop_chat or is_desktop(self.session_id)):
            return False
        return self.tail.in_use() and background_proc(self.session_id, BaudeWindow) is None
