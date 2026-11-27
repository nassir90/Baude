"""windowsignal — Signal in a Baude window (WindowSignal): its links per chat, and background tasks rebuilt from a
transcript.
"""
import html
import os
import re
import weakref
import time
import urllib.parse
from baudelib.transcripts import find_session, history_items, ref_epoch
from baudelib import providers as providerslib  # noqa: E402  (where the models besides Claude's run: their providers)
from baudelib.client import chat_name, seen_past, unseen  # noqa: E402
from baudelib import wire  # noqa: E402  (what a host takes: seen)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from lookup import REF_KINDS, Ref
from lookupkube import pg_servers_cached
from lookupgit import _git

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

import fixview  # noqa: E402  (☰ → Ongoing Feedback: the fixes, approved or rejected)
from livefeed import read_appended  # noqa: E402
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
import telemetryview  # noqa: E402  (its chats' activity, told to this machine's host)
import markdownview  # noqa: E402
import chatfind  # noqa: E402
from markdownview import hover_uri  # noqa: E402,F401
from widgets import Ticker  # noqa: E402,F401
from widgets import (  # noqa: E402,F401
    ZOOM_STEP, discard, load_settings, pretty_path, save_settings, themed_icon, toast, walk,
)
from refkinds import REF_SCHEME, ref_hashlike, ref_span  # noqa: E402,F401
from markdownview import ARTIFACT_TITLES, MarkdownView, REF_ABSENT_WHY, open_code, open_in_editor  # noqa: E402,F401
from codeview import CODE_LIMIT, CodePage, CodeView, in_code  # noqa: E402,F401
from schemaview import DB_POOL, db_locate  # noqa: E402,F401
from gitview import GIT_POOL  # noqa: E402,F401
from signalrefs import db_prefetch, pg_forget, tool_dir  # noqa: E402
from signalcode import REF_LSP_PACKAGES, lsp_dir  # noqa: E402
from signalnames import Signal  # noqa: E402
from toolcards import artifact_from_tool  # noqa: E402
from sessionlist import bot_picture, network, remote_info  # noqa: E402
from refpopovers import (  # noqa: E402
    LookupPopover, RefPopover, chat_checkout, in_chat, ref_busy, ref_column_table, ref_kind_label, ref_run_names,
    ref_status_text, ref_status_tip,
)


TURNS = {}  # (a notification of a host's chat's turn's end, by its key, while it's up: {remote, sid, uuid (its
# result's; None: not known), size (its transcript's as it ended: None till its host says), posted (monotonic)})


def turn_size(note):
    """The size a notification's turn's end was at (its transcript's then), once its host has said it: by its turn's
    uuid, as turn_ended or a listing says it, or, its uuid not known, the first turn's end told after it was posted."""
    if note["size"] is not None:
        return note["size"]
    r, sid = note["remote"], note["sid"]
    told, listed = r.ended.get(sid), (r.sessions.get(sid) or {}).get("ended")
    for end in (told, listed):
        if isinstance(end, dict) and note["uuid"] is not None and end.get("uuid") == note["uuid"]:
            note["size"] = end["size"]
    if note["size"] is None and note["uuid"] is None and told is not None and told["at"] >= note["posted"]:
        note["size"] = told["size"]
    return note["size"]


def withdraw_seen():
    """(the network changed: a host listed its sessions) Each notification of a turn's end taken back once its host
    says that end, or one after it, has been seen: on a head anywhere on the mesh, this one too (wire's Seen)."""
    for key, note in list(TURNS.items()):
        if not widgets.notified(key):  # (clicked, or taken back already)
            TURNS.pop(key, None)
            continue
        size = turn_size(note)
        if size is not None and seen_past(note["remote"].sessions.get(note["sid"]), size):
            TURNS.pop(key, None)
            widgets.withdraw_notification(key)


