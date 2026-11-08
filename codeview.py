"""The code and files pages (CodeView, a file as code; CodePage, the side panel's; FilesPage, the folder's files), with what only they need. Baude and Record both use them."""
import argparse
import array
import base64
import glob
import hashlib
import html
import itertools
import json
import math
import os
import re
import shutil
import socket
import struct
import weakref
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import queue
import urllib.parse

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402,F401

import lookup  # noqa: E402,F401
from lookup import (  # noqa: E402,F401  (⌘K's engine: what a folder has)
    _git, git_exe,
)
from widgets import (  # noqa: E402,F401
    IMAGE_POOL, FindBar, LineGutter, WorkerPool, ZOOM_MAX, ZOOM_MIN, ZOOM_STEP,
    code_spans, code_style, discard, fmt_scale, load_settings, open_code,
    open_in_editor, pretty_path, save_settings, themed_icon, toast,
)
from widgets import empty_label  # noqa: E402
import pointers  # noqa: E402


CODE_LIMIT = 4 * 1024 * 1024  # bigger files open in the editor instead


def python_context(text, line):
    """For a line of Python: the classes and functions around it ([(kind, name, line)], outermost first) and
    the class it belongs to (or, for a plain function, a class of this module its signature names):
    {"name", "bases", "decorators", "doc", "start", "end", "members": [(kind, name, line)], "related"}."""
    import ast
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return [], None
    defs = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    chain, node = [], tree
    while True:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, defs):
                start = min([d.lineno for d in child.decorator_list] + [child.lineno])
                if start <= line <= (child.end_lineno or child.lineno):
                    chain.append(child)
                    node = child
                    break
        else:
            break
    crumbs = [("class" if isinstance(n, ast.ClassDef) else "def", n.name, n.lineno) for n in chain]
    cls = next((n for n in reversed(chain) if isinstance(n, ast.ClassDef)), None)
    related = False
    if cls is None and chain:  # a plain function: a class of this module named in its signature
        fn, top = chain[-1], {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        names = [n.id for a in [*(a.annotation for a in [*fn.args.args, *fn.args.kwonlyargs]), fn.returns]
                 if a is not None for n in ast.walk(a) if isinstance(n, ast.Name)]
        cls = next((top[n] for n in names if n in top), None)
        related = cls is not None
    if cls is None:
        return crumbs, None
    members = []
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members.append(("def", item.name + "()", item.lineno))
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            members.append(("field", item.target.id, item.lineno))
        elif isinstance(item, ast.Assign):
            members += [("attr", t.id, item.lineno) for t in item.targets if isinstance(t, ast.Name)]
        elif isinstance(item, ast.ClassDef):
            members.append(("class", item.name, item.lineno))
    doc = (ast.get_docstring(cls) or "").strip().split("\n\n")[0].replace("\n", " ")
    return crumbs, {"name": cls.name, "bases": [ast.unparse(b) for b in cls.bases],
                    "decorators": [ast.unparse(d) for d in cls.decorator_list], "doc": doc,
                    "start": min([d.lineno for d in cls.decorator_list] + [cls.lineno]),
                    "end": cls.end_lineno or cls.lineno, "members": members, "related": related}


class FindInText:
    """Ctrl+F in a text view (mixed in, on a widget whose .view it is: CodeView, and a page of Record's notes): a find
    bar over it (make_find_bar), every match shaded, the current one more, "3 of 12" beside the box, Enter / Ctrl+G for
    the next, Shift+Enter / Ctrl+Shift+G for the one before, Esc to close; text selected is what it looks for."""

    MATCH_LIMIT = 5000  # matches shaded at most
    FIND_FLAGS = Gtk.TextSearchFlags.CASE_INSENSITIVE

    def make_find_bar(self):
        """The bar, for the widget to put over its text, and its tags (over those made so far)."""
        self.search_entry = Gtk.SearchEntry(placeholder_text="Find", hexpand=True)
        self.search_count = Gtk.Label(width_chars=9, xalign=1)
        self.search_count.add_css_class("dim-label")
        self.search_count.add_css_class("caption")
        bar = Gtk.Box(spacing=4)
        bar.append(self.search_entry)
        bar.append(self.search_count)
        for icon, tip, step in (("go-up-symbolic", "Previous match (Shift+Enter)", -1),
                                ("go-down-symbolic", "Next match (Enter)", 1)):
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_, step=step: self.find_next(step))
            bar.append(btn)
        self.search_bar = FindBar(child=bar, show_close_button=True)
        # (Esc: close_search before the bar's own, which closes it, the matches going with it: DiaryEditor's wants them)
        self.search_entry.connect("stop-search", lambda *_: self.close_search())
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.connect("notify::search-mode-enabled",
                                lambda *_: self.search_bar.get_search_mode() or self.clear_matches())
        click = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", lambda *_: self.close_for_click())
        self.view.add_controller(click)
        self.search_entry.connect("search-changed", lambda e: self.find(e.get_text()))
        self.search_entry.connect("activate", lambda *_: self.find_next(1))
        self.search_entry.connect("next-match", lambda *_: self.find_next(1))
        self.search_entry.connect("previous-match", lambda *_: self.find_next(-1))
        keys = Gtk.EventControllerKey()  # (Shift+Enter: the match before)
        keys.connect("key-pressed", lambda _c, kv, _k, state: (self.find_next(-1), True)[1]
                     if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.SHIFT_MASK else False)
        self.search_entry.add_controller(keys)
        self.match_tag = self.view.get_buffer().create_tag("search-match")
        self.current_tag = self.view.get_buffer().create_tag("search-current")
        self.matches, self.match_index = [], -1
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.find_colours())
        self.find_colours()
        return self.search_bar

    def find_tints(self):
        """The (red, green, blue, alpha) of a match and of the current one, for the light or dark theme."""
        return ((255, 214, 102, 0.28), (255, 170, 40, 0.75)) if Adw.StyleManager.get_default().get_dark() else \
            ((255, 200, 0, 0.35), (255, 150, 0, 0.70))

    def find_colours(self):
        """The matches' colours, as the theme changes."""
        match, current = self.find_tints()
        self.match_tag.set_property("background", "rgba(%d,%d,%d,%g)" % match)
        self.current_tag.set_property("background", "rgba(%d,%d,%d,%g)" % current)

    def open_search(self):
        """Show the find bar, with the selected text in it if there is some (one line), and focus it."""
        b = self.view.get_buffer()
        if b.get_has_selection():
            start, end = b.get_selection_bounds()
            picked = b.get_text(start, end, False)
            if picked and "\n" not in picked:
                self.search_entry.set_text(picked)
        self.search_bar.set_search_mode(True)
        self.search_entry.grab_focus()
        self.search_entry.select_region(0, -1)
        if self.search_entry.get_text() and not self.matches:
            self.find(self.search_entry.get_text())

    def close_search(self):
        self.search_bar.set_search_mode(False)
        self.view.grab_focus()

    def find_adjustment(self):
        """What scrolls the text."""
        return self.view.get_vadjustment()

    def close_for_click(self):
        """A click in the text with the bar open: back to the text, where it's clicked. The bar's over it, so the text would
        rise as it goes: it goes at once, and the page back by as much, what's under the pointer staying there."""
        def close():
            adj, slide = self.find_adjustment(), self.search_bar.get_first_child()  # (its revealer)
            height, takes = self.search_bar.get_height(), slide.get_transition_duration()
            slide.set_transition_duration(0)
            self.search_bar.set_search_mode(False)
            slide.set_transition_duration(takes)
            adj.set_value(adj.get_value() - height)
            return False
        if self.search_bar.get_search_mode():
            GLib.idle_add(close, priority=GLib.PRIORITY_HIGH)  # (once the text has had the press, by the page as it's shown)

    def clear_matches(self):
        b = self.view.get_buffer()
        b.remove_tag(self.match_tag, b.get_start_iter(), b.get_end_iter())
        b.remove_tag(self.current_tag, b.get_start_iter(), b.get_end_iter())
        self.matches, self.match_index = [], -1
        self.search_count.set_label("")

    def find(self, text, scroll=True, near=None):
        """Shade every match of text (ignoring case); the current one is the first at or after near (an offset), or
        else the top of what's shown."""
        self.clear_matches()
        if not text:
            return
        b, it = self.view.get_buffer(), self.view.get_buffer().get_start_iter()
        while len(self.matches) < self.MATCH_LIMIT and (found := it.forward_search(text, self.FIND_FLAGS, None)):
            if found[0].equal(found[1]):
                break
            it = found[1]
            if (span := self.match_span(*found)) is not None:
                b.apply_tag(self.match_tag, *span)
                self.matches.append((span[0].get_offset(), span[1].get_offset()))
        if not self.matches:
            self.search_count.set_label("No matches")
            return
        top = self.find_top() if near is None else near
        first = next((i for i, (s0, _e) in enumerate(self.matches) if s0 >= top), 0)
        self.show_match(first, scroll)

    def find_next(self, step):
        if not self.matches:
            if self.search_entry.get_text():
                self.find(self.search_entry.get_text())
            return
        self.show_match((self.match_index + step) % len(self.matches))

    def show_match(self, i, scroll=True):
        b = self.view.get_buffer()
        if 0 <= self.match_index < len(self.matches):
            s0, e0 = self.matches[self.match_index]
            b.remove_tag(self.current_tag, b.get_iter_at_offset(s0), b.get_iter_at_offset(e0))
        self.match_index = i
        s0, e0 = self.matches[i]
        start, end = b.get_iter_at_offset(s0), b.get_iter_at_offset(e0)
        b.apply_tag(self.current_tag, start, end)
        more = "+" if len(self.matches) >= self.MATCH_LIMIT else ""
        self.search_count.set_label(f"{i + 1} of {len(self.matches)}{more}")
        mark = b.get_mark("found") or b.create_mark("found", start, True)
        b.move_mark(mark, start)
        if scroll:
            self.find_scroll(mark)

    def match_span(self, start, end):
        """What of a match found to shade (start, end), or None if it's not to count."""
        return start, end

    def find_top(self):
        """The offset at the top of what's shown."""
        return self.view.get_line_at_y(self.view.get_visible_rect().y)[0].get_offset()

    def find_scroll(self, mark):
        """The match at mark brought into view."""
        self.view.scroll_to_mark(mark, 0.1, False, 0.0, 0.0)


