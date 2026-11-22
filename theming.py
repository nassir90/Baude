"""theming — how the apps look: their CSS, the shaders warmed before they're needed, the theme (load_css,
theme_css), the window's corners and title bar, popovers, the accent (apply_accent, AccentFade, AccentDot) and the
logo in it (follow_accent, theme_logo), Settings' shared rows, and shared settings shown again as another app here
changes them (follow_setting, watch_settings).
"""
import json
import math
import os
import re
import weakref
import sys
import threading
import traceback
import warnings

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402,F401

from macos import ns_string, objc_send, set_window_frame  # noqa: E402,F401
import fontsetup  # noqa: E402  (the code font, and its size beside the prose)
from baudelib import tokens  # noqa: E402  (the measures and colours below: schema/tokens, the Android app's too)
from baudelib import wording  # noqa: E402
from widgets import (  # noqa: E402
    ACCENTS, INSIDE_GAP, PR_COLORS, SCALE_MAX, SCALE_MIN, SETTINGS_WAIT_MS, SHARED_SETTINGS, ZOOM_STEP, _accent,
    _settings_path, _watch, accent_rgba, accent_text_rgba, load_settings, notifications_on, save_settings, walk,
)
from refkinds import LinkLabel  # noqa: E402
from pickers import snapshot_faded  # noqa: E402


_following = weakref.WeakSet()  # (Settings rows up that show a shared setting: follow_setting)


def notifications_row():
    """Settings → Notifications (Baude's and Record's, the one setting)."""
    row = Adw.SwitchRow(title=wording.say("shared_settings.notifications"), active=notifications_on(),
                        subtitle=wording.say("shared_settings.notifications_subtitle"))
    row.connect("notify::active", lambda r, _p: r.syncing or save_settings(notify=r.get_active()))
    return follow_setting(row, "notify", lambda v: row.set_active(v is not False))


def follow_setting(row, key, show):
    """row, a Settings row for the shared setting key, shown again (show(value)) as another app here changes it. Its
    own handler isn't run for that (it checks row.syncing): it's no change of this app's."""
    row.setting_key, row.show_setting, row.syncing = key, show, False
    _following.add(row)
    return row


def watch_settings(changed):
    """changed(keys, before) as another process here changes shared settings (SHARED_SETTINGS) in settings.json: the
    keys that changed, and what they were before. The accent's taken in every window here first (apply_accent). A
    listener given again is the one listener."""
    if changed not in _watch["listeners"]:
        _watch["listeners"].append(changed)
    if _watch["monitor"] is not None:
        return
    _watch["seen"] = _shared_now() or {}
    try:
        os.makedirs(os.path.dirname(_settings_path()), exist_ok=True)
        mon = Gio.File.new_for_path(_settings_path()).monitor_file(Gio.FileMonitorFlags.NONE, None)
    except (OSError, GLib.Error):
        return
    mon.connect("changed", _settings_event)
    _watch["monitor"] = mon


def _shared_now():
    """The shared settings as settings.json has them now; None while it can't be read whole (being written)."""
    try:
        with open(_settings_path()) as f:
            settings = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return {k: settings.get(k) for k in SHARED_SETTINGS} if isinstance(settings, dict) else None


def _settings_event(_mon, _file, _other, event):
    if event != Gio.FileMonitorEvent.DELETED:
        if _watch["wait"] is not None:
            GLib.source_remove(_watch["wait"])
        _watch["wait"] = GLib.timeout_add(SETTINGS_WAIT_MS, _settings_read)


def _settings_read():
    _watch["wait"] = None
    now = _shared_now()
    if now is None:
        return False
    before = _watch["seen"]
    keys = {k for k in SHARED_SETTINGS if now.get(k) != before.get(k)}
    _watch["seen"] = now
    if keys:
        if "accent" in keys and (display := Gdk.Display.get_default()) is not None:
            apply_accent(display)
        for row in list(_following):
            if row.setting_key in keys:
                row.syncing = True
                try:
                    row.show_setting(now.get(row.setting_key))
                finally:
                    row.syncing = False
        for changed in list(_watch["listeners"]):
            try:
                changed(keys, before)
            except Exception:  # noqa: BLE001 (the others are still told)
                traceback.print_exc()
    return False


