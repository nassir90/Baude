#!/usr/bin/env python3
"""baude — a tiny GTK4 front-end for the `claude` CLI.

It spawns one long-lived `claude -p` process speaking the stream-json
protocol on stdin/stdout (the same protocol the Agent SDK uses) and renders
the events: streamed text, thinking, tool calls + results, permission
prompts, cost/usage. Everything "agentic" (tools, context, compaction,
MCP, hooks, CLAUDE.md...) is done by the CLI; baude is only a view.

    python3 baude.py [--cwd DIR] [--model MODEL] [--resume SESSION_ID]
    python3 baude.py --install | --uninstall   # app-menu launcher (per user)

Keys: Ctrl+Enter (⌘Enter) sends, Enter adds a line, Esc stops, Ctrl +/-/0 (⌘ too on macOS, or Ctrl+scroll)
zooms the text, Ctrl+V pastes images/files as attachments, Ctrl+N starts a new session, Ctrl+, (⌘,) opens Settings,
Ctrl+. (⌘.) Send Feedback over whatever's open, Ctrl+M (Control on macOS too) shows Meta, its box focused, or
closes it. Images and PDFs can also be attached with the paperclip or dropped onto the window.

Set BAUDE_CLAUDE=/path/to/claude to use a specific claude binary (else claude on PATH, else, on macOS,
the Claude desktop app's newest). One that's gone (an old desktop build, removed) is passed over, said on stderr.
"""
import argparse
import base64
import collections
import html
import json
import math
import os
import re
import shlex
import weakref
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from macos import full_redraws, ns_string, objc_send, window_frame  # (no GTK: used before it loads)
from baudelib.claude import claude_env, claude_exe, EFFORT_LABELS, MODELS, MODES, spent_since
from baudelib import claude as claudelib  # (its ClaudeProc: Baude's is it, on the GTK main loop)
from baudelib.transcripts import (
    _first_text, _prompt_texts, ChunkFill, clean_prompt, find_session, history_items, HistoryIndex, session_file,
    transcript_model, ref_epoch,
)
import baudelib.client  # noqa: E402  (servers on the network and their sessions: see baudelib/client.py)
import baudelib.hosts  # noqa: E402  (this machine's host process, started when it's wanted: see baudelib/hosts.py)
from baudelib import models as modelslib  # noqa: E402  (what runs a model: claude, apple-claude or pi-claude)
from baudelib import providers as providerslib  # noqa: E402  (where the models besides Claude's run: their providers)
from baudelib import worktree as worktreelib  # noqa: E402  (a chat's worktree that's gone: made again from its branch)
from baudelib import tokens  # noqa: E402  (Baude's measures: schema/tokens, the Android app's too)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from baudelib.models import model_program  # noqa: E402,F401
from lookup import REF_KINDS, Ref, signal_place
from lookupkube import pg_servers_cached
from lookupgit import _git


def macos_fonts():
    """The fonts set up before Pango's loaded: text through FontConfig and FreeType on macOS, the code font Settings ->
    Code Font says, and the fonts bundled beside Baude (fontsetup)."""
    import fontsetup  # (no GTK: beside this file)
    fontsetup.setup()


def macos_app_name():
    """On macOS, the menu bar says Baude rather than Python: it names the app after its bundle (Python.app's),
    read when the app starts, so this renames the bundle in memory before GTK starts it (the CFBundleName of
    +[NSBundle mainBundle]'s info, as PyObjC apps do)."""
    import ctypes
    if sys.platform != "darwin":
        return
    try:
        bundle = objc_send(b"NSBundle", b"mainBundle")
        for which in (b"infoDictionary", b"localizedInfoDictionary"):
            if info := objc_send(bundle, which):
                objc_send(info, b"setObject:forKey:", ns_string("Baude"), ns_string("CFBundleName"),
                          types=(ctypes.c_void_p, ctypes.c_void_p))
    except Exception as e:
        print("baude: still named Python:", e, file=sys.stderr)


macos_fonts()
macos_app_name()
FULL_REDRAWS = full_redraws()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango  # noqa: E402

import feedback  # noqa: E402,F401  (Menu → Send Feedback…: Record's too)
import fixview  # noqa: E402  (☰ → Ongoing Feedback: the fixes, approved or rejected)
import livefeed  # noqa: E402  (a chat followed as it's written: Record's too)
import pointers  # noqa: E402  (the pointer's shape: Record's too)
from livefeed import LIVE_SECONDS, read_appended  # noqa: E402
import update  # noqa: E402  (a new version from git, restarted onto once idle: Record's too)
baudelib.wire.HEAD_COMMIT = update.STARTED  # (said to the host processes it reaches: logged by one on other code)
baudelib.wire.HEAD_CLIENT = "baude"  # (listed as Baude among the apps connected to a host)
import meta  # noqa: E402  (Meta, the helper in the side panel: Record's too; see meta.py)
import metalook  # noqa: E402
from feedback import FeedbackDraft, FeedbackHost  # noqa: E402,F401
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
import markdownview  # noqa: E402
import chatparts  # noqa: E402
import composerview  # noqa: E402
import panels  # noqa: E402
import lightbox  # noqa: E402
import chatfind  # noqa: E402
import pickers  # noqa: E402
import theming  # noqa: E402
import meshui  # noqa: E402
from markdownview import hover_uri  # noqa: E402,F401
from chatparts import fmt_tokens  # noqa: E402,F401
from composerview import Composer, ComposerMarkdown  # noqa: E402,F401
from widgets import Ticker  # noqa: E402,F401
from chatparts import (  # noqa: E402  (a message of yours, its revert button: Meta's and side questions' too)
    HeldTurn, QueuedTurn, SenderTurn, UserTurn,
)
from pickers import ChoiceDropDown  # noqa: E402  (a dropdown over (value, label): Record's hosts too)
from widgets import (  # noqa: E402,F401
    SCALE_MAX, SCALE_MIN, SETTINGS_KEY, scale_setting, zoom_keys, ZOOM_MAX, ZOOM_MIN, ZOOM_STEP, _settings_path,
    discard, ensure_icons, fmt_scale, load_settings, paned_grip, pretty_path, save_settings, themed_icon, toast, walk,
)
from refkinds import REF_SCHEME, ref_hashlike, ref_span  # noqa: E402,F401
from imageviews import Fade  # noqa: E402,F401
from markdownview import (  # noqa: E402,F401
    ARTIFACT_TITLES, MarkdownView, REF_ABSENT_WHY, link_markup, open_code, open_in_editor, sel_label,
)
from pickers import PALETTE_KEY, HostPicker  # noqa: E402,F401
from theming import settings_size, settings_switch, macos_appearance, CSS  # noqa: E402,F401
from codeview import CODE_LIMIT, CodePage, CodeView, find_in_focus, in_code  # noqa: E402,F401
from kubeview import KubeView  # noqa: E402,F401
from schemaview import DB_POOL, SchemaView, db_locate  # noqa: E402,F401
from gitview import GIT_POOL  # noqa: E402,F401
from startup import (  # noqa: E402
    APP_ID, LAUNCHER, TRACE, apply_scales, launch_command, logo_icon, macos_dock_icon, record_item, restart_state_path,
    take_restart_state, trace,
)
from signalrefs import TOOL_NAMES, db_prefetch, pg_forget, tool_dir  # noqa: E402
from signalcode import REF_LSP_PACKAGES, lsp_dir  # noqa: E402
from signalnames import Signal  # noqa: E402
from toolcards import (  # noqa: E402
    ARTIFACT_URL, COMMAND_TOOLS, CUT_CONTINUE, QuestionForm, STOP_SAID_RE, StatusSlot, ThinkingView, ToolCard,
    ToolGroup, api_error_label, artifact_from_tool, command_what, ended_by_stop, html_title, summarize_tool,
    task_note_widget, tool_display, tool_input_view, track_commands,
)
from chatinput import (  # noqa: E402
    Attachment, ChatRefCompleter, LoginCard, SlashCompleter, _AUTH_ESCAPES, attachment_widget, clipboard_has,
    needs_login,
)
from sessionlist import (  # noqa: E402
    ClaudeProc, HERE_HOST, RemoteProc, THIS_MACHINE, _HOST_APART, _LOCAL_SERVER, _NETWORK, background_proc,
    cached_session_info, chat_group, chat_groups, host_apart, host_key, is_desktop, local_host, made_groups,
    main_folder, network, on_main, remote_info, remote_name, remote_path, save_info_cache, session_cost, stopping_said,
    turn_said,
)
from sessionsidebar import SessionSidebar  # noqa: E402
from sidequestion import CompactMarks, SideQuestion  # noqa: E402
from refpopovers import (  # noqa: E402
    LookupPopover, RefPalette, RefPopover, chat_checkout, in_chat, ref_busy, ref_column_table, ref_kind_label,
    ref_run_names, ref_status_text, ref_status_tip,
)
from sidepanel import SidePanel  # noqa: E402


trace("imported")


MESH_CHAT_TOOLS = ("mcp__mesh__start_subagent", "mcp__mesh__message_chat")  # (drawn as MeshCard)


class MeshCard(ToolCard):
    """A call of the mesh's tools (baudelib/meshtools.py) that starts a subagent on a host of the mesh, or messages a
    chat there: a line as a subagent's row in the side panel is, [mark] its title over its host and model, ›, the
    whole line opening that chat as the sidebar does (its mark: the chat's state, as its host lists it, else the
    call's); under it, once it's come, what the chat said back. Prompt (Message) opens what it was given."""

    def __init__(self, name, tool_id):
        self.reply, self.answer_view, self.mark, self.began = {}, None, None, time.time()  # (began: as it's
        super().__init__(name, tool_id)                                                     # made, a live call's start)
        self.add_css_class("mesh-card")
        self.input_toggle.set_label("Prompt" if self.base == "start_subagent" else "Message")
        head = self.head = Gtk.Box(spacing=10)
        head.add_css_class("mesh-head")
        self.mark = StatusSlot(self.shown_state())
        head.append(self.mark)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.line.remove(self.title)
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        texts.append(self.title)
        self.sub = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.sub.add_css_class("dim-label")
        self.sub.add_css_class("caption")
        texts.append(self.sub)
        head.append(texts)
        self.line.remove(self.toggles)
        head.append(self.toggles)
        chevron = self.chevron = Gtk.Image.new_from_icon_name("go-next-symbolic")
        chevron.add_css_class("soft-icon")
        head.append(chevron)
        self.grid.remove(self.line)
        self.remove(self.grid)
        self.remove_css_class("tool-card")  # (its frame's: what the chat said back goes under that, outside it)
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("tool-card")
        frame.append(head)
        frame.append(self.grid)
        self.append(frame)
        self.grid.set_margin_start(10)
        self.grid.set_margin_end(10)
        self.grid.set_column_homogeneous(True)  # (what opens under the line as wide as the card: its line's gone)
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("released", lambda g, _n, x, y: g.get_widget().contains(x, y) and self.open_chat())
        head.add_controller(click)
        self.refresh_header()

    def has_chat(self):
        """Is there a chat to open: the one messaged, or the subagent started (or being started)?"""
        return self.base == "message_chat" or bool(self.reply.get("session")) or self.state == "running"

    def task_title(self):
        """Its title in the list, as meshtools gives it one."""
        return self.input.get("title") or " ".join(str(self.input.get("prompt") or "").split())[:60]

    def listing(self):
        """The chat as its host lists it (Network.sessions'), if that's known here: by its id, from the reply (or what
        was messaged); a subagent's while it's started, by its title, among the chats this one started."""
        sid = self.reply.get("session") or (self.input.get("session") if self.base == "message_chat" else None)
        if sid:
            r = network().find(sid)
            listed = r.sessions.get(sid) if r is not None else None
            return {**listed, "server": r} if listed is not None else None
        me = getattr(self.get_root(), "session_id", None)
        if not me or self.base != "start_subagent" or self.state != "running" or not self.input_whole:
            return None
        title, host = self.task_title(), self.input.get("host")
        win = self.get_root()
        claimed = {c.listing_id for c in getattr(win, "mesh_cards", ()) if c is not self and c.listing_id}
        found = max((s for s in network().sessions() if s.get("spawned_by") == me and s.get("title") == title
                     and (s.get("created") or 0) >= self.began - 10 and s["id"] not in claimed  # (this call's: not
                     and (not host or host in (s["server"].info.get("id"), remote_name(s["server"])))),  # a run before,
                    key=lambda s: s.get("created") or 0, default=None)                   # nor another card's)
        self.listing_id = found["id"] if found is not None else None
        return found

    listing_id = None  # (the chat found for it while it starts: no other card takes it)

    def shown_state(self, listed=None):
        """The mark's: the chat's own state while it works (or waits on a question), else the call's."""
        if self.state in ("running", "error", "interrupted"):
            return self.state
        if listed is not None and listed.get("state") in ("working", "waiting"):
            return "running"
        return "error" if self.reply.get("error") or self.reply.get("exited") is not None or \
            self.reply.get("lost") or self.reply.get("interrupted") or self.reply.get("expired") else "done"

    def show_status(self):
        self.refresh_header()

    def refresh_header(self):
        if self.mark is None:
            return
        listed = self.listing()
        self.mark.set_state(self.shown_state(listed) if self.asking is None else "done")
        self.mark.set_opacity(1 if self.asking is None else 0)  # (waiting on you: the border says so; its place kept)
        r = listed["server"] if listed is not None else None
        host = self.reply.get("host_name") or (remote_name(r) if r is not None else None) or self.input.get("host")
        if self.base == "start_subagent":
            title = (listed or {}).get("title") or self.task_title() or "Subagent"
            model = self.input.get("model") or (listed or {}).get("model")
            if model and r is not None:
                model = next((m.get("displayName") for m in r.info.get("models") or []
                              if isinstance(m, dict) and m.get("value") == model and m.get("displayName")), model)
            bits = [f"Subagent on {host}" if host else "Subagent"] + ([model] if model else [])
        else:
            bot = ((listed or {}).get("bot") or {}).get("name")
            title = bot or (listed or {}).get("title") or f"Chat {str(self.input.get('session') or '')[:8]}".strip()
            bits = [f"Messaged on {host}" if host else "Messaged"]
        reply = self.reply
        if self.state == "interrupted" or reply.get("interrupted"):  # (its own turn, or the subagent's, cut short)
            bits.append("interrupted")
        elif reply.get("expired"):
            bits.append("the wait ran out")
        elif self.decision == "denied":
            bits.append("denied")
        elif listed is not None and listed.get("state") == "waiting":
            bits.append("waiting for an answer")
        elif reply.get("still_working"):
            bits.append("stopped waiting for it" if (listed or {}).get("state") != "working" else "still working")
        elif reply.get("sent"):
            bits.append("not waited for")
        elif reply.get("exited") is not None:
            bits.append(f"its claude exited ({reply['exited']})")
        elif reply.get("lost"):
            bits.append("its host went before it answered")
        self.title.set_markup(f"<b>{html.escape(title)}</b>")
        self.sub.set_text(" · ".join(bits))
        self.head_tip(listed, host)

    def head_tip(self, listed, host):
        """The line's pointer, ›, and what it says on hover: opening the chat, if there's one."""
        where, chat = f" on {host}" if host else "", self.has_chat()
        self.chevron.set_visible(chat)
        (self.head.add_css_class if chat else self.head.remove_css_class)("openable")
        pointers.set_pointer(self.head, pointers.HAND if chat else None)
        tip = None if not chat else f"Open this chat{where}" if listed is not None else \
            f"This chat{where} isn't listed here yet"
        if tip != self.head.get_tooltip_text():
            self.head.set_tooltip_text(tip)

    def open_chat(self):
        """Its chat, opened as the sidebar opens one (BaudeWindow.open_mesh_chat); one not listed here, said so."""
        win, listed = self.get_root(), self.listing()
        if not isinstance(win, BaudeWindow) or not self.has_chat():
            return
        if listed is not None:
            win.open_mesh_chat(listed)
            return
        host = self.reply.get("host_name") or self.input.get("host")
        widgets.toast(win, f"{self.title.get_text()}: its chat isn't listed here yet"
                      + (f" (is {host} reachable from here?)" if host and self.state != "running" else ""))

    def ask(self, buttons):
        super().ask(buttons)
        buttons.set_margin_bottom(8)

    def build_input(self):
        fresh = self.input_box is None
        super().build_input()
        if fresh:
            self.input_box.set_margin_top(0)
            self.input_box.set_margin_bottom(8)

    def output_boxes(self):
        fresh = self._output is None
        out = super().output_boxes()
        if fresh:
            box = self.output_toggle.get_child()
            box.set_margin_top(0)
            box.set_margin_bottom(8)
        return out

    def set_result(self, content, is_error):
        texts = [c.get("text", "") if isinstance(c, dict) else str(c)
                 for c in (content if isinstance(content, list) else [content]) if c]
        try:
            reply = None if is_error else json.loads("\n".join(texts))
        except ValueError:
            reply = None
        if not isinstance(reply, dict):  # (refused, or failed: as the tool said it, under Error)
            return super().set_result(content, is_error)
        if self.asking is not None:
            self.answered()
        self.reply = reply
        said = reply.get("answer") or reply.get("said_so_far")
        if said and self.answer_view is None:
            box = self.answer_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6)
            box.add_css_class("mesh-answer")
            if not reply.get("answer"):
                box.append(sel_label("<span alpha='60%'>What it had said so far</span>"))
            box.append(MarkdownView(said))
            self.append(box)
        self.set_state("done")


# (session_file, find_session, a transcript's items and HistoryIndex: baudelib.transcripts)


# ------------------------------------------------------------- the process --
# (claude_env, spent_since and ClaudeProc's workings: baudelib.claude)


def own_writes(proc, exiting=False):
    """A chat's claude left mid-turn wrote its transcript: every window takes those writes as its own (own_mtimes), so
    the chat opened after isn't one "active elsewhere". exiting: as it's stopped, with the cost it writes as it goes."""
    if getattr(proc, "path", None) is None:
        proc.path = (find_session(proc.key) or {}).get("path") if getattr(proc, "key", None) else None
    if proc.path is None:
        return
    try:
        mtime = os.stat(proc.path).st_mtime
    except OSError:
        return
    app = Gio.Application.get_default()
    for w in app.get_windows() if app is not None else []:
        if isinstance(w, BaudeWindow):
            w.own_mtimes[proc.path] = max(mtime, time.time() + 2) if exiting else mtime


class SettingsDialog(Adw.PreferencesDialog):
    """Menu → Settings… (⌘, on macOS, Ctrl+, elsewhere): Meta's (its model, Code mode), full-width text, the code font,
    the accent colour, Baude in
    the platform's list of apps, feedback, and the sizes, 1.0 the usual: the text's and the icons', all of them in every
    window (apply_scales), and the chat's zoom that ⌘0 / Ctrl+0 goes back to. Each takes as it's changed (the code font
    from the next start) and is kept in settings.json. (The switches are the window's actions, full-width's the menu's
    too, so the two agree; the handlers are given the row, never a closure over this dialog: see discard)"""

    def __init__(self, win):
        super().__init__(title=wording.say("window.settings_title"))
        page = widgets.settings_page()
        page.add(meta.settings_group(win.panel.meta))  # (Record's too, and the same settings)
        group = Adw.PreferencesGroup()
        group.add(self.switch(wording.say("meta_settings.full_width"), None, "win.fullwidth"))
        group.add(theming.settings_code_font(win, "Baude"))
        group.add(theming.settings_accent())
        group.add(self.switch(wording.say("window.show_in", where=LAUNCHER.where),
                              LAUNCHER.paths()[0].replace(os.path.expanduser("~"), "~", 1), "win.launcher"))
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
        self.add(page)

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


