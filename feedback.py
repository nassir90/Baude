"""Menu → Send Feedback…, or ⌘. / Ctrl+. over whatever's open (Baude's and Record's): a note, an issue or a suggestion,
and screen captures of the window (the image and the text on screen, with regions you mark), saved under
~/.local/share/baude/feedback/<time>/; with the gh CLI signed in to GitHub, filed as an issue on the repository Baude came
from too, or the private one it has for feedback if gh shows you that (issues). ☰ → Ongoing Feedback lists them with the
issues (fixview.py: feedback_items). A window takes feedback by mixing in FeedbackHost."""
import base64
import html
import json
import os
import re
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402,F401

from fixes import gh, gh_path, code_repo, known_submit_issue_target, submit_issue_target  # noqa: E402,F401
import pointers  # noqa: E402
from widgets import (  # noqa: E402,F401
    FEEDBACK_KEY, ZOOM_MODS, FixedPicture, ShutBar, _data_home, discard, fit_size, no_text_drag, scaled_texture,
    screen_scale, walk,
)


# ----------------------------------------------------------------- window --

# ------------------------------------------------------------------ feedback --
# Menu → Send Feedback…: a note, plus screen captures of Baude (the image and the text on screen,
# with regions you mark), saved under ~/.local/share/baude/feedback/<time>/, and filed as an issue (issues).

def feedback_dir():
    return os.path.join(_data_home(), "baude", "feedback")


def _visible_rect(widget, root):
    """widget's on-screen rectangle in root's coordinates (x, y, w, h), or None: clipped by every
    scrolled view it's inside, so text scrolled out of sight doesn't count."""
    if not widget.get_mapped():
        return None
    ok, b = widget.compute_bounds(root)
    if not ok:
        return None
    x0, y0, x1, y1 = b.get_x(), b.get_y(), b.get_x() + b.get_width(), b.get_y() + b.get_height()
    p = widget.get_parent()
    while p is not None and p is not root:
        if isinstance(p, (Gtk.ScrolledWindow, Gtk.Viewport, Gtk.Revealer)):
            ok, c = p.compute_bounds(root)
            if ok:
                x0, y0 = max(x0, c.get_x()), max(y0, c.get_y())
                x1, y1 = min(x1, c.get_x() + c.get_width()), min(y1, c.get_y() + c.get_height())
        p = p.get_parent()
    ok, r = root.compute_bounds(root)
    x1, y1 = min(x1, r.get_width()), min(y1, r.get_height())
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    return round(x0), round(y0), round(x1 - x0), round(y1 - y0)


def _pango_words(layout, data, start, end, ox):
    """The words of a layout's text (data, its bytes) from start to end: [(word and the spaces after it, x0, x1)], from ox
    in. Where it is from its first and last characters': either way round, as right-to-left text has them."""
    words = []
    for m in re.finditer(rb"(\S+)(\s*)", data[start:end]):
        last = start + m.end(1) - 1
        while data[last] & 0xC0 == 0x80:  # (its last character's start)
            last -= 1
        a, b = layout.index_to_pos(start + m.start(1)), layout.index_to_pos(last)
        xs = (a.x, a.x + a.width, b.x, b.x + b.width)
        words.append((m.group().decode("utf-8", "replace"), ox + min(xs) / Pango.SCALE, ox + max(xs) / Pango.SCALE))
    return words


def _shown_words(layout, data, it, ox):
    """The words of the line an iterator of an ellipsized layout is on: those shown, and "…" where it's cut. The runs Pango
    laid out say what's gone, at the start, in the middle or at the end."""
    runs, it = [], it.copy()
    while (run := it.get_run_readonly()) is not None:
        item = run.item  # (held: run.item.analysis read through one that's gone reads anything)
        analysis = item.analysis
        runs.append((item.offset, item.length, analysis.flags & Pango.ANALYSIS_FLAG_IS_ELLIPSIS, it.get_run_extents()[1]))
        it.next_run()
    words, shown = [], None  # (shown: the bytes since the last cut)
    for offset, length, cut, r in sorted(runs, key=lambda run: run[0]):
        if not cut:
            shown = (shown[0] if shown else offset, offset + length)
            continue
        words += _pango_words(layout, data, *shown, ox) if shown else []
        words.append(("…", ox + r.x / Pango.SCALE, ox + (r.x + r.width) / Pango.SCALE))
        shown = None
    return words + (_pango_words(layout, data, *shown, ox) if shown else [])


def _label_lines(label):
    """A label's lines as they're drawn: [(words, y, h)], in its coordinates, words as _pango_words has them."""
    layout = label.get_layout()
    ox, oy = label.get_layout_offsets()
    data, lines, it = layout.get_text().encode(), [], layout.get_iter()
    while True:
        line, (_ink, r) = it.get_line_readonly(), it.get_line_extents()
        words = _shown_words(layout, data, it, ox) if layout.is_ellipsized() else \
            _pango_words(layout, data, line.start_index, line.start_index + line.length, ox)
        lines.append((words, oy + r.y / Pango.SCALE, r.height / Pango.SCALE))
        if not it.next_line():
            return lines


def _entry_lines(entry):
    """An entry's text as _label_lines has a label's: what its box shows, scrolled as it is. A password's isn't text on
    screen, whatever's typed."""
    if not entry.get_visibility() or not entry.get_text():
        return []
    data, off = entry.get_text().encode(), entry.get_property("scroll-offset")
    layout = entry.create_pango_layout(entry.get_text())
    start, end = sorted(layout.xy_to_index(x * Pango.SCALE, 0)[1] for x in (off, off + entry.get_width()))
    end += 1
    while end < len(data) and data[end] & 0xC0 == 0x80:  # (a character partly in sight is drawn: all of it)
        end += 1
    return [(_pango_words(layout, data, start, end, -off), 0, entry.get_height())]


