"""windowproc — a Baude window's claude (WindowProc): control requests, attachments, the process's life, a Claude
Desktop chat left mid-turn, swapping chats, read-only sessions, the UI's events, the queue and what a host held;
and own_writes, a chat's claude's writes taken as every window's own.
"""
import os
import re
import shlex
import sys
import threading
import time
import uuid
from baudelib import claude as claudelib  # (its ClaudeProc: Baude's is it, on the GTK main loop)
from baudelib.transcripts import find_session
import baudelib.client  # noqa: E402  (servers on the network and their sessions: see baudelib/client.py)
import baudelib.hosts  # noqa: E402  (this machine's host process, started when it's wanted: see baudelib/hosts.py)
from baudelib import models as modelslib  # noqa: E402  (what runs a model: claude, apple-claude or pi-claude)
from baudelib import providers as providerslib  # noqa: E402  (where the models besides Claude's run: their providers)
from baudelib import worktree as worktreelib  # noqa: E402  (a chat's worktree that's gone: made again from its branch)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from baudelib.models import model_program  # noqa: E402,F401

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

import meta  # noqa: E402  (Meta, the helper in the side panel: Record's too; see meta.py)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
import telemetryview  # noqa: E402  (its chats' activity, told to this machine's host)
import composerview  # noqa: E402
from composerview import ComposerMarkdown  # noqa: E402,F401
from chatparts import (  # noqa: E402  (a message of yours, its revert button: Meta's and side questions' too)
    HeldTurn, QueuedTurn, SenderTurn, UserTurn,
)
from widgets import ZOOM_STEP, discard, load_settings, save_settings, toast  # noqa: E402,F401
from markdownview import ARTIFACT_TITLES, MarkdownView  # noqa: E402,F401
from toolcards import CUT_CONTINUE, ToolCard, tool_label, track_commands  # noqa: E402
from chatinput import Attachment, attachment_widget, clipboard_has  # noqa: E402
from sessionlist import (  # noqa: E402
    ClaudeProc, HERE_HOST, RemoteProc, THIS_MACHINE, is_desktop, local_host, main_folder, on_main, remote_info,
    remote_name, remote_path, turn_said,
)


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
    from baudewindow import BaudeWindow  # (here: baudewindow imports this module)
    app = Gio.Application.get_default()
    for w in app.get_windows() if app is not None else []:
        if isinstance(w, BaudeWindow):
            w.own_mtimes[proc.path] = max(mtime, time.time() + 2) if exiting else mtime