class CodeView(Gtk.Box, FindInText):
    """Read-only code: highlighted (off the main thread, applied in chunks, the marked line's part first),
    with line numbers, a marked line and a shaded range (the class around it). Long lines wrap if you like
    (set_wrap), and Ctrl+F finds text in it (FindInText). The text has a size of its own, the same in every CodeView
    and apart from the chat's (set_zoom)."""

    CHUNK = 3000  # spans tagged per idle step
    zoom, zoom_css = 1.0, None  # the code's text size (remembered as "code_zoom"), and the CSS that sets it
    scrolled = 0.0, 0  # Ctrl+scroll that hasn't made a whole step yet, and when it came (ms)
    live = weakref.WeakSet()
    __gsignals__ = {"zoomed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.NONE,
                                 left_margin=10, right_margin=10, top_margin=6, bottom_margin=24)
        self.view.add_css_class("code-view")
        self.buffer = self.view.get_buffer()
        self.gutter = LineGutter(self.view)
        self.view.set_gutter(Gtk.TextWindowType.LEFT, self.gutter)
        self.scroll = Gtk.ScrolledWindow(child=self.view, vexpand=True, hexpand=True)
        # (the zoom sizes it; the text and its numbers follow. Not the chat's code blocks' code-scroll: the code's
        # size was theirs too, and the whole chat was laid out again, moving under you, as the code was zoomed)
        self.scroll.add_css_class("code-view-scroll")
        self.scroll.get_vadjustment().connect("value-changed", lambda *_: self.gutter.queue_draw())
        self.range_tag = self.buffer.create_tag("marked-range")
        self.line_tag = self.buffer.create_tag("marked-line")  # (made last: it's drawn over the range)
        self.append(self.make_find_bar())  # (its tags over the marks; token tags are under them)
        self.append(self.scroll)
        find = Gtk.ShortcutController()  # (Ctrl+F with the code focused; the window opens it from elsewhere)
        find.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>f"),
                                           Gtk.CallbackAction.new(lambda *_: self.open_search() or True)))
        self.add_controller(find)
        self.link_tag = self.buffer.create_tag("code-link", underline=Pango.Underline.SINGLE)
        self.links, self.on_link = [], None  # [(start, end, target)] (set_links), and what a click on one calls
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("released", self._link_clicked)
        self.view.add_controller(click)
        hover = Gtk.EventControllerMotion()
        hover.connect("motion", lambda _c, x, y: self._link_cursor(x, y))
        self.view.add_controller(hover)
        self.token_tags, self.generation, self.path, self.dark = {}, 0, "", None
        self.highlighted = 0  # spans tagged so far (tests and the status wait on it)
        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", lambda *_: self.restyle())
        self.restyle()
        CodeView.live.add(self)
        CodeView.set_zoom(float(load_settings().get("code_zoom") or 1.0), save=False)  # (the size it was left at)

    def restyle(self):
        """Colours for the light or dark theme: the marks and every token tag made so far."""
        self.dark = Adw.StyleManager.get_default().get_dark()
        line, rng = ("rgba(255,214,102,0.20)", "rgba(120,160,255,0.08)") if self.dark else \
            ("rgba(255,200,0,0.22)", "rgba(53,132,228,0.06)")
        self.line_tag.set_property("paragraph-background", line)
        self.range_tag.set_property("paragraph-background", rng)
        self.link_tag.set_property("foreground", "#78aeed" if self.dark else "#1c71d8")  # (Adwaita's blue, as links)
        for ttype, tag in self.token_tags.items():
            self._style_tag(tag, ttype)

    def _style_tag(self, tag, ttype):
        fg, bold, italic = code_style(ttype, self.dark)
        tag.set_property("foreground", fg)
        tag.set_property("foreground-set", fg is not None)
        tag.set_property("weight", Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
        tag.set_property("style", Pango.Style.ITALIC if italic else Pango.Style.NORMAL)

    def tag_for(self, ttype):
        tag = self.token_tags.get(ttype)
        if tag is None:
            tag = self.token_tags[ttype] = self.buffer.create_tag(None)
            self._style_tag(tag, ttype)
            tag.set_priority(0)  # under the marks
        return tag

    def load(self, text, path, line=None, shade=None):
        """Show text (path picks the language), mark line and shade the (start, end) lines; highlight in
        the background."""
        self.generation += 1
        gen, self.path, self.highlighted = self.generation, path, 0
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.links = []  # (the text they were in is gone)
        self.buffer.set_text(text)
        self.gutter.queue_resize()
        self.mark(line, shade)
        if self.search_bar.get_search_mode():  # (a new file: find the same text in it)
            self.find(self.search_entry.get_text(), scroll=False)
        IMAGE_POOL.submit(lambda: GLib.idle_add(self._spans_ready, gen, code_spans(text, path), line))

    def set_links(self, links, on_link):
        """Make spans of the text links: [(line, column, length, target)] (0-based), drawn as links are; a click on
        one that selects nothing calls on_link(target). The text stays selectable. The next load clears them."""
        b, self.links, self.on_link = self.buffer, [], on_link
        for line, col, length, target in links:
            ok, start = b.get_iter_at_line_offset(line, col)
            if not ok:
                continue
            end = start.copy()
            end.forward_chars(length)
            b.apply_tag(self.link_tag, start, end)
            self.links.append((start.get_offset(), end.get_offset(), target))

    def _link_cursor(self, x, y):
        """Over a link, the hand; else the I-beam."""
        pointers.set_pointer(self.view, pointers.HAND if self.link_at(x, y) is not None else pointers.TEXT)

    def link_at(self, x, y):
        """The target of the link at a point in the view (widget coordinates), or None."""
        if not self.links:
            return None
        bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        ok, it = self.view.get_iter_at_location(bx, by)
        if not ok:
            return None
        at = it.get_offset()
        return next((t for a, z, t in self.links if a <= at < z), None)

    def _link_clicked(self, _gesture, _n, x, y):
        if self.buffer.get_has_selection() or self.on_link is None:  # (text being selected: not a click on a link)
            return
        if (target := self.link_at(x, y)) is not None:
            self.on_link(target)

    def _spans_ready(self, gen, spans, line):
        if gen != self.generation:
            return False
        if line:  # the part around the marked line first, then the rest
            start = self.buffer.get_iter_at_line(max(0, line - 150))[1].get_offset()
            end = self.buffer.get_iter_at_line(line + 150)[1].get_offset()
            spans = [s for s in spans if start <= s[0] < end] + [s for s in spans if not start <= s[0] < end]
        self._apply(gen, spans, 0)
        return False

    def _apply(self, gen, spans, i):
        if gen != self.generation:
            return False
        it, end_it = self.buffer.get_start_iter(), self.buffer.get_start_iter()
        for start, end, ttype in spans[i:i + self.CHUNK]:
            it.set_offset(start)
            end_it.set_offset(end)
            self.buffer.apply_tag(self.tag_for(ttype), it, end_it)
        self.highlighted = min(len(spans), i + self.CHUNK)
        if self.highlighted < len(spans):
            GLib.idle_add(self._apply, gen, spans, self.highlighted)
        return False

    def mark(self, line=None, shade=None):
        """Mark a line (1-based) and shade a range of lines; scroll so the line is a third of the way down."""
        b = self.buffer
        b.remove_tag(self.line_tag, b.get_start_iter(), b.get_end_iter())
        b.remove_tag(self.range_tag, b.get_start_iter(), b.get_end_iter())
        if shade:
            s, e = b.get_iter_at_line(shade[0] - 1)[1], b.get_iter_at_line(shade[1])[1]
            if shade[1] >= b.get_line_count():
                e = b.get_end_iter()
            b.apply_tag(self.range_tag, s, e)
        self.gutter.marked = line
        self.gutter.queue_draw()
        if not line:
            return
        it = b.get_iter_at_line(line - 1)[1]
        end = it.copy()
        end.forward_line()
        b.apply_tag(self.line_tag, it, end)
        b.place_cursor(it)
        mark = b.get_mark("jump") or b.create_mark("jump", it, True)
        b.move_mark(mark, it)
        GLib.idle_add(lambda: self.view.scroll_to_mark(mark, 0.0, True, 0.0, 0.3) and False)

    def marked_line(self):
        return self.gutter.marked

    # ---- wrapping
    def set_wrap(self, on):
        """Wrap long lines at the view's width (at word boundaries where there are some), or scroll sideways."""
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if on else Gtk.WrapMode.NONE)
        self.gutter.queue_draw()

    # ---- text size
    @classmethod
    def set_zoom(cls, zoom, save=True):
        """The code's text size, in every CodeView; the chat's is its own. The line at the top stays there: a
        TextView holds its first line in place while the lines are laid out again."""
        zoom = round(min(ZOOM_MAX, max(ZOOM_MIN, zoom)), 2)
        if cls.zoom_css is None:
            cls.zoom_css = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), cls.zoom_css,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        elif zoom == cls.zoom:
            return
        cls.zoom = zoom
        if save:
            save_settings(code_zoom=zoom)
        cls.zoom_css.load_from_string(f".code-view-scroll {{ font-size: {zoom:.2f}em; }}")
        for view in list(cls.live):
            # (a hidden widget misses a provider's new rules, and keeps its old size when shown; a class it gets
            # again restyles it: the Kubernetes view's YAML, a Code page not open)
            view.scroll.remove_css_class("code-view-scroll")
            view.scroll.add_css_class("code-view-scroll")
            view.gutter.queue_resize()  # (wider or narrower digits)
            view.emit("zoomed")

    @classmethod
    def zoom_scroll(cls, dy, pixels, time):
        """Ctrl+scroll over code: a wheel's notch is a step. A touchpad's many small deltas add up to steps (50px
        to one, in pixels), from nothing again after a pause or on turning back."""
        total, last = cls.scrolled
        if abs(time - last) > 400 or total * dy < 0:
            total = 0.0
        total += dy / 50 if pixels else dy
        steps = int(round(total, 6))
        cls.scrolled = total - steps, time
        if steps:
            cls.set_zoom(cls.zoom - steps * ZOOM_STEP)


