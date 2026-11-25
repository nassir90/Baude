"""metachat — MetaChat, Meta's column in Baude's and Record's right bar: what each app's column says (the hooks
its subclass fills in), the conversation, reverting a message, and the forms it opens. How a tool call and the reply
as it comes are shown is in metacards.py (MetaCards), its tools and the index in metatools.py (MetaTools).
"""
import json
import os
import sys
import time
import uuid

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gtk, Pango  # noqa: E402

import widgets  # noqa: E402
import markdownview  # noqa: E402
import chatparts  # noqa: E402
import composerview  # noqa: E402
import panels  # noqa: E402
import chatfind  # noqa: E402
import theming  # noqa: E402
from baudelib import wording  # noqa: E402
from baudelib.claude import EFFORT_LABELS  # noqa: E402
from meta import (  # noqa: E402
    CHATS, COMMAND_TOOLS, NAME, READ_TOOLS, TOO_SMALL, UNAVAILABLE, available, code_mode, effort, label, model,
    model_name, report_text, runs_commands, settings_changed, transcript_text, window,
)
from metaagent import SETUP_TOOLS  # noqa: E402
from metalook import RefCompleter, install_css  # noqa: E402
from metacards import MetaCards  # noqa: E402
from chatinput import LoginCard  # noqa: E402
from metatools import MetaTools  # noqa: E402


# ---------------------------------------------------------------- the column

