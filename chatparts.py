"""chatparts — a chat's bars and turns: Update at a sidebar's foot (UpdateBar), what's in a host's way (HostState),
a background task's end line, the context meter and its usage (ContextMeter, ContextUsage), a compaction's mark
(CompactMark; its summary's heading pinned over the chat while the summary's read: SummaryPin), your turns (UserTurn;
queued, sent by another, held: QueuedTurn, SenderTurn, HeldTurn), a chat past its window (Exhausted) and the find bar
(FindBar).
"""
import html
import math
import re
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Graphene, Gtk, Pango  # noqa: E402,F401

from baudelib import wording  # noqa: E402
from baudelib.transcripts import summary_tools  # noqa: E402
from widgets import WorkingDots, empty_label  # noqa: E402
from markdownview import MarkdownView  # noqa: E402


class UpdateBar(Gtk.Box):
    """At the foot of a sidebar, under a rule from what's listed above it: Update, while a new version's waiting to
    be restarted onto (what it waits for in its tooltip: say); clicked, on_click (restart now; asked only if that
    stops something)."""

    def __init__(self, on_click):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False)
        self.add_css_class("update-bar")
        self.append(Gtk.Separator())
        self.button = Gtk.Button(label=wording.say("update_bar.update"), halign=Gtk.Align.START, margin_start=6,
                                 margin_end=6, margin_top=4, margin_bottom=4)
        self.button.add_css_class("flat")
        self.button.connect("clicked", lambda *_: on_click())
        self.append(self.button)

    def say(self, waiting):
        """waiting: what the restart waits for (shown in the tooltip), or None: no update waiting, no bar."""
        self.set_visible(waiting is not None)
        if waiting is not None:
            self.button.set_tooltip_text(
                wording.say("update_bar.waiting_for", waits="\n".join(f"• {w}" for w in waiting)) if waiting else
                wording.say("update_bar.ready"))


class HostState(Gtk.Revealer):
    """Over a sidebar's list: what's in the way of the host it lists (gone, deactivated), an island inset from the
    sidebar's edges, as an empty list says it (the subagents' "No subagents yet"): a boxed-list with only its
    placeholder (empty_label), outlined as the side panel's lists are. set_title: what it says (markup);
    set_revealed: whether it's shown."""

    def __init__(self):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.label = empty_label("")
        self.label.set_margin_top(12)
        self.label.set_margin_bottom(12)
        self.label.set_margin_start(12)
        self.label.set_margin_end(12)
        self.label.set_wrap(True)
        self.label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)  # (a long host name, an address: broken too)
        self.label.set_justify(Gtk.Justification.CENTER)
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, focusable=False, margin_start=8, margin_end=8,
                          margin_top=6, margin_bottom=6, css_classes=["boxed-list", "host-state"])
        box.set_placeholder(self.label)
        self.set_child(box)

    def set_title(self, markup):
        self.label.set_markup(markup)

    def set_revealed(self, on):
        self.set_reveal_child(on)

    def get_revealed(self):
        return self.get_reveal_child()


TASK_ENDINGS_SAID = {"completed": wording.say("task_notes.finished"), "failed": wording.say("task_notes.failed"),
                     "killed": wording.say("task_notes.stopped"), "error": wording.say("task_notes.failed")}


def task_note_line(note):
    """A background task's ending (a <task-notification>: baudelib.transcripts.task_notification's), as one quiet line,
    as Baude's chat says one whose call isn't drawn: (markup "<b>Background command</b> · what it was — finished (exit
    0)", plain text, whether it failed)."""
    status = note.get("status") or "completed"
    summary = html.unescape(note.get("summary") or "")
    what = wording.say("common.agent") if summary.startswith(("Agent", "Subagent")) else \
        wording.say("task_notes.background_command")
    quoted = re.match(r'[^"]*"(.*)"', summary, re.S)
    desc = (quoted.group(1) if quoted else summary).strip().splitlines()[:1]
    desc = desc[0] if desc else wording.say("task_notes.unnamed")
    if len(desc) > 80:
        desc = desc[:79] + "…"
    ending = TASK_ENDINGS_SAID.get(status, status)
    if (code := re.search(r"exit code (\d+)", summary)) is not None:
        ending = wording.say("task_notes.ending_exit", ending=ending, code=code.group(1))
    return (f"<b>{html.escape(what)}</b> · {html.escape(desc)} — {html.escape(ending)}",
            f"{what} · {desc} — {ending}", status in ("failed", "killed", "error"))