# Baude's stylesheet: the chat's, and the viewers' (kubeview, schemaview, gitview, codeview), so wherever they run
# (Record's viewers.ViewerWindow too) they look as they do in Baude. Its classes are its own: none is Record's. Its
# {tokens} are schema/tokens' (tokens.css).
CSS = tokens.css("""
.update-bar button { font-weight: normal; }  /* (the text's own colour: white on a dark sidebar, as its rows are) */
.cut-note { margin-top: 2px; }
.cut-note button { min-height: 0; padding: 1px 8px; font-size: 0.9em; }
/* One look for Baude and Record: the frame round the work (the bars, the lists on the left, the panels on the
   right: one colour, no lines between them), the inside where it's read and written (a box set into the frame),
   one rule for every edge (the inside's, a card's, a table's), and one fill for what's raised inside it (a code
   block, a table's head, a bubble). libadwaita's own colours, so light and dark and the accent follow. */
@define-color baude_frame {baude.frame};
@define-color baude_inside {baude.inside};
@define-color baude_rule {baude.rule};
@define-color baude_raised {baude.raised};
.baude-frame { background-color: @baude_frame; box-shadow: none; }
/* (square; its edge a border, inside it: a ring outside it was clipped where it meets the bar above) */
.baude-inside { background-color: @baude_inside; border: 1px solid @baude_rule; }
/* no focus ring: not on what's clicked, nor on what the keys move to, nor round a box being typed in. The theme's is on
   what has the focus or, these others, on a part of it. */
:focus:focus-visible, expander-widget:focus:focus-visible > box > title,
notebook:focus:focus-visible > header > tabs > tab, scale:focus:focus-visible > trough > slider,
tabbar tabbox > tabboxchild:focus-visible, tabbar tabbox > tabboxchild:focus-within:focus-visible,
tabgrid > tabgridchild:focus:focus-visible .card, .navigation-sidebar > row:focus-visible:focus-within,
entry:focus-within, spinbutton:focus-within, spinbutton.vertical > text:focus-within,
row.entry.focused, row.spin.focused, editablelabel:focus-within { outline: none; }
.user-bubble { background: alpha(@accent_bg_color, 0.15); border-radius: {radius.bubble}; padding: {gap.bubble-v} {gap.bubble-h}; }
.revert-turn { min-width: 24px; min-height: 24px; padding: 0; color: alpha(@view_fg_color, 0.6); }  /* (beside a
   message of yours, on hover: small, and the grey of the chat's notes, .sysnote's) */
.queue-arrow { font-weight: bold; font-size: 13px; }
.user-bubble.queued-bubble { background: alpha(@view_fg_color, 0.07); color: alpha(@view_fg_color, 0.55); }  /* (a
   message queued while the agent works: grey, QueuedTurn) */
.user-bubble.sender-bubble { background: alpha(@view_fg_color, 0.07); }  /* (a message from another than you:
   grey, as a queued one is, not your accent: SenderTurn; held, grey words too: HeldTurn) */
.sender-from { font-size: 0.85em; color: alpha(@view_fg_color, 0.6); }
.tool-card { border: 1px solid @baude_rule; border-radius: {radius.card}; padding: {gap.card-v} {gap.card-h}; }
.api-error { margin-left: 11px; margin-right: 11px; }  /* (its text where a .tool-card's is: border and padding in) */
.tool-card.error { border-color: @error_color; }
.tool-card.asking { border-color: @warning_color; }  /* (waiting for you to allow it: the approval's colour) */
/* a subagent started on the mesh, or a chat there messaged (baude's MeshCard): its line a row, as a subagent's in the
   side panel is, lit under the pointer as one is (it opens the chat); what the chat said back under it, a reply of
   its own: in a bubble as yours are, in the raised fill */
.mesh-card > .tool-card { padding: 0; }
.mesh-card.error > .tool-card { border-color: @error_color; }
.mesh-card.asking > .tool-card { border-color: @warning_color; }
.mesh-card.flash > .tool-card { background: alpha(@accent_bg_color, 0.12); transition: background 400ms; }
.mesh-card .mesh-head { padding: 7px {gap.card-h}; border-radius: 7px; }
.mesh-card .mesh-head.openable:hover { background: alpha(currentColor, 0.05); }
.mesh-card .mesh-answer { background: @baude_raised; border-radius: {radius.bubble}; padding: {gap.bubble-v} {gap.bubble-h}; }
/* every box's border is 1px, a card's that waits on you too (its colour says so); a quote's rule at the left 3px */
.perm-card { border: 1px solid @warning_color; border-radius: {radius.card}; padding: 8px 12px; }
.thinking { opacity: 0.62; }  /* Claude's narration between steps: part of the conversation, quieter */
.mono { font-family: monospace; font-size: {font.mono-scale}em; }
.statusbar { padding: 4px 10px; font-size: 0.85em; opacity: 0.75; }
.statusbar .chat-host { padding: 0 5px; border-radius: 4px; background: alpha(currentColor, 0.1); }
.sysnote { font-size: {font.small}em; opacity: 0.6; }
.compact-mark label.error { opacity: 1; }
.composer-actions button { min-width: {composer.button}; min-height: {composer.button}; padding: 0; }
.feedback-actions button { min-width: 34px; min-height: 34px; padding: 0; }
.attach-chip { background: @baude_raised; border-radius: {radius.card}; padding: 2px 2px 2px 4px; }
.attach-thumb { border-radius: 6px; }
.file-thumb { border-radius: 3px; }
.attach-chip.image { background: none; padding: 0; }
.codeblock { background: @baude_raised; border-radius: {radius.code}; }
.kube-error-fix { background: @baude_raised; border-radius: {radius.code}; padding: 2px 2px 2px 10px; }
.codeblock .code-scroll > viewport > label, .codeblock .code-scroll > label { padding: 6px 8px; }
.codeblock .code-scroll box { padding: 6px 0; }
/* the tag and copy button sit over the code's first line: give them the block's own colour (opaque, feathered in
   from the left) so a long line runs under them instead of through them */
.codeblock .code-corner { padding: 3px 4px 3px 18px; border-radius: 0 {radius.code} 0 0;
  background-image: linear-gradient(to right, alpha(mix(@baude_inside, @view_fg_color, 0.06), 0),
                                    mix(@baude_inside, @view_fg_color, 0.06) 14px); }
/* a block on the side panel is on the frame (a side question's); in an open row of its list (Processes), on the
   row's fill (libadwaita's: half its card shade) on the card on the frame: the corner's the block's colour over
   that, layer by layer */
.side-panel .codeblock .code-corner {
  background-image: linear-gradient(to right, alpha(mix(@baude_frame, @view_fg_color, 0.06), 0),
                                    mix(@baude_frame, @view_fg_color, 0.06) 14px); }
.side-panel .baude-inside .codeblock .code-corner {
  background-image: linear-gradient(to right, alpha(mix(@baude_inside, @view_fg_color, 0.06), 0),
                                    mix(@baude_inside, @view_fg_color, 0.06) 14px); }
.side-panel list.nested .codeblock .code-corner, .side-panel .process-row .process-body .codeblock .code-corner {
  background-image: linear-gradient(to right, alpha(@baude_raised, 0), @baude_raised 14px),
    linear-gradient(to right, transparent, color-mix(in srgb, var(--card-shade-color) 50%, transparent) 14px),
    linear-gradient(to right, alpha(@card_bg_color, 0), @card_bg_color 14px),
    linear-gradient(to right, alpha(@baude_frame, 0), @baude_frame 14px); }
/* a wrapped block's lines can't be scrolled out from under them: they show while the pointer's on it */
.codeblock.wrapped .code-corner { opacity: 0; transition: opacity 150ms; }
.codeblock.wrapped:hover .code-corner, .codeblock.wrapped .code-corner:focus-within { opacity: 1; }
/* a list in ExpandStack while its row opens into its page, or closes back into it: nothing lit up by the pointer
   fading out, or in (ExpandStack._still) */
.expand-still, .expand-still * { transition: none; }
/* a one-line block stays one line tall: a vertical scrollbar's minimum would pad it out */
.codeblock .code-scroll > scrollbar.vertical, .codeblock .code-scroll > scrollbar.vertical > range,
.codeblock .code-scroll > scrollbar.vertical > range > trough { min-height: 0; }
.codeblock .code-scroll > scrollbar.vertical slider { min-height: 16px; }
.codeblock .code-lang { font-size: 0.8em; opacity: 0.5; }
.codeblock .code-copy { min-height: 22px; min-width: 22px; padding: 0; }
/* secondary icons share one fade: a fainter stroke reads as a thinner one */
.soft-icon, .codeblock .code-copy, .sidebar-plus { opacity: 0.75; }
.diff-del { background: {diff.removed-line}; padding: 0 8px; }
.diff-add { background: {diff.added-line}; padding: 0 8px; }
.diff-ctx { padding: 0 8px; }
.md-marker { opacity: 0.7; font-weight: bold; }
.md-h1 { font-size: 1.44em; }
.md-h2 { font-size: 1.2em; }
.md-quote { border-left: 3px solid alpha(@view_fg_color, 0.25); padding-left: 10px; opacity: 0.85; }
.md-image { border-radius: 6px; }
.tool-path { opacity: 0.85; }
levelbar block.filled { background-color: @accent_bg_color; }
levelbar block.ctx-mid { background-color: @warning_color; }
levelbar block.ctx-high { background-color: @error_color; }
levelbar.context-meter trough, levelbar.context-meter block { min-height: 16px; border-radius: 4px; }
.session-row { padding: {gap.sidebar-row-v} {gap.sidebar-row-h}; }
/* a compact list: rows 28px apart instead of libadwaita's 38 */
.session-sidebar list.navigation-sidebar > row { min-height: 26px; padding-top: 1px; padding-bottom: 1px;
  margin-top: 1px; margin-bottom: 0; }
.session-sidebar list.navigation-sidebar > row:first-child > box.sidebar-header { margin-top: 0; }
/* a Bot: twice a chat's height, its picture and its name (baude's SessionSidebar.bot_row) */
.session-sidebar list.navigation-sidebar > row.bot-row { min-height: {gap.bot-row}; }
.session-sidebar .bot-name { font-weight: {font.semibold}; }
.session-sidebar .sidebar-action { min-height: 28px; padding-top: 0; padding-bottom: 0; font-weight: normal; }
dropdown.choice > button, menubutton.choice > button { font-weight: normal; }
/* the top bar's pickers (provider, model, effort, mode): each list's rows the same height, a palette's as a menu's */
dropdown.choice popover listview > row, menubutton.choice popover modelbutton, .model-palette row.model-row {
  min-height: 30px; padding-top: 0; padding-bottom: 0; }
.model-palette row.model-row { padding-left: 8px; padding-right: 8px; border-radius: 6px; }
.model-palette list.model-list { background: none; }
.model-palette .model-family { font-size: 0.8em; font-weight: bold; margin: 8px 8px 2px; }
/* a provider picker with one provider to pick (Baude's beside its model picker, Meta's in Settings): there, a little
   greyed, as there's nothing to choose */
.one-provider { opacity: 0.55; }
banner label.heading { font-weight: normal; }
.session-sidebar .sidebar-folder { font-size: {font.small}em; font-weight: {font.bold}; opacity: 0.6; }
/* the Files page's tree: on the frame, as compact as the sessions list (a list view is a .view, which a GTK theme fills
   with its views' white, edge to edge of the panel and the window) */
listview.file-tree { background: none; }
listview.file-tree > row { min-height: 24px; padding-top: 1px; padding-bottom: 1px; margin-top: 1px; margin-bottom: 0; }
.session-sidebar .sidebar-folder.current { opacity: 1; color: @accent_color; }
.session-sidebar .fork-title { opacity: 0.85; }
.session-sidebar .row-tag { font-size: 0.75em; padding: 0 5px; border-radius: 4px;
  background: @baude_raised; color: alpha(@view_fg_color, 0.7); }
.feedback-thumb { padding: 0; border-radius: 8px; border: 1px solid @baude_rule; }
.feedback-tag { font-size: 0.8em; padding: 1px 6px; border-radius: 6px; }
/* (as .git-ref, Git's bubbles, a shade or two of its colours; its weight, not a head's bold) */
.fix-status { font-size: {fixes.status-size}em; padding: 0 {fixes.status-pad}; border-radius: {radius.status};
              background: alpha(@window_fg_color, 0.08);
              color: alpha(@window_fg_color, 0.7); }
.fix-status.working { background: alpha(@accent_bg_color, 0.14); color: @accent_color; }
.fix-status.ready { background: alpha(#c88800, 0.16); color: #c88800; }
.fix-status.merged, .fix-status.resolved { background: alpha(@success_color, 0.14); color: @success_color; }
.fix-status.rejected { background: alpha(@error_color, 0.14); color: @error_color; }
.fix-round { border: 1px solid @baude_rule; border-radius: 8px; padding: {fixes.round-pad-v} {fixes.round-pad-h}; }
.fix-round.failed { border-color: @error_color; }
.fix-shot { border: 1px solid @baude_rule; }
.capture-bar { padding: 4px 6px; }
.tool-card.flash { background: alpha(@accent_bg_color, 0.12); border-radius: {radius.card}; transition: background 400ms; }
.status-dot.working { color: alpha(@view_fg_color, 0.55); }
.status-dot.waiting { color: #e5a50a; }
.status-dot.done { color: #3584e4; }
.status-dot.inactive { color: alpha(@view_fg_color, 0.45); }
.sidebar-header .folder-arrow { min-width: 20px; min-height: 20px; padding: 0; opacity: 0; transition: opacity 120ms; }
.sidebar-header.hover .folder-arrow, .sidebar-header .folder-arrow.collapsed { opacity: 0.75; }
.session-sidebar .sidebar-plus { min-width: 24px; min-height: 24px; padding: 0; }
.tool-group { padding: 2px 0; }  /* (no indent: the calls' boxes start where the chat's text does) */
.tool-group > box > box > label { opacity: 0.8; }
/* a tool card's Command / Output: small toggle buttons after its name, only a little rounded */
.tool-card button.tool-toggle { min-height: 0; min-width: 0; padding: 1px 7px; font-size: 0.85em; border-radius: 4px; }
/* the side panel sits on the window background, split by a 1px rule; lists are outlined, not shadowed, and so are a
   sidebar's host state (an island as they are: HostState) and the box a side question's asked in (inside its edge, as
   theirs is: as tall as an empty list, it lines up with one) */
.side-panel { background: transparent; }
.side-panel .boxed-list, .boxed-list.host-state, .side-panel .composer-box.side-question-ask {
  box-shadow: none; border: 1px solid @baude_rule; }
.side-panel .side-question { padding: 0; }  /* (its thread's square set INSIDE_GAP into it) */
.side-question-full { min-width: 28px; min-height: 28px; padding: 0; }
/* a process's row: its line where a subagent's row has its own, on the same padding (Adwaita's for a list's row); its
   command and output open under it across the card, on the row's fill (half its card shade, as an open expander
   row's), a rule over them; filling the page, on the panel's own */
.side-panel .process-row { padding: 0; }
.side-panel .process-row > box > row { margin: 2px; }
.side-panel .process-row .process-body { background-color: color-mix(in srgb, var(--card-shade-color) 50%, transparent);
  box-shadow: inset 0 1px var(--border-color); }
/* (the inside's edge is the line. Filled as the frame is: left clear, the pixel between the panes shows the darker window
   behind them, a second line beside it. The paned too, as the rule's moved off that pixel, below) */
paned.main-split { background-color: @baude_frame; }
paned.main-split > separator { min-width: 1px; background: @baude_frame; box-shadow: none; }
/* where it's dragged from, GTK's 6px either side of the rule: on the inside's edge, not that gap short of it */
paned.main-split.inside-after > separator { margin-left: 6px; margin-right: -6px; }
paned.main-split.inside-before > separator { margin-left: -6px; margin-right: 6px; }
.composer-box { border-radius: {radius.composer}; box-shadow: 0 0 0 1px @baude_rule; }
.composer-head { background-color: @view_bg_color; }  /* (as the text under it is: Composer.head) */
.composer-box:disabled { opacity: 0.55; }
.ref-pop contents { padding: 4px; }  /* (a list to pick from as you type: PickList) */
.ref-pop listview { background: none; }
.lightbox { color: white; }  /* (what's behind it dimmed: Lightbox.do_snapshot) */
.lightbox .dim-label { color: alpha(white, 0.7); }
.lightbox-arrow { min-width: 44px; min-height: 44px; }
.lightbox-strip, .lightbox-strip > row { background: none; }
.lightbox-strip > row { padding: 3px; margin: 0 2px; border-radius: 6px; }
.lightbox-strip > row:selected { background: alpha(white, 0.85); }
.lightbox-strip > row:hover:not(:selected) { background: alpha(white, 0.25); }
.lightbox-thumb { border-radius: 4px; }
.gallery { background: none; padding: 6px 8px; }
.gallery > row { padding: 0; background: none; }
.gallery-tile { padding: 0; border-radius: 6px; min-width: 0; min-height: 0; background: alpha(currentColor, 0.06); }
.gallery-tile-pic { border-radius: 6px; }
.gallery-folder { padding: 4px 10px; }
.md-table-scroll { margin: 2px 0; }
.md-table { border: 1px solid @baude_rule; border-radius: 8px; }
.md-table .md-head { background: @baude_raised; padding: 5px 12px;
                     border-bottom: 1px solid @baude_rule; }
.md-table .md-cell { padding: 4px 12px; border-bottom: 1px solid alpha(@baude_rule, 0.5); }
.artifact-chip .artifact-icon { color: @accent_color; }
.tasks-indicator { padding: 0 8px; min-height: 0; font-size: 0.95em; }
.ref-kind { font-size: 0.85em; font-weight: bold; opacity: 0.6; }
/* (shaded_picture: someone's picture pressed into the page, its rim in shadow, as evenly a square's as a circle's,
   its middle as it is. The frame clips the picture and its shade to one shape: round, under a square theme square) */
.picture-frame, .picture-frame avatar, .picture-frame picture, .picture-shade { border-radius: 9999px; }
.picture-shade { box-shadow: inset 0 0 {picture-shade.blur} {picture-shade.blur-color},
  inset 0 {picture-shade.top-y} {picture-shade.top-blur} {picture-shade.top-color},
  inset 0 0 0 {picture-shade.ring} {picture-shade.ring-color}; }
.git-log { background: transparent; }
.git-log row { padding: 0 8px 0 2px; }
.git-ref { font-size: 0.78em; padding: 0 5px; border-radius: 4px; background: alpha(@accent_bg_color, 0.14); color: @accent_color; }
.git-ref.remote { background: alpha(@window_fg_color, 0.08); color: alpha(@window_fg_color, 0.7); }
.git-ref.tag { background: alpha(#c88800, 0.16); color: #c88800; }
.git-ref.head { font-weight: bold; }
.git-ref.chat, .git-chat-btn { background: alpha(#7f77dd, 0.18); color: #7f77dd; }
.git-own { font-weight: 500; }
.git-log row.git-found { background: alpha(@accent_bg_color, 0.2); box-shadow: inset 3px 0 @accent_bg_color; }
.git-log row.git-hover { background: alpha(@accent_bg_color, 0.14); }
/* a pull request's pill on the Git page's list of the chat's (where it stands: PR_COLORS, set below), as a chip;
   the list's rows as the log's are, theirs a little apart */
.git-pr { font-size: 0.78em; font-weight: normal; padding: 0 5px 0 4px; border-radius: 4px;
          background: alpha(currentColor, 0.16); }
.git-pr.closed { background: alpha(currentColor, 0.09); }
.git-pr.closed label { text-decoration-line: line-through; }
.git-log.git-pulls row { padding-top: 2px; padding-bottom: 2px; }
/* the branch picker: its button's names as a choice's are (not a button's bold); its list's rows 26px apart, as
   ⌘K's are, instead of libadwaita's 34, the rule between this chat's branches and the rest a faint one */
menubutton.git-branches > button { font-weight: normal; }
popover.git-branches list { background: transparent; }
popover.git-branches list > row { min-height: 0; margin: 0; padding: 5px 8px; border-radius: 6px; }
popover.git-branches list > separator { margin: 4px 6px; }
/* the sessions sidebar's ⋮ filters (Hosts, Groups): rows as the menu's own items are, what's listed ticked rather
   than highlighted */
list.filter-list { background: transparent; }
list.filter-list > row { min-height: 0; margin: 0; padding: 6px 10px; border-radius: 6px; }
list.filter-list > row:selected { background: none; color: inherit; }
list.filter-list > row:hover, list.filter-list > row:selected:hover { background: alpha(currentColor, 0.07); }
list.filter-list > row:focus-visible { outline-offset: -2px; }
.ref-loc { padding: 2px 6px; min-height: 24px; }
.ref-on image { color: @accent_color; }
.ref-links image { color: alpha(@accent_color, 0.5); }
.reader-glossary { font-size: 0.85em; margin-left: 10px; padding-left: 8px; border-left: 3px solid @accent_color; }
.reader-part { font-size: 0.8em; opacity: 0.55; margin-top: 4px; }
.reader-refused { font-size: 0.8em; color: @warning_color; margin-left: 10px; }
.ref-option { padding: 2px 12px; min-height: 26px; font-weight: normal; background: none; box-shadow: inset 0 0 0 1px alpha(currentColor, 0.18); }
.ref-option:hover { background: alpha(currentColor, 0.07); }
.ref-option:checked { background: alpha(@accent_bg_color, 0.22); color: @accent_color;
                      box-shadow: inset 0 0 0 1px alpha(@accent_color, 0.45); }
/* swapping what a view shows (the chat, a cluster, a database): it goes lighter until the new one is in (Fade) */
.switchable { transition: opacity 120ms ease-out, filter 120ms ease-out; }
.switchable.switching { opacity: 0.45; filter: grayscale(0.6); }
/* code on the inside's background, as the chat and the notes are, its line numbers' column too; its page's bar on the
   frame's, as the window's bars are */
.baude-inside .code-view, .baude-inside .code-view > border { background: none; }
.code-view { font-family: monospace; font-size: {font.mono-scale}em; }
.code-view text { background: transparent; }
.code-gutter { font-family: monospace; font-size: {font.mono-scale}em; }
.code-bar { background-color: @baude_frame; box-shadow: inset 0 -1px @baude_rule; padding: 3px 8px 3px 10px; }
.code-member { padding: 1px 6px; min-height: 22px; font-family: monospace; font-size: 0.85em; font-weight: normal; }
.code-member.current { background: alpha(@accent_bg_color, 0.18); }
.code-class { border: 1px solid @baude_rule; border-radius: 8px; padding: 6px 8px; }
/* the table on the inside's background, as the chat's tables are: a column view is a .view, whose darker colour
   (#1d1d20 in dark) read as navy beside the rest; its header row shaded; no rules between rows, a little more room
   in each instead (the theme's hover tint and selection stay) */
columnview.kube-table { background: none; }
columnview.kube-table > header { background: @baude_raised;
                                 box-shadow: inset 0 -1px @baude_rule; }
columnview.kube-table > listview > row > cell { padding-top: {kube.row-pad}; padding-bottom: {kube.row-pad}; }
.kube-row-btn, .kube-row-btn > button { padding: 0 4px; min-height: 0; min-width: 0; }
.kube-side-head { font-weight: bold; font-size: 0.85em; opacity: 0.7; }
/* the side panel's page names (Subagents, Processes, Side questions, Artifacts…): libadwaita draws them bold */
.side-panel viewswitcher button label { font-weight: normal; }
/* as compact as the sessions list: rows 28px apart instead of libadwaita's 38 */
list.navigation-sidebar.kube-side > row { min-height: 26px; padding-top: 1px; padding-bottom: 1px;
  margin-top: 1px; margin-bottom: 0; }
/* a category's gap above it is outside its row, so its hover tint sits evenly around its icon and name */
list.navigation-sidebar.kube-side > row.kube-side-head-row { margin-top: 9px; }
.kube-status-line { padding: 4px 12px; font-size: 0.9em; opacity: 0.8; }
.kube-link { padding: 0 4px; min-height: 0; font-weight: normal; }
.kube-ok { color: @success_color; }
.kube-warn { color: @warning_color; }
.kube-bad { color: @error_color; }
.schema-canvas { font-size: 0.92em; }
.schema-rows { background: @baude_inside; }  /* (opaque: nothing of the diagram shows through) */
.schema-status { background: alpha(@baude_inside, 0.85); border-radius: 6px; padding: 3px 8px; font-size: 0.85em; }
.ref-now { font-size: 0.9em; }
""")


