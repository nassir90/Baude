"""sidequestion — a side question asked in a chat (SideQuestion), and compactions' marks (CompactMarks).
"""
import uuid

from baudelib.transcripts import find_session
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from baudelib.models import model_program  # noqa: E402,F401

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
import chatparts  # noqa: E402
import composerview  # noqa: E402
import chatfind  # noqa: E402
from chatparts import UserTurn  # noqa: E402
from markdownview import MarkdownView  # noqa: E402,F401
from panels import natural_height  # noqa: E402,F401

from toolcards import StatusSlot  # noqa: E402
from sessionlist import ClaudeProc  # noqa: E402


# --------------------------------------------------------- side questions --

class CompactMarks:
    """A conversation with claude that marks its compactions (chatparts.compact_said: Baude's chat, a side question's):
    where it's compacting, as it happens (it can take a minute), then what it did, the summary it wrote folded under
    that (chatparts.CompactMark). Its compact_add(mark) puts a marker where the conversation's at (returning it),
    compact_here(mark) says whether one's still in it, compact_started() that it's busy."""

    compact_mark = None  # (this turn's: compact_ended lets go of it as the turn ends, or its claude does)
    compact_kept = False  # (its connection dropped mid-turn: the turn, and the compaction, going on without it)

    def compact_mark_now(self):
        """The marker of this turn's compaction under way, made where the conversation's at if there's none."""
        mark = self.compact_mark
        if mark is None or mark.state != "running" or not self.compact_here(mark):
            mark = self.compact_mark = self.compact_add(chatparts.CompactMark())
        return mark

    def on_compaction(self, msg):
        """What msg says of a compaction, marked; True if it was the summary (nothing more to draw of it)."""
        what, detail = chatparts.compact_said(msg) or (None, None)
        mark = self.compact_mark
        if what == "running":
            self.compact_started()
            self.compact_mark_now().running(detail)
        elif what == "failed":
            self.compact_mark_now().fail(detail)
        elif what == "ended" and mark is not None and mark.state == "running":
            self.compact_mark = None  # (none made after all: nothing to mark)
            if (parent := mark.get_parent()) is not None:
                parent.remove(mark)
        elif what == "succeeded":
            self.refresh_context()
        elif what == "done":
            self.compact_mark_now().finish(detail)  # (kept: the summary claude says next goes under it)
        elif what == "summary" and mark is not None and mark.state == "done" and mark.fold is None:
            mark.set_summary(detail)
            self.compact_mark = None
            return True
        return False

    def compact_ended(self):
        """Its turn's over, or its claude's gone: a compaction still under way didn't finish, and the next turn's is
        its own."""
        mark, self.compact_mark, self.compact_kept = self.compact_mark, None, False
        if mark is not None and mark.state == "running":
            mark.fail(stopped=True)


