"""windowdrafts — what each of a Baude window's chats keeps (WindowDrafts): its draft, its model, the inference
providers, the stream-json protocol, compaction, its title, background tasks and artifacts.
"""
import json
import os
import uuid
from baudelib.claude import EFFORT_LABELS, MODELS, MODES
from baudelib.transcripts import history_item, session_file, transcript_model
import baudelib.client  # noqa: E402  (servers on the network and their sessions: see baudelib/client.py)
import baudelib.hosts  # noqa: E402  (this machine's host process, started when it's wanted: see baudelib/hosts.py)
from baudelib import models as modelslib  # noqa: E402  (what runs a model: claude, apple-claude or pi-claude)
from baudelib import providers as providerslib  # noqa: E402  (where the models besides Claude's run: their providers)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from baudelib.models import model_program  # noqa: E402,F401
from lookup import signal_place

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

import meta  # noqa: E402  (Meta, the helper in the side panel: Record's too; see meta.py)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
from chatparts import SenderTurn, UserTurn  # noqa: E402
from widgets import discard, load_settings, save_settings  # noqa: E402,F401
from markdownview import ARTIFACT_TITLES, MarkdownView  # noqa: E402,F401
from codeview import CodePage  # noqa: E402,F401
from startup import LAUNCHER  # noqa: E402
from signalrefs import TOOL_NAMES  # noqa: E402
from toolcards import ARTIFACT_URL, STOP_SAID_RE, ThinkingView, ToolCard, api_error_label  # noqa: E402
from baudelib.hostlogin import login_error  # noqa: E402
from chatinput import Attachment, attachment_widget  # noqa: E402
from sessionlist import HERE_HOST, THIS_MACHINE, local_host, main_folder, network, on_main, remote_name  # noqa: E402
from sidequestion import SideQuestion  # noqa: E402