def warm_shaders(window):
    """Have the renderer make its shaders for what the windows draw while nothing else is happening, soon after the
    first window's first frame, rather than in the frame that first needs each: GTK's GL renderer makes one the first
    time something needs it, and on macOS (through Metal) a frame that met a few took 175 to 313 ms, the first scroll's
    as a chat came into view. What a chat is made of (_warm_scenes) is drawn by the window's renderer to a texture nobody
    sees, a scene at a time, each when the main loop has nothing else to do: they're the shaders its frames use. Once a
    run: the windows share them."""
    if getattr(warm_shaders, "started", False):
        return
    warm_shaders.started = True

    def painted(clock, handler):
        clock.disconnect(handler[0])
        GLib.idle_add(_warm_step, window, _warm_scenes(window), priority=GLib.PRIORITY_LOW)

    def realized(*_):
        handler = []
        handler.append(window.get_frame_clock().connect("after-paint", painted, handler))
    if window.get_realized():
        realized()
    else:
        window.connect("realize", realized)


def _warm_step(window, scenes):
    """warm_shaders' next scene, drawn; again while there are more and the window's there to draw them."""
    renderer = window.get_renderer() if window.get_realized() else None
    if renderer is None or isinstance(renderer, Gsk.CairoRenderer) or not scenes:  # (cairo has no shaders)
        return False
    snap = Gtk.Snapshot()
    scenes.pop(0)(snap)
    if (node := snap.to_node()) is not None:
        renderer.render_texture(node, Graphene.Rect().init(0, 0, 40, 40))
    return bool(scenes)