class CodePage(Gtk.Box):
    """The side panel's Code page: a file shown in Baude (CodeView), where it is (module › class › def,
    for Python), the class a function or field belongs to with its members (click one to go to it),
    and Open in editor."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.path, self.text, self.line = win, None, "", None
        head = Gtk.Box(spacing=6, margin_start=10, margin_end=8, margin_top=8, margin_bottom=4)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.START)
        self.title.add_css_class("heading")
        self.where = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.START)
        self.where.add_css_class("dim-label")
        self.where.add_css_class("caption")
        titles.append(self.title)
        titles.append(self.where)
        head.append(titles)
        self.find_btn = Gtk.Button(icon_name="edit-find-symbolic", tooltip_text="Find (Ctrl+F)")
        self.find_btn.add_css_class("flat")
        self.find_btn.connect("clicked", lambda *_: self.code.open_search())
        head.append(self.find_btn)
        self.wrap_btn = Gtk.ToggleButton(icon_name=themed_icon("text-wrap-symbolic", "format-justify-fill-symbolic"),
                                         tooltip_text="Wrap long lines", active=bool(load_settings().get("code_wrap")))
        self.wrap_btn.add_css_class("flat")
        self.wrap_btn.connect("toggled", lambda b: (self.code.set_wrap(b.get_active()),
                                                    save_settings(code_wrap=b.get_active())))
        head.append(self.wrap_btn)
        # the code's text size, apart from the chat's: smaller, the size (click: 1.0 again), larger
        sizes = Gtk.Box()
        self.zoom_out_btn, self.zoom_btn, self.zoom_in_btn = (
            Gtk.Button(icon_name="value-decrease-symbolic", tooltip_text="Smaller text (Ctrl+−, or Ctrl+scroll)"),
            Gtk.Button(label="1.0", tooltip_text="Back to 1.0 (Ctrl+0)"),
            Gtk.Button(icon_name="value-increase-symbolic", tooltip_text="Larger text (Ctrl+plus, or Ctrl+scroll)"))
        for btn, size in ((self.zoom_out_btn, lambda: CodeView.zoom - ZOOM_STEP), (self.zoom_btn, lambda: 1.0),
                          (self.zoom_in_btn, lambda: CodeView.zoom + ZOOM_STEP)):
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_, size=size: CodeView.set_zoom(size()))
            sizes.append(btn)
        self.zoom_btn.add_css_class("code-zoom")
        self.zoom_btn.get_child().set_width_chars(4)  # (the buttons don't shift as it goes from 90% to 100%)
        head.append(sizes)
        files_btn = Gtk.Button(icon_name="folder-symbolic", tooltip_text="Show in Files")
        files_btn.add_css_class("flat")
        files_btn.connect("clicked", lambda *_: self.path and os.path.exists(self.path) and
                          self.win.show_folder(os.path.dirname(self.path), self.path))
        head.append(files_btn)
        self.editor_btn = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Open in your editor")
        self.editor_btn.add_css_class("flat")
        self.editor_btn.connect("clicked", lambda *_: self.path and open_in_editor(self.path, self.line, self))
        head.append(self.editor_btn)
        self.append(head)
        self.crumbs = Gtk.Label(xalign=0, wrap=True, margin_start=10, margin_end=10, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.crumbs.add_css_class("caption")
        self.crumbs.connect("activate-link", self.on_crumb)
        self.append(self.crumbs)
        self.note = Gtk.Label(xalign=0, wrap=True, margin_start=10, margin_end=10, visible=False)
        self.note.add_css_class("caption")
        self.note.add_css_class("warning")
        self.append(self.note)
        self.cls = Gtk.Expander(expanded=True, margin_start=10, margin_end=10, margin_top=4, visible=False)
        self.cls.add_css_class("code-class")
        cls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        self.cls_doc = Gtk.Label(xalign=0, wrap=True, max_width_chars=80, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.cls_doc.add_css_class("caption")
        cls_box.append(self.cls_doc)
        self.members = Adw.WrapBox(child_spacing=2, line_spacing=2) if hasattr(Adw, "WrapBox") else \
            Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, halign=Gtk.Align.START, max_children_per_line=12)
        cls_box.append(self.members)
        self.cls.set_child(cls_box)
        self.append(self.cls)
        self.code = CodeView()
        self.code.set_margin_top(6)
        self.code.set_wrap(self.wrap_btn.get_active())
        self.code.connect("zoomed", lambda *_: self.show_zoom())
        self.show_zoom()
        self.append(self.code)
        self.empty = empty_label("Click a file, function or class in a reply to see its code here.")
        self.append(self.empty)
        self.code.set_visible(False)

    def tab_link(self):
        """Its file, at its line, for an agent to read (MainTabs.copy_link). None for what isn't that file as it is now
        (a version of it, a diff: there's a note to say so) or isn't on the disk."""
        if not self.path or self.note.get_visible() or not os.path.exists(self.path):
            return None
        return f"{os.path.abspath(self.path)}:{self.line}" if self.line else os.path.abspath(self.path)

    def show_zoom(self):
        self.zoom_btn.set_label(fmt_scale(CodeView.zoom))
        self.zoom_out_btn.set_sensitive(CodeView.zoom > ZOOM_MIN)
        self.zoom_in_btn.set_sensitive(CodeView.zoom < ZOOM_MAX)

    def show(self, path, text, line=None, note=None):
        """Show path's text at line; for Python, the class around the line (or the one its signature names)."""
        self.path, self.text, self.line = path, text, line
        self.empty.set_visible(False)
        self.code.set_visible(True)
        root = self.win.signal.root if self.win.signal else self.win.run_cwd
        rel = os.path.relpath(path, root) if root and path.startswith(root + os.sep) else pretty_path(path)
        self.title.set_label(os.path.basename(path) + (f":{line}" if line else ""))
        self.where.set_label(os.path.dirname(rel) or ".")
        self.note.set_label(note or "")
        self.note.set_visible(bool(note))
        crumbs, cls = python_context(text, line) if path.endswith((".py", ".pyi")) and line else ([], None)
        parts = [html.escape(os.path.splitext(os.path.basename(path))[0])] + \
            [f'<a href="line:{n}">{kind} {html.escape(name)}</a>' for kind, name, n in crumbs]
        self.crumbs.set_markup(" › ".join(parts))
        self.crumbs.set_visible(bool(crumbs))
        while (c := self.members.get_first_child()) is not None:
            self.members.remove(c)
            discard(c)
        self.cls.set_visible(cls is not None)
        if cls:
            bases = f"({', '.join(cls['bases'])})" if cls["bases"] else ""
            deco = "".join(f"@{d}  " for d in cls["decorators"])
            label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
            label.set_markup(f'{"Related class" if cls["related"] else "Class"}: <tt>{html.escape(deco)}'
                             f'<b>{html.escape(cls["name"])}</b>{html.escape(bases)}</tt>'
                             f'  <span alpha="60%">lines {cls["start"]}–{cls["end"]}</span>')
            self.cls.set_label_widget(label)
            self.cls_doc.set_label(cls["doc"])
            self.cls_doc.set_visible(bool(cls["doc"]))
            for kind, name, n in [("class", cls["name"], cls["start"])] + cls["members"]:
                btn = Gtk.Button(label=name, tooltip_text=f"{kind} {name}, line {n}")
                btn.add_css_class("flat")
                btn.add_css_class("code-member")
                if n == line or (kind == "def" and any(c[2] == n for c in crumbs)):
                    btn.add_css_class("current")
                btn.connect("clicked", lambda *_, n=n: self.go(n))
                self.members.append(btn)
        self.code.load(text, path, line, (cls["start"], cls["end"]) if cls and not cls["related"] else None)

    def go(self, line):
        """Jump to a line of the file shown (a member, a breadcrumb)."""
        self.line = line
        self.title.set_label(os.path.basename(self.path) + f":{line}")
        self.code.mark(line, self.code_shade())
        return True

    def code_shade(self):
        _crumbs, cls = python_context(self.text, self.line) if self.path.endswith((".py", ".pyi")) else ([], None)
        return (cls["start"], cls["end"]) if cls and not cls["related"] else None

    def on_crumb(self, _label, uri):
        if uri.startswith("line:"):
            self.go(int(uri[5:]))
        return True


class FileNode(GObject.Object):
    """A file or folder on the Files page; ignored (by git) comes in after it's listed (FileIgnores)."""
    ignored = GObject.Property(type=bool, default=False)

    def __init__(self, path, is_dir, ignored=False):
        super().__init__()
        self.path, self.is_dir, self.ignored = path, is_dir, ignored


def git_ignored(folder, names):
    """Which of a folder's entries git ignores (none outside a repository)."""
    if not names:
        return set()
    try:
        p = subprocess.run([git_exe(), "-C", folder, "check-ignore", "--stdin", "-z"],
                           input="".join(n + "\0" for n in names), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {n for n in p.stdout.split("\0") if n}


class FileIgnores:
    """Marks the Files page's listed entries that git ignores, in the background: the folders listed in one go (a
    folder opened, and the folders in it, which GTK lists to know if they open) are asked about in one git call per
    repository, not one each on the main loop."""
    waiting, soon = [], None
    POOL = WorkerPool(1)

    @classmethod
    def add(cls, folder, nodes):
        if nodes:
            cls.waiting.append((folder, nodes))
            if cls.soon is None:
                cls.soon = GLib.idle_add(cls.flush)

    @classmethod
    def flush(cls):
        cls.soon, batch, cls.waiting = None, cls.waiting, []

        def work():
            by_top = {}  # (paths as git gives them: real ones, not through a link like macOS's /var)
            for folder, nodes in batch:
                real = os.path.realpath(folder)
                top = next((t for t in by_top if real == t or real.startswith(t + os.sep)), None) or \
                    (_git(folder, "rev-parse", "--show-toplevel") or "").strip()
                if top:
                    by_top.setdefault(top, []).extend(
                        (os.path.relpath(os.path.join(real, os.path.basename(n.path)), top), n) for n in nodes)
            hits = []
            for top, named in by_top.items():
                ignored = git_ignored(top, [rel for rel, _n in named])
                hits += [n for rel, n in named if rel in ignored]
            if hits:
                GLib.idle_add(lambda: [setattr(n, "ignored", True) for n in hits] and False)
        cls.POOL.submit(work)
        return False


FILE_ICON_KINDS = {"application/pdf": "x-office-document-symbolic", "application/zip": "package-x-generic-symbolic",
                   "application/x-tar": "package-x-generic-symbolic", "application/gzip": "package-x-generic-symbolic",
                   "application/x-7z-compressed": "package-x-generic-symbolic",
                   "application/x-bzip2": "package-x-generic-symbolic", "application/x-xz": "package-x-generic-symbolic"}


def file_icon(name):
    """A file's icon on the Files page (and an attachment's): the theme's generic one for its kind, so every text file
    (code, config, a dotfile) has the same one. A content type's own icon is missing for most files (and on macOS,
    whose types are Apple's, a file it doesn't know, .dockerignore or .toml, gets the binary file's; .ts reads as a
    video), so only images, audio, video, PDFs and archives get another."""
    mime = Gio.content_type_get_mime_type(Gio.content_type_guess(name, None)[0]) or ""
    kind = mime.split("/")[0]
    if kind in ("image", "audio", "video") and mime != "video/mp2t":  # (.ts: TypeScript, far likelier)
        return Gio.ThemedIcon.new(f"{kind}-x-generic-symbolic")
    return Gio.ThemedIcon.new(FILE_ICON_KINDS.get(mime, "text-x-generic-symbolic"))


def file_children(folder):
    """A folder's entries for the Files page: folders first, then files, by name. .git is left out; what git ignores
    is marked a moment later (FileIgnores; it's drawn dimmed)."""
    store = Gio.ListStore(item_type=FileNode)

    def is_dir(e):
        try:
            return e.is_dir()
        except OSError:
            return False
    try:
        entries = [e for e in os.scandir(folder) if e.name != ".git"]
    except OSError:
        return store
    entries.sort(key=lambda e: (not is_dir(e), e.name.lower(), e.name))
    nodes = [FileNode(e.path, is_dir(e)) for e in entries]
    store.splice(0, 0, nodes)
    FileIgnores.add(folder, nodes)
    return store


class FilesPage(Gtk.Box):
    """The side panel's Files page: a folder's tree (the chat's folder, or the repository a folder is in), opened
    down to the folder or file asked for. A folder link opens here, not in your file manager; a file opens on the
    Code page (or in its app, if it isn't text). Right-click a row to copy its path or open it in the file manager."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.root, self.tree, self.selection = win, None, None, None
        head = Gtk.Box(spacing=6, margin_start=10, margin_end=8, margin_top=8, margin_bottom=4)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title.add_css_class("heading")
        self.where = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.START)
        self.where.add_css_class("dim-label")
        self.where.add_css_class("caption")
        titles.append(self.title)
        titles.append(self.where)
        head.append(titles)
        for icon, tip, fn in (("view-refresh-symbolic", "Read the folders again", self.refresh),
                              ("folder-open-symbolic", "Open in your file manager",
                               lambda: self.root and self.launch(self.selected() or self.root))):
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_, fn=fn: fn())
            head.append(btn)
        self.append(head)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup)
        factory.connect("bind", self._bind)
        self.view = Gtk.ListView(factory=factory, single_click_activate=True)
        self.view.add_css_class("navigation-sidebar")
        self.view.add_css_class("file-tree")
        self.view.connect("activate", self._activate)
        self.scroll = Gtk.ScrolledWindow(child=self.view, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(self.scroll)

    def _setup(self, _factory, item):
        exp = Gtk.TreeExpander(indent_for_icon=True)
        box = Gtk.Box(spacing=6)
        box.append(Gtk.Image())
        box.append(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True))
        exp.set_child(box)
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda _g, _n, x, y: self.menu(exp, x, y))
        exp.add_controller(click)
        item.set_child(exp)

    def _bind(self, _factory, item):
        row, exp = item.get_item(), item.get_child()
        node = exp.node = row.get_item()
        exp.set_list_row(row)
        icon, label = exp.get_child().get_first_child(), exp.get_child().get_last_child()
        name = os.path.basename(node.path)
        icon.set_from_gicon(Gio.ThemedIcon.new("folder-symbolic") if node.is_dir else file_icon(name))
        label.set_label(name)
        if getattr(exp, "dim", None) is not None:
            exp.dim.unbind()
        exp.dim = node.bind_property("ignored", exp, "opacity", GObject.BindingFlags.SYNC_CREATE,
                                     lambda _b, ignored: 0.55 if ignored else 1.0)
        exp.set_tooltip_text(pretty_path(node.path))

    def _activate(self, _view, pos):
        row = self.tree.get_row(pos)
        node = row.get_item()
        if node.is_dir:
            row.set_expanded(not row.get_expanded())
        else:
            open_code(node.path, None, self.win)

    def set_root(self, root, expanded=()):
        """Show root's tree, with the folders in expanded opened again."""
        self.root = root
        self.tree = Gtk.TreeListModel.new(file_children(root), False, False,
                                          lambda n: file_children(n.path) if n.is_dir else None)
        self.selection = Gtk.SingleSelection(model=self.tree, autoselect=False, can_unselect=True)
        self.view.set_model(self.selection)
        self.title.set_label(os.path.basename(root.rstrip(os.sep)) or root)
        self.where.set_label(pretty_path(os.path.dirname(root.rstrip(os.sep))))
        for path in expanded:  # (in the tree's order: a folder's parent is open by the time it's reached)
            if (pos := self.find(path)) is not None:
                self.tree.get_row(pos).set_expanded(True)

    def ensure(self):
        """Something to show when the page is opened: the folder the chat works in, the first time."""
        if self.root is None and (root := (self.win.signal.root if self.win.signal else None) or self.win.run_cwd):
            self.set_root(root)

    def refresh(self):
        """Read the folders again (as they are now), keeping the open ones open and the selection."""
        if self.root is None:
            return
        picked = self.selected()
        self.set_root(self.root, [self.tree.get_row(i).get_item().path for i in range(self.tree.get_n_items())
                                  if self.tree.get_row(i).get_expanded()])
        if picked and (pos := self.find(picked)) is not None:
            self.selection.set_selected(pos)

    def root_for(self, path):
        """The tree a path is shown in: the one showing, the chat's repository or folder if it's in them, else its
        own repository, else its folder."""
        for root in (self.root, self.win.signal.root if self.win.signal else None, self.win.run_cwd, self.win.cwd):
            if root and (path == root or path.startswith(root.rstrip(os.sep) + os.sep)):
                return root
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        return (_git(folder, "rev-parse", "--show-toplevel") or "").strip() or folder

    def find(self, path):
        for i in range(self.tree.get_n_items() if self.tree else 0):
            if self.tree.get_row(i).get_item().path == path:
                return i
        return None

    def selected(self):
        item = self.selection.get_selected_item() if self.selection else None
        return item.get_item().path if item is not None else None

    def reveal(self, path, select=None):
        """Open the tree down to path (a folder is opened too), with select (or path) selected and scrolled to."""
        path = os.path.abspath(path).rstrip(os.sep) or os.sep
        root = self.root_for(path)
        if root != self.root:
            self.set_root(root)
        target = os.path.abspath(select) if select else path
        rel = os.path.relpath(target, root)
        cur, pos = root, None
        for part in [] if rel == "." else rel.split(os.sep):
            cur = os.path.join(cur, part)
            if (pos := self.find(cur)) is None:
                break
            if cur != target or (cur == path and os.path.isdir(cur)):
                self.tree.get_row(pos).set_expanded(True)
        if pos is not None:
            self.selection.set_selected(pos)
            self.view.scroll_to(pos, Gtk.ListScrollFlags.NONE, None)
        return pos

    def launch(self, path):
        f = Gio.File.new_for_path(path)
        launcher = Gtk.FileLauncher(file=f)
        if os.path.isdir(path):
            launcher.launch(self.get_root(), None, None)
        else:
            launcher.open_containing_folder(self.get_root(), None, None)

    def menu(self, exp, x, y):
        node = getattr(exp, "node", None)
        if node is None:
            return
        pop = Gtk.Popover(has_arrow=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for label, fn in (("Copy path", lambda: (self.get_clipboard().set(node.path), toast(self, "Path copied"))),
                          ("Open in your file manager", lambda: self.launch(node.path))):
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.get_child().set_xalign(0)
            btn.connect("clicked", lambda *_, fn=fn: (pop.popdown(), fn()))
            box.append(btn)
        pop.set_child(box)
        pop.set_parent(exp)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()