def _text_view_words(view, first, end):
    """What's between two places in a text view, as _pango_words has it, in buffer coordinates: what's hidden left out.
    (Where text's hidden on the line, a character's place is from the caret's: NotesView.char_rect says why. Elsewhere not:
    beside a run of right-to-left text the caret's at either end of it.)"""
    buf = view.get_buffer()
    hidden = buf.get_text(first, end, False) != buf.get_text(first, end, True)

    def edges(it):
        if not hidden:
            r = view.get_iter_location(it)
            return r.x, r.x + r.width
        here, after = view.get_cursor_locations(it)[0], it.copy()
        after.forward_char()
        there = view.get_cursor_locations(after)[0]
        return here.x, there.x if there.y == here.y else here.x

    spans, it = [], first.copy()  # ([word, the spaces after it, its first character, its last])
    while it.compare(end) < 0:
        after = it.copy()
        after.forward_char()
        ch = buf.get_text(it, after, False)
        if ch.isspace():
            if spans:
                spans[-1][1] += ch
        elif ch:
            if spans and not spans[-1][1]:
                spans[-1][0] += ch
                spans[-1][3] = it
            else:
                spans.append([ch, "", it, it])
        it = after
    words = []
    for word, gap, a, b in spans:
        xs = (*edges(a), *edges(b))
        words.append((word + gap, min(xs), max(xs)))
    return words


def _text_view_lines(view, left, top, right, bottom):
    """A text view's lines as they're wrapped, those from top to bottom of it, as _label_lines has a label's. Not wrapped,
    only what's between left and right: a long line scrolled sideways."""
    buf, lines = view.get_buffer(), []
    wrapped = view.get_wrap_mode() != Gtk.WrapMode.NONE
    bx0, by0 = view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, left, top)
    bx1, by1 = view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, right, bottom)
    ox, oy = view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, 0, 0)
    start = view.get_iter_at_location(bx0, by0)[1]
    view.backward_display_line_start(start)
    while True:
        here = view.get_cursor_locations(start)[0]
        if here.y >= by1:
            return lines
        following = start.copy()
        more = view.forward_display_line(following)
        first, end = start, following if more else buf.get_end_iter()
        if not wrapped:
            first = view.get_iter_at_location(bx0, here.y + here.height // 2)[1]
            end = view.get_iter_at_location(bx1, here.y + here.height // 2)[1]
            if first.compare(end) > 0:  # (right to left)
                first, end = end, first
            if not end.ends_line():
                end.forward_char()
        words = [(word, ox + x0, ox + x1) for word, x0, x1 in _text_view_words(view, first, end)]
        lines.append((words, oy + here.y, here.height))
        if not more:
            return lines
        start = following


def _lines_on_screen(widget, root):
    """The text a label, a text view or an entry's text has on screen, line by line: [(text, x, y, w, h, words)], in root's
    coordinates; words [(word and the spaces after it, x0, x1)], in reading order. A line counts if its middle's in sight,
    and of it the words in sight: not what's scrolled away, nor one all but gone."""
    if (rect := _visible_rect(widget, root)) is None:
        return []
    cx, cy, cw, ch = rect
    ok, at = widget.compute_point(root, Graphene.Point().init(0, 0))
    if not ok:
        return []
    if isinstance(widget, Gtk.Label):
        lines = _label_lines(widget)
    elif isinstance(widget, Gtk.TextView):
        lines = _text_view_lines(widget, cx - at.x, cy - at.y, cx + cw - at.x, cy + ch - at.y)
    else:
        lines = _entry_lines(widget)
    out = []
    for words, y, h in lines:
        y += at.y
        words = [(word.replace("\u2063", ""), x0 + at.x, x1 + at.x) for word, x0, x1 in words]  # (a logo's place: LinkLabel)
        words = [w for w in words if w[0].strip() and w[1] < cx + cw and cx < w[2]]
        if words and cy <= y + h / 2 <= cy + ch:
            x0, x1 = min(w[1] for w in words), max(w[2] for w in words)
            out.append(("".join(w[0] for w in words).strip(), x0, y, x1 - x0, h, words))
    return out


def region_color():
    """What a marked region's drawn in: the app's accent, as the Save button has it."""
    return Adw.StyleManager.get_default().get_accent_color_rgba()


class Capture:
    """One screen capture for feedback: the window as an image, the text that was on screen (each
    line with where it was and which part of the window; top, where its first line was, if it's one of several; words, each
    word and the spaces after it, from where to where), and the regions marked on it."""

    def __init__(self, png, width, height, items):
        self.png, self.width, self.height, self.items, self.regions = png, width, height, items, []
        self.texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))

    def state(self):
        """As a restart keeps it (BaudeWindow.restart_state)."""
        return {"png": base64.b64encode(self.png).decode(), "width": self.width, "height": self.height,
                "items": self.items, "regions": self.regions}

    @classmethod
    def from_state(cls, state):
        capture = cls(base64.b64decode(state["png"]), state["width"], state["height"], state["items"])
        capture.regions = [tuple(r) for r in state["regions"]]
        return capture

    def text_in(self, region):
        """The on-screen text in a region (x, y, w, h), line by line: of each line whose middle runs through it (so an
        outline that just grazes the next line down doesn't take that line with it), the words it's over. (A capture kept
        from before there were words, the whole line, if the region's over it.)"""
        rx, ry, rw, rh = region
        out = []
        for it in self.items:
            if not ry <= it["y"] + it["h"] / 2 <= ry + rh:
                continue
            if "words" not in it:
                if it["x"] < rx + rw and rx < it["x"] + it["w"]:
                    out.append(it)
            elif words := [w[0] for w in it["words"] if w[1] < rx + rw and rx < w[2]]:
                out.append({**it, "text": "".join(words).strip()})
        return out

    def page_text(self):
        """Everything that was on screen, area by area, top to bottom, the lines of one text together (a table's cells,
        side by side, aren't read a line of each)."""
        out, area = [], None
        for it in sorted(self.items, key=lambda i: (i["area"], *i.get("top", (i["y"], i["x"])), i["y"], i["x"])):
            if it["area"] != area:
                area = it["area"]
                out.append(f"\n## {area}\n")
            out.append(it["text"])
        return "\n".join(out).strip() + "\n"

    def marked_png(self, renderer, widget=None):
        """The image with its regions outlined and numbered (what a reader of the feedback sees);
        widget gives the font for the numbers."""
        if not self.regions:
            return self.png
        snap = Gtk.Snapshot()
        snap.append_texture(self.texture, Graphene.Rect().init(0, 0, self.width, self.height))
        accent = region_color()
        white = Gdk.RGBA()
        white.parse("white")
        for n, (x, y, w, h) in enumerate(self.regions, 1):
            rect = Gsk.RoundedRect()
            rect.init_from_rect(Graphene.Rect().init(x, y, w, h), 3)
            snap.append_border(rect, [3, 3, 3, 3], [accent] * 4)
            if widget is not None:  # the same numbered tab as in the editor, top-right above it
                layout = widget.create_pango_layout(str(n))
                tw, th = layout.get_pixel_size()
                top = y - (th + 4) if y >= th + 4 else y
                left = x + w - (tw + 10)
                snap.append_color(accent, Graphene.Rect().init(left, top, tw + 10, th + 4))
                snap.save()
                snap.translate(Graphene.Point().init(left + 5, top + 2))
                snap.append_layout(layout, white)
                snap.restore()
        tex = renderer.render_texture(snap.to_node(), Graphene.Rect().init(0, 0, self.width, self.height))
        return tex.save_to_png_bytes().get_data()


