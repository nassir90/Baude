"""windowremote — a Baude window's sessions on the network (WindowRemote), steered through their hosts; and the
mesh's calls in a chat (MESH_CHAT_TOOLS, MeshCard).
"""
import base64
import html
import json
import os
import re
import weakref
import subprocess
import threading
import time
import urllib.parse
from baudelib.claude import claude_env, claude_exe, MODELS, spent_since
from baudelib import claude as claudelib  # (its ClaudeProc: Baude's is it, on the GTK main loop)
from baudelib.transcripts import find_session, history_items, session_file
import baudelib.client  # noqa: E402  (servers on the network and their sessions: see baudelib/client.py)
import baudelib.hosts  # noqa: E402  (this machine's host process, started when it's wanted: see baudelib/hosts.py)
from baudelib import worktree as worktreelib  # noqa: E402  (a chat's worktree that's gone: made again from its branch)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from baudelib.models import model_program  # noqa: E402,F401

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

import pointers  # noqa: E402  (the pointer's shape: Record's too)
from livefeed import LIVE_SECONDS  # noqa: E402
import meta  # noqa: E402  (Meta, the helper in the side panel: Record's too; see meta.py)
import metalook  # noqa: E402
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
from chatparts import fmt_tokens  # noqa: E402,F401
from widgets import load_settings, pretty_path, save_settings, toast  # noqa: E402,F401
from markdownview import MarkdownView, sel_label  # noqa: E402,F401
from startup import trace  # noqa: E402
from toolcards import (  # noqa: E402
    QuestionForm, StatusSlot, ToolCard, ended_by_stop, summarize_tool, tool_display, tool_input_view,
)
from chatinput import LoginCard, _AUTH_ESCAPES, needs_login  # noqa: E402
from sessionlist import (  # noqa: E402
    HERE_HOST, THIS_MACHINE, _HOST_APART, _LOCAL_SERVER, cached_session_info, chat_group, chat_groups, host_apart,
    host_key, is_desktop, local_host, made_groups, main_folder, network, on_main, remote_info, remote_name,
    session_cost, stopping_said, turn_said,
)
from refpopovers import chat_checkout  # noqa: E402


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
        from baudewindow import BaudeWindow  # (here: baudewindow imports this module)
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


class WindowRemote:
    """A window's sessions on the network, steered through their hosts."""

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