def _warm_scenes(widget):
    """What warm_shaders draws, a scene at a time ([fn(snapshot)]): what a chat and the left pane are made of beyond what
    a window's first frame already had the renderer make (colours, rounded ones, text, borders), each scene a shader or
    two. Not every kind of node in every setting: each shader takes 40 to 150 ms to make, and five long chats, opened
    and scrolled through, needed only these (GSK_DEBUG=shaders lists the ones made)."""
    ink, clear = Gdk.RGBA(), Gdk.RGBA()
    ink.parse("rgba(40, 60, 90, 0.8)")
    clear.parse("rgba(0, 0, 0, 0)")
    stops = []
    for offset, color in ((0.0, ink), (1.0, clear)):
        stop = Gsk.ColorStop()
        stop.offset, stop.color = offset, color  # (a copy: set whole)
        stops.append(stop)
    layout = widget.create_pango_layout(None)
    layout.set_markup("Ag <b>fn()</b> <tt>code</tt> ✓", -1)
    pixels = GLib.Bytes.new(bytes([90, 120, 200, 255, 128, 128, 128, 128] * 32))  # (8 x 8, two colours)
    pictures = [Gdk.MemoryTexture.new(8, 8, fmt, pixels, 32)  # (as images come, and as drawings and icons do)
                for fmt in (Gdk.MemoryFormat.R8G8B8A8, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED)]
    shape = Gsk.Path.parse("M 4 4 L 36 10 L 14 34 Z")
    box, corner = Gsk.RoundedRect(), Gsk.RoundedRect()  # (kept: what init_from_rect gives back doesn't keep it)
    box.init_from_rect(Graphene.Rect().init(4, 4, 32, 24), 6)
    corner.init_from_rect(Graphene.Rect().init(6, 6, 28, 28), 10)

    def rect(x, y, w, h):
        return Graphene.Rect().init(x, y, w, h)

    def point(x, y):
        return Graphene.Point().init(x, y)

    def text(s):
        s.save()
        s.translate(point(2, 2))
        s.append_layout(layout, ink)
        s.restore()

    def basics(s):  # (what a first frame has, for a window whose first frame didn't)
        s.append_color(ink, rect(0, 0, 4, 4))
        s.push_rounded_clip(corner)
        s.append_color(ink, rect(2, 2, 36, 36))
        s.pop()
        s.append_border(box, [1, 1, 1, 1], [ink] * 4)
        text(s)

    def textures(s):  # (pictures, and drawings and icons, as they are and scaled)
        for i, picture in enumerate(pictures):
            s.append_texture(picture, rect(2 + 10 * i, 2, 8, 8))
            s.append_scaled_texture(picture, Gsk.ScalingFilter.LINEAR, rect(2 + 14 * i, 14, 13, 13))

    def rounded_textures(s):  # (an image in a card, cut at its rounded corners)
        s.push_rounded_clip(corner)
        textures(s)
        s.pop()

    def logo(s):  # (draw_ref_icon: a logo's shapes, scaled, with a cut-out)
        s.scale(0.83, 0.83)
        s.push_mask(Gsk.MaskMode.LUMINANCE)
        s.append_fill(shape, Gsk.FillRule.WINDING, ink)
        s.pop()
        s.append_fill(shape, Gsk.FillRule.WINDING, ink)
        s.append_stroke(shape, Gsk.Stroke.new(1.5), ink)
        s.pop()
    return [basics,
            lambda s: s.append_outset_shadow(box, ink, 0, 2, 0, 6),  # (a popover's, a card's)
            lambda s: s.append_linear_gradient(rect(0, 0, 40, 40), point(0, 0), point(40, 0), stops),
            textures, rounded_textures,
            lambda s: snapshot_faded(s, text, 30, 20, at_start=False),  # (a name cut short: FadeLabel)
            logo]


CSS += "".join(f".git-pr.{state} {{ color: {color}; }}\n" for state, color in PR_COLORS.items())


def load_css():
    """Baude's stylesheet (CSS), once per display: what's drawn with its classes elsewhere (Record's lists, the
    viewers) looks as it does in Baude."""
    display = Gdk.Display.get_default()
    if display is not None and not getattr(display, "baude_css", None):
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        display.baude_css = css