class ShutBar(Gtk.Revealer):
    """A revealer that, shut, is no width. Slid up or down, GTK's is as wide shut as open: a bar slid away kept what it's
    in from going any narrower than the bar (Feedback's Capture bar held Record's notes at 530px)."""

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL and not (self.get_reveal_child() or self.get_child_revealed()):
            return 0, 0, -1, -1
        least, natural, _, _ = Gtk.Revealer.do_measure(self, orientation, for_size)
        return least, natural, -1, -1


class WidthLayout(Gtk.BinLayout):
    """A BinLayout that asks its widget how wide it is: widget.width_of(least, natural) → (least, natural). A search bar, a
    level bar are measured by their layout managers, not themselves, so a do_measure of theirs is never asked."""

    def do_measure(self, widget, orientation, for_size):
        least, natural, _, _ = Gtk.BinLayout.do_measure(self, widget, orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            least, natural = widget.width_of(least, natural)
        return least, natural, -1, -1


def fmt_tokens(n):
    return wording.say("tokens.millions", n=f"{n / 1_000_000:.1f}".removesuffix(".0")) if n >= 1_000_000 else \
        wording.say("tokens.thousands", n=f"{n / 1000:.1f}".removesuffix(".0")) if n >= 1000 else str(n)


class ContextMeter(Gtk.LevelBar):
    """The context bar, its count ("24k / 200k") drawn in it: over the fill in the window's background colour, after
    it in its text colour, so each part reads against what's under it. WIDTH wide given the room, narrower as its
    column is (a width_request held it open)."""

    WIDTH = 110  # px

    def __init__(self, **kw):
        super().__init__(**kw)
        self.text = ""
        self.add_css_class("context-meter")
        self.set_layout_manager(WidthLayout())

    def width_of(self, least, natural):
        return least, max(natural, self.WIDTH)

    def set_text(self, text):
        self.text = text
        self.update_property([Gtk.AccessibleProperty.DESCRIPTION], [text])
        self.queue_draw()

    def do_snapshot(self, snapshot):
        Gtk.LevelBar.do_snapshot(self, snapshot)
        width, height = self.get_width(), self.get_height()
        if not self.text or width <= 0:
            return
        layout = self.create_pango_layout(self.text)
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_scale_new(0.8))
        attrs.insert(Pango.attr_font_features_new("tnum"))
        layout.set_attributes(attrs)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_width((width - 6) * Pango.SCALE)
        text_w, text_h = (v / Pango.SCALE for v in layout.get_size())
        origin = Graphene.Point().init(max(3, (width - text_w) / 2), (height - text_h) / 2)
        fill = math.floor(width * (self.get_value() - self.get_min_value()) / ((self.get_max_value() - self.get_min_value()) or 1))
        if self.get_direction() == Gtk.TextDirection.RTL:
            spans = ((width - fill, width, True), (0, width - fill, False))
        else:
            spans = ((0, fill, True), (fill, width, False))
        found, bg = self.get_style_context().lookup_color("window_bg_color")
        for start, end, on_fill in spans:
            if end <= start:
                continue
            snapshot.push_clip(Graphene.Rect().init(start, 0, end - start, height))
            snapshot.save()
            snapshot.translate(origin)
            snapshot.append_layout(layout, bg if on_fill and found else self.get_color())
            snapshot.restore()
            snapshot.pop()