class BaudeWindow(CompactMarks, Adw.ApplicationWindow, FeedbackHost, theming.AccentFade):
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
        menu = Gio.Menu()
        item = Gio.MenuItem.new(wording.say("window.settings"), "win.settings")
        item.set_attribute_value("accel", GLib.Variant.new_string(SETTINGS_KEY))  # (its key, shown beside it)
        menu.append_item(item)
        menu.append(wording.say("changelog.title"), "win.changelog")
        menu.append(wording.say("mesh_versions.title"), "win.mesh")  # (Record's too)
        menu.append(wording.say("meta_settings.full_width"), "win.fullwidth")
        menu.append(wording.say("window.read_only"), "win.readonly")
        menu.append(wording.say("window.database_schemas"), "win.dbschema")
        menu.append(wording.say("window.kubernetes_menu"), "win.kube")
        menu.append(record_item(), "win.record")
        item = Gio.MenuItem.new(wording.say("fixes.send_feedback"), "win.feedback")
        item.set_attribute_value("accel", GLib.Variant.new_string(widgets.FEEDBACK_KEY))
        menu.append_item(item)
        menu.append(wording.say("window.ongoing_feedback"), "win.fixes")
        menu.append(wording.say("window.copy_transcript"), "win.copyreport")
        self.main_menu = menu
        menu.append(wording.say("code_apart.restart_app", app="Baude"), "win.restart")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                  tooltip_text=wording.say("chat_header.menu"))
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
        self.connect("realize", lambda *_: self.get_frame_clock().connect_after("layout", self.on_layout))
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
        # (☰ → Read-only session hides it, and so does another program having the session: show_composer)
        self.composer, self.read_only, self.locked = composer, False, False
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
        self.tabs = panels.MainTabs(shown_alone=False, reopen=self.reopen_tab, closed=BaudeWindow.tab_closed)
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
        host = Gtk.Overlay(child=self.toasts)
        host.add_overlay(self.schema)
        host.add_overlay(self.kube)
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
                                   lambda: find_in_focus(self) or self.kube.find() or self.schema.start_find() or
                                   self.find_in_chat())))
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
                wording.say("report.folder_line", folder=self.cwd), f"- Baude: {os.path.realpath(__file__)}",
                *feedback.report_lines([(wording.say("report.the_chat"), self.report),
                                        ("Meta", self.panel.meta.report)])]

    def capture_areas(self):
        """The window's parts, by name, for the text a capture records."""
        return {self.sidebar: "sidebar", self.scroller: "chat", self.panel: "side panel",
                self.composer_box: "input box", self.status.get_parent(): "status bar", self.header_bar: "header",
                self.banner: "banner", self.kube: "Kubernetes", self.schema: "databases", self.lightbox: "image"}

    def restart_state(self):
        """What a restart brings back: the chat, where it was scrolled to, the window, and whatever's being written: the
        draft (its caret and attachments too), a side question typed over their list, a Feedback note put aside, the
        Feedback tab's reply. The other chats' drafts are every window's: BaudeApp.restart's."""
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

    # ---- helpers
    def short_cwd(self):
        tree = main_folder(self.run_cwd)[1]
        return pretty_path(self.cwd) + (f" (worktree {os.path.basename(tree)})" if tree else "")

    def reset_stream_state(self):
        self.blocks = {}         # stream index -> widget for current message
        self.tool_cards = {}     # tool_use_id -> ToolCard
        self.tool_parent = {}    # tool_use_id -> the subagent's call it ran inside (nested agents)
        self.mesh_cards = []     # the MeshCards among them (their chats' states followed: on_network)
        self.task_lines = {}     # task id -> the line its ending got while its card wasn't drawn (task_note)
        self.streamed_ids = set()  # message ids we already rendered via stream events

    def tool_card(self, name, tool_id):
        """A tool call's card, kept by its id: a mesh subagent's or a message to a chat a MeshCard, else a ToolCard."""
        card = self.tool_cards[tool_id] = (MeshCard if name in MESH_CHAT_TOOLS else ToolCard)(name, tool_id)
        if isinstance(card, MeshCard):
            self.mesh_cards.append(card)
        return card

    def open_mesh_chat(self, listed):
        """A chat on the network (Network.sessions'), opened from its card here (MeshCard), as the sidebar opens it."""
        info = remote_info(listed)
        self.show_chat()
        if info["id"] == self.session_id:
            self.opening_remote = None
            return
        self.open_remote_soon(info)

    def add(self, widget, parent_tool=None):
        """Append to the chat, or to a subagent's transcript (side panel, else inside its card).
        Tool calls and the thinking between them fold into a ToolGroup; a MeshCard stands on its own, as a reply does."""
        if parent_tool and isinstance(widget, ToolCard):
            self.tool_parent[widget.tool_id] = parent_tool
        if parent_tool and (view := self.panel.agent_view(parent_tool)) is not None:
            self.subagent_opened(parent_tool)  # (one not drawn yet, a message of its come live: what's before it first)
            target = view
        elif parent_tool in self.tool_cards:
            self.tool_cards[parent_tool].show_output()
            target = self.tool_cards[parent_tool].children
        else:
            target = self.render_target or self.transcript
        if isinstance(widget, ToolCard):
            self.add_activity(target, widget)
        else:
            target.append(widget)
        if self.reverting is not None and target is self.transcript:  # (after the message being reverted: hidden too)
            self.hide_reverted(self.transcript.get_last_child())
        return widget

    def add_activity(self, target, widget):
        last = target.get_last_child()
        if isinstance(widget, MeshCard):
            target.append(widget)
            return widget
        if isinstance(last, ToolGroup):
            last.add_item(widget)
            return widget
        # the run of tool cards at the end of the container (narration between them splits it)
        run, child = [], last
        while isinstance(child, ToolCard) and not isinstance(child, MeshCard):
            run.insert(0, child)
            child = child.get_prev_sibling()
        if isinstance(widget, ToolCard) and any(isinstance(w, ToolCard) for w in run):
            group = ToolGroup()
            for w in run:
                target.remove(w)
                group.add_item(w)
            group.add_item(widget)
            target.append(group)
            return widget
        target.append(widget)
        return widget

    def task_note(self, note):
        """The line a background task's ending gets: none while its card's in the chat, which says it ended itself
        (ToolCard.finish_task); else task_note_widget's, which links to its card if that's drawn later (card_task)."""
        task = self.tasks.get(note.get("task_id")) or {}
        if self.tool_cards.get(note.get("tool_use_id") or task.get("tool_use_id")) is not None:
            return None
        line = task_note_widget(note)
        if note.get("task_id"):
            self.task_lines[note["task_id"]] = line
        return line

    def card_task(self, card):
        """A card drawn after its background task's ending was (that's in a later part of the chat, drawn first): it
        says how the task ended, as a card that saw it end does, and the ending's line links back to it."""
        task = next((t for t in self.tasks.values() if t.get("tool_use_id") == card.tool_id), None)
        if task is None or task.get("status") in (None, "running", "interrupted"):
            return
        card.finish_task(task)
        if (line := self.task_lines.pop(task["task_id"], None)) is not None:
            line.link_card(card, self.jump_to_card)

    def jump_to_card(self, card):
        """Scroll the chat back to a card (opening the group it's folded into) and flash it."""
        p = card.get_parent()
        while p is not None and p is not self.transcript:
            if isinstance(p, ToolGroup):
                p.toggle.set_expanded(True)
            p = p.get_parent()
        if p is None:
            return
        self.stick_bottom = False
        card.add_css_class("flash")
        GLib.timeout_add(1400, lambda: card.remove_css_class("flash") and False)

        def scroll():
            ok, b = card.compute_bounds(self.transcript)
            if ok:
                adj = self.scroller.get_vadjustment()
                adj.set_value(max(0, b.get_y() - 48))
            return False
        GLib.timeout_add(160, scroll)  # after the group's slide-down has laid out

    def note(self, text, parent_tool=None):
        return self.add(sel_label(html.escape(text), css="sysnote"), parent_tool)

    def on_scrolled(self, _adj):
        # (the scrollbar or the keys moved it: the reader; ScrollGlide says so itself. Not GTK clamping it, the view
        # held, or the end followed: those aren't a move of the reader's, and neither waits for them)
        if self.thumb_held or time.monotonic() - self.keyed_at < 0.3:
            self.reader_moved()
        if self.fill_tick is None and (self.chunk_slots or self.fill_jobs):
            self.plan_fill()  # (an earlier chunk near the view now, or under it: drawn first)

    def on_scrollbar_event(self, controller, *_):
        event = controller.get_current_event()  # (its argument's None under PyGObject 3.58)
        kind = event.get_event_type() if event is not None else None
        if kind == Gdk.EventType.BUTTON_PRESS:
            self.thumb_held = True
        elif kind in (Gdk.EventType.BUTTON_RELEASE, Gdk.EventType.GRAB_BROKEN):
            self.thumb_held = False
        return False

    # ---- while the chat's scrolled, nothing in it is under the pointer. GTK looks again at what's under a still pointer
    # each frame the chat moves (a synthesized motion: a pick through every widget of the chat from the bottom up to the
    # pointer), hover goes from message to message as they pass under it, and each change restyled whole chunks: in a
    # long chat 25-60 ms a frame, 11-22 frames a second. Back STILL_MS after the last move, or at once when a glide or
    # a wheel's click comes to rest, or on a press; not while a selection's dragged (it finds its labels by a pick).
    STILL_MS = 150

    def reader_moved(self):
        self.moved_at = time.monotonic()
        Signal.moved()  # (Signal's lookups wait while a chat's scrolled)
        if self.text_selection.dragging:
            return
        if self.transcript.get_can_target():
            self.transcript.set_can_target(False)
        if self.still_timer is not None:
            GLib.source_remove(self.still_timer)
        self.still_timer = GLib.timeout_add(self.STILL_MS, self._still_timeout)

    def _still_timeout(self):
        self.still_timer = None
        self.chat_still()
        return False

    def chat_still(self):
        if self.still_timer is not None:
            GLib.source_remove(self.still_timer)
            self.still_timer = None
        if not self.transcript.get_can_target():
            self.transcript.set_can_target(True)
            # (GTK looks at what's under a still pointer again only after a layout: hover, the pointer's shape, and
            # where a click without a move between lands come back with the next frame)
            self.transcript.queue_allocate()
        if self.chunk_slots or self.fill_jobs:
            self.plan_fill()  # (what's near the view of its history drawn on, now it's still)

    def chat_moving(self):
        """Scrolled by the reader, and not still since (chat_still): what's drawn ahead waits for that."""
        return self.still_timer is not None or self.scroll_glide.gliding()

    PANE_STILL_S, PANE_HAND_S = 0.3, 1.5

    @staticmethod
    def pane_touched(hand, _dx, _dy):
        hand.get_widget().touched_at = time.monotonic()
        return False

    @staticmethod
    def pane_scrolled(adj):
        adj.moved_at = time.monotonic()

    def pane_moving(self):
        """The left pane or Meta scrolled by the reader in the last PANE_STILL_S (a wheel's or a trackpad's, its glide
        after: moved within PANE_HAND_S of their last scroll; not Meta following its reply to the end): the chat's
        earlier messages aren't drawn meanwhile (fill_frame), as they'd take the frames those need, the chat's laid out
        again with each one shown."""
        now = time.monotonic()
        return any(now - getattr(pane.get_vadjustment(), "moved_at", 0.0) < self.PANE_STILL_S and
                   now - getattr(pane, "touched_at", 0.0) < self.PANE_HAND_S
                   for pane in (self.sidebar.scroller, self.panel.meta.scroll))

    stick_bottom = property(lambda self: self.follow.stick, lambda self, on: setattr(self.follow, "stick", on))

    def on_reader_scroll(self, *_):
        self.restore_scroll = None  # the reader is scrolling: a restart's saved place no longer applies
        return False

    def on_layout(self, _clock):
        """Right after each layout pass: what's being held in place first, else the newest content in view."""
        # (a zoom, or a restart's place put back: those hold the view themselves, this one would fight them)
        # (a chunk opened at, place_chunk: the view put in it; what's at its top is noted once it's laid out there)
        self.trim_slack()
        placed = self.place_at is not None and self.put_place()
        if placed:
            self.view_anchor = None
        held = True if placed else \
            self.hold_view_anchor() if self.zoom_job is None and self.restore_scroll is None else None
        if getattr(self, "keep_place", None) is not None:
            self.hold_keep_place(held)
        if self.zoom_job is not None:
            self.view_anchor = None
            self.hold_zoom_place()
            return
        if self.restore_scroll is not None:
            self.view_anchor = None
            from_bottom, until = self.restore_scroll
            adj = self.scroller.get_vadjustment()
            if time.monotonic() > until:
                self.restore_scroll = None
            else:
                self.follow.put(max(0.0, adj.get_upper() - adj.get_page_size() - from_bottom))
                # (following the end only if that's where it's held: GTK clamping the view while earlier parts are
                # drawn above it could have said otherwise, and the end would be jumped to once the hold was over)
                self.follow.stick = from_bottom <= 1
            return
        if self.above_changed:
            self.above_changed = False
            if self.stick_bottom:  # (an earlier chunk drawn or sized again: the end's kept at once, not glided to as
                self.follow.to_end()  # new content at the end is)
        self.toggle_settled()
        self.follow.laid_out()
        if held is not True:  # (moved this pass: what's in view is found once it's laid out where it's moved to)
            self.note_view_anchor()
        self.cull_chunks()

    # ---- history chunks far from the view taken out of what's drawn and picked: set_child_visible keeps them laid out
    # (nothing moves, bounds still hold) but unmapped, so GTK's picks and GSK's walk of every node it draws (each frame:
    # 13% of a long chat's frame) skip them, and their pictures give their pixels back. Back within CULL_NEAR screens,
    # out beyond CULL_FAR: one at the edge isn't shown and hidden over and over.
    CULL_NEAR, CULL_FAR = 2.0, 4.0  # screens

    def view_top(self):
        """The top of the view in the chat's coordinates, and the view's height; None if it isn't laid out."""
        page = self.scroller.get_height()
        ok, tb = self.transcript.compute_bounds(self.scroller)
        return (-tb.get_y(), page) if ok and page > 0 else (None, page)

    def cull_chunks(self):
        hist = getattr(self, "history_box", None)
        top, page = self.view_top()
        if hist is None or top is None:
            return
        at = (round(top * 4 / page), hist.get_height())  # (a quarter of a screen moved, or the chunks' height changed)
        if at == self.cull_at:
            return
        self.cull_at = at
        if self.chunk_slots or self.fill_jobs:
            # (the view's somewhere else now it's laid out: a chunk near it is drawn. on_scrolled asks as the view
            # moves, before the frame's layout, when it's still where it was: this asks after it)
            self.plan_fill()
        going, slot = set(), hist.get_first_child()
        while slot is not None:
            ok, b = slot.compute_bounds(self.transcript)
            if ok and b.get_height() > 0:
                away = max(top - (b.get_y() + b.get_height()), b.get_y() - (top + page))
                if away < self.CULL_NEAR * page and not slot.get_child_visible():
                    slot.set_child_visible(True)
                elif away > self.CULL_FAR * page and slot.get_child_visible():
                    going.add(slot)
            slot = slot.get_next_sibling()
        watch = getattr(self.scroller, "picture_watch", None)
        for pic in list(watch.pictures) if going and watch is not None else ():
            chunk = pic.get_parent()
            while chunk is not None and chunk.get_parent() is not hist:
                chunk = chunk.get_parent()
            if chunk in going:
                pic.release()  # (decoded again as it comes near: PictureWatch)
        for slot in going:
            slot.set_child_visible(False)

    def item_below(self, y):
        """The chat's first item (a message, card or group, inside history chunks too) whose bottom is below y, in the
        chat's coordinates, with its bounds: by their bounds, not a pick (a pick walks every widget of the chat below
        the point: 1-25 ms in a long one, and nothing at all while it's scrolled: reader_moved)."""
        hist, btn = getattr(self, "history_box", None), getattr(self, "earlier_btn", None)
        child = self.transcript.get_first_child()
        while child is not None:
            if child.get_visible() and child is not btn:
                ok, b = child.compute_bounds(self.transcript)
                if ok and b.get_y() + b.get_height() > y:
                    if child is not hist:
                        return child, b
                    slot = hist.get_first_child()
                    while slot is not None:
                        ok, sb = slot.compute_bounds(self.transcript)
                        if ok and sb.get_height() > 0 and sb.get_y() + sb.get_height() > y:
                            item = slot.get_first_child()
                            while item is not None:
                                if item.get_visible() and item is not getattr(slot, "spacer", None):
                                    ok, ib = item.compute_bounds(self.transcript)
                                    if ok and ib.get_y() + ib.get_height() > y:
                                        return item, ib
                                item = item.get_next_sibling()
                        slot = slot.get_next_sibling()
            child = child.get_next_sibling()
        return None, None

    # ---- the view held to the message at its top as what's above it changes height (a chunk of history drawn, a web
    # image arriving in place of its link, a card filling in): GTK keeps the scrolled distance, so all of that pushed
    # what's read down, or up. After each layout pass the first message in view is noted with where it is in the chat;
    # after the next, the view moves by as much as it has. Not while following the end, nor across a change of width
    # (a reflow: zoom_job and keep_place_through_restyle hold the view then).

    def chat_message_of(self, widget):
        """The message widget is in (a child of the chat, or of a history chunk): None for the chat's own boxes, the
        Show earlier button, or what's outside the chat (the scrollbar)."""
        hist = getattr(self, "history_box", None)
        while widget is not None:
            parent = widget.get_parent()
            if parent is self.transcript or (hist is not None and parent is not None and parent.get_parent() is hist):
                return None if widget is hist or widget is getattr(self, "earlier_btn", None) or \
                    widget is getattr(parent, "spacer", None) else widget
            widget = parent
        return None

    def note_view_anchor(self):
        self.view_anchor = None
        if self.stick_bottom or self.transcript.get_width() <= 0:
            return
        top, _page = self.view_top()
        msg, b = self.item_below(top + 1) if top is not None else (None, None)
        if msg is not None:
            self.view_anchor = (msg, b.get_y(), self.transcript.get_width())

    def hold_view_anchor(self):
        """The view moved by as much as the message noted at its top has (note_view_anchor): True if it was moved,
        False if it's where it was, None if there's nothing to hold to."""
        anchor = getattr(self, "view_anchor", None)
        if anchor is None:
            return None
        msg, y, width = anchor
        if self.stick_bottom or width != self.transcript.get_width() or not msg.is_ancestor(self.transcript):
            self.view_anchor = None
            return None
        ok, b = msg.compute_bounds(self.transcript)
        if not ok:
            self.view_anchor = None
            return None
        if abs(b.get_y() - y) < 0.5:
            return False
        self.hold_value(self.scroller.get_vadjustment().get_value() + b.get_y() - y)
        self.view_anchor = (msg, b.get_y(), width)  # (held to it till the view's laid out where it's been moved)
        return True

    def hold_toggle(self, toggle):
        """A toggle in the chat clicked (a card's Command or Output, a group's › 5 tool calls): it stays where it is on
        screen, what opens under it pushing what's below down (closing, pulling it up). The end isn't followed from
        then on, nor a restart's place kept: either would keep the bottom still and move the toggle. The message at
        the view's top holds the view (hold_view_anchor); scrolled back to the end, it's followed again, and so it is
        if it's at the end once the toggle's done sliding (closed again where it was opened: toggle_settled). Closed
        with less below the view than closes, the end would come up past the view's bottom and GTK would move the view
        down to it, the toggle with it: blank room is left under the last message instead (set_slack)."""
        if not toggle.button.is_ancestor(self.transcript):
            return
        self.follow.stop_glide()
        self.stick_bottom, self.restore_scroll, self.toggle_held = False, None, toggle
        adj = self.scroller.get_vadjustment()
        below = adj.get_upper() - adj.get_page_size() - adj.get_value()  # (what's below the view)
        if toggle.expanded and toggle.revealer is not None and toggle.revealer.get_height() > below + 0.5:
            self.set_slack(self.transcript.get_height() - below)

    def set_slack(self, height):
        """The chat kept at least height px tall (None: as tall as what's in it): what it's short of that is blank room
        under its last message, for the view to sit past the end (hold_toggle) till it can go (trim_slack)."""
        self.slack = None if height is None else (math.ceil(height), self.transcript.get_width())
        self.transcript.set_size_request(-1, -1 if height is None else self.slack[0])

    def trim_slack(self):
        """After each layout pass, the slack that's below the view goes: scrolled up from it, it's gone, and scrolled
        down again the view stops at what's left of it. Nothing on screen moves. It all goes once what comes into the
        chat has filled it (the chat taller than it's kept), and with a reflow (another width, a zoom). With the view
        at what's left of it, the end's in view and followed (FollowEnd decided before what was below went)."""
        if self.slack is None:
            return
        height, width = self.slack
        if self.transcript.get_height() > height or self.transcript.get_width() != width or self.zoom_job is not None:
            self.set_slack(None)
            return
        adj = self.scroller.get_vadjustment()
        below = adj.get_upper() - adj.get_page_size() - adj.get_value()
        if below >= 1:
            self.set_slack(height - below)
        elif self.toggle_held is None:  # (not while one opens into it: that's held, not followed)
            self.stick_bottom = True

    def toggle_settled(self):
        held = self.toggle_held
        here = held is not None and held.button.is_ancestor(self.transcript)  # (not: the chat's been switched)
        if held is None or here and held.sliding():
            return
        self.toggle_held = None
        adj = self.scroller.get_vadjustment()
        if here and adj.get_value() + adj.get_page_size() >= adj.get_upper() - 1:
            self.stick_bottom = True

    def hold_value(self, value):
        """The view put at value to hold what's in it still (not a move of the reader's: reader_moved isn't told)."""
        self.scroller.get_vadjustment().set_value(value)

    def set_busy(self, busy):
        if busy and not getattr(self, "busy", False):
            self.said_last = None  # (a turn starting: what the last one said isn't this one's)
            if not self.compact_kept:  # (nor its compaction; one kept through a dropped connection, its turn going on)
                self.compact_ended()
        self.busy = busy
        if not busy:
            self.drop_interrupting()  # (the turn's over: no interrupt's waited on)
            if getattr(self, "panel", None) is not None and self.panel.memory_page.get_visible():
                self.panel.memory.refresh()  # (a Bot's turn over: what it kept in its memory, shown)
                self.panel.routines.refresh()  # (its routines' last and next, as they are now)
            if getattr(self, "queue_upto", None) is not None:  # (queued messages to send now it's over)
                GLib.idle_add(self.flush_queue)
        if busy and getattr(self, "exhausted", None) is not None and self.exhausted.on:
            self.exhausted.set(False)  # (a turn claude started itself: its stop button; exhausted again as it ends)
        if not busy and self.proc is not None:
            self.turn_finished = True
        if not busy and getattr(self, "retrying", None) is not None:
            self.show_retry(None)  # (the turn's over: not retrying any more)
        self.set_send_button(busy)
        self.update_status()
        self.update_session_state()

    def session_state(self):
        """working / waiting / done / inactive, for the sidebar's dot."""
        if self.resume_pending and self.remote is not None:  # (a network session viewed: as its server says)
            listed = self.remote.sessions.get(self.session_id) or {}
            return "inactive" if self.remote.state == "dead" else \
                {"working": "working", "waiting": "waiting"}.get(listed.get("state"), "inactive")
        if self.resume_pending:  # only viewing: what the program writing it is doing
            return self.tail.state()
        if self.waiting_cards:
            return "waiting"
        if self.busy or self.running_tasks():
            return "working"
        if getattr(self, "turn_finished", False) and self.proc is not None:
            return "done"
        return "inactive"

    def update_session_state(self):
        state = self.session_state()
        if hasattr(self, "sidebar"):
            self.sidebar.set_state(state)

    def set_send_button(self, busy):
        self.composer_box.set_busy(busy)  # (idle: a plain grey button; while working, a red stop button)

    # ---- text zoom
    def set_zoom(self, zoom):
        zoom = round(min(ZOOM_MAX, max(ZOOM_MIN, zoom)), 2)
        if zoom == self.zoom:
            return
        self.zoom = zoom
        save_settings(zoom=zoom)
        # a burst of Ctrl+ presses re-lays the chat out once; a held key or a long swipe still
        # shows a new size every 60 ms instead of only when it stops
        if not self.zoom_timer:
            self.zoom_timer = GLib.timeout_add(60, self.apply_zoom_keeping_place)

    def show_zoom_toast(self):
        # A toast coming or going restyles everything shown in the window (a long chat: half a
        # second), so it comes once the zoom has hidden what's far off, and a burst shares one.
        title = f"Chat zoom {fmt_scale(self.zoom)}"
        if self.zoom_toast:
            self.zoom_toast.set_title(title)
            return
        self.zoom_toast = Adw.Toast(title=title, timeout=1)
        self.zoom_toast.connect("dismissed", lambda *_: setattr(self, "zoom_toast", None))
        self.toasts.add_toast(self.zoom_toast)

    def apply_zoom(self):
        self.zoom_css.load_from_string(f".zoomable {{ font-size: {self.zoom:.2f}em; }}")

    def zoom_units(self):
        """The chat's top-level pieces (messages, cards, groups), looking inside history chunks."""
        child = self.transcript.get_first_child()
        while child is not None:
            if getattr(child, "is_history", False):
                chunk = child.get_first_child()
                while chunk is not None:
                    inner = chunk.get_first_child()
                    while inner is not None:
                        if inner is not getattr(chunk, "spacer", None):  # (a chunk's room, while it's drawn)
                            yield inner
                        inner = inner.get_next_sibling()
                    chunk = chunk.get_next_sibling()
            else:
                yield child
            child = child.get_next_sibling()

    def apply_zoom_keeping_place(self):
        self.zoom_timer = None
        self.keep_place_through_restyle()
        self.apply_zoom()
        self.show_zoom_toast()
        return False

    def keep_place_through_restyle(self):
        """Before the text's size changes (the zoom, or Settings → Size): without the slow full relayout or the jump.
        Re-laying out a long session at a new size takes seconds, so only what's near the view is laid out at once: the
        rest is hidden and comes back a few messages a frame, nearest first. Each frame, after GTK's layout and before
        anything is drawn, on_layout puts the message at the top of the view back where it was on screen (or keeps the
        view at the bottom, if it was there). Putting it back from a timer instead let frames through with the view
        off by a whole batch of messages."""
        self.cancel_keep_place()  # a stale "Show earlier" correction would fight the zoom's own
        self.follow.stop_glide()
        self.restore_scroll = None
        clock = self.get_frame_clock()
        if clock is None:  # not on screen: there's no place to keep
            return
        adj = self.scroller.get_vadjustment()
        top, page = adj.get_value(), adj.get_page_size()
        anchor, above, below = None, [], []
        for unit in self.zoom_units():
            if getattr(unit, "zoom_hidden", False):  # still hidden from the last zoom
                (below if anchor else above).append(unit)
                continue
            if not unit.get_visible():
                continue
            ok, b = unit.compute_bounds(self.transcript)
            if not ok:
                continue
            y0, y1 = b.get_y(), b.get_y() + b.get_height()
            if anchor is None and y1 > top:
                anchor = unit
            if y1 < top - 2 * page or y0 > top + 3 * page:
                unit.set_visible(False)
                unit.zoom_hidden = True
                (below if anchor else above).append(unit)
        below.reverse()  # both lists pop() the message nearest the view first
        hold = self.zoom_job
        if hold is None:
            hold = self.zoom_job = {"clock": clock}
            # the messages come back as the frame begins, before GTK styles what's shown in its tick callbacks (one
            # shown after them was styled a frame late: drawn at the old size, then the new); the place is noted
            # after them, so a touchpad's kinetic glide counts as the reader's
            hold["handlers"] = (clock.connect("before-paint", self.zoom_show),
                                clock.connect_after("update", self.zoom_frame))
            clock.begin_updating()
        hold.update(anchor=anchor, above=above, below=below, upper=None, calm=0, frames=0, shown=0)
        self.note_zoom_place()

    def note_zoom_place(self):
        """Where the reader is, as last drawn and scrolled since: the bottom, or how far the anchor
        is from the top of the view."""
        hold, adj = self.zoom_job, self.scroller.get_vadjustment()
        value = adj.get_value()
        hold["bottom"] = value + adj.get_page_size() >= adj.get_upper() - 1
        hold["offset"] = None
        if hold["anchor"] is not None:
            ok, b = hold["anchor"].compute_bounds(self.transcript)
            if ok:
                hold["offset"] = b.get_y() - value

    def zoom_show(self, _clock):
        """As each frame begins: show the next few hidden messages, styled at the new size in this frame."""
        hold = self.zoom_job
        if hold is None:
            return
        shown = 0
        while shown < 16 and (hold["above"] or hold["below"]):
            for side in (hold["below"], hold["above"]):
                if side:
                    unit = side.pop()
                    unit.set_visible(True)
                    unit.zoom_hidden = False
                    shown += 1
        hold["shown"] = shown

    def zoom_frame(self, clock):
        """Before each frame's layout: note the place afresh (so the reader can scroll meanwhile
        and isn't pulled back); the messages zoom_show brought back aren't laid out yet."""
        hold = self.zoom_job
        if hold is None:
            return
        self.note_zoom_place()
        if hold["shown"]:
            return
        # all shown: hold on until the height stops changing (code blocks follow the view's height,
        # which the composer's new text size changes, a frame later)
        upper = self.scroller.get_vadjustment().get_upper()
        hold["calm"] = hold["calm"] + 1 if upper == hold["upper"] else 0
        hold["upper"], hold["frames"] = upper, hold["frames"] + 1
        if hold["calm"] >= 3 or hold["frames"] > 60:
            self.zoom_job = None
            for handler in hold["handlers"]:
                clock.disconnect(handler)
            clock.end_updating()
            self.plan_fill()  # (the earlier chunks drawn on: they waited for the zoom)

    def hold_zoom_place(self):
        hold = self.zoom_job
        # (GTK clamps the view while the hidden messages are out, which on_scrolled took for the
        # reader going to the bottom: the view then followed new content down there afterwards)
        self.stick_bottom = hold["bottom"]
        if hold["bottom"]:
            self.follow.to_end()
        elif hold["offset"] is not None:
            ok, b = hold["anchor"].compute_bounds(self.transcript)
            if ok:
                self.follow.put(b.get_y() - hold["offset"])

    # ---- settings
    def open_settings(self):
        """Menu → Settings… (one at a time: the key again while it's open leaves it)."""
        if isinstance(self.get_visible_dialog(), SettingsDialog):
            return self.get_visible_dialog()
        dialog = SettingsDialog(self)
        dialog.present(self)
        return dialog

    # macOS: once a dialog has opened, the whole window drawn afresh (see Repaint), unless every frame already is
    REPAINT_AFTER_DIALOG = sys.platform == "darwin" and not FULL_REDRAWS

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

    # ---- full width
    def apply_full_width(self, on):
        # Adw.Clamp can't be switched off, so "off" is just a limit wider than any screen
        self.clamp.set_maximum_size(100_000 if on else tokens.px("gap.chat-max"))
        self.clamp.set_tightening_threshold(100_000 if on else tokens.px("gap.chat-tighten"))

    def on_fullwidth_toggled(self, action, value):
        action.set_state(value)
        self.apply_full_width(value.get_boolean())
        save_settings(full_width=value.get_boolean())

    # ---- control requests with replies
    def request(self, req, callback, refused=None):
        """callback(response) when claude does what's asked; refused(error) when it says no (with no refused, a no
        goes unsaid)."""
        if self.proc:
            self.pending[self.proc.control(req)] = (callback, refused)

    def report(self, most=None):
        """The open chat as text for an issue (Copy Transcript for an Issue; a note sent with Send Feedback): its model
        and tools as claude said them as it started, its effort, mode, host, folder and context, as the header shows
        them, then its transcript as it's drawn here (its mirror, for a chat on another host), each tool call with its
        input and result (meta.transcript_report); None if there's no transcript yet."""
        ctx, here = self.ctx_box, self.chat_host() == HERE_HOST
        init = getattr(self, "init_info", None) or {}
        tip = (ctx.get_tooltip_text() or "").split("\n")[1:]
        tools = [t for t in init.get("tools") or () if isinstance(t, str)]
        head = [(wording.say("report.app"), "Baude"), (wording.say("report.session"), self.session_id or "new"),
                (wording.say("common.model"), init.get("model") or self.model or "default"),
                (wording.say("report.picked"), self.model if self.model and self.model != init.get("model") else ""),
                (wording.say("meta_settings.effort"), self.effort or "default"),
                (wording.say("report.mode"), self.mode),
                (wording.say("new_session.host"), THIS_MACHINE if here else f"{remote_name(self.remote)} ({self.remote.host})"),
                (wording.say("common.folder"), self.cwd or ""), (wording.say("report.claude_code"), init.get("claude_code_version") or ""),
                (wording.say("report.context"),
                 (shown + (f" ({', '.join(tip)})" if tip else "")) if (shown := ctx.label.get_text()
                                                                       .removeprefix("context").strip(" :—"))
                 else wording.say("report.not_said_yet")),
                (wording.say("lookup.tools"), ", ".join(tools)),
                (wording.say("report.copied"), time.strftime("%Y-%m-%d %H:%M:%S %z"))]
        path = getattr(self, "history_path", None) or \
            ((find_session(self.session_id) or {}).get("path") if self.session_id and here else None)
        return meta.transcript_report(head, path, most=most) if path and os.path.exists(path) else None

    def copy_report(self):
        self.get_clipboard().set(self.report() or wording.say("report.nothing_said") + "\n")
        widgets.toast(self, wording.say("window.transcript_copied"))

    def refresh_context(self):
        model = self.model  # (the model the answer's for: another picked while it's asked for isn't, on_model_changed)
        self.request({"subtype": "get_context_usage"},
                     lambda usage: (self.remember_context(model, usage), self.show_context(usage)))

    CONTEXT_EVERY_MS = 1000

    def refresh_context_soon(self):
        """The meter asked again a moment after each step of a turn (a reply, a tool's result), at most once a second:
        asked only as a turn ended, a small model's 32k filled through a turn of tool calls with the bar standing
        still, then jumped (a large window hid it)."""
        if getattr(self, "context_soon", None) is None:
            self.context_soon = GLib.timeout_add(self.CONTEXT_EVERY_MS, self._context_now)

    def _context_now(self):
        self.context_soon = None
        if self.proc:
            self.refresh_context()
        return False

    def window_key(self, model):
        """What a model's window is kept by (modelslib.remember_window): its value, for a chat on this machine (as
        Meta's are); with its host, for one on another, whose account can give the same model another window."""
        host = self.chat_host()
        return model if host == HERE_HOST else f"{model}@{host}"

    def shown_window(self, model=None):
        """The window of a model (the chat's, if none's given), as far as it's known before its session says
        (meta.window, by window_key); on claude's own default before it's said which model that is, the window the
        default last had."""
        model = model or getattr(self, "model", None) or getattr(self, "model_shown", None)
        if not model or model == "default":
            return modelslib.said_window(self.window_key("default"))
        return meta.window(model, said=self.window_key(model))

    def remember_context(self, model, usage):
        """A session on model (None: claude's own default) has said its window (usage): kept for the next's, by
        window_key; the default's under "default" too, and under what it's resolved to."""
        for m in {model or getattr(self, "model_shown", None), None if model else "default"} - {None}:
            modelslib.remember_window(self.window_key(m), usage.get("maxTokens"))

    def show_context(self, usage):
        self.ctx_box.set_usage(usage)
        if self.outgrown:
            self.ctx_box.bar.set_value(1)
        # (a turn's stop button kept: asked again as it ends)
        # Codex can compact the thread before its next turn.
        over = self.ctx_box.over and model_program(self.model) != "codex"
        self.exhausted.set((over or self.outgrown) and not self.busy)

    def refresh_effort(self):
        self.request({"subtype": "get_settings"},
                     lambda r: self.sync_effort((r.get("applied") or {}).get("effort")))

    def on_ctrl_scroll(self, ctl, _dx, dy):
        if self.schema.on_top():  # (it covers the chat: its diagram zooms itself, about the pointer)
            return False
        if self.zoom_code_scroll(ctl, dy):  # (over the code: its own size)
            return True
        if not ctl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK or dy == 0:
            return False
        # a wheel click is a step; a touchpad sends a stream of small deltas (pixels on Wayland)
        # that add up to steps, or each would be a step and the size would race to the limit
        per_step = 1.0 if ctl.get_unit() == Gdk.ScrollUnit.WHEEL else 60.0
        if self.zoom_wheel * dy < 0:  # turned around
            self.zoom_wheel = 0.0
        self.zoom_wheel += dy / per_step
        steps = int(round(self.zoom_wheel, 6))  # (ten 0.1s add up to 0.999…)
        if steps:
            self.zoom_wheel -= steps
            self.set_zoom(self.zoom - steps * ZOOM_STEP)
        return True

    # ---- attachments
    def add_attachment(self, att):
        self.attachments.append(att)
        self.refresh_attach_strip()

    def remove_attachment(self, att):
        self.attachments.remove(att)
        self.refresh_attach_strip()

    def refresh_attach_strip(self):
        self.draft_changed()  # (an attachment begins a chat too)
        while (c := self.attach_row.get_first_child()) is not None:
            self.attach_row.remove(c)
            discard(c)
        for att in self.attachments:
            self.attach_row.append(attachment_widget(att, 48, self.remove_attachment))
        self.attach_strip.set_visible(bool(self.attachments))

    def attach_paths(self, paths):
        for path in paths:
            if path and os.path.isfile(path):
                self.add_attachment(Attachment.from_path(path))
            elif path:
                self.note(f"can't attach {path}: not a file")

    def attach_texture(self, texture):
        try:
            self.add_attachment(Attachment.from_texture(texture))
        except (GLib.Error, ValueError) as e:
            self.note(f"can't attach image: {e}")

    def on_pick_attachments(self, *_):
        dlg = Gtk.FileDialog(title="Attach files", modal=True)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        for name, mimes in (("All files", None),
                            ("Images & PDFs", ["image/*", "application/pdf"])):
            f = Gtk.FileFilter(name=name)
            for m in mimes or []:
                f.add_mime_type(m)
            if mimes is None:
                f.add_pattern("*")
            filters.append(f)
        dlg.set_filters(filters)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            self.attach_paths([files.get_item(i).get_path() for i in range(files.get_n_items())])
        dlg.open_multiple(self, None, done)

    def on_drop(self, _target, value, _x, _y):
        if self.read_only:
            return False  # (nothing to send them with)
        if isinstance(value, Gdk.FileList):
            self.attach_paths([f.get_path() for f in value.get_files()])
        elif isinstance(value, Gdk.Texture):
            self.attach_texture(value)
        else:
            return False
        self.input.grab_focus()
        return True

    def on_paste(self, view):
        """A paste, however it's asked for (⌘V, Ctrl+V, Shift+Insert, the menu): copied files or a copied image
        attached (paste_attachment); text pasted as text."""
        if self.paste_attachment():
            view.stop_emission_by_name("paste-clipboard")

    IMAGE_NAME = re.compile(r"\S+\.(?:png|jpe?g|gif|webp|heic|tiff?|bmp|svg)|[a-z][\w+.-]*://\S+", re.I)

    def paste_attachment(self):
        """Copied files, or a copied image, attached. An image copied with text beside it: the image, if the text's
        only its name or address (a browser's, Finder's: IMAGE_NAME), else the text (a spreadsheet's cells, copied
        with a picture of them). Returns False to let text paste."""
        clip = self.input.get_clipboard()
        formats = clip.get_formats()
        image = clipboard_has(formats, Gdk.Texture, ("image/",))

        def got_texture(c, res):
            try:
                self.attach_texture(c.read_texture_finish(res))
            except GLib.Error as e:
                self.note(f"paste failed: {e.message}")

        def got_text(c, res):
            try:
                text = c.read_text_finish(res) or ""
            except GLib.Error as e:
                text = None
                if not image:
                    self.note(f"paste failed: {e.message}")
            if image and (not text or self.IMAGE_NAME.fullmatch(text.strip())):
                c.read_texture_async(None, got_texture)  # (the image's own name or address: the image's meant)
            elif text:
                buf = self.input.get_buffer()
                buf.begin_user_action()  # (one step to undo, as GTK's own paste)
                buf.delete_selection(True, True)
                buf.insert_at_cursor(text)
                buf.end_user_action()
                self.input.scroll_mark_onscreen(buf.get_insert())

        def image_or_text():
            if clipboard_has(formats, GObject.TYPE_STRING, ("text/plain", "text/uri-list")):
                clip.read_text_async(None, got_text)
            elif image:
                clip.read_texture_async(None, got_texture)

        if clipboard_has(formats, Gdk.FileList, ("text/uri-list", "x-special/gnome-copied-files")):
            def got_files(c, res):
                try:
                    paths = [f.get_path() for f in c.read_value_finish(res).get_files()]
                except GLib.Error as e:
                    self.note(f"paste failed: {e.message}")
                    return
                if paths and all(paths):
                    self.attach_paths(paths)
                else:  # (web addresses, not files here: the image beside them, else the text they are)
                    image_or_text()
            clip.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, got_files)
            return True
        if not image:
            return False
        image_or_text()
        return True

    def update_status(self, extra=None):
        if extra is not None:
            self.status_extra = extra
        self.working_dots.set_working(self.busy)
        here = self.chat_host() == HERE_HOST
        there = None if here else self.remote_folder()
        self.status.set_text(f"{(remote_path(there, self.remote) if there else '') if not here else self.short_cwd()}"
                             f"   {getattr(self, 'status_extra', '')}")
        self.cost_label.set_text(self.cost_said())
        self.status.set_tooltip_text(there or ("" if not here else self.run_cwd))
        self.host_label.set_text(THIS_MACHINE if here else remote_name(self.remote))
        self.host_label.set_tooltip_text(wording.say("status_bar.host_tip") if here else
                                         wording.say("status_bar.host_tip_at", address=self.remote.host))

    def cost_said(self):
        """What the chat's spent, as the status bar says it: claude's figure ($0.0125); on a model run on your own
        machines (providers.runs_free), which a turn adds nothing to, $0.00 (local) while it's spent nothing, so it
        reads as a price and not as one missing."""
        if not self.total_cost and providerslib.runs_free(self.model):
            return wording.say("status_bar.cost_local")
        return wording.say("status_bar.cost", cost=f"{self.total_cost:.4f}")

    def remote_folder(self):
        """A chat on another host's folder there, as its host says it: in the sessions it lists, else as it said it
        when the chat was attached to (RemoteProc's listing: a new chat it doesn't list yet), else as last said; None
        till it's said."""
        sid = self.session_id
        said = (self.remote.sessions.get(sid) or {}).get("cwd") or \
            ((getattr(self.proc, "listing", None) or {}).get("cwd") if isinstance(self.proc, RemoteProc) else None)
        if said:
            self.remote_cwd = (sid, said)
        return said or (self.remote_cwd[1] if getattr(self, "remote_cwd", (None,))[0] == sid else None)

    # ---- process lifecycle
    def runs_here(self):
        """Is the chat's claude this process's own (ClaudeProc)? A Claude Desktop chat's (desktop_chat, however it's
        opened: is_desktop). Else every chat of Baude's, new or on this machine already, runs in the host process
        (take_up), as a host's session does, so a restart or an update of Baude's never stops it."""
        return self.remote is None and (self.desktop_chat or is_desktop(self.session_id))

    def start_proc(self):
        if self.remote is not None:
            return self.start_remote_proc()
        if not self.runs_here() and self.session_id is None and not self.resume_pending:
            return self.start_draft()  # (a new chat: in the host process from the start)
        if not self.runs_here():  # (one on this machine already: nothing runs till its first message: take_up)
            self.proc = None
            if local_host() is None:  # (started now, so it's there when it's wanted: its commands and models meanwhile)
                self.local_server(lambda _r: self.proc is None and self.remote is None and self.apply_host_init(),
                                  quiet=True)
            self.apply_host_init()
            self.update_status()
            return
        try:
            # while a session is only being viewed, claude runs fresh (it supplies models and commands
            # but writes nothing); it resumes the session when the first message is sent
            resume = None if self.resume_pending else self.session_id
            # (how what runs the model is started: baudelib.models. Apple's model: apple-claude, Manual, no effort, a
            # chat of its own, never carried on; a model on this Mac: pi-claude, Manual, its thinking level)
            kind = modelslib.program(self.model)
            effort = (self.local_effort if kind["name"] == "pi" else self.effort) if self.effort_dd.get_sensitive() else None
            run = modelslib.launch(self.model, self.mode, effort, resume if kind["resumes"] else None, resumed=True)
            if run["missing"]:
                self.note("Apple Foundation Model: apple-claude isn't built yet (swift build -c release, in apple-claude/)")
                self.proc = None
                self.update_status()
                return
            self.proc = ClaudeProc(self.on_msg, self.on_exit, self.run_cwd, self.model, run["mode"], run["resume"],
                                   run["effort"], extra=run["extra"], exe=run["exe"])
            self.proc.resumed = resume
            if self.resume_reason and self.resume_reason[0] == self.session_id:  # (its last claude here exited: on_exit)
                if run["resume"]:  # (Apple's model starts afresh: nothing resumed)
                    self.note(f"— resumed from {self.resume_reason[1]} —")
                self.resume_reason = None
            self.pending.clear()
        except OSError as e:
            self.note(f"failed to start claude: {e}")
            self.proc = None
        self.update_status()

    def start_remote_proc(self):
        """A network session's claude, on its server (RemoteProc): while it's only viewed, what it asks and its models
        and commands (its transcript, fetched as it grows, is the rest: sync_remote); once you write in it, all it
        says, as it says it. Nothing if its server's gone."""
        r = self.remote
        self.proc, self.remote_tried = None, time.monotonic()
        if self.drop_session != self.session_id:  # (another chat's drop: not this one's to end, or say)
            self.drop_running = self.drop_session = None
        if r.state == "alive":
            try:
                self.proc = RemoteProc(self.on_msg, self.on_exit, r.reach_where(), self.session_id,
                                       live=not self.resume_pending,  # (this machine's: as Baude's own claude would
                                       env=claudelib.claude_env() if r.mine else None,  # have had it)
                                       always_allow=sorted(self.allowed_in(self.session_id)))
            except OSError as e:
                refused = isinstance(e, baudelib.client.Refused)
                if refused:  # (its host there, but the session not to be had)
                    self.drop_running = self.drop_session = None
                if refused and str(e).startswith("no session"):  # (its host doesn't know it: session_missing)
                    return self.session_missing(r)
                name = "daemon" if r.mine else remote_name(r)
                kind = "refused" if refused else "unreachable"
                # (dropped: said under the chat already. Each kind of failure said once a chat, however often it's
                # tried again)
                if self.drop_running is None and self.attach_said != (self.session_id, kind):
                    self.note(f"{name} wouldn't open this chat: {e}" if refused else f"couldn't reach {name}: {e}")
                    self.attach_said = (self.session_id, kind)
        if self.proc is not None:
            self.attach_said = self.missing_session = self.missing_reason = self.retaken = None
            self.allowed_in(self.session_id).update(self.proc.listing.get("always_allow") or ())  # (what its host
            # allows it: another head's too)
        if self.proc is not None and self.drop_running is not None:  # (attached again after a drop: its claude gone
            if self.drop_running and self.proc.listing.get("state") == "stopped":  # meanwhile, the next message
                self.resume_reason = (self.session_id, "network error")  # resumes it: said then, begin_resume)
            if self.proc.listing.get("state") not in ("working", "waiting"):
                self.settle_kept_compaction()  # (its turn over meanwhile)
            self.drop_running = self.drop_session = None
        if self.proc is not None:
            self.proc.mode = self.proc.listing.get("mode")
            self.proc.cost_seen = self.total_cost  # (its claude counts on from what the transcript says was spent)
            self.pending.clear()
            for msg in list(self.proc.asks.values()):  # (what it's waiting on an answer to)
                self.h_control_request(msg)
        self.update_status()
        self.update_banner()

    def session_missing(self, r):
        """A chat's host answered but has no such session (its state lost, a draft it dropped, ended there). Nothing
        shown in the chat (a draft it dropped): a new chat in its place, the text box as it is. On this machine with a
        Claude transcript here: taken up again by its id, as take_up does, and attached (once a loss: if that doesn't
        hold, as below). Else it's marked missing: the banner says so, with New chat, send's off, and it isn't tried
        again till its host lists it again (on_network)."""
        sid = self.session_id
        if r.mine and not self.shows_anything() and not getattr(self, "chat_sent", False):
            self.restart_proc(resume=False)
            return
        info = find_session(sid) if r.mine and sid else None  # (Claude's transcripts only: a Codex chat, or pi's,
        self.missing_reason = None                            # its host can't take up again)
        if info is not None and self.retaken != sid:
            self.retaken = sid
            effort = (self.local_effort if model_program(self.model) == "pi" else self.effort) \
                if self.effort_dd.get_sensitive() else None
            reply = r.call({"op": "start", "adopt": sid, "cwd": info.get("cwd") or self.run_cwd, "model": self.model,
                            "effort": effort, "mode": self.mode, "env": claudelib.claude_env(), "group": ""},
                           timeout=10)
            if reply.get("op") == "started":
                r.sessions[sid] = reply["session"]
                return self.start_remote_proc()
            self.missing_reason = baudelib.wire.said(reply) or wording.say("agent_proxy.no_answer")
        r.sessions.pop(sid, None)  # (as its host has it: listed again, it's there again)
        self.missing_session = sid
        self.update_status()
        self.update_banner()

    def shows_anything(self):
        """Whether the chat shows anything of a conversation: a turn drawn, or a transcript it's viewed from."""
        if self.view_path and os.path.exists(self.view_path) and os.path.getsize(self.view_path):
            return True
        child = self.transcript.get_first_child()
        while child is not None:
            if not child.has_css_class("sysnote") and child is not getattr(self, "cut_row", None):
                return True
            child = child.get_next_sibling()
        return False

    def new_chat_on_host(self):
        """New chat (the banner's, for a chat its host no longer has): one on that host."""
        r = self.remote
        if r is None or r.mine:
            self.new_session()
        else:
            self.new_session_on(r)

    def side_question_proc(self, on_msg, on_exit):
        """(proc, forked) for a side question in a chat of Baude's: a session of the host process's, forked from the
        chat there (taken up first, if it isn't yet), that keeps nothing (--no-session-persistence), is listed nowhere,
        and goes when it's closed; a fresh one for a chat with nothing saved yet. (None, False) if it can't be had
        (said)."""
        r = self.remote or local_host()
        if self.remote is None and r is not None and self.session_id and find_session(self.session_id):
            reply = r.call({"op": "start", "adopt": self.session_id, "cwd": self.recorded_cwd(), "model": self.model,
                            "mode": self.mode, "group": "", "env": claudelib.claude_env()}, timeout=10)
            if reply.get("op") != "started":  # (the host's to fork: taken up, the chat here still only viewed)
                self.note(wording.say("side_questions.not_asked", why=baudelib.wire.said(reply)))
                return None, False
        if r is None or not baudelib.wire.takes(r.info, "ephemeral"):
            self.note(f"{remote_name(r)}'s host process can't ask a side question" if r else
                      f"{THIS_MACHINE}'s host process can't ask a side question: restart it")
            return None, False
        mode = "default" if model_program(self.model) == "codex" else "plan"
        params = {"op": "start", "ephemeral": True, "mode": mode, "model": self.model, "prompt": ""}
        # (the chat's, forked, if it's the host's: a fresh, context-free one, if nothing's written in it yet, the host
        # says: started's forked)
        params.update({"fork_from": self.session_id} if self.session_id and (self.remote is not None or
                                                                              find_session(self.session_id)) else
                      {"cwd": self.run_cwd if r.mine else "~"})
        reply = r.call(params, timeout=10)
        if reply.get("op") != "started":
            self.note(wording.say("side_questions.not_asked", why=baudelib.wire.said(reply)))
            return None, False
        forked = bool(reply.get("forked"))
        try:
            return RemoteProc(on_msg, on_exit, r.reach_where(), reply["session"]["id"], live=True,
                              env=claudelib.claude_env() if r.mine else None), forked
        except OSError as e:
            self.note(wording.say("side_questions.not_asked", why=e))
            return None, False

    def start_draft(self):
        """A new chat, in the host process from the start, as a draft there (listed and kept once it's written in, gone
        if it never is), its own claude giving it its commands and models, as one of Baude's own did; once This Mac's
        host process is there, if it isn't yet (started for it)."""
        self.proc = None
        r = local_host()
        if r is None:
            self.draft_wanted = wanted = object()
            self.local_server(lambda _r: getattr(self, "draft_wanted", None) is wanted and self.remote is None
                              and self.session_id is None and not self.resume_pending and self.start_draft(), quiet=True)
            self.update_status()
            return
        self.draft_wanted = None
        effort = (self.local_effort if model_program(self.model) == "pi" else self.effort) \
            if self.effort_dd.get_sensitive() else None
        reply = r.call({"op": "start", "draft": True, "prompt": "", "cwd": self.run_cwd, "model": self.model,
                        "effort": effort, "mode": self.mode, "env": claudelib.claude_env(),
                        "group": getattr(self, "draft_group", "")}, timeout=10)
        if reply.get("op") != "started":
            self.note(f"{THIS_MACHINE}'s host process didn't start a chat: {baudelib.wire.said(reply)}")
            self.update_status()
            return
        self.remote, self.session_id, self.desktop_chat = r, reply["session"]["id"], False
        r.sessions[self.session_id] = reply["session"]  # (listed before it next says what it has)
        self.resume_pending, self.view_path = False, None
        MarkdownView.base_dir = self.run_cwd
        self.start_remote_proc()

    def apply_host_init(self):
        """A chat of Baude's with no claude yet (a new one, or one the host process hasn't taken up): its commands and
        models as This Mac's host process last had them from a claude (its info's init), till its own say."""
        r = local_host()
        init = (r.info.get("init") if r is not None else None) or {}
        if init:
            self.completer.set_commands(init.get("commands") or [])
            self.sync_models(init.get("models"))

    def take_up(self):
        """A chat of Baude's on this machine, to be written in: made the host process's (start: a new one; adopt: one
        claude has already, a terminal's or one Baude ran itself before, by its own id), and talked to through it from
        then on, as a host's session is. True if it is; False if This Mac's host process isn't there yet; None if it
        wouldn't take it up (said)."""
        r = local_host()
        if r is None:
            return False
        effort = (self.local_effort if model_program(self.model) == "pi" else self.effort) \
            if self.effort_dd.get_sensitive() else None
        params = {"op": "start", "cwd": self.run_cwd, "model": self.model, "effort": effort, "mode": self.mode,
                  "env": claudelib.claude_env(), "group": ""}  # (Baude's own chats: in no group)
        if self.session_id and find_session(self.session_id):
            params["adopt"], params["cwd"] = self.session_id, self.recorded_cwd()
        else:  # (a new chat: in the group it was started in, its draft not made: start_draft)
            params["prompt"], params["group"] = "", getattr(self, "draft_group", "")
        reply = r.call(params, timeout=10)  # (on the main loop: this machine's socket, an answer in a moment)
        if reply.get("op") != "started":
            self.note(f"{THIS_MACHINE}'s host process didn't take this chat up: {baudelib.wire.said(reply)}")
            return None
        listed = reply["session"]
        r.sessions[listed["id"]] = listed  # (listed before it next says what it has)
        self.remote, self.session_id, self.desktop_chat = r, listed["id"], False
        self.resume_pending, self.view_path, self.gone_tree = False, None, None
        self.tail.stop()
        self.start_remote_proc()
        self.sidebar.fill()
        return True

    def restart_proc(self, resume=True):
        if self.proc:
            p, self.proc = self.proc, None
            p.on_exit = lambda *_: None
            p.kill()
            self.remember_own_writes()
        if not resume:
            self.session_id, self.desktop_chat = None, False  # (a new conversation: Baude's, the host process's)
            if self.remote is not None:  # (a new conversation: the host process's draft, from start_proc)
                self.remote, self.resume_pending, self.view_path = None, False, None
                self.tail.stop()
                MarkdownView.base_dir = self.run_cwd
                self.update_banner()
            self.reset_stream_state()
        else:
            # the same conversation carries on: keep tool_use_id -> card, so results, task endings
            # and subagent messages that arrive later still find their cards (only the half-drawn
            # stream of the old process is dropped)
            self.blocks, self.streamed_ids = {}, set()
        for waiting in list(self.waiting_cards):  # (the claude that asked is gone: nothing to answer. A copy: answered()
            # takes the card out of waiting_cards as it goes)
            if isinstance(waiting, ToolCard):
                waiting.answered()
        self.waiting_cards.clear()
        self.turn_finished = False
        self.set_busy(False)
        self.start_proc()

    # ---- a Claude Desktop chat left mid-turn (another opened, or a new one started): Baude's own claude, here, which
    # goes on with the turn, not killed (every other chat's is the host process's, which goes on there whoever's
    # attached: detach_proc only lets go of it),
    # writing its transcript as it goes, and is this window's again when the chat's opened (attach_proc), in this
    # window or another. Meanwhile a permission it asks for waits, its chat's dot saying so (Sidebar.file_state), and
    # is asked when the chat's opened. Once its turn's over it's let go; it goes with the window.
    def detach_proc(self):
        if self.remote is not None:  # (a host's session: its turn goes on there, attached or not: let go of here)
            if self.session_id:  # (what it does there said as it's listed: watch_left_remote)
                self.left_remote[self.session_id] = [self.remote, "working"]
            p, self.proc = self.proc, None
            p.on_msg, p.on_exit = (lambda *_: False), (lambda *_: None)
            p.kill()
            self.waiting_cards.clear()
            self.set_busy(False)
            return
        p, self.proc = self.proc, None  # (a Claude Desktop chat's claude, Baude's own: always one with an id)
        p.owner, p.key, p.mode, p.model = self, self.session_id or f"new-{id(p)}", self.mode, self.model
        p.path = (find_session(self.session_id) or {}).get("path") if self.session_id else None
        p.on_msg, p.on_exit = self.on_background_msg, lambda _code, p=p: self.let_go_background(p)
        p.commands = {f"c{i}": w for i, w in enumerate(self.own_commands())}  # (running as it's left: track_commands)
        self.background[p.key] = p
        self.waiting_cards.clear()
        self.set_busy(False)

    def watch_left_remote(self):
        """Network chats left mid-turn here (detach_proc), their turns going on at their hosts: as one's listed waiting
        for an answer, or its turn over, said (tell_desktop), as its dot says it. Once over, no longer watched."""
        for sid, seen in list(self.left_remote.items()):
            r, before = seen
            listed = r.sessions.get(sid)
            state = "gone" if listed is None or r.state == "dead" else listed.get("state") or "idle"
            if state == before:
                continue
            seen[1] = state
            if state not in ("working", "waiting"):
                del self.left_remote[sid]
            if state in ("waiting", "idle", "stopped"):
                info = remote_info({**listed, "server": r})
                self.tell_desktop(sid, wording.say("asks.waiting") if state == "waiting" else
                                  wording.say("meta.finished"),
                                  title=listed.get("title"), info=info)

    def on_background_msg(self, proc, msg):
        t, req = msg.get("type"), msg.get("request") or {}
        track_commands(proc.commands, msg)
        if t != "stream_event":
            own_writes(proc)  # (what it writes to its transcript meanwhile is Baude's, not another program's)
        if t == "control_request" and req.get("subtype") != "can_use_tool":
            proc.send({"type": "control_response", "response": {
                "subtype": "error", "request_id": msg["request_id"], "error": f"baude: unsupported {req.get('subtype')}"}})
        elif t == "control_request":
            if req.get("tool_name") in self.always_allowed.get(proc.key, ()) and model_program(proc.model) == "claude":
                proc.respond(msg["request_id"], {"behavior": "allow", "updatedInput": req.get("input", {})})
            else:
                proc.asks[msg["request_id"]] = msg
                self.sidebar.restate()
                self.tell_desktop(proc.key, wording.say("asks.waiting_for_tool",
                                                        tool=req.get("tool_name") or wording.say("asks.a_tool")))
        elif t == "result":
            self.let_go_background(proc)
            self.tell_desktop(proc.key, turn_said(msg))
        return False

    def let_go_background(self, proc):
        """A chat's claude left mid-turn, its turn over (or the window closing): stopped, its chat's dot back to its
        transcript's."""
        if self.background.get(getattr(proc, "key", None)) is proc:
            del self.background[proc.key]
        proc.on_msg, proc.on_exit = (lambda *_: False), (lambda *_: None)
        proc.kill()
        own_writes(proc, exiting=True)
        self.sidebar.restate()

    def take_background(self, session_id):
        """The claude still at a turn in a chat left mid-turn, from whichever window left it: None if there's none."""
        if (p := background_proc(session_id, BaudeWindow)) is not None:
            p.owner.background.pop(p.key, None)
            p.owner.sidebar.restate()
        return p

    def attach_proc(self, p):
        """A chat's claude, left mid-turn (detach_proc), this window's again: what it did meanwhile is in the history
        just drawn, what it does next comes as it's done, and what it asked meanwhile is asked here."""
        if self.proc:
            old, self.proc = self.proc, None
            old.on_exit = lambda *_: None
            old.kill()
        self.resume_pending, self.view_path, self.resumed_note = False, None, False
        self.tail.stop()
        self.update_banner()
        p.on_msg, p.on_exit = self.on_msg, self.on_exit
        self.proc = p
        self.pending.clear()
        self.blocks, self.streamed_ids = {}, set()
        self.waiting_cards.clear()
        self.turn_finished = False
        if p.mode in self.mode_dd.values:  # (the mode it's running in, not the one a chat opens in)
            self.mode = p.mode
            self.mode_dd.select(p.mode)
        self.set_busy(True)
        for msg in list(p.asks.values()):
            self.h_control_request(msg)

    def clear_transcript(self, closing=False, keep_box=False, gone=False):
        """The chat emptied for another (closing: the window's going). Its draft kept for it (keep_draft), the box
        emptied for the next one's. keep_box: what's typed in the box goes on to the next one (a new conversation for
        another kind of model: remote_new_conversation; the chat deleted: carry_on), the rest of its draft (its queue,
        a revert written over) kept for it, or, gone, dropped with it."""
        self.remember_place()  # (to open it there again)
        key = self.draft_key()  # (what its draft's kept under, below)
        self.restore_scroll = None
        self.chat_sent, self.listed_begun = False, None  # (another chat: nothing sent in it yet)
        self.carry_box = None  # (a new conversation that opens later doesn't take this box: open_remote)
        carried = None
        if keep_box:  # (what's typed: under a revert, the box as it was before it)
            text = self.reverting["draft"] if self.reverting is not None else widgets.typed_text(self.input)
            carried = (text, [len(text)] * 2 if self.reverting is not None else widgets.caret_of(self.input),
                       list(self.attachments))
            if self.reverting is not None:
                self.reverting["draft"] = ""
            self.set_box(widgets.typed_text(self.input) if self.reverting is not None else "", None, [])
        if self.reverting is not None:  # (a revert not sent: gone with its chat, the box as it was before it, and
            rev, self.reverting = self.reverting, None  # what's been written over it in its draft, ahead of that)
            typed = widgets.typed_text(self.input).strip()
            self.set_box(rev["draft"], rev["draft"] and [len(rev["draft"])] * 2)
            if typed and typed != (rev["turn"].text or "").strip():
                self.put_ahead([typed], [])
        if self.send_after_result is not None:  # (nor what was to be sent after one)
            GLib.source_remove(self.send_after_result)
            self.send_after_result = None
        while (c := self.transcript.get_first_child()) is not None:
            self.transcript.remove(c)
            discard(c)
        if self.queued:  # (not sent: in its draft, before what's in the box, as a queued message's revert puts it)
            self.put_ahead([t.text for t in self.queued if t.text], [a for t in self.queued for a in t.atts])
        self.unqueue(list(self.queued))  # (another chat: this one's queue gone from under it)
        self.held_after_revert = None
        for t in getattr(self, "held_turns", {}).values():  # (and what its host holds for it: the next one's own)
            if t.get_parent() is self.queue_box:
                self.queue_box.remove(t)
        self.held_turns = {}
        self.queue_box.set_visible(False)
        if not gone:
            self.keep_draft(key)  # (the box this chat's to come back to; the next chat's own put in it: take_draft)
        if carried is not None:
            self.set_box(*carried)
        self.cut_row = self.cut_pending = None  # (the next chat's own: cut_state)
        self.cut_continuing = False
        self.set_slack(None)  # (the next chat's no room left under it)
        self.toggle_held = None
        self.total_cost = 0.0
        self.ctx_box.reset()
        self.outgrown = False
        self.exhausted.set(False)
        self.show_title(None)
        self.tasks.clear()
        self.sub_tails.clear()
        self.artifacts.clear()
        if not closing:  # (they're every window's: one closing leaves the others theirs)
            ARTIFACT_TITLES.clear()
        self.ref_surfaces.clear()
        self.reply_views.clear()
        self.orphan_results.clear()  # (results whose calls the chat before never drew: output, images and all)
        self.history, self.chunk_slots, self.looked_in = None, None, (None, set())
        self.drop_fills()
        self.chat_find.close()  # (its matches are the chat before's labels: not kept, nor looked for in the next)
        self.panel.reset()
        self.update_tasks_indicator()

    # ---- swapping the chat, with Claude's fade: the chat goes lighter, swaps, and the new one comes back
    def switch_to(self, action, show=True):
        """Swap the chat (another session, a new one, another folder) after a short fade, so the swap is seen.
        Clicks while it's faded (or a slow session still loading) don't start the fade again: the chat stays
        faded and the last one wins (Fade.swap). The chat's tab is shown first, whatever was (the Feedback tab, a
        file's code), so the swap's seen and what's picked is what's shown; show False: not (a network chat's, once
        it's fetched: shown as it was picked, and what's been shown since is left in front)."""
        if show:
            self.show_chat()
        self.chat_fade.swap(action)

    def show_chat(self):
        """The chat's tab shown (from the Feedback tab, a file's code)."""
        self.tabs.show("chat")

    def new_session(self, run_in=None, group=None, carry=False):
        """A new chat in this folder; run_in: the folder claude runs in, if not this one (a worktree picked with
        Open folder…). A worktree's chat's folder is its repository, so a new one from there starts in that. group:
        the group it's started in (New session with one group picked in ⋮), kept while it's a draft (another folder or
        model) and dropped once another chat's opened; None: the new chat's before it, if it was one, else none.
        carry: the open chat's gone (carry_on)."""
        if group is not None or self.remote is None or not (self.remote.sessions.get(self.session_id) or {}).get(
                "draft"):
            self.draft_group = group or ""
        self.opening_remote = None  # (a network session still being fetched: not opened after all)
        self.always_allowed.pop(None, None)  # (a new chat: nothing allowed in it yet)
        run_in = self.norm_dir(run_in) if run_in else self.cwd if os.path.isdir(self.cwd) else self.run_cwd
        if run_in != self.run_cwd:
            self.switching_chat = True  # (the new chat's own switches come below)
            self.set_cwd(run_in)
        self.remember_own_writes()
        if self.busy and self.proc is not None:
            self.detach_proc()  # (the turn goes on: detach_proc)
        self.resume_pending, self.view_path, self.remote, self.desktop_chat = False, None, None, False
        MarkdownView.base_dir = self.run_cwd  # (a network session's folder is its server's: open_remote)
        self.tail.stop()
        self.update_banner()
        self.clear_transcript(keep_box=carry, gone=carry)
        self.default_mode()
        self.restart_proc(resume=False)
        self.signal_pending, self.switching_chat = None, False
        self.apply_chat_signal()  # (a new chat: links shown, Signal not activated)
        if not carry:
            self.take_draft()  # (a new chat's, if one was left)
        self.apply_read_only()
        self.sidebar.fill()
        self.panel.git_page.chat_changed()  # (its own Git page, in this folder too)

    # ---- read-only sessions (☰ → Read-only session): no text box, for a chat you only watch (tailing one with
    # Signal on, say); the status line, with the context it uses, stays. Remembered per session. A session another
    # program has (update_banner) is read-only too while it does, and has no text box either: show_composer
    def read_only_ids(self):
        return [i for i in load_settings().get("read_only_sessions") or [] if isinstance(i, str)]

    def set_read_only(self, on):
        """Make this session read-only, or not (and remember it for the session, once it has an id)."""
        if self.session_id:
            ids = [i for i in self.read_only_ids() if i != self.session_id] + ([self.session_id] if on else [])
            save_settings(read_only_sessions=ids or None)
        self.apply_read_only(on)

    def apply_read_only(self, on=None):
        """Show or hide the text box as the session asks (on: as given, None: as remembered for it)."""
        if on is None:
            on = bool(self.session_id) and self.session_id in self.read_only_ids()
        self.read_only = on
        if (action := self.lookup_action("readonly")) is not None:
            action.set_state(GLib.Variant.new_boolean(on))
        self.show_composer()

    def show_composer(self):
        """The text box, there unless the session can't be written to: read-only by choice (☰ → Read-only session),
        or while another program has it (update_banner). Read-only is no text box at all, not a greyed-out one."""
        hidden = self.read_only or self.locked
        self.composer.set_visible(not hidden)
        if hidden and self.input.has_focus():
            self.scroller.grab_focus()

    def on_exit(self, code):
        if self.remote is not None and code == "deleted":  # (deleted on its host, by another head: a new chat)
            self.proc = None
            self.set_busy(False)
            toast(self.sidebar, f"That session was deleted on {remote_name(self.remote)}")
            self.carry_on(self.session_id)
            return
        if self.remote is not None:
            # (nothing said in the chat. "lost": let go of with no exit said, its host process gone (or restarted onto
            # an update) or the connection fallen silent: the host keeps the session, its claude going on with a turn
            # if it's at one, and it's said under the chat till it's attached again (drop). Its claude's own exit, a
            # code (-1 too): the next message resumes it, and says why)
            # (its claude running: attached live, and its host listing it started, not just its draft attached)
            running = bool(getattr(self.proc, "live", False)) and \
                (self.remote.sessions.get(self.session_id) or {}).get("state") not in (None, "stopped")
            if code == "lost":
                self.drop(running)
            elif code not in (0, "stopped"):  # (stopped: Stop, not an error)
                self.resume_reason = (self.session_id, "claude binary error")
            for waiting in list(self.waiting_cards):  # (what it asked: asked again as it's attached again, if it still
                if isinstance(waiting, ToolCard):  # waits; a copy: answered() takes it out as it goes)
                    waiting.answered()
            self.waiting_cards.clear()
            self.show_retry(None)
            if code == "lost":  # (its claude going on there: what it says as it's attached again settles it)
                self.compact_kept = True
            else:
                self.compact_ended()
            self.proc = None
            self.set_busy(False)
            self.remote_view()
            return
        self.compact_ended()
        if code != 0 and self.session_id and not self.resume_pending:  # (nothing said now: the claude started next
            self.resume_reason = (self.session_id, "claude binary error")  # resumes it, and says why: start_proc. One
            # only showing a session it isn't running (resume_pending): nothing to resume)
        self.proc = None
        self.set_busy(False)

    # ---- UI events
    def on_key(self, _ctl, keyval, _code, state):
        if self.completer.handle_key(keyval, state) or self.refs.handle_key(keyval, state):
            return True
        if composerview.leave_wrapped(self.input.get_buffer(), keyval, state):
            return True  # (just formatted, and Left or Right: outside its marks)
        if composerview.tab_key(self.composer_md, keyval, state):
            return True  # (a list item in or out a level, as on a Record page)
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and state & Gdk.ModifierType.ALT_MASK and \
                not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.SHIFT_MASK):
            return self.composer_md.move_item(-1 if keyval == Gdk.KEY_Up else 1)  # (a list item, past its neighbour)
        if not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK) and \
                composerview.wrap_selection(self.input.get_buffer(), chr(Gdk.keyval_to_unicode(keyval) or 0)):
            return True  # (a selection, and * ` or /: bold, code, italic)
        if keyval in (Gdk.KEY_v, Gdk.KEY_V) and state & Gdk.ModifierType.CONTROL_MASK and sys.platform == "darwin":
            self.input.emit("paste-clipboard")  # (GTK binds only ⌘V on a Mac: Ctrl+V pastes there as well)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if not composerview.sends(state):  # (Enter: a new line, a list's next item; Ctrl/⌘+Enter sends)
                return composerview.newline_key(self.composer_md, state)  # False: the TextView inserts a plain newline
            self.on_send()
            return True
        if keyval == Gdk.KEY_Escape and self.reverting is not None:
            if not self.revert_asked():  # (asked of claude already: its answer settles it)
                self.cancel_revert()
            return True
        if keyval == Gdk.KEY_Escape and self.busy:
            self.interrupt()
            return True
        return False

    def on_send(self):
        buf = self.input.get_buffer()
        typed = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip()
        if self.composer_box.gates and not self.busy and not (typed in ("/clear", "/new", "/cd", "/login", "/logout") or
                                                              typed.startswith(("/cd ", "/login "))):
            return  # (the context exhausted, a host gone or its connection dropped: only what Baude does itself goes)
        if (typed in ("/login", "/logout") or typed.startswith("/login ")) and self.remote is not None \
                and not self.remote.local:  # (signing in is a machine's: another's claude signs in there)
            buf.set_text("")
            self.completer.hide()
            self.note(wording.say("sign_in.signs_in_there", host=remote_name(self.remote)))
            return
        if typed == "/btw" or typed.startswith("/btw "):  # even mid-turn: it doesn't interrupt
            question = typed[4:].strip()
            if question:
                buf.set_text("")
                self.completer.hide()
                self.ask_side_question(question)
            return
        if typed in ("/login", "/logout") or typed.startswith("/login "):  # (claude auth: even mid-turn, too)
            buf.set_text("")
            self.completer.hide()
            if typed == "/logout":
                self.sign_out()
            else:
                try:
                    self.sign_in(shlex.split(typed[len("/login"):]))
                except ValueError as e:  # (a quote left open)
                    self.note(f"/login: {e}")
            return
        if (typed or self.attachments) and not self.busy and not (typed in ("/clear", "/new", "/cd") or
                                                                  typed.startswith("/cd ")) and self.remake_tree():
            return  # (its worktree being made again: sent once it is, from the box)
        if self.reverting is not None and not (typed in ("/clear", "/new", "/cd") or typed.startswith("/cd ")):
            if (typed or self.attachments or isinstance(self.reverting["turn"], SenderTurn)) and \
                    not self.revert_asked():  # (one of another's: made with nothing in the box, nothing sent after)
                self.commit_revert()  # (sent: the revert made, then this sent, reverted)
            return
        if self.busy:
            if (typed or self.attachments) and not (typed in ("/clear", "/new", "/cd") or typed.startswith("/cd ")):
                self.queue_message()  # (written mid-turn: queued, sent as the turn ends or with its ↑)
            else:
                self.interrupt()
            return
        if self.live_elsewhere():  # the input is off meanwhile; this is just the Enter key
            return
        buf = self.input.get_buffer()
        text = ComposerMarkdown.to_markdown(
            buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)).strip()
        if (text or self.attachments) and self.resume_pending:
            self.begin_resume()
        if not text and not self.attachments:
            return
        # (sent: still listed in the sidebar once the composer's emptied, before the chat's first saved)
        self.chat_sent = getattr(self, "chat_sent", False) or not (text in ("/clear", "/new") or text == "/cd" or
                                                                   text.startswith("/cd "))
        buf.set_text("")
        self.completer.hide()
        if text in ("/clear", "/new"):
            self.switch_to(self.new_session)
            return
        if text == "/cd" or text.startswith("/cd "):
            self.change_cwd(text[3:].strip() or "~")
            return
        self.send_text(text)

    # ---- the queue: messages written while the agent's at a turn (QueuedTurn), under the conversation, grey. A turn
    # that ends on its own sends them all, together as one message; one stopped (Esc, the stop button) leaves them.
    # A queued message's ↑ interrupts the turn and sends it with every one before it; its revert puts it back in the
    # box; its ✕ drops it. They're the open chat's: another chat opened, they go into its draft (clear_transcript)
    def queue_message(self):
        buf = self.input.get_buffer()
        text = ComposerMarkdown.to_markdown(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)).strip()
        atts, self.attachments = self.attachments, []
        self.refresh_attach_strip()
        buf.set_text("")
        self.completer.hide()
        turn = QueuedTurn(self, self.user_bubble(text, atts), text, atts)
        self.queued.append(turn)
        self.queue_box.append(turn)
        self.queue_box.set_visible(True)
        self.stick_bottom, self.restore_scroll = True, None

    def unqueue(self, turns):
        for t in turns:
            if t in self.queued:
                self.queued.remove(t)
            if t.get_parent() is self.queue_box:
                self.queue_box.remove(t)
            if self.queue_upto is t:  # (what was to be sent up to: the one before it, if any's left before it)
                self.queue_upto = None
        self.queue_box.set_visible(bool(self.queued or any(t.get_visible() for t in
                                                            getattr(self, "held_turns", {}).values())))

    def queue_drop(self, turn):
        self.unqueue([turn])

    def queue_edit(self, turn):
        """Its revert: out of the queue and into the box, before what's written there; sent (or queued again) as any."""
        buf = self.input.get_buffer()
        draft = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip()
        self.unqueue([turn])
        buf.set_text(turn.text + ("\n\n" + draft if draft else ""))
        buf.place_cursor(buf.get_iter_at_offset(len(turn.text)))
        self.attachments = list(turn.atts) + self.attachments
        self.refresh_attach_strip()
        self.input.grab_focus()

    def queue_send(self, turn):
        """Its ↑: it and the queued messages before it sent together: now, or once the interrupted turn's ended."""
        if turn not in self.queued:
            return
        if self.busy:
            if self.queue_upto is None or self.queued.index(turn) > self.queued.index(self.queue_upto):
                self.queue_upto = turn
            self.interrupt()
        else:
            self.queue_upto = turn
            self.flush_queue()

    def flush_queue(self):
        """The queue sent up to queue_upto (all of it: "all"), as one message (send_text), once no turn's going."""
        upto, self.queue_upto = self.queue_upto, None
        if upto is None or self.busy or not self.queued:
            return False
        turns = self.queued if upto == "all" else self.queued[:self.queued.index(upto) + 1]
        turns = list(turns)
        self.unqueue(turns)
        text = "\n\n".join(t.text for t in turns if t.text)
        buf = self.input.get_buffer()  # (sent from the box, as anything is: on_send; what was written there kept)
        draft, draft_atts = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip(), self.attachments
        buf.set_text(text)
        self.attachments = [a for t in turns for a in t.atts]
        self.on_send()
        left = widgets.typed_text(self.input).strip()
        buf.set_text("\n\n".join(p for p in (left, draft) if p))  # (not sent yet, or at all: still there, to send)
        self.attachments = self.attachments + draft_atts
        self.refresh_attach_strip()
        return False

    # ---- messages its host held for the chat (its held: a subagent's answer that came after the chat's host restarted,
    # which doesn't start its agent): under the conversation, over what's queued, grey, each sent with its ↑ as you'd
    # send it, or dropped with its ✕ (HeldTurn). They're its host's, as it lists them: there till one's done with,
    # whatever restarts meanwhile
    def show_held(self, gone=()):
        """The held messages as the open chat's host lists them now (gone: those just dropped, not again); one being
        sent (its ↑, its host's answer not come yet: held_send) left as it is."""
        listed = (self.remote.sessions.get(self.session_id) or {}) if self.remote is not None else {}
        held = {h["id"]: h for h in listed.get("held") or []
                if isinstance(h, dict) and isinstance(h.get("text"), str) and h.get("id") and h["id"] not in gone}
        turns = self.held_turns = getattr(self, "held_turns", {})
        for hid in [k for k, t in turns.items() if k not in held and not t.sending]:
            t = turns.pop(hid)
            if t.get_parent() is self.queue_box:
                self.queue_box.remove(t)
        last = None
        for hid, h in held.items():
            if hid not in turns:  # (from whom its host says it is; what a person's shown of it, else what it says)
                said = h["shown"] if isinstance(h.get("shown"), str) else h["text"]
                turns[hid] = HeldTurn(self, self.sender_bubble(baudelib.wire.clean_sender(h.get("sender")) or
                                                               {"kind": "host"}, baudelib.wire.unsaid(said).strip()), h)
                self.queue_box.insert_child_after(turns[hid], last)  # (over what's queued, in the order held)
            last = turns[hid]
        self.queue_box.set_visible(bool(self.queued or any(t.get_visible() for t in turns.values())))

    def held_send(self, turn):
        """Its ↑, once no turn's going: sent to the agent by its host and no longer held there, at once (held, send:
        kept if it can't be sent), on this chat's own connection, so what its claude says of it comes as it's said;
        drawn as sent meanwhile, and put back, its host's no said, if it isn't. A revert not made yet is made first,
        as sending does (on_send), and this sent after it (held_after_revert), what's in the box left there."""
        if turn.sending:
            return
        if self.busy:
            toast(self, wording.say("held_send.after_turn"))
            return
        if gates := self.composer_box.gates:  # (what's in the way of sending, said as it is)
            toast(self, wording.say("held_send.while_exhausted") if "exhausted" in gates else
                  wording.say("held_send.while_down") if "network" in gates else
                  wording.say("held_send.while_no_host") if "host" in gates else wording.say("held_send.while_other"))
            return
        if self.reverting is not None:
            if not self.revert_asked():
                self.held_after_revert = turn
                self.commit_revert()
            return
        if self.resume_pending:
            self.begin_resume()
        if self.proc is None:
            self.start_proc()
        if self.proc is None or not hasattr(self.proc, "held"):
            toast(self, "It can't be sent: this chat's agent couldn't be started")
            return
        h, sid = turn.held, self.session_id
        sender = baudelib.wire.clean_sender(h.get("sender")) or {"kind": "host"}
        from baudelib.meshtools import shown_of_told
        shown = h["shown"] if isinstance(h.get("shown"), str) else shown_of_told(h["text"])
        if shown is not None:  # (drawn as its host's sender store will have it: what's shown of it)
            sender = {**sender, "shown": shown}
        sent = self.sender_turn(sender, h["text"], (), None)
        turn.sending = sent
        turn.set_visible(False)
        self.show_held()  # (the queue's box, with nothing else in it, gone meanwhile)
        self.add(sent)
        self.stick_bottom, self.restore_scroll = True, None
        self.proc.stop_asked = False
        self.set_busy(True)

        def done(answer):
            turn.sending = None
            if self.session_id != sid:
                return
            if answer.get("op") != "error":
                self.show_held(gone={h["id"]})
                return
            if sent.get_parent() is not None:  # (not sent: as it was, and why said)
                sent.get_parent().remove(sent)
            self.set_busy(False)
            turn.set_visible(True)
            self.show_held()
            toast(self, wording.say("held_send.not_sent",
                                    why=baudelib.wire.said(answer) or wording.say("bot_browser.host_said_no")))
        self.proc.held(h["id"], True, done)

    def held_drop(self, turn):
        """Its ✕: dropped by its host, and here once it has; its host's no said, and it's kept."""
        if turn.sending or self.remote is None:
            return
        h, sid = turn.held, self.session_id
        turn.sending = "dropping"  # (left as it is meanwhile: show_held)
        turn.set_visible(False)
        self.show_held()

        def done(answer):
            turn.sending = None
            if self.session_id != sid:
                return
            if answer.get("op") != "error":
                self.show_held(gone={h["id"]})
                return
            turn.set_visible(True)
            self.show_held()
            toast(self, wording.say("held_send.not_deleted",
                                    why=baudelib.wire.said(answer) or wording.say("bot_browser.host_said_no")))
        self.remote.request({"op": "held", "session": sid, "id": h["id"]}, done)

    def recorded_cwd(self):
        """Where the open chat ran, for the host process to take it up in: its worktree even if it's gone (made again
        there with its first message: the host process's restore_folder), else where claude runs here."""
        gone = getattr(self, "gone_tree", None)
        return gone[1] if gone and gone[0] == self.session_id else self.run_cwd

    def remake_tree(self):
        """Written in a chat Baude runs itself (a Claude Desktop chat) whose worktree is gone (gone_tree): made again
        from its branch on a thread (baudelib.worktree: git), then what's in the box sent, in it; or, if it can't be,
        said, and sent in its repository, where claude finds the conversation by its id. True while that's under way.
        A chat the host process takes up has it made again there (recorded_cwd)."""
        if getattr(self, "remaking", None) is not None:
            return True
        gone = getattr(self, "gone_tree", None)
        if gone is None or gone[0] != self.session_id or self.remote is not None or not self.runs_here():
            return False
        self.gone_tree, self.remaking = None, gone[0]

        def work():
            try:
                result = worktreelib.restore(gone[1], gone[2])
            except worktreelib.Gone as e:
                result = e
            on_main(lambda: done(result))

        def done(result):
            self.remaking = None
            if self.session_id != gone[0] or self.remote is not None:
                return
            if isinstance(result, worktreelib.Gone):
                self.note(f"{result.why}; resuming in {self.short_cwd()}")
            else:
                if result:
                    self.note(wording.say("worktree_notes.remade",
                                          name=os.path.basename(main_folder(gone[1])[1] or gone[1]), branch=result))
                self.set_cwd(gone[1])
            self.on_send()
        threading.Thread(target=work, daemon=True, name="worktree").start()
        return True

    def send_text(self, text):
        """(on_send's) What was written, and what's attached, sent: to the host process for a chat of Baude's, taken up
        by it first (take_up; once it's started, if it isn't yet), or to the chat's claude here (a Desktop chat's)."""
        self.cut_pending = None  # (written to: no longer a turn cut short; its host forgets its own as it's sent)
        going = getattr(self, "cut_continuing", False) and text == CUT_CONTINUE  # (Continue's: not the box's to keep)
        if not going:
            self.cut_continuing = False
        self.show_cut(None)
        buf = self.input.get_buffer()
        if self.proc is None and self.remote is None and not self.runs_here():
            took = self.take_up()
            if took is False:  # (This Mac's host process isn't there yet: started, and this sent once it is, from the
                if not going:  # box it's kept in meanwhile, if it's still this chat's and still what's written)
                    buf.set_text(text)
                self.note(f"Starting {THIS_MACHINE}'s host process…")
                sid = self.session_id
                if going:  # (the host process never answering: Continue back once it's given up on)
                    GLib.timeout_add_seconds(22, lambda: self.continue_failed(sid) and False)

                def ready(_r):
                    if self.session_id != sid or self.remote is not None:
                        return
                    if going and getattr(self, "cut_continuing", False):
                        self.send_text(text)
                    elif not going and widgets.typed_text(self.input).strip() == text:
                        buf.set_text("")
                        self.send_text(text)
                self.local_server(ready)
                return
            if took is None:
                if going:
                    self.continue_failed()  # (Continue back, to press again)
                else:
                    buf.set_text(text)
                return
        if self.proc is None:
            self.start_proc()
        if self.proc is None:  # (it couldn't be started, or its server isn't there: what was written stays)
            if going:
                self.continue_failed()
            else:
                buf.set_text(text)
            if self.remote is not None:
                self.remote_view()
            return
        self.cut_continuing = False
        # (Continue's message goes alone: what's attached is kept for what's written next)
        atts, self.attachments = ([], self.attachments) if going else (self.attachments, [])
        self.refresh_attach_strip()
        said = str(uuid.uuid4())  # (claude keeps it as the message's: what a revert names, revert_to)
        self.add(UserTurn(self, self.user_bubble(text, atts), text, said))
        self.stick_bottom, self.restore_scroll = True, None  # (what you sent in view, whatever place was held)
        self.proc.stop_asked = False  # (a new turn: a stop asked of the last isn't this one's)
        self.proc.user(self.user_content(text, atts), said)
        self.set_busy(True)

    def chat_begun(self):
        """Whether the open chat is something yet, so the sidebar lists it before it's saved: something typed or
        attached, a message sent, a title given. A blank new chat isn't listed."""
        return bool(getattr(self, "chat_sent", False) or getattr(self, "session_title", None) or self.attachments or
                    self.input.get_buffer().get_property("text").strip())

    def draft_changed(self):
        """The composer's text changed: the sidebar lists the open chat once it's begun, and not while it's blank."""
        if getattr(self, "swapping_draft", False):  # (a chat's draft put away or back: the switch fills the sidebar)
            return
        if getattr(self, "sidebar", None) is not None and (begun := self.chat_begun()) != self.listed_begun:
            self.listed_begun = begun
            self.sidebar.fill()

    # ---- each chat's draft: what's written in the box, its caret and what's attached, kept for the chat as it's left
    # for another (clear_transcript) and put back as it's opened again (take_draft); every window's, kept over a
    # restart (BaudeApp.restart). A new chat (nothing sent in it, nor anything of it listed or saved) has the one draft,
    # "": the next new chat opens with it, as its host drops a draft nobody's on and its id goes with it
    DRAFTS = {}  # draft_key -> {"text", "caret", "attachments"}
    MAX_DRAFTS = 200

    GONE = set()  # the ids of chats deleted or trashed: forget_draft

    @classmethod
    def forget_draft(cls, sid):
        """A chat deleted or trashed (here, in another window, or on its host: forget_gone): its draft gone with it, and
        nothing kept under its id from now on (draft_key)."""
        cls.GONE.add(sid)
        cls.DRAFTS.pop(sid, None)

    LISTED = None  # {host's key: its sessions' ids}, as last listed while it was there: forget_gone

    @classmethod
    def forget_gone(cls):
        """The network changed: a chat a host that's there no longer lists (deleted there, by any head) forgotten."""
        net = network()
        with net.lock:
            now = {r.key: set(r.sessions) for r in net.servers.values() if r.state == "alive"}
        for key, ids in (cls.LISTED or {}).items():
            for sid in ids - now.get(key, ids):
                cls.forget_draft(sid)
        cls.LISTED = {**(cls.LISTED or {}), **now}
        cls.GONE.difference_update(*now.values())  # (listed again: a host that had lost it has it back)

    def carry_on(self, sid):
        """The open chat (sid) deleted or trashed: a new chat in its place, what's typed in the box carried into it."""
        if sid == self.session_id:
            self.new_session(carry=True)
        self.forget_draft(sid)

    def draft_key(self):
        """What the open chat's draft is kept under: its id, or "" while it's a new chat (or one that's gone)."""
        sid = self.session_id
        if not sid or sid in self.GONE:
            return ""
        if getattr(self, "chat_sent", False):
            return sid
        r = self.remote or network().find(sid)  # (a host's, let go of as a chat's left: its host's still)
        listed = r.sessions.get(sid) if r is not None else None
        if listed is not None:
            return "" if listed.get("draft") else sid
        return sid if self.shows_anything() or session_file(sid) else ""

    def set_box(self, text, caret=None, atts=None):
        """The box's text (its caret, what's attached: as given, if given), the sidebar not filled again for it."""
        self.swapping_draft = True
        try:
            self.input.get_buffer().set_text(text)
            widgets.put_caret(self.input, caret)
            if atts is not None:
                self.attachments = list(atts)
                self.refresh_attach_strip()
        finally:
            self.swapping_draft = False

    def put_ahead(self, texts, atts):
        """texts and atts put in the box ahead of what's there, the caret after them, as a queued message's revert does
        (queue_edit)."""
        ahead = "\n\n".join(t for t in texts if t)
        draft = widgets.typed_text(self.input).strip()
        self.set_box("\n\n".join(p for p in (ahead, draft) if p), [len(ahead)] * 2, list(atts) + self.attachments)

    @staticmethod
    def join_drafts(first, then):
        """Two drafts for one chat (another window's, and this one's) as one: first's text, then then's (the same text
        once), and what's attached to either; the caret where then's was."""
        a, b = first["text"], then["text"]
        if not a.strip() or a.strip() == b.strip():
            text, caret = b, then.get("caret")
        elif not b.strip():
            text, caret = a, first.get("caret")
        else:
            text, caret = f"{a}\n\n{b}", [c + len(a) + 2 for c in then.get("caret") or [len(b)] * 2]
        seen = [(x.name, x.path, x.data) for x in first["attachments"]]
        atts = first["attachments"] + [x for x in then["attachments"] if (x.name, x.path, x.data) not in seen]
        return {"text": text, "caret": caret, "attachments": atts}

    def keep_draft(self, key):
        """The box's draft kept as key's, with one another window left there (join_drafts), and the box emptied for
        the next chat's. An empty box keeps nothing and drops nothing: a draft this window took is gone already
        (take_draft), and one there is another window's."""
        text, atts = widgets.typed_text(self.input), list(self.attachments)
        if text.strip() or atts:
            draft = {"text": text, "caret": widgets.caret_of(self.input), "attachments": atts}
            if (other := self.DRAFTS.pop(key, None)) is not None:
                draft = self.join_drafts(other, draft)
            self.DRAFTS[key] = draft
            while len(self.DRAFTS) > self.MAX_DRAFTS:  # (the longest left go)
                del self.DRAFTS[next(iter(self.DRAFTS))]
        self.set_box("", None, [])

    def take_draft(self):
        """The chat just opened: its draft (keep_draft) in the box, if it was left with one."""
        if draft := self.DRAFTS.pop(self.draft_key(), None):
            self.set_box(draft["text"], draft["caret"], draft["attachments"])
            self.completer.hide()

    @classmethod
    def drafts_state(cls):
        """The drafts as a restart keeps them (BaudeApp.restart)."""
        return {k: {**d, "attachments": [a.state() for a in d["attachments"]]} for k, d in cls.DRAFTS.items()}

    @classmethod
    def restore_drafts(cls, state):
        for key, d in (state or {}).items():
            cls.DRAFTS.setdefault(key, {**d, "attachments": [Attachment.from_state(a) for a in d["attachments"]]})

    def ask_side_question(self, question):
        sq = SideQuestion(self, question)
        self.panel.add_side_question(sq)
        self.show_panel(True, "btw")
        return sq

    @staticmethod
    def user_content(text, atts):
        """Plain text when there is nothing attached; otherwise blocks, media first."""
        if not atts:
            return text
        blocks = [b for a in atts if (b := a.block())]
        paths = [a.path for a in atts if a.kind == "file"]
        if paths:
            listing = "\n".join(f"- {p}" for p in paths)
            text = (text + "\n\n" if text else "") + f"Attached files (open them with your tools):\n{listing}"
        if text:
            blocks.append({"type": "text", "text": text})
        return blocks

    def user_bubble(self, text, atts):
        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.END)
        bubble.add_css_class("user-bubble")
        if atts:
            row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
            for a in atts:
                row.append(attachment_widget(a, 96))
            bubble.append(row)
        if text:
            bubble.append(MarkdownView(text))
        return bubble

    def chat_senders(self):
        """Who the open chat's messages from others than you are from, by their uuids, as its host's sender store
        had them when it was last fetched ({} for a chat of no host's)."""
        if self.remote is None or not self.session_id:
            return {}
        return self.remote.senders_of(self.session_id)

    def sync_senders(self):
        """The open chat's host's sender store fetched, if it lists a newer one than is here (senders_came)."""
        if self.remote is not None and self.session_id and self.remote.state == "alive":
            sid = self.session_id
            self.remote.sync_senders(sid, lambda: self.senders_came(sid))

    def senders_came(self, sid):
        """The chat's sender store, fetched: a message of yours drawn before it came that it says is another's
        drawn as that one's, hidden as it was (a revert not sent yet: in what it hides). The one a pending revert's of
        is left as it is: the revert goes on with it."""
        if sid != self.session_id or getattr(self, "let_go_done", False):
            return
        senders, rev = self.chat_senders(), self.reverting
        stack = [self.transcript]
        while stack:
            child = stack.pop().get_first_child()
            while child is not None:
                after = child.get_next_sibling()
                if isinstance(child, UserTurn) and not isinstance(child, SenderTurn) and child.uuid in senders and \
                        isinstance(child.get_parent(), Gtk.Box) and (rev is None or child is not rev["turn"]):
                    turn = self.sender_turn(senders[child.uuid], child.text, getattr(child, "atts", ()), child.uuid)
                    turn.set_visible(child.get_visible())
                    turn.zoom_hidden = getattr(child, "zoom_hidden", False)
                    if rev is not None and child in rev["hidden"]:
                        rev["hidden"][rev["hidden"].index(child)] = turn
                    for side in ("above", "below"):  # (one a zoom's hidden, to show as it settles: this in its place)
                        held = (self.zoom_job or {}).get(side) or []
                        if child in held:
                            held[held.index(child)] = turn
                    child.get_parent().insert_child_after(turn, child)
                    child.get_parent().remove(child)
                elif getattr(child, "is_history", False) or getattr(child, "span", None) is not None:
                    stack.append(child)  # (what's drawn of its history, and of its earlier chunks)
                child = after

    def sender_turn(self, sender, text, atts, said):
        """A message from sender, its host's sender store says (uuid said), as the chat draws it: SenderTurn, without
        the line its host put at its head for its agent (wire.said_by), and what the store says is shown of it in
        place of its text if it says (shown: a held answer sent, its agent told more). It reverts as yours do, its
        words left out of your box."""
        said_text = baudelib.wire.unsaid(text or "").strip()
        drawn = baudelib.wire.unsaid(sender["shown"]).strip() if isinstance(sender.get("shown"), str) else said_text
        return SenderTurn(self, self.sender_bubble(sender, drawn, atts), said_text, said)

    def sender_bubble(self, sender, text, atts=()):
        """A message from sender, not you (its host's sender store's): where yours are, grey (not your accent), as a
        queued one is, with who it's from in a small line at its top."""
        bubble = self.user_bubble(text, atts)
        bubble.add_css_class("sender-bubble")
        head = Gtk.Label(label=wording.say("common.from_who", author=baudelib.wire.sender_name(sender)), xalign=1,
                         wrap=True,
                         wrap_mode=Pango.WrapMode.WORD_CHAR, halign=Gtk.Align.END)
        head.add_css_class("sender-from")
        bubble.prepend(head)
        return bubble

    def can_revert(self):
        """Whether a message of yours can be reverted here: every model's chat can (its program cuts the conversation:
        rewind_conversation, which claude has, and pi-claude and apple-claude answer as it does), but not one that's
        only read here (live_elsewhere)."""
        return not self.live_elsewhere()

    def program_name(self):
        """What runs the chat's model, as a note names it: claude, pi, or Apple's model."""
        program = model_program(self.model)
        return wording.say(f"programs.{program if program in ('pi', 'apple', 'codex') else 'claude'}")

    def revert_to(self, turn):
        """A message of yours reverted (UserTurn's button), tentatively, as a model or a folder picked for a chat is till
        it's written in: it and everything after it hidden, and what it said put in the box (another's message: the box
        left as it is, and sending makes the revert with nothing in it too). Esc puts them back, and the
        draft that was there (cancel_revert); sending makes the revert (commit_revert), then sends. One already being
        reverted is put back first (the draft kept: the one from before either)."""
        if not self.can_revert() or turn.uuid is None:
            return
        draft = None
        if self.reverting is not None:
            if self.revert_asked():
                return
            draft = self.reverting["draft"]
            self.cancel_revert(restore_draft=False)
        buf = self.input.get_buffer()
        if draft is None:
            draft = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        self.reverting = {"turn": turn, "hidden": [], "draft": draft, "asked": None}
        w = turn
        while w is not None and w is not self.transcript:  # (it, what's after it, and after what it's in)
            after = w.get_next_sibling() if w is not turn else w
            while after is not None:
                self.hide_reverted(after)
                after = after.get_next_sibling()
            w = w.get_parent()
        if not isinstance(turn, SenderTurn):  # (another's words aren't put in your box: it's left as it is)
            buf.set_text(turn.text or "")
        buf.place_cursor(buf.get_end_iter())
        self.input.grab_focus()
        self.stick_bottom = True

    def hide_reverted(self, widget):
        """A widget hidden while a revert's not sent (revert_to), to be shown again if it's not (cancel_revert)."""
        if widget is not None and getattr(widget, "zoom_hidden", False):  # (hidden by a zoom, to be shown as it
            widget.zoom_hidden = False                                     # settles: not now, but with the rest)
            for side in ("above", "below"):
                if self.zoom_job is not None and widget in self.zoom_job.get(side, ()):
                    self.zoom_job[side].remove(widget)
            self.reverting["hidden"].append(widget)
        elif widget is not None and widget.get_visible():
            widget.set_visible(False)
            self.reverting["hidden"].append(widget)

    def revert_asked(self):
        """Whether the pending revert's been asked of claude, and it's still to answer (one gone meanwhile won't)."""
        asked = self.reverting["asked"] if self.reverting is not None else None
        return asked is not None and asked[0] is self.proc and asked[1] in self.pending

    def say_now(self, text):
        """A note shown while a revert's pending, below what's still shown (add hides what comes after the reverted
        message)."""
        rev, self.reverting = self.reverting, None
        try:
            return self.note(text)
        finally:
            self.reverting = rev

    def show_waiting(self, widget):
        """A permission asked while a revert's pending: what it's in shown again, to be answered (the turn waits on
        it)."""
        rev, w = self.reverting, widget
        while rev is not None and w is not None and w is not self.transcript:
            if w in rev["hidden"]:
                rev["hidden"].remove(w)
                w.set_visible(True)
            w = w.get_parent()

    def cancel_revert(self, restore_draft=True):
        """Esc on a revert not sent: what it hid shown again, and the box as it was before."""
        rev, self.reverting = self.reverting, None
        self.held_after_revert = None  # (a held message's ↑ that made it: not sent after it either)
        if rev is None:
            return
        for widget in rev["hidden"]:
            widget.set_visible(True)
        if restore_draft:
            buf = self.input.get_buffer()
            buf.set_text(rev["draft"])
            buf.place_cursor(buf.get_end_iter())

    def commit_revert(self):
        """A revert sent (on_send): claude takes the message and everything after it back (a turn at it stopped, what it
        started since stopped too), then what's in the box is sent (reverted). A chat only viewed is resumed first, as
        sending in it does (send_text)."""
        rev = self.reverting
        turn = rev["turn"]
        self.reverting = None  # (what's said meanwhile shown, not hidden with what's being reverted: add)
        try:
            if self.resume_pending:
                self.begin_resume()
            if self.proc is None and self.remote is None and not self.runs_here():
                took = self.take_up()
                if took is False:  # (This Mac's host process isn't there yet: started, and sent once it is)
                    self.note(f"Starting {THIS_MACHINE}'s host process…")
                    self.held_after_revert = None  # (what's in the box is what goes, once it is)
                    self.local_server(lambda _r: self.reverting is rev and not self.revert_asked() and
                                      self.commit_revert())
                    return
                if took is None:
                    self.held_after_revert = None
                    return
            if self.proc is None:
                self.start_proc()
            if self.proc is None:
                self.held_after_revert = None
                self.note(wording.say("chat_turns.couldnt_revert", why=wording.say(
                    "chat_turns.not_running", program=self.program_name())))
                return
        finally:
            if rev["turn"].is_ancestor(self.transcript):
                self.reverting = rev
        if self.busy:  # (the turn it stops ends as a stopped one: not an error, h_result)
            self.proc.stop_asked = True
        rid = self.proc.control({"subtype": "rewind_conversation", "target_message_uuid": turn.uuid,
                                 "interrupt_if_running": True})
        self.pending[rid] = (lambda r: self.reverted(rev, r), lambda error: self.revert_refused(rev, error))
        rev["asked"] = (self.proc, rid)

    def revert_refused(self, rev, why):
        """claude wouldn't revert: said, and the revert's still not sent (Esc puts it back; Ctrl/⌘+Enter asks again);
        nor a held message whose ↑ asked for it (held_after_revert: ↑ again)."""
        self.held_after_revert = None
        if rev["asked"] is not None:
            rev["asked"][0].stop_asked = False  # (its turn not stopped by it: an error that ends it is said, h_result)
        rev["asked"] = None
        if self.reverting is rev:
            self.say_now(wording.say("chat_turns.couldnt_revert", why=why))

    def reverted(self, rev, response):
        """claude's answer to commit_revert: rewound, the message and all after it gone from the chat, and what's in the
        box sent; else why not, said."""
        response = response or {}
        if not response.get("rewound"):
            why = next((v for k, v in response.items() if k != "rewound" and isinstance(v, str)), None)
            return self.revert_refused(rev, why or f"{self.program_name()} said no")
        if self.reverting is not rev or not rev["turn"].is_ancestor(self.transcript):  # (another chat opened meanwhile)
            return
        self.reverting = None
        self.drop_from(rev["turn"])
        self.blocks, self.streamed_ids = {}, set()  # (a turn it stopped: what was half drawn is gone)
        if self.busy:  # (a turn it stopped, its ending not said yet: what's in the box sent once it is, h_result; or
            self.send_after_result = GLib.timeout_add(3000, self.send_reverted)  # in a moment, if it never is)
        else:
            self.send_reverted(waited=False)

    def send_reverted(self, waited=True):
        """After a revert's made (reverted): what's in the box sent. waited: once its stopped turn's ended, or a moment
        after (whichever's first: once)."""
        if waited and self.send_after_result is None:
            return False
        if self.send_after_result is not None:
            GLib.source_remove(self.send_after_result) if GLib.MainContext.default().find_source_by_id(
                self.send_after_result) else None
            self.send_after_result = None
        self.set_busy(False)
        held, self.held_after_revert = getattr(self, "held_after_revert", None), None
        if held is not None and held.get_parent() is self.queue_box:  # (a held message's ↑ made it: that sent)
            self.held_send(held)
            return False
        self.on_send()
        return False

    def drop_from(self, widget):
        """widget and everything drawn after it, out of the chat (an earlier chunk it's in: the rest of that, and the
        chunks after it, not drawn later either)."""
        w = widget
        while w is not None and w is not self.transcript:
            parent, after = w.get_parent(), w.get_next_sibling()
            while after is not None:
                nxt = after.get_next_sibling()
                parent.remove(after)
                discard(after)
                after = nxt
            w = parent
        widget.get_parent().remove(widget)
        discard(widget)
        if self.chunk_slots:
            gone = [slot for slot in self.chunk_slots if slot.get_parent() is None]
            self.chunk_slots = [slot for slot in self.chunk_slots if slot.get_parent() is not None]
            for slot in gone:  # (the chunks after it not drawn yet: their items counted as drawn, never to be)
                self.history.take(*slot.span)
            if gone:
                self.update_earlier_btn()
        kept = [job for job in self.fill_jobs if job.slot.get_parent() is not None]
        self.fill_jobs.clear()
        self.fill_jobs.extend(kept)

    INTERRUPT_WAIT = 5000  # ms for an interrupted turn to end, before its claude's stopped outright (stop_stuck)

    def interrupt(self):
        """The turn interrupted (Esc, the stop button): asked of its claude once (pressed again meanwhile: nothing
        more). Not over within INTERRUPT_WAIT, whatever runs the model, its claude's stopped (stop_stuck), so the chat's
        usable again; at once if its host says nothing's running there to interrupt. Nothing said yet: the turn's end
        says it, once (h_result; stop_stuck)."""
        if not self.proc:
            return
        if self.interrupting is not None and self.interrupting[0] is self.proc:
            return  # (asked already: stopped outright if it doesn't end soon)
        self.drop_interrupting()
        proc = self.proc
        proc.stop_asked = True
        self.pending[proc.control({"subtype": "interrupt"})] = (lambda r: self.interrupt_answered(proc, r), None)
        self.interrupting = (proc, GLib.timeout_add(self.INTERRUPT_WAIT, lambda: self.stop_stuck(proc)))

    def interrupt_answered(self, proc, response):
        """An interrupt's answer: a host's, that its claude isn't running (or is being stopped), ends the turn now,
        as no result's coming; claude's own, that it's stopping, waits for the turn's end (or INTERRUPT_WAIT)."""
        if response.get("running") is False:
            self.drop_interrupting()
            self.stop_stuck(proc, ran=False)

    def drop_interrupting(self):
        """An interrupt's wait (interrupt) let go of: its turn's over, or its claude's gone."""
        it, self.interrupting = getattr(self, "interrupting", None), None
        if it is not None:
            GLib.source_remove(it[1])

    def stop_stuck(self, proc, ran=True):
        """An interrupted turn not over in time (its claude still starting on a resume, stuck, or gone without a
        word), or with nothing running to end it (ran False: its host said so): its claude stopped, the turn shown
        over. A host's session's, there, as Stop does it (its next message starts it again); this process's own,
        killed and started again on the conversation."""
        self.interrupting = None
        if proc is not self.proc or not self.busy:
            return False
        proc.stop_asked = "said"  # (its end said here: not again if its result comes after all, h_result)
        self.note(wording.say("interrupt.not_stopped", program=self.program_name(),
                              seconds=self.INTERRUPT_WAIT // 1000) if ran else
                  wording.say("interrupt.not_running", program=self.program_name()))
        self.cut_short()
        if self.remote is not None:
            if ran:
                self.remote.request({"op": "stop", "session": self.session_id})
            self.blocks.clear()
            for waiting in list(self.waiting_cards):  # (what it asked: nothing to answer now. A copy: answered()
                if isinstance(waiting, ToolCard):  # takes the card out of waiting_cards as it goes)
                    waiting.answered()
            self.waiting_cards.clear()
        else:
            self.restart_after_turn = False  # (started again now, on whatever sign-in there is)
            self.restart_proc(resume=True)
        self.end_turn(proc)
        return False

    def model_said(self, value):
        """A model's name as a note says it: as the model picker lists it (Qwen3 4B)."""
        return getattr(self, "model_names", {}).get(value) or self.model_dd.label()

    def on_model_changed(self, value):
        dropped = getattr(self, "move_after_turn", None) is not None
        self.move_after_turn = None  # (a model picked mid-turn: that one, not one picked before it)
        was = self.model
        if value == was:  # (the one it's on: one waiting for the turn's end dropped)
            if dropped:
                self.sync_providers()
            return
        self.outgrown = False  # (another model's window: as its usage says)
        if self.remote is not None and model_program(value) != model_program(was):
            self.sync_providers()  # (this one goes on with its own model: the one picked is the new session's)
            self.remote_new_conversation(value)  # (a network session: a new one, on its server)
            return
        if getattr(self, "busy", False) and self.proc is not None and \
                (model_program(value) != model_program(was) or model_program(value) == "pi" or
                 modelslib.runs_on(value) != modelslib.runs_on(was)):
            # (it'd restart pi or claude, here or in the host process, which would stop the reply: once the turn's
            # over, as a provider picked)
            self.move_after_turn = (self.proc, value)
            self.sync_providers()
            return
        self.model = value
        self.sync_providers()
        self.sync_effort_choices()
        self.sync_local()
        if self.remote is None and model_program(value) != model_program(was):  # (Claude's to Apple's: another program)
            self.note(wording.say("chat_header.model_note_new", name=self.model_said(value)))
            self.restart_proc(resume=False)
            return
        if self.remote is None and value != was and \
                (model_program(value) == "pi" or modelslib.runs_on(value) != modelslib.runs_on(was)):
            # (pi-claude serves one model, and one on another host runs there: pi's session goes on, kept on the chat's
            # host; a network session's server starts it again on the new one itself)
            self.note(wording.say("chat_header.model_note", name=self.model_said(value)))
            self.restart_proc(resume=True)
            return
        if self.proc:
            self.proc.control({"subtype": "set_model", "model": value})
            self.note(wording.say("chat_header.model_note", name=self.model_said(value)))
            self.refresh_effort()  # the CLI may change or drop the effort with the model
            self.refresh_context()  # different models have different windows
        else:
            self.exhausted.set(False)  # (no claude to ask: the one started next says)

    def on_effort_changed(self, value):
        if model_program(self.model) == "pi":
            self.local_effort = value
        else:
            self.effort = value
        if self.proc:
            self.proc.control({"subtype": "apply_flag_settings", "settings": {"effortLevel": value}})
            self.note(wording.say("chat_header.effort_note", label=self.effort_dd.label()))

    def sync_effort_choices(self):
        """Offer the current model's effort levels; grey the picker out if it has none."""
        info = self.models_info.get(self.model_dd.value() or "", {})
        levels = info.get("supportedEffortLevels") if info.get("supportsEffort") else None
        if levels:
            self.effort_dd.set_choices([(l, EFFORT_LABELS.get(l, l.title())) for l in levels])
            want = self.local_effort if model_program(self.model) == "pi" else self.effort
            if want in levels:
                self.effort_dd.select(want)
            elif info.get("defaultEffort") in levels:
                self.effort_dd.select(info["defaultEffort"])
            self.effort_dd.set_tooltip_text(wording.say("chat_header.effort_tip"))
        else:
            self.effort_dd.set_choices([("", wording.say("meta_settings.effort"))])
            self.effort_dd.select("")
            self.effort_dd.set_tooltip_text(wording.say("meta_settings.no_effort", model=self.model_dd.label() or
                                                        wording.say("chat_header.this_model")))
        self.effort_dd.set_sensitive(bool(levels))

    def sync_effort(self, effort):
        if effort and effort in self.effort_dd.values:
            if model_program(self.model) == "pi":
                self.local_effort = effort
            else:
                self.effort = effort
            self.effort_dd.select(effort)

    def on_mode_changed(self, value):
        self.mode = value
        if self.proc:
            self.request({"subtype": "set_permission_mode", "mode": value},
                         lambda _r: self.mode_taken(value), lambda error: self.mode_refused(value, error))

    def mode_taken(self, value):
        self.proc.mode = value  # (what it's in: where a mode it refuses next goes back to)
        self.note(wording.say("chat_header.mode_note", mode=dict(MODES).get(value, value)))

    def mode_refused(self, value, error):
        """claude said no to a mode picked (Bypass permissions, when it wasn't started able to bypass them): the
        picker and self.mode (what the chat's claude starts in next) back on the mode it's still in, unless another's
        been picked since, and why said."""
        was = getattr(self.proc, "mode", None)
        if self.mode == value and was:
            self.sync_mode(was)
        why = error.split(" because ", 1)[-1].rstrip(".") if error else wording.say("chat_header.claude_said_no")
        mode = dict(MODES).get(value, value)
        self.note(wording.say("chat_header.mode_refused_still", mode=mode, why=why, was=dict(MODES).get(was, was))
                  if was else wording.say("chat_header.mode_refused", mode=mode, why=why))

    # ---- each chat's own model: picked in one, it isn't the others'. Kept by session id as a turn ends (chat_models);
    # a chat opened is put back on it, or on its transcript's last reply's model (one Baude hasn't kept)
    CHAT_MODELS = 500

    def keep_chat_model(self):
        if not (self.session_id and self.model) or self.resume_pending or self.model == "default":
            return
        kept = load_settings().get("chat_models") or {}
        if kept.get(self.session_id) != self.model:
            chats = {k: v for k, v in kept.items() if k != self.session_id} | {self.session_id: self.model}
            save_settings(chat_models=dict(list(chats.items())[-self.CHAT_MODELS:]))
        if model_program(self.model) == "claude":
            self.claude_model = self.model  # (where a chat goes that has no model of its own: apply_chat_model)

    def apply_chat_model(self, info):
        """Put the picker on the model the chat info names was on. A Claude transcript with no model Baude knows goes
        on Claude's last used (or claude's own default): never Apple's or one on this Mac, which can't carry it on. A
        network session's is the one its server says it's on."""
        if info.get("remote") is not None and info.get("model"):
            self.model = info["model"]
            self.sync_providers()  # (named as its server lists it once it's said: sync_models)
            self.sync_effort_choices()
            self.sync_local()
            return
        kept = (load_settings().get("chat_models") or {}).get(info["id"])
        want = (kept if kept != "default" else None) or transcript_model(info["path"])
        models = {v: m for v, m in getattr(self, "models_info", {}).items() if v != "default"}  # (an alias, not a model)
        model = next((v for v, m in models.items() if want in (v, m.get("resolvedModel"))), None) or \
            (modelslib.claude_match(want, list(models.values())) or {}).get("value") if want else None
        if model is None and want and model_program(want) == "claude":  # ("claude-opus-5-5" for the picker's "opus")
            model = next((v for v in models if model_program(v) == "claude" and v in want), None)
        if model is None and model_program(self.model) != "claude":
            model = getattr(self, "claude_model", None)
        elif model is None:
            return
        self.model = model
        self.sync_providers()
        self.sync_effort_choices()
        self.sync_local()

    def sync_local(self):
        """Show the permission and effort controls available for this backend."""
        program = model_program(self.model)
        self.effort_dd.set_visible(program == "claude" or self.effort_dd.get_sensitive())
        self.mode_dd.set_visible(program in ("claude", "codex") or self.host_lists("set_permission_mode"))
        self.sync_mode(self.mode)

    def host_lists(self, op):
        """Does this chat's host take op as claude's own modes and always-allow have it, whatever its model's program,
        as it says (its hello's ops: a phone's agents)? Not one that lists no ops: that's said by its program."""
        ops = None if self.remote is None else self.remote.info.get("ops")
        return isinstance(ops, list) and op in ops

    def sync_models(self, models):
        """Fill the model picker from initialize's list, labelled with real names ("Opus 5.5"), and the models this
        machine runs besides (modelslib.runnable_models: Apple's, MLX's, Ollama's, an endpoint's), with every other
        host's in the mesh (sync_providers). apple-claude's and pi-claude's lists are their one model: Claude's, as claude last
        listed them, stay.

        The list's "default" entry is an alias, not a model: it is dropped, and when no model
        was chosen the entry it resolves to is selected instead.
        """
        models = [m for m in models or [] if m.get("value")]
        if self.remote is not None:  # (a network session: what its server can run, not this machine)
            if not any(model_program(m["value"]) == "claude" for m in models):
                models = [m for m in getattr(self, "claude_models", []) if model_program(m["value"]) == "claude"] + models
        elif models and all(model_program(m["value"]) != "claude" for m in models):
            models = getattr(self, "claude_models", [])
        else:
            self.claude_models = models
            meta.listed(models, seed=True)  # (what this machine's claude runs: Meta's Intelligence, till Meta's own lists)
        if self.remote is None:
            models = [m for m in models if model_program(m["value"]) == "claude"] + \
                modelslib.runnable_models(wait=self.PROVIDERS_WAIT)
        self.models_info = {m["value"]: m for m in models}
        self.merge_codex_models()
        default = next((m for m in models if m["value"] == "default"), {})
        models = [m for m in models if m["value"] != "default"]
        want = self.model or default.get("resolvedModel")
        hit = next((m["value"] for m in models if want in (m["value"], m.get("resolvedModel"))), None) or \
            (modelslib.claude_match(want, models) or {}).get("value", want) if want else None
        self.model_shown = hit  # (what the picker shows while claude's own default's the model: self.model None)
        self.sync_providers(hit)
        self.sync_effort_choices()
        self.sync_local()

    # ---- inference providers: where the models besides Claude's run (Ollama, MLX, Apple's, an OpenAI-compatible
    # server), by host: the chat's host's, and every other member's of the mesh (network().providers()). The model
    # picker lists every model there is: Claude's (claude's: not a provider's, bound to where it runs), then every
    # other, the chat's host's first, each once however many hosts (or runtimes) have it (providers.alike: the same
    # one). A model picked runs on the first provider that has it (the chat's host's, then the mesh's in its order),
    # unless the chat's on another of its copies already. Left of the model picker, the provider picker lists where the
    # chat's model runs: each provider that has it, by host, greyed with one; picking another moves the chat's model
    # there (mid-turn, once the turn's over). It's gone while the chat's on Claude's. Neither picker changes the
    # chat's host: the chat stays where it is, with its files, and another host's model runs for it through the mesh's
    # proxy where it serves an API, else as an agent there whose tools are the chat's host's (providers.elsewhere).
    # Where a chat lives is picked as it's made (New Session's host), or by Meta. A model's name is the same wherever
    # it runs: its provider and host are the provider picker's to say
    PROVIDERS_WAIT = 0.5  # (s a chat waits for this machine's providers the first time: the rest come as they're found)

    def chat_host(self):
        """The host this chat lives on, its files and its agent's tools, as New session's hosts key it: this machine (its
        own claude, or its host process), or the server's."""
        return HERE_HOST if self.remote is None or baudelib.client.is_here(self.remote.info) else self.remote.key

    def provider_choices(self):
        """[(key, host name, provider name, models)], a host's together: the providers of the chat's host (this
        machine's, as they're found here, whether its own claude or its host process runs the chat; another's, the
        models it lists: models_info), then every other host's in the mesh (this machine's too, for a chat on another),
        each model as the chat runs it where it is: through the mesh's proxy, or as an agent there whose tools are the
        chat's host's (providers.elsewhere). Another host's Apple model is one too; the chat's host's own is in
        models_info (providers.group leaves it out)."""
        own, out = self.chat_host(), []

        def add(host, name, models):
            for pid, pname, ms in providerslib.group(models):
                out.append((f"{host}|{pid}", name, pname, ms))
        if own == HERE_HOST:
            add(own, THIS_MACHINE, modelslib.runnable_models(wait=False))
            theirs = set()
        else:  # (its own, as it lists them: not those of other hosts' it runs, which are kept beside them)
            add(own, remote_name(self.remote), [m for m in self.models_info.values() if m["value"] != "default" and
                                                not modelslib.proxied(m["value"]) and
                                                not m["value"].startswith("openai/mesh-")])
            theirs = {self.remote.info.get("id"), own}
        if self.runs_here():  # (Baude's own claude runs it: no host process here to reach another host's from)
            return out
        for p in network().providers():
            host = p.get("host") or {}
            if (host.get("here") if own == HERE_HOST else host.get("id") in theirs) or not p.get("models") or \
                    not baudelib.wire.is_id(host.get("id")):
                continue
            hname, pname = THIS_MACHINE if host.get("here") else host.get("name") or str(host["id"])[:8], \
                p.get("name") or p.get("id")
            pid, models = providerslib.elsewhere(p, host["id"])
            out.append((f"{host['id']}|{p['id']}", hname, pname,
                        [m | {"providerName": f"{hname} / {pname}"} for m in models]))
        return out

    def merge_codex_models(self):
        """Use the chat host's current Codex model catalog, independent of Claude initialization."""
        host = self.remote or local_host()
        if host is None or "models" not in host.info:
            return False
        catalog = {m["value"]: m for m in host.info["models"] if model_program(m["value"]) == "codex"}
        old = {v: m for v, m in self.models_info.items() if model_program(v) == "codex"}
        if catalog == old:
            return False
        for value in old:
            del self.models_info[value]
        self.models_info.update(catalog)
        return True

    def providers_listed(self, found=None):
        """What the provider picker lists (found: provider_choices'), to tell whether it's changed."""
        return [(k, host, label, [m["value"] for m in ms]) for k, host, label, ms in
                (self.provider_choices() if found is None else found)]

    def sync_providers(self, shown=None):
        """The model picker and the provider picker as the chat's host and the mesh have them now, on shown (the chat's
        model, if not given). The model picker: Claude's models, then every other, one of each kind (providers.alike),
        the chat's host's first: shown where it's of a kind, else the first provider's copy (the one a pick runs on);
        shown last if it's none of these. The provider picker: where shown runs, each provider that has it by host
        (alike_keys: its copy there), on shown's (or the copy a move picked mid-turn waits to go to); greyed with one,
        hidden on Claude's."""
        found = self.provider_choices()
        self.providers_seen = self.providers_listed(found)
        self.provider_index, named = {}, {}  # (each model's provider, the first that lists it; each provider's names)
        for k, host, label, ms in found:
            named[k] = (host, label)
            for m in ms:
                self.provider_index.setdefault(m["value"], k)
                self.models_info.setdefault(m["value"], m)  # (as it's run: its effort levels, its name)
                if m.get("digest") and self.models_info[m["value"]].get("digest") != m["digest"]:  # (pulled again)
                    self.models_info[m["value"]] = {**self.models_info[m["value"]], "digest": m["digest"]}
        pending = getattr(self, "move_after_turn", None)  # (picked mid-turn: where the chat goes once it's over)
        shown = (pending[1] if pending and pending[0] is self.proc else None) or shown or self.model or \
            getattr(self, "model_shown", None)

        if shown == "codex/default":
            default = self.models_info.get(shown, {}).get("resolvedModel")
            shown = f"codex/{default}" if default else None

        def pid_of(m):
            return m.get("provider") or providerslib.provider_of(m["value"])

        def info(value):
            return self.models_info.get(value) or {"value": value, "displayName": providerslib.model_name(value)}
        pid = pid_of(info(shown)) if shown else None
        # (every model with a provider: the chat's host's Apple model, then the providers' in their order; the chat's
        # own last if its host's gone, the same one as another host's where it is)
        offered, seen = [], set()
        for m in [m for m in self.models_info.values() if m["value"] not in self.provider_index and
                  pid_of(m) == "apple"] + [m for *_x, ms in found for m in ms]:
            if m["value"] not in seen:
                seen.add(m["value"])
                offered.append(self.models_info.get(m["value"], m))
        if pid is not None and shown not in seen:
            offered.append(info(shown))
        kinds = providerslib.alike(offered, [shown] if shown else [])
        own = [m for m in self.models_info.values() if m["value"] not in ("default", "codex/default") and
               m["value"] not in seen and pid_of(m) is None]  # (Claude's as claude lists them, each its own row)
        same = modelslib.claude_match(shown, own) if shown and pid is None else None  # (the chat's model said
        if shown and pid is None and same is None:  # another way, claude-opus-5-5, its server's say: the row it is;
            own.append(info(shown))  # else its own row, last)
        pick = same["value"] if same else shown
        listed = own + [lead for lead, _ms in kinds]
        others = [m for m in listed if pid_of(m) is not None]  # (one of each kind: told apart where two share a name)
        names = [(m["value"], providerslib.told_apart(m, others) if pid_of(m) is not None else
                  m.get("displayName") or m["value"]) for m in listed]
        self.model_names = dict(names) | ({shown: dict(names)[pick]} if same else {})
        self.model_dd.set_choices(names or MODELS)
        if pick:
            self.model_dd.select(pick)

        here = THIS_MACHINE if self.chat_host() == HERE_HOST else remote_name(self.remote)
        copies = next((ms for _lead, ms in kinds if any(m["value"] == shown for m in ms)), []) if pid else []
        hosts, picked, self.alike_keys = {}, None, {}
        for m in copies:
            k = self.provider_index.get(m["value"])
            if k is None:  # (the chat's host's Apple model, or one whose host's gone: by what its value says)
                there, label = providerslib.where(pid_of(m))
                agent = modelslib.proxied(m["value"])
                k, host = f"{agent[0] if agent else there or self.chat_host()}|{pid_of(m)}", there or here
            else:
                host, label = named[k]
            if k in self.alike_keys:
                continue
            hosts.setdefault(k.split("|", 1)[0], (host, []))[1].append((k, label))
            self.alike_keys[k] = m["value"]
            if m["value"] == shown:
                picked = k
        one = len(self.alike_keys) == 1
        self.provider_dd.set_choices(list(hosts.values()))
        self.provider_dd.select(picked)
        self.provider_dd.set_sensitive(not one)  # (one provider has it: greyed, there's no other to pick)
        self.provider_dd.set_visible(bool(self.alike_keys))  # (Claude's: none)
        self.provider_dd.set_tooltip_text(wording.say("chat_header.provider_tip_only") if one else
                                          wording.say("chat_header.provider_tip"))
        self.ctx_box.window_changed()  # (no session's said what it's used yet: this model's window)

    def on_provider_changed(self, key):
        """Another provider of the chat's model picked (alike_keys): the chat's model is its copy there, as a model
        picked is; mid-turn, once the turn's over. Its own picked back: no move, one waiting dropped."""
        self.move_after_turn = None
        value = getattr(self, "alike_keys", {}).get(key)
        if value is not None and value != self.model:
            self.move_to(value)
        else:
            self.sync_providers()

    def move_to(self, value):
        """The chat's model to its copy on another host (a provider picked): now, or mid-turn once the turn's over
        (moving restarts pi or claude, which would stop the reply), if its claude's still the one running it."""
        if getattr(self, "busy", False) and self.proc is not None:
            self.move_after_turn = (self.proc, value)
            self.sync_providers()
        else:
            self.on_model_changed(value)

    def providers_found(self):
        """This machine's providers changed (found again: Ollama started, a model pulled): the pickers as they are now."""
        on_main(lambda: self.get_application() is not None and not getattr(self, "let_go_done", False) and
                self.resync_models())

    def resync_models(self):
        if self.remote is None and getattr(self, "claude_models", None) is not None:
            self.sync_models(self.claude_models)  # (an own claude's: the models this machine runs, again)
        else:
            self.sync_providers()

    def default_mode(self):
        """Pick the backend's starting mode without inheriting the previous chat's mode."""
        self.sync_mode("default" if model_program(self.model) == "codex" else "auto")

    def sync_mode(self, mode):
        program = model_program(self.model)
        choices = modelslib.CODEX_MODES if program == "codex" else MODES
        values = [value for value, _label in choices]
        if self.mode_dd.values != values:
            self.mode_dd.set_choices(choices)
        if mode and program == "codex":
            mode = modelslib.codex_mode(mode)
        elif mode and program != "claude" and not self.host_lists("set_permission_mode"):
            mode = "default"
        if mode:
            self.mode = mode
            self.mode_dd.select(mode)  # (quietly: not asked of claude again)
            if self.proc is not None:
                self.proc.mode = mode  # (what claude says it's in)

    def on_launcher_toggled(self, action, value):
        try:
            if value.get_boolean():
                self.note(f"added to {LAUNCHER.where}: {LAUNCHER.install()}")
            else:
                removed = LAUNCHER.uninstall()
                self.note(f"removed from {LAUNCHER.where}" if removed else "launcher was not installed")
        except (OSError, GLib.Error) as e:
            self.note(f"launcher: {e}")
        action.set_state(GLib.Variant.new_boolean(LAUNCHER.installed()))

    @staticmethod
    def norm_dir(path):
        """The one spelling of a folder used everywhere: absolute, ~ expanded, symlinks resolved
        (claude reports its cwd resolved, so this is what it actually runs in)."""
        return os.path.realpath(os.path.expanduser(path))

    def set_cwd(self, path):
        """Make `path` the working folder: the next claude process runs in it (self.run_cwd), and so do relative
        paths. self.cwd is the folder it belongs to (main_folder: a worktree's is its repository's main checkout),
        which the status line, the sidebar's group, new sessions and Signal's glossary follow. Nothing else may
        assign either."""
        ran, self.run_cwd = self.run_cwd, self.norm_dir(path)
        self.cwd, tree = main_folder(self.run_cwd)
        MarkdownView.base_dir = self.run_cwd
        checkout = tree if tree and os.path.isdir(tree) else None  # (a worktree's chat works in it)
        moved = getattr(self, "signal", None) and \
            self.signal.place.key != signal_place(self.ref_checkout or checkout or self.cwd).key
        if moved or self.run_cwd != ran and checkout != self.ref_checkout:
            self.ref_checkout = checkout  # (another folder or checkout: its chat's checkout is looked at again)
            if getattr(self, "signal", None) and not getattr(self, "switching_chat", False):
                self.set_signal(save=False)  # (a chat being opened applies its own switches once it's in)
        if hasattr(self, "panel"):
            self.panel.git_page.chat_changed()
        if hasattr(self, "status"):
            self.update_status()
        if hasattr(self, "sidebar") and not getattr(self, "switching_chat", False):  # (a chat switched to: once it's in)
            self.sidebar.fill()  # this folder's group moves to the top

    def cwd_dialog(self):
        dlg = Gtk.FileDialog(title="Working directory", modal=True)
        dlg.set_initial_folder(Gio.File.new_for_path(self.cwd))  # open where we are
        return dlg

    def pick_folder(self):
        """Open folder…: a new session in the folder you pick."""
        dlg = self.cwd_dialog()
        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            self.switch_to(lambda: self.start_in_folder(f.get_path()))
        dlg.select_folder(self, None, done)

    def start_in_folder(self, path):
        """A new chat in a folder (Open folder…, a folder's +): from a new chat not written in yet that was started
        in a group, in that group still; else in the group New session would put it in (new_group)."""
        if not os.path.isdir(path):
            self.note(f"not a folder: {path}")
            return
        draft = self.session_id is None or self.remote is not None and \
            (self.remote.sessions.get(self.session_id) or {}).get("draft")
        group = self.draft_group if draft and getattr(self, "draft_group", "") else self.sidebar.new_group(HERE_HOST)
        self.sidebar.put_in(group)
        self.new_session(path, group=group)

    def change_cwd(self, path):
        path = self.norm_dir(os.path.join(self.run_cwd, path))  # relative to the folder claude runs in
        if not os.path.isdir(path):
            self.note(f"not a directory: {path}")
            return
        if self.busy:
            self.note(wording.say("slash_commands.cd_busy"))
            return
        if path == self.run_cwd and (self.remote is None or self.remote_folder() in (None, path)):
            return  # (a host's chat whose folder there isn't this window's, a worktree that's gone, say: moved still)
        if self.remote is not None and not self.remote.mine:  # (another machine's folders: not this one's to pick)
            self.note(wording.say("slash_commands.cd_elsewhere", host=remote_name(self.remote)))
            return
        if self.remote is not None:  # (this machine's host process runs it: there, from its next message on)
            reply = self.remote.call({"op": "set_cwd", "session": self.session_id, "cwd": path}, timeout=10)
            if reply.get("op") != "cwd_set":
                self.note(f"folder not changed: {baudelib.wire.said(reply)}")
                return
            if self.session_id in self.remote.sessions:  # (as it's listed, before it next says so)
                self.remote.sessions[self.session_id] = {**self.remote.sessions[self.session_id], "cwd": reply["cwd"]}
        self.set_cwd(path)
        keep = self.session_id is not None
        self.note(f"folder → {self.short_cwd()}" + (" (conversation continues)" if keep else ""))
        self.restart_proc(resume=True)

    # ---- protocol
    def on_msg(self, proc, msg):
        if proc is not self.proc:
            return False  # leftover output from a process we already replaced
        t = msg.get("type")
        if t in ("stream_event", "assistant", "result") and not msg.get("parent_tool_use_id"):
            self.show_retry(None)  # (the model's answering again, or the turn's over)
        if t in ("stream_event", "assistant") and not msg.get("parent_tool_use_id") and not self.busy:
            self.set_busy(True)  # the CLI started a turn by itself
        handler = getattr(self, "h_" + str(t).lstrip("_"), None)
        if handler:
            handler(msg)
        return False

    def h_note(self, msg):  # (the host process's word on the chat: its worktree made again, say)
        self.note(msg.get("text") or "")

    def h_stderr(self, msg):
        own = model_program(self.model) != "claude"  # (apple-claude and pi-claude say things there for the chat)
        self.note(("" if own else "stderr: ") + msg["text"])

    def h_system(self, msg):
        st = msg.get("subtype")
        if st == "init":
            self.init_info = msg  # (its model and tools, as claude says them: report)
            TOOL_NAMES.update(t for t in msg.get("tools") or [] if isinstance(t, str))  # (what Signal can name)
        if st == "init" and self.resume_pending:
            return  # the fresh helper process's own id: the session we show is the viewed one
        if st == "init" and self.remote is not None:  # (a network session is its server's id, whatever claude's is)
            self.update_status("")
            return
        if st == "init":
            new_id = msg.get("session_id") != self.session_id
            if new_id and self.session_id is None:  # (a new chat's notification, under its id now; what it's allowed)
                widgets.withdraw_notification(self.chat_key(None))
                self.allowed_in(msg.get("session_id")).update(self.always_allowed.pop(None, ()))
            self.session_id = msg.get("session_id")
            if new_id:
                if self.session_id and (self.signal_pending is not None or self.session_id not in
                                        (load_settings().get("signal_chats") or {})):
                    # (its switches as they are now, so the defaults changing later doesn't change them)
                    self.keep_chat_signal(self.session_id, *(self.signal_pending or (self.signal_run.get_active(),
                                                                                     self.signal_show.get_active())))
                    self.signal_pending = None
                self.sidebar.fill()
            if msg.get("cwd") and self.norm_dir(msg["cwd"]) != self.run_cwd:
                self.note(f"claude is running in {msg['cwd']}")
                self.set_cwd(msg["cwd"])  # what claude really uses wins
            self.update_status(f"{len(msg.get('tools', []))} tools")
        elif st == "codex_turn_started":
            if not self.busy:
                self.set_busy(True)
        elif st in ("codex_context", "baude_context"):  # (baude_context: pi-claude's, MLX's count of what it's sent)
            self.remember_context(self.model, msg["usage"])
            self.show_context(msg["usage"])
        elif st == "api_retry" and not msg.get("parent_tool_use_id"):  # (a subagent's: its own to wait out)
            self.show_retry(msg)
        elif st == "compact_boundary":
            self.on_compaction(msg)
            self.refresh_context()
        elif st == "status":
            self.sync_mode(msg.get("permissionMode"))  # e.g. leaving plan mode
            self.on_compaction(msg)
            if msg.get("status") == "requesting" and not self.busy:
                self.set_busy(True)  # a turn the CLI started itself (e.g. a background task finished)
        elif st == "session_title_changed":
            self.show_title(msg.get("title"))
            self.sidebar.refresh()  # the list shows the new title
        elif st in ("task_started", "task_progress", "task_updated", "task_notification"):
            self.on_task_event(st, msg)
        elif st == "background_tasks_changed":
            self.on_tasks_changed(msg.get("tasks") or [])
        elif st == "commands_changed":
            self.completer.set_commands(msg.get("commands") or [])
        elif st not in ("hook_started", "hook_response", "thinking_tokens", "task_summary",
                        "post_turn_summary", "rate_limit_event"):
            self.note(f"[system:{st}]")

    # ---- compaction (CompactMarks): marked in the chat, at its end
    def compact_add(self, mark):
        return self.add(mark)

    def compact_here(self, mark):
        return mark.is_ancestor(self.transcript)

    def compact_started(self):
        if not self.busy:
            self.set_busy(True)

    # ---- session title (click it in the header to rename)
    def show_title(self, title):
        self.session_title = title
        self.title_label.set_text(title or wording.say("sidebar.new_session"))
        self.set_title(f"Baude — {title}" if title else "Baude")
        # (not the sidebar: a chat being switched to fills it once it's in; a rename re-reads it)

    # ---- background tasks (commands and subagents the CLI runs after a turn ends)
    def on_task_event(self, kind, msg):
        tid = msg.get("task_id")
        if not tid:
            return
        task = self.tasks.setdefault(tid, {"task_id": tid, "status": "running"})
        if kind == "task_updated":
            task.update(msg.get("patch") or {})
        elif kind == "task_progress":  # its "description" is the current activity, not the title
            fields = {k: v for k, v in msg.items() if k not in ("type", "subtype", "uuid", "session_id")}
            fields["activity"] = fields.pop("description", None)
            task.update(fields)
        else:
            task.update({k: v for k, v in msg.items() if k not in ("type", "subtype", "uuid", "session_id")})
        if kind == "task_notification":
            task["status"] = msg.get("status") or "completed"
        card = self.tool_cards.get(task.get("tool_use_id"))
        if card is not None:
            if kind == "task_started":
                card.bind_task(task)
                if task.get("task_type") == "local_agent":
                    self.panel.link_card(card, task)
            elif kind == "task_notification" and (card.state in ("running", "background", "interrupted")
                                                  or card.task is None):
                # not on task_updated: it comes first, without the summary and output file. One settled as
                # interrupted (a chat opened as finished, settle_tasks) or never told of its task ended after all.
                card.finish_task(task)
        if kind == "task_notification" and not task.get("noted"):
            task["noted"] = True
            if (line := self.task_note(task)) is not None:  # (none while its card's in the chat: it says so)
                self.add(line)
        self.panel.update_task(task)
        self.update_tasks_indicator()

    def on_tasks_changed(self, running):
        """The CLI's current list of running tasks (endings arrive as task_notification)."""
        for t in running:
            if isinstance(t, dict) and t.get("task_id"):
                self.tasks.setdefault(t["task_id"], {"status": "running"}).update(t)
                self.panel.update_task(self.tasks[t["task_id"]])
        self.update_tasks_indicator()

    def running_tasks(self):
        return [t for t in self.tasks.values() if t.get("status") == "running"]

    def update_tasks_indicator(self):
        self.update_session_state()
        running = self.running_tasks()
        agents = sum(t.get("task_type") == "local_agent" for t in running)
        procs = len(running) - agents
        parts = ([wording.say("status_bar.processes", n=procs)] if procs else []) + \
                ([wording.say("status_bar.subagents", n=agents)] if agents else [])
        self.tasks_btn.set_visible(bool(running))
        self.tasks_btn.set_label(wording.say("status_bar.running", tasks=" · ".join(parts)) if running else "")
        self.tasks_btn.set_tooltip_text("\n".join(t.get("description") or t["task_id"] for t in running)
                                        + "\n" + wording.say("status_bar.open_panel"))

    def stop_task(self, task_id):
        self.request({"subtype": "stop_task", "task_id": task_id}, lambda _r: None)
        self.note(wording.say("side_panel.stop_requested", task=self.tasks.get(task_id, {}).get("description") or task_id))

    def place_split(self):
        """Give the panel the width it was dragged to when it opens, else about a third of the window."""
        width = self.split.get_width() or self.get_width()
        if width:
            want = self.panel_width or max(320, min(460, int(width * 0.36)))
            chat = self.split.get_start_child().measure(Gtk.Orientation.HORIZONTAL, -1)[0]  # (what it can't do without)
            self.split.set_position(width - max(320, min(want, width - chat)))

    def panel_closed_by_drag(self, width):
        """Dragged on past its least and closed: what's kept is the width it was, not that."""
        self.panel_width = width
        save_settings(panel_width=width)

    def split_dragged(self):
        """The rule between the chat and the panel moved while it's held: the panel's width is yours from now on
        (saved once the dragging stops)."""
        if not self.panel_slide.following() or not (self.panel.get_visible() and self.split.get_width()):
            return
        self.panel_width = self.split.get_width() - self.split.get_position()
        if self.split_save is not None:
            GLib.source_remove(self.split_save)
        self.split_save = GLib.timeout_add(400, lambda: setattr(self, "split_save", None) or
                                           save_settings(panel_width=self.panel_width) or False)

    def show_panel(self, show=True, page=None):
        self.panel_slide.set_shown(show)
        if show:
            self.panel.show_page(page or ("agents" if any(t.get("task_type") == "local_agent"
                                                          for t in self.running_tasks()) else "processes"))

    def toggle_meta(self):
        """Ctrl+M (widgets.META_KEY), from anywhere in the window: Meta shown (show_meta), or closed if it's open
        already (close_meta)."""
        if self.panel_slide.target and self.panel.stack.get_visible_child_name() == "meta" \
                and self.capture_cover() is None:
            self.close_meta()
        else:
            self.show_meta()

    def show_meta(self):
        """Meta's page of the side panel, the panel shown if it was hidden, and the focus in Meta's box."""
        cover = self.capture_cover()  # (the large image, the Kubernetes or the database view: over Meta too)
        if cover is not None:
            cover.close()
        if self.completer.visible():
            self.completer.hide()  # (/'s or @'s list, not left over the chat's box)
        self.refs.forget()
        self.show_panel(True, "meta")
        self.panel.meta.composer.grab_focus()

    def _focus_moved(self, _pspec):
        focus = self.get_focus()
        if focus is not None and not focus.is_ancestor(self.panel):
            self.meta_from = focus

    def close_meta(self):
        """The side panel hidden, Meta's @ list with it, and the focus, if it was in the panel, back where it was last
        outside it if that's still on screen, else in what the tab shown has to type in or scroll: the chat's box,
        the transcript (the box hidden: read-only), a file's code."""
        meta_chat, focus = self.panel.meta, self.get_focus()
        if meta_chat.refs.visible():
            meta_chat.refs.hide()
        self.show_panel(False)
        if focus is not None and not focus.is_ancestor(self.panel):
            return
        page = self.tabs.current()
        if not (widgets.focus_shown(self.meta_from) or widgets.focus_shown(self.input)
                or widgets.focus_shown(self.scroller if page is self.chat_column else
                                       page.code.view if isinstance(page, CodePage) else None)
                or (page is not None and page.child_focus(Gtk.DirectionType.TAB_FORWARD))):
            self.set_focus(None)  # (not left in Meta's box as it slides shut)

    def h_stream_event(self, msg):
        ev, parent = msg.get("event", {}), msg.get("parent_tool_use_id")
        et = ev.get("type")
        key = lambda i: (parent, i)  # noqa: E731
        if et == "message_start":
            self.streamed_ids.add(ev["message"]["id"])
        elif et == "content_block_start":
            cb = ev["content_block"]
            if cb["type"] == "text":
                self.blocks[key(ev["index"])] = self.add(self.reply_view(), parent)
            elif cb["type"] == "thinking":  # shown once it has words (most blocks never do)
                view = ThinkingView()
                view.parent_tool = parent
                self.reply_views.append(view.md)  # (its names link as a reply's do)
                self.blocks[key(ev["index"])] = view
            elif cb["type"] in ("tool_use", "server_tool_use"):
                self.blocks[key(ev["index"])] = self.add(self.tool_card(cb["name"], cb["id"]), parent)
        elif et == "content_block_delta":
            w, d = self.blocks.get(key(ev["index"])), ev["delta"]
            if w is None:
                return
            if d["type"] == "text_delta":
                w.append(d["text"])
            elif d["type"] == "thinking_delta":
                w.append(d["thinking"])
                if w.get_parent() is None and w.has_text():
                    self.add(w, w.parent_tool)
            elif d["type"] == "input_json_delta":
                w.add_partial_json(d["partial_json"])

    def h_assistant(self, msg):
        m, parent = msg["message"], msg.get("parent_tool_use_id")
        if parent is None and self.compact_mark is not None and self.compact_mark.state == "done":
            self.compact_mark = None  # (the conversation's gone on: a summary only follows its boundary at once)
        if parent is None:  # (what a turn ending in an error last said: the error, said once, not again by h_result)
            said = "".join(c.get("text", "") for c in m.get("content", []) if c.get("type") == "text").strip()
            self.said_last = said.removeprefix("⚠").strip() or self.said_last
        for w in self.blocks.values():  # its streamed text is complete: no frame's delay
            if isinstance(w, (MarkdownView, ThinkingView)):
                w.finish()
        if msg.get("is_api_error_message") or m.get("model") == "<synthetic>":
            stopped = parent is None and getattr(self.proc, "stop_asked", False)
            for c in m.get("content", []):
                if c.get("type") == "text" and not (stopped and STOP_SAID_RE.fullmatch(c["text"])):  # (a stop's
                    # words, aborted: said once, as the turn ends, h_result)
                    self.add(api_error_label(c["text"]), parent)
            return
        self.render_assistant(m.get("content", []), parent, streamed=m.get("id") in self.streamed_ids)
        if parent is None:
            self.refresh_context_soon()

    def render_assistant(self, content, parent=None, streamed=False, written_at=None):
        """Assistant content blocks. Streamed messages already drew their text and thinking."""
        for c in content if isinstance(content, list) else [{"type": "text", "text": str(content)}]:
            if c.get("type") in ("tool_use", "server_tool_use"):
                card = self.tool_cards.get(c["id"])
                if card is None:
                    card = self.add(self.tool_card(c["name"], c["id"]), parent)
                card.set_input(c.get("input", {}))
                if c["id"] in self.orphan_results:  # its result was drawn before it (older history)
                    self.render_tool_results([self.orphan_results.pop(c["id"])])
            elif str(c.get("type")).endswith("_tool_result") and c.get("tool_use_id") in self.tool_cards:
                # (a server tool's result, a web search's: in the reply itself; an error's a dict of type …_error)
                failed = isinstance(c.get("content"), dict) and str(c["content"].get("type")).endswith("_error")
                self.tool_cards[c["tool_use_id"]].set_state("error" if failed else "done")
            elif c.get("type") == "text" and c.get("text", "").strip():
                self.queue_refs(c["text"], written_at)  # first: new names are drawn pending, not redrawn
                if not streamed:
                    self.add(self.reply_view(c["text"]), parent)
                self.add_artifact_chips(c["text"], parent)
            elif c.get("type") == "thinking" and c.get("thinking", "").strip():
                self.queue_refs(c["thinking"], written_at)  # (its names link as a reply's do)
                if not streamed:
                    view = ThinkingView(c["thinking"])
                    self.reply_views.append(view.md)
                    self.add(view, parent)

    def render_tool_results(self, content, from_file=False):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") not in self.tool_cards:
                self.orphan_results[c.get("tool_use_id")] = c
            if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in self.tool_cards:
                card = self.tool_cards[c["tool_use_id"]]
                if from_file:
                    self.task_from_result(card, c.get("content"))  # before: the card waits on it
                card.set_result(c.get("content"), c.get("is_error", False))
                if card.task is None and self.task_lines:  # (its task's ending drawn before it: a later part)
                    self.card_task(card)
                if card.artifact:
                    self.register_artifact(card.artifact)

    # ---- artifacts
    def register_artifact(self, info):
        """Remember an artifact (newer info wins, but a known title is never lost)."""
        old = self.artifacts.get(info["url"], {})
        merged = {**old, **{k: v for k, v in info.items() if v}}
        if old.get("title") and merged.get("title") == wording.say("references.claude_artifact"):
            merged["title"] = old["title"]
        self.artifacts[info["url"]] = merged
        if merged.get("title") and merged["title"] != wording.say("references.claude_artifact"):
            ARTIFACT_TITLES[merged["url"]] = merged["title"]  # links in replies read as the title
        self.panel.update_artifact(merged)
        return merged

    def add_artifact_chips(self, text, parent=None):
        """Artifact links in a reply are drawn inline (see link_markup); here they're only
        recorded, so the side panel lists them."""
        for url in dict.fromkeys(ARTIFACT_URL.findall(text or "")):
            if url not in self.artifacts:
                self.register_artifact({"url": url, "title": wording.say("references.claude_artifact")})

    # ---- Signal
    def signal_menu(self):
        """The link button's dropdown: this chat's two Signal switches (shown links, and running)."""
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=8, margin_bottom=6, margin_start=10,
                      margin_end=10, width_request=310)

        def row(title, detail):
            line = Gtk.Box(spacing=12)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            text.append(Gtk.Label(label=title, xalign=0))
            sub = Gtk.Label(label=detail, xalign=0, wrap=True, max_width_chars=38)
            sub.add_css_class("dim-label")
            sub.add_css_class("caption")
            text.append(sub)
            sw = Gtk.Switch(valign=Gtk.Align.CENTER)
            line.append(text)
            line.append(sw)
            box.append(line)
            return sw
        self.signal_show = row(wording.say("signal_menu.show_links"), wording.say("signal_menu.show_links_detail"))
        self.signal_run = row(wording.say("signal_menu.activate"), wording.say("signal_menu.activate_detail"))
        pop.set_child(box)
        return pop

    # ---- Signal per chat, as Remote Control is: each chat keeps its own two switches, and a new chat starts with
    # its links shown and Signal not activated
    SIGNAL_CHATS = 500  # chats whose switches are kept (the most recently changed)

    def chat_signal(self, sid):
        """A chat's Signal switches, (on, links): its own if it has some, else a new chat's (not activated, links
        shown)."""
        s = load_settings()
        kept = (s.get("signal_chats") or {}).get(sid) if sid else None
        if isinstance(kept, list) and len(kept) == 2:
            return bool(kept[0]), bool(kept[1])
        return False, True

    def keep_chat_signal(self, sid, on, links):
        chats = {k: v for k, v in (load_settings().get("signal_chats") or {}).items() if k != sid}
        chats[sid] = [bool(on), bool(links)]
        save_settings(signal_chats=dict(list(chats.items())[-self.SIGNAL_CHATS:]))

    def apply_chat_signal(self):
        """The chat shown changed: its switches (a new chat's: links shown and not activated, or what you set before
        it had an id), and Signal as they say."""
        on, links = self.chat_signal(self.session_id) if self.session_id else \
            (self.signal_pending or self.chat_signal(None))
        for sw, value, handler in ((self.signal_run, on, self.signal_run_handler),
                                   (self.signal_show, links, self.signal_show_handler)):
            sw.handler_block(handler)
            sw.set_active(value)
            sw.handler_unblock(handler)
        self.set_signal(save=False)

    def set_signal(self, on=None, links=None, save=True):
        """This chat's two Signal switches (None: as they are now); save keeps them for the chat. on: it looks up the
        names in the chat's replies, and the reader reads the chat. links: the names it knows are drawn as links. With
        links alone it draws what this folder's glossary already knows and what the catalogue and git say, with no
        model (and makes no glossary where there's none); with on alone it fills the glossary while the replies stay plain. A folder's Signal is
        shared by the windows that show its chats: it runs while one of their chats has it on, and only that one
        steers its reader. The replies shown are relinked either way."""
        on = self.signal_run.get_active() if on is None else on
        links = self.signal_show.get_active() if links is None else links
        if save:
            if self.session_id:
                self.keep_chat_signal(self.session_id, on, links)
            else:
                self.signal_pending = (on, links)  # (kept once the chat has an id: h_system)
        old = self.signal
        was_on = old is not None and self in old.runners
        if old is not None:
            if self.on_refs_changed in old.listeners:
                old.listeners.remove(self.on_refs_changed)
                old.status_listeners.remove(self.on_ref_status)
                old.ask_listeners.remove(self.ask_lsp)
                old.question_listeners.remove(self.ask_ref)
            old.runners.discard(self)
        folder, home = (self.ref_checkout, self.cwd) if self.ref_checkout else (self.cwd, None)
        keep = bool(self.cwd) and (on or links)  # (links alone: what's known, the catalogue's and git's)
        self.signal = Signal.for_folder(folder, home=home, halted=not on) if keep else None
        if old is not None and old is not self.signal:
            if not old.listeners:
                stopped = old.stop()  # (no window shows its chat now: everything it was doing stops)
                if save and was_on:
                    self.ref_toast("Signal is off" + (": stopped " + ", ".join(stopped) if stopped else ""))
            elif not old.runners and not old.halted:
                old.links_only()  # (another window shows its links, but no chat runs it now)
        if self.signal is not None:
            if on:
                self.signal.runners.add(self)
                self.signal.start()  # (if it was stopped)
                if save and not was_on:
                    self.ref_toast("Signal is on for this chat: the names in its replies are looked up" +
                                   (" and become links" if links else ""), show=True)
            elif not self.signal.runners and not self.signal.halted:
                stopped = self.signal.links_only()
                if save and was_on:
                    self.ref_toast("Signal stopped looking things up" + (" (" + ", ".join(stopped) + ")" if stopped
                                                                         else "") + "; its links stay")
            self.signal.listeners.append(self.on_refs_changed)
            self.signal.status_listeners.append(self.on_ref_status)
            self.signal.ask_listeners.append(self.ask_lsp)
            self.signal.question_listeners.append(self.ask_ref)
            self.use_links()
            if on and self.signal.runners <= {self}:
                self.signal.steer(self.ref_transcript(), self)  # (a new chat with no transcript: none, not the chat before)
            elif on or self.signal.halted:  # (a chat that doesn't run it never steers one another window runs)
                self.signal.follow(self.ref_transcript(), self)
            for view in self.assistant_views():
                self.queue_refs(view.text)
            if self.history and not self.history.todo:  # the replies further up, not drawn yet (or, while the start of
                self.queue_history_refs(self.history.earlier_replies())  # a long chat's indexed, once it is)
        elif markdownview.SIGNAL is old:
            markdownview.SIGNAL = None
        if old is not None and was_on and (old is not self.signal or not on) and old.runners:
            old.steer((runner := next(iter(old.runners))).ref_transcript(), runner)  # (another window's chat runs it: its reader reads that)
        for view in self.assistant_views():
            view.relink()
        self.panel.signal_page.changed()
        self.on_ref_status()

    def allowed_in(self, session_id):
        """The tools always allowed in a chat (each chat its own, as its host keeps them; a new one's, till it has an
        id, under None)."""
        return self.always_allowed.setdefault(session_id, set())

    def chat_key(self, session_id):
        """A chat's notification's key: a new chat's, till it has an id, this window's (seen_chat takes it back then)."""
        return f"chat-{session_id}" if session_id else f"chat-new-{id(self)}"

    def looking_at(self, session_id):
        """Whether the chat's in front of you: some Baude window the one you're in, with it open and its chat's tab
        shown (not Feedback, not a file's code)."""
        app = self.get_application()
        return any(w.is_active() and w.session_id == session_id and w.tabs.current() is w.tabs.get("chat")
                   for w in (app.windows() if app is not None else [self]))

    def tell_desktop(self, session_id, body, title=None, info=None):
        """A turn's end, or a question it asks, in a chat you aren't looking at (looking_at: in another window or
        app, behind the Feedback tab, or left mid-turn behind another chat), as a desktop notification
        (widgets.desktop_notify): a click on it brings this window up with that chat open. info: the chat's, if it's
        known (a network chat's, which find_session doesn't know). The sidebar's dot says it too. Other programs'
        chats (Claude Desktop, a terminal) are theirs to say: they notify on their own."""
        if self.looking_at(session_id):
            return
        here = session_id == self.session_id
        if info is None and session_id and not session_id.startswith("new-") and not here:
            info = find_session(session_id)
        if title is None:
            title = self.title_label.get_text() if here else (info or {}).get("title")
        me = weakref.ref(self)

        def clicked():
            if (win := me()) is None:
                return
            win.present()
            win.show_chat()
            if session_id and session_id != win.session_id and info is not None:
                win.opening_remote = None  # (as a click on its row in the sidebar opens it: Sidebar.on_activate)
                if info.get("remote") is not None:
                    win.open_remote_soon(info)
                else:
                    win.switch_to(lambda: win.resume_session(info))
        widgets.desktop_notify(self.chat_key(session_id), title or wording.say("common.untitled"), body, clicked)

    def seen_chat(self):
        """The open chat seen (this window's the one you're in, on its chat's tab, or the chat's just opened): its
        notification gone."""
        if self.is_active() and self.tabs.current() is self.tabs.get("chat"):
            widgets.withdraw_notification(self.chat_key(self.session_id))

    def links_on_focus(self):
        """This window became the active one: its own links are drawn (another window's chat may show none)."""
        before = markdownview.SIGNAL
        self.use_links()
        if markdownview.SIGNAL is not before:
            for view in self.assistant_views():
                view.relink()

    def use_links(self):
        """Point markdownview.SIGNAL (what _inline links names with) at this window's Signal if its chat shows links,
        else at none: before this window's replies are drawn, and when it becomes the active window."""
        markdownview.SIGNAL = self.signal if self.signal is not None and self.signal_show.get_active() else None

    def destroy(self):
        self.drop_signal()  # (destroyed without being closed: its Signal is let go too)
        super().destroy()
        self.let_go()

    def let_go(self):
        """The window is closed: what still runs for it stops (its timers, its file watches), and its chat is freed
        (see discard). Another window keeps the app going, and a closed one lived on till the end, chat and all,
        polling its transcript every second. (Not the rest of it: list views manage their rows' widgets themselves,
        and disposing those by hand unrefs them twice.)"""
        if getattr(self, "let_go_done", False):
            return False
        self.let_go_done = True
        for owner in (self, self.panel, self.sidebar):
            for name in ("poll_source", "expiry"):
                if (source := getattr(owner, name, None)) is not None:
                    setattr(owner, name, None)
                    if GLib.MainContext.default().find_source_by_id(source):
                        GLib.source_remove(source)
        self.tail.stop()
        self.panel.meta.close_agent()  # (Meta's session: the window's own)
        for fn in (self.on_network, self.sidebar.on_network):  # (the network's news isn't this window's any more)
            if fn in network().listeners:
                network().listeners.remove(fn)
        providerslib.unlisten(self.providers_found)
        for mon in self.sidebar.monitors.values():
            mon.cancel()
        self.sidebar.monitors.clear()
        self.sidebar.drop_midnight()
        self.kube.close()
        Gtk.StyleContext.remove_provider_for_display(self.get_display(), self.zoom_css)  # (its zoom: the display's)
        self.clear_transcript(closing=True)
        self.reset_stream_state()  # (the cards by id, too)
        self.history = None
        return False

    def drop_signal(self):
        """The window is gone: it stops listening to its Signal (which stops, if no other window shows it), and its
        links are no longer the ones drawn."""
        ref, self.signal = self.signal, None
        if ref is None:
            return
        for listeners, fn in ((ref.listeners, self.on_refs_changed), (ref.status_listeners, self.on_ref_status),
                              (ref.ask_listeners, self.ask_lsp), (ref.question_listeners, self.ask_ref)):
            if fn in listeners:
                listeners.remove(fn)
        ran = self in ref.runners
        ref.runners.discard(self)
        if not ref.listeners:
            ref.stop()
        elif not ref.runners and not ref.halted:
            ref.links_only()  # (another window shows its links; none runs it now)
        elif ran and ref.runners:
            ref.steer((runner := next(iter(ref.runners))).ref_transcript(), runner)  # (its reader reads the chat that runs it now)
        if markdownview.SIGNAL is ref:
            markdownview.SIGNAL = None

    def git_chat(self):
        """The chat the Git page keeps its state for (GitPage.chat_check): its session."""
        return self.session_id

    def ref_transcript(self):
        """The shown chat's transcript, if there is one yet."""
        return self.view_path if self.resume_pending else \
            (find_session(self.session_id) or {}).get("path") if self.session_id else None

    def follow_checkout(self, transcript=None):
        """The chat works in another checkout of its folder's repository (it moved into a worktree): Signal
        searches that one, with a glossary of its own. Looked at when a session opens, as it grows, and after
        each turn; and the reader reads what's new."""
        transcript = transcript or self.ref_transcript()
        if not transcript or not self.cwd:
            return  # (no transcript to tell from: it stays where it is)
        checkout = chat_checkout(self.cwd, transcript)
        if checkout != self.ref_checkout:
            self.ref_checkout = checkout
            if self.signal is not None:
                self.set_signal(save=False)
        if self.signal is not None and (self.signal_run.get_active() or self.signal.halted):
            self.signal.follow(transcript, self)  # (a chat that doesn't run it never steers one another window runs)
        self.panel.git_page.chat_changed()

    def ask_ref(self, topic, detail):
        """Signal asks, once: fetch the tool schemas from Anthropic ("tools"), or start the MCP servers
        configured here to read their tools ("mcp"). Your answer is kept in settings."""
        key = {"tools": "signal_tool_schemas", "mcp": "signal_mcp_schemas"}[topic]
        if load_settings().get(key) is not None:
            return
        if getattr(self, "ref_dialog", None) is not None:  # (asked after the one that's up)
            self.ref_questions = [q for q in getattr(self, "ref_questions", []) if q[0] != topic] + [(topic, detail)]
            return
        if topic == "tools":
            dlg = Adw.AlertDialog(
                heading="Fetch Claude Code's tool schemas?",
                body="When a reply talks about a tool (the Agent tool, TodoWrite, Read), Signal can show what it "
                     "takes and does. Baude would download Anthropic's Agent SDK package for your Claude Code "
                     f"({detail or 'its version'}; about 1.3 MB from npm, keeping only its tool types) and the tools page "
                     f"of the Claude Code docs, into {pretty_path(tool_dir())}. The switch on the Signal page changes this.",
                default_response="fetch", close_response="later")
            dlg.add_response("no", "Don't Fetch")
            dlg.add_response("fetch", "Fetch")
            dlg.set_response_appearance("fetch", Adw.ResponseAppearance.SUGGESTED)
        else:
            names = ", ".join(f"{n} ({c.get('command')})" for n, c in list(detail.items())[:6])
            dlg = Adw.AlertDialog(
                heading="Start MCP servers to read their tools?",
                body=f"This folder configures MCP servers: {names}. To show their tools' schemas, Signal would "
                     "start each one briefly, ask for its tool list, and stop it.",
                default_response="fetch", close_response="later")
            dlg.add_response("no", "Don't Start")
            dlg.add_response("fetch", "Start Them")
            dlg.set_response_appearance("fetch", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, response):
            if self.ref_dialog is not dlg:  # (answered already)
                return
            self.ref_dialog = None
            if getattr(self, "ref_questions", None):
                GLib.idle_add(lambda: self.ask_ref(*self.ref_questions.pop(0)) and False)
            if response in ("no", "fetch") and self.signal is not None:
                (self.signal.set_tool_schemas if topic == "tools" else self.signal.set_mcp_schemas)(
                    response == "fetch")
                if topic == "tools":
                    self.panel.signal_page.tools.set_active(response == "fetch")
            elif response in ("no", "fetch"):
                save_settings(**{key: response == "fetch"})
        dlg.connect("response", answered)
        self.ref_dialog = dlg
        dlg.present(self)

    def ask_lsp(self):
        """Signal wants a language server and none is fetched: ask, once (your answer is kept in settings)."""
        if getattr(self, "lsp_dialog", None) is not None or load_settings().get("signal_lsp") is not None:
            return
        what = ", ".join(f"{name} {version}" for name, version in REF_LSP_PACKAGES)
        dlg = Adw.AlertDialog(
            heading="Fetch language servers for code names?",
            body="Signal can ask a language server which definition a code name means (whose field "
                 "previous.job_id is) instead of guessing from a search. Baude would fetch "
                 f"{what} with npm (about 60 MB) into {pretty_path(lsp_dir())}, and run them read-only on this "
                 "folder's packages: pyright with each package's .venv, TypeScript with each package's own. "
                 "The switch on the Signal page changes this later.",
            default_response="fetch", close_response="later")
        dlg.add_response("no", "Don't Fetch")
        dlg.add_response("fetch", "Fetch")
        dlg.set_response_appearance("fetch", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, response):
            self.lsp_dialog = None
            if response in ("no", "fetch"):
                if self.signal is not None:
                    self.signal.set_lsp(response == "fetch")
                else:
                    save_settings(signal_lsp=response == "fetch")
                self.panel.signal_page.lsp.set_active(response == "fetch")  # (the setting is saved: it only shows it)
        dlg.connect("response", answered)
        self.lsp_dialog = dlg
        dlg.present(self)

    def assistant_views(self):
        return self.reply_views

    def reply_view(self, text=""):
        view = MarkdownView(text)
        self.reply_views.append(view)
        return view

    def queue_refs(self, text, written_at=None):
        if self.signal is not None and text:
            for s in self.signal.queue(text, self.session_id, written_at or time.time(), self.touched_paths(),
                                       look=self.signal_run.get_active()):
                self.ref_surfaces.setdefault(s, True)
            self.panel.signal_page.changed()

    HISTORY_REFS_MS = 4  # how long queue_history_refs reads at a time, between frames

    def queue_history_refs(self, items):
        """Replies earlier in a long session, not drawn yet: looked up too, newest first, after the ones on
        screen (so the Signal page covers the whole session, and links are ready when you scroll up). items:
        newest first (HistoryIndex.earlier_replies, read from the transcript only once it's needed here). A few ms of
        them at a time, between frames, and none while the chat moves (queue_history_some): all at once, a long
        chat's froze the window for half a second or more, a second after it opened."""
        if self.signal is None:
            return
        self.history_refs = items = iter(items)  # (these: not those given before, if they're not all read yet)
        GLib.idle_add(self.queue_history_some, self.history, items, priority=GLib.PRIORITY_LOW)

    def queue_history_some(self, history, items):
        """HISTORY_REFS_MS of queue_history_refs' replies, then on in the next idle moment; or on once the chats have
        been still a moment (Signal.STILL_S)."""
        if items is not self.history_refs:
            return False  # (given again since)
        if history is not self.history or self.signal is None:
            self.history_refs = None  # (another chat now, or Signal off)
            return False
        if (wait := Signal.moved_at + Signal.STILL_S - time.monotonic()) > 0:
            GLib.timeout_add(int(wait * 1000) + 1, self.queue_history_some, history, items, priority=GLib.PRIORITY_LOW)
            return False
        end = time.monotonic() + self.HISTORY_REFS_MS / 1000
        for kind, content, *rest in items:
            if kind != "assistant" or (rest and rest[0]):
                continue
            for c in content if isinstance(content, list) else [{"type": "text", "text": str(content)}]:
                if isinstance(c, dict) and (text := c.get("text") or c.get("thinking") or "").strip() and \
                        c.get("type") in ("text", "thinking"):
                    self.queue_refs(text, ref_epoch(rest[1]) if len(rest) > 1 else None)
            if time.monotonic() > end:
                GLib.idle_add(self.queue_history_some, history, items, priority=GLib.PRIORITY_LOW)
                return False
        self.history_refs = None
        return False

    def on_ref_status(self):
        """What Signal is doing changed: the button is lit while it's on and pulses while it works, and its
        tooltip says what a click does; the page says what it's doing."""
        ref = self.signal
        running = ref is not None and not ref.halted and self in ref.runners  # (this chat's: another may run it)
        busy = running and ref_busy(ref)
        shown = ref is not None and markdownview.SIGNAL is ref
        for css, on in (("ref-working", busy), ("ref-on", running), ("ref-links", shown and not running)):
            (self.signal_btn.add_css_class if on else self.signal_btn.remove_css_class)(css)
        if (icon := next((w for w in walk(self.signal_btn) if isinstance(w, Gtk.Image)), None)) is not None:
            (Ticker.pulse if busy else Ticker.still)(icon)
        hidden = "" if shown else "\nIts links are hidden (Show links)"
        if ref is None:
            tip = wording.say("signal_menu.tip_off")
        elif not running:
            tip = wording.say("signal_menu.tip_links", status=ref.status_text())
        elif busy:
            tip = f"Signal is working: {ref.status_text()}.{hidden}\nClick to stop it (Activate Signal)"
        else:
            tip = f"Signal is on: {ref.status_text()}.{hidden}\nClick for its switches"
        self.signal_btn.set_tooltip_text(tip)
        self.panel.signal_page.status_changed()

    def ref_toast(self, title, show=False):
        t = Adw.Toast(title=html.escape(title), timeout=4)
        if show:
            t.set_button_label(wording.say("common.show"))
            t.connect("button-clicked", lambda *_: self.show_panel(True, "refs"))
        self.toasts.add_toast(t)

    def touched_paths(self):
        """Files this session's own tools read or wrote: the ones its replies most likely mean."""
        return {p for card in self.tool_cards.values() for k in ("file_path", "notebook_path", "path")
                if isinstance(p := card.input.get(k), str)}

    def on_refs_changed(self, surfaces):
        if self.signal is None:
            return
        self.use_links()  # (this window's replies are drawn with this window's links)
        for view in self.assistant_views() if markdownview.SIGNAL is self.signal else ():  # (links hidden: nothing to redraw)
            if any(s in view.text for s in surfaces):
                view.relink()
        self.panel.signal_page.changed()

    def pointer_rect(self, widget):
        """A 1px rectangle at the pointer, in widget's coordinates (where a popover should point)."""
        ok, pt = self.compute_point(widget, Graphene.Point().init(*self.pointer))
        if not ok:
            return None
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(pt.x), int(pt.y), 1, 1
        return rect

    def open_ref(self, surface, anchor, message=None):
        """A Signal link was clicked: where the reader placed it in that reply; else a file opens, a commit or branch
        shows in the Git page, a pull request or issue opens as ⌘K opens it (fixview.open_github: its page, or one of
        Baude's own feedback issues on the Feedback tab), a tool shows its card, and everything else (several places,
        not found, stale, still looking) gets a popover."""
        dec = self.signal.decision(message, surface) if self.signal else None
        if dec is not None and dec["verdict"] == "link" and dec.get("path"):
            full = os.path.join(self.signal.root, dec["path"])
            if dec.get("role") in ("table", "column"):  # a table or column: its database
                name = re.sub(r"\([^()]*\)$", "", surface).split(".")[-1]
                code = (full, dec.get("line"))
                if dec["role"] == "column":
                    return self.open_db_name(ref_column_table(full, dec.get("line")), name, dec["path"], code,
                                             anchor, surface)
                return self.open_db_name(name, None, dec["path"], code, anchor, surface)
            elif dec.get("kind") in ("service", "k8s") and (pop := self.runs_popover(surface, dec, anchor)):
                return pop
            elif os.path.exists(full):
                open_code(full, dec.get("line"), self)
            return
        if dec is not None and dec["verdict"] == "not_in_code":  # (what the reader says it is, in this reply)
            why = REF_ABSENT_WHY.get(dec.get("why"), REF_ABSENT_WHY["not_found"])
            ref = Ref(surface, "external", "resolved", data={"note": dec.get("note") or why}) \
                if dec.get("why") == "external" else \
                Ref(surface, dec.get("kind") if dec.get("kind") in REF_KINDS else "symbol", "not_found",
                    reason=why[0].lower() + why[1:] + (f": {dec['note']}" if dec.get("note") else ""))
            pop = RefPopover(self, ref)
            pop.attach(anchor, self.pointer_rect(anchor))
            pop.popup()
            return pop
        ref = self.signal.refs.get(surface) if self.signal else None
        if (ref is None or ref.status == "pending") and self.signal and ref_hashlike(surface):
            return self.open_commit_first(surface, anchor, message)
        if ref is None:
            return
        if ref.status == "pending":  # (not looked up yet: say so, and what it turns out to be)
            return self.lookup_pending(surface, anchor, message)
        if (live := ref.data.get("db")):  # (a running database's table, collection or database: its diagram)
            if live.get("table"):
                return self.open_db_name(live["table"], None, "", None, anchor, surface)
            server = next((s for s in pg_servers_cached() if s["container"] == live.get("container")), None)
            if server is not None:
                self.schema.open(found=(server, live["database"], None, None))
            return None
        if ref.status == "resolved" and ref.kind in ("commit", "branch"):  # (its place in the history: the Git page)
            self.show_git_ref(ref)
            return
        if ref.status == "resolved" and ref.kind == "pr" and ref.data.get("url"):  # (not its card: its page)
            d = ref.data
            return fixview.open_github(self, {"pr": not d.get("issue"), "repo": d["repo"], "number": d["number"],
                                              "url": d["url"]})
        if ref.status == "resolved" and (ref.kind in ("tool", "subagent", "skill") or ref.data.get("context")):
            self.show_ref(surface)
            return
        defs = [l for l in ref.locations if l["role"] != "used"]
        picked = ref.status == "ambiguous" and ref.data.get("checked") == "haiku" and ref.data.get("pick") and \
            ref.locations  # (Haiku chose one of the matches for the sentence: that's what a click opens)
        if ref.kind == "symbol" and ref.status in ("resolved", "ambiguous") and defs and not ref_span(surface) and \
                (all(l["role"] in ("table", "column") for l in defs) or
                 picked and ref.locations[0]["role"] in ("table", "column")):  # a table or column: its database
            return self.show_schema_for(ref, anchor)
        if picked and self.open_location(ref, 0):
            return
        if ref.status == "resolved" and ref.kind in ("file", "symbol", "constant", "config", "cli", "k8s") and \
                len(ref.locations) == 1 and not (ref.kind == "k8s" and ref_run_names(ref)) \
                and self.open_location(ref, 0):  # (a workload: the popover says where it runs, too)
            return
        pop = RefPopover(self, ref)
        pop.attach(anchor, self.pointer_rect(anchor))
        pop.popup()
        return pop

    def on_pointer(self, _ctl, x, y):
        self.pointer = (x, y)
        if self.hover_soon is None:  # (once a frame at most, after the widget under it has seen the move)
            self.hover_soon = GLib.idle_add(self.check_hover)

    def pointer_left(self):
        if self.hover_soon is not None:  # (a check on its way would light what it was on again)
            GLib.source_remove(self.hover_soon)
            self.hover_soon = None
        self.hover_link(None)

    def check_hover(self):
        self.hover_soon = None
        picked = self.pick(*self.pointer, Gtk.PickFlags.DEFAULT)
        uri = None
        if isinstance(picked, Gtk.Label):
            ok, point = self.compute_point(picked, Graphene.Point().init(*self.pointer))
            uri = hover_uri(picked, point.x, point.y) if ok else None
        self.hover_link(uri)
        return False

    def hover_link(self, uri):
        """The pointer went onto a link (uri), or off one (None). A commit or branch: its row in the Git page is lit
        while it's there. A table or column: the running database with it is looked for already (db_locate), so a
        click opens it at once, or joins the lookup on its way, rather than starting it."""
        if uri == self.hovered:
            return
        self.hovered = uri
        surface = message = None
        if uri and uri.startswith(REF_SCHEME):
            name, _, message = uri[len(REF_SCHEME):].partition("#")
            surface, message = urllib.parse.unquote(name), message or None
        ref = self.signal.refs.get(surface) if self.signal and surface else None
        git = ref if ref is not None and ref.status == "resolved" and ref.kind in ("commit", "branch") else None
        self.panel.git_page.hover(git)
        target = self.db_target(surface, message) if surface else None
        if target and (target != self.db_prefetched[0] or time.monotonic() - self.db_prefetched[1] > 20):
            self.db_prefetched = (target, time.monotonic())
            db_prefetch(target)

    def db_target(self, surface, message=None):
        """(table, column, hint) a click on a Signal link would look for in the running databases (open_ref's ways to
        a table or column), or None if it isn't one."""
        dec = self.signal.decision(message, surface) if self.signal else None
        if dec is not None and dec["verdict"] == "not_in_code":
            return None  # (the click says what the reader said)
        if dec is not None and dec["verdict"] == "link" and dec.get("path"):
            if dec.get("role") not in ("table", "column"):
                return None
            name = re.sub(r"\([^()]*\)$", "", surface).split(".")[-1]
            if dec["role"] == "column":
                return ref_column_table(os.path.join(self.signal.root, dec["path"]), dec.get("line")), name, dec["path"]
            return name, None, dec["path"]
        ref = self.signal.refs.get(surface) if self.signal else None
        if ref is None or ref.status == "pending" or ref.status == "resolved" and ref.data.get("context"):
            return None
        if (live := ref.data.get("db")):
            return (live["table"], None, "") if live.get("table") else None
        defs = [l for l in ref.locations if l["role"] != "used"]
        picked = ref.status == "ambiguous" and ref.data.get("checked") == "haiku" and ref.data.get("pick") and \
            ref.locations
        if not (ref.kind == "symbol" and ref.status in ("resolved", "ambiguous") and defs and not ref_span(surface) and
                (all(l["role"] in ("table", "column") for l in defs) or
                 picked and ref.locations[0]["role"] in ("table", "column"))):
            return None
        first = next((l for l in ref.locations if l["role"] in ("table", "column")), None)
        name = re.sub(r"\([^()]*\)$", "", ref.surface).split(".")[-1]
        if first and first["role"] == "column":
            table = ref_column_table(os.path.join(self.signal.root, first["path"]), first.get("line")) or \
                (ref.surface.split(".")[-2] if ref.surface.count(".") == 1 else None)
            return table, name, first["path"]
        return name, None, first["path"] if first else ""

    def show_ref(self, surface):
        self.show_panel(True, "refs")
        self.panel.signal_page.show_ref(surface)

    def show_git(self, rev=None, sha=None, folder=None):
        """The side panel's Git page: a branch's history (rev), or a commit (sha) highlighted in the history of a branch
        that has it; folder: another repository's."""
        self.show_panel(True, "git")
        self.panel.git_page.focus(rev, sha, folder)

    def show_git_ref(self, ref):
        """A commit or branch Signal resolved, on the Git page: a range's last commit; another repository's in its own."""
        if ref.kind == "branch":
            self.show_git(rev=(ref.data.get("ref") or "").removeprefix("refs/heads/").removeprefix("refs/remotes/")
                          or None, folder=ref.data.get("repo"))
        else:
            self.show_git(sha=ref.data.get("sha"), folder=ref.data.get("repo"))

    def runs_popover(self, surface, dec, anchor):
        """Something the reader linked that can run (a service, a Kubernetes object): a popover with its places and
        where it runs in your local clusters, rather than straight to the one place. None if no cluster could run
        it by that name."""
        ref = self.signal.refs.get(surface)
        if ref is None or ref.status not in ("resolved", "ambiguous") or \
                not any(l["path"] == dec["path"] for l in ref.locations):  # (the reader's own place, else)
            ref = Ref(surface, dec["kind"], "resolved", data={"kind": dec.get("role"), "name": surface,
                                                              "note": dec.get("note") or ""},
                      locations=[{"role": dec.get("role") or "definition", "path": dec["path"], "line": dec.get("line")}])
        if not ref_run_names(ref):
            return None
        pop = RefPopover(self, ref)
        pop.attach(anchor, self.pointer_rect(anchor))
        pop.popup()
        return pop

    def open_location(self, ref, index):
        """Open a location where it is now (it may have moved with the code). False if it's lost. One on another
        branch: its file as that branch has it, on the Code page."""
        loc = ref.locations[index]
        if loc.get("rev"):
            text = self.signal.text_on(loc)
            if text is None:
                toast(self, f"{loc['rev']} hasn't {loc['path']} any more")
                return False
            return bool(self.show_code(os.path.join(self.signal.root, loc["path"]), loc.get("line"), text=text,
                                       note=f"On {loc['rev']}: not in this checkout"))
        full, line, how = self.signal.locate(loc)
        if how == "lost":
            self.signal.mark_stale(ref.surface)
            toast(self, "The code there changed and this couldn't be found again")
            return False
        if how == "moved":
            loc["line"] = line
            self.signal.reanchor(ref.surface, index, line)
        open_code(full, line, self)
        return True

    @property
    def code_page(self):
        """The code tab shown, else the one opened last (CodePage); None with none open."""
        pages = [c for c in self.tabs.children() if isinstance(c, CodePage)]
        current = self.tabs.current()
        return current if current in pages else pages[-1] if pages else None

    def find_in_chat(self):
        """⌘F / Ctrl+F: find in the chat the focus is in (Meta's, a side question's, a subagent's: chatfind.ChatFind),
        else in the code tab shown, else in the main chat."""
        find = chatfind.ChatFind.for_focus(self)
        if find is not None:
            return find.open()
        if self.find_in_code():
            return True
        return self.chat_find.open() if self.tabs.current() is self.tabs.get("chat") else False

    def find_in_code(self):
        """Ctrl+F: find in the code tab shown, if one is; False (not handled) if not."""
        page = self.tabs.current()
        if not isinstance(page, CodePage) or not page.code.get_visible():
            return False
        page.code.open_search()
        return True

    def zoom_schema(self, steps):
        """Ctrl +/−/0 while the database schema view is open: its diagram zooms in or out about its middle, or (0)
        fits in the view. False when it isn't open."""
        if not self.schema.on_top():
            return False
        self.schema.zoom(steps)
        return True

    def zoom_code(self, steps):
        """Ctrl +/−/0 with the focus on code (a Code page, the Kubernetes view's YAML), or with a code tab shown and
        the focus in no chat (the Files page's list, say): the code's size, steps up or down (0: 100% again). False
        elsewhere, where they size the chat."""
        focus = self.get_focus()
        if not in_code(focus) and (not isinstance(self.tabs.current(), CodePage) or in_chat(focus)):
            return False
        CodeView.set_zoom(CodeView.zoom + steps * ZOOM_STEP if steps else 1.0)
        return True

    def zoom_code_scroll(self, ctl, dy):
        """Ctrl+scroll with the pointer on the Code page (or the Kubernetes view's YAML) sizes the code, not the
        chat; False anywhere else."""
        if dy == 0 or not ctl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return False
        # a scroll event has no position of its own: where the pointer is (on the surface, around the window's shadow)
        ok, x, y, _mask = self.get_surface().get_device_position(self.get_display().get_default_seat().get_pointer())
        sx, sy = self.get_surface_transform()
        if not ok or not in_code(self.pick(x - sx, y - sy, Gtk.PickFlags.DEFAULT)):
            return False
        CodeView.zoom_scroll(dy, ctl.get_unit() == Gdk.ScrollUnit.SURFACE, ctl.get_current_event_time())
        return True

    def show_code(self, path, line=None, text=None, note=None):
        """Show a file (or the text of a version of it) in a tab of its own in the main area, beside the chat (one
        open already: shown, at line). False if it isn't something to read here (a folder, a binary or very big
        file)."""
        if text is None:
            if os.path.isdir(path):
                return False
            try:
                with open(path, "rb") as f:
                    data = f.read(CODE_LIMIT + 1)
            except OSError:
                return False
            if len(data) > CODE_LIMIT or b"\0" in data[:8192]:
                return False
            text = data.decode("utf-8", "replace")
        name = os.path.basename(path) + (f" ({note})" if note else "")
        page = self.tabs.open(("code", os.path.realpath(path), note or ""), lambda: CodePage(self), name,
                              icon=themed_icon("text-x-script-symbolic", "text-x-generic-symbolic"), tooltip=path,
                              quiet=True)
        page.show(path, text, line, note)
        self.tabs.remember()  # (shown at another line: a step back and forward go through)
        return True

    @staticmethod
    def tab_closed(_key, child):
        if isinstance(child, fixview.FixesPage):
            discard(child)  # (its images, and all that holds it)

    def image_source(self):
        """What the large image view goes through with ← and →: the Feedback tab's images, or the chat's."""
        page = self.tabs.current()
        return page if isinstance(page, fixview.FixesPage) else self.transcript

    def open_fixes(self, select=True):
        """☰ → Ongoing Feedback: its tab, opened or shown (fixview.FixesPage)."""
        return self.tabs.open(("fixes",), lambda: fixview.FixesPage(self), wording.say("feedback.title"),
                              icon="mail-send-symbolic", tooltip=wording.say("window.feedback_tab_tip"),
                              select=select)

    def reopen_tab(self, key, line):
        """What was shown, gone back or forward to (MainTabs.back, forward): the chat, the feedback, or a file's code
        at its line (opened again, if it's still there). False if it can't be."""
        if key == "chat":
            return self.tabs.show("chat")
        if key[0] == "fixes":
            return bool(self.open_fixes())
        if key[0] == "code":
            if key[2]:  # (a version's text: only while its tab's open)
                return self.tabs.show(key)
            return os.path.exists(key[1]) and self.show_code(key[1], line)
        return False

    def go_up(self):
        """Up (⌥⌘↑): from a file's code, its folder on the Files page, the file selected. False from the chat."""
        page = self.tabs.current()
        if isinstance(page, CodePage) and page.path and os.path.exists(page.path):
            return self.show_folder(os.path.dirname(page.path), page.path)
        return False

    def show_folder(self, path, select=None):
        """A folder (or a file in it: select) on the side panel's Files page, its tree opened down to it."""
        self.panel.files_page.reveal(path, select)
        self.show_panel(True, "files")
        return True

    def show_schema_for(self, ref, anchor=None):
        """A table or column name: the database diagram, with it selected."""
        defs = [l for l in ref.locations if l["role"] in ("table", "column")]
        name = re.sub(r"\([^()]*\)$", "", ref.surface).split(".")[-1]
        first = defs[0] if defs else None
        code = (os.path.join(self.signal.root, first["path"]), first.get("line")) if first else None
        if first and first["role"] == "column":  # (its table, from the code: preview is in more than one)
            table = ref_column_table(code[0], first.get("line")) or \
                (ref.surface.split(".")[-2] if ref.surface.count(".") == 1 else None)
            return self.open_db_name(table, name, first["path"], code, anchor, ref.surface)
        return self.open_db_name(name, None, first["path"] if first else "", code, anchor, ref.surface)

    def lookup_popover(self, anchor, name, kind=None):
        pop = LookupPopover(name, kind)
        pop.attach(anchor, self.pointer_rect(anchor))
        pop.popup()
        return pop

    def github_folders(self):
        """What GitHub's lists are read for (lookupgit.GitHub.look): the repository this chat's folder is in."""
        return [self.signal.root if self.signal is not None else self.ref_checkout or self.cwd]

    @staticmethod
    def _github_changed(win):
        """GitHub's lists, or where its pull requests stand (lookupgit.PULLS), have changed: the replies naming one
        drawn again (their pills: markdownview.pr_pill), the Git page's list and pills too."""
        if win.signal is not None:
            win.signal.github_changed()
        was = markdownview.SIGNAL, MarkdownView.base_dir  # (drawn with this window's links, as it would itself: then
        win.use_links()                                # the window that was being drawn's again)
        MarkdownView.base_dir = win.run_cwd
        try:
            for view in win.assistant_views():
                if "/pull/" in view.text or re.search(r"#\d", view.text):
                    view.relink()
        finally:
            markdownview.SIGNAL, MarkdownView.base_dir = was
        win.panel.git_page.pulls_changed()

    def catalogue_signal(self):
        """The Signal whose names the glossary's search lists: this chat's, or, with Signal off for it, the folder's
        own switched off (made so if there's none yet): its catalogue is wanted, not its lookups. None without a
        folder."""
        if self.signal is not None or not self.cwd:
            return self.signal
        folder, home = (self.ref_checkout, self.cwd) if self.ref_checkout else (self.cwd, None)
        return Signal.for_folder(folder, home=home, halted=True)

    def open_db_name(self, table, column, hint, code, anchor=None, title=None):
        """A table or column: find the running database that has it first, and open the schema diagram there,
        with it selected. Several databases have it (and the code's path names none): pick one. None has it: say
        so, with its definition and Try again, rather than open a database it isn't in. The popover only shows
        if finding it takes a moment, or there's something to say."""
        name = title or (f"{table}.{column}" if table and column else table or column or "")
        kind = "Database column" if column else wording.say("databases.database_table")
        state = {"pop": None, "done": False, "run": 0}

        def popover():
            if state["pop"] is None and anchor is not None and anchor.get_root() is not None:
                state["pop"] = self.lookup_popover(anchor, name, kind)
            return state["pop"]

        def show_busy():
            if not state["done"] and (pop := popover()) is not None:
                pop.state("Finding it in the running databases…", busy=True)
            return False

        def definition():
            if code:
                open_code(code[0], code[1], self)

        def open_at(hit):
            self.schema.open(table=hit[3], column=column, hint=hint, code=code, found=hit)

        def done(run, res):
            if run != state["run"]:
                return False
            state["done"] = True
            pop = state["pop"]
            if pop is not None and pop.is_closed:
                return False  # (dismissed while it looked: nothing opens)
            if res["best"]:
                if pop is not None:
                    pop.popdown()
                open_at(res["best"])
                return False
            pop = popover()
            if pop is None:  # (no reply to point from: say it in a toast)
                toast(self, res["error"] or f"{len(res['hits'])} databases have {name}")
                return False
            if res["hits"]:
                n = len({(h[0]["container"], h[1]) for h in res["hits"]})
                pop.state(f"{n} running databases have it. Which one?",
                          choices=[(f"{h[0]['container']} › {h[1]} › {h[2]}.{h[3]}", None, lambda h=h: open_at(h))
                                   for h in res["hits"][:10]],
                          actions=[("Definition", lambda: (pop.popdown(), definition()))] if code else [])
            else:
                acts = [(wording.say("common.try_again"), lambda: start(again=True))]
                if code:
                    acts.append(("Open the definition", lambda: (pop.popdown(), definition())))
                pop.state(res["error"], actions=acts)
            return False

        def start(again=False):
            state["run"] += 1
            state["done"], run = False, state["run"]
            if again:
                pg_forget()
                show_busy()
            DB_POOL.submit(lambda: GLib.idle_add(done, run, db_locate(table, column, hint)))
            if not again:
                GLib.timeout_add(250, show_busy)
        start()
        return state

    def open_commit_first(self, surface, anchor, message=None):
        """A name shaped like a commit hash that Signal hasn't looked up yet (it waits behind every other name in the
        chat): git is asked first, at once (rev-parse, a few ms). A commit it has opens in the Git page; otherwise as any
        name not looked up yet."""
        sig = self.signal
        root = sig.root

        def work():
            sha, folder = "", None
            for top in (root, *sig.chat_repos()):  # (this repository, then the others the chat worked in)
                if sha := (_git(top, "rev-parse", "--verify", "--quiet", f"{surface}^{{commit}}") or "").strip():
                    folder = None if top == root else top
                    break
            GLib.idle_add(lambda: (self.show_git(sha=sha, folder=folder) if sha else
                                   sig.refs.get(surface) is not None and
                                   self.lookup_pending(surface, anchor, message)) and False)
        GIT_POOL.submit(work)

    def lookup_pending(self, surface, anchor, message=None):
        """A name Signal hasn't looked up yet: "Looking it up…" (it may be anything: no kind is claimed), then
        Found, with Open, or Not found, with Try again."""
        sig = self.signal
        pop = self.lookup_popover(anchor, surface)

        def show(ref):
            if ref is None or ref.status == "pending":
                pop.set_title()
                pop.state(wording.say("lookup.looking_it_up"), busy=True)
            elif ref.status == "not_found":
                pop.set_title(ref_kind_label(ref))
                again = lambda: (sig.refresh([surface]), show(sig.refs.get(surface)))  # noqa: E731
                pop.state(wording.say("signal.status_not_found", reason=ref.reason),
                          actions=[(wording.say("common.try_again"), again)])
            else:
                pop.set_title(ref_kind_label(ref))
                pop.state(wording.say("signal.found", status=ref_status_text(ref)),
                          actions=[(wording.say("common.open"),
                                    lambda: (pop.popdown(), self.open_ref(surface, anchor, message)))])
                ref_status_tip(pop.status, ref)

        def changed(surfaces):
            if pop.is_closed:
                if changed in sig.listeners:
                    sig.listeners.remove(changed)
            elif surface in surfaces:
                show(sig.refs.get(surface))
        sig.listeners.append(changed)
        pop.connect("closed", lambda *_: changed in sig.listeners and sig.listeners.remove(changed))
        show(sig.refs.get(surface))
        return pop

    def open_as_it_was(self, ref, index):
        """Open the version of a location's file that was read when it was looked up (read-only)."""
        loc = ref.locations[index]
        text = self.signal.old_text(loc.get("blob"))
        if text is None:
            toast(self, "That version of the file wasn't kept")
            return
        if load_settings().get("code_viewer", True):
            when = " ".join(x for x in (loc.get("state"), (loc.get("commit_sha") or "")[:9] and
                                        f"at {(loc.get('commit_sha') or '')[:9]}", loc.get("branch") and
                                        f"on {loc['branch']}") if x)
            self.show_code(os.path.join(self.signal.root, loc["path"]), loc.get("line"), text=text,
                           note=f"As it was when looked up ({when}): not the file as it is now.")
            return
        path = os.path.join(self.signal.dir, "as-it-was", f"{loc['blob'][:12]}-{os.path.basename(loc['path'])}")
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(path, 0o444)
        open_in_editor(path, loc.get("line"), self)

    # ---- background tasks rebuilt from a transcript (a viewed / tailed / resumed session has no
    # task events: those only exist on the live stream of the claude that runs it)
    def task_from_result(self, card, content):
        texts = content if isinstance(content, list) else [content]
        text = "\n".join(c.get("text", "") if isinstance(c, dict) else str(c or "") for c in texts)
        info = None
        if m := re.search(r"Command running in background with ID: (\S+?)\.?\s+Output is being written to: (\S+)",
                          text):
            info = {"task_id": m.group(1), "task_type": "local_bash", "output_file": m.group(2).rstrip("."),
                    "description": card.input.get("description") or card.input.get("command")}
        elif "Async agent launched" in text and (m := re.search(r"agentId: (\w+)", text)):
            info = {"task_id": m.group(1), "task_type": "local_agent", "description": card.input.get("description"),
                    "subagent_type": card.input.get("subagent_type"), "prompt": card.input.get("prompt")}
        if info is None or info["task_id"] in self.tasks:
            return
        info.update({"tool_use_id": card.tool_id, "is_backgrounded": True})
        self.on_task_event("task_started", info)
        self.tasks[info["task_id"]]["from_file"] = True
        if info["task_type"] == "local_agent":
            self.follow_subagent(info["task_id"], card.tool_id, opened=False)

    def task_from_note(self, note):
        tid = note.get("task_id")
        if not tid:
            return
        task = self.tasks.get(tid)
        if task is None:  # a task whose start isn't in what's shown (older history, a Monitor)
            card = self.tool_cards.get(note.get("tool_use_id"))
            kind = "local_agent" if card is not None and card.base in ("Agent", "Task") else "local_bash"
            self.on_task_event("task_started", {"task_id": tid, "tool_use_id": note.get("tool_use_id"),
                                                "task_type": kind, "description": note.get("summary")})
            task = self.tasks[tid]
            task["from_file"] = True
        task["noted"] = True  # (its line, if it gets one, is drawn from the transcript's own note: render_history)
        self.on_task_event("task_notification", {
            "task_id": tid, "tool_use_id": note.get("tool_use_id") or task.get("tool_use_id"),
            # the card shows what the task produced: a subagent's report, else the one-line summary
            "status": note.get("status") or "completed", "summary": note.get("result") or note.get("summary"),
            "output_file": note.get("output_file") or task.get("output_file")})

    def subagent_path(self, agent_id):
        base = getattr(self, "history_path", None)
        return os.path.join(base[:-len(".jsonl")], "subagents", f"agent-{agent_id}.jsonl") \
            if base and base.endswith(".jsonl") else None

    def follow_subagent(self, agent_id, tool_use_id, opened=True):
        """Draw a subagent's own transcript in its side-panel view, and keep reading it; one in the history drawn
        (not opened), from when its view's first opened (subagent_opened): a long chat has many, each as long as a
        chat, and scrolling up through the history comes to them all."""
        path = self.subagent_path(agent_id)
        if not path:
            return
        self.sub_tails[agent_id] = {"path": path, "offset": 0, "partial": b"", "tool_use_id": tool_use_id,
                                    "first": True, "opened": opened}
        if not opened:
            self.subagent_artifacts(path)
        self.read_subagent(agent_id)

    def subagent_opened(self, tool_use_id):
        """Its view opened in the side panel (or a message of its come live): its transcript drawn there now, if it
        waited for that; its calls that never got a result interrupted, as the chat's are (render_history), unless
        it's still running."""
        for agent_id, st in list(self.sub_tails.items()):
            if st["tool_use_id"] == tool_use_id and not st["opened"]:
                st["opened"] = True
                before = set(self.tool_cards)
                self.read_subagent(agent_id)
                task = self.tasks.get(agent_id) or {}
                if getattr(self, "history_final", False) or task.get("status") != "running":
                    for tid, card in self.tool_cards.items():
                        if tid not in before and card.state == "running" and tid not in self.orphan_results:
                            card.set_state("interrupted")

    def subagent_artifacts(self, path):
        """What a subagent not drawn yet (follow_subagent) published, listed in the side panel's Artifacts and named as
        its links read, without its transcript drawn: read only if it has an Artifact call in it at all."""
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return
        if b"Artifact" not in data:
            return
        calls = {}
        for _kind, content, *_ in history_items(data.decode("utf-8", "replace").splitlines(), sidechain=True):
            for c in content if isinstance(content, list) else ():
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use" and str(c.get("name", "")).split("__")[-1] == "Artifact":
                    calls[c.get("id")] = c.get("input") or {}
                elif c.get("type") == "tool_result" and c.get("tool_use_id") in calls:
                    res = c.get("content")
                    text = "\n".join(x.get("text", "") if isinstance(x, dict) else str(x or "")
                                     for x in (res if isinstance(res, list) else [res]))
                    if (info := artifact_from_tool(calls[c["tool_use_id"]], text)) is not None:
                        ARTIFACT_TITLES[info["url"]] = info["title"]
                        self.register_artifact(info)

    def read_subagent(self, agent_id):
        st = self.sub_tails.get(agent_id)
        if st is None or not st["opened"] or not os.path.exists(st["path"]):
            return
        lines, st["offset"], st["partial"] = read_appended(st["path"], st["offset"], st["partial"])
        for kind, content, *rest in history_items(lines, sidechain=True):
            if kind == "assistant":
                self.render_assistant(content, parent=st["tool_use_id"],
                                      written_at=ref_epoch(rest[1]) if len(rest) > 1 else None)
            elif kind == "tool_results":
                self.render_tool_results(content)
            elif kind == "user" and st["first"]:
                st["first"] = False  # its prompt: the panel shows that already

    def read_subagents(self):
        for agent_id in list(self.sub_tails):
            self.read_subagent(agent_id)

    def h_user(self, msg):
        content = msg.get("message", {}).get("content")
        if self.on_compaction(msg):
            return  # (what it compacted the conversation to)
        if isinstance(content, list):  # a plain string is the echo of our own prompt
            self.render_tool_results(content)
            if not msg.get("parent_tool_use_id"):
                self.refresh_context_soon()

    # ---- history
    # items drawn when a session opens, and per earlier chunk (loaded as you scroll near the top, or by
    # "Show earlier messages"): only the last screen is in view, and each item costs layout time
    HISTORY_CHUNK = 60
    PLACES = {}  # session id -> where its chat was left (chat_place): every window's, kept over a restart
    MAX_PLACES = 500

    def chat_place(self):
        """Where the chat is scrolled to, for opening it there again (open_at): measured from its end (what's added at
        its top as you scroll up doesn't move it), and the earlier chunk at the top of the view, if it's in one
        (view_chunk). None if it was never laid out."""
        adj = self.scroller.get_vadjustment()
        if adj.get_page_size() <= 0:
            return None
        from_bottom = max(0.0, adj.get_upper() - adj.get_page_size() - adj.get_value())
        at_end = self.stick_bottom or from_bottom <= 1
        return {"from_bottom": from_bottom, "at_end": at_end, "chunk": None if at_end else self.view_chunk()}

    def view_chunk(self):
        """The earlier chunk at the top of the view, as [where it starts (its first item's line sum), how far into it
        the view's top is]; None if that's in the newest part (drawn on opening anyway)."""
        hist = getattr(self, "history_box", None)
        top, _page = self.view_top()
        slot = hist.get_first_child() if hist is not None and top is not None else None
        while slot is not None:
            ok, b = slot.compute_bounds(self.transcript)
            if ok and b.get_y() <= top < b.get_y() + b.get_height() and slot.first_sum is not None:
                return [slot.first_sum, top - b.get_y()]
            slot = slot.get_next_sibling()
        return None

    def place_chunk(self, chunk):
        """Open at an earlier chunk (view_chunk's): that one drawn now, the view put back in it once it's laid out
        (on_layout), and held there as what's around it is drawn. Only that one: what was drawn ahead of the reader isn't
        drawn again at once. False if it isn't there to draw (the transcript was rewritten)."""
        first_sum, offset = chunk
        self.history.finish()
        self.make_chunk_slots()
        slots = self.chunk_slots or []
        slot = next((s for s in slots if s.first_sum == first_sum), None)
        if slot is None:  # (the transcript grew since: its chunks begin elsewhere now; the one with that item in it)
            sums = self.history.sums
            at = next((i for i in range(len(sums)) if sums[i] == first_sum), None)
            slot = next((s for s in slots if at is not None and s.span[0] <= at < s.span[1]), None)
        if slot is None:
            return False
        self.fill_now(slot)
        self.place_at = (slot, float(offset))
        return True

    def put_place(self):
        """After the layout pass a chunk opened at (place_chunk) is in: the view's top where it was in it."""
        slot, offset = self.place_at
        ok, b = slot.compute_bounds(self.transcript)
        top, _page = self.view_top()
        if not ok or top is None or b.get_height() <= 0:
            return False
        self.place_at = None
        self.hold_value(self.scroller.get_vadjustment().get_value() + b.get_y() + offset - top)
        return True

    def remember_place(self):
        """The open chat's place remembered (chat_place), as it's left for another."""
        if self.session_id and (place := self.chat_place()) is not None:
            self.PLACES.pop(self.session_id, None)
            self.PLACES[self.session_id] = place
            while len(self.PLACES) > self.MAX_PLACES:  # (the longest unopened go)
                del self.PLACES[next(iter(self.PLACES))]

    def open_at(self, place):
        """A chat just drawn shown where it was left (place: chat_place), the earlier chunk it was in drawn back
        (place_chunk); never left before, at its end. There at once and held there while it lays out (on_layout: images
        and code come in sizes later), so it doesn't glide down to its end or move under you."""
        if place and not place.get("at_end"):
            self.stick_bottom = False
            if place.get("chunk") and self.place_chunk(place["chunk"]):
                return
            from_bottom = float(place.get("from_bottom") or 0)
        else:
            self.stick_bottom, from_bottom = True, 0.0
        self.restore_scroll = (from_bottom, time.monotonic() + 2.0)

    def load_history(self, path, live=False):
        """Draw the newest part of a saved session; older parts load on demand from the top."""
        self.history = HistoryIndex(path)
        items = self.history.load(self.HISTORY_CHUNK)
        self.history_path, self.history_final = path, not live  # (finished: no task in it is running: settle_tasks)
        self.earlier_btn = Gtk.Button(halign=Gtk.Align.CENTER, visible=False)
        self.earlier_btn.add_css_class("pill")
        self.earlier_btn.connect("clicked", lambda *_: self.show_earlier())
        self.add(self.earlier_btn)
        self.history_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)  # (shown once one's drawn)
        self.history_box.is_history, self.chunk_slots = True, None
        self.add(self.history_box)
        self.drop_fills()
        self.make_chunk_slots()
        self.render_history(items, final=not live)
        if self.history.todo:  # (the start of a long one: indexed in idle time, then counted and read by Signal)
            GLib.idle_add(self.index_history, self.history, priority=GLib.PRIORITY_LOW)
        else:
            self.queue_history_refs(self.history.earlier_replies())
        self.update_earlier_btn()
        self.resumed_note = bool(items)  # "— resumed —" goes in when you first write, not on opening
        self.open_at(self.PLACES.get(self.session_id))
        held = self.fill_held = object()  # (till then nothing's drawn ahead: the opening frames are the chat's own)

        def release():
            if self.fill_held is held:
                self.fill_held = None
                self.plan_fill()
            return False
        GLib.timeout_add(self.FILL_AFTER_MS, release)

    def update_earlier_btn(self):
        n = self.history.count() if self.history else 0
        self.earlier_btn.set_label("Show earlier messages" if n is None else f"Show earlier messages ({n} more)")
        top = self.history_box.get_first_child() if self.history else None
        # (not once the chat's start is drawn, out of turn: nothing's earlier than that)
        self.earlier_btn.set_visible(bool(self.history and self.history.more) and not getattr(top, "filled", False))

    # ---- the earlier chunks of a long chat: each has its place from the start (make_chunk_slots), as tall as its items
    # are likely to be (estimate_slots), so the chat's about its full height at once and the scrollbar's thumb doesn't
    # shrink and jump as they're drawn; each is drawn a little a frame (fill_frame) as the view comes near it: the one
    # under the view first, the next ahead of the reader while the chat's still. One far from the view isn't drawn
    # till the view comes near it, or it's asked for (Show earlier, a place put back, a find's match in it).
    PX_PER_ITEM = 100  # (an item's height, till some are drawn: about the average of long chats)
    FILL_NEAR = 3.0  # screens: a chunk this near the view is drawn even while it's scrolled
    FILL_AHEAD = 20.0  # screens: and one this near while it's still (a hard fling's length: it meets what's drawn)
    FILL_BUDGET, FILL_BUDGET_STILL = 0.003, 0.008  # s of making items a frame: while the chat's scrolled, still
    FILL_AFTER_MS = 600  # (after opening: an earlier chunk near the view is drawn from then on)

    def make_chunk_slots(self):
        """The earlier chunks' places, made once it's known how many: a chunk put in above what's drawn moved everything
        below it, and GTK styled all of that again (a quarter of a second a chunk, deep into a long chat). Each knows
        which items it's for (span: as HistoryIndex.take numbers them), and has a spacer at its top that takes up what
        its estimated height has over what's drawn in it, so what's drawn sits at its bottom, next to what's below."""
        if self.chunk_slots is not None or (n := self.history.count()) is None:
            return
        slots = []
        for i in range(-(-n // self.HISTORY_CHUNK)):
            end = n - i * self.HISTORY_CHUNK
            slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, vexpand=False)  # (its spacer's expanding
            slot.span, slot.filled = (max(0, end - self.HISTORY_CHUNK), end), False           # goes no further)
            slot.first_sum = self.history.sums[slot.span[0]] if slot.span[0] < end else None
            slot.spacer = Gtk.Box(vexpand=True)
            slot.append(slot.spacer)
            slot.set_margin_bottom(10 if i else 0)  # (the chunk below it; the lowest has the chat's own spacing)
            slots.append(slot)
        self.chunk_slots = slots  # (the ones not drawn yet: the lowest, next to what's drawn on opening, first)
        for slot in slots:
            self.history_box.prepend(slot)  # (above what's drawn: the newest's, or a rewritten transcript's chunks)
        self.history_box.set_visible(self.history_box.get_first_child() is not None)
        self.estimate_slots()
        self.estimate_soon()  # (again once what's drawn is laid out)

    def px_per_item(self):
        """How tall an item of this chat's is, on average: what's drawn (the newest, the chunks drawn) over how many."""
        height, items = 0.0, 0
        hist = self.history_box
        child = hist.get_first_child()
        while child is not None:
            if child.filled:
                height += child.get_height() + child.get_margin_bottom()
                items += child.span[1] - child.span[0]
            child = child.get_next_sibling()
        ok, hb = hist.compute_bounds(self.transcript)
        newest = self.history.shown - self.history.taken_count
        if ok and newest > 0 and self.transcript.get_height() > 0:
            height += self.transcript.get_height() - hb.get_y() - hb.get_height()
            items += newest
        return height / items if items > 0 and height > 0 else self.PX_PER_ITEM

    def estimate_slots(self):
        """Each undrawn chunk's place as tall as its items are likely to be (px_per_item): set again only when that's
        changed by a tenth, since each change moves the thumb (the view's held: hold_view_anchor)."""
        if not self.chunk_slots and not self.fill_jobs:
            return
        per = self.px_per_item()
        if abs(per - self.estimated_per) < per / 10:
            return
        self.estimated_per, self.above_changed = per, True
        for slot in (self.chunk_slots or []) + [job.slot for job in self.fill_jobs]:
            slot.set_size_request(-1, round(per * (slot.span[1] - slot.span[0])))

    def estimate_soon(self):
        """estimate_slots once what's drawn now has been laid out: as a frame begins (in GTK's update, before its layout,
        so the view's hold sees the change in the same frame: sized after a layout pass, it took more passes than GTK
        runs in a frame, and a frame was drawn with the view thousands of px off)."""
        if self.estimate_tick is None:
            self.estimate_tick = [0, self.scroller.add_tick_callback(self._estimate_frame)]

    def _estimate_frame(self, _widget, _clock):
        self.estimate_tick[0] += 1
        if self.estimate_tick[0] < 2:  # (the frame after next: what was added is laid out by then)
            return GLib.SOURCE_CONTINUE
        self.estimate_tick = None
        if self.zoom_job is None and self.history is not None:
            self.estimate_slots()
        return GLib.SOURCE_REMOVE

    def index_history(self, history):
        """A slice of indexing the start of a long chat (HistoryIndex.index_some), between frames."""
        if history is not self.history:
            return False  # (another chat now)
        if history.index_some():
            return True
        self.make_chunk_slots()
        self.update_earlier_btn()
        self.queue_history_refs(history.earlier_replies())
        self.plan_fill()
        return False

    def show_earlier(self):
        """Draw the topmost chunk not drawn, at once (the Show earlier button, at the chat's top: what's under it),
        keeping the view where it was."""
        if not (self.history and self.history.more):
            return
        self.history.finish()  # (scrolled up before the start of a long chat was indexed: now)
        self.make_chunk_slots()
        slot = self.history_box.get_first_child()  # (the one under the button: the chat's start, its first not drawn)
        while slot is not None and slot.filled:
            slot = slot.get_next_sibling()
        if slot is None:  # (nothing in it to draw after all)
            self.update_earlier_btn()
            return
        adj = self.scroller.get_vadjustment()
        before = adj.get_upper()
        self.fill_now(slot)
        self.stick_bottom = False

        # once the chunk is laid out, shift the view by what was added above it (only once: later growth, like a reply
        # streaming in at the bottom, must not move it), right after the layout pass it's in (on_layout), so the frame
        # is drawn with it: at notify::upper the frame was drawn first, the view a chunk's height off (6000 px of
        # screenshots), and set back from where it was, not where it's been scrolled to since
        self.cancel_keep_place()
        # if the layout settles without a size change, don't leave it armed: fired much later (by
        # a zoom, a new message) it would throw the view off by the old chunk's height
        expire = GLib.timeout_add(1500, lambda: (self.cancel_keep_place(), False)[1])
        self.keep_place = (adj, before, expire)

    def fill_now(self, slot):
        """Draw a chunk into its place at once (show_earlier, place_chunk), the part of it made already too."""
        job = next((job for job in self.fill_jobs if job.slot is slot), None) or self.start_fill(slot)
        self.fill_jobs.remove(job)
        while job.items:
            self.fill_item(job)
        while job.hidden:
            job.hidden.pop().set_visible(True)
        self.fill_done(job)

    def start_fill(self, slot):
        """Begin drawing a chunk into its place (its items read again as they're made: HistoryIndex.take), first."""
        self.chunk_slots.remove(slot)
        job = ChunkFill(slot, self.history.take(*slot.span))
        slot.sums = {line_sum for _start, line_sum in job.items}  # (what it draws: not drawn again after a rewrite)
        self.fill_jobs.appendleft(job)
        return job

    def find_undrawn(self):
        """Whether the chat has earlier parts not drawn (ChatFind.undrawn: its count says there may be more)."""
        return bool(self.history is not None and (self.chunk_slots or self.fill_jobs or self.history.todo))

    _FIND_MARKUP = re.compile(r"[*_`~]+|^[ \t]*(?:#+|>+|[-+]|\d+\.)[ \t]+", re.MULTILINE)

    @classmethod
    def find_plain(cls, text):
        """text as find compares what's not drawn: Markdown's marks out (a drawn reply's labels have none: **8.6 m**
        is found as 8.6 m), spaces and line ends one space, case folded. What's looked for goes through it too."""
        return " ".join(cls._FIND_MARKUP.sub("", text).split()).casefold()

    def item_words(self, item):
        """What an item of the transcript shows as text once drawn, as find_plain has it: a message's words (what its
        sender store says is shown of it, if it says: sender_turn), a reply's, and each call's line on its card
        (summarize_tool: what ChatFind finds there). Not what's folded: a card's input and output, thinking, a
        summary."""
        kind, content = item[0], item[1] if len(item) > 1 else None
        shown = (self.chat_senders().get(item[4]) or {}).get("shown") if kind == "user" and len(item) > 4 else None
        if isinstance(shown, str):
            said = [shown]
        elif kind in ("user", "start_prompt"):
            said = [clean_prompt(t) for t in _prompt_texts(content)]
        elif kind == "assistant":
            said = [content] if isinstance(content, str) else [
                c.get("text") or "" if c.get("type") == "text" else summarize_tool(c.get("name") or "", c.get("input") or {})
                for c in content or () if isinstance(c, dict) and c.get("type") in ("text", "tool_use", "server_tool_use")]
        else:
            said = []
        return self.find_plain("\n".join(said))

    FIND_SLICE = 0.008  # s of looking through the transcript at a time, between frames (find_beyond)

    def find_beyond(self, text, by, y0, y1, done):
        """ChatFind.beyond: the nearest earlier chunk not drawn whose items have text (item_words) drawn at once,
        looking by (1 down, -1 up) from height y0 in the chat as far as y1 (None: on round its other end, back to
        y0; y0 None: outward from the view); done(whether one was drawn) once it's known. The transcript's looked
        through FIND_SLICE at a time between frames (a long one is hundreds of MB), each line parsed only if it has
        the text's longest word as it's written (HistoryIndex.holds). A chunk looked in is remembered while the
        text's the same (looked_in): Enter pressed again reads none twice. Returns what stops the look."""
        scan = {"on": True}
        needle = self.find_plain(text)
        if self.history is None or not needle:
            done(False)
            return lambda: None
        history = self.history
        history.finish()
        self.make_chunk_slots()
        if self.looked_in[0] != (history, needle):
            self.looked_in = ((history, needle), set())
        looked, places = self.looked_in[1], []
        content = chatfind.ChatFind._content(self.scroller)  # (heights as find has them: in what's scrolled)
        for slot in list(self.chunk_slots or ()) + [job.slot for job in self.fill_jobs]:
            ok, b = slot.compute_bounds(content)
            if ok and slot not in looked:
                places.append((b.get_y(), b.get_y() + b.get_height(), slot))
        if y0 is None:
            top, page = self.view_top()
            order = sorted(places, key=lambda p: self.slot_away(p[2], top or 0, page))
        elif by < 0:
            above = sorted((p for p in places if p[0] < y0), key=lambda p: -p[0])
            order = [p for p in above if p[1] > y1] if y1 is not None else \
                above + sorted((p for p in places if p[0] >= y0), key=lambda p: -p[0])
        else:
            below = sorted((p for p in places if p[1] > y0), key=lambda p: p[0])
            order = [p for p in below if p[0] < y1] if y1 is not None else \
                below + sorted((p for p in places if p[1] <= y0), key=lambda p: p[0])
        order = collections.deque(slot for _top, _bottom, slot in order)
        # (a line with the text has its longest run of letters and digits as it's written: in no escape, under no mark)
        word = max(re.findall(r"[a-z0-9]+", needle), key=len, default="").encode()

        def some():
            if not scan["on"] or self.history is not history:
                return False
            end = time.monotonic() + self.FIND_SLICE
            while order and time.monotonic() < end:
                slot = order.popleft()
                begun = any(job.slot is slot for job in self.fill_jobs)
                if slot.filled or not (begun or slot in (self.chunk_slots or ())):
                    continue  # (drawn meanwhile, or gone: the transcript rewritten)
                # (one begun, part of it drawn, is looked in as the rest are; one rewritten since it was indexed is
                # passed by)
                if history.holds(*slot.span, word, lambda item: needle in self.item_words(item)):
                    self.stick_bottom = False
                    self.fill_now(slot)
                    done(True)
                    return False
                looked.add(slot)
            if order:
                return True
            done(False)
            return False
        GLib.idle_add(some)
        return lambda: scan.update(on=False)

    def history_rewritten(self):
        """The transcript was rewritten since it opened: what's left to draw indexed again, with places of its own above
        what's drawn (the places not drawn yet go; what's made of one part drawn stays)."""
        for slot in self.chunk_slots:
            self.history_box.remove(slot)
        jobs = list(self.fill_jobs)
        self.fill_jobs.clear()
        for job in jobs:
            while job.hidden:
                job.hidden.pop().set_visible(True)
            job.broken = False
            self.fill_done(job)
        drawn = set()
        child = self.history_box.get_first_child()
        while child is not None:
            drawn |= getattr(child, "sums", set())
            child = child.get_next_sibling()
        self.history.reindex(drawn)
        self.chunk_slots, self.estimated_per = None, 0
        self.make_chunk_slots()
        self.update_earlier_btn()

    def fill_item(self, job):
        """Make the next item of a chunk being drawn, hidden (hidden, GTK neither styles nor lays it out)."""
        item = self.history.item_at(job.items.popleft())
        if item is None:  # (not the line indexed: the rest of the chunk isn't either)
            job.broken = True
            job.items.clear()
        before = set(self.tool_cards)
        self.render_target, self.render_job = job.slot, job
        try:
            self.render_history([item] if item is not None else [], final=False)
            if not job.items and (mark := self.last_compact_mark()) is not None:
                self.adopt_summary(mark)  # (the chunk's drawn: a summary after what a compaction kept, at its end)
        finally:
            self.render_target = self.render_job = None
        job.calls.update(tid for tid in self.tool_cards if tid not in before)
        if not job.items:  # (its calls that never got a result were interrupted; a later chunk's result puts it right)
            for tid in job.calls:
                card = self.tool_cards.get(tid)
                if card is not None and card.state == "running" and tid not in self.orphan_results:
                    card.set_state("interrupted")
            if getattr(self, "history_final", False):  # (and its background tasks that never ended, as when it opened)
                self.settle_tasks()
                self.show_cut(self.cut_state())
        for w in [w for w in job.hidden if w.get_parent() is not job.slot]:  # (a call gone into a run's group: the
            w.set_visible(True)                                               # group's hidden instead)
            job.hidden.remove(w)
        made, child = [], job.slot.get_last_child()
        while child is not None and child.get_visible() and child is not job.slot.spacer:
            child.set_visible(False)
            made.append(child)
            child = child.get_prev_sibling()
        job.hidden.extend(reversed(made))

    def fill_done(self, job):
        """A chunk's all drawn: its place as tall as it is now."""
        slot = job.slot
        slot.set_size_request(-1, -1)
        if slot.spacer is not None:
            slot.remove(slot.spacer)
            slot.spacer = None
        slot.filled, self.above_changed = True, True
        self.estimate_soon()
        self.update_earlier_btn()
        if job.broken:
            self.history_rewritten()

    def drop_fills(self):
        """Another chat: nothing of the one before is drawn on."""
        self.fill_jobs.clear()
        if self.fill_tick is not None:
            self.scroller.remove_tick_callback(self.fill_tick)
            self.fill_tick = None
        if self.estimate_tick is not None:
            self.scroller.remove_tick_callback(self.estimate_tick[1])
            self.estimate_tick = None
        self.estimated_per, self.cull_at, self.place_at = 0, None, None

    def plan_fill(self):
        """Draw on, a frame at a time, if there's a chunk to draw (fill_frame picks which). Not while a chat just
        opened is held (fill_held: its first FILL_AFTER_MS are its own frames')."""
        if self.fill_tick is None and self.history is not None and (self.chunk_slots or self.fill_jobs) and \
                self.fill_held is None:
            self.fill_tick = self.scroller.add_tick_callback(self.fill_frame)

    def slot_away(self, slot, top, page):
        """How far a chunk's place is from the view, in px (0: in it)."""
        ok, b = slot.compute_bounds(self.transcript)
        return max(0.0, top - (b.get_y() + b.get_height()), b.get_y() - (top + page)) if ok else float("inf")

    def next_fill(self, moving):
        """The chunk to draw on now: the one nearest the view within FILL_NEAR screens (a thumb dragged into a part not
        drawn yet: what's under the view first), or, while the chat's still, within FILL_AHEAD (ahead of the
        reader); None for none now. The rest stay places in the file."""
        top, page = self.view_top()
        if top is None:
            return None
        # (a thumb dragged fast, half a screen a frame: what it passes by isn't drawn, nor read)
        self.fill_sweeping = moving and abs(top - getattr(self, "fill_top", top)) > page / 2
        self.fill_top = top
        if self.fill_sweeping:
            return None
        near = [(self.slot_away(job.slot, top, page), i, job) for i, job in enumerate(self.fill_jobs)]
        near += [(self.slot_away(slot, top, page), len(near) + i, slot) for i, slot in enumerate(self.chunk_slots or ())]
        still = not moving and not self.busy
        reach = self.FILL_AHEAD if still else self.FILL_NEAR
        near = [n for n in near if n[0] < reach * page]
        if near:
            what = min(near, key=lambda n: (n[0], n[1]))[2]
        elif still and self.fill_jobs:
            what = self.fill_jobs[0]  # (one begun as the view passed near it: not left half made)
        else:
            return None
        if isinstance(what, ChunkFill):
            self.fill_jobs.remove(what)
            self.fill_jobs.appendleft(what)
            return what
        return self.start_fill(what)

    def fill_frame(self, _widget, clock):
        """Each frame: draw on the chunk next_fill picks, making its items for FILL_BUDGET (FILL_BUDGET_STILL while the
        chat's still), then showing them from the bottom up: one a frame while it's scrolled; while it's still, twice as
        many each frame that keeps its time, half after one that doesn't. What's shown goes in above what's read, where
        the view's hold keeps it still."""
        if self.zoom_job is not None or self.history is None:  # (a zoom brings messages back a few a frame itself)
            self.fill_tick = None
            return GLib.SOURCE_REMOVE
        moving = self.chat_moving()
        if not moving and self.pane_moving():
            return GLib.SOURCE_CONTINUE
        job = self.next_fill(moving)
        if job is None:
            if self.fill_sweeping:
                return GLib.SOURCE_CONTINUE  # (till it slows down over a part to draw)
            self.fill_tick = None
            return GLib.SOURCE_REMOVE
        if job.items:
            end = time.perf_counter() + (self.FILL_BUDGET if moving else self.FILL_BUDGET_STILL)
            while job.items and time.perf_counter() < end:
                self.fill_item(job)
            return GLib.SOURCE_CONTINUE  # (shown from the next frame)
        frame = clock.get_frame_time()
        late, self.fill_frame_at = frame - getattr(self, "fill_frame_at", frame) > 20_000, frame
        self.fill_reveal = 1 if moving else max(1, self.fill_reveal // 2) if late else min(30, self.fill_reveal * 2)
        for _ in range(self.fill_reveal):
            if not job.hidden:
                break
            job.hidden.pop().set_visible(True)  # (the newest first: next to what's shown)
            self.above_changed = True
        if not job.hidden:
            self.fill_jobs.remove(job)
            self.fill_done(job)
        return GLib.SOURCE_CONTINUE

    def hold_keep_place(self, held):
        """After a layout pass: the view moved by what a chunk drawn above added, once it's laid out (show_earlier),
        unless the message at the view's top has held it already (held: hold_view_anchor's)."""
        adj, before, _expire = self.keep_place
        if adj.get_upper() != before:
            self.cancel_keep_place()
            if held is None:
                self.hold_value(adj.get_value() + adj.get_upper() - before)

    def cancel_keep_place(self):
        pending, self.keep_place = getattr(self, "keep_place", None), None
        if pending:
            _adj, _before, expire = pending
            GLib.source_remove(expire) if GLib.MainContext.default().find_source_by_id(expire) else None

    def last_compact_mark(self):
        """The marker of the last compaction drawn where history's being drawn (a chunk's slot, or the chat), if its
        summary isn't under it yet: the summary's record follows its boundary's, the messages a partial compaction kept
        between them at most; items are drawn one at a time (fill_item), so it's looked for among what's drawn: a
        chunk's slot, then the ones before it that are drawn and have no marker (chunks are drawn nearest the view
        first: the one with its boundary may be several up, drawn before the ones between)."""
        where = target = self.render_target or self.transcript
        while where is not None:
            child = where.get_last_child()
            while child is not None and not isinstance(child, chatparts.CompactMark):
                child = child.get_prev_sibling()
            if child is not None:
                return child if child.fold is None and child.state == "done" else None
            if target is self.transcript or (where is not target and not where.filled):
                return None  # (the chat itself; or a chunk not drawn yet: its marker, if it has one, adopts as it's drawn)
            where = where.get_prev_sibling()
        return None

    COMPACT_LOOK = 200  # (how far past a marker its summary's looked for: what a partial compaction kept between)

    def adopt_summary(self, mark):
        """A compaction's marker drawn from history after its summary was (history's drawn newest first, a chunk at a
        time: the summary below a chunk's edge, or past what a partial compaction kept): that summary, drawn on its
        own, folded under the marker instead. Looked for after it, in what's drawn in order from it: the rest of its
        chunk once that's all drawn, the next chunk's, and after the lowest, what was drawn on opening; up to the
        next marker."""
        where, node, seen = mark.get_parent(), mark.get_next_sibling(), 0
        while seen < self.COMPACT_LOOK:
            if node is None:
                job = getattr(self, "render_job", None)
                whole = getattr(where, "filled", False) or job is not None and job.slot is where and not job.items
                if getattr(where, "span", None) is None or not whole:
                    return  # (the end of the chat, or of what's drawn of a chunk: what's after comes later, in order)
                where = where.get_next_sibling()
                if where is None:  # (the lowest chunk: then what was drawn on opening)
                    where = self.history_box.get_parent()
                    node = self.history_box.get_next_sibling()
                else:
                    node = where.get_first_child()
                continue
            if isinstance(node, chatparts.CompactMark):
                return
            if getattr(node, "compact_summary", None) is not None:
                mark.set_summary(node.compact_summary)
                node.get_parent().remove(node)
                return
            node, seen = node.get_next_sibling(), seen + 1

    def render_history(self, items, final=True):
        made = set(self.tool_cards)
        turn_calls, stop_said = [], False  # (the calls drawn since the last message of yours; its stop said)
        spawned = self.remote is not None and (self.remote.sessions.get(self.session_id) or {}).get("spawned_by")
        # (a chat a Bot started on the mesh: the task it was given drawn as any prompt is)
        for kind, content, *rest in items:
            if kind in ("user", "start_prompt"):
                turn_calls, stop_said = [], False
            elif kind == "assistant":
                before = len(self.tool_cards)
            stopped = kind == "stopped" or kind == "assistant" and rest and rest[0] and \
                STOP_SAID_RE.fullmatch(_first_text(content) or "")  # (aborted: as the live chat's, h_assistant)
            if stopped:  # (stopped: said once, as the live chat says it, h_result; not if a call it stopped says so)
                if not stop_said and not any(self.tool_cards[t].state in ("running", "interrupted")
                                             and t not in self.orphan_results for t in turn_calls):
                    self.note(wording.say("meta.interrupted"))
                stop_said = True
            elif kind == "tool_results":
                self.render_tool_results(content, from_file=True)
            elif kind == "artifact":
                if content["url"] not in self.artifacts:  # not already shown by its tool call
                    if not content.get("title"):
                        content["title"] = html_title(content.get("path")) or wording.say("references.claude_artifact")
                    info = self.register_artifact(content)
                    self.add(sel_label("Published " + link_markup(info["url"]), css="sysnote"))
            elif kind == "task_note":
                self.task_from_note(content)  # (its card, if it's drawn, says it ended)
                if (line := self.task_note(content)) is not None:
                    self.add(line)
            elif kind == "compact_boundary" and self.render_target is None and self.compact_mark is not None and \
                    self.compact_mark.state == "running" and self.compact_here(self.compact_mark):
                self.compact_mark.finish(content)  # (drawn going on: one kept through a dropped connection, finished)
            elif kind == "compact_boundary":  # (where claude compacted it, as it did live: on_compaction)
                self.adopt_summary(self.add(chatparts.CompactMark(content)))
            elif kind == "compact_summary" and (mark := self.last_compact_mark()) is not None:
                mark.set_summary(content)  # (what it wrote for itself on compacting: folded under its marker)
            elif kind == "compact_summary":  # (its boundary not drawn yet, or none: on its own till it is, adopt_summary)
                fold = Gtk.Expander(label=wording.say("compaction.summary"), child=MarkdownView(content))
                fold.add_css_class("sysnote")
                fold.compact_summary = content
                self.add(fold)
            elif kind == "ci_event":  # (what the desktop app says of a pull request it's watching: folded)
                fold = Gtk.Expander(label=content["title"], child=MarkdownView(content["text"]))
                fold.add_css_class("sysnote")
                self.add(fold)
            elif kind == "assistant" and rest and rest[0]:
                self.add(api_error_label(_first_text(content) or ""))
            elif kind == "assistant":
                self.render_assistant(content, written_at=ref_epoch(rest[1]) if len(rest) > 1 else None)
                turn_calls += list(self.tool_cards)[before:]
            else:
                text = "\n\n".join(clean_prompt(t) for t in _prompt_texts(content))
                atts = [Attachment("image", "image", c["source"].get("media_type"),
                                   base64.b64decode(c["source"]["data"]))
                        for c in (content if isinstance(content, list) else [])
                        if isinstance(c, dict) and c.get("type") == "image"
                        and c.get("source", {}).get("type") == "base64"]
                atts += [Attachment(c.get("title") or "document.pdf", "file", "application/pdf")
                         for c in (content if isinstance(content, list) else [])
                         if isinstance(c, dict) and c.get("type") == "document"]
                said = rest[2] if len(rest) > 2 else None
                if (sender := self.chat_senders().get(said)) is not None:  # (from another than you, its host's
                    # sender store says: a Bot, a chat; a subagent's task, its parent's. Never from what it says)
                    self.add(self.sender_turn(sender, text, atts, said))
                elif kind == "start_prompt" and not spawned:  # (what a session started from outside was asked: at
                    # its top, not yours)
                    row = Gtk.Box(spacing=6)
                    for a in atts:
                        row.append(attachment_widget(a, 96))
                    self.add(composerview.start_prompt_view(text, row if atts else None))
                else:
                    turn = UserTurn(self, self.user_bubble(text, atts), text, said)
                    turn.atts = atts  # (drawn again as another's if its host's sender store says so: senders_came)
                    self.add(turn)
        # calls that never got a result were interrupted; don't spin forever
        for tid, card in self.tool_cards.items():
            if final and tid not in made and card.state == "running" and tid not in self.orphan_results:
                card.set_state("interrupted")
        if final:
            self.settle_tasks()
            self.show_cut(self.cut_state())

    def settle_tasks(self):
        """Background tasks (commands, subagents) a finished session's transcript never heard the end of: interrupted,
        not running for ever. When it opens, and as each earlier part is drawn (fill_item): one started there and
        never ended came back as running, its spinner going, counted in the header, its subagent followed again."""
        for task in self.tasks.values():
            if task.get("from_file") and task.get("status") == "running":
                task["status"] = "interrupted"
                card = self.tool_cards.get(task.get("tool_use_id"))
                if card is not None and card.state in ("running", "background"):
                    card.set_state("interrupted")
                self.panel.update_task(task)
        self.update_tasks_indicator()

    # ---- sessions on the network (baudelib.server): opened from their transcripts' mirrors here, steered through
    # their servers (start_remote_proc), started from the sidebar while a group on the network is shown
    def open_remote_soon(self, info):
        """Open a network session once its transcript's here, what's new in it fetched from its server (sync_remote);
        as it was last seen if its server's gone."""
        r = info["remote"]
        self.opening_remote = info["id"]
        if r.state != "alive":
            if r.mine:  # (this machine's host process, gone after a long idle: started again, and taken up then)
                self.local_server(lambda _r: None)
            self.switch_to(lambda: self.resume_session(info))
            return

        def fetched():
            if self.opening_remote == info["id"] and not getattr(self, "let_go_done", False):
                self.switch_to(lambda: self.resume_session(info), show=False)
        self.sync_remote(r, info["id"], fetched)

    def open_remote(self, info):
        """A session on a server on the network: shown from its transcript's mirror here (Remote.mirror) as a chat
        Baude isn't running is, followed as its server writes it (on_network: sync_remote), and talked to through its
        server (start_remote_proc) as Baude's own claude is. Its folder is this window's only when its server is on
        this machine: links to files elsewhere are fetched from it (open_remote_link)."""
        r = info["remote"]
        self.left_remote.pop(info["id"], None)  # (open here again: what it does is seen)
        self.draft_group = ""  # (a new chat's group: not another's)
        carry = getattr(self, "carry_box", None) == (info["id"], self.session_id)  # (the box for its new conversation)
        self.carry_box = None
        self.remember_own_writes()
        if self.busy and self.proc is not None:
            self.detach_proc()  # (the turn goes on: detach_proc)
        self.clear_transcript(keep_box=carry)
        self.switching_chat = True
        cwd = info.get("cwd")
        if baudelib.client.is_here(r.info) and cwd and os.path.isdir(cwd):
            self.set_cwd(cwd)
        elif cwd:
            MarkdownView.base_dir = cwd  # (its relative links are its server's: not one of this machine's files)
        self.reset_stream_state()
        self.session_id, self.remote, self.desktop_chat = info["id"], r, False
        path = r.mirror(info["id"])
        if not os.path.exists(path) and not r.local:  # (one on this machine is read where it is, once it's there)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "a").close()
        self.apply_chat_model({**info, "path": path})
        self.signal_pending, self.switching_chat = None, False
        self.ref_checkout = None
        self.apply_chat_signal()
        self.sync_senders()  # (who its messages from others are from, if what's here of that's older: senders_came)
        self.view_session(path)
        self.show_title(info.get("title"))
        self.show_held()
        self.default_mode()
        if r.state == "alive" and (r.sessions.get(info["id"]) or {}).get("state") in ("working", "waiting") \
                and not self.live_elsewhere():  # (at a turn: live, as it goes on, Stop and all; attached once, so
            self.begin_resume()  # what it asks is answered through the one that's attached)
            self.set_busy(True)
        else:
            self.restart_proc(resume=True)  # (start_remote_proc: what it asks, its models; all it says once you write)
        if not carry:
            self.take_draft()
        self.apply_read_only()
        self.update_status()  # (its host, and its folder there)
        self.sidebar.fill()

    def sync_remote(self, r, sid, then=None):
        """Fetch what a network session's transcript has that its mirror here hasn't, on a thread; then() after (its
        subagents' are fetched after that, not waited for). One fetch at a time: one asked for meanwhile is made after
        it."""
        key = (r.key, sid)
        if (job := self.remote_syncing.get(key)) is not None:
            job["again"] = True
            job["then"] += [then] if then else []
            return
        job = self.remote_syncing[key] = {"again": False, "then": [then] if then else []}

        def work():
            try:
                r.sync(sid, subagents=False)
            finally:  # (whatever went wrong, the next fetch isn't held up by this one)
                on_main(done)
            if r.subagents_behind(sid):
                r.sync_subagents(sid)

        def done():
            if job["again"]:
                job["again"] = False
                threading.Thread(target=work, daemon=True).start()
                return
            self.remote_syncing.pop(key, None)
            for fn in job["then"]:
                fn()
        threading.Thread(target=work, daemon=True).start()

    def on_network(self):
        """The network changed: the open network session's transcript fetched as it grows (its subagents' too), its
        title as it's listed (renamed in another window, or elsewhere), the banner as it is now, and the chats its
        mesh cards name as they are now."""
        if getattr(self, "let_go_done", False):
            return
        self.watch_left_remote()
        self.check_host_version()
        if (app := self.get_application()) is not None:
            app.show_update_bars()  # (This Mac's host process waiting on its commands to restart: info's update)
        if (host := local_host()) is not None and "models" in host.info:
            meta.set_codex_catalog(host.info["models"])
        codex_changed = self.merge_codex_models()
        if codex_changed or self.providers_listed() != getattr(self, "providers_seen", None):
            self.sync_providers()
        if codex_changed:
            self.sync_effort_choices()
            self.sync_local()
        self.mesh_cards = [c for c in getattr(self, "mesh_cards", ()) if c.get_parent() is not None]  # (a reverted
        # one's gone; one in history not shown yet, kept)
        for card in self.mesh_cards:  # (the chats they started or messaged: as their hosts list them now)
            card.refresh_header()
        self.show_held()
        if self.remote is None:
            return
        self.sync_senders()
        listed = self.remote.sessions.get(self.session_id)
        if listed and listed.get("title") and listed["title"] != getattr(self, "session_title", None):
            self.show_title(listed["title"])
        if self.resume_pending and self.remote.local and self.remote.direct(self.session_id) and \
                self.view_path != self.remote.mirror(self.session_id):  # (on this machine: its transcript's there now)
            self.view_session(self.remote.mirror(self.session_id))
        if listed and self.remote.state == "alive" and \
                ((listed.get("size") or 0) != self.remote.mirrored(self.session_id) or
                 self.remote.subagents_behind(self.session_id)):
            self.sync_remote(self.remote, self.session_id)
        if listed and self.missing_session == self.session_id:  # (its host has it again: attached again, below)
            self.missing_session = self.missing_reason = None
        if self.proc is None and self.resume_pending and self.remote.state == "alive" and \
                self.missing_session != self.session_id and \
                time.monotonic() - getattr(self, "remote_tried", 0.0) > 10:
            self.start_remote_proc()  # (its server's there again, or at last: what it asks, its models)
        self.update_banner()
        self.update_session_state()
        if bool(self.cut_state()) != (getattr(self, "cut_row", None) is not None):
            self.show_cut(self.cut_state())

    def remote_view(self):
        """A network session with nothing of its server's attached (gone, or not there): watched from its mirror again,
        as it's fetched, the banner saying why there's nothing to write in."""
        if self.remote is None or self.resume_pending:
            self.update_banner()
            return
        path = self.remote.mirror(self.session_id)
        self.resume_pending, self.view_path = True, path
        try:
            self.tail.start(path, os.path.getsize(path))
        except OSError:
            self.tail.start(path, 0)
        self.update_banner()

    def host_form(self):
        """Add a host's fields and what they do, Add a host…'s (ask_add_host) and Meta's add_host form's: where ssh goes
        (an alias in ~/.ssh/config, a host, or user@host), and its host process's command there, if
        baude_headless.py isn't on its PATH. Kept from then on, reached or not."""
        dest = Gtk.Entry(placeholder_text=wording.say("new_session.host_placeholder"), activates_default=True,
                         width_chars=30)
        command = Gtk.Entry(placeholder_text=wording.say("hosts.command_placeholder",
                                                         command=baudelib.wire.SSH_COMMAND), activates_default=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(dest)
        box.append(command)
        where = lambda: dest.get_text().strip() if baudelib.wire.is_dest(dest.get_text().strip()) else None  # noqa: E731

        def submit():
            if (to := where()) is None:
                raise ValueError("Where ssh goes: user@host, or an alias in ~/.ssh/config")
            network().add({"via": "ssh", "dest": to}, by_hand=True, command=command.get_text().strip() or None)
            toast(self.sidebar, wording.say("hosts.looking_for_baude", dest=to))
            return f"looking for Baude on {to}"
        heading = wording.say("new_session.add_host")
        return metalook.Form(heading, box, [("destination", "user@host, or an alias in ~/.ssh/config", dest),
                                        ("command", "its host process's command there, run as `<command> "
                                                    "connect`: empty if baude_headless.py is on its PATH, else "
                                                    "its path (in a git checkout of Baude, <checkout>/"
                                                    "baude_headless.py; not the baude launcher, which runs the "
                                                    "app)", command)],
                         lambda: None if where() else "Where ssh goes: user@host, or an alias in ~/.ssh/config",
                         submit, verb=wording.say("common.add"), said=False)

    def ask_add_host(self):
        """New session's host right-clicked → Add Host…: another machine's host, by where ssh goes, as host_form has it."""
        form = self.host_form()
        dest, command = (w for _name, _takes, w in form.fields)
        dlg = Adw.AlertDialog(heading=form.heading, extra_child=form.child, default_response="add",
                              close_response="cancel", body=form.body)
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("add", form.verb)
        dlg.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dest.connect("changed", lambda *_: dlg.set_response_enabled("add", form.check() is None))
        dlg.set_response_enabled("add", False)
        dlg.connect("response", lambda _dlg, response: response == "add" and form.check() is None and form.submit())
        self.dialog = self.add_server_dialog = dlg
        dlg.present(self)
        dest.grab_focus()
        return dest, command

    def ask_host_command(self, key):
        """A host's right-click → Host Command…: the command its host process is started by there (ssh's
        `<command> connect`), if baude_headless.py isn't on its PATH or isn't the one; none: what it says, or PATH's."""
        r = next((r for r in network().remotes() if host_key(r) == key), None)
        if r is None:
            return
        entry = Gtk.Entry(text=r.command or "", activates_default=True, width_chars=36,
                          placeholder_text=r.where.get("command") or
                          wording.say("hosts.command_placeholder", command=baudelib.wire.SSH_COMMAND))
        dlg = Adw.AlertDialog(heading=wording.say("hosts.command_heading", host=remote_name(r)), extra_child=entry,
                              default_response="save", close_response="cancel",
                              body=wording.say("hosts.command_body", host=r.host))
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("save", wording.say("common.save"))
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _d, res: res == "save" and network().set_command(r, entry.get_text().strip()))
        self.dialog = dlg
        dlg.present(self)
        entry.grab_focus()
        return entry

    def ask_remote_start(self, server=None, folder=None, here=False):
        """New session through a server: a server's folder's + (here: this machine's, one started there if there's
        none, local_server). A prompt, a folder and a group (new_group's, if it has one);
        the model, effort and permission mode the top bar has (remote_start_form). Opened once its server's started it
        (remote_started)."""
        group = self.sidebar.new_group(HERE_HOST if here else host_key(server) if server is not None else None) or None
        if here:
            servers = [r for r in network().alive() if host_key(r) == HERE_HOST][:1]
        else:
            servers = network().alive(group)
            if server in servers:
                servers.remove(server)
                servers.insert(0, server)
            if not servers:  # (none there: through this machine's, started)
                return self.ask_remote_start(folder=folder, here=True)
        form = self.remote_start_form(servers or [None], folder, group, said=here, here=here)
        dlg = Adw.AlertDialog(heading=form.heading, extra_child=form.child, default_response="start",
                              close_response="cancel", body=form.body)
        if hasattr(dlg, "set_prefer_wide_layout"):  # (libadwaita 1.6: room for a folder's whole path)
            dlg.set_prefer_wide_layout(True)
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("start", form.verb)
        dlg.set_response_appearance("start", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _dlg, response: response == "start" and self.submit_form(form))
        self.dialog = self.remote_dialog = dlg
        self.remote_fields = form.parts
        dlg.present(self)
        form.parts["prompt"].grab_focus()

    def submit_form(self, form):
        """A dialog's form submitted (its own button): what stops it said, as a toast, not raised in the handler."""
        try:
            form.submit()
        except ValueError as e:
            toast(self.sidebar, str(e))

    def remote_start_form(self, servers, folder=None, group=None, said=False, agents=False, here=False):
        """A new session's fields and what they do, New session…'s (ask_remote_start) and Meta's spawn_agent form's:
        the host (servers: those to pick from; None, this machine's host process, started as it's needed; said: the
        one, said, not picked; here: this machine's host process), with agents what runs on it (the
        models it offers: its own, or the top bar's), the folder (the one last used on that host, else its home; empty:
        its home, ~), the group and the prompt; the effort and mode the top
        bar has, and its model unless an agent's picked. Started, it's opened (remote_started)."""
        grid = Gtk.Grid(row_spacing=8, column_spacing=12, width_request=420)
        rows = []

        def field(label, widget, top=False):
            name = Gtk.Label(label=label, xalign=1, valign=Gtk.Align.START if top else Gtk.Align.CENTER)
            name.add_css_class("dim-label")
            widget.set_hexpand(True)
            grid.attach(name, 0, len(rows), 1, 1)
            grid.attach(widget, 1, len(rows), 1, 1)
            rows.append(widget)
        named = lambda r: THIS_MACHINE if r is None or host_key(r) == HERE_HOST else \
            wording.say("new_session.host_named", name=remote_name(r), host=r.host)  # noqa: E731
        def offered(r):
            host = r or local_host()
            return [(m["value"], m.get("displayName") or m["value"])
                    for m in (host.info.get("models", []) if host is not None else []) if m["value"] != "codex/default"] or MODELS
        servers_dd = agent_dd = None
        if said:  # (the host it's to be on: said, not picked)
            field(wording.say("new_session.host"), Gtk.Label(label=named(servers[0]), xalign=0))
        else:
            servers_dd = Gtk.DropDown.new_from_strings([named(r) for r in servers])
            field(wording.say("new_session.host"), servers_dd)
        server = lambda: servers[servers_dd.get_selected()] if servers_dd is not None else servers[0]  # noqa: E731
        if agents:
            agent_dd = Gtk.DropDown.new_from_strings([label for _value, label in offered(servers[0])])
            field(wording.say("common.agent"), agent_dd)
        last = load_settings().get("remote_folders") or {}
        place = lambda r: HERE_HOST if r is None or host_key(r) == HERE_HOST else r.key  # noqa: E731
        home = lambda r: self.cwd if place(r) == HERE_HOST else r.info.get("home")  # noqa: E731
        where = Gtk.Entry(text=folder or last.get(place(servers[0])) or home(servers[0]) or "~")
        field(wording.say("common.folder"), where)
        group_entry = Gtk.Entry(text=group or (servers[0].info.get("group") if servers[0] is not None else None) or "")
        field(wording.say("new_session.group"), group_entry)
        prompt = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, top_margin=6, bottom_margin=6,
                              left_margin=8, right_margin=8)
        frame = Gtk.ScrolledWindow(child=prompt, min_content_height=96, hscrollbar_policy=Gtk.PolicyType.NEVER)
        frame.add_css_class("card")
        field(wording.say("common.prompt"), frame, top=True)
        effort = lambda: (self.local_effort if model_program(self.model) == "pi" else self.effort) \
            if self.effort_dd.get_sensitive() else None  # noqa: E731  (the top bar's as it's started)
        runs = " · ".join(x for x in (None if agents else self.model_said(self.model_dd.value()),
                                      self.effort_dd.label() if effort() else "", self.mode_dd.label()) if x)

        def picked_server(*_):  # (another server: the folder last used on it, else its home; what it runs)
            r = server()
            if not folder:
                where.set_text((load_settings().get("remote_folders") or {}).get(place(r)) or home(r) or "~")
            if agent_dd is not None:
                agent_dd.set_model(Gtk.StringList.new([label for _value, label in offered(r)]))
        if servers_dd is not None:
            servers_dd.connect("notify::selected", picked_server)

        def check():
            if agent_dd is not None and agent_dd.get_selected_item() is None:
                return f"{named(server())} offers no agent"
            return None

        def submit():
            if (problem := check()) is not None:
                raise ValueError(problem)
            buf = prompt.get_buffer()
            r = server()
            model = offered(r)[agent_dd.get_selected()][0] if agent_dd is not None else self.model
            params = {"op": "start", "prompt": buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip(),
                      "cwd": where.get_text().strip() or "~", "model": model, "effort": effort(),
                      "mode": "default" if model_program(model) == "codex" else self.mode,
                      "group": group_entry.get_text().strip() or None}
            save_settings(remote_folders={**(load_settings().get("remote_folders") or {}), place(r): params["cwd"]})
            start = lambda r: r.request(params, lambda reply: self.remote_started(r, reply))  # noqa: E731
            if r is None:
                self.local_server(start)
            else:
                start(r)
            return f"starting it on {named(r)}"
        on = THIS_MACHINE if here or servers[0] is None else remote_name(servers[0])
        heading = wording.say("new_session.spawn_agent") if agents else \
            wording.say("new_session.on_host", host=on) if said else wording.say("new_session.on_network")
        fields = ([("host", "the host it runs on", servers_dd)] if servers_dd is not None else []) + \
            ([("agent", "what runs it: one of the host's", agent_dd)] if agent_dd is not None else []) + \
            [("folder", "the folder it starts in, on that host", where), ("group", "its group (or empty)", group_entry),
             ("prompt", "what it's asked first", prompt)]
        form = metalook.Form(heading, grid, fields, check, submit, verb=wording.say("new_session.start"),
                         body=f"With the top bar's {runs}" if runs else "")
        form.parts = {"servers": servers_dd, "agent": agent_dd, "folder": where, "group": group_entry, "prompt": prompt}
        return form

    def group_form(self):
        """New Group's field and what it does, as the sidebar asks for one (ask_group_name), for Meta's new_group
        form."""
        entry = Gtk.Entry(activates_default=True, placeholder_text=wording.say("meta_app.its_name"))
        name = lambda: " ".join(entry.get_text().split())  # noqa: E731

        def known():  # (the sidebar's, once the window has one: a form's drawn as the side panel is made, first)
            if (sidebar := getattr(self, "sidebar", None)) is not None:
                return {k for k, *_ in sidebar.known_groups()}
            return {*made_groups(), *chat_groups().values()} - {""}

        def check():
            if not name():
                return wording.say("new_session.group_needs_name")
            if name() in known():
                return wording.say("new_session.group_exists", name=name())
            return None

        def submit():
            if (problem := check()) is not None:
                raise ValueError(problem)
            self.sidebar.make_group(name())
            return f"made group {name()}"
        return metalook.Form(wording.say("new_session.new_group"), entry, [("name", "the group's name", entry)], check,
                         submit, verb=wording.say("common.create"), body=wording.say("new_session.group_body"))

    def check_host_version(self):
        """This Mac's host process on other code than Baude (an update one has picked up and the other hasn't, or
        another checkout's): looked at (host_apart), and said in every window's banner once it's lasted, with the
        restart that puts them on the same code."""
        if (app := self.get_application()) is not None:
            host_apart(app).look(local_host())

    def host_apart_said(self):
        """(apart, title, button label) for the banner, or None (meshui.HostApart.now)."""
        app = self.get_application()
        if app is None or not _HOST_APART:
            return None
        return _HOST_APART[0].now(local_host(), "Baude", app.updater is not None and bool(app.updater.ready))

    def act_host_apart(self):
        if (said := self.host_apart_said()) is not None:
            app = self.get_application()
            _HOST_APART[0].act(said[0], app.restart, lambda then=None: self.restart_host(then=then))

    def restart_host(self, waited=0, then=None):
        """This Mac's host process stopped and started again (a newer one, after an update): once no command's running
        there (the host says: quit if_idle), as stopping it would stop that; a turn that's only the model writing is
        cut short, said so (cut), and carried on with Continue. Its claudes stop, their transcripts kept, and each of
        its sessions resumes as it's next written to (the windows on them reattach as it's back). then: what's done
        once it's gone, in place of starting it again (Baude restarted too: the new Baude starts it)."""
        r = local_host()
        if r is None:
            return then() if then is not None else None
        busy = {c.get("session") for c in (r.info.get("update") or {}).get("waiting") or ()} - {None}  # (its say)

        def later(n):
            if not waited:
                toast(self.sidebar, wording.say("hosts.restart_waiting", n=n))
            GLib.timeout_add_seconds(5, lambda: self.restart_host(waited + 5, then) and False)

        def answered(reply):
            if reply.get("op") == "busy":
                return later(len(reply.get("sessions") or []) or 1)
            end = time.monotonic() + 15

            def gone():  # (the old one gone: a new one, started as any is: local_server)
                if r.state == "alive" and time.monotonic() < end:
                    return True
                if then is not None:
                    then()
                    return False
                self.local_server(lambda _r: toast(self.sidebar, wording.say("hosts.daemon_restarted")), quiet=True)
                return False
            GLib.timeout_add(250, gone)
        if busy:
            return later(len(busy))
        r.request({"op": "quit", "if_idle": True, "restart": True}, answered)

    def local_server(self, then, wait_s=20, quiet=False):
        """This machine's host process (local_host), which runs every chat of Baude's: then(it), on the main loop, once
        it's there. The one that's there, else one started (baudelib.hosts.start_here: baude_headless.py, in a process
        of its own that goes on after Baude does, its output in its state folder's host.log; one running whose socket's
        gone, told to listen again). Waited for once, for every window that wants it. quiet: nothing said of it."""
        net = network()
        if (r := local_host()) is not None:
            then(r)
            return
        if _LOCAL_SERVER["wanted"] is not None:
            _LOCAL_SERVER["wanted"].append(then)
            return
        # (as the command-line heads start it: one that's running but lost its socket nudged)
        threading.Thread(target=baudelib.hosts.start_here, daemon=True, name="start-host").start()
        if not quiet:
            toast(self.sidebar, f"Starting {THIS_MACHINE}'s host process…")
        _LOCAL_SERVER["wanted"] = [then]
        end = time.monotonic() + wait_s

        def look():
            net.here()  # (its socket's there as it binds: no need to wait for the next look)
            net.look()
            r = local_host()
            if r is None and time.monotonic() < end:
                return True
            wanted, _LOCAL_SERVER["wanted"] = _LOCAL_SERVER["wanted"], None
            if r is None:
                self.note(f"{THIS_MACHINE}'s host process didn't answer: {pretty_path(baudelib.hosts.log_path())} "
                          "may say why")
            for fn in wanted:
                r is not None and fn(r)
            return False
        GLib.timeout_add(250, look)

    def new_session_on(self, r):
        """New session, on another host (r, picked beside it): a blank chat there, opened to be written in, in the
        folder last used there (else its home), on the top bar's model if it's Claude's (else the host's own); listed
        once it's written in, as a new chat here is."""
        self.show_chat()  # (as it's asked for, from the Feedback tab or a file's code: not once it's started)
        group = self.sidebar.new_group(host_key(r))
        model = self.model if self.model and model_program(self.model) == "claude" else None
        r.request({"op": "start", "prompt": "", "cwd": (load_settings().get("remote_folders") or {}).get(r.key) or "~",
                   "model": model, "effort": self.effort if model else None, "mode": "auto", "group": group,
                   "draft": True, **({"env": claudelib.claude_env()} if r.mine else {})},
                  lambda reply: self.remote_started(r, reply))

    def remote_new_conversation(self, model):
        """Another kind of model picked in a network session (Claude's to Apple's, say): as in a chat here, a new
        conversation, but on the same server, in the same folder and group, opened to be written in."""
        r, listed, was = self.remote, self.remote.sessions.get(self.session_id) or {}, self.session_id
        effort = self.local_effort if model_program(model) == "pi" else self.effort
        mode = "default" if "codex" in (model_program(model), model_program(self.model)) else self.mode
        r.request({"op": "start", "prompt": "", "cwd": listed.get("cwd") or "~", "model": model, "effort": effort,
                   "mode": mode, "group": listed.get("group"), "draft": True,
                   **({"env": claudelib.claude_env()} if r.mine else {})},
                  lambda reply: self.remote_started(r, reply, carry_from=was))

    def remote_started(self, r, reply, carry_from=None):
        """A server started a session asked for here: its group listed, and it opened (another host's isn't listed till
        that host's picked beside New session: Meta's button:host). carry_from: the chat whose box it opens with, if
        that's still the one open (a new conversation for another kind of model: open_remote)."""
        if reply.get("op") != "started":
            self.note(wording.say("new_session.host_refused", host=remote_name(r), why=baudelib.wire.said(reply)))
            return
        self.carry_box = (reply["session"]["id"], carry_from) if carry_from else None
        r.sessions[reply["session"]["id"]] = reply["session"]  # (listed before the server next says what it has)
        sb, info = self.sidebar, remote_info({**reply["session"], "server": r})
        groups, group = sb.group_filter(), chat_group(info)  # (and its group, if ⋮ leaves it out)
        sb.filtered(sidebar_groups=sorted(groups | {group}) if groups is not None else None)
        self.open_remote_soon(info)

    def open_remote_link(self, uri):
        """A link in a network session to a file on its server, not this machine: fetched from it and shown in a tab, as
        a file here is (show_code). False for anything else (a web address, a session on this machine)."""
        r = self.remote
        if r is None or baudelib.client.is_here(r.info) or \
                re.match(r"[a-z][a-z0-9+.-]*:", uri, re.I) and not uri.startswith("file:"):
            return False
        m = re.fullmatch(r"(?:file://)?(.+?)(?:#L?(\d+)(?:-L?\d+)?|:(\d+)(?::\d+)?)?", uri)
        if not m:
            return False
        home = r.info.get("home") or "/"
        raw = urllib.parse.unquote(m[1])
        raw = home + raw[1:] if raw == "~" or raw.startswith("~/") else raw
        cwd = (r.sessions.get(self.session_id) or {}).get("cwd") or home
        self.show_remote_file(r, os.path.normpath(os.path.join(cwd, raw)), int(m[2] or m[3]) if m[2] or m[3] else None)
        return True

    def show_remote_file(self, r, path, line=None):
        """A file on r's host, fetched from it (read_file) and shown in a tab, as a file here is (show_code)."""
        def shown(reply):
            data = base64.b64decode(reply.get("data") or "") if reply.get("op") == "file" else b""
            if reply.get("op") != "file" or reply.get("error") or b"\0" in data[:8192]:
                self.note(wording.say("file_view.unreadable", path=path, host=remote_name(r),
                                      why=baudelib.wire.said(reply) or wording.say("file_view.not_text")))
                return
            self.show_code(path, line, text=data.decode("utf-8", "replace"),
                           note=wording.say("file_view.on_host", host=remote_name(r)))
        r.request({"op": "read_file", "path": path}, shown)

    def show_sessions(self):
        self.sidebar_slide.set_shown(True)
        self.sidebar.focus_search()

    def resume_session(self, info):
        if info.get("remote") is not None:
            return self.open_remote(info)
        self.draft_group = ""  # (a new chat's group: not another's)
        self.remember_own_writes()  # before the view switches to the other session
        if self.busy and self.proc is not None:
            self.detach_proc()  # (the turn goes on: detach_proc; a host's chat's let go of, before it's not this one)
        self.remote, self.desktop_chat = None, bool(info.get("desktop")) or is_desktop(info.get("id"))
        MarkdownView.base_dir = self.run_cwd  # (not a network session's folder, whatever's set below)
        cwd = info.get("cwd")
        self.clear_transcript()
        self.switching_chat = True  # (its own switches come below: not the chat before's, in its folder)
        self.gone_tree = None
        if cwd and os.path.isdir(cwd):  # (where it was recorded, a worktree too: --resume finds it in that folder's
            self.set_cwd(cwd)           # project, so claude runs there; the window's folder is its repository's)
        elif cwd:
            if (folder := main_folder(cwd)[0]) != cwd and os.path.isdir(folder):
                self.set_cwd(folder)  # (a worktree that's gone: its repository, till it's made again)
            if worktreelib.restorable(cwd):  # (removed, as Claude Desktop does as it archives a chat: made again from
                self.gone_tree = (info["id"], cwd, info.get("path"))  # its branch as it's written in, as Desktop does)
                self.note(f"worktree {os.path.basename(main_folder(cwd)[1] or cwd)} was removed: "
                          "it's made again from its branch when you write")
            else:
                self.note(f"{pretty_path(cwd)} no longer exists; resuming in {self.short_cwd()}")
        self.reset_stream_state()
        self.session_id = info["id"]  # (before the history: Signal files its replies under it)
        self.seen_chat()
        self.apply_chat_model(info)  # (the model it was on, not the chat before's)
        self.signal_pending, self.switching_chat = None, False
        tree = main_folder(self.run_cwd)[1]  # (the checkout it works in, before Signal starts: from what it did, else
        self.ref_checkout = chat_checkout(self.cwd, info["path"]) or (tree if tree and os.path.isdir(tree) else None)  # the worktree it runs in)
        self.apply_chat_signal()  # (this chat's own switches)
        self.follow_checkout(info["path"])
        self.view_session(info["path"])
        if info.get("title"):
            self.show_title(info["title"])
        self.default_mode()
        if (left := self.take_background(info["id"])) is not None:  # (left mid-turn, still at it: its own claude again)
            self.attach_proc(left)
        else:
            self.restart_proc(resume=True)  # a fresh helper: resume_pending keeps --resume off
        self.take_draft()
        self.apply_read_only()
        self.sidebar.fill()

    def view_session(self, path):
        """Show a saved session read-only and follow it as it grows (it may be running elsewhere)."""
        try:
            st = os.stat(path)
        except OSError:
            st = None
        live = st is not None and time.time() - st.st_mtime < LIVE_SECONDS
        self.total_cost = session_cost(path)  # (claude's, as it last exited)
        self.update_status()
        self.load_history(path, live=live)
        self.resume_pending, self.view_path = True, path
        self.tail.start(path, st.st_size if st else 0)
        if st is not None and st.st_mtime <= self.own_mtimes.get(path, -1) + 1:
            self.tail.last_written, self.tail.turn_open = 0.0, False  # baude's own writes, not another program's
        self.update_banner()
        self.follow_checkout(path)

    def recorded_cost(self, session_id):
        """What the session's transcript says claude had spent on it when it last exited (its last cost-state), which a
        claude --resume'd on it counts on from; 0 for none."""
        info = cached_session_info(path) if session_id and (path := session_file(session_id)) else None
        return (info or {}).get("cost") or 0.0

    def remember_own_writes(self):
        """Note how baude's own claude left the session it ran, so coming back to it isn't
        mistaken for another program having just written it."""
        if self.resume_pending or not self.session_id:
            return
        if (info := find_session(self.session_id)) is not None:
            try:
                self.own_mtimes[info["path"]] = os.stat(info["path"]).st_mtime
            except OSError:
                pass

    def live_elsewhere(self):
        """Is the viewed session one Claude Desktop runs, and is it writing it now? Desktop runs its chats itself, so a
        second writer here would interleave two conversations in its transcript: that's kept from it (update_banner).
        No other chat is: a host's session (this machine's or another's), one this machine's host hasn't taken up yet,
        one another window or a terminal's claude has open too. Several writers at once is how those are used, and
        what's sent mid-turn waits for the turn's end. Not Baude's own claude at a turn in a Desktop chat left
        mid-turn, which writing here takes over (begin_resume)."""
        if not self.resume_pending or self.remote is not None:
            return False
        if not (self.desktop_chat or is_desktop(self.session_id)):
            return False
        return self.tail.in_use() and background_proc(self.session_id, BaudeWindow) is None

    def drop(self, running):
        """A host's session's connection dropped (on_exit's "lost"): "Connection to <host> dropped…" in the banner, its
        send button off, till it's attached again (start_remote_proc, tried every 10 s or so); its host off the network
        meanwhile: the banner says that instead. running: its claude was running (written in from here), so if it's
        gone by then, the next message resumes it, and says so."""
        self.drop_running, self.drop_session = running, self.session_id
        if self.drop_timer is None:
            self.drop_timer = GLib.timeout_add_seconds(2, self.watch_drop)

    def watch_drop(self):
        if getattr(self, "let_go_done", False):  # (the window's gone)
            self.drop_timer = None
            return False
        if self.drop_running is None or self.remote is None or self.drop_session != self.session_id:
            self.drop_running = self.drop_session = self.drop_timer = None
            self.update_banner()
            return False
        if self.proc is None and self.resume_pending and self.remote.state == "alive" and \
                self.missing_session != self.session_id and \
                time.monotonic() - getattr(self, "remote_tried", 0.0) > 10:  # (a try waits seconds for its host, as
            # on_network's: on the main loop)
            self.start_remote_proc()
        return True

    def show_retry(self, msg):
        """claude retrying the model's API (its api_retry: the provider overloaded or failing, or not answering): said in
        the banner, an ellipsis going, till what it says next comes, or the turn's over (msg None)."""
        if msg is None and self.retrying is None:
            return
        self.retrying = msg
        self.update_banner()

    def banner_text(self, kind):
        """What the banner says (update_banner's kind), as markup: one waiting something out with its ellipsis."""
        if kind == "apart":
            text = (self.host_apart_said() or (None, ""))[1]
        elif kind in ("gone", "drop") and (said := stopping_said(self.remote)) is not None:
            text = said
        elif kind == "gone":
            name = "daemon" if self.remote.mine else remote_name(self.remote)  # (this machine's: over its socket,
            # not the network)
            text = wording.say("hosts.down", host=name) if self.remote.state == "dead" else \
                "Looking for daemon…" if self.remote.mine else wording.say("hosts.looking", host=name)
        elif kind == "live":
            text = "This session is currently active elsewhere"
        elif kind == "missing":  # (its host there, but with no such session: session_missing)
            name = "Daemon" if self.remote.mine else remote_name(self.remote)
            text = wording.say("chat_banner.missing_because", name=name, reason=self.missing_reason) \
                if self.missing_reason else wording.say("chat_banner.missing", name=name)
        elif kind == "drop":
            text = f"Connection to {'daemon' if self.remote.mine else remote_name(self.remote)} dropped"
        else:
            m = self.retrying or {}
            what = wording.say("chat_banner.api_status", status=m["error_status"]) if m.get("error_status") else \
                wording.say("chat_banner.api_silent")
            text = wording.say("chat_banner.retrying_attempt", what=what, attempt=m["attempt"], max=m["max_retries"]) \
                if m.get("attempt") and m.get("max_retries") else wording.say("chat_banner.retrying", what=what)
        if kind not in ("drop", "retry"):
            return GLib.markup_escape_text(text)
        n = self.banner_dots or 1
        return GLib.markup_escape_text(text) + "." * n + f'<span alpha="1">{"." * (3 - n)}</span>'

    def tick_banner(self):
        if self.banner_kind not in ("drop", "retry") or getattr(self, "let_go_done", False):
            self.banner_timer = None
            return False
        self.banner_dots = self.banner_dots % 3 + 1
        self.banner.set_title(self.banner_text(self.banner_kind))
        return True

    def update_banner(self):
        """What's in the way of this chat from outside it, said in the banner till it's gone. Another program (not Baude:
        live_elsewhere) has the session: no text box (show_composer), as two writers would interleave two
        conversations in one transcript. Its host isn't there, or its connection's dropped (drop): its send button off
        (Composer.gate), as there's no one to send to. claude's retrying the model's API (show_retry). Those waited out
        here (dropped, retrying) with an ellipsis going."""
        live = self.live_elsewhere()
        gone = self.remote is not None and (self.resume_pending and self.remote.state != "alive" or
                                            stopping_said(self.remote) is not None)  # (stopping: as good as gone)
        dropped = self.drop_running is not None and self.remote is not None and self.drop_session == self.session_id
        missing = self.remote is not None and self.proc is None and self.missing_session == self.session_id
        apart = self.host_apart_said()  # (this Mac's host process on other code: said when nothing of the chat's is)
        kind = "gone" if gone else "missing" if missing else "live" if live else "drop" if dropped else \
            "retry" if self.retrying is not None else "apart" if apart is not None else None
        self.banner_kind = kind
        if kind is not None:
            self.banner.set_title(self.banner_text(kind))
        if kind in ("drop", "retry") and self.banner_timer is None:
            self.banner_timer = GLib.timeout_add(widgets.WorkingDots.STEP_MS, self.tick_banner)
        self.banner.set_button_label(wording.say("chat_banner.new_chat") if kind == "missing" else apart[2] if kind == "apart" else None)
        self.banner.set_revealed(kind is not None)
        self.locked = live
        if hasattr(self, "composer_box"):
            self.composer_box.gate("network", dropped)
            self.composer_box.gate("host", gone or missing)
        self.show_composer()

    def tail_lines(self, lines):
        """Draw what other programs appended to the viewed session."""
        if not (self.resume_pending and self.view_path):
            return
        items = history_items(lines, first=self.history.first if self.history is not None else None)
        if items:
            if self.history is not None:
                self.history.shown += len(items)
            self.render_history(items, final=False)
        self.update_banner()
        self.update_session_state()
        self.follow_checkout(self.view_path)

    def settle_kept_compaction(self):
        """A compaction kept going through a dropped connection (compact_kept), its turn over on its host now: what its
        transcript says of it drawn first (its marker finished: render_history), then ended if it didn't finish."""
        if not self.compact_kept:
            return
        if self.remote is not None and self.remote.state == "alive" and self.resume_pending and self.view_path:
            self.remote.sync(self.session_id, timeout=3, subagents=False)  # (once: on the main loop, as begin_resume)
            self.tail.read()
        self.compact_ended()

    def poll_tail(self):
        """Once a second: let 'live' and 'working' expire when the writes stop."""
        if self.compact_kept and self.proc is not None and self.session_state() not in ("working", "waiting"):
            self.settle_kept_compaction()  # (attached again, its turn's over there)
        if self.resume_pending and self.view_path:
            self.read_subagents()
            self.update_banner()
            self.update_session_state()
        return True

    def begin_resume(self):
        """The first message: stop viewing, start `claude --resume` on the session."""
        if self.remote is not None and self.remote.state == "alive" and self.view_path:
            # (a network session: what its server's written since it was last fetched, drawn now, not lost to the
            # live chat that takes over: the end of a turn answered while it was watched, say)
            self.remote.sync(self.session_id, timeout=3, subagents=False)  # (on the main loop: only what's drawn)
            self.tail.read()
        self.resume_pending, self.view_path = False, None
        self.tail.stop()
        self.update_banner()
        reason = self.resume_reason[1] if self.resume_reason and self.resume_reason[0] == self.session_id else None
        if reason:  # (its claude gone meanwhile, and started again now: drop, on_exit)
            self.note(f"— resumed from {reason} —")
        elif self.resumed_note:
            self.note("— resumed —")
        self.resumed_note, self.resume_reason = False, None
        if (left := self.take_background(self.session_id)) is not None:  # (left mid-turn in a window, still at it)
            self.attach_proc(left)
            return
        self.restart_proc(resume=True)

    def h_result(self, msg):
        self.compact_ended()
        if self.queued and self.send_after_result is None and not getattr(self.proc, "stop_asked", False) and \
                self.queue_upto is None:
            self.queue_upto = "all"  # (a turn that ended on its own: what was queued sent, once it's over)
        if self.send_after_result is not None:  # (the turn a revert stopped, ended: what's in the box sent, after this)
            GLib.idle_add(self.send_reverted)
        GLib.idle_add(lambda: self.follow_checkout() and False)  # (the turn may have moved into a worktree)
        if self.proc is not None:
            self.proc.asks.clear()  # (the turn's over: what it asked and wasn't answered can't be now)
        if self.proc is not None and not hasattr(self.proc, "cost_seen"):  # (what --resume counted on from, read now,
            self.proc.cost_seen = self.recorded_cost(getattr(self.proc, "resumed", None))  # not as it started: the
        self.total_cost += spent_since(self.proc, msg)  # claude before writes its cost-state as it exits, after that)
        self.blocks.clear()
        cut = self.cut_short()
        u = msg.get("usage", {})
        tok = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
               + u.get("cache_creation_input_tokens", 0))
        said, self.said_last = getattr(self, "said_last", None), None
        stop_asked = getattr(self.proc, "stop_asked", False)
        if self.proc is not None:
            self.proc.stop_asked = False
        if msg.get("is_error") and stop_asked and ended_by_stop(msg):
            if stop_asked is True and self.send_after_result is None and not cut:  # (said already, stop_stuck; one
                self.note(wording.say("meta.interrupted"))  # a revert stopped: gone from the chat; a call it
                # stopped: its card says so)
        elif msg.get("is_error"):
            shown = msg.get("result")
            if (gone := msg.get("gone")) and self.remote is not None and not self.remote.mine:  # (no /cd there)
                shown = f"{gone.get('why')}: start a new chat " + (f"in {gone['folder']} " if gone.get("folder") else "") \
                    + f"on {remote_name(self.remote)} to carry on"
            if (msg.get("result") or "").strip().removeprefix("⚠").strip() != said:
                self.note(f"turn ended with error: {shown or msg.get('subtype')}")
            if meta.OVERFLOW_RE.search(msg.get("result") or ""):
                self.outgrown = True
            if needs_login(msg.get("result")):
                self.login_prompt(msg["result"])
        self.end_turn()
        self.tell_desktop(self.session_id, turn_said(msg))
        self.update_status(wording.say("status_bar.last_turn", turns=msg.get("num_turns", 0),
                                       seconds=f"{msg.get('duration_ms', 0) / 1000:.1f}", used=fmt_tokens(tok),
                                       written=fmt_tokens(u.get("output_tokens", 0))))

    def end_turn(self, proc=None):
        """A turn's over (its result; or stopped here, stop_stuck: proc, the claude it ran in): the chat not busy, and
        what waited for its end done."""
        proc = self.proc if proc is None else proc
        self.set_busy(False)
        self.keep_chat_model()
        if getattr(self, "restart_after_turn", False):  # (signed in while it ran: on the new sign-in now)
            self.restart_after_turn = False
            GLib.idle_add(lambda: self.restart_claude() and False)
        move, self.move_after_turn = getattr(self, "move_after_turn", None), None
        if move is not None and move[0] is proc:  # (a model or provider picked while it ran: that model now)
            GLib.idle_add(lambda: self.on_model_changed(move[1]) and False)
        self.refresh_context()
        self.sidebar.refresh()  # the session file just changed (first save, newer time)

    def cut_short(self):
        """A turn's over (stopped with Esc, say) and its calls still running never will be: interrupted, not spinning
        for ever. A background subagent's calls go on after the turn: left running. Whether any was."""
        cut = False
        for tid, card in list(self.tool_cards.items()):
            if card.state != "running":
                continue
            parent = self.tool_parent.get(tid)
            while parent is not None and not self.goes_on(parent):
                parent = self.tool_parent.get(parent)
            if parent is None:
                card.answered()  # (asked whether to allow it: nothing to answer now)
                card.set_state("interrupted")
                cut = True
        return cut

    def goes_on(self, tool_id):
        """Whether a subagent's call goes on after its turn: run in the background (its card says so, or, its card
        not running in the turn, a running task's is it), or not drawn here, so not known to have stopped."""
        card = self.tool_cards.get(tool_id)
        if card is None or card.state == "background":
            return True
        return card.state != "running" and any(
            t.get("tool_use_id") == tool_id and t.get("status") == "running" for t in self.tasks.values())

    def h_control_response(self, msg):
        r = msg.get("response", {})
        callback, refused = self.pending.pop(r.get("request_id"), (None, None))
        if r.get("request_id") == self.proc.init_rid and r.get("subtype") == "success":
            trace("claude ready")  # (its commands, models and modes are known)
            resp = r.get("response") or {}
            self.completer.set_commands(resp.get("commands") or [])
            self.sync_models(resp.get("models"))
            self.sync_mode(resp.get("current_permission_mode"))
            self.refresh_effort()
            self.refresh_context()
        elif callback and r.get("subtype") == "success":
            callback(r.get("response") or {})
        elif r.get("subtype") == "error" and refused:
            refused(r.get("error") or "")
        elif r.get("subtype") == "error" and not callback:
            self.note(f"control error: {r.get('error')}")

    def h_control_request(self, msg):
        rid, req = msg["request_id"], msg.get("request", {})
        if req.get("subtype") != "can_use_tool":
            self.proc.send({"type": "control_response", "response": {
                "subtype": "error", "request_id": rid, "error": f"baude: unsupported {req.get('subtype')}"}})
            return
        name, inp = req.get("tool_name"), req.get("input", {})
        if name in self.allowed_in(self.session_id) and name != "AskUserQuestion" and model_program(self.model) == "claude":  # (a model on this Mac: always asked)
            self.proc.asks.pop(rid, None)  # (one held while its chat was left: answered now)
            self.proc.respond(rid, {"behavior": "allow", "updatedInput": inp})
            return
        self.proc.asks[rid] = msg  # (asked again here if the chat's left and opened again before it's answered)
        self.permission_card(rid, name, inp, req.get("tool_use_id"))
        self.tell_desktop(self.session_id, wording.say("asks.has_question_for_you", name="Claude")
                          if name == "AskUserQuestion"
                          else wording.say("asks.waiting_for_tool", tool=name or wording.say("asks.a_tool")))

    def sign_in(self, args=()):
        """/login (or Sign in on a turn that failed for want of it): claude auth login, as a card (LoginCard)."""
        self.add(LoginCard(self, args))
        self.stick_bottom = True

    def signed_in(self):
        """Signed in afresh: the chat's claude started again on it (after the turn, if one's running)."""
        if self.busy:
            self.restart_after_turn = True
        else:
            self.restart_claude()

    def restart_claude(self):
        """The chat's claude started again (signed in or out since): Baude's own, here (restart_proc); this machine's
        host process's, stopped there, so its next message starts it again, as it's signed in now."""
        if self.remote is not None and self.remote.mine and self.session_id:
            self.remote.request({"op": "stop", "session": self.session_id, "restart": True})
        self.restart_proc(resume=True)

    def sign_out(self):
        """/logout: claude auth logout, said in the chat; the chat's claude started again (signed out, as it is)."""
        self.note("signing out…")

        def run():
            try:
                r = subprocess.run([claude_exe(), "auth", "logout"], capture_output=True, text=True, timeout=60,
                                   stdin=subprocess.DEVNULL, env=claude_env())
                said = _AUTH_ESCAPES.sub("", (r.stdout + r.stderr)).strip().splitlines()
                text = "signed out of Claude" if r.returncode == 0 else \
                    f"sign-out failed: {said[-1] if said else r.returncode}"
            except (OSError, subprocess.SubprocessError) as e:
                text = f"sign-out failed: {e}"
            GLib.idle_add(lambda: (self.note(text), self.signed_in()) and False)
        threading.Thread(target=run, daemon=True).start()

    def login_prompt(self, why):
        """A turn that failed for want of signing in: said, with Sign in (sign_in); another machine's: where to."""
        if self.remote is not None and not self.remote.local:
            self.note(wording.say("sign_in.not_signed_in_there", host=remote_name(self.remote)))
            return
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("perm-card")
        card.append(sel_label(f"<b>{html.escape(wording.say('sign_in.not_signed_in'))}</b>  <span alpha='60%'>"
                              f"{html.escape(why[:200])}</span>"))
        btn = Gtk.Button(label=wording.say("sign_in.sign_in"), halign=Gtk.Align.START)
        btn.add_css_class("suggested-action")
        btn.connect("clicked", lambda *_: (card.get_parent() and card.get_parent().remove(card), self.sign_in()))
        card.append(btn)
        self.add(card)
        self.stick_bottom = True

    def permission_card(self, rid, name, inp, tool_use_id):
        """Asks whether to allow a call: in the call's own card, when it's in the chat (ToolCard.ask); else (no card for
        it yet, or it's a subagent's, drawn elsewhere) in a box of its own here, saying what it would run."""
        card = self.tool_cards.get(tool_use_id)
        if card is not None and tool_use_id in self.tool_parent:
            card = None
        row = Gtk.Box(spacing=6)
        box = None
        asking = name == "AskUserQuestion" and isinstance(inp.get("questions"), list)
        if card is None:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.add_css_class("perm-card")
        if card is None and asking:
            box.append(sel_label(f"<b>{html.escape(wording.say('asks.has_question', name='Claude'))}</b>"))
            box.append(row)
        elif card is None:
            title, source = tool_display(name)
            what = f"{source}: {title}" if source else title
            summary = summarize_tool(name, inp)
            box.append(sel_label(f"<b>{html.escape(wording.say('asks.allow_tool', tool=what))}</b>" +
                                 (f"  <span alpha='60%'>{html.escape(summary)}</span>" if summary else "")))
            box.append(tool_input_view(name, inp))
            box.append(row)
        waiting = card if card is not None else box
        self.waiting_cards.add(waiting)
        if self.reverting is not None:  # (asked mid-turn while a revert's pending: shown, once it's in the chat)
            GLib.idle_add(lambda: self.show_waiting(waiting) and False)
        self.update_session_state()
        proc = self.proc

        def decide(_b, behavior, always=False):  # (answering: always is the answers)
            if always is True:
                allowed = self.allowed_in(self.session_id)
                allowed.add(name)
                if self.remote is not None and self.session_id:  # (its host allows it too, a head attached or not:
                    # added to its list; refused, not always allowed here either)
                    def kept(reply, allowed=allowed):
                        if reply.get("op") == "error":
                            allowed.discard(name)
                            self.note(wording.say("asks.not_always_allowed", tool=tool_display(name)[0],
                                                  why=baudelib.wire.said(reply)))
                    self.remote.request({"op": "always_allow", "session": self.session_id, "add": [name]}, kept)
            proc.asks.pop(rid, None)
            if behavior == "allow":
                proc.respond(rid, {"behavior": "allow", "updatedInput": inp})
            elif behavior == "answer":
                proc.respond(rid, {"behavior": "allow", "updatedInput": {**inp, "answers": always}})
                if tool_use_id in self.tool_cards:  # (its card says what was answered)
                    self.tool_cards[tool_use_id].set_input({**inp, "answers": always})
                behavior, always = "allow", False
            elif asking:
                proc.respond(rid, {"behavior": "deny", "message": "The user chose not to answer. Carry on without "
                                   "their answers, or ask in your reply."})
            else:
                proc.respond(rid, {"behavior": "deny", "message": "The user denied this tool use."})
            if card is not None:
                card.answered()
            else:
                box.get_parent() and box.get_parent().remove(box)
            self.waiting_cards.discard(waiting)
            self.update_session_state()
            decision = ("always allowed" if always else "allowed") if behavior == "allow" else \
                "not answered" if asking else "denied"
            if tool_use_id in self.tool_cards:
                self.tool_cards[tool_use_id].set_decision(decision)  # keeps tool groups together
            elif behavior != "allow":  # (allowed, it runs: nothing to say)
                self.note(wording.say("tool_calls.denied_tool" if decision == "denied" else "tool_calls.not_answered_tool",
                                      tool=name))

        choices = [(wording.say("common.allow"), "allow", False, "suggested-action"),
                   (wording.say("asks.always_allow", tool=tool_display(name)[0]), "allow", True, None),
                   (wording.say("common.deny"), "deny", False, "destructive-action")]
        if model_program(self.model) != "claude" and not self.host_lists("always_allow"):  # (Apple's model, or one on
            del choices[1]  # this Mac: each call asked, no always; a host's that keeps it, as a phone's: offered)
        if asking:  # (claude's questions: answered in a form, sent back as the call's answers)
            choices = []
            row.set_orientation(Gtk.Orientation.VERTICAL)
            row.append(QuestionForm(inp["questions"], lambda answers: decide(None, "answer", answers),
                                    lambda: decide(None, "deny")))
        for label, beh, always, css in choices:
            b = Gtk.Button(label=label)
            if css:
                b.add_css_class(css)
            b.connect("clicked", decide, beh, always)
            row.append(b)
        if card is not None:
            # (however it stops asking, answered or not, it isn't waiting on you any more; the window held weakly, as the
            # card's listeners outlive its handlers)
            me = weakref.ref(self)
            card.listeners.append(lambda c: c.asking is None and (proc.asks.pop(rid, None), True) and (w := me()) is not None
                                  and c in w.waiting_cards and (w.waiting_cards.discard(c), w.update_session_state()))
            card.ask(row)
        else:
            self.add(box)
        self.stick_bottom = True