def capture_window(win):
    """The window as it is now: an image (at the screen's pixel density), dialogs and all, and its visible text."""
    root = win.get_child()
    w, h = root.get_width(), root.get_height()
    scale = screen_scale(root)
    snap = Gtk.Snapshot()
    snap.scale(scale, scale)
    Gtk.WidgetPaintable.new(root).snapshot(snap, w, h)
    node = snap.to_node()
    renderer = win.get_native().get_renderer()
    tex = renderer.render_texture(node, Graphene.Rect().init(0, 0, w * scale, h * scale))
    # text: labels, text views and entries that are really on screen, by window area, of what's on top if something's over
    # the rest (under a dialog nothing's read)
    areas, cover = win.capture_areas(), win.capture_cover()
    if isinstance(cover, Adw.Dialog):
        title = cover.get_heading() if isinstance(cover, Adw.AlertDialog) else cover.get_title()
        areas[cover] = f"dialog: {title}" if title else "dialog"
    items = []
    for widget in walk(cover or root):
        if not isinstance(widget, (Gtk.Label, Gtk.TextView, Gtk.Text)) or not (lines := _lines_on_screen(widget, root)):
            continue
        area, p = "window", widget
        while p is not None and area == "window":
            area = areas.get(p, "window")
            p = p.get_parent()
        top = {"top": [round(lines[0][2] * scale), round(lines[0][1] * scale)]} if len(lines) > 1 else {}
        items += [{"text": text, "x": round(x * scale), "y": round(y * scale), "w": round(rw * scale),
                   "h": round(rh * scale), "area": area, **top,
                   "words": [[word, round(x0 * scale), round(x1 * scale)] for word, x0, x1 in words]}
                  for text, x, y, rw, rh, words in lines]
    png = tex.save_to_png_bytes().get_data()
    return Capture(png, tex.get_width(), tex.get_height(), items)


class RegionLayer(Gtk.Widget):
    """The marked regions, outlined and numbered, over the capture (GTK's own drawing: no cairo)."""

    def __init__(self, editor):
        super().__init__(hexpand=True, vexpand=True)
        self.editor = editor

    def do_snapshot(self, snap):
        ed = self.editor
        k, ox, oy = ed.fit()
        accent = region_color()
        boxes = list(ed.capture.regions) + ([ed.drag_rect()] if ed.drag else [])
        for i, (x, y, w, h) in enumerate(boxes):
            rect = Gsk.RoundedRect()
            rect.init_from_rect(Graphene.Rect().init(ox + x * k, oy + y * k, max(w * k, 1), max(h * k, 1)), 3)
            snap.append_border(rect, [2, 2, 2, 2], [accent] * 4)
            if i < len(ed.capture.regions):  # its number, in a tab of the same colour at the corner
                layout = self.create_pango_layout(str(i + 1))
                tw, th = layout.get_pixel_size()
                # on its top-right corner, just above it (inside only at the very top): text starts at the
                # left and seldom reaches the right, so the tab covers neither this region nor one above
                top = oy + y * k - (th + 4) if y * k >= th + 4 else oy + y * k
                left = ox + (x + w) * k - (tw + 10)
                tab = Graphene.Rect().init(left, top, tw + 10, th + 4)
                snap.append_color(accent, tab)
                snap.save()
                snap.translate(Graphene.Point().init(left + 5, top + 2))
                white = Gdk.RGBA()
                white.parse("white")
                snap.append_layout(layout, white)
                snap.restore()


class ThumbRegions(Gtk.Widget):
    """A capture's marked regions outlined over its thumbnail, in the same colour as on the capture itself (too small
    there for their numbers). Clicks go through to the thumbnail."""

    def __init__(self, capture):
        super().__init__(can_target=False)
        self.capture = capture

    def do_snapshot(self, snap):
        width, height = self.get_width(), self.get_height()
        # (fitted and centred, as the picture is in a thumbnail its row has made taller)
        k = min(width / self.capture.width, height / self.capture.height)
        ox, oy = (width - self.capture.width * k) / 2, (height - self.capture.height * k) / 2
        accent = region_color()
        for x, y, w, h in self.capture.regions:
            rect = Gsk.RoundedRect()
            rect.init_from_rect(Graphene.Rect().init(ox + x * k, oy + y * k, max(w * k, 2), max(h * k, 2)), 2)
            snap.append_border(rect, [2, 2, 2, 2], [accent] * 4)


def region_hint():
    """What marking regions says: where their words go, whatever gh says just now (a note's filed when it can be:
    keep_filing), to the repository feedback goes to, as far as that's known without asking gh. None: nowhere to file it."""
    repo = known_submit_issue_target()
    return "Drag over what you mean · those words " + \
        (f"go in the note's GitHub issue on {repo}" if repo else "go in the note's GitHub issue" if code_repo() else
         "stay with the note") + " · right-click a region to remove it"