class ContextUsage(Gtk.Box):
    """How much of its context window a session's used, Baude's chat's and Meta's alike: "context" and a bar with
    "24k / 200k" in it, amber past 60%, red past 85%; its tip, what the tokens are (set_usage: get_context_usage's
    answer). Past the window ("12.4k / 8.2k"), the bar's full: over. Till its session's said, the window its model has,
    as far as it's known (window: a function giving it, None where it isn't): "— / 200k"."""

    def __init__(self, window=None, **kw):
        super().__init__(spacing=6, tooltip_text=wording.say("context_usage.tooltip"), **kw)
        self.window = window
        self.label = Gtk.Label(xalign=1, ellipsize=Pango.EllipsizeMode.END)
        self.bar = ContextMeter(min_value=0, max_value=1, value=0, valign=Gtk.Align.CENTER)
        for name in ("low", "high", "full"):
            self.bar.remove_offset_value(name)
        self.bar.add_offset_value("ctx-low", 0.6)
        self.bar.add_offset_value("ctx-mid", 0.85)
        self.bar.add_offset_value("ctx-high", 1.0)
        self.append(self.label)
        self.append(self.bar)
        self.reset()

    def set_usage(self, usage):
        used, total = usage.get("totalTokens") or 0, usage.get("maxTokens") or 0
        if not total and usage.get("showWithoutLimit"):
            self.said = True
            self.bar.set_visible(False)
            self.label.set_visible(True)
            self.over = False
            self.label.set_text(wording.say("context_usage.used_no_limit", used=fmt_tokens(used))
                                if usage.get("totalTokens") is not None else
                                wording.say("context_usage.unknown_no_limit"))
            self.set_tooltip_text(wording.say("context_usage.tooltip"))
            return
        if not total:
            return
        self.said = True
        self.bar.set_visible(True)
        self.over = used > total
        self.label.set_text("")
        self.label.set_visible(False)
        self.bar.set_text(wording.say("context_usage.count", used=fmt_tokens(used), total=fmt_tokens(total)))
        self.bar.set_value(min(1.0, used / total))
        lines = [f"{c['name']}: {fmt_tokens(c.get('tokens', 0))}" for c in usage.get("categories") or []
                 if c.get("tokens") and c.get("kind") != "deferred"]
        self.set_tooltip_text(wording.say("context_usage.usage_percent", percent=f"{used / total:.0%}",
                                          categories="\n".join(lines)))

    def reset(self):
        """No session's: nothing used yet, of the window its model has (window_changed)."""
        self.over = self.said = False
        self.label.set_text("")
        self.label.set_visible(False)
        self.bar.set_visible(True)
        self.bar.set_value(0)
        self.window_changed()

    def window_changed(self):
        """Its model's another, or what's known of its window is: shown, till a session says what it's used."""
        if self.said:
            return
        total = self.window() if self.window else None
        self.bar.set_text(wording.say("context_usage.window_only", total=fmt_tokens(total)) if total else
                          wording.say("context_usage.nothing"))
        self.set_tooltip_text(wording.say("context_usage.window_known", total=total) if total else
                              wording.say("context_usage.tooltip"))