class BaudeApp(Adw.Application):
    def __init__(self, opts):
        # a traced run (a benchmark) is its own app: never handed to the baude you're using
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE if TRACE else Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.opts = opts
        self.updater = None  # (update.Updater: started with the first window)

    def do_shutdown(self):
        save_info_cache(force=True)
        if _NETWORK:
            _NETWORK[0].save()  # (the network's sessions as last seen: dead ones are shown from it next time)
        Adw.Application.do_shutdown(self)

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name("Baude")
        if sys.platform == "darwin":
            for fn in (macos_dock_icon, macos_appearance):
                try:
                    fn()
                except Exception as e:  # (Python's Dock tile isn't worth not starting over)
                    print(f"baude: {fn.__name__}:", e, file=sys.stderr)
        else:
            widgets.notify_as(logo_icon())
        ensure_icons()
        pointers.start()
        install_debug_hooks(self)
        pickers.press_opens_dropdowns()  # (a dropdown's list open as the mouse goes down on it)
        theming.theme_css()  # (whose stylesheet Baude's look is drawn over: libadwaita's, or a GTK_THEME's)

    def do_activate(self):
        trace("activate")
        Gtk.Window.set_default_icon_name(APP_ID)
        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        restore, self.opts.restore = take_restart_state(getattr(self.opts, "restore", None)), None  # (once)
        if restore is not None and "windows" not in restore:
            restore = {"windows": [restore]}  # (one window's, from before every window came back)
        BaudeWindow.restore_drafts((restore or {}).get("drafts"))  # (before the windows: each takes its chat's)
        for i, state in enumerate((restore or {}).get("windows") or [None]):
            opts = argparse.Namespace(**vars(self.opts))
            if state:
                opts.cwd, opts.restore = state.get("cwd") or opts.cwd, state
            win = BaudeWindow(self, opts)
            trace("window built")
            if TRACE and i == 0:
                win.connect("map", lambda *_: trace("window mapped"))
                clock_id = []

                def painted(clock):
                    trace("first frame")
                    clock.disconnect(clock_id[0])
                win.connect("realize", lambda w: clock_id.append(
                    w.get_frame_clock().connect("after-paint", painted)))
            update.show(win, restore)  # (in the order they were in: the last one you were in, last, on top)
        theming.flat_popovers(Gdk.Display.get_default())  # (X11 under picom: its shadow is round the whole popover)
        if restore:
            update.restarted(win, restore, "Baude")
        if self.updater is None and not TRACE:
            self.updater = update.Updater("baude", self.idle, self.restart, blocked=self.update_waiting)
        # (whether this claude knows --chrome, looked up now, off the main loop: a chat starts without waiting on it)
        threading.Thread(target=lambda: claudelib.has_flag(claudelib.claude_exe(), "--chrome"), daemon=True).start()

    def windows(self):
        """Baude's windows, the one you were in last first."""
        return [w for w in self.get_windows() if isinstance(w, BaudeWindow)]

    def idle(self):
        """Every window idle (BaudeWindow.idle): a new version can be restarted onto."""
        return all(w.idle() for w in self.windows())

    # ---- Update: the sidebars' foot while a new version waits, Baude's (on its windows: restart_blockers)
    # or This Mac's host process's (on its commands: info's update)
    def update_waiting(self, waiting):
        self.update_held = waiting
        self.show_update_bars()

    def update_reasons(self):
        """What the updates waiting wait for, said; None if none is waiting."""
        why, waiting = [], False
        if getattr(self, "update_held", False) and self.updater is not None and self.updater.ready:
            waiting = True
            why += [w for win in self.windows() for w in win.restart_blockers()]
        host = local_host()
        held = (host.info.get("update") if host is not None else None) or None
        if held:
            waiting = True
            why += [f"{THIS_MACHINE}'s host process: {c.get('what') or 'a command'}"
                    for c in held.get("waiting") or []] or [f"{THIS_MACHINE}'s host process"]
        return list(dict.fromkeys(why)) if waiting else None

    def show_update_bars(self):
        why = self.update_reasons()
        for w in self.windows():
            if (bar := getattr(w.sidebar, "update_bar", None)) is not None:
                bar.say(why)

    def update_now(self, win):
        """The bar clicked: restarted now. Nothing running: straight away; what's running, stopped: said first, and
        asked."""
        why = [w for w in (self.update_reasons() or []) if w != wording.say("window.blocker_using")]

        def go():
            host = local_host()
            if host is not None and host.info.get("update"):
                host.request({"op": "quit", "restart": True})  # (it comes back as a chat wants it: local_server)
            if self.updater is not None and self.updater.ready:
                u = self.updater
                self.restart({"from": u.started, "to": u.ready, "commits": update.new_commits(u.started, u.ready, u.root)})
        if not why:
            return go()
        dlg = Adw.AlertDialog(heading=wording.say("window.restart_now_heading"), default_response="cancel",
                              close_response="cancel",
                              body="The new version is picked up now. These are stopped, and their chats can be "
                                   "continued after:\n" + "\n".join(f"• {w}" for w in why))
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("go", wording.say("window.restart_now"))
        dlg.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "go" and go())
        dlg.present(win)

    def restart(self, updated=None):
        """This process replaced by a fresh baude (a new version picked up) that puts back every window as it was
        (restart_state), giving the front back to the app you were in (update.restarted): ☰ → Restart Baude, or a
        new version found (update.Updater; updated, what by). A session being watched isn't touched, and one of
        baude's own is resumed after (a turn it was running is cut short, as Ctrl+C would)."""
        wins = self.windows()[::-1]  # (the one you were in last, last: put back last, on top)
        path = restart_state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"windows": [w.restart_state() for w in wins], "drafts": BaudeWindow.drafts_state(),
                       "front": update.front_app(), "updated": updated}, f)
        for w in wins:
            if w.proc:
                w.proc.on_exit = lambda *_: None
                w.proc.kill()
            for p in list(w.background.values()):  # (chats left mid-turn: their turns end here)
                w.let_go_background(p)
        save_info_cache(force=True)  # (as quitting does: exec skips do_shutdown)
        if _NETWORK:
            _NETWORK[0].save()
        os.execv(sys.executable, launch_command("--cwd", wins[-1].run_cwd if wins else self.opts.cwd, "--restore", path))