class MetaChat(MetaCards, MetaTools, Gtk.Box):
    """Your conversation with Meta, in the right bar: replies, each with the actions it took (undo, redo) and what it
    shows (rows of settings, chats, pages; the changelog; a button); @ in the box lists what's to reference
    (RefCompleter); over the box, while changes in the left bar wait to be kept, how many, Keep and Undo; under it,
    Clear at the left and how much of its context window the session's used at the right. Each app's is a subclass of
    its own (APP, WHAT, LISTED; the hooks below)."""

    APP, WHAT, LISTED, PAGES = "the app", "", "what the left bar lists", False
    CHAT_ROWS = False  # (whether its left bar lists chats: what rename_chat changes is marked there)

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        install_css(Gdk.Display.get_default())
        widgets.ensure_icons()
        theming.watch_settings(settings_changed)
        CHATS.add(self)
        self.win, self.ask, self.agent = win, None, None
        self.logins, self.signed_in_after = {}, False  # (a failed reply's sign-in card, by the reply; signed in mid-turn)
        self.live_widget, self.asking = None, []  # (the reply coming; the reads it asks to make)
        self.reverting = None  # (a message of yours being reverted, not sent yet: {msg, draft}: revert_to)
        self.lit = {}  # (what highlight_ asked for, by kind: {kind: {key, …}}; gone with the next message)
        self.painted = {}  # ((kind, key): {where: paint(lit)}: its highlight in the left bar, Meta's boxes, a page)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                           margin_start=12, margin_end=12)
        # (a selection across messages, yours and its replies, as Baude's chat has one: chatfind.ChatSelection, its
        # shading in an overlay over the conversation, under the forms pinned over it)
        shaded = Gtk.Overlay(child=self.box)
        self.scroll = panels.markdown_scroller(shaded, vexpand=True)
        self.text_selection = chatfind.ChatSelection(self.box, self.scroll, shaded)
        # the conversation and the box asked in, set into the column as a chat is into Baude's window (the inside:
        # widgets.inside), all of the column's height theirs (clear, or /clear, asked: cleared, submit)
        talk = widgets.inside(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), start=False)
        # (a form not yet submitted, scrolled past: pinned over the conversation's top, _pin_forms)
        self.pins = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START, margin_top=self.PIN_MARGIN,
                            margin_start=12, margin_end=12)
        self.forms_open = {}  # (each form's id: {spec, form, widget (its card), slot (its place in its reply)})
        over = Gtk.Overlay(child=self.scroll, vexpand=True)
        self.chat_find = chatfind.ChatFind(self.box, self.scroll, pane=self)  # (⌘F / Ctrl+F in Meta: its bar over the
        over.add_overlay(self.chat_find.marks)                              # conversation, its matches under the forms)
        over.add_overlay(chatparts.SummaryPin(self.scroll))  # (an open compaction summary's heading, scrolled past)
        over.add_overlay(self.pins)
        for change in ("value-changed", "changed"):  # (scrolled; laid out again, the conversation's height changed)
            self.scroll.get_vadjustment().connect(change, lambda *_: self._pin_soon())
        self._pin_after, self._pin_waits = None, 0  # (the after-paint handler waiting to look again: _pin_soon)
        talk.append(self.chat_find.bar)
        talk.append(over)
        self.append(talk)
        comp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=10, margin_end=10)
        self.bars = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)  # (an app's own, over the box)
        comp.append(self.bars)
        self.changes_box = self._changes_bar()
        comp.append(self.changes_box)
        self.writing = composerview.Composer()  # (its button's tip says the model: model_changed)
        self.composer, self.send = self.writing.input, self.writing.send
        self.refs = RefCompleter(self.space, self.composer, self.writing, self.first_refs)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.composer.add_controller(keys)
        self.send.connect("clicked", lambda *_: self.ask.stop() if self.ask else self.submit())
        comp.append(self.writing)
        # (more said than the model's window holds: said over the box, the box off, till a model with a bigger
        # window's picked or it's cleared; used: the tokens it takes, as its session last said)
        self.exhausted, self.used = chatparts.Exhausted(self.scroll, self.writing), None
        self.outgrew = None  # (the session that said the conversation's more than its window holds)
        self.too_small = None  # (the model whose window's too small for Meta's own instructions: Agent.first)
        foot = Gtk.Box(spacing=12)
        foot.add_css_class("meta-foot")
        clear = Gtk.Button(label=wording.say("common.clear"), tooltip_text=wording.say("meta.clear_tip"))
        clear.add_css_class("flat")
        clear.connect("clicked", lambda *_: self._clear())
        foot.append(clear)
        copy = Gtk.Button(label=wording.say("common.copy"), tooltip_text=wording.say("meta.copy_tip"))
        copy.add_css_class("flat")
        copy.connect("clicked", lambda *_: self.copy_report())
        foot.append(copy)
        self.dots = widgets.WorkingDots()  # (after Clear: it doesn't move when they come)
        foot.append(self.dots)
        self.ctx = chatparts.ContextUsage(hexpand=True, halign=Gtk.Align.END, window=lambda: window(model()))
        self.context_soon_id = None
        foot.append(self.ctx)
        comp.append(foot)
        talk.append(comp)
        self.settings = {}  # (made once the window is: make_settings)
        self.model_changed()

    # ---- what an app's MetaChat says
    def messages(self):
        """The conversation's messages (a list kept by the app)."""
        return []

    def actions(self):
        """Its actions, in the order they were taken (a list kept by the app)."""
        return []

    def save(self, now=False):
        pass

    def told(self):
        """What Meta's to be told with the next message (the user's undid or redid one of its actions): a list kept
        with the conversation it's for."""
        return []

    def ready(self):
        """Whether there's a conversation to ask in."""
        return True

    def space(self):
        """What @ lists (a lookup.SearchSpace), or None."""
        return None

    def first_refs(self, items):
        return items

    def said_where(self, m):
        """What goes before a message's text in a new session's earlier conversation (earlier_said)."""
        return ""

    def agent_setup(self):
        """{cwd, roots, exe, env_of, protect} for its session (protect: folders of the app's own files, never written
        by its Edit or Write)."""
        return {"cwd": os.path.expanduser("~"), "roots": ()}

    def scope(self):
        """What a session's for, besides its model and mode (Record's: the folders it reads)."""
        return ()

    def app_prompt(self):
        """What its instructions add for this app."""
        return ""

    def app_tools(self):
        return []

    def app_handlers(self):
        return {}

    def sections(self):
        """The index's parts: {name: (rows(filter) -> [dict], line(row) -> str)}; the app's, then settings', runtime's
        and the changelog's (shared_sections)."""
        return {}

    def index_lines(self):
        """The app's lines of the index (index_text puts them after its first)."""
        return []

    def running_lines(self):
        """What's running that the app knows of (its sessions, its agents), for the index's runtime part."""
        return []

    def find_chat(self, ref):
        """A chat a tool names (an id or its start, a title): {key, title, path} or None."""
        return None

    def chats_rows(self, refs):
        """Rows to click for chats (show chats): [{icon, title, subtitle, open}], or ValueError."""
        raise ValueError("There are no chats here.")

    def pages_rows(self, refs):
        raise ValueError("There are no pages here.")

    def button(self, action, items):
        """A button for the user to press (show button:<action>), or ValueError."""
        raise ValueError(f"There's no button {action}.")

    def buttons(self):
        """{action: what it does}: the buttons show offers."""
        return {}

    def rename_chat_to(self, chat, title):
        """A chat (find_chat's) renamed; raises ValueError if it can't be."""
        raise ValueError("Chats can't be renamed here.")

    def chat_text(self, chat):
        return transcript_text(chat["path"]) if chat.get("path") else "(no transcript)"

    def apply_app(self, kind, data):
        """An app's own action done or undone (data: its undo's or redo's)."""
        raise ValueError(f"No such action: {kind}")

    def marks_changed(self):
        """What's marked in the left bar changed (an action taken, kept or undone; a highlight)."""

    def context(self, agent, msg):
        """What goes with a message, before it (the app's): [text, …]."""
        return []

    def make_settings(self):
        return {}

    # ---- the conversation
    def model_changed(self):
        """Settings → Meta: the send button says the model it asks; the next message is the new model's (submit)."""
        level = effort()
        name = model_name() + (f", {EFFORT_LABELS.get(level, level).lower()}" if level else "")
        if runs_commands():
            name = wording.say("meta.with_commands", model=name)
        if code_mode():
            name = wording.say("meta.with_code_mode", model=name)
        self.writing.send_tip = wording.say("meta.send_tip", model=name, shortcut=composerview.SEND_KEY)
        if self.ask is None:
            self.send.set_tooltip_text(self.writing.send_tip)
        self.ctx.window_changed()  # (no session's said what it's used: the new model's window)
        agent = self.agent
        if not available():  # (its host gone from the mesh, or its model: said so, till it's back or another's picked)
            self.exhausted.set(True, UNAVAILABLE)
            if not getattr(self, "unavailable_watch", None):
                self.unavailable_watch = GLib.timeout_add_seconds(self.UNAVAILABLE_EVERY, self._unavailable_check)
        elif agent is not None and not agent.closed and not agent.fresh:
            self._context_now()
        elif self.used is not None:
            self.show_usage({"totalTokens": self.used, "maxTokens": agent.window if agent else window(model())})
        else:
            self.exhausted.set(False)

    UNAVAILABLE_EVERY = 5  # (s between looks at whether Meta's model is back in the mesh, while it isn't)

    def _unavailable_check(self):
        if not available():
            return True
        self.unavailable_watch = None  # (back: its box as it should be now)
        self.model_changed()
        return False

    def show_usage(self, usage):
        """What the session says it's used of its window (usage): in the window of the model picked, if it's another
        (its next session's). Past it, or the session's said it is (outgrew), the chat's exhausted, once any turn's
        done (the box's stop button kept till then)."""
        agent = self.agent
        if usage.get("maxTokens") or usage.get("showWithoutLimit"):
            self.used, self.last_usage = usage.get("totalTokens") or 0, usage
            if agent is None or agent.model != model():
                usage = {"totalTokens": self.used, "maxTokens": window(model())}
            self.ctx.set_usage(usage)
        outgrown = agent is not None and agent is self.outgrew and agent.model == model()
        if outgrown:
            self.ctx.bar.set_value(1)
        if available():
            self.exhausted.set((self.ctx.over or outgrown) and self.ask is None,
                               TOO_SMALL if self.too_small == model() else wording.say("context_usage.exhausted"))

    def report_head(self):
        """The lines over a copied conversation (report_text): the app, Meta's model and what runs it, its effort, its
        modes, the tools it's given, its context as its session last said it. The session's, while there's one (what
        the conversation's had); Settings → Meta's, before there is."""
        agent = self.agent
        if agent is not None:
            value, level, coded, commands = agent.model, agent.effort, agent.key[3], agent.commands
        else:
            value, level, coded, commands = model(), effort(), code_mode(), runs_commands()
        tools = list(READ_TOOLS) + (list(COMMAND_TOOLS) if commands else [])
        tools += ["run_python", *[t["name"] for t in SETUP_TOOLS]] if coded else [t["name"] for t in self.all_tools()]
        usage = getattr(self, "last_usage", None) or {}
        if self.used is not None:
            total = usage.get("maxTokens") or (agent.window if agent else window(value))
            context = (wording.say("report.tokens_of", used=f"{self.used:,}", total=f"{total:,}") if total
                       else wording.say("side_panel.tokens", n=f"{self.used:,}"))
            parts = [f"{c['name']} {c.get('tokens', 0):,}" for c in usage.get("categories") or []
                     if c.get("tokens") and c.get("kind") != "deferred"]
            context += f" ({', '.join(parts)})" if parts else ""
        else:
            shown = self.ctx.label.get_text().removeprefix("context").strip(" :—")
            context = wording.say("report.no_session") if agent is None else shown or wording.say("report.not_said_by_session")
        picked = model()
        return [(wording.say("report.app"), f"{self.APP} ({NAME})"),
                (wording.say("common.model"), f"{model_name(value)} ({value})"),
                (wording.say("report.next_message"), wording.say("report.picked_since", name=model_name(picked), value=picked)
                 if picked != value else ""),
                (wording.say("report.runs_on"), agent.runs if agent is not None else ""),
                (wording.say("meta_settings.effort"),
                 EFFORT_LABELS.get(level, level) if level else wording.say("report.default")),
                (wording.say("meta_settings.code_mode"), wording.say("report.on" if coded else "report.off")),
                (wording.say("report.runs_commands"), wording.say("report.on" if commands else "report.off")),
                (wording.say("report.mode"), agent.started_mode if agent is not None else ""),
                (wording.say("report.context"), context), (wording.say("lookup.tools"), ", ".join(dict.fromkeys(tools))),
                (wording.say("report.copied"), time.strftime("%Y-%m-%d %H:%M:%S %z"))]

    def report(self, most=None):
        """The conversation as text for an issue (report_text), what's being said now too; None if nothing's been."""
        messages = list(self.messages()) if self.ready() else []
        if self.ask is not None and self.ask.turn is not None and self.ask.turn.get("items"):
            messages.append({"role": "meta", "items": self.ask.turn["items"], "warned": [wording.say("report.still_replying")],
                             "model": self.ask.model})
        return report_text(self.report_head(), messages, most=most) if messages else None

    def copy_report(self):
        self.get_clipboard().set(self.report() or report_text(self.report_head(), []))
        widgets.toast(self, wording.say("meta.copied"))

    def close_agent(self):
        """Its session stopped (the conversation cleared, the window gone): the next message starts another, told what
        was said."""
        if self.agent is not None:
            self.agent.close()
        self.agent = self.ask = self.used = self.outgrew = self.too_small = None
        self.ctx.reset()
        self.exhausted.set(False)

    def agent_gone(self, agent):
        if agent is self.agent:
            self.agent = self.ask = None
            self.ctx.reset()
            self._busy(False)
            self._drop_asking()
            self.model_changed()  # (exhausted or not, as the conversation stands in the next session's window)

    CONTEXT_EVERY_MS = 1000

    def context_soon(self, agent):
        """The context shown asked for again a moment after each step of a turn (the message sent, a tool's result,
        the reply), at most once a second, as Baude's chat's is."""
        if agent is self.agent and self.context_soon_id is None:
            self.context_soon_id = GLib.timeout_add(self.CONTEXT_EVERY_MS, self._context_now)

    def _context_now(self):
        self.context_soon_id = None
        if (agent := self.agent) is not None and not agent.closed and not agent.fresh:  # (fresh: nothing sent yet)
            agent.usage(lambda usage: agent is self.agent and self.show_usage(usage))
        return False

    def _clear(self):
        if self.reverting is not None:  # (a revert pending: the box as it was before it)
            rev, self.reverting = self.reverting, None
            self.composer.get_buffer().set_text(rev["draft"])
            self.put_held(rev["held"])
        self.close_agent()
        del self.messages()[:]
        self.save(now=True)
        self.render()

    def _key(self, _c, keyval, _code, state):
        """Ctrl+Enter (⌘Enter) asks; Enter is a new line (a list's next item), as in Baude; a selection
        and * ` or / is made bold, code or italic; @'s list, while it's up, has the arrows, Enter, Tab and Esc
        (RefCompleter); Esc stops the reply."""
        if self.refs.handle_key(keyval, state):
            return True
        if composerview.leave_wrapped(self.composer.get_buffer(), keyval, state):
            return True
        if composerview.tab_key(self.writing.markdown, keyval, state):
            return True  # (a list item in or out a level, as on a Record page)
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and state & Gdk.ModifierType.ALT_MASK and \
                not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.SHIFT_MASK):
            return self.writing.markdown.move_item(-1 if keyval == Gdk.KEY_Up else 1)
        if not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK) and \
                composerview.wrap_selection(self.composer.get_buffer(), chr(Gdk.keyval_to_unicode(keyval) or 0)):
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            markdown = self.writing.markdown
            if not composerview.sends(state):
                return composerview.newline_key(markdown, state)  # (a list's next item; Shift: a plain line break)
            self.submit()
            return True
        if keyval == Gdk.KEY_Escape and self.ask:
            self.ask.stop()
            return True
        if keyval == Gdk.KEY_Escape and self.reverting is not None:
            self.cancel_revert()
            return True
        return False

    # ---- revert (as Baude's chat's: revert_to there): a message of yours, and everything after it, taken back
    def can_revert(self):
        """Whether a message of yours can be reverted now: not while Meta's answering."""
        return self.ask is None and self.ready()

    def revert_to(self, turn):
        """A message of yours reverted (UserTurn's button), tentatively: it and what came after it not shown, and what
        it said in the box. Esc puts them back, and the draft that was there (cancel_revert); sending makes the revert
        (commit_revert), then sends. Over the context window: the box on again meanwhile, as the revert's a way out."""
        said = self.messages()
        at = next((i for i, m in enumerate(said) if m is turn.message), None)
        if not self.can_revert() or at is None:
            return
        buf = self.composer.get_buffer()
        before = self.reverting
        self.reverting = {"msg": turn.message, "gone": [id(m) for m in said[at:]],  # (what it takes back, as pressed)
                          "draft": before["draft"] if before else buf.get_text(*buf.get_bounds(), True),
                          "held": before["held"] if before else self.held()}
        buf.set_text(turn.message["text"])
        self.restored(turn.message)
        if available():
            self.exhausted.set(False)
        self.render()
        self.composer.grab_focus()

    def cancel_revert(self):
        """Esc on a revert not sent: what it hid shown again, and the box as it was before (what went with it too)."""
        rev, self.reverting = self.reverting, None
        self.composer.get_buffer().set_text(rev["draft"])
        self.put_held(rev["held"])
        self.render()
        self.model_changed()  # (exhausted again, if it was)

    def commit_revert(self):
        """A revert sent (submit): the message and what came after it when it was pressed out of the conversation (what
        another window's added since kept), and its sessions stopped, so the next message's is a new one told only
        what's left (message_for). The actions its replies took stay, kept or undone as any are (the changes bar,
        Meta's changelog); what they proposed goes with them (after_revert)."""
        rev, self.reverting = self.reverting, None
        said = self.messages()
        if not any(m is rev["msg"] for m in said):  # (cleared, or another conversation shown, meanwhile)
            return
        gone = set(rev["gone"])
        said[:] = [m for m in said if id(m) not in gone]
        self.close_agents()
        self.save(now=True)
        self.after_revert()

    def close_agents(self):
        """The sessions the conversation's in stopped (a revert sent): Baude's, each window's."""
        self.close_agent()

    def held(self):
        """What goes with what's in the box besides its text (Record's quote), kept while a revert's pending."""
        return None

    def put_held(self, held):
        """held() put back (a revert put back, or cleared)."""

    def restored(self, msg):
        """A message put back in the box (revert_to): what went with it too (Record's quote), as taken let it go."""

    def after_revert(self):
        """A revert made: what the app shows of what was taken back, gone too (Record's proposed edits)."""

    def render(self):
        self.text_selection.forget()  # (its labels gone with what's drawn again)
        while (c := self.box.get_first_child()) is not None:
            self.box.remove(c)
        for m in self.messages() if self.ready() else ():
            if self.reverting is not None and m is self.reverting["msg"]:
                break  # (being reverted: it and what's after it not shown, till it's sent or put back)
            self.box.append(self._bubble(m))
        if self.ask is not None and self.live_widget is not None:  # (the reply coming, and what it's asking: kept)
            self.box.append(self.live_widget)
            for card in self.asking:
                self.box.append(card)
        self.show_changes_bar()
        for ms in (60, 300):  # (and again once what's tall, a changelog, a list of rows, is laid out)
            GLib.timeout_add(ms, self._to_bottom)

    def login_card(self, m):
        """A reply that failed for want of signing in, as LoginCard: Sign in, on this machine's claude, and Send again
        (what it was asked), kept as it is across the conversation's being drawn again."""
        card = self.logins.get(id(m))
        if card is None:
            said = self.messages()
            asked = next((u["text"] for u in reversed(said[:said.index(m)]) if u["role"] == "user"), None)
            card = self.logins[id(m)] = LoginCard(self, ask=m.get("text"), retry=(asked, []) if asked else None)
        if card.get_parent() is not None:
            card.get_parent().remove(card)
        return card

    def signed_in(self):
        """A LoginCard's: signed in afresh, its session started again on it (after its turn, if one's running)."""
        if self.ask is not None:
            self.signed_in_after = True
        else:
            self.close_agent()

    def send_again(self, sent):
        """A LoginCard's Send again: what the failed reply was asked, asked again, unless a turn's going. Whether it was."""
        buf = self.composer.get_buffer()
        if self.ask is not None or self.exhausted.on or buf.get_char_count():
            return False
        buf.set_text(sent[0])
        self.submit()
        return True

    def _to_bottom(self):
        if getattr(self.scroll, "held", False):
            return False  # (put back where it was read to: panels.hold_place)
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_upper())
        return False

    def quote_said(self, m):
        """A user's message's quote, as its bubble shows it (above what they wrote, its bar saying it's a quote), or
        None."""
        q = m.get("quote")
        return " ".join(q.split())[:400] + ("…" if len(q) > 400 else "") if q else None

    def prose(self, text):
        """A reply's text, as Baude's chat shows one: Markdown, in no bubble (Record's links its folders' names)."""
        return markdownview.MarkdownView(text)

    def set_prose(self, widget, text):
        widget.set_text(text)

    def _bubble(self, m):
        """A message as Baude's chat has one: what you wrote in a bubble, on the right; Meta's reply in none, in the
        order it came (each part of what it said, and each tool it called, as a tool card, with what that showed
        under it)."""
        if m["role"] == "user":
            b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.END)
            b.add_css_class("user-bubble")
            if (q := self.quote_said(m)) is not None:
                b.append(label(q, css=("quote-in-bubble",), select=True))
            b.append(self._said(m))
            # (its row the conversation's width, its revert button fading in beside it as the chat's: UserTurn)
            outer = chatparts.UserTurn(self, b, m["text"], m.get("t", 0))
            outer.set_margin_start(28)
            outer.message = m
            return outer
        reply = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        reply.bubble, reply.prose = reply, None
        for text in m.get("warned") or ():  # (what it was told it can't do, as the chat says it: over what it said)
            reply.append(label(text, css=("dim-label", "caption"), select=True))
        items = m.get("items")
        if m.get("error"):
            drawn = set()
            for it in items or ():
                if it["kind"] == "compact":
                    reply.append(self.compact_mark(it))
                elif it["kind"] == "tool" and not (it.get("actions") and set(it["actions"]) <= drawn):
                    widget, holds = self.call_widget({**it, "actions": [i for i in it.get("actions") or ()
                                                                         if i not in drawn]})
                    drawn.update(it.get("actions") or ())
                    if widget is not None:
                        reply.append(widget)
                    if not holds:
                        self.add_shown(reply, it.get("shown") or ())
                elif it["kind"] == "tool" and self.call_hunks(it):
                    reply.append(self.tool_card(it, controls=False))
            if m.get("login"):
                reply.append(self.login_card(m))
                return reply
            reply.prose = label(m.get("text") or "", css=("error",), select=True)
            reply.append(reply.prose)
            return reply
        if items is None:  # (a reply kept from before its parts were: its text, then what it did)
            items = [{"kind": "text", "text": m["text"]}] if m.get("text") else []
        drawn = set()  # (the actions drawn: a turn's change, made by several calls, drawn once, at the first)
        for it in items:
            if it["kind"] == "text":
                if (text := self.reply_text(it["text"]).strip()):
                    reply.prose = self.prose(text)
                    reply.append(reply.prose)
            elif it["kind"] == "compact":
                reply.append(self.compact_mark(it))
                reply.prose = None
            else:
                ids = it.get("actions") or []
                fresh = [i for i in ids if i not in drawn]
                if ids and not fresh:
                    if self.call_hunks(it):  # (an edit, its change drawn at an earlier call's: its card, with its hunk)
                        reply.append(self.tool_card(it, controls=False))
                    self.add_shown(reply, it.get("shown") or ())
                    continue
                drawn.update(ids)
                widget, holds = self.call_widget({**it, "actions": fresh})
                if widget is not None:
                    reply.append(widget)
                if not holds:
                    self.add_shown(reply, it.get("shown") or ())
                reply.prose = None  # (what grows is the part after it, if one comes)
        if m.get("items") is None:
            self.add_work(reply, m)
        if not items and not m.get("actions") and not m.get("shown"):
            reply.prose = self.prose(m.get("text") or "…")
            reply.append(reply.prose)
        return reply

    def has_work(self, m):
        """Whether a reply did anything: called a tool, took an action, showed something."""
        return bool(m.get("actions") or m.get("shown") or m.get("code") or
                    any(it["kind"] == "tool" for it in m.get("items") or ()))

    def add_shown(self, box, specs):
        for spec in specs:
            try:
                if (w := self.shown_widget(spec)) is not None:
                    box.append(w)
            except (ValueError, KeyError) as e:
                box.append(label(f"({e})", css=("dim-label", "caption")))

    def add_work(self, box, m):
        """A reply kept from before its parts were: its actions, as one card's, and what it showed."""
        acts = {a["id"]: a for a in self.actions()}
        if mine := [acts[i] for i in m.get("actions") or () if i in acts]:
            box.append(self.tool_card({"kind": "tool", "name": "", "input": {}, "state": "done",
                                       "actions": [a["id"] for a in mine]}))
        self.add_shown(box, m.get("shown") or ())

    # ---- forms (open_form, fill_form): inline, pinned till they're submitted
    def forms(self):
        """The app's forms: {name: (what it's for, make() -> a Form, or ValueError: it can't be had now)}."""
        return {}

    def _form_spec(self, fid, turn=None):
        """A form's spec as it's kept (under the reply that brought it up), by its id."""
        specs = [s for m in self.messages() for it in m.get("items") or () for s in it.get("shown") or ()]
        specs += list((turn or {}).get("shown") or ())
        return next((s for s in specs if s.get("kind") == "form" and s.get("id") == fid), None)

    def form_slot(self, spec):
        """Where a form goes in its reply: its card (made once, kept as the conversation's drawn again: what's typed
        in it stays), or, while it's pinned over the conversation, a gap its height."""
        entry = self.forms_open.get(spec["id"])
        if entry is None:
            entry = self.forms_open[spec["id"]] = {"spec": spec, "slot": None}
            entry["form"], entry["widget"] = self.form_card(spec)
        entry["spec"] = spec
        if (sync := getattr(entry["widget"], "sync", None)) is not None:
            sync()  # (as it's kept: another window's may have filled it, or submitted it)
        slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        slot.add_css_class("meta-ui")
        widget = entry["widget"]
        if widget.get_parent() is self.pins:
            slot.set_size_request(-1, widget.get_height())
        else:
            if (parent := widget.get_parent()) is not None:
                parent.remove(widget)
            slot.append(widget)
        entry["slot"] = slot
        self._pin_soon()
        return slot

    def form_card(self, spec):
        """A form's card: its dialog's heading, what it says, its fields, what's wrong with them, and its buttons
        (Cancel, and its own: Add, Create, Start); submitted or cancelled, what came of it. Right-click: Pin, Unpin."""
        name = spec["form"]
        try:  # (whatever goes wrong making it: said in its place, never in the way of the window it's in)
            form = self.forms()[name][1]()
        except Exception as e:  # noqa: BLE001
            print(f"meta: form {name}: {type(e).__name__}: {e}", file=sys.stderr)
            return None, label(wording.say("meta.form_unavailable", form=name.replace("_", " "), why=e),
                               css=("dim-label", "caption"))
        kept = dict(spec.get("values") or {})
        missed = form.fill({k: v for k, v in kept.items() if v not in (None, "")}, partial=True)
        base = form.values()  # (as it's shown: a field that couldn't be filled, kept as it was till it's changed)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("meta-form")
        title = Gtk.Label(label=form.heading, xalign=0, wrap=True)
        title.add_css_class("heading")
        card.append(title)
        if form.body:
            body = Gtk.Label(label=form.body, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            body.add_css_class("dim-label")
            body.add_css_class("caption")
            card.append(body)
        card.append(form.child)
        wrong = label("", css=("error", "caption"))
        card.append(wrong)
        said = label("", css=("dim-label", "caption"))
        card.append(said)
        row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        cancel = Gtk.Button(label=wording.say("common.cancel"))
        go = Gtk.Button(label=form.verb)
        go.add_css_class("suggested-action")
        row.append(cancel)
        row.append(go)
        card.append(row)

        filling = []

        def checked():
            try:
                return form.check()
            except Exception as e:  # noqa: BLE001 (said, as what's wrong)
                return str(e) or type(e).__name__

        def looks():
            open_ = spec.get("state", "open") == "open"
            form.child.set_sensitive(open_)
            row.set_visible(open_)
            problem = checked() if open_ else None
            go.set_sensitive(problem is None)
            filled = any(str(v).strip() for v in form.values().values())
            wrong.set_text((spec.get("error") or problem or "") if open_ and (filled or spec.get("error")) else "")
            wrong.set_visible(bool(wrong.get_text()))
            said.set_text({"submitted": wording.say("meta.form_done", said=spec.get("said") or form.verb)
                           if form.said else "", "cancelled": wording.say("meta.cancelled")}.get(spec.get("state"), ""))
            said.set_visible(not open_ and bool(said.get_text()))
            card.set_tooltip_text(wording.say("meta.unpin_tip") if open_ and spec.get("pinned", True) else
                                  wording.say("meta.pin_tip") if open_ else None)

        def edited():
            if filling:
                return
            values = form.values()
            for f in list(missed):  # (one that couldn't be filled: kept as it was, unless it's been changed since)
                if values.get(f) == base.get(f) and f in kept:
                    values[f] = kept[f]
                else:
                    missed.discard(f)
            spec["values"], spec["error"] = values, None
            looks()
            self.save()

        def sync():
            """The card as its form's kept now: filled as it's been elsewhere, submitted or cancelled there."""
            now = {k: v for k, v in (spec.get("values") or {}).items() if v not in (None, "")}
            if any(form.values().get(k) != v for k, v in now.items() if k not in missed):
                filling.append(True)
                try:
                    unmatched = form.fill(now, partial=True)
                finally:
                    filling.clear()
                # (what this card can't show, a host its list hasn't: kept as it's kept, as form_card has it)
                kept.clear()
                kept.update(spec.get("values") or {})
                missed.clear()
                missed.update(unmatched)
                base.clear()
                base.update(form.values())
            looks()

        def submit(*_):
            if spec.get("state", "open") != "open":  # (submitted already: in another window, say)
                return looks()
            if (problem := checked()) is not None:
                spec["error"] = problem
                return looks()
            try:
                done = form.submit()
            except Exception as e:  # noqa: BLE001 (said on the form, not raised in GTK's handler)
                spec["error"] = str(e) or type(e).__name__
                return looks()
            spec.update(state="submitted", said=done, error=None)
            self.told().append(f"(The user submitted the form {spec['id']}, {form.heading}: {done})")
            self._form_closed(spec)
            looks()

        def dismiss(*_):
            if spec.get("state", "open") != "open":  # (submitted or cancelled already: in another window, say)
                return looks()
            spec.update(state="cancelled", error=None)
            self.told().append(f"(The user cancelled the form {spec['id']}, {form.heading}.)")
            self._form_closed(spec)
            looks()
        form.changed(edited)
        go.connect("clicked", submit)
        cancel.connect("clicked", dismiss)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", lambda _g, _n, x, y: self._form_menu(card, spec, x, y))
        card.add_controller(menu)
        card.looks, card.sync = looks, sync
        looks()
        return form, card

    def _form_menu(self, card, spec, x, y):
        """A form's menu (right-click): Unpin, while it's pinned (it scrolls away with the conversation); else Pin."""
        if spec.get("state", "open") != "open":
            return
        pinned = spec.get("pinned", True)
        pop = Gtk.Popover(has_arrow=False)
        btn = Gtk.Button(label=wording.say("meta.unpin") if pinned else wording.say("meta.pin"))
        btn.add_css_class("flat")

        def toggle(*_):
            pop.popdown()
            spec["pinned"] = not pinned
            card.looks()
            self.save()
            self._pin_forms()
        btn.connect("clicked", toggle)
        pop.set_child(btn)
        pop.set_parent(card)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        self.form_menu = pop
        pop.popup()

    def _form_closed(self, spec):
        """A form submitted or cancelled: kept so, and in its place in the conversation, pinned no more."""
        self.save(now=True)
        entry = self.forms_open.get(spec["id"])
        if entry is not None and entry["widget"].get_parent() is self.pins:
            self._unpin(entry)

    PIN_MARGIN = 8  # px: a pinned form hovers this far from the pane's top

    def _pin_soon(self):
        """_pin_forms once the next frame's laid out and drawn: a scroll moves what's in the conversation only as
        the frame's allocated, and what's just been drawn again has no place till then."""
        clock = self.get_frame_clock()
        if self._pin_after is not None or clock is None:
            return

        def after(c):
            c.disconnect(self._pin_after)
            self._pin_after = None
            self._pin_forms()
        self._pin_after = clock.connect("after-paint", after)
        clock.request_phase(Gdk.FrameClockPhase.AFTER_PAINT)

    def _pin_forms(self):
        """The form pinned over the conversation's top: the last one not yet submitted, and pinned, whose place has
        gone up past the pane's top (its place kept as a gap its height); none once its place is back in view."""
        pinned = next((e for e in self.forms_open.values() if e["widget"].get_parent() is self.pins), None)
        want = None
        for e in self.forms_open.values():
            slot, spec = e["slot"], e["spec"]
            if slot is None or slot.get_root() is None or spec.get("state", "open") != "open" or \
                    not spec.get("pinned", True) or e["form"] is None:
                continue
            if not slot.get_mapped() or slot.get_width() <= 0:  # (not laid out yet: looked at again once it is, a
                self._pin_waits += 1  # few frames at most; hidden, as the pane's shown again: its range changes then)
                if self._pin_waits <= 3 and self.get_mapped():
                    self._pin_soon()
                return False
            ok, at = slot.compute_point(self.scroll, Graphene.Point().init(0, 0))
            if ok and at.y < self.PIN_MARGIN:
                want = e
        self._pin_waits = 0
        if want is not pinned:
            if pinned is not None:
                self._unpin(pinned)
            if want is not None:
                self._pin(want)
        return False

    def _pin(self, entry):
        widget, slot = entry["widget"], entry["slot"]
        slot.set_size_request(-1, widget.get_height())
        slot.remove(widget)
        widget.add_css_class("meta-pinned")
        self.pins.append(widget)

    def _unpin(self, entry):
        widget, slot = entry["widget"], entry["slot"]
        self.pins.remove(widget)
        widget.remove_css_class("meta-pinned")
        if slot is not None:
            slot.set_size_request(-1, -1)
            if slot.get_root() is not None:
                slot.append(widget)

    def open_form(self, turn, args):
        name = str(args.get("form") or "").strip()
        forms = self.forms()
        if name not in forms:
            raise ValueError(f"No form {name!r}: they're {', '.join(forms) or 'none'} (app_index forms).")
        form = forms[name][1]()
        values = args.get("values") or {}
        if values:
            form.fill(values)
        spec = {"kind": "form", "form": name, "id": uuid.uuid4().hex[:6], "values": form.values(), "state": "open",
                "pinned": True}
        self._show(turn, spec)
        problem = form.check()
        return {"text": f"Form {spec['id']} ({form.heading}) is up for the user to finish and submit; you don't "
                        f"submit it. Its fields: {json.dumps(form.described(), ensure_ascii=False)}. " +
                        (f"Still wrong: {problem}" if problem else "It's ready to submit.")}

    def fill_form(self, turn, args):
        fid = str(args.get("id") or "").strip()
        spec = self._form_spec(fid, turn)
        if spec is None:
            raise ValueError(f"No form {fid!r} of yours: open_form gives a form's id.")
        if spec.get("state", "open") != "open":
            raise ValueError(f"That form is {spec['state']} already.")
        values = args.get("values") or {}
        entry = self.forms_open.get(fid)
        if entry is not None and entry["form"] is not None:
            form = entry["form"]
            form.fill(values)  # (its card says so as it's filled: Form.changed)
        else:
            form = self.forms()[spec["form"]][1]()
            form.fill({**(spec.get("values") or {}), **values})
            spec["values"] = form.values()
        problem = form.check()
        return {"text": ("Filled. Still wrong: " + problem) if problem else "Filled: it's ready for the user to submit."}