class WindowSignal:
    """Signal in a window, per chat; background tasks rebuilt from a transcript."""

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

    def in_front_anywhere(self, session_id, r):
        """Is the chat in front of you on any machine of the mesh (this one's too: Record's, the phone's), as each
        host says of its own machine's apps (network's in_front, from their telemetry)? r: the host it's on, if it's a
        host's chat; else it's this machine's host's."""
        if not session_id or session_id.startswith("new-"):
            return False
        host = r if r is not None else network().here_alive()
        host_id = host is not None and (host.info.get("id") or host.key)
        return bool(host_id) and network().in_front(host_id, session_id)

    def tell_desktop(self, session_id, body, title=None, info=None, turn=None):
        """A turn's end, or a question it asks, in a chat you aren't looking at (looking_at: in another window or
        app, behind the Feedback tab, or left mid-turn behind another chat), as a desktop notification
        (widgets.desktop_notify): a click on it brings this window up with that chat open. info: the chat's, if it's
        known (a network chat's, which find_session doesn't know). The sidebar's dot says it too. Other programs'
        chats (Claude Desktop, a terminal) are theirs to say: they notify on their own. Nor one in front of you on
        another machine of the mesh (in_front_anywhere). turn: a host's chat's turn's
        end it's of ({uuid, size?}, as its host knows it), taken back as that's seen on any head of the mesh
        (withdraw_seen), and not posted if it's been seen already."""
        if self.looking_at(session_id):
            return
        here = session_id == self.session_id
        if info is None and session_id and not session_id.startswith("new-") and not here:
            info = find_session(session_id)
        if info is None and here and (r := getattr(self, "remote", None)) is not None \
                and (listed := r.sessions.get(session_id)) is not None:
            info = remote_info({**listed, "server": r})  # (the open chat's, as its host lists it: a Bot's, its picture)
        if title is None:
            title = self.title_label.get_text() if here else chat_name(info or {})
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
        key, title = self.chat_key(session_id), title or wording.say("common.untitled")
        r = (info or {}).get("remote") or (getattr(self, "remote", None) if here else None)
        if self.in_front_anywhere(session_id, r):
            return
        TURNS.pop(key, None)  # (one in its place: a question it asks, taken back as it's answered)
        if turn is not None and r is not None:
            note = {"remote": r, "sid": session_id, "posted": time.monotonic(),
                    "uuid": turn.get("uuid") if wire.is_id(turn.get("uuid") or "") else None,
                    "size": turn.get("size") if isinstance(turn.get("size"), int) else None}
            if (size := turn_size(note)) is not None and seen_past(r.sessions.get(session_id), size):
                return  # (seen already, on another head)
            if withdraw_seen not in network().listeners:  # (once, for the app)
                network().listen(withdraw_seen)
        opens = None  # (a local chat's: what starts Baude on it, for a click once this process is gone (macOS))
        if session_id and not session_id.startswith("new-") and (info or {}).get("remote") is None:
            cwd = (info or {}).get("cwd") or (self.cwd if here else None)
            opens = [*(["--cwd", cwd] if cwd else []), "--resume", session_id]
        if not (info or {}).get("bot") or not widgets.notification_pictures():  # (osascript's take none)
            widgets.desktop_notify(key, title, body, clicked, opens=opens)
            if turn is not None and r is not None:
                TURNS[key] = note
            return

        def pictured(tex):
            if (win := me()) is None or win.looking_at(session_id) or win.in_front_anywhere(session_id, r):  # (seen
                return  # while its picture came)
            if turn is not None and r is not None:
                if (size := turn_size(note)) is not None and seen_past(r.sessions.get(session_id), size):
                    return  # (seen on another head while its picture came)
                TURNS[key] = note
            png = tex.save_to_png_bytes().get_data() if tex is not None else None
            widgets.desktop_notify(key, title, body, clicked, picture=png and widgets.notification_picture(
                png, widgets.notify_logo()), opens=opens)
        bot_picture(info, pictured)

    def seen_chat(self):
        """The open chat seen (this window's the one you're in, on its chat's tab, or the chat's just opened): its
        notification gone, and a host's chat's host told, if you're there to see it (acted, tell_seen)."""
        telemetryview.look(self)
        if self.is_active() and self.tabs.current() is self.tabs.get("chat"):
            widgets.withdraw_notification(self.chat_key(self.session_id))
            self.acted()

    def acted(self):
        """Something done in this window by hand (focus come to it, a tab switched to, a key, a scroll): where the
        system can't say whether anyone's at the machine (widgets.user_present), what lets the chat's end on screen
        count as seen (tell_seen)."""
        self.acted_at = time.monotonic()
        self.tell_seen()

    def chat_at_end(self):
        """Whether the chat's scrolled all the way down: its end on screen."""
        adj = self.scroller.get_vadjustment()
        return adj.get_value() + adj.get_page_size() >= adj.get_upper() - 1

    def shown_upto(self, r, sid):
        """How much of a host's chat's transcript this window has drawn: as far as it's read it (Tail), or, its agent
        attached live, all that's fetched of it (what it says is drawn as it comes)."""
        tail = getattr(self, "tail", None)
        if self.resume_pending and tail is not None and tail.path == r.mirror(sid):
            return tail.offset
        return r.mirrored(sid)

    def tell_seen(self):
        """The open chat seen by you, a host's: its host told its last turn's end is seen here, once (each end:
        seen_told). Seen means its window's the one you're in, on its chat's tab, scrolled to its end, that end drawn,
        and you there: input in the last minute, the screen not locked, the display awake (widgets.user_present);
        where that can't be told, something done by hand in this window since the end came (acted). Idle at it,
        looked at again every IDLE_LOOK_S till you're back. Its host tells every head, each of which takes back its
        notification of it and its dot (wire's Seen)."""
        r, sid = getattr(self, "remote", None), self.session_id
        if r is None or not sid or r.state != "alive" or not wire.takes(r.info, "seen") or not self.is_active() \
                or self.tabs.current() is not self.tabs.get("chat"):
            return
        listed = r.sessions.get(sid)
        if not unseen(listed):
            return
        end = listed["ended"]
        told = (r.key, sid, end.get("uuid"))
        if getattr(self, "seen_told", None) == told:
            return
        if getattr(self, "end_heard", (None,))[0] != told:  # (when this window first knew of that end)
            self.end_heard = (told, time.monotonic())
        upto = self.shown_upto(r, sid)
        if upto < end.get("size", 0) or not self.chat_at_end():
            return
        present = widgets.user_present()
        if present is False:
            self.look_again()
            return
        if present is None and getattr(self, "acted_at", 0.0) < self.end_heard[1]:
            return
        self.seen_told = told

        def answered(reply):
            if reply.get("op") != "seen_set" and getattr(self, "seen_told", None) == told:
                self.seen_told = None  # (tried again as it's next looked at)
        r.request({"op": "seen", "session": sid, "upto": upto}, answered)

    def scrolled_chat(self):
        """The chat scrolled: by hand (acted), or as it follows what's new (its end may be on screen now)."""
        follow = getattr(self, "follow", None)
        if follow is not None and not follow.auto and follow.glide is None:
            self.acted()
        else:
            self.tell_seen()

    IDLE_LOOK_S = 5

    def look_again(self):
        """The chat's end on screen with nobody at the machine: tell_seen again in IDLE_LOOK_S, while the window's the
        one you're in (one wait at a time)."""
        if getattr(self, "idle_look", None) is not None:
            return

        def again():
            self.idle_look = None
            if not getattr(self, "let_go_done", False) and self.is_active():
                self.tell_seen()
            return False
        self.idle_look = GLib.timeout_add_seconds(self.IDLE_LOOK_S, again)

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
        self.docker.close()
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
        ref.unfollow(self)
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
        so, with its definition and Try Again, rather than open a database it isn't in. The popover only shows
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
        Found, with Open, or Not found, with Try Again."""
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