def compact_text(meta):
    """What a compaction did, as its marker says it: "Conversation compacted automatically · 167k tokens of context →
    18k · 41s". meta: a compact_boundary's compact_metadata as claude streams it (trigger, pre_tokens, post_tokens,
    duration_ms) or its compactMetadata as the transcript keeps it (preTokens, postTokens, durationMs)."""
    meta = meta if isinstance(meta, dict) else {}
    pre, post = meta.get("pre_tokens", meta.get("preTokens")), meta.get("post_tokens", meta.get("postTokens"))
    took = meta.get("duration_ms", meta.get("durationMs"))
    trigger = meta.get("trigger")
    parts = [wording.say("compaction.compacted_auto") if trigger == "auto" else
             wording.say("compaction.compacted_manual") if trigger == "manual" else wording.say("compaction.compacted")]
    if isinstance(pre, int) and pre > 0:
        parts.append(wording.say("compaction.tokens_before_after", pre=fmt_tokens(pre), post=fmt_tokens(post))
                     if isinstance(post, int) and post > 0 else
                     wording.say("compaction.tokens_before", pre=fmt_tokens(pre)))
    if isinstance(took, (int, float)) and took >= 1000:
        secs = round(took / 1000)
        parts.append(wording.say("time.minutes_seconds", minutes=secs // 60, seconds=secs % 60) if secs >= 60 else
                     wording.say("time.seconds", seconds=secs))
    return " · ".join(parts)


def compact_said(msg):
    """What a message of claude's stream says of a compaction, as (what, detail), or None:
      ("running", characters of summary written or None): its status "compacting";
      ("failed", claude's compact_error or None): its status null, compact_result "failed";
      ("succeeded", None): compact_result "success" (its compact_boundary next);
      ("ended", None): its status null with no result (a PreCompact hook blocked it, it had nothing to do): none made;
      ("done", compact_metadata): compact_boundary;
      ("summary", text): the summary it wrote, as the message after its boundary (isSynthetic, its content a string;
      not a message of the conversation's replayed).
    A subagent's (parent_tool_use_id) are its own."""
    if not isinstance(msg, dict) or msg.get("parent_tool_use_id"):
        return None
    kind, sub = msg.get("type"), msg.get("subtype")
    if kind == "system" and sub == "compact_boundary":
        return "done", msg.get("compact_metadata") or {}
    if kind == "system" and sub == "status" and msg.get("status") == "compacting":
        return "running", msg.get("compact_output_chars")
    if kind == "system" and sub == "status" and msg.get("status") is None:
        result = msg.get("compact_result")
        if result == "failed":
            return "failed", msg.get("compact_error")
        if result == "success":
            return "succeeded", None
        return None if "permissionMode" in msg else ("ended", None)  # (a mode changed says its status too)
    content = (msg.get("message") or {}).get("content")
    if kind == "user" and msg.get("isSynthetic") and not msg.get("isReplay") and isinstance(content, str):
        return "summary", content
    return None


class CompactMark(Gtk.Box):
    """Where Claude Code compacted a conversation (summarised what came before, to free its context window), in a chat
    (Baude's, Record's reader, Meta's): a quiet line between two rules. While it runs (claude's status "compacting"),
    "Compacting the conversation", its dots going, and how much of the summary is written where claude says it;
    done (compact_boundary), what it did (compact_text); failed or cut short, that. The summary it wrote, once known (the
    message claude adds after the boundary; the transcript's isCompactSummary record), folded under it (set_summary)."""

    def __init__(self, meta=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6)
        self.add_css_class("compact-mark")
        row, said = Gtk.Box(spacing=10), Gtk.Box()
        self.label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, selectable=True)
        self.label.add_css_class("sysnote")
        self.dots = WorkingDots()
        self.dots.add_css_class("sysnote")
        self.dots.set_margin_start(2)
        said.append(self.label)
        said.append(self.dots)
        for w in (Gtk.Separator(hexpand=True, valign=Gtk.Align.CENTER), said,
                  Gtk.Separator(hexpand=True, valign=Gtk.Align.CENTER)):
            row.append(w)
        self.append(row)
        self.fold = None
        self.set_tooltip_text(wording.say("compaction.tooltip"))
        self.finish(meta) if meta is not None else self.running()

    def running(self, written=None):
        """Under way; written: how many characters of the summary claude says it's written (an automatic one's)."""
        self.state = "running"
        self.label.set_text(wording.say("compaction.running_written", written=written)
                            if isinstance(written, int) and written > 0 else wording.say("compaction.running"))
        self.label.remove_css_class("error")
        self.dots.set_working(True)

    def finish(self, meta):
        self.state = "done"
        self.dots.set_working(False)
        self.label.remove_css_class("error")
        self.label.set_text(compact_text(meta))

    def fail(self, error=None, stopped=False):
        """It failed (claude's compact_result "failed", and its compact_error if it gave one), or its turn ended before
        it did (stopped)."""
        self.state = "failed"
        self.dots.set_working(False)
        self.label.set_text(wording.say("compaction.unfinished") if stopped else
                            wording.say("compaction.failed_because", error=error) if error else
                            wording.say("compaction.failed"))
        self.label.add_css_class("error")

    opened = weakref.WeakSet()  # (every chat's marks with their summary open: what SummaryPin looks among)

    def set_summary(self, text):
        """The summary under the mark, folded. The list of tools the conversation had used that Claude Code puts in it
        is said as how many, in a quiet line at its top."""
        if not text or self.fold is not None:
            return
        self.fold = summary_fold(text, self)
        self.append(self.fold)

def summary_fold(text, owner):
    """A compaction summary's fold (the heading "Summary of the earlier conversation", closed): the list of tools the
    conversation had used that Claude Code puts in it said as how many, in a quiet line at its top, then the summary.
    owner: the widget SummaryPin looks for while the fold's open (a CompactMark; a LoneSummary)."""
    text, tools = summary_tools(text)
    head = Gtk.Label(label=wording.say("compaction.summary"))
    head.add_css_class("sysnote")
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=18, margin_bottom=6)
    if tools:
        used = Gtk.Label(label=wording.say("compaction.tools_before", count=len(tools)), xalign=0, selectable=True,
                         tooltip_text=", ".join(tools))
        used.add_css_class("sysnote")
        body.append(used)
    body.append(MarkdownView(text))
    fold = Gtk.Expander(label_widget=head, child=body)

    def opened(fold, _p):
        (CompactMark.opened.add if fold.get_expanded() else CompactMark.opened.discard)(owner)
        for pin in list(SummaryPin.live):
            pin.look()
    fold.connect("notify::expanded", opened)
    return fold


