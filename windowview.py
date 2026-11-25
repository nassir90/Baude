"""windowview — what a Baude window's chat shows and how it scrolls (WindowView): a turn's parts and tool cards
added to it, the pointer kept still while it scrolls, history far from the view taken out of what's drawn, the view
held to its message, text zoom, full width, the history drawn and its earlier chunks.
"""
import base64
import collections
import html
import math
import os
import re
import time
from baudelib.transcripts import _first_text, _prompt_texts, ChunkFill, clean_prompt, HistoryIndex, ref_epoch
from baudelib import tokens  # noqa: E402  (Baude's measures: schema/tokens, the Android app's too)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

import chatparts  # noqa: E402
import composerview  # noqa: E402
import chatfind  # noqa: E402
from chatparts import UserTurn  # noqa: E402
from widgets import ZOOM_MAX, ZOOM_MIN, fmt_scale, pretty_path, save_settings  # noqa: E402,F401
from markdownview import MarkdownView, link_markup, sel_label  # noqa: E402,F401
from signalnames import Signal  # noqa: E402
from toolcards import (  # noqa: E402
    STOP_SAID_RE, ToolCard, ToolGroup, api_error_label, html_title, summarize_tool, task_note_widget,
)
from chatinput import Attachment, attachment_widget  # noqa: E402
from sessionlist import main_folder, remote_info  # noqa: E402
from windowremote import MESH_CHAT_TOOLS, MeshCard  # noqa: E402


class WindowView:
    """A window's chat as it's shown: its parts, scrolling, the history drawn, text zoom, full width."""

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

    # ---- full width
    def apply_full_width(self, on):
        # Adw.Clamp can't be switched off, so "off" is just a limit wider than any screen
        self.clamp.set_maximum_size(100_000 if on else tokens.px("gap.chat-max"))
        self.clamp.set_tightening_threshold(100_000 if on else tokens.px("gap.chat-tighten"))

    def on_fullwidth_toggled(self, action, value):
        action.set_state(value)
        self.apply_full_width(value.get_boolean())
        save_settings(full_width=value.get_boolean())

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
                node.dispose_fold()
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
                self.add(chatparts.LoneSummary(content))
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