def debug_report_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "baude", f"debug-{os.getpid()}.txt")


def window_state(win):
    """What a window is doing, for a debug report: enough to tell a stuck UI from a locked session."""
    now, proc = time.time(), getattr(win, "proc", None)
    alive = proc is not None and proc.p.poll() is None
    try:
        live = win.live_elsewhere()
    except Exception as e:  # noqa: BLE001  (a report must not fail)
        live = f"error: {e}"
    last = win.tail.last_written or 0
    history = getattr(win, "history", None)
    return {
        "title": win.get_title(), "session_id": win.session_id, "cwd": win.cwd, "run_cwd": win.run_cwd,
        "history_path": getattr(win, "history_path", None),
        "viewing read-only (resume_pending)": getattr(win, "resume_pending", None),
        "locked: live elsewhere": live,
        "other program last wrote, s ago": round(now - last, 1) if last else None,
        "its turn open (tail.turn_open)": win.tail.turn_open,
        "banner shown": win.banner.get_revealed(), "text box shown": win.composer.get_visible(),
        "busy": win.busy, "stick_bottom": win.stick_bottom,
        "claude": {"pid": proc.p.pid if proc else None, "alive": alive,
                   "args": proc.p.args if proc else None, "exit code": None if alive or not proc else proc.p.poll()},
        "pending control requests": list(win.pending),
        "tasks": {k: {f: t.get(f) for f in ("task_type", "status", "description")} for k, t in win.tasks.items()},
        "history items not drawn yet": history.count() if history else None,
        "tool cards": len(win.tool_cards), "zoom": win.zoom,
    }