class LoneSummary(Gtk.Box):
    """A compaction's summary drawn before its mark is (its boundary in a chunk of history not drawn yet, or further
    off than the mark's looked for it): folded as it is under the mark, its heading pinned as that one's; adopted
    by the mark once that's drawn (adopt_summary)."""

    def __init__(self, text):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.compact_summary = text
        self.fold = summary_fold(text, self)
        self.append(self.fold)

    def dispose_fold(self):
        """Gone from the chat (a mark adopted its summary): no longer one SummaryPin looks among."""
        CompactMark.opened.discard(self)


class SummaryPin(Gtk.Expander):
    """An open compaction summary's heading, pinned at the top of the chat it's in while the heading's scrolled above
    the top and the summary's still in view (a summary runs to many screens): the same heading, opened; clicked, the
    summary folds and the chat's scrolled back to its mark. Laid over the chat's scroller (an overlay's), and looked
    for only as the chat scrolls or a summary's opened or folded: nothing's done while none is open."""

    live = weakref.WeakSet()

    def __init__(self, scroller):
        head = Gtk.Label(label=wording.say("compaction.summary"))
        head.add_css_class("sysnote")
        super().__init__(label_widget=head, expanded=True, visible=False, valign=Gtk.Align.START,
                         halign=Gtk.Align.START)
        self.add_css_class("summary-pin")
        self.scroller, self.mark = scroller, None
        adj = scroller.get_vadjustment()
        for change in ("value-changed", "changed"):  # (scrolled; laid out again)
            adj.connect(change, lambda *_: self.look())
        self.connect("notify::expanded", self._fold)
        SummaryPin.live.add(self)

    after = None  # (the frame clock's after-paint handler, while a look's waiting for the frame)

    def look(self):
        """Looked at again once the next frame's laid out (a scroll moves what's in the chat only as the frame's
        allocated): nothing, while no summary's open."""
        clock = self.scroller.get_frame_clock()
        if (not CompactMark.opened and not self.get_visible()) or self.after is not None or clock is None:
            return

        def drawn(c):
            c.disconnect(self.after)
            self.after = None
            self._place()
        self.after = clock.connect("after-paint", drawn)
        clock.request_phase(Gdk.FrameClockPhase.AFTER_PAINT)

    def _place(self):
        """Shown over the open summary whose heading's above the chat's top, the rest of it in view, if one is."""
        found = None
        for mark in list(CompactMark.opened):
            fold = mark.fold
            if not mark.get_mapped() or not fold.is_ancestor(self.scroller):
                continue
            ok, top = fold.compute_point(self.scroller, Graphene.Point())
            if ok and top.y < 0 and top.y + fold.get_height() > max(self.get_height(), 1):
                found = (mark, top.x, fold.get_width())
                break
        self.mark = found and found[0]
        if found:
            self.set_margin_start(max(0, round(found[1])))
            self.set_size_request(found[2], -1)
        self.set_visible(found is not None)

    def _fold(self, *_):
        if self.get_expanded():
            return
        self.set_expanded(True)  # (this one stays as the heading of an open summary; the summary itself folds)
        mark, self.mark = self.mark, None
        self.set_visible(False)
        if mark is None or mark.fold is None:
            return
        ok, top = mark.compute_point(self.scroller, Graphene.Point())
        mark.fold.set_expanded(False)
        if ok:  # (back to its mark, at the chat's top)
            adj = self.scroller.get_vadjustment()
            adj.set_value(max(0.0, adj.get_value() + top.y))