class WindowProc:
    """A window's claude: control requests, attachments, its process, chats swapped, the queue."""

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
                (wording.say("subscriptions.account"), self.chat_account() or wording.say("subscriptions.cli_default")),
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
        if self.remote is not None:  # (a host's chat: its host asks its agent, ask_context, and lists what it said)
            return self.show_listed_context()
        model = self.model  # (the model the answer's for: another picked while it's asked for isn't, on_model_changed)
        self.request({"subtype": "get_context_usage"},
                     lambda usage: (self.remember_context(model, usage), self.show_context(usage)))

    def show_listed_context(self, again=False):
        """A host's chat: how much of its window it's used, as its host lists it (Session's context), shown as it
        changes; again: shown whatever was shown before (another chat opened)."""
        listed = (self.remote.sessions.get(self.session_id) or {}) if self.remote is not None and self.session_id else {}
        usage = listed.get("context")
        if not isinstance(usage, dict) or (not again and (self.session_id, usage) == getattr(self, "context_listed", None)):
            return
        self.context_listed = (self.session_id, usage)
        self.remember_context(self.model, usage)
        self.show_context(usage)
        sid = self.session_id  # (the tooltip's categories: not listed, asked of its host, which has them kept)
        self.request({"subtype": "get_context_usage"},
                     lambda said: self.session_id == sid and said.get("categories") and self.show_context(
                         {**usage, "categories": said["categories"]}))

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
                self.note(wording.say("chat_notes.cant_attach_file", path=path))

    def attach_texture(self, texture):
        try:
            self.add_attachment(Attachment.from_texture(texture))
        except (GLib.Error, ValueError) as e:
            self.note(wording.say("chat_notes.cant_attach_image", why=str(e)))

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
                self.note(wording.say("chat_notes.paste_failed", why=e.message))

        def got_text(c, res):
            try:
                text = c.read_text_finish(res) or ""
            except GLib.Error as e:
                text = None
                if not image:
                    self.note(wording.say("chat_notes.paste_failed", why=e.message))
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
                    self.note(wording.say("chat_notes.paste_failed", why=e.message))
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
        tip = wording.say("status_bar.host_tip") if here else wording.say("status_bar.host_tip_at",
                                                                         address=self.remote.host)
        account = self.chat_account()
        self.host_label.set_tooltip_text(tip + ("\n" + wording.say("subscriptions.account_line", name=account)
                                                if account else ""))

    def chat_account(self):
        """The name of the subscription account the chat's agent runs under on its host, or None: the CLI's own
        sign-in."""
        return (self.chat_account_info() or {}).get("name")

    def subscription_off(self):
        """Is the subscription the chat runs on disabled on its host (its listing's account_disabled; a chat this
        process runs claude for itself: as this machine keeps it)?"""
        if self.remote is not None:
            return bool(self.session_id and (self.remote.sessions.get(self.session_id) or {}).get("account_disabled"))
        if not self.session_id or model_program(self.model) != "claude":
            return False
        import sqlite3
        from baudelib import accounts
        try:
            return accounts.disabled(accounts.usage_key("claude", accounts.claude_session_profile(self.session_id)))
        except (OSError, sqlite3.Error):
            return False

    def chat_account_info(self):
        """The subscription account the chat's agent runs under on its host (Account's fields: its listing's; a chat
        this process runs claude for itself: as this machine keeps it), or None: the CLI's own sign-in."""
        if self.remote is not None and self.session_id:
            account = (self.remote.sessions.get(self.session_id) or {}).get("account")
            return account if isinstance(account, dict) else None
        if self.remote is None and model_program(self.model) == "claude":
            import sqlite3
            from baudelib import accounts
            try:
                return accounts.account(accounts.claude_session_profile(self.session_id))
            except (OSError, sqlite3.Error):
                return None
        return None

    def usage_account(self):
        """Whose usage ☰'s usage row shows (subscriptionpage.MenuUsage): (where, provider, account id, "" for the CLI's
        own sign-in) of the chat's on a Claude or Codex model: its host's, its model's provider's, the account its agent
        runs under (None for a chat its host doesn't list yet: the one a new chat there takes); else None, as with no
        chat."""
        program = model_program(self.model or "default")
        if program not in ("claude", "codex", "antigravity"):
            return None
        if self.remote is not None:
            listed = self.remote.sessions.get(self.session_id) if self.session_id else None
            profile = ((listed.get("account") or {}).get("id") or "") if isinstance(listed, dict) else None
            return self.remote.where, program, profile
        if program == "claude" and self.session_id:
            return {"via": "local"}, program, (self.chat_account_info() or {}).get("id") or ""
        return None

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
        (take_up), as a host's session does, so a restart or an update of Baude's never stops it. Not a Desktop chat this
        machine's host has taken up (for another machine's head): that host's claude is its one writer here."""
        return self.remote is None and (self.desktop_chat or is_desktop(self.session_id)) and not self.host_took_up()

    def host_took_up(self):
        """Has this machine's host taken up the viewed chat (it's one of its sessions, not one it only lists)?"""
        return bool(self.session_id) and (r := local_host()) is not None and self.session_id in r.sessions

    def start_proc(self):
        if getattr(self, "agent_draft", None) is not None:
            self.proc = None
            target = self.agent_draft.get("host")
            if target is not None:
                self.sync_models((target.info or {}).get("models", []))
            self.update_status()
            return
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
                    self.note(wording.say("chat_notes.resumed_from", reason=self.resume_reason[1]))
                self.resume_reason = None
            self.pending.clear()
        except OSError as e:
            self.note(wording.say("chat_notes.failed_to_start", why=str(e)))
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
                self.proc = RemoteProc(self.on_msg, self.on_exit, r.where, self.session_id,
                                       live=not self.resume_pending,  # (this machine's: as Baude's own claude would
                                       env=claudelib.claude_env() if r.mine else None,  # have had it)
                                       always_allow=sorted(self.allowed_in(self.session_id) -
                                                           self.host_allowed.get(self.session_id, set())))
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
                    self.note(f"{name} wouldn't open this chat: {e}" if refused else wording.say("chat_notes.couldnt_reach", host=name, why=str(e)))
                    self.attach_said = (self.session_id, kind)
        if self.proc is not None:
            self.attach_said = self.missing_session = self.missing_reason = self.retaken = None
            self.host_allowed[self.session_id] = set(self.proc.listing.get("always_allow") or ())  # (what its host
            self.allowed_in(self.session_id).update(self.host_allowed[self.session_id])  # allows it: another head's
            # too; not sent it again as this attaches again)
            self.apply_read_only()  # (one its host lets this head only read: no text box)
            self.reattached_draft()  # (what's typed while it was away, or what its host's had since)
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
            if getattr(self.proc, "login", None) is not None:  # (its sign-in, as its host has it: its card)
                self.h_login({"login": self.proc.login, "attached": True})
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
            if not child.has_css_class("sysnote") and child is not getattr(self, "cut_row", None) and \
                    child is not getattr(self, "resume_row", None):
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
            return RemoteProc(on_msg, on_exit, r.where, reply["session"]["id"], live=True,
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
        self.stop_left = bool(getattr(self.proc, "stop_asked", False))  # (a stop asked of its turn: queue_state)
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
                                  title=baudelib.client.chat_name(listed), info=info,
                                  turn=None if state == "waiting" else listed.get("ended"))

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
                                                        tool=tool_label(req["tool_name"]) if req.get("tool_name")
                                                        else wording.say("asks.a_tool")))
        elif t == "result":
            self.tell_desktop(proc.key, turn_said(msg))
            if not self.left_turn_ended(proc.key, lambda content, said, done: (proc.user(content, said),
                                                                               done({"op": "accepted"}))):
                self.let_go_background(proc)  # (else kept for the turn its queue starts)
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
        a revert written over) kept for it, or, gone, dropped with it. Its queue's kept with its draft (keep_draft), to
        be sent as its turn ends meanwhile (send_left_queues) or drawn again as it's opened (take_draft); a new chat's,
        whose draft any new chat takes, goes into its box as a queued message's revert puts it."""
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
        queue = self.queue_state()
        if queue and not key:
            self.put_ahead([t.text for t in self.queued if t.text], [a for t in self.queued for a in t.atts])
            queue = None
        elif queue and not gone:  # (whether its turn's going on as it's left: its end, seen as it's listed, sends it)
            self.QUEUE_SEEN[key] = "working" if self.busy or key in self.left_remote else None
        self.unqueue(list(self.queued), host=False)  # (another chat: this one's queue kept for it, not shown under the
        # next; its host's left there)
        self.held_after_revert = None
        for t in getattr(self, "held_turns", {}).values():  # (and what its host holds for it: the next one's own)
            if t.get_parent() is self.queue_box:
                self.queue_box.remove(t)
        self.held_turns = {}
        self.queue_box.set_visible(False)
        if not gone:
            self.keep_draft(key, queue)  # (the box and queue this chat's to come back to; the next chat's: take_draft)
        if carried is not None:
            self.set_box(*carried)
        self.cut_row = self.cut_pending = None  # (the next chat's own: cut_state)
        self.resume_row = self.resume_shown = None  # (and resume_state)
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

    def new_session(self, run_in=None, group=None, carry=False, agent=False):
        """A new chat in this folder; agent: keep the unstarted Bot child draft; run_in: the folder claude runs in, if not this one (a worktree picked with
        Open folder…). A worktree's chat's folder is its repository, so a new one from there starts in that. group:
        the group it's started in (New session with one group picked in ⋮), kept while it's a draft (another folder or
        model) and dropped once another chat's opened; None: the new chat's before it, if it was one, else none.
        carry: the open chat's gone (carry_on)."""
        if not agent:
            self.agent_draft = None
            self.agent_starting = False
            self.agent_start_attempt = None
            self.agent_send_pending = None
            self.composer_box.gate("agent-starting", False)
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
        self.input.grab_focus()

    # ---- read-only sessions (☰ → Read-only session): no text box, for a chat you only watch (tailing one with
    # Signal on, say); the status line, with the context it uses, stays. Remembered per session. An archived chat is
    # read-only till it's unarchived, its ☰ item checked and greyed. A session another program has (update_banner) is
    # read-only too while it does, and has no text box either: show_composer
    def read_only_ids(self):
        return [i for i in load_settings().get("read_only_sessions") or [] if isinstance(i, str)]

    def chat_archived(self):
        """Whether the open chat is archived: here, in Claude Desktop (sidebar.archived_ids) or on its host."""
        sid = self.session_id
        if not sid or getattr(self, "sidebar", None) is None:
            return False
        if self.remote is not None and (self.remote.sessions.get(sid) or {}).get("archived"):
            return True
        return sid in self.sidebar.archived_ids()

    def writes_chat(self):
        """Whether this head may change the open chat: no, where its host lets it only read it (access)."""
        return getattr(self, "remote", None) is None or getattr(getattr(self, "proc", None), "writes", True) is not False

    def set_read_only(self, on):
        """Make this session read-only, or not (and remember it for the session, once it has an id)."""
        if self.archived or not self.writes_chat():
            return  # (read-only till it's unarchived: the item's greyed)
        if self.session_id:
            ids = [i for i in self.read_only_ids() if i != self.session_id] + ([self.session_id] if on else [])
            save_settings(read_only_sessions=ids or None)
        self.apply_read_only(on)

    def apply_read_only(self, on=None):
        """Show or hide the text box as the session asks (on: as given, None: as remembered for it); archived, or one
        its host lets this head only read, it's hidden whatever was chosen, and ☰ → Read-only session is checked and
        greyed."""
        if on is None:
            on = bool(self.session_id) and self.session_id in self.read_only_ids()
        self.archived = self.chat_archived()
        forced = self.archived or not self.writes_chat()
        self.read_only = on or forced
        if (action := self.lookup_action("readonly")) is not None:
            action.set_state(GLib.Variant.new_boolean(self.read_only))
            action.set_enabled(not forced)
        self.show_composer()

    def show_composer(self):
        """The text box, there unless the session can't be written to: read-only by choice (☰ → Read-only session)
        or archived, or while another program has it (update_banner). Read-only is no text box at all, not a greyed-out one."""
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
            if code != "lost":
                self.follow_host_queue()  # (its host sending what's queued: attached again for it)
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
        """Enter (or Send): what's written sent, queued or done. The box emptied by it is a send of its own
        (enter_id): its host's draft emptied for it, and nothing written before it saved back (draft_typed)."""
        self.enter_id = str(uuid.uuid4())
        try:
            self.send_box()
        finally:
            self.enter_id = None

    def send_box(self):
        buf = self.input.get_buffer()
        typed = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip()
        if self.composer_box.gates and not self.busy and not (typed in ("/clear", "/new", "/cd", "/login", "/logout") or
                                                              typed.startswith(("/cd ", "/login "))):
            return  # (the context exhausted, a host gone or its connection dropped: only what Baude does itself goes)
        if typed == "/logout" and self.remote is not None and not self.remote.local:  # (another's claude signs out
            buf.set_text("")  # there)
            self.completer.hide()
            self.note(wording.say("sign_in.signs_out_there", host=remote_name(self.remote)))
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
            if self.chat_host() != HERE_HOST:  # (another host's chat: a new one on it, in this chat's folder)
                self.new_session_on(self.remote, (self.remote.sessions.get(self.session_id) or {}).get("cwd"), keep=False)
            else:
                self.switch_to(self.new_session)
            return
        if text == "/cd" or text.startswith("/cd "):
            self.change_cwd(text[3:].strip() or "~")
            return
        self.send_text(text)

    # ---- the queue: messages written while the agent's at a turn (QueuedTurn), under the conversation, grey. A turn
    # that ends sends them all, together as one message. A queued message's ↑ sends it with every one before it at
    # once, to the turn going on, which goes on with it, its subagents moved to the background, not stopped
    # (send_at_turn); its revert puts it back in the box; its ✕
    # drops it. They're the open chat's: another chat opened, they're kept with its draft
    # (clear_transcript), sent as its turn ends meanwhile (send_left_queues) and drawn again as it's opened (take_draft)
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
        self.queue_to_host(turn)  # (a host's chat's: kept there, for every head, and sent by it)

    def queue_state(self):
        """The queue as the chat's draft keeps it (keep_draft): its messages (text, attachments, and the uuid one put
        back is sent by), the one whose ↑ the turn's stopping for (its place), and whether the turn was stopped
        otherwise (hold: its end sends nothing); None if nothing's queued."""
        stopped = self.__dict__.pop("stop_left", False) or bool(self.busy and getattr(self.proc, "stop_asked", False))
        mine = [t for t in self.queued if not t.hosted]  # (its host's are kept there)
        if not mine:
            return None
        upto = self.queue_upto if self.queue_upto in mine else None
        return {"turns": [{"text": t.text, "attachments": list(t.atts), "said": t.said} for t in mine],
                "upto": mine.index(upto) if isinstance(upto, QueuedTurn) else None, "hold": stopped}

    def put_queue(self, queue):
        """A queue the chat's draft kept (queue_state) under the conversation again, after what's queued; its ↑ and
        stop for the turn going on, if one is."""
        if not queue:
            return
        before = len(self.queued)
        for t in queue["turns"]:
            turn = QueuedTurn(self, self.user_bubble(t["text"], t["attachments"]), t["text"], t["attachments"],
                              t.get("said"))
            self.queued.append(turn)
            self.queue_box.append(turn)
        if self.busy and queue.get("upto") is not None and self.queue_upto is None:
            self.queue_upto = self.queued[before + queue["upto"]]
        elif self.busy and queue.get("hold") and self.proc is not None:
            self.proc.stop_asked = True
        self.queue_box.set_visible(bool(self.queued))
        self.stick_bottom, self.restore_scroll = True, None

    def unqueue(self, turns, host=True):
        """turns out of the queue; out of its host's too (host), those it has."""
        if host:
            self.unqueue_on_host(turns)
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
        """Its ↑: it and the queued messages before it sent together, now: to the turn going on (send_at_turn), or as
        a turn of its own when none is. A revert waiting to be made is made first, as sending does (on_send): the turn
        it stops ends, and they're sent after it."""
        if turn not in self.queued or self.live_elsewhere():
            return
        if self.busy and self.reverting is None and self.proc is not None:
            self.send_at_turn(self.queued[:self.queued.index(turn) + 1])
        elif self.busy:
            if self.queue_upto is None or self.queued.index(turn) > self.queued.index(self.queue_upto):
                self.queue_upto = turn
            self.interrupt()
        else:
            self.queue_upto = turn
            self.flush_queue()

    def send_at_turn(self, turns):
        """Queued messages given the agent at its turn, together as one message, without stopping it: claude, Codex
        and pi take it in between the turn's steps (Apple's model, once its turn's over), and the turn goes on. Drawn
        as sent, where the chat's got to. Claude Code's commands and subagents running in the foreground are moved to
        the background (background_tasks, as its Ctrl+B), where they go on: the step waiting on them ends, so the turn
        takes the message in now, not once they're done. Moved once its host has taken the message: one it refuses
        moves nothing."""
        turns = list(turns)
        self.unqueue(turns)
        text = "\n\n".join(t.text for t in turns if t.text)
        atts = [a for t in turns for a in t.atts]
        said = turns[0].said if len(turns) == 1 and turns[0].said else str(uuid.uuid4())
        self.add(UserTurn(self, self.user_bubble(text, atts), text, said))
        self.stick_bottom, self.restore_scroll = True, None
        sid = self.session_id
        self.proc.user(self.user_content(text, atts), said,
                       lambda r: r.get("op") != "error" and self.session_id == sid and self.move_foreground())

    def flush_queue(self):
        """The queue sent up to queue_upto (all of it: "all"), as one message (send_text), once no turn's going."""
        upto, self.queue_upto = self.queue_upto, None
        if upto is None or self.busy or not self.queued:
            return False
        turns = [t for t in self.queued if not t.hosted] if upto == "all" else \
            self.queued[:self.queued.index(upto) + 1]  # (all: what its host hasn't, which it sends itself)
        if not turns:
            return False
        self.unqueue(turns)
        text = "\n\n".join(t.text for t in turns if t.text)
        buf = self.input.get_buffer()  # (sent from the box, as anything is: on_send; what was written there kept)
        draft, draft_atts = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip(), self.attachments
        buf.set_text(text)
        self.attachments = [a for t in turns for a in t.atts]
        self.send_said = turns[0].said if len(turns) == 1 else None  # (sent before, maybe taken: taken once)
        self.on_send()
        self.send_said = None
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
                  wording.say("held_send.while_no_host") if "host" in gates else
                  wording.say("held_send.while_disabled") if "disabled" in gates else wording.say("held_send.while_other"))
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
            toast(self, "It can't be sent: this chat couldn't be started")
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

    def send_text(self, text, again=None):
        """(on_send's) What was written, and what's attached, sent: to the host process for a chat of Baude's, taken up
        by it first (take_up; once it's started, if it isn't yet), or to the chat's claude here (a Desktop chat's).
        again: the attachments of a turn sent again (a sign-in card's Send again), in place of what's attached now."""
        self.cut_pending = None  # (written to: no longer a turn cut short; its host forgets its own as it's sent)
        if getattr(self, "agent_draft", None) is not None:
            return self.start_agent_draft(text, again)
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
                        self.send_text(text, again)
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
        if again is not None:  # (what the turn sent, no more or less: what's attached now is for the next)
            atts = again
        else:
            atts, self.attachments = ([], self.attachments) if going else (self.attachments, [])
        self.turn_sent = (text, list(atts))  # (sent again, if the turn fails for want of signing in: LoginCard's Send again)
        self.refresh_attach_strip()
        said = getattr(self, "send_said", None) or str(uuid.uuid4())  # (claude keeps it as the message's: what a
        self.send_said = None  # revert names, revert_to)
        self.add(UserTurn(self, self.user_bubble(text, atts), text, said))
        self.stick_bottom, self.restore_scroll = True, None  # (what you sent in view, whatever place was held)
        self.proc.stop_asked = False  # (a new turn: a stop asked of the last isn't this one's)
        self.proc.user(self.user_content(text, atts), said)
        telemetryview.turn_sent(self)
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
        self.draft_typed()  # (a host's chat's: saved to its host)
        if getattr(self, "sidebar", None) is not None and (begun := self.chat_begun()) != self.listed_begun:
            self.listed_begun = begun
            self.sidebar.fill()