def install_debug_hooks(app):
    """Inspect a running Baude without a debugger (ptrace is usually off limits):
       kill -USR1 <pid>   every thread's Python stack, written from the signal handler itself, so it
                          comes even when the main thread is stuck
       kill -USR2 <pid>   each window's state (session, lock, busy, claude, tasks), from the main loop
    Both append to ~/.cache/baude/debug-<pid>.txt."""
    import faulthandler
    import signal
    path = debug_report_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        report = open(path, "a", buffering=1)  # (kept open: faulthandler writes to its descriptor)
    except OSError:
        return None
    faulthandler.register(signal.SIGUSR1, file=report, all_threads=True)

    def state():
        report.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  Baude state (pid {os.getpid()})\n")
        for win in app.get_windows():
            if isinstance(win, BaudeWindow):
                report.write(json.dumps(window_state(win), indent=2, default=str) + "\n")
        return True  # (keep listening)
    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix
        GLibUnix.signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR2, state)
    except (ImportError, ValueError):  # older GLib
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR2, state)
    return path


def main():
    ap = argparse.ArgumentParser(prog="Baude", description=__doc__.splitlines()[0])
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--model", help="alias (opus, sonnet, ...) or full model name; default: the CLI's")
    ap.add_argument("--resume", help="session id to resume")
    ap.add_argument("--restore", help=argparse.SUPPRESS)  # state left by "Restart Baude"
    inst = ap.add_mutually_exclusive_group()
    inst.add_argument("--install", action="store_true",
                      help=wording.say("install.install_help", app="Baude", where=LAUNCHER.where))
    inst.add_argument("--uninstall", action="store_true",
                      help=wording.say("install.uninstall_help", app="Baude", where=LAUNCHER.where))
    opts = ap.parse_args()
    if opts.install:
        LAUNCHER.install()
        print("\n".join(wording.say("install.installed", path=p) for p in LAUNCHER.paths()))
        return 0
    if opts.uninstall:
        removed = LAUNCHER.uninstall()
        print("\n".join(wording.say("install.removed", path=p) for p in removed) or
              wording.say("install.not_installed", app="Baude"))
        return 0
    return BaudeApp(opts).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