# ---- GTK themes. Baude's look is drawn over libadwaita's stylesheet. Started with GTK_THEME set, libadwaita doesn't
# load it and the theme draws everything, below the app's CSS: there the parts of Baude's look that only make sense over
# libadwaita stand aside (the header bar the frame's colour, the title in the text's own weight), so a theme's own title
# bar and title show, and a GTK 3-era theme's outline stays the focus ring it was there (GTK 3 drew `outline` only round
# what had the keyboard's focus; GTK 4 draws it round every widget that has one, so `* { outline-style: dotted }` boxed
# everything). A theme put in ~/.config/gtk-4.0/gtk.css instead is above the app's CSS: these don't reach it.
OVER_ADWAITA_CSS = """
.baude-frame headerbar { background-color: @baude_frame; box-shadow: none; }
headerbar .title.baude-title { padding-left: 0; padding-right: 0; font-weight: inherit; }
/* (the box a side question's asked in, over their list: an entry's fill, as Search sessions' is: a theme's entries
   have its own, and the box keeps a view's) */
.composer-box.side-question-ask { background-color: alpha(currentColor, 0.1); }
.side-question-ask textview, .side-question-ask textview > text, .side-question-ask .composer-head {
  background-color: transparent; }
"""
UNDER_GTK_THEME_CSS = """
*:not(:focus-visible) { outline-style: none; }
.baude-theme-probe { color: @view_bg_color; }  /* (theme_dark: never shown) */
.baude-frame-probe { color: @baude_frame; }  /* (bar_on_frame: never shown) */
"""
# Baude's stylesheets name libadwaita's colours (@view_bg_color, @sidebar_bg_color, @accent_bg_color...). A GTK 3-era
# theme names its own (@theme_base_color, @theme_bg_color, @theme_selected_bg_color...), and what's drawn in a colour
# no stylesheet names isn't drawn. Below everything, each of libadwaita's names as the theme's colour it stands for: a
# theme that names it itself is above, and its own.
BELOW_GTK_THEME_CSS = """
@define-color window_bg_color @theme_bg_color;
@define-color window_fg_color @theme_fg_color;
@define-color view_bg_color @theme_base_color;
@define-color view_fg_color @theme_text_color;
@define-color sidebar_bg_color @window_bg_color;
@define-color sidebar_fg_color @window_fg_color;
@define-color card_bg_color @view_bg_color;
@define-color card_fg_color @view_fg_color;
@define-color popover_bg_color @window_bg_color;
@define-color popover_fg_color @window_fg_color;
@define-color borders alpha(@window_fg_color, 0.15);
:root { --border-color: color-mix(in srgb, currentColor 15%, transparent); --card-shade-color: rgb(0 0 6 / 7%); }
/* (a dialog's sheet over the window: its fill, its edge, its shadow and the dimming under it are libadwaita's, and
   most themes don't draw them (some fill only a window); without them the sheet runs into the window, or the window
   shows through it. The fill the window's, the edge in the theme's border colour; a theme that draws them, its own) */
floating-sheet > sheet, bottom-sheet > sheet { background-color: @window_bg_color;
  box-shadow: 0 0 0 1px @borders, 0 0 14px 2px rgb(0 0 6 / 3%), 0 0 5px 2px rgb(0 0 6 / 10%); }
/* (a toast: libadwaita's, which a theme drawing no toasts leaves see-through, its text on whatever's under it) */
toast { background-color: #505053; color: white; margin: 12px 12px 24px; border-radius: 150px; border-spacing: 6px;
  padding: 6px 6px 6px 12px; box-shadow: 0 1px 3px 1px rgb(0 0 6 / 7%), 0 2px 6px 2px rgb(0 0 6 / 3%); }
toast > widget { margin: 0 6px; }
"""
# What below the theme depends on whether it's light or dark (theme_dark): the dimming under a dialog, the status
# colours (a theme without its own), as libadwaita has them light and dark.
BELOW_GTK_THEME_SHADE = {False: ("rgb(0 0 6 / 14%)", "#1b8553", "#9c6e03", "#c01c28"),
                         True: ("rgb(0 0 6 / 50%)", "#78e9ab", "#ffc252", "#ff938c")}


def gtk_themed():
    """Whether a GTK theme draws the apps: GTK_THEME set (even empty, as libadwaita has it: it loads none of its
    stylesheet then)."""
    return "GTK_THEME" in os.environ


def below_gtk_theme_css(dark):
    """BELOW_GTK_THEME_CSS, light or dark, and Baude's accent: what accent_rgba and accent_text_rgba have, so what the
    CSS draws in it and what's drawn here are the one colour (a theme's own accent, if it names one, above)."""
    dim, success, warning, error = BELOW_GTK_THEME_SHADE[dark]
    return (BELOW_GTK_THEME_CSS + f"floating-sheet > dimming, bottom-sheet > dimming {{ background-color: {dim}; }}\n"
            f"@define-color success_color {success};\n@define-color warning_color {warning};\n"
            f"@define-color error_color {error};\n@define-color accent_bg_color {accent_rgba().to_string()};\n"
            f"@define-color accent_color {accent_text_rgba().to_string()};\n")


def theme_dark():
    """Whether the GTK theme is dark: its views' colour (as it's resolved, the theme's or below it) dark."""
    probe = Gtk.Label(css_classes=["baude-theme-probe"])
    c = probe.get_color()
    return c.alpha > 0 and 0.2126 * c.red + 0.7152 * c.green + 0.0722 * c.blue < 0.5


def theme_css(*app_css):
    """As an app starts, once: the parts of Baude's stylesheet that depend on whose stylesheet it's drawn over
    (libadwaita's, or a GTK_THEME's). app_css: the app's own stylesheets besides CSS (Record's), whose corners go
    square with Baude's under a square theme (fit_corners; one installed later: rounded_css). Under a theme, light or dark is the theme's: what
    Baude draws in its own colours (code's, the accent's shade, a diagram's) is then drawn for the theme's background,
    not for the system's light or dark."""
    display = Gdk.Display.get_default()
    if display is None or getattr(display, "baude_theme_css", None):
        return
    css = Gtk.CssProvider()
    themed = gtk_themed()
    css.load_from_string(UNDER_GTK_THEME_CSS if themed else OVER_ADWAITA_CSS)
    Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    display.baude_theme_css = css
    if themed:
        below = Gtk.CssProvider()
        below.load_from_string(BELOW_GTK_THEME_CSS)
        Gtk.StyleContext.add_provider_for_display(display, below, Gtk.STYLE_PROVIDER_PRIORITY_FALLBACK)
        dark = theme_dark()
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
        below.load_from_string(below_gtk_theme_css(dark))
        display.baude_below_theme = below
        # (the system's accent changed: what's drawn here follows it, and so does the CSS)
        Adw.StyleManager.get_default().connect("notify::accent-color",
                                               lambda *_: below.load_from_string(below_gtk_theme_css(dark)))
        _corners["sheets"].extend(app_css)
        _corners["hook"] = GObject.add_emission_hook(Gtk.Window, "map", _first_window_mapped)


# ---- a GTK theme's corners. Baude's own (a bubble's, the input box's, a table's, a code block's: its stylesheets'
# border-radius) are libadwaita's roundness. Under a GTK theme they follow the theme's: the first window shown, one of
# its buttons is drawn as the theme draws it, its corner's radius read off what's drawn (how far along the diagonal from
# the corner it's clear), and every border-radius in Baude's stylesheets is held to it: 0 under Windows 95, Redmond,
# CDE, NeXT; a few pixels under XP or Clearlooks; as it was under a theme as round as libadwaita. No theme is named:
# it's what the theme draws. So are the theme's own text fields' (an entry's, a search entry's, a spin button's: many
# themes keep Adwaita's pill-shaped search entry when their buttons are square), Baude's being in them.
_corners = {"hook": None, "tries": 0, "done": False, "sheets": [CSS]}


def _first_window_mapped(window):
    if _corners["done"]:
        return False  # (the hook comes off)
    if not isinstance(window, Gtk.Window):  # (the hook is on every widget's map)
        return True
    _corners["done"] = True
    GLib.timeout_add(50, _probe_corners, window)
    GLib.timeout_add(50, _probe_title_bar, window)
    return False


def _probe_corners(window):
    """Once the window's laid out: its first solid button's corner radius, Baude's held to it (retried a while: none
    laid out yet, or all flat). False: done."""
    _corners["tries"] += 1
    for w in walk(window):
        if (w.get_css_name() == "button" and not w.has_css_class("flat") and w.get_mapped()
                and w.get_width() >= 16 and w.get_height() >= 12):
            radius = corner_radius(w)
            if radius is not None:
                fit_corners(window.get_display(), radius)
                return False
    return _corners["tries"] < 40


# ---- a GTK theme's title bar, flush with the window's top. Two ways a theme leaves a sliver of another colour above
# it, both seen in what's drawn as the first window shows (as the corners are), no theme named:
# - it pads the window to show a frame (Windows 95's bevel): the window's top padding goes, and as much transparent
#   margin above it keeps the edge the window's resized by (GTK takes a window's margin as resize area). The frame
#   stays at the sides and bottom. Only for a window drawn with transparency (.csd): one without (.solid-csd, X11
#   with no compositor) would show the margin black, and keeps its frame.
# - its title bar is an image a pixel or two wide and as tall as the bar, tiled across it (XP's): GTK clips that to the
#   bar less its top padding, and the bar's plain colour shows above it. The image is stretched across the bar instead,
#   the same picture with nothing cut off.
# And where the inside meets the bar: the frame's round it at the sides and bottom; at the top, the bar is the frame
# when it's drawn in the frame's colour (libadwaita's, a theme's like it), and the inside's edge is enough there. Under
# a bar of another colour (XP's blue, Windows 95's navy) the frame shows above the inside too (.baude-under-bar), so
# the inside's set into it on all four sides.
_title_bar = {"tries": 0}