class RegionEditor(Adw.Dialog):
    """A capture, large: drag to mark a region, right-click a region to remove it, Done (or Esc) when that's all."""

    def __init__(self, capture, on_change=None):
        super().__init__(title="Mark regions", content_width=1100, content_height=760)
        self.capture, self.on_change = capture, on_change
        self.drag = None
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", lambda *_: (self.capture.regions.clear(), self.changed()))
        header.pack_start(clear)
        self.done_btn = Gtk.Button(label="Done")
        self.done_btn.add_css_class("suggested-action")
        self.done_btn.connect("clicked", lambda *_: self.close())
        header.pack_end(self.done_btn)
        tv.add_top_bar(header)
        self.hint = Gtk.Label(label=region_hint(), wrap=True, justify=Gtk.Justification.CENTER, margin_top=4, margin_bottom=6)
        self.hint.add_css_class("dim-label")
        tv.add_top_bar(self.hint)
        self.area = RegionLayer(self)
        pointers.set_pointer(self.area, "crosshair")
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_begin)
        drag.connect("drag-update", self.on_update)
        drag.connect("drag-end", self.on_end)
        self.area.add_controller(drag)
        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right.connect("pressed", self.on_right)
        self.area.add_controller(right)
        # the image fitted into the dialog, the regions drawn over it
        pic = Gtk.Picture(paintable=capture.texture, content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        stack = Gtk.Overlay(child=pic)
        stack.add_overlay(self.area)
        tv.set_content(stack)
        self.set_child(tv)

    # image <-> widget coordinates (the image is fitted into the area, centred)
    def fit(self):
        aw, ah = self.area.get_width(), self.area.get_height()
        k = min(aw / self.capture.width, ah / self.capture.height) if aw and ah else 1
        return k, (aw - self.capture.width * k) / 2, (ah - self.capture.height * k) / 2

    def to_image(self, x, y):
        k, ox, oy = self.fit()
        return (min(max((x - ox) / k, 0), self.capture.width), min(max((y - oy) / k, 0), self.capture.height))

    def drag_rect(self):
        (x0, y0), (x1, y1) = self.drag
        return min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)

    def on_begin(self, g, x, y):
        p = self.to_image(x, y)
        self.drag = (p, p)

    def on_update(self, g, dx, dy):
        ok, x, y = g.get_start_point()
        self.drag = (self.drag[0], self.to_image(x + dx, y + dy))
        self.area.queue_draw()

    def on_end(self, g, dx, dy):
        if self.drag:
            x, y, w, h = self.drag_rect()
            self.drag = None
            if w > 4 and h > 4:
                self.capture.regions.append((round(x), round(y), round(w), round(h)))
            self.changed()

    def on_right(self, g, n, x, y):
        ix, iy = self.to_image(x, y)
        for r in reversed(self.capture.regions):
            if r[0] <= ix <= r[0] + r[2] and r[1] <= iy <= r[1] + r[3]:
                self.capture.regions.remove(r)
                break
        self.changed()

    def changed(self):
        self.area.queue_draw()
        if self.on_change:
            self.on_change()


FEEDBACK_SAVE_KEYS = tuple(f"{m}{k}" for m in ZOOM_MODS for k in ("Return", "KP_Enter"))  # (⌘↩ on macOS, Ctrl+Enter)


# -------------------------------------------------------------------- issues --
# With the gh CLI signed in to GitHub, a note saved is filed as an issue on the repository Baude runs from (its
# checkout's origin): the note, where it came from and the text in the regions marked. The captures stay with the note
# (gh can't upload images; the issue says so). issue.json in the note's folder says where it went.

GH_CHECKED_S = 300  # (gh's sign-in, once looked at, taken as it was for this long)
_gh_seen = {"at": None, "repo": None}
FILE_EVERY_S = 60  # (a look for a note not filed yet: keep_filing)
FILE_AFTER_S = 90  # (none just saved: its own save files it, and gh gives up on that after 60)
FILING_STALE_S = 300  # (a claim to be filing one, left by what failed or died: claim_note)
NOT_TAKEN_WAIT_S = 3600  # (one gh won't take, with GitHub there: left this long, the rest going on)
_not_taken, _filing = {}, {"started": False, "busy": False}
ISSUE_BODY_MOST = 65000  # (GitHub refuses a body over 65536 characters)
ISSUE_REGIONS_MOST = 20000  # (of that, the words in the regions: the rest of them stay with the note)
_REGION = r"^### Region \d+ of capture \d+.*?(?=^#{2,3} |\Z)"  # (one, and the text in it: save_feedback)
_SENT_FROM = "_Sent from Baude's Send Feedback…_"


def issues_repo():
    """Where notes are filed (owner/name): Baude's repository, or the private one it has for feedback if gh shows you
    that (fixes.submit_issue_target), if gh is there and signed in to GitHub; else None. Slow the first time (gh asks GitHub):
    off the main loop (check_issues)."""
    now, seen = time.monotonic(), _gh_seen["at"]  # (once: it's set from the thread that files, too)
    if seen is not None and now - seen < GH_CHECKED_S:
        return _gh_seen["repo"]
    repo = submit_issue_target() if gh_path() else None
    # (gh's active account can read the repository: not gh auth status, which fails for any account it has that's
    # signed out, the one it'd file with or not)
    if repo is not None and not gh("api", f"repos/{repo}", "--silent", timeout=20)[0]:
        repo = None
    _gh_seen.update(at=now, repo=repo)
    return repo


def check_issues(then):
    """then(owner/name or None) on the main loop, once issues_repo has looked."""
    def run():
        repo = issues_repo()
        GLib.idle_add(lambda: then(repo) and False)
    threading.Thread(target=run, daemon=True).start()


def issue_title(text, captures=0):
    first = next((l.strip().lstrip("#").strip() for l in text.splitlines() if l.strip()), "")
    if not first:
        return f"Feedback: {captures} screen capture{'s' if captures != 1 else ''}"
    return first if len(first) <= 90 else first[:89].rstrip() + "…"


def note_mark(folder):
    """What an issue says of the note it's from: what finds it, if it's not known whether it was made."""
    return f"baude-note-{os.path.basename(folder.rstrip(os.sep))}"