class UserTurn(Gtk.Box):
    """A message of yours in a conversation (Baude's chat, a side question's, Meta's): the whole width of it is its row,
    its bubble at the right. With the pointer anywhere in the row, a revert button fades in just outside the bubble,
    level with its last line, and fades out as it leaves (win: what it's in, its revert_to: this message and
    everything after it taken back, and what it said put back in the box; its can_revert: whether it can be now)."""

    def __init__(self, win, bubble, text, uuid):
        super().__init__(spacing=6, hexpand=True)
        self.win, self.bubble, self.text, self.uuid = win, bubble, text, uuid
        bubble.set_hexpand(False)  # (a list in it, which fills its width, doesn't take the row: the button's place)
        self.revert = Gtk.Button(child=Gtk.Image(icon_name="view-refresh-symbolic", pixel_size=12),
                                 tooltip_text=wording.say("chat_turns.revert"), hexpand=True, halign=Gtk.Align.END,
                                 valign=Gtk.Align.END, opacity=0, can_target=False)
        self.revert.add_css_class("flat")
        self.revert.add_css_class("circular")
        self.revert.add_css_class("revert-turn")
        self.revert.connect("clicked", lambda *_: win.revert_to(self))
        self.append(self.revert)
        self.append(bubble)
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_: self.show_revert(True))
        hover.connect("leave", lambda *_: self.show_revert(False))
        self.add_controller(hover)

    BUTTON, PADDING = 24, 8  # px: the button's height (.revert-turn), the bubble's padding at its foot (.user-bubble)
    FADE_MS = 150  # (the button fading in as the pointer comes onto the row, and out as it leaves)
    fade = None

    def show_revert(self, on):
        on = on and self.uuid is not None and self.win.can_revert()
        if on:  # (centred on the bubble's last line: its padding and half a line up, as the chat's font is now)
            metrics = self.bubble.get_pango_context().get_metrics(None, None)
            line = (metrics.get_height() or metrics.get_ascent() + metrics.get_descent()) / Pango.SCALE
            self.revert.set_margin_bottom(max(0, round(self.PADDING + line / 2 - self.BUTTON / 2)))
        self.revert.set_can_target(on)
        if self.fade is None:
            self.fade = Adw.TimedAnimation.new(self.revert, 0.0, 1.0, self.FADE_MS,
                                               Adw.PropertyAnimationTarget.new(self.revert, "opacity"))
            self.fade.set_easing(Adw.Easing.EASE_OUT_QUAD)  # (libadwaita's transitions' ease)
        self.fade.set_value_from(self.revert.get_opacity())  # (from where it is: back the other way partway through)
        self.fade.set_value_to(1.0 if on else 0.0)
        self.fade.play()



class QueuedTurn(Gtk.Box):
    """A message of yours written while the agent's at a turn, not sent yet (Baude's chat: its queue, under the
    conversation): grey, and with the pointer on its row, ✕ (dropped), ↑ (sent: the turn interrupted, then it and every
    queued message before it sent, together) and the revert button (put back in the box to edit) fade in beside it, as
    a sent message's revert does. Its bubble's narrower by the buttons. win: its queue_send, queue_edit, queue_drop.
    said: the uuid it was sent by, if it was and its host may not have taken it (left_turn_ended): sent by it again."""

    def __init__(self, win, bubble, text, atts, said=None):
        super().__init__(spacing=6, hexpand=True)
        self.win, self.bubble, self.text, self.atts, self.said = win, bubble, text, atts, said
        bubble.set_hexpand(False)  # (as UserTurn's: its buttons beside it whatever's in it)
        bubble.add_css_class("queued-bubble")
        self.buttons = Gtk.Box(spacing=2, hexpand=True, halign=Gtk.Align.END, valign=Gtk.Align.END, opacity=0,
                               can_target=False)
        for icon, tip, act in (("window-close-symbolic", wording.say("chat_turns.queued_delete"), win.queue_drop),
                               ("↑", wording.say("chat_turns.queued_send_now"), win.queue_send),
                               ("view-refresh-symbolic", wording.say("chat_turns.queued_edit"), win.queue_edit)):
            b = Gtk.Button(child=Gtk.Label(label="↑", css_classes=["queue-arrow"]) if icon == "↑" else
                           Gtk.Image(icon_name=icon, pixel_size=12), tooltip_text=tip)  # (an arrow, shaft and head)
            for c in ("flat", "circular", "revert-turn"):
                b.add_css_class(c)
            b.connect("clicked", lambda _b, act=act: act(self))
            self.buttons.append(b)
        self.append(self.buttons)
        self.append(bubble)
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_: self.show_buttons(True))
        hover.connect("leave", lambda *_: self.show_buttons(False))
        self.add_controller(hover)

    fade = None

    def show_buttons(self, on):
        if on:  # (level with its last line, as UserTurn's revert)
            metrics = self.bubble.get_pango_context().get_metrics(None, None)
            line = (metrics.get_height() or metrics.get_ascent() + metrics.get_descent()) / Pango.SCALE
            self.buttons.set_margin_bottom(max(0, round(UserTurn.PADDING + line / 2 - UserTurn.BUTTON / 2)))
        self.buttons.set_can_target(on)
        if self.fade is None:
            self.fade = Adw.TimedAnimation.new(self.buttons, 0.0, 1.0, UserTurn.FADE_MS,
                                               Adw.PropertyAnimationTarget.new(self.buttons, "opacity"))
            self.fade.set_easing(Adw.Easing.EASE_OUT_QUAD)
        self.fade.set_value_from(self.buttons.get_opacity())
        self.fade.set_value_to(1.0 if on else 0.0)
        self.fade.play()