def _probe_title_bar(window):
    """Once the window's laid out and drawn: the window's top padding, and its first header bar's image if GTK cuts
    its top off (retried a while: none drawn yet). False: done."""
    _title_bar["tries"] += 1
    bar = next((w for w in walk(window) if w.get_css_name() == "headerbar" and w.get_mapped() and w.get_height()),
               None)
    cut = title_bar_cut(bar) if bar is not None else None
    if cut is None:
        return _title_bar["tries"] < 40
    ok, bounds = window.compute_bounds(window)
    pad = round(-bounds.get_y()) if ok else 0  # (the window's border box above its content: its top border and padding)
    css = ""
    if pad > 0 and window.has_css_class("csd"):
        css += f"window.csd {{ padding-top: 0; margin-top: {pad}px; }}\n"
    if cut:
        css += "headerbar { background-size: 100% 100%; }\n"
    if not bar_on_frame(bar):
        css += f".baude-inside.baude-under-bar {{ margin-top: {INSIDE_GAP}px; }}\n"
    if css:
        provider = Gtk.CssProvider()
        provider.load_from_string(css)
        Gtk.StyleContext.add_provider_for_display(window.get_display(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        window.get_display().baude_title_bar_css = provider
    return False


def bar_on_frame(bar):
    """Whether bar, as it's drawn, is the frame's colour where the inside meets it (a few pixels up from its foot, clear
    of its buttons and its bottom edge's line): the colour most of the way across it, or see-through there."""
    drawn = drawn_pixels(bar)
    if drawn is None or drawn[2] < 8 or drawn[3] < 8:
        return True
    data, stride, w, h = drawn
    y = h - 1 - max(2, h // 10)
    row = [data[y * stride + x * 4:y * stride + x * 4 + 4] for x in range(0, w, 3)]
    r, g, b, a = (sorted(p[i] for p in row)[len(row) // 2] for i in range(4))
    if a < 128:
        return True
    frame = Gtk.Label(css_classes=["baude-frame-probe"]).get_color()
    return max(abs(r / 255 - frame.red), abs(g / 255 - frame.green), abs(b / 255 - frame.blue)) < 0.12


def title_bar_cut(bar):
    """Whether bar's own background is a strip at most 2px wide and as tall as the bar, tiled, whose top is clipped
    off (a clip starting below where the strip does). None: it isn't drawn yet."""
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(bar).snapshot(snap, bar.get_width(), bar.get_height())
    node = snap.to_node()
    while isinstance(node, Gsk.TransformNode):  # (the bar's own drawing; what's in it, its children, is further in)
        node = node.get_child()
    if node is None:
        return None
    own = [node.get_child(i) for i in range(node.get_n_children())] if isinstance(node, Gsk.ContainerNode) else [node]
    for clip in own:
        if isinstance(clip, (Gsk.ClipNode, Gsk.RoundedClipNode)) and isinstance(clip.get_child(), Gsk.RepeatNode):
            tile = clip.get_child().get_child().get_bounds()
            if (tile.size.width <= 2 and tile.size.height >= bar.get_height() - 1
                    and tile.origin.y < clip.get_bounds().origin.y - 0.5):
                return True
    return False


def drawn_pixels(widget):
    """widget as it's drawn, rendered by its window's renderer: (RGBA bytes, stride, width, height) in the texture's
    pixels, or None (not drawn yet, or no renderer)."""
    w, h = widget.get_width(), widget.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(widget).snapshot(snap, w, h)
    node = snap.to_node()
    native = widget.get_native()
    if node is None or native is None or native.get_renderer() is None:
        return None
    texture = native.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
    down = Gdk.TextureDownloader.new(texture)
    down.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = down.download_bytes()
    return data.get_data(), stride, texture.get_width(), texture.get_height()


def corner_radius(widget):
    """The radius, in pixels, of widget's top-left corner as the theme draws it: from the pixels along the diagonal
    from the corner that are clear of the top edge's solidity (a circle of radius r leaves about r * (1 - 1/sqrt 2) of
    them). None: no solid top edge to tell by (a flat button)."""
    drawn = drawn_pixels(widget)
    if drawn is None:
        return None
    data, stride, w, h = drawn
    alpha = lambda x, y: data[y * stride + x * 4 + 3]  # noqa: E731
    edge = alpha(w // 2, 0)
    if edge < 128:
        return None
    clear = 0
    while clear < min(w, h) // 2 and alpha(clear, clear) * 10 < edge * 9:
        clear += 1
    return round(clear / (1 - 0.5 ** 0.5))


def rounded_css(display, sheet):
    """sheet, a stylesheet installed after theme_css (Meta's), held to the theme's corners as Baude's are (fit_corners):
    at once if they're known, else when they are."""
    if sheet in _corners["sheets"]:
        return
    _corners["sheets"].append(sheet)
    if display is not None and getattr(display, "baude_corner_radius", None) is not None:
        fit_corners(display, display.baude_corner_radius)


def selector_parts(sel):
    """A selector list's selectors: split at its own commas, not those in a :not(…) or :is(…) in it."""
    parts, depth, start = [], 0, 0
    for i, c in enumerate(sel):
        depth += (c == "(") - (c == ")")
        if c == "," and depth == 0:
            parts.append(sel[start:i].strip())
            start = i + 1
    return [p for p in (*parts, sel[start:].strip()) if p]


def theme_text_fields_css():
    """The GTK theme's own rules for its text fields' corners (an entry's, a search entry's, a spin button's), as GTK
    has them: a selector list's selectors that end in one, or that could reach one in a linked group (.linked >
    :first-child, which squares where it joins its neighbour), with their border-radius. Read once (kept: a theme's
    errors, if it has any, aren't said again each time). "": no theme, or none."""
    if (kept := _corners.get("fields")) is not None:
        return kept
    name, _, variant = os.environ.get("GTK_THEME", "").partition(":")
    rules = []
    if name:
        theme = Gtk.CssProvider()
        theme.connect("parsing-error", lambda *_: None)  # (GTK said them as it loaded the theme itself)
        with warnings.catch_warnings():  # (load_named is deprecated, with nothing in its place: it's how GTK loads it)
            warnings.simplefilter("ignore", DeprecationWarning)
            theme.load_named(name, variant or None)
        sheet = re.sub(r"@[\w-]+[^;{}]*;", "", re.sub(r"/\*.*?\*/", "", theme.to_string(), flags=re.S))
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", sheet):
            fields = []
            for part in selector_parts(sel):
                bare = part
                while bare != (bare := re.sub(r"\([^()]*\)", "", bare)):  # (what's in a :not(…), out of the way)
                    pass
                last = re.split(r"[\s>+~]+", bare)[-1]
                if re.match(r"(entry|spinbutton)\b", last) or (".linked" in part and re.match(r"[*.:]", last)):
                    fields.append(part)
            radii = re.findall(r"(border(?:-[a-z]+)*-radius:\s*[^;}]+)", body)
            if fields and radii:
                rules.append(f"{', '.join(fields)} {{ {'; '.join(radii)}; }}")
    _corners["fields"] = "\n".join(rules)
    return _corners["fields"]


def fit_corners(display, radius):
    """Every border-radius in Baude's stylesheets (the app's: theme_css; one installed later: rounded_css) and in the
    theme's text fields (theme_text_fields_css) held to radius, above them. Again: what was held before, replaced."""
    def held(value):
        return re.sub(r"(\d+(?:\.\d+)?)px", lambda m: f"{min(float(m.group(1)), radius):g}px", value)
    fields = theme_text_fields_css()
    theirs, ours = [], []
    for sheet, into in ((fields, theirs), *((sheet, ours) for sheet in _corners["sheets"])):
        sheet = re.sub(r"@[\w-]+[^;{}]*;", "", re.sub(r"/\*.*?\*/", "", sheet, flags=re.S))  # (comments, @define-colors)
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", sheet):
            for prop, value in re.findall(r"(border(?:-[a-z]+)*-radius):\s*([^;}]+)", body):
                into.append((f"{' '.join(sel.split())} {{ {prop}: {held(value).strip()}; }}", held(value) != value))
    # (the theme's text fields', every one held or not, so they're as specific over each other as the theme has
    # them; Baude's own above them, every one then too, as they were above the theme's)
    theirs = [rule for rule, _ in theirs] if any(changed for _, changed in theirs) else []
    ours = [rule for rule, changed in ours if changed or theirs]
    display.baude_corner_radius = radius
    for name, rules, above in (("baude_fields_css", theirs, 1), ("baude_corners_css", ours, 2)):
        if (old := getattr(display, name, None)) is not None:
            Gtk.StyleContext.remove_provider_for_display(display, old)
        setattr(display, name, None)
        if rules:
            css = Gtk.CssProvider()
            css.load_from_string("\n".join(rules))
            Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + above)
            setattr(display, name, css)


# ---- popovers on X11 under picom. A popover's window is its box with GTK's own shadow round it (transparent, ~20px a
# side) and its arrow's room; picom (or compton, or xcompmgr -c) puts a shadow of its own round the whole window, a
# larger rectangle round the box. There they're drawn as libadwaita draws them with no compositor (its .solid-csd look): no
# shadow, square corners, an edge, no arrow, so the window is the box and picom's shadow is the popover's.
SHADOWING_COMPOSITORS = ("picom", "compton", "xcompmgr")
FLAT_POPOVER_CSS = """
popover.background:not(.touch-selection):not(.magnifier) { background: @popover_bg_color; color: @popover_fg_color; border: 1px solid alpha(currentColor, 0.15); }
popover > contents, popover > arrow { box-shadow: none; border-radius: 0; background-clip: border-box; border: none; }
"""
_flat = {"display": None, "provider": None, "on": False, "hook": None, "arrowless": weakref.WeakSet()}


def shadowing_compositor():
    """Whether picom (or another of SHADOWING_COMPOSITORS) is running on this machine."""
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return False
    for pid in pids:
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() in SHADOWING_COMPOSITORS:
                    return True
        except OSError:
            pass
    return False


def flat_popovers(display):
    """Popovers drawn flat (FLAT_POPOVER_CSS, no arrow) while display is X11 composited by picom: looked at again as
    the compositor comes and goes. Once a widget exists (the hook is on GtkWidget's realize)."""
    if display is None or display.__gtype__.name != "GdkX11Display" or _flat["display"] is display:
        return
    _flat["display"], _flat["provider"] = display, Gtk.CssProvider()
    _flat["provider"].load_from_string(FLAT_POPOVER_CSS)

    def realized(widget, *_):
        if _flat["on"] and isinstance(widget, Gtk.Popover) and widget.get_has_arrow():
            widget.set_has_arrow(False)
            _flat["arrowless"].add(widget)  # (its arrow back if picom goes)
        return True

    def update(*_):
        on = display.is_composited() and shadowing_compositor()
        if on == _flat["on"]:
            return
        _flat["on"] = on
        if on:
            Gtk.StyleContext.add_provider_for_display(display, _flat["provider"],
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            if _flat["hook"] is None:
                _flat["hook"] = GObject.add_emission_hook(Gtk.Widget, "realize", realized)
        else:
            Gtk.StyleContext.remove_provider_for_display(display, _flat["provider"])
            for pop in list(_flat["arrowless"]):
                pop.set_has_arrow(True)
            _flat["arrowless"].clear()

    display.connect("notify::composited", update)
    update()


def apply_accent(display):
    """The accent settings.json has, in every window. Changed, what's drawn here is drawn again, a chip's name
    recoloured, what keeps its own colours told (accent_changed), as the CSS is restyled."""
    name = load_settings().get("accent")
    name = name if name in ACCENTS else None
    rgba = Adw.AccentColor.to_rgba(ACCENTS[name]).to_string() if name else None
    css = f"@define-color accent_bg_color {rgba};\n:root {{ --accent-bg-color: {rgba}; }}" if name else ""
    if name and gtk_themed():  # (no libadwaita to make @accent_color of it: what accent_text_rgba has)
        text = Adw.AccentColor.to_standalone_rgba(ACCENTS[name], Adw.StyleManager.get_default().get_dark())
        css += f"\n@define-color accent_color {text.to_string()};"
    if _accent["provider"] is None:
        _accent["provider"] = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, _accent["provider"], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    if css == _accent["css"]:
        return
    again = _accent["css"] is not None
    for win in Gtk.Window.list_toplevels() if again else ():
        if isinstance(win, AccentFade):
            win.fade_accent()
    picked = again and name != _accent["name"]
    _accent["name"], _accent["css"] = name, css
    _accent["provider"].load_from_string(css)
    for win in Gtk.Window.list_toplevels() if again else ():
        for widget in walk(win):
            if isinstance(widget, LinkLabel) and widget.chips:
                widget.place_icons()
            if (changed := getattr(widget, "accent_changed", None)) is not None:  # (its own colours: Record's links)
                changed()
            widget.queue_draw()
    if picked and _logos:  # (after the windows: a logo's drawn at 1024 px, not in the fade's first frames)
        GLib.idle_add(lambda: [redraw() for redraw in list(_logos)] and False, priority=GLib.PRIORITY_LOW)


_logos = []  # (follow_accent's: each app's logo, drawn again in the accent)


_jobs_lock = threading.Lock()
_jobs = {}  # (off_main's: per job, whether one runs, and the last asked while it did)


def off_main(job, work, done=None):
    """work() on a worker thread (a logo's PNGs and .icns are drawn without GTK, and not in the accent's fade), its
    result to done(result) on the main loop. Asked again while it runs, only the last ask runs after it: arrowing
    through the accents draws the one they stop on."""
    state = _jobs.setdefault(job, {"running": False, "next": None})
    with _jobs_lock:
        if state["running"]:
            state["next"] = (work, done)
            return
        state["running"] = True

    def run(work, done):
        while True:
            try:
                result = work()
            except Exception as e:  # (an icon isn't worth the accent's change)
                print(f"baude: {job}:", e, file=sys.stderr)
            else:
                if done is not None:
                    GLib.idle_add(lambda done=done, result=result: done(result) and False)
            with _jobs_lock:
                nxt, state["next"] = state["next"], None
                if nxt is None:
                    state["running"] = False
                    return
            work, done = nxt
    threading.Thread(target=run, args=(work, done), daemon=True).start()


def follow_accent(redraw):
    """redraw() as the accent changes here (apply_accent: picked in Settings, set by Meta or in another Baude or
    Record): the app's logo drawn again in it (widgets.logo), its Dock tile's or its windows' and its launcher's."""
    if redraw not in _logos:
        _logos.append(redraw)


def theme_logo(root, name, svg):
    """Linux: an app's logo, svg, as icon name in an icon folder of its own (root), which the display's icon theme
    looks in: windows take it from there by name. Written again with another svg (the accent changed), the theme looks
    again, and windows showing it take it anew. Its path (None: not written)."""
    path = os.path.join(root, "hicolor", "scalable", "apps", f"{name}.svg")
    try:
        with open(path) as f:
            same = f.read() == svg
    except OSError:
        same = False
    if not same:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(svg)
        except OSError:
            return None
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    if root not in theme.get_search_path():
        theme.add_search_path(root)
    elif not same:
        theme.set_search_path(theme.get_search_path())
    return path


class AccentFade:
    """Mixed into a window (Baude's, Record's) whose do_snapshot is snapshot_fading's: as the accent changes
    (apply_accent, picked in Settings or set by Meta), the window as it was last drawn cross-fades into it as it is
    now, in FADE_MS, so what Baude and Record colour themselves (Record's links, a mark's highlight, a chip's name)
    changes as libadwaita's own buttons and switches do, in their time and ease. The fade's time starts once the
    window's drawn in the new colours (restyling all of it can take a frame or more). Not shown, or with animations
    off: at once; minimized, or hidden before it's drawn, the new colours as it's next seen. GTK's first frame of a
    fade is slow (80 ms and more, readying how it draws one), so each window has one READY_MS after it's first drawn,
    unseen (ready_fade), and its first fade runs smoothly."""
    FADE_MS = 200
    READY_MS = 1000
    drawn = None  # (the window as it was last drawn, a fade in it: what a fade starts from)
    accent_fade = None  # (that picture, and how much of it's still seen)
    readying = None  # (ready_fade, to come)

    def snapshot_fading(self, snapshot, base):
        """do_snapshot's: the window as base (its class) draws it, a fade's picture cross-fading into it; kept as
        it's drawn."""
        if self.readying is None:
            self.readying = GLib.timeout_add(self.READY_MS, self.ready_fade)
            self.connect("unmap", AccentFade.drop_fade)
            self.connect("notify::suspended", AccentFade.drop_fade)  # (minimized: no frames till it's back)
        inner = Gtk.Snapshot()
        if (fade := self.accent_fade) is not None:
            inner.push_cross_fade(1.0 - fade["seen"])  # (what's the same in both stays as it is)
            inner.append_node(fade["node"])
            inner.pop()
            base.do_snapshot(self, inner)
            inner.pop()
        else:
            base.do_snapshot(self, inner)
        if (node := inner.to_node()) is not None:
            self.drawn = node
            snapshot.append_node(node)

    def fade_accent(self):
        if not self.can_fade():
            return
        # (a fade drawn and under way: its picture as it is, drawn into one, so fades started in fades don't pile up)
        under_way = self.accent_fade is not None and "painted" not in self.accent_fade
        fade = {"node": self.flattened(self.drawn) if under_way else self.drawn, "seen": 1.0}

        def step(v):
            if self.accent_fade is fade:
                fade["seen"] = v
                self.queue_draw()

        def done(_a):
            if self.accent_fade is fade:
                self.accent_fade = None
                self.queue_draw()
            fade.clear()  # (its picture and animation let go: they and its callbacks hold each other)

        def painted():  # (the first frame in the new colours, all of it still the old: it fades from here)
            if self.accent_fade is not fade:  # (another change since: that one fades)
                fade.clear()
                return
            anim = fade["anim"] = Adw.TimedAnimation.new(self, 1.0, 0.0, self.FADE_MS,
                                                         Adw.CallbackAnimationTarget.new(step))
            anim.set_easing(Adw.Easing.EASE_OUT_QUAD)  # (libadwaita's transitions' ease)
            anim.connect("done", done)
            anim.play()
        self.begin_fade(fade, painted)

    def can_fade(self):
        return self.get_mapped() and not self.is_suspended() and Adw.get_enable_animations(self) and \
            self.drawn is not None

    def ready_fade(self):
        """One frame with the frame before all but gone, cross-faded into this one: settled, the two are the same,
        so nothing's seen, and GTK's slow first frame of a fade is had now."""
        if self.accent_fade is not None or not self.can_fade():
            return False
        ready = {"node": self.drawn, "seen": 0.001}

        def painted():
            if self.accent_fade is ready:
                self.accent_fade = None
                self.queue_draw()
        self.begin_fade(ready, painted)
        return False

    def begin_fade(self, fade, painted):
        """fade drawn from the next frame (snapshot_fading), in place of one not yet drawn, and painted() once that
        frame's drawn."""
        self.drop_fade()
        clock = self.get_frame_clock()

        def after(_clock):
            clock.disconnect(fade.pop("painted"))
            painted()
        fade["clock"], fade["painted"] = clock, clock.connect("after-paint", after)
        self.accent_fade = fade
        self.queue_draw()

    def drop_fade(self, *_):
        """A fade not yet drawn let go: hidden or minimized, so shown again the window's as it is (libadwaita ends
        one under way: its animation's done); or another in its place."""
        if (fade := self.accent_fade) is not None and "painted" in fade:
            fade["clock"].disconnect(fade.pop("painted"))
            self.accent_fade = None

    def flattened(self, node):
        """node drawn once as a picture of its own: the window's part of it, in whole pixels of its display's (else
        node, if that's nothing)."""
        s, b = self.get_surface().get_scale(), node.get_bounds()
        x0, y0 = math.floor(max(b.get_x(), 0) * s), math.floor(max(b.get_y(), 0) * s)
        x1 = math.ceil(min(b.get_x() + b.get_width(), self.get_width()) * s)
        y1 = math.ceil(min(b.get_y() + b.get_height(), self.get_height()) * s)
        if x1 <= x0 or y1 <= y0:
            return node
        texture = self.get_renderer().render_texture(Gsk.TransformNode.new(node, Gsk.Transform.new().scale(s, s)),
                                                     Graphene.Rect().init(x0, y0, x1 - x0, y1 - y0))
        if texture is None:
            return node
        snap = Gtk.Snapshot()
        snap.append_texture(texture, Graphene.Rect().init(x0 / s, y0 / s, (x1 - x0) / s, (y1 - y0) / s))
        return snap.to_node()


class AccentDot(Gtk.Widget):
    """A round patch of a colour (rgba, a Gdk.RGBA): Settings → Accent Colour's, before each one's name."""

    def __init__(self):
        super().__init__(valign=Gtk.Align.CENTER)
        self.rgba = None

    def do_measure(self, orientation, for_size):
        return 12, 12, -1, -1

    def do_snapshot(self, snapshot):
        if self.rgba is not None:
            rect = Graphene.Rect().init(0, 0, self.get_width(), self.get_height())
            round_rect = Gsk.RoundedRect()
            round_rect.init_from_rect(rect, min(self.get_width(), self.get_height()) / 2)
            snapshot.push_rounded_clip(round_rect)
            snapshot.append_color(self.rgba, rect)
            snapshot.pop()


def settings_accent():
    """Settings → Accent Colour: the system's, or one of libadwaita's (ACCENTS), each with its colour. Taken in every
    window as it's picked (apply_accent) and kept in settings.json."""
    names = ["system", *ACCENTS]
    picked = load_settings().get("accent")
    row = Adw.ComboRow(title=wording.say("shared_settings.accent_colour"), model=Gtk.StringList.new(names),
                       selected=names.index(picked) if picked in ACCENTS else 0)

    def setup(_f, item):
        line = Gtk.Box(spacing=8)
        line.append(AccentDot())
        line.append(Gtk.Label(xalign=0))
        item.set_child(line)

    def bind(_f, item):
        name = item.get_item().get_string()
        dot, label = item.get_child().get_first_child(), item.get_child().get_last_child()
        dot.rgba = Adw.AccentColor.to_rgba(ACCENTS.get(name, Adw.StyleManager.get_default().get_accent_color()))
        dot.queue_draw()
        label.set_text(name.capitalize())

    factory = Gtk.SignalListItemFactory()
    factory.connect("setup", setup)
    factory.connect("bind", bind)
    row.set_factory(factory)

    def changed(r, _p):
        if r.syncing:
            return
        name = names[r.get_selected()]
        save_settings(accent=name if name in ACCENTS else None)
        apply_accent(r.get_display())
    row.connect("notify::selected", changed)
    return follow_setting(row, "accent", lambda v: row.set_selected(names.index(v) if v in ACCENTS else 0))


def put_back(win, frame, maximized):
    """A window made again (a restart) where the one before was: on macOS, at its frame (window_frame: the display
    it was on, where, how big) once it's shown and GTK has placed it, then maximized there if it was. Elsewhere (or
    without a frame), maximized if it was."""
    if not frame or sys.platform != "darwin":
        if maximized:
            win.maximize()
        return

    def place():
        set_window_frame(win, frame)
        if maximized:  # (on the display it's on now)
            GLib.timeout_add(60, lambda: win.maximize() and False)
        return False

    def mapped(*_):
        win.disconnect(handler)
        GLib.timeout_add(60, place)  # (after GTK's own placing of a new window)
    handler = win.connect("map", mapped)


def macos_appearance():
    """On macOS, light and dark follow the system as it changes: libadwaita reads AppleInterfaceStyle from the user
    defaults, which stays unset while macOS is dark, so Baude stayed light. -[NSApplication effectiveAppearance] is
    right, and Apple documents it as observable: an observer (an NSObject subclass made through the runtime, as
    there's no PyObjC) forces libadwaita's scheme to it, at once and on every change."""
    import ctypes
    app = objc_send(b"NSApplication", b"sharedApplication")
    rt = objc_send.rt
    rt.objc_allocateClassPair.restype = ctypes.c_void_p
    rt.objc_allocateClassPair.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    rt.objc_registerClassPair.argtypes = [ctypes.c_void_p]
    rt.class_addMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]

    def changed(_self, _sel, _path, _app, _change, _context):
        if gtk_themed():  # (light or dark is the theme's: theme_css)
            return
        name = objc_send(objc_send(app, b"effectiveAppearance"), b"name")
        dark = b"Dark" in (objc_send(name, b"UTF8String", ret=ctypes.c_char_p) or b"") if name else False
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
    # (kept: AppKit calls it for as long as baude runs)
    macos_appearance.imp = ctypes.CFUNCTYPE(None, *[ctypes.c_void_p] * 6)(changed)
    cls = rt.objc_allocateClassPair(rt.objc_getClass(b"NSObject"), b"BaudeAppearanceObserver", 0)
    rt.class_addMethod(cls, rt.sel_registerName(b"observeValueForKeyPath:ofObject:change:context:"),
                       ctypes.cast(macos_appearance.imp, ctypes.c_void_p), b"v@:@@@^v")
    rt.objc_registerClassPair(cls)
    observer = objc_send(objc_send(cls, b"alloc"), b"init")
    # (NSKeyValueObservingOptionInitial | New: called once now, then on each change)
    objc_send(app, b"addObserver:forKeyPath:options:context:", observer, ns_string("effectiveAppearance"), 4 | 1,
              None, types=(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p))


def settings_switch(title, subtitle, action):
    """A Settings row whose switch is a menu's toggle (a stateful action of the window's: "win.fullwidth"); subtitle
    None: just its name."""
    row = Adw.ActionRow(title=title, subtitle=subtitle or "")
    toggle = Gtk.Switch(action_name=action, valign=Gtk.Align.CENTER)
    row.add_suffix(toggle)
    row.set_activatable_widget(toggle)
    return row


def settings_size(title, subtitle, size, apply, low=SCALE_MIN, high=SCALE_MAX, key=None):
    """A Settings row for a size, 1.0 the usual, in the zoom's steps of 0.1, applied as it changes; key: the shared
    setting it is, followed as another app changes it (follow_setting)."""
    row = Adw.SpinRow.new_with_range(low, high, ZOOM_STEP)
    row.set_digits(1)
    row.set_title(title)
    row.set_subtitle(subtitle or "")
    row.set_value(size)
    row.connect("notify::value", lambda r, _p: getattr(r, "syncing", False) or apply(round(r.get_value(), 2)))
    if key is not None:
        follow_setting(row, key, lambda v: row.set_value(float(v or 1.0)))
    return row


def settings_code_font(win, app):
    """Settings → Code Font: the font code's drawn in (fontsetup.code_fonts), kept in settings.json, from app's next
    start ("Baude", "Record": Pango's fonts are set up before it starts); picked other than the one it's drawing with,
    a Restart button (win's ☰ → Restart). It's drawn at its own size beside the prose (code_scale)."""
    fonts, running = list(fontsetup.code_fonts()), fontsetup.running_font()
    if fontsetup.code_font() not in fonts:  # (one named in settings.json by hand)
        fonts.append(fontsetup.code_font())
    if running not in fonts:
        fonts.append(running)
    row = Adw.ComboRow(title=wording.say("shared_settings.code_font"), model=Gtk.StringList.new(fonts),
                       selected=fonts.index(fontsetup.code_font()))
    restart = Gtk.Button(label=wording.say("shared_settings.restart"), valign=Gtk.Align.CENTER)
    # (in Settings, closed first; under a reply of Meta's, there's no dialog to close)
    restart.connect("clicked", lambda b: ((d := b.get_ancestor(Adw.Dialog)) is not None and d.close(),
                                          win.activate_action("win.restart", None)))
    row.add_suffix(restart)

    def show(font):
        restart.set_visible(font != running)
        row.set_subtitle("" if font == running else
                         wording.say("shared_settings.code_font_next_start", font=font, app=app))

    def changed(r, _p):
        if not r.syncing:
            save_settings(code_font=fonts[r.get_selected()])
        show(fonts[r.get_selected()])
    row.connect("notify::selected", changed)
    show(fontsetup.code_font())
    return follow_setting(row, "code_font", lambda v: v in fonts and row.set_selected(fonts.index(v)))