def _regions_within(md, most):
    """md, at most this long, with its regions' words each in a code fence, longer than any run of backticks in them:
    GitHub links, pings and loads nothing from what's in one, and what's quoted from a screen could say anything. Each says
    what it had to leave out, and the last how many regions there was no room for."""
    flags = re.M | re.S
    regions, at, dropped, words = len(re.findall(_REGION, md, flags=flags)), 0, 0, ISSUE_REGIONS_MOST
    left_most, none_most = 80, 60  # (the longest each of those lines can be)
    room = most - len(re.sub(_REGION, "", md, flags=flags)) - none_most

    def one(m):
        nonlocal at, dropped, room, words
        at += 1
        head, _, text = m.group().partition("\n")
        head, text = head.split(" (x ")[0], text.strip()  # (where on a picture they don't have says nothing)
        quoted = text.startswith(">")
        text = re.sub(r"^> ?", "", text, flags=re.M) if quoted else text
        run = max(map(len, re.findall(r"`+", text)), default=2) + 1
        fixed = len(head) + 2 * run + 8 + left_most  # (the heading, the fences, the blank lines, and what's left out)
        piece = ""
        if dropped or room < fixed:
            dropped += 1
        else:
            kept, left, fits = text, "", min(words, room - fixed)
            if len(text) > fits:
                cut = text.rfind("\n", 0, fits)
                kept = text[:cut if cut > 0 else fits]
                left = f"_(The other {len(text) - len(kept)} characters of its text are kept with the note.)_"
            words -= len(kept)
            if quoted and kept:
                fence = "`" * (max(map(len, re.findall(r"`+", kept)), default=2) + 1)
                kept = f"{fence}\n{kept}\n{fence}"
            piece = "\n\n".join(p for p in (head, kept, left) if p) + "\n\n"
            room -= len(piece)
        if at == regions and dropped:
            piece += f"_(The words in {dropped} more region{'s' if dropped != 1 else ''} are kept with the note.)_\n\n"
        return piece
    return re.sub(_REGION, one, md, flags=flags)


def issue_body(md, folder=None):
    """feedback.md as an issue's body, always short enough for GitHub: its heading left to the title, each capture's image
    said to be kept with the note (not uploaded), its screen text's link left out, and the words in its regions in code
    fences (_regions_within): whoever reads the issue has them, not the picture. The note's mark is its last line."""
    md = md.split("\n", 1)[1] if md.startswith("# Feedback") else md
    # (a note saved when a region's text was said to stay with it, its heading as it was then: it still does)
    md = re.sub(r"^### Region \d+ \(.*?(?=^#{2,3} |\Z)", "", md, flags=re.M | re.S)
    md = re.sub(r"^!\[capture (\d+)\]\([^)]*\)$", r"_(Capture \1: kept with the note in Baude, not uploaded.)_", md,
                flags=re.M)
    md = re.sub(r"^Screen text: \[[^\]]*\]\([^)]*\)\n?", "", md, flags=re.M)
    end = "\n\n" + _SENT_FROM + (f" `{note_mark(folder)}`" if folder else "") + "\n"
    md = _regions_within(md, ISSUE_BODY_MOST - len(end)).strip()
    if len(md) > ISSUE_BODY_MOST - len(end):  # (the note by itself)
        cut = "\n\n_(The rest is kept with the note.)_"
        md = md[:ISSUE_BODY_MOST - len(end) - len(cut)] + cut
    return md + end


class NoteBusy(RuntimeError):
    """Another's filing the note, or tried a moment ago: claim_note."""