class SenderTurn(UserTurn):
    """A message to the chat from another than you, as its host's sender store says (a Bot, another chat, a subagent's
    answer held for it): where yours are, grey as a queued one is, with who it's from at its top (the window's
    sender_bubble), and reverted as yours are, but its words aren't put in your box: what's there is sent after it, or
    nothing (uuid: the message's, once it's in the chat's transcript)."""


class HeldTurn(Gtk.Box):
    """A message its host held for the chat, not sent (its held: a subagent's answer that came after the chat's host
    restarted, which doesn't start its agent): under the conversation with the queue, as a queued message is, and
    with the pointer on its row ✕ (dropped) and ↑ (sent) fade in beside it, as a queued message's do. held: as its
    host lists it ({id, text, shown, sender, at}); sending: what's drawn as sent while its host's answer's to come;
    win: its held_drop, held_send."""

    def __init__(self, win, bubble, held):
        super().__init__(spacing=6, hexpand=True)
        self.win, self.bubble, self.held, self.sending = win, bubble, held, None
        bubble.set_hexpand(False)
        bubble.add_css_class("queued-bubble")
        self.buttons = Gtk.Box(spacing=2, hexpand=True, halign=Gtk.Align.END, valign=Gtk.Align.END, opacity=0,
                               can_target=False)
        for icon, tip, act in (("window-close-symbolic", wording.say("held.delete"), win.held_drop),
                               ("↑", wording.say("held.send"), win.held_send)):
            b = Gtk.Button(child=Gtk.Label(label="↑", css_classes=["queue-arrow"]) if icon == "↑" else
                           Gtk.Image(icon_name=icon, pixel_size=12), tooltip_text=tip)
            for c in ("flat", "circular", "revert-turn"):
                b.add_css_class(c)
            b.connect("clicked", lambda _b, act=act: act(self))
            self.buttons.append(b)
        self.append(self.buttons)
        self.append(bubble)
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_: QueuedTurn.show_buttons(self, True))
        hover.connect("leave", lambda *_: QueuedTurn.show_buttons(self, False))
        self.add_controller(hover)

    fade = None


class Exhausted:
    """A chat whose conversation is more than its model's window holds (ContextUsage.over): "Context Exhausted" said
    over its box, in plain grey, and its send button off (Composer.gate: Enter too, its owner looking), till a model
    with a bigger window's picked or the conversation's cleared. The text box stays as it is, to write in. It's the
    chat's own state, which only you change: what's in its way from outside it is said in a banner. box: the Composer,
    already in its column; chat: (kept for its callers)."""

    def __init__(self, chat, box):
        self.box, self.on = box, False
        self.label = Gtk.Label(label=wording.say("context_usage.exhausted"), xalign=0, margin_start=10, visible=False,
                               wrap=True, max_width_chars=1)  # (a longer reason wrapped in the column, not widening it)
        self.label.add_css_class("dim-label")
        box.get_parent().insert_child_after(self.label, box.get_prev_sibling())

    def set(self, on, text=wording.say("context_usage.exhausted")):
        """On or off; on, what's said (This model is unavailable, Meta's: its model's gone from the mesh)."""
        self.on = on
        self.label.set_text(text)
        self.label.set_visible(on)
        self.box.gate("exhausted", on)


class FindBar(Gtk.SearchBar):
    """A search bar that, closed, is no width, as ShutBar."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.set_layout_manager(WidthLayout())

    def width_of(self, least, natural):
        if self.get_search_mode() or self.get_first_child().get_child_revealed():  # (its revealer)
            return least, natural
        return 0, 0