class SideQuestion(CompactMarks, Gtk.ListBoxRow):
    """One /btw thread, a row of the side panel's Side questions titled with its question: its own claude, started with
        --resume <session> --fork-session --no-session-persistence --permission-mode plan
    so it sees the whole conversation so far but writes nothing (not to the session, not a new
    one), and can't edit anything. Tools that need permission are refused. Follow-ups go to the
    same process; it ends with the window. Any number can run at once. One asked in the box over the pane's list
    (SidePanel.ask_typed) is made with no question and given it at once. One whose claude couldn't be had keeps its
    question in its own box, to send again: its claude starts then.
    Its thread (inside: the darker inside, the box it's asked in under it, Meta's) opens under its line (toggle: ⌄, −
    to close it again), or fills the page (full: SidePanel.open_side_question, back out of it), its dots and context
    meter under the box there."""

    PREFACE = ("(This is a side question, asked while you work on the conversation above. Answer it "
               "briefly from what you already know; don't continue the task or use tools.)\n\n")
    CONTEXT_EVERY_MS = 1000

    def __init__(self, win, question=None):
        super().__init__(activatable=False)
        self.add_css_class("side-question")
        self.win, self.busy, self.answer, self.proc, self.answers = win, False, None, None, {}
        self.stopped = None  # (the answer stopped: Esc, or the box's stop button)
        self.reverting = None  # (a follow-up being reverted, not sent yet: {turn, hidden, draft, asked}: revert_to)
        self.model = win.model
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(rows)
        head = Gtk.Box(spacing=12, margin_start=12, margin_end=6, margin_top=8, margin_bottom=8)
        head.add_css_class("side-question-head")
        self.status = StatusSlot("running")  # (a spinner while it answers; a mark only for one that failed)
        self.status.set_visible(False)
        head.append(self.status)
        self.title = Gtk.Label(label=question or "", xalign=0, hexpand=True, wrap=True,
                               wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.title.add_css_class("title")  # (as a subagent's row's)
        head.append(self.title)
        self.toggle = Gtk.Button(valign=Gtk.Align.CENTER)
        self.toggle.add_css_class("flat")
        self.toggle.connect("clicked", lambda *_: self.set_expanded(not self.expanded()))
        head.append(self.toggle)
        self.full = Gtk.Button(icon_name="window-maximize-symbolic", valign=Gtk.Align.CENTER,
                               tooltip_text="Open this side question in the whole panel")
        self.full.add_css_class("flat")
        self.full.add_css_class("side-question-full")
        self.full.connect("clicked", lambda *_: win.panel.open_side_question(self))
        head.append(self.full)
        rows.append(head)
        # its thread, and the box it's asked in, on the darker inside: under its line, or the page's whole
        self.inside = widgets.inside(Gtk.Box(orientation=Gtk.Orientation.VERTICAL))  # (set into the row)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12, margin_end=12,
                            margin_top=12)
        self.body.add_css_class("zoomable")
        self.scroll = natural_height(Gtk.ScrolledWindow(child=self.body, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                        visible=False))  # (nothing over its box till something's asked)
        self.chat_find = chatfind.ChatFind(self.body, self.scroll, pane=self.inside)  # (⌘F / Ctrl+F in it)
        self.inside.append(self.chat_find.wrap())
        comp = self.comp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=10, margin_end=10,
                                   margin_top=10, margin_bottom=10)
        self.writing = composerview.Composer(follow_text=True)  # (its button by the text's last line)
        self.composer = self.writing.input
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.composer.add_controller(keys)
        self.writing.send.connect("clicked", lambda *_: self.stop() if self.busy else self.submit())
        comp.append(self.writing)
        self.foot = Gtk.Box(spacing=12)  # (Meta's foot, without its Clear)
        self.foot.add_css_class("meta-foot")
        self.dots = widgets.WorkingDots()
        self.foot.append(self.dots)
        self.ctx = chatparts.ContextUsage(hexpand=True, halign=Gtk.Align.END,  # (the window of the model it's asked on)
                                        window=lambda: win.shown_window(self.model or getattr(win, "model_shown", None)))
        self.context_soon = None
        self.foot.append(self.ctx)
        comp.append(self.foot)
        self.inside.append(comp)
        self.reveal = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        rows.append(self.reveal)
        self.put_inside_row()
        self.set_expanded(False)
        if question and not self.start(question):  # (couldn't be had: kept in its box, to send again)
            self.composer.get_buffer().set_text(question)

    def expanded(self):
        return self.reveal.get_reveal_child()

    def opened(self):
        """Its thread open under its line, all the way (not opening or closing there)."""
        return self.reveal.get_reveal_child() and self.reveal.get_child_revealed()

    def set_expanded(self, on):
        self.reveal.set_reveal_child(on)
        self.toggle.set_icon_name("list-remove-symbolic" if on else "pan-down-symbolic")
        self.toggle.set_tooltip_text("Hide this side question" if on else "Show this side question")

    def put_inside_row(self):
        """Its thread back under its line (from the whole page), as tall as it is."""
        if (parent := self.inside.get_parent()) is not None:
            parent.remove(self.inside)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_vexpand(False)
        self.scroll.set_visible(self.answer is not None)  # (nothing over its box till something's asked)
        self.inside.set_vexpand(False)
        self.inside.set_margin_start(widgets.INSIDE_GAP)
        self.comp.set_margin_bottom(10)
        self.foot.set_visible(False)
        self.reveal.set_child(self.inside)

    def take_inside(self):
        """Its thread, for the whole page: scrolled there, the box under it, dots and context meter too."""
        self.reveal.set_child(None)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_propagate_natural_height(False)
        self.scroll.set_vexpand(True)
        self.scroll.set_visible(True)  # (asked or not: the page over its box, the box at its foot, as Meta's)
        self.inside.set_vexpand(True)
        self.inside.set_margin_start(0)  # (as a subagent's square: none on the main chat's side, its gap's the one)
        self.comp.set_margin_bottom(0)  # (as Meta's: its box as far up from the page's foot as Meta's and the chat's)
        self.foot.set_visible(True)
        return self.inside

    def text(self):
        return self.writing.text()

    def _key(self, _c, keyval, _code, state):
        """Ctrl+Enter (⌘Enter) asks; Enter is a new line (a list's next item); Esc stops the answer: as in Meta's
        box."""
        if composerview.leave_wrapped(self.composer.get_buffer(), keyval, state):
            return True
        if composerview.tab_key(self.writing.markdown, keyval, state):
            return True  # (a list item in or out a level, as on a Record page)
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            markdown = self.writing.markdown
            if not composerview.sends(state):
                return composerview.newline_key(markdown, state)  # (a list's next item; Shift: a plain line break)
            self.submit()
            return True
        if keyval == Gdk.KEY_Escape and self.busy:
            self.stop()
            return True
        if keyval == Gdk.KEY_Escape and self.reverting is not None and self.reverting["asked"] is None:
            self.cancel_revert()
            return True
        return False

    def submit(self):
        """What's written asked (kept if it couldn't be: said in the window's note, there to send again); a revert
        pending, made first (commit_revert)."""
        if self.busy or not self.text().strip():
            return
        if self.reverting is not None:
            if self.reverting["asked"] is None:
                self.commit_revert()
            return
        if self.ask(self.text()) is not False:
            self.composer.get_buffer().set_text("")

    STOP_WAIT = 3000  # ms for a stopped answer to end (as Meta's), before it's shown stopped anyway

    def stop(self):
        """The answer stopped: shown stopped, not failed, as its result comes (or after STOP_WAIT, if it doesn't)."""
        if self.busy and self.proc is not None:
            self.stopped = answer = self.answer
            self.proc.control({"subtype": "interrupt"})
            GLib.timeout_add(self.STOP_WAIT, lambda: self.busy and self.answer is answer and self._stopped() and False)

    def _stopped(self):
        self._busy(False)
        self._mark("interrupted")

    def _mark(self, state):
        """Its line's status: a spinner, a failed or stopped one's mark, or (None) nothing; its page's header's too."""
        if state is not None:
            self.status.set_state(state)
        self.status.set_visible(state is not None)
        self.win.panel.side_question_status(self)

    def start(self, question):
        """Its claude, and the first question to it: False if it couldn't be had (said)."""
        win = self.win
        if not win.runs_here():  # (a chat of Baude's, or a host's: its side question's the host process's too)
            self.proc, self.forked = win.side_question_proc(self.on_msg, self.on_exit)
        else:
            info = find_session(win.session_id) if win.session_id else None
            fork = ["--fork-session"] if info else []  # nothing saved yet: a fresh, context-free side chat
            self.forked = bool(info)
            try:
                self.proc = ClaudeProc(self.on_msg, self.on_exit, win.run_cwd, win.model, "plan",
                                       info["id"] if info else None, None, extra=fork + ["--no-session-persistence"])
            except OSError as e:  # (claude not there, or the chat's folder gone: said, as the chat's own would be)
                win.note(wording.say("side_questions.not_asked", why=e))
                self.proc = None
        if self.proc is None:
            return False
        self.model = win.model  # (what it was started on: whether it can revert, can_revert)
        self.title.set_label(question)
        self.win.panel.side_question_titled(self)
        self.ask(question, first=True)
        return True

    def ask(self, text, first=False):
        text = (text or "").strip()
        if text and not first and self.proc is None and self.answer is None:  # (its first question, sent again)
            return None if self.start(text) else False
        if not text or self.busy or self.proc is None:
            return
        said = str(uuid.uuid4())  # (its claude keeps it as the message's: what a revert names, revert_to)
        if not first:
            self.body.append(UserTurn(self, self.win.user_bubble(text, []), text, said))
        self.answer = MarkdownView()
        self.body.append(self.answer)
        self.scroll.set_visible(True)
        self._busy(True)
        self.proc.user((self.PREFACE if first else "") + text, uuid=said)

    # ---- revert (as the chat's: Baude's revert_to): a follow-up, and everything after it, taken back
    def can_revert(self):
        """Whether a follow-up of its can be reverted now: its claude there, and not answering. Not on Codex's models,
        whose program has no rewind."""
        return self.proc is not None and not self.busy and self.writing.get_sensitive() and \
            model_program(self.model) != "codex"

    def revert_to(self, turn):
        """A follow-up reverted (UserTurn's button), tentatively: it and what came after it hidden, and what it said in
        the box. Esc puts them back, and the draft that was there (cancel_revert); asking makes the revert
        (commit_revert), then asks. One already being reverted is put back first (the draft kept: the one from before
        either)."""
        if not self.can_revert() or (self.reverting is not None and self.reverting["asked"] is not None):
            return
        draft = None
        if self.reverting is not None:
            draft = self.reverting["draft"]
            self.cancel_revert(restore_draft=False)
        buf = self.composer.get_buffer()
        hidden, w = [], turn
        while w is not None:
            if w.get_visible():
                w.set_visible(False)
                hidden.append(w)
            w = w.get_next_sibling()
        self.reverting = {"turn": turn, "hidden": hidden, "asked": None,
                          "draft": draft if draft is not None else buf.get_text(*buf.get_bounds(), True)}
        buf.set_text(turn.text)
        self.composer.grab_focus()

    def cancel_revert(self, restore_draft=True):
        """Esc on a revert not asked: what it hid shown again, and the box as it was before."""
        rev, self.reverting = self.reverting, None
        if rev is None:
            return
        for w in rev["hidden"]:
            w.set_visible(True)
        if restore_draft:
            self.composer.get_buffer().set_text(rev["draft"])

    def commit_revert(self):
        """A revert asked (submit): its claude takes the follow-up and everything after it back (rewind_conversation, as
        the chat's revert does), then what's in the box is asked (reverted); if it won't, said in the thread."""
        rev = self.reverting
        rid = self.proc.control({"subtype": "rewind_conversation", "target_message_uuid": rev["turn"].uuid,
                                 "interrupt_if_running": True})
        rev["asked"] = rid
        self.writing.gate("reverting", True)  # (its box off till its claude's answered)
        self.answers[rid] = (lambda r: self.reverted(rev, r), lambda why: self.revert_refused(rev, why))

    def revert_refused(self, rev, why):
        """Its claude wouldn't revert: said under what's still shown, the revert still pending (Esc puts it back;
        Enter asks again)."""
        rev["asked"] = None
        self.writing.gate("reverting", False)
        if self.reverting is rev:
            self.win.note(f"Couldn't revert the side question's follow-up: {why}")

    def reverted(self, rev, response):
        """Its claude's answer to commit_revert: rewound, the follow-up and what came after it gone, and what's in the
        box asked; else why not, said."""
        if not (response or {}).get("rewound"):
            why = next((v for k, v in (response or {}).items() if k != "rewound" and isinstance(v, str)), None)
            return self.revert_refused(rev, why or "its claude said no")
        self.writing.gate("reverting", False)
        if self.reverting is not rev:
            return
        self.reverting = None
        for w in rev["hidden"]:
            self.body.remove(w)
        last = self.body.get_last_child()
        self.answer = last if isinstance(last, MarkdownView) else None
        self.submit()

    def _busy(self, busy):
        if busy and not self.busy:
            self.compact_ended()  # (an answer starting: an earlier one's compaction isn't its)
        self.busy = busy
        self._mark("running" if busy else None)
        self.dots.set_working(busy)
        self.writing.set_busy(busy)

    def compact_add(self, mark):  # (CompactMarks: over the answer it's before)
        if self.answer is not None and self.answer.get_parent() is self.body:
            mark.insert_before(self.body, self.answer)
        else:
            self.body.append(mark)
        return mark

    def compact_here(self, mark):
        return mark.get_parent() is self.body

    def compact_started(self):
        pass

    def refresh_context(self):
        if self.proc is not None:
            model = self.model  # (what it answers for, whatever's picked meanwhile)
            self.answers[self.proc.control({"subtype": "get_context_usage"})] = \
                lambda usage: (self.win.remember_context(model, usage), self.ctx.set_usage(usage))

    def refresh_context_soon(self):
        """The meter asked again a moment after each step of an answer, at most once a second (as the chat's)."""
        if self.context_soon is None:
            self.context_soon = GLib.timeout_add(self.CONTEXT_EVERY_MS, self._context_now)

    def _context_now(self):
        self.context_soon = None
        self.refresh_context()
        return False

    def on_msg(self, proc, msg):
        if proc is not self.proc:
            return False
        t = msg.get("type")
        if t in ("system", "user") and self.on_compaction(msg):
            return False
        if t == "stream_event" and not msg.get("parent_tool_use_id"):
            d = msg.get("event", {}).get("delta") or {}
            if d.get("type") == "text_delta" and self.answer is not None:
                self.answer.append(d["text"])
        elif t == "assistant" and not msg.get("parent_tool_use_id") and self.answer is not None \
                and not self.answer.text:
            for c in msg.get("message", {}).get("content", []):
                if c.get("type") == "text":
                    self.answer.append(c["text"])
        if t == "assistant" and not msg.get("parent_tool_use_id"):
            self.refresh_context_soon()  # (each step of the answer)
        if t == "control_request" and msg.get("request", {}).get("subtype") == "can_use_tool":
            self.proc.respond(msg["request_id"], {"behavior": "deny",
                                                  "message": "Side questions can't run tools that need permission."})
        elif t == "control_response":
            r = msg.get("response") or {}
            then = self.answers.pop(r.get("request_id"), None)
            if isinstance(then, tuple):  # (a revert's: what's done with its answer, and with its refusal)
                then[0](r.get("response") or {}) if r.get("subtype") == "success" else \
                    then[1](r.get("error") or "its claude couldn't")
            elif then is not None and r.get("subtype") == "success":
                then(r.get("response") or {})
        elif t == "result":
            stopped, self.stopped = self.stopped is not None and self.stopped is self.answer, None
            self.compact_ended()
            self._busy(False)
            if stopped:
                self._stopped()
            elif msg.get("is_error"):
                self._mark("error")
                if self.answer is not None and not self.answer.text:
                    self.answer.append(f"*{msg.get('result') or wording.say('side_questions.failed')}*")
            self.refresh_context()
        return False

    def on_exit(self, code):
        self.compact_ended()
        if self.proc is not None:
            if self.busy:  # (ended mid-answer)
                self._busy(False)
                self._mark("interrupted")
            self.writing.set_sensitive(False)
            if self.reverting is not None:  # (its claude gone: nothing to revert in, what was hidden shown again)
                self.writing.gate("reverting", False)
                self.cancel_revert()
        return False

    def close(self):
        proc, self.proc = self.proc, None
        if proc:
            proc.kill()
        if self.context_soon is not None:
            GLib.source_remove(self.context_soon)
            self.context_soon = None
        self.win.panel.drop_side_question(self)