def claim_note(folder):
    """This one's turn to file the note in folder (.filing), or NoteBusy: Baude and Record both look for notes to file. A
    claim stays if it fails, whoever tried next waiting till it's stale: GitHub may have made the issue all the same, and
    what finds that takes it a while to see one."""
    claim = os.path.join(folder, ".filing")
    try:
        if time.time() - os.path.getmtime(claim) > FILING_STALE_S:
            os.remove(claim)
    except OSError:
        pass
    try:
        os.close(os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except OSError:
        raise NoteBusy("Another window is filing this") from None


def file_issue(folder, repo, again=False):
    """The note in folder filed on repo (gh issue create), once: its url, kept in the folder's issue.json. again: it may
    have been before, without its being known (found_issue). Raises RuntimeError with gh's error if it wasn't."""
    claim_note(folder)
    url = _file_claimed(folder, repo, again)
    try:
        os.remove(os.path.join(folder, ".filing"))
    except OSError:
        pass
    return url


def found_issue(folder, repo):
    """The issue on repo that says it's this note's (note_mark) in its last line, as gh issue create left it (anywhere else
    it's only what an issue quotes): {url, number}, or None. Raises RuntimeError if gh can't say."""
    mark = note_mark(folder)
    ok, out = gh("issue", "list", "--repo", repo, "--state", "all", "--limit", "5", "--search", f'"{mark}" in:body',
                 "--json", "number,url,body")
    if not ok:
        raise RuntimeError(out or "gh issue list failed")
    last = f"{_SENT_FROM} `{mark}`"
    return next((i for i in json.loads(out or "[]") if (i.get("body") or "").rstrip().rsplit("\n", 1)[-1].strip() == last), None)


def _keep_issue(folder, url, number, repo):
    tmp = os.path.join(folder, "issue.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"url": url, "number": number, "repo": repo}, f)
    os.replace(tmp, os.path.join(folder, "issue.json"))
    return url


def _file_claimed(folder, repo, again):
    try:  # (filed since it was looked at)
        with open(os.path.join(folder, "issue.json"), encoding="utf-8") as f:
            return json.load(f)["url"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if again and (made := found_issue(folder, repo)) is not None:
        return _keep_issue(folder, made["url"], made["number"], repo)
    with open(os.path.join(folder, "feedback.md"), encoding="utf-8") as f:
        md = f.read()
    text = md.split("\n", 1)[1].split("\n## ", 1)[0].strip() if "\n" in md else ""
    captures = sum(re.fullmatch(r"capture-\d+\.png", n) is not None for n in os.listdir(folder))
    ok, out = gh("issue", "create", "--repo", repo, "--title", issue_title("" if text == "(no text)" else text, captures),
                 "--body-file", "-", body=issue_body(md, folder))
    url = next((l for l in out.splitlines() if l.startswith("https://")), None) if ok else None
    if url is None:
        raise RuntimeError(out or "gh issue create failed")
    m = re.search(r"/issues/(\d+)", url)
    return _keep_issue(folder, url, int(m.group(1)) if m else None, repo)


def file_next_saved():
    """The oldest note not filed yet, filed, if gh can reach GitHub: its url, else None. Off the main loop. Not one just
    saved (its own save files it), nor one resolved. One gh won't take is left a while, so it doesn't hold up the rest."""
    now, waiting = time.time(), []
    for note in reversed(feedback_items()):
        folder = note["folder"]
        if note["issue"] is None and note["resolution"] is None and now - _not_taken.get(folder, 0) > NOT_TAKEN_WAIT_S:
            try:
                if now - os.path.getmtime(os.path.join(folder, "feedback.md")) > FILE_AFTER_S:
                    waiting.append(folder)
            except OSError:
                pass
    if not waiting or (repo := issues_repo()) is None:
        return None
    for folder in waiting:
        try:
            return file_issue(folder, repo, again=True)
        except NoteBusy:
            continue  # (the next's turn)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError):
            _gh_seen["at"] = None  # (the network gone, or its sign-in? Then it's none of the note's doing)
            if issues_repo() is not None:
                _not_taken[folder] = now
            return None
    return None


def keep_filing():
    """Notes not filed yet (saved offline or signed out) filed as they can be, a note a look: started once, by the first
    window. (BAUDE_KEEP_FILING=0: not; a test's stand-in gh doesn't get notes from nowhere.)"""
    if _filing["started"] or os.environ.get("BAUDE_KEEP_FILING") == "0":
        return
    _filing["started"] = True

    def run():
        try:
            file_next_saved()
        finally:
            _filing["busy"] = False

    def look():
        if not _filing["busy"]:
            _filing["busy"] = True
            threading.Thread(target=run, daemon=True).start()
        return True
    GLib.timeout_add_seconds(FILE_EVERY_S, look)


def file_soon(win, folder, repo):
    """The note filed on repo off the main loop, a toast in win saying it was (Open issue), or why not."""
    def done(url, error):
        if url:
            toast = Adw.Toast(title=f"Filed as an issue on {repo}", button_label="Open issue", timeout=8)
            toast.connect("button-clicked", lambda *_: Gtk.UriLauncher(uri=url).launch(win, None, None))
        else:
            why = (error or "").splitlines()
            toast = Adw.Toast(title=html.escape(f"Saved, and it'll be filed on {repo} when it can be"
                                                + (f": {why[0][:100]}" if why else ""), quote=False), timeout=10)
        if getattr(win, "toasts", None) is not None:
            win.toasts.add_toast(toast)
        return False

    def run():
        try:
            url, error = file_issue(folder, repo), None
        except (OSError, RuntimeError) as e:
            url, error = None, str(e)
        GLib.idle_add(done, url, error)
    threading.Thread(target=run, daemon=True).start()


class FeedbackDraft:
    """What's been written and captured so far: kept while the dialog is closed for a capture."""

    def __init__(self):
        self.text, self.captures = "", []

    def state(self):
        """As a restart keeps it, if there's anything in it."""
        return {"text": self.text, "captures": [c.state() for c in self.captures]} \
            if self.text.strip() or self.captures else None

    @classmethod
    def from_state(cls, state):
        draft = cls()
        draft.text, draft.captures = state["text"], [Capture.from_state(c) for c in state["captures"]]
        return draft


class FeedbackDialog(Adw.Dialog):
    """Menu → Send Feedback… (⌘. / Ctrl+.): the note, in a box as the chat's is (widgets.Composer), and screen captures (its
    +: Baude hides this, you go to what you want to show, and press Capture in the bar at the bottom; or, over a dialog or a
    view that the bar's under, it captures that at once). A capture goes on to its regions (RegionEditor), then back here,
    its thumbnail over the box as an attachment is over the chat's."""

    def __init__(self, win, draft):
        super().__init__(title="Feedback", content_width=560)
        self.win, self.draft = win, draft
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.discard())
        header.pack_start(cancel)
        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.connect("clicked", lambda *_: self.save())
        header.pack_end(self.save_btn)
        tv.add_top_bar(header)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=18, margin_end=18,
                       margin_top=6, margin_bottom=18)
        self.shots = Gtk.Box(spacing=6)
        self.strip = Gtk.ScrolledWindow(child=self.shots, visible=False, vexpand=False,
                                        vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        body.append(self.strip)
        # (the text's right margin keeps it from running under the +)
        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=10, bottom_margin=10,
                                 left_margin=10, right_margin=58)
        self.text.add_css_class("feedback-text")
        no_text_drag(self.text)  # (a selection isn't dragged away: a press in it places the caret)
        self.text.get_buffer().set_text(draft.text)
        self.text.get_buffer().connect("changed", lambda *_: self.sync())
        frame = Gtk.ScrolledWindow(child=self.text, min_content_height=140, max_content_height=280,
                                   propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True)
        self.box = Gtk.Overlay(child=frame, hexpand=True)
        self.box.add_css_class("card")
        self.box.add_css_class("composer-box")
        self.box.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_btn = Gtk.Button(icon_name="list-add-symbolic",
                                  tooltip_text="Capture what's open now" if win.capture_cover() else
                                  "Add a screen capture: go to it, then press Capture")
        self.add_btn.add_css_class("flat")
        self.add_btn.connect("clicked", lambda *_: self.win.start_feedback_capture(self))
        actions = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=10, margin_bottom=10)
        actions.add_css_class("composer-actions")
        actions.append(self.add_btn)
        self.box.add_overlay(actions)
        body.append(self.box)
        tv.set_content(body)
        self.set_child(tv)
        self.repo = None
        check_issues(self.found_repo)
        self.fill()
        self.sync()
        # (the note has the focus, not Cancel, its cursor after what's written: click, type, ⌘↩ / Ctrl+Enter saves;
        # caught before the TextView, which would make it a new line)
        buf = self.text.get_buffer()
        buf.place_cursor(buf.get_end_iter())
        self.connect("map", lambda *_: self.text.grab_focus())
        keys = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        for t in FEEDBACK_SAVE_KEYS:
            keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(t), Gtk.CallbackAction.new(
                lambda *_: (self.save() if self.save_btn.get_sensitive() else None) or True)))
        self.add_controller(keys)

    def found_repo(self, repo):
        self.repo = repo

    def sync(self):
        buf = self.text.get_buffer()
        self.draft.text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        self.save_btn.set_sensitive(bool(self.draft.text.strip() or self.draft.captures))

    def fill(self):
        while (c := self.shots.get_first_child()) is not None:
            self.shots.remove(c)
            discard(c)
        for i, cap in enumerate(self.draft.captures):
            self.shots.append(self.thumb(i, cap))
        self.strip.set_visible(bool(self.draft.captures))

    def thumb(self, i, cap):
        box = Gtk.Overlay()
        pic = FixedPicture(size=fit_size(cap.width, cap.height, 160),
                           load=lambda scale, cap=cap: scaled_texture(cap.png, 160, scale))
        over = Gtk.Overlay(child=pic)
        over.add_overlay(ThumbRegions(cap))
        btn = pointers.magnifier(Gtk.Button(child=over, tooltip_text="Mark regions on this capture"))
        btn.add_css_class("feedback-thumb")
        btn.connect("clicked", lambda *_: self.mark(cap))
        box.set_child(btn)
        n = len(cap.regions)
        tag = Gtk.Label(label=f"{n} region{'s' if n != 1 else ''}" if n else "click to mark regions",
                        halign=Gtk.Align.START, valign=Gtk.Align.END, margin_start=6, margin_bottom=6)
        tag.add_css_class("osd")
        tag.add_css_class("feedback-tag")
        box.add_overlay(tag)
        remove = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Remove this capture",
                            halign=Gtk.Align.END, valign=Gtk.Align.START, margin_end=4, margin_top=4)
        remove.add_css_class("osd")
        remove.add_css_class("circular")
        remove.connect("clicked", lambda *_: self.drop_capture(cap))
        box.add_overlay(remove)
        return box

    def mark(self, cap):
        """A capture's regions marked, over this. Back from it, the note has the focus again: Adw gives it to Cancel, which
        Return or Space would press, and what's written would go."""
        editor = RegionEditor(cap, on_change=self.fill)
        editor.connect("notify::parent", lambda e, _p: e.get_parent() is None and self.text.grab_focus())
        editor.present(self.win)
        return editor

    def drop_capture(self, cap):
        self.draft.captures.remove(cap)
        self.fill()
        self.sync()
        self.text.grab_focus()  # (what had it is gone, and ⌘↩ with it)

    def discard(self):
        self.win.feedback_draft = None
        self.close()

    def save(self):
        folder = save_feedback(self.draft, self.win.get_native().get_renderer(), self.win)
        self.win.feedback_draft = None
        self.close()
        toast = Adw.Toast(title="Feedback saved" + (", filing it…" if self.repo else ""), button_label="Open folder",
                          timeout=6)
        toast.connect("button-clicked", lambda *_: Gtk.FileLauncher.new(Gio.File.new_for_path(folder))
                      .launch(self.win, None, None))
        self.win.toasts.add_toast(toast)
        if self.repo:
            file_soon(self.win, folder, self.repo)
        return folder