class WindowDrafts:
    """What each chat keeps: its draft, its model, providers, the protocol, compaction, its title,
    background tasks, artifacts."""

    # ---- each chat's draft: what's written in the box, its caret and what's attached, and its queue (queue_state),
    # kept for the chat as it's left for another (clear_transcript) and put back as it's opened again (take_draft);
    # every window's, kept over a restart (BaudeApp.restart). A new chat (nothing sent in it, nor anything of it listed
    # or saved) has the one draft, "": the next new chat opens with it, as its host drops a draft nobody's on and its id
    # goes with it. A queue kept for a chat not open is sent as its turn ends, as the open chat's is (send_left_queues)
    DRAFTS = {}  # draft_key -> {"text", "caret", "attachments", "queue"?: queue_state's}
    MAX_DRAFTS = 200
    QUEUE_SEEN = {}  # the id of a chat whose queue's kept here -> its state as last listed (send_left_queues)

    GONE = set()  # the ids of chats deleted or trashed: forget_draft

    @classmethod
    def forget_draft(cls, sid):
        """A chat deleted or trashed (here, in another window, or on its host: forget_gone): its draft gone with it, and
        nothing kept under its id from now on (draft_key)."""
        cls.GONE.add(sid)
        cls.DRAFTS.pop(sid, None)
        cls.QUEUE_SEEN.pop(sid, None)

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
        joined = {"text": text, "caret": caret, "attachments": atts}
        if first.get("queue") or then.get("queue"):  # (both queues, first's first; its ↑ and stop then's, if it has any)
            a, b = first.get("queue") or {"turns": []}, then.get("queue") or {"turns": []}
            joined["queue"] = {"turns": a["turns"] + b["turns"],
                               "hold": bool(b.get("hold") if b["turns"] else a.get("hold")),
                               "upto": len(a["turns"]) + b["upto"] if b.get("upto") is not None else a.get("upto")}
        return joined

    def keep_draft(self, key, queue=None):
        """The box's draft kept as key's, its queue with it (queue: queue_state's), with one another window left there
        (join_drafts), and the box emptied for the next chat's. An empty box and no queue keep nothing and drop
        nothing: a draft this window took is gone already (take_draft), and one there is another window's."""
        text, atts = widgets.typed_text(self.input), list(self.attachments)
        if text.strip() or atts or queue:
            draft = {"text": text, "caret": widgets.caret_of(self.input), "attachments": atts,
                     **({"queue": queue} if queue else {})}
            if (other := self.DRAFTS.pop(key, None)) is not None:
                draft = self.join_drafts(other, draft)
            self.DRAFTS[key] = draft
            while len(self.DRAFTS) > self.MAX_DRAFTS:  # (the longest left go)
                del self.DRAFTS[next(iter(self.DRAFTS))]
        self.set_box("", None, [])

    def take_draft(self):
        """The chat just opened: its draft (keep_draft) in the box, and its queue under the conversation, if it was left
        with them."""
        if draft := self.DRAFTS.pop(self.draft_key(), None):
            self.QUEUE_SEEN.pop(self.draft_key(), None)  # (its queue this window's again: sent as its turn ends here)
            self.set_box(draft["text"], draft["caret"], draft["attachments"])
            self.put_queue(draft.get("queue"))
            self.completer.hide()

    @classmethod
    def drafts_state(cls):
        """The drafts as a restart keeps them (BaudeApp.restart)."""
        return {k: {**d, "attachments": [a.state() for a in d["attachments"]],
                    **({"queue": cls.queue_saved(d["queue"])} if d.get("queue") else {})}
                for k, d in cls.DRAFTS.items()}

    @classmethod
    def restore_drafts(cls, state):
        for key, d in (state or {}).items():
            cls.DRAFTS.setdefault(key, {**d, "attachments": [Attachment.from_state(a) for a in d["attachments"]],
                                        **({"queue": cls.queue_loaded(d["queue"])} if d.get("queue") else {})})

    @staticmethod
    def queue_saved(queue):
        """A queue (queue_state's) as a restart keeps it: its attachments as theirs (Attachment.state)."""
        return {**queue, "turns": [{**t, "attachments": [a.state() for a in t["attachments"]]} for t in queue["turns"]]}

    @staticmethod
    def queue_loaded(queue):
        return {**queue, "turns": [{**t, "attachments": [Attachment.from_state(a) for a in t["attachments"]]}
                                   for t in queue["turns"]]}

    @classmethod
    def send_left_queues(cls):
        """(the network changed) A chat left with a queue, whose turn its host lists over now: what's queued sent to
        it, as the open chat's is as its turn ends (left_turn_ended), by its host, whatever's open. Only a turn's end
        seen here sends it: one over by the time it's first listed (a restart's) leaves it for its chat's ↑."""
        for key in [k for k, d in cls.DRAFTS.items() if d.get("queue")]:
            r = network().find(key)
            listed = r.sessions.get(key) if r is not None and r.state == "alive" else None
            if listed is None:
                continue
            state, was = listed.get("state") or "idle", cls.QUEUE_SEEN.get(key)
            cls.QUEUE_SEEN[key] = state
            if was in ("working", "waiting") and state not in ("working", "waiting"):
                cls.left_turn_ended(key, lambda content, said, done, r=r, key=key: r.request(
                    {"op": "input", "session": key, "msg": {"type": "user", "uuid": said,
                                                            "message": {"role": "user", "content": content}}}, done))

    @classmethod
    def left_turn_ended(cls, key, send):
        """The turn of a chat left with a queue (key's draft's) over: the queue sent as the open chat's would be
        (flush_queue), with send(content, uuid, done): all of it as one message, or up to the one whose ↑ stopped the
        turn. Sent by a uuid kept with it, so its host takes it once if it's sent
        again; put back at the queue's front if it isn't taken (in its window's queue, if it's open in one by then).
        True if something was sent."""
        draft = cls.DRAFTS.get(key) or {}
        queue = draft.get("queue")
        if not queue:
            return False
        upto = queue.get("upto")
        queue["upto"], queue["hold"] = None, False
        turns = queue["turns"][:upto + 1 if upto is not None else None]
        del queue["turns"][:len(turns)]
        if not queue["turns"]:
            del draft["queue"]
            if not draft["text"].strip() and not draft["attachments"]:
                del cls.DRAFTS[key]
            cls.QUEUE_SEEN.pop(key, None)
        text = "\n\n".join(t["text"] for t in turns if t["text"])
        atts = [a for t in turns for a in t["attachments"]]
        said = turns[0].get("said") if len(turns) == 1 and turns[0].get("said") else str(uuid.uuid4())

        def done(r):
            if r.get("op") != "error":
                return
            back = {"text": text, "attachments": atts, "said": said}
            app = Gio.Application.get_default()
            for w in app.windows() if app is not None and hasattr(app, "windows") else ():
                if w.draft_key() == key:
                    w.put_queue({"turns": [back], "upto": None, "hold": False})
                    return
            d = cls.DRAFTS.setdefault(key, {"text": "", "caret": None, "attachments": []})
            d.setdefault("queue", {"turns": [], "upto": None, "hold": False})["turns"].insert(0, back)
        send(cls.user_content(text, atts), said, done)
        return True

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

    def login_ask(self, msg):
        """A message of claude's saying its sign-in ran out, or None: only a Claude Code chat's (another program's
        sign-in isn't `claude auth login`'s)."""
        return login_error(msg) if model_program(self.model) == "claude" else None

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

    def h_said(self, msg):
        """A message the chat was sent from elsewhere, as its agent's given it (its host's word: claude never says one
        back), drawn as its transcript's record of it is: as who it's from if not you (sender), as its host says."""
        if self.remote is not None and self.session_id and msg.get("sender") is not None:
            self.remote.heard_sender(self.session_id, msg.get("uuid"), msg["sender"])
        rec = {"type": "user", "uuid": msg.get("uuid"), "message": msg.get("message") or {}}
        if (item := history_item(json.dumps(rec))) is not None:
            self.render_history([item], final=False)

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
        """The chat's title in the header and the window's title; a Bot's name instead, for a Bot (it has no other)."""
        self.session_title = title
        shown = self.bot_name() or title
        self.title_label.set_text(shown or wording.say("sidebar.new_session"))
        self.set_title(f"Baude — {shown}" if shown else "Baude")

    def bot_name(self):
        """The open chat's Bot's name, as its host lists it; None if it isn't a Bot."""
        r = getattr(self, "remote", None)
        listed = r.sessions.get(self.session_id) if r is not None else None
        return ((listed or {}).get("bot") or {}).get("name") or None
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
        if (why := self.login_ask(msg)) is not None:  # (its sign-in ran out, say: a card to sign in again, not the error)
            if not getattr(self, "login_asked", False):
                self.login_prompt(why)
            return
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