def save_feedback(draft, renderer, win=None):
    """feedback/<time>/: feedback.md (what you wrote, where it came from and the text in each capture's regions, quoted,
    each capture), capture-N.png, capture-N-marked.png (regions drawn on) and capture-N.txt (the screen's text).
    Returns the folder."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder, n = os.path.join(feedback_dir(), stamp), 1
    while os.path.exists(folder):  # (two in a second: each its own, never one written over the other)
        n += 1
        folder = os.path.join(feedback_dir(), f"{stamp}-{n}")
    os.makedirs(folder)
    lines = [f"# Feedback {time.strftime('%Y-%m-%d %H:%M')}", "", draft.text.strip() or "(no text)", ""]
    # (- session: … (title), - folder: …, the app's path)
    context = win.feedback_context() if win is not None and hasattr(win, "feedback_context") else []
    if context or any(cap.regions for cap in draft.captures):
        lines += ["## Context", "", *context, ""]
    for n, cap in enumerate(draft.captures, 1):
        for r, region in enumerate(cap.regions, 1):
            x, y, w, h = region
            lines += [f"### Region {r} of capture {n} (x {x}, y {y}, {w}×{h} px)", ""]
            texts = cap.text_in(region)
            lines += [f"> {t['text']}" if "\n" not in t["text"] else "\n".join("> " + l for l in t["text"].splitlines())
                      for t in texts] or ["(no text in it)"]
            lines.append("")
    for n, cap in enumerate(draft.captures, 1):
        with open(os.path.join(folder, f"capture-{n}.png"), "wb") as f:
            f.write(cap.png)
        with open(os.path.join(folder, f"capture-{n}.txt"), "w", encoding="utf-8") as f:
            f.write(cap.page_text())
        lines += [f"## Capture {n}", "", f"![capture {n}](capture-{n}{'-marked' if cap.regions else ''}.png)",
                  f"Screen text: [capture-{n}.txt](capture-{n}.txt)", ""]
        if cap.regions:
            with open(os.path.join(folder, f"capture-{n}-marked.png"), "wb") as f:
                f.write(cap.marked_png(renderer, win))
    with open(os.path.join(folder, "feedback.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.join(folder, "captures.json"), "w", encoding="utf-8") as f:
        json.dump([{"image": f"capture-{n}.png", "size": [c.width, c.height], "regions": c.regions,
                    "text": c.items} for n, c in enumerate(draft.captures, 1)], f, indent=1)
    return folder


def feedback_items():
    """What's been saved (feedback/<time>/), newest first: [{folder, when, text, chat, cwd, captures, resolution,
    resolved_at, issue}]; issue, where it was filed ({url, number, repo}: issue.json), or None. A resolution is resolution.md in the item's folder (markdown), written by whoever resolved it: a
    chat, or you; resolved_at is when it was written. None: still open."""
    try:
        names = sorted(os.listdir(feedback_dir()), reverse=True)
    except OSError:
        return []
    items = []
    for name in names:
        folder = os.path.join(feedback_dir(), name)
        try:
            with open(os.path.join(folder, "feedback.md"), encoding="utf-8", errors="replace") as f:
                md = f.read()
        except OSError:
            continue
        head, _, rest = md.partition("\n")
        context = dict(re.findall(r"^- (session|folder): (.*)$", md, re.M))
        chat = re.search(r"\((.*)\)$", context.get("session", ""))
        resolution = resolved_at = issue = None
        try:
            with open(os.path.join(folder, "issue.json"), encoding="utf-8") as f:
                issue = json.load(f)
            issue = issue if isinstance(issue, dict) else None
        except (OSError, ValueError):
            pass
        try:
            with open(os.path.join(folder, "resolution.md"), encoding="utf-8", errors="replace") as f:
                resolution = f.read().strip()
            resolved_at = os.path.getmtime(os.path.join(folder, "resolution.md"))
        except OSError:
            pass
        items.append({"folder": folder, "when": head.removeprefix("# Feedback").strip(),
                      "text": rest.split("\n## ", 1)[0].strip(), "chat": chat.group(1) if chat else "",
                      "cwd": context.get("folder", ""), "resolution": resolution, "resolved_at": resolved_at,
                      "issue": issue if isinstance(issue, dict) and issue.get("url") else None,
                      "captures": sum(re.fullmatch(r"capture-\d+\.png", n) is not None for n in os.listdir(folder))})
    return items


class FeedbackHost:
    """What a window needs to take feedback (Baude's, Record's; mixed in): Menu → Send Feedback… (open_feedback), ⌘. /
    Ctrl+. (feedback_key), the Capture bar while a capture's pending (make_capture_bar: the window puts it at its bottom),
    and of its own capture_areas() (its parts, by name, for a capture's text), feedback_context() (what a note says it came
    from), toasts (an Adw.ToastOverlay), and lightbox, kube and schema (the views that go over all of it)."""

    feedback_draft = None
    feedback_shooting = False  # (a capture about to be taken: capture_soon)

    def make_capture_bar(self):
        """The Capture bar (self.capture_bar, a Revealer; self.capture_btn its button), hidden till a capture's begun."""
        keep_filing()
        bar = Gtk.ActionBar()
        bar.add_css_class("capture-bar")
        msg = Gtk.Label(label="Feedback: go to what you want to show, then capture it", xalign=0,
                        ellipsize=Pango.EllipsizeMode.END)
        bar.pack_start(Gtk.Image(icon_name="camera-photo-symbolic"))
        bar.pack_start(msg)
        shoot = Gtk.Button(label="Capture")
        shoot.add_css_class("suggested-action")
        shoot.connect("clicked", lambda *_: self.take_feedback_capture())
        back = Gtk.Button(label="Cancel")
        back.connect("clicked", lambda *_: self.end_feedback_capture())
        bar.pack_end(shoot)
        bar.pack_end(back)
        self.capture_bar = ShutBar(child=bar, reveal_child=False, transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        self.capture_btn = shoot
        self.take_feedback_key(self)
        self.connect("notify::visible-dialog", FeedbackHost.dialog_takes_feedback_key)
        return self.capture_bar

    def capture_areas(self):
        return {}

    def feedback_context(self):
        return []

    def open_feedback(self):
        self.capture_bar.set_reveal_child(False)  # (from the menu, with one pending: the note, not that)
        self.feedback_draft = self.feedback_draft or FeedbackDraft()
        self.feedback_dialog = FeedbackDialog(self, self.feedback_draft)
        self.feedback_dialog.present(self)
        return self.feedback_dialog

    def take_feedback_key(self, widget):
        """⌘. / Ctrl+. on widget, before what has the focus: GTK's text boxes take Ctrl+. for their emoji chooser."""
        keys = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(FEEDBACK_KEY),
                                           Gtk.CallbackAction.new(lambda *_: self.feedback_key() or True)))
        widget.add_controller(keys)

    def dialog_takes_feedback_key(self, _pspec):
        """A dialog over the window has the keys to itself (none get as far as the window's controllers): each its own."""
        dialog = self.get_visible_dialog()
        if dialog is not None and not getattr(dialog, "feedback_key_taken", False):
            dialog.feedback_key_taken = True
            self.take_feedback_key(dialog)

    def feedback_key(self):
        """⌘. / Ctrl+., over whatever's open: Send Feedback. With a capture pending it's Capture, whatever has come over
        the bar since. With Feedback or its regions open already, nothing."""
        if self.feedback_shooting or any(isinstance(d, (FeedbackDialog, RegionEditor)) for d in self.get_dialogs()):
            return
        if self.capture_bar.get_reveal_child():
            self.take_feedback_capture()
        else:
            self.open_feedback()

    def capture_cover(self):
        """What's over everything else in the window, if anything: the dialog on top (not Feedback or its regions, gone
        by the time it's captured), the large image, the Kubernetes or the database view. What's under it isn't seen, and
        the Capture bar can't be got at."""
        dialogs = [d for d in self.get_dialogs() if not isinstance(d, (FeedbackDialog, RegionEditor))]
        if dialogs:
            return dialogs[-1]
        return next((v for v in (self.lightbox, self.kube, self.schema) if v.get_visible()), None)

    def start_feedback_capture(self, dialog):
        """The note's +: put the dialog away and show the Capture bar until a capture (or Cancel). With something over the
        window the bar couldn't be pressed, and there's nowhere to go first: that's captured as soon as the dialog's gone."""
        if self.feedback_shooting:  # (a double click: the dialog's still there as it goes)
            return
        if self.capture_cover() is None:
            dialog.close()
            self.capture_bar.set_reveal_child(True)
        else:
            self.feedback_shooting = True
            # (not "closed": that's as it starts to fade, and it would be in the picture)
            dialog.connect("notify::parent", lambda d, _p: d.get_parent() is None and self.capture_soon())
            dialog.close()

    def end_feedback_capture(self, capture=None):
        """Back from the bar. With a capture, on to marking its regions, and the note after that."""
        self.capture_bar.set_transition_duration(250)
        self.capture_bar.set_reveal_child(False)
        if capture is None:
            self.open_feedback()
            return
        self.feedback_draft.captures.append(capture)
        editor = RegionEditor(capture)
        editor.connect("closed", lambda *_: self.open_feedback())
        editor.present(self)

    def take_feedback_capture(self):
        """Hide the bar at once (it isn't part of what you're showing), then capture."""
        if self.feedback_shooting:
            return
        self.capture_bar.set_transition_duration(0)
        self.capture_bar.set_reveal_child(False)
        self.capture_soon()

    def capture_soon(self):
        """Wait for a frame without what's just gone, then capture."""
        self.feedback_shooting = True

        def shoot(*_):
            self.feedback_shooting = False
            self.end_feedback_capture(capture_window(self))
            return False
        GLib.timeout_add(120, shoot)
