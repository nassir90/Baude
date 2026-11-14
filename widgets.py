"""GTK pieces Baude and Record both use (Record imports this, never baude.py), and the viewers (kubeview, schemaview, gitview, codeview) too: markdown, links, images, code, settings; the links' logos, drawn as the
vectors they are in the text's colour (draw_ref_icon, RefIcon), at the icons' size (scales: Baude's Settings → Size
sets it, as baude's _scales, the same dict)."""
import argparse
import array
import base64
import bisect
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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "vendor"))  # (Pygments: vendor/README.md)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402,F401

import lookup  # noqa: E402,F401
from lookup import (  # noqa: E402,F401  (⌘K's engine: what a folder has)
    _CATALOGUE_TOKEN, _KUBE_NAME, _kube_args, catalogue_shaped, find_spans, ref_icon,
    ref_key,
)
from macos import ns_string, objc_send, set_window_frame, window_frame  # noqa: E402,F401
import pointers  # noqa: E402
import fontsetup  # noqa: E402  (the code font, and its size beside the prose)
from baudelib.transcripts import ref_epoch, typed_at  # noqa: E402,F401  (Record and the viewers' too)


# the sizes for what's drawn here: the text's, and an icon's in px (16 at 1.0); Baude's Settings → Size sets them
scales = {"dpi": None, "icons": None, "icons_css": "", "text": 1.0, "icon_px": 16}
_scales = scales


# A Signal link's logo, drawn just before it (LinkLabel): what it links to at a glance (git's, Kubernetes' helm,
# a database, a graph for ArangoDB, a file…), in the text's own colour. 16×16, drawn at the text's size.
_REF_WHEEL = "".join(  # (Kubernetes' helm: a heptagon, its wheel cut out of it)
    f"M{8 + 1.15 * math.cos(a):.2f} {8 + 1.15 * math.sin(a):.2f}L{8 + 5.1 * math.cos(a):.2f} {8 + 5.1 * math.sin(a):.2f}"
    for a in (-math.pi / 2 + 2 * math.pi * k / 7 for k in range(7)))
_REF_HEPTAGON = "M" + "L".join(f"{8 + 7.6 * math.cos(a):.2f} {8 + 7.6 * math.sin(a):.2f}"
                               for a in (-math.pi / 2 + 2 * math.pi * k / 7 for k in range(7))) + "Z"
_REF_STROKE = 'fill="none" stroke="{c}" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"'
REF_ICON_SVG = {
    "git": ('<defs><mask id="m"><rect width="16" height="16" fill="#fff"/><path d="M6.3 4.4v7.2M6.6 4.7l3.4 3.4" '
            'stroke="#000" stroke-width="1.3"/><circle cx="6.3" cy="4.4" r="1.35"/><circle cx="6.3" cy="11.6" '
            'r="1.35"/><circle cx="10.2" cy="8.3" r="1.35"/></mask></defs>'
            '<path d="M8 .5 15.5 8 8 15.5.5 8z" fill="{c}" mask="url(#m)"/>'),
    "k8s": (f'<defs><mask id="m"><rect width="16" height="16" fill="#fff"/><circle cx="8" cy="8" r="3.1" fill="none" '
            f'stroke="#000" stroke-width="1.25"/><path d="{_REF_WHEEL}" stroke="#000" stroke-width="1.15" '
            f'stroke-linecap="round"/><circle cx="8" cy="8" r="1.05"/></mask></defs>'
            f'<path d="{_REF_HEPTAGON}" fill="{{c}}" mask="url(#m)"/>'),
    "db": ('<defs><mask id="m"><rect width="16" height="16" fill="#fff"/><path d="M2.5 6.2c0 1.3 2.5 2.3 5.5 2.3s5.5-1 '
           '5.5-2.3M2.5 10c0 1.3 2.5 2.3 5.5 2.3s5.5-1 5.5-2.3" stroke="#000" stroke-width="1.1" fill="none"/></mask>'
           '</defs><path d="M2.5 3.5C2.5 2.2 5 1.2 8 1.2s5.5 1 5.5 2.3v9c0 1.3-2.5 2.3-5.5 2.3s-5.5-1-5.5-2.3z" '
           'fill="{c}" mask="url(#m)"/>'),
    "arango": (f'<path d="M4.3 4.6 11.6 6.4M4.3 4.6l2.4 7.3M11.6 6.4l-4.9 5.5" {_REF_STROKE}/><circle cx="4.3" '
               'cy="4.6" r="2.3" fill="{c}"/><circle cx="11.6" cy="6.4" r="2.3" fill="{c}"/><circle cx="6.7" '
               'cy="11.9" r="2.3" fill="{c}"/>'),  # (a graph: ArangoDB's collections)
    "file": (f'<path d="M3.5 1.5h5.8l3.2 3.2v9.8h-9zM9 1.5V5h3.5" {_REF_STROKE}/>'),
    "folder": (f'<path d="M1.5 3.5h4.5l1.5 1.5h7v8.5h-13z" {_REF_STROKE}/>'),
    "page": (f'<path d="M3.5 1.5h9v13h-9zM5.8 5h4.4M5.8 8h4.4M5.8 11h2.6" {_REF_STROKE}/>'),  # (Record's notes)
    "notebook": (f'<path d="M3.5 1.5h8.2a.8.8 0 0 1 .8.8v11.4a.8.8 0 0 1-.8.8H3.5zM5.6 1.5v13M7.8 5h2.6" '
                 f'{_REF_STROKE}/>'),
    "code": ('<path d="M6 2.5c-1.5 0-2 .6-2 2v1.6c0 .9-.5 1.4-1.5 1.4.9 0 1.5.5 1.5 1.4v1.6c0 1.4.5 2 2 2M10 2.5c1.5 0 2 '
             '.6 2 2v1.6c0 .9.5 1.4 1.5 1.4-1 0-1.5.5-1.5 1.4v1.6c0 1.4-.5 2-2 2" fill="none" stroke="{c}" '
             'stroke-width="1.6" stroke-linecap="round"/>'),
    "env": ('<path d="M10.8 4.6C10.3 3.6 9.3 3 8 3 6.4 3 5.2 3.9 5.2 5.2c0 3 5.8 1.7 5.8 4.9 0 1.4-1.3 2.4-3 2.4-1.4 '
            '0-2.6-.7-3.1-1.8M8 1.3v13.4" fill="none" stroke="{c}" stroke-width="1.5" stroke-linecap="round"/>'),
    "route": (f'<circle cx="8" cy="8" r="6.2" {_REF_STROKE}/><path d="M1.8 8h12.4M8 1.8c-3.3 3.5-3.3 8.9 0 '
              f'12.4M8 1.8c3.3 3.5 3.3 8.9 0 12.4" {_REF_STROKE}/>'),
    "config": (f'<path d="M2 4h12M2 8h12M2 12h12" {_REF_STROKE}/><circle cx="5" cy="4" r="1.8" fill="{{c}}"/>'
               '<circle cx="10.5" cy="8" r="1.8" fill="{c}"/><circle cx="6.5" cy="12" r="1.8" fill="{c}"/>'),
    "cli": (f'<rect x="1.5" y="2.5" width="13" height="11" rx="2" {_REF_STROKE}/><path d="M4.5 6.5 7 8.5l-2.5 2M8.5 '
            f'11h3" {_REF_STROKE}/>'),
    "package": (f'<path d="M8 1.5 14 4.7v6.6L8 14.5 2 11.3V4.7zM2 4.7 8 8l6-3.3M8 8v6.5" {_REF_STROKE}/>'),
    "service": ('<rect x="1.5" y="7" width="13" height="7" rx="1.8" fill="{c}"/><path d="M3 3.8h3v2.4H3zM6.6 3.8h3v2.4h-3z'
                'M6.6.9h3v2.4h-3z" fill="{c}"/>'),
    "issue": ('<path d="M6.4 2 4.8 14M11.2 2 9.6 14M2.6 5.7h11.4M2 10.3h11.4" fill="none" stroke="{c}" '
              'stroke-width="1.6" stroke-linecap="round"/>'),  # (GitHub's: the # it goes by)
    "claude": ('<path d="M8 1.5v13M2.4 4.8l11.2 6.4M2.4 11.2l11.2-6.4" fill="none" stroke="{c}" stroke-width="1.9" '
               'stroke-linecap="round"/>'),
    "external": (f'<path d="M9 2.5h4.5V7M13.5 2.5 7 9M11 9.5v3.5H3V5h3.5" {_REF_STROKE}/>'),
}
# A pull request has a branch's logo, an issue its #, in a colour of their own for where they stand (lookup.github_icon),
# light or dark: green while it's open, purple once merged, grey if it came to nothing (closed unmerged, or an issue
# closed: an open issue is the text's colour, as most logos are).
REF_ICON_SVG.update(pr_open=REF_ICON_SVG["git"], pr_merged=REF_ICON_SVG["git"], pr_closed=REF_ICON_SVG["git"],
                    issue_closed=REF_ICON_SVG["issue"])
REF_ICON_TINTS = {"pr_open": "#2da44e", "pr_merged": "#8957e5", "pr_closed": "#8a8f98", "issue_closed": "#8a8f98"}
# git's merge (two branches' ends, one bending into the other): a pull request's pill has it before its number
REF_ICON_SVG["merge"] = (f'<circle cx="4.6" cy="3.3" r="1.85" {_REF_STROKE}/><circle cx="4.6" cy="12.7" r="1.85" '
                         f'{_REF_STROKE}/><circle cx="11.6" cy="9" r="1.85" {_REF_STROKE}/><path d="M4.6 5.2v5.6M4.6 '
                         f'5.2c0 2.3 1.6 3.8 5.1 3.8" {_REF_STROKE}/>')
# A pull request's pill, in a reply and on the Git page, is in the colour of where it stands (lookup.pull_state), the
# same light or dark (each reads on either), its patch that colour faint: green while it's open, grey while it's a
# draft, red once a review asks for changes (whatever else it is: it's held up), purple once merged; closed without
# being merged, the same grey fainter and struck through. One not known yet is a plain link on a patch of the text's
# colour, and only takes its own when it's known: nothing moves.
PR_COLORS = {"open": "#2da44e", "draft": "#8a8f98", "changes": "#e5484d", "merged": "#8957e5", "closed": "#8a8f98"}
PR_SAID = {"open": "Open", "draft": "Draft", "changes": "Changes requested", "merged": "Merged",
           "closed": "Closed without being merged"}


_REF_ICON_OPS = {}


def ref_icon_ops(icon):
    """A logo (REF_ICON_SVG) as what GSK draws, parsed once: [(Gsk.Path, fill or None, Gsk.Stroke or None, stroke or
    None, mask ops or None)], the colours "c" (the text's), "#fff" or "#000". The SVG they use: path, circle and rect,
    filled or stroked (width, caps, joins), and a mask (white shows, black cuts) for a cut-out."""
    if icon in _REF_ICON_OPS:
        return _REF_ICON_OPS[icon]
    import xml.etree.ElementTree as ET
    root = ET.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg">{REF_ICON_SVG[icon].replace("{c}", "c")}</svg>')
    ns = "{http://www.w3.org/2000/svg}"
    caps = {"round": Gsk.LineCap.ROUND, "square": Gsk.LineCap.SQUARE}
    joins = {"round": Gsk.LineJoin.ROUND, "bevel": Gsk.LineJoin.BEVEL}

    def num(e, k, default=0.0):
        return float(e.get(k, default))

    def ops(parent):
        out = []
        for e in parent:
            tag = e.tag.replace(ns, "")
            if tag == "path":
                path = Gsk.Path.parse(e.get("d", ""))
            elif tag in ("circle", "rect"):
                b = Gsk.PathBuilder.new()
                if tag == "circle":
                    b.add_circle(Graphene.Point().init(num(e, "cx"), num(e, "cy")), num(e, "r"))
                else:
                    rect = Graphene.Rect().init(num(e, "x"), num(e, "y"), num(e, "width"), num(e, "height"))
                    if num(e, "rx"):
                        rr = Gsk.RoundedRect()
                        rr.init_from_rect(rect, num(e, "rx"))
                        b.add_rounded_rect(rr)
                    else:
                        b.add_rect(rect)
                path = b.to_path()
            else:
                continue
            fill = e.get("fill", "#000")
            stroke = None
            if e.get("stroke") not in (None, "none"):
                stroke = Gsk.Stroke.new(num(e, "stroke-width", 1.0))
                stroke.set_line_cap(caps.get(e.get("stroke-linecap"), Gsk.LineCap.BUTT))
                stroke.set_line_join(joins.get(e.get("stroke-linejoin"), Gsk.LineJoin.MITER))
            mask = None
            if (ref := e.get("mask", "")).startswith("url(#"):
                found = root.find(f".//{ns}mask[@id='{ref[5:-1]}']")
                mask = ops(found) if found is not None else None
            out.append((path, None if fill == "none" else fill, stroke, e.get("stroke"), mask))
        return out
    _REF_ICON_OPS[icon] = ops(e for e in root if e.tag.replace(ns, "") != "defs")
    return _REF_ICON_OPS[icon]


def draw_ref_icon(snapshot, icon, x, y, size, rgba):
    """A logo drawn as the vectors it is (sharp at any scale, zoomed in too), size logical pixels square at (x, y), in
    rgba (the text's colour), or its own if it has one (REF_ICON_TINTS)."""
    white, black = Gdk.RGBA(), Gdk.RGBA()
    white.parse("#fff")
    black.parse("#000")
    if icon in REF_ICON_TINTS:
        rgba = Gdk.RGBA()
        rgba.parse(REF_ICON_TINTS[icon])
    colors = {"c": rgba, "#fff": white, "#ffffff": white, "#000": black, "#000000": black}

    def paint(ops):
        for path, fill, stroke, stroke_color, mask in ops:
            if mask is not None:  # (white shows the drawing, black cuts it away)
                snapshot.push_mask(Gsk.MaskMode.LUMINANCE)
                paint(mask)
                snapshot.pop()
            if fill:
                snapshot.append_fill(path, Gsk.FillRule.WINDING, colors.get(fill, rgba))
            if stroke is not None:
                snapshot.append_stroke(path, stroke, colors.get(stroke_color, rgba))
            if mask is not None:
                snapshot.pop()
    snapshot.save()
    snapshot.translate(Graphene.Point().init(x, y))
    snapshot.scale(size / 16, size / 16)
    paint(ref_icon_ops(icon))
    snapshot.restore()


class RefIcon(Gtk.Widget):
    """A link's logo on its own (REF_ICON_SVG, drawn as vectors in the text's colour), an icon's size square (16px at
    1.0: Settings → Size)."""

    live = weakref.WeakSet()  # (resized when the icons' size changes: see apply_scales)

    def __init__(self, icon=None):
        super().__init__(valign=Gtk.Align.CENTER)
        self.icon = icon
        RefIcon.live.add(self)

    def do_measure(self, orientation, for_size):
        return scales["icon_px"], scales["icon_px"], -1, -1

    def do_snapshot(self, snapshot):
        if self.icon:
            draw_ref_icon(snapshot, self.icon, 0, 0, min(self.get_width(), self.get_height()), self.get_color())


class PickList:
    """A list to pick from as you type, the focus left in the text: Record's @ and # in its notes, Baude's / in its
    composer. A row's a logo (RefIcon), a name and a dimmed line about it; under them, how many there are. Rows are made
    as they're shown (a folder has thousands): bind(i, icon, name, about) says what row i is. ↑ ↓ Page Up and Page Down
    move in it (move); Enter or Tab, and a click, take what's picked (the caller's on_pick)."""

    WIDTH, HEIGHT = 520, 360  # px: past that height it scrolls
    STEPS = {Gdk.KEY_Up: -1, Gdk.KEY_Down: 1, Gdk.KEY_Page_Up: -10, Gdk.KEY_Page_Down: 10}

    def __init__(self, parent, bind, on_pick, position=Gtk.PositionType.BOTTOM,
                 about_ellipsis=Pango.EllipsizeMode.MIDDLE):
        self.bind, self.about_ellipsis = bind, about_ellipsis
        self.pop = Gtk.Popover(autohide=False, has_arrow=False, position=position, can_focus=False)
        self.pop.add_css_class("ref-pop")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.model = Gtk.StringList()  # (its strings: the rows' numbers)
        self.sel = Gtk.SingleSelection(model=self.model, autoselect=False, can_unselect=True)
        rows = Gtk.SignalListItemFactory()
        rows.connect("setup", lambda _f, li: li.set_child(self._row()))
        rows.connect("bind", lambda _f, li: self._bind(li))
        self.list = Gtk.ListView(model=self.sel, factory=rows, single_click_activate=True, can_focus=False)
        self.list.connect("activate", lambda _l, i: (self.sel.set_selected(i), on_pick()))
        self.scroll = Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         propagate_natural_height=True, max_content_height=self.HEIGHT,
                                         width_request=self.WIDTH)
        self.note = Gtk.Label(xalign=0, css_classes=["dim-label", "caption"])
        box.append(self.scroll)
        box.append(self.note)
        self.pop.set_child(box)
        self.pop.set_parent(parent)
        # (the pointer picks a row only once it's moved over the list: resting where the list came up as you typed, it
        # picked the row that came under it, and Enter took that, not the first)
        self.rest = None  # (where the pointer was as the list came up, in the window: the list moves as it's filled)
        self.list.set_can_target(False)
        moves = Gtk.EventControllerMotion()
        moves.connect("enter", self._pointer_moved)
        moves.connect("motion", self._pointer_moved)
        self.pop.add_controller(moves)
        self.pop.connect("closed", lambda *_: (self.list.set_can_target(False), setattr(self, "rest", None)))

    def _pointer_moved(self, _c, x, y):
        surface = self.pop.get_surface()
        at = (x + surface.get_position_x(), y + surface.get_position_y()) if surface is not None else (x, y)
        if self.rest is None:
            self.rest = at
        elif abs(at[0] - self.rest[0]) + abs(at[1] - self.rest[1]) > 2:
            self.list.set_can_target(True)

    def _row(self):
        line = Gtk.Box(spacing=8, margin_start=4, margin_end=4, margin_top=2, margin_bottom=2)
        line.append(RefIcon())
        name = Gtk.Label(xalign=0, css_classes=["heading"], ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=36)
        line.append(name)
        line.append(Gtk.Label(xalign=0, css_classes=["dim-label", "caption"], ellipsize=self.about_ellipsis,
                              hexpand=True))
        return line

    def _bind(self, li):
        icon = li.get_child().get_first_child()
        name = icon.get_next_sibling()
        self.bind(int(li.get_item().get_string()), icon, name, name.get_next_sibling())
        icon.queue_draw()

    def show(self, n, at, note, pointing_to=None):
        """n rows, row at picked (None: none), note under them; up, if it wasn't, pointing at pointing_to (a
        Gdk.Rectangle in the parent's coordinates)."""
        self.model.splice(0, self.model.get_n_items(), [str(i) for i in range(n)])
        self.list.set_can_target(False)  # (other rows under the pointer: none of them picked till it moves again)
        self.rest = None
        # (a few, as tall as they are: a scrollbar's least height, 58px, is more than two rows', and showed one name
        # with an empty row under it; more than HEIGHT holds even at twice the size, scrolled)
        self.scroll.set_policy(Gtk.PolicyType.NEVER,
                               Gtk.PolicyType.NEVER if n * 48 <= self.HEIGHT else Gtk.PolicyType.AUTOMATIC)
        self.sel.set_selected(Gtk.INVALID_LIST_POSITION if at is None or not n else at)
        if n:
            self.list.scroll_to(at or 0, Gtk.ListScrollFlags.NONE, None)
        self.scroll.set_visible(bool(n))
        self.note.set_text(note)
        if pointing_to is not None:
            self.pop.set_pointing_to(pointing_to)
        if not self.pop.get_visible():
            self.pop.popup()

    @staticmethod
    def over(view, offset, box):
        """Where a list over box points, as show takes it: its left side on view's character at offset (the / or @
        that started it), box's height under it. Past the screen's edge, GTK slides it back on."""
        caret = view.get_cursor_locations(view.get_buffer().get_iter_at_offset(offset))[0]
        x, _y = view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, caret.x, caret.y)
        ok, at = view.compute_point(box, Graphene.Point().init(x, 0))
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = round(at.x) if ok else 0, 0, 1, box.get_height()
        return rect

    def picked(self):
        """The picked row's number, or None."""
        i = self.sel.get_selected()
        return i if i != Gtk.INVALID_LIST_POSITION and i < self.model.get_n_items() else None

    def move(self, step):
        """The pick moved step rows (nothing picked: the first), kept in sight; it stops at either end."""
        if n := self.model.get_n_items():
            i = self.sel.get_selected()
            i = 0 if i == Gtk.INVALID_LIST_POSITION else max(0, min(n - 1, i + step))
            self.sel.set_selected(i)
            self.list.scroll_to(i, Gtk.ListScrollFlags.NONE, None)

    def visible(self):
        return self.pop.get_visible()

    def hide(self):
        if self.pop.get_visible():
            self.pop.popdown()


# Text zoom (Ctrl +/-/0, and ⌘ +/-/0 on macOS, where GTK calls Command Meta) applies to widgets with the .zoomable
# class, on top of the app's text size (apply_scales). 0 goes back to the zoom Settings… keeps ("zoom_default").
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.6, 2.5, 0.1


# --------------------------------------------------------------- settings --

def _settings_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "baude", "settings.json")


SETTINGS_RENAMED = {"referencer": "signal", "referencer_agent": "signal_reader", "referencer_agent_model": "signal_model",
                    "referencer_agent_budget": "signal_budget", "referencer_agent_context": "signal_context",
                    "referencer_agent_effort": "signal_effort", "referencer_evidence": "signal_evidence",
                    "referencer_lsp": "signal_lsp", "referencer_tool_schemas": "signal_tool_schemas",
                    "referencer_mcp_schemas": "signal_mcp_schemas"}  # (Signal was Referencer)


def load_settings():
    try:
        with open(_settings_path()) as f:
            settings = json.load(f)
    except (OSError, ValueError):
        return {}
    for old, new in SETTINGS_RENAMED.items():
        if old in settings and new not in settings:
            settings[new] = settings[old]
    return settings


def save_settings(**changes):
    """changes kept in settings.json, written whole beside it and put in its place, so another app reading it as it's
    written (watch_settings) never reads half of it. Made new (Baude's or Record's first save), full-width text is on."""
    fresh = {} if os.path.exists(_settings_path()) else {"full_width": True}
    settings = {**fresh, **load_settings(), **changes}
    path, written = os.path.realpath(_settings_path()), None  # (a link to it kept a link)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=os.path.dirname(path), prefix=".settings-", suffix=".json",
                                         delete=False) as f:
            written = f.name
            json.dump(settings, f, indent=2)
        os.replace(written, path)
    except OSError:
        if written is not None and os.path.exists(written):
            try:
                os.remove(written)
            except OSError:
                pass
    if _watch["seen"] is not None:  # (this process's own change: no news to it)
        _watch["seen"].update({k: v for k, v in changes.items() if k in SHARED_SETTINGS})


# The settings Baude and Record share, in settings.json (this machine's: they never go to another host). Changed in
# one app, every other one running here takes it as it's written (watch_settings): the accent, the text's, the icons'
# and the Global Scale, Auto restart on update and the code font (each read where it's used, the font at the next
# start), and Meta's model, provider, effort and Code mode. A window's own zoom (Ctrl/⌘ + − 0) stays its own.
SHARED_SETTINGS = ("accent", "text_scale", "icon_scale", "zoom_default", "auto_update", "code_font",
                   "meta_model", "meta_provider", "meta_effort", "meta_code_mode")
SETTINGS_WAIT_MS = 100  # (a write's events, and the file read once they've stopped)
_watch = {"monitor": None, "seen": None, "listeners": [], "wait": None}
_following = weakref.WeakSet()  # (Settings rows up that show a shared setting: follow_setting)


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


def fmt_scale(scale):
    """1.0, 1.2, 1.25: a size or a zoom as it's shown (Settings' rows show one decimal)."""
    text = f"{scale:.2f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


# ---------------------------------------------------------------- markdown --

# ------------------------------------------------------------- file links --
# Paths in Claude's text ("baude.py:42", `tests/run.sh`, /etc/hosts, ~/notes.md) become links when
# they exist, relative to the session folder. Clicking one opens it in the user's editor.

FILE_SCHEME = "baude-open:"


_PATH = re.compile(r"(?<![\w/.:~@-])("
                   r"(?:~|\.{1,2})?/(?:[\w.@+-]+/)*[\w.@+-]*"           # /abs, ~/x, ./x, ../x (files or folders)
                   r"|(?:[\w.@+-]+/)*[\w@+-][\w.@+-]*\.[A-Za-z0-9]{1,8}"  # relative, with an extension
                   r"|(?:[\w.@+-]+/)+(?:[\w@+-][\w.@+-]*)?"  # relative, in a folder: services/x/Dockerfile, a folder
                   r")(?::(\d+)(?::\d+)?)?(?![\w/])")


_exists_cache = {}  # (base, path) -> (checked at, exists)


EXISTS_FOR = 2  # seconds a path's answer is used for before it's looked at again


_checked_since = float("inf")  # the oldest answer about a path used since _inline started on a text (see _inline)


def resolve_path(text):
    """(absolute path, line) if text names an existing file or folder, else None."""
    global _checked_since
    m = _PATH.fullmatch(text.strip())
    if not m or len(m.group(1)) < 3:
        return None
    raw, line = m.group(1), int(m.group(2)) if m.group(2) else None
    path = os.path.expanduser(raw)
    if not os.path.isabs(path):
        path = os.path.join(MarkdownView.base_dir, path)
    path = os.path.normpath(path)
    key = (MarkdownView.base_dir, path)
    when, ok = _exists_cache.get(key, (0, False))
    if time.monotonic() - when > EXISTS_FOR:  # streaming re-renders often; don't stat every frame
        ok = os.path.exists(path)
        if len(_exists_cache) > 4096:  # (every path every chat named: only the last couple of seconds' are used)
            _exists_cache.clear()
        when = time.monotonic()
        _exists_cache[key] = (when, ok)
    _checked_since = min(_checked_since, when)
    return (path, line) if ok else None


def resolve_link(url):
    """(absolute path, line) for a [link](target) whose target is a file or folder, relative to the chat's folder
    (a #L12 or :12 after it is the line), else None. A link says it's a path: unlike one in prose, it needs no
    extension or folder (Dockerfile, Makefile)."""
    if re.match(r"[a-z][a-z0-9+.-]*:", url, re.I) and not url.startswith("file:"):
        return None  # (a URL)
    m = re.fullmatch(r"(?:file://)?(.+?)(?:#L?(\d+)(?:-L?\d+)?|:(\d+)(?::\d+)?)?", url)
    if not m:
        return None
    path = os.path.normpath(os.path.join(MarkdownView.base_dir, os.path.expanduser(urllib.parse.unquote(m[1]))))
    line = m[2] or m[3]
    return (path, int(line) if line else None) if os.path.exists(path) else None


def file_href(path, line=None):
    return FILE_SCHEME + urllib.parse.quote(path) + (f"#{line}" if line else "")


def link_paths(escaped_text):
    """Wrap existing paths in plain (already escaped) text in editor links."""
    def sub(m):
        hit = resolve_path(html.unescape(m.group(0)))
        if not hit:
            return m.group(0)
        return f'<a href="{html.escape(file_href(*hit))}" title="{html.escape(hit[0])}">{m.group(0)}</a>'
    return _PATH.sub(sub, escaped_text)


def editor_command(path, line=None):
    """How to open path (at line) in the user's editor: settings.json's "editor" (a template
    with {path} and {line}), else $VISUAL / $EDITOR if graphical, else VS Code if installed
    (it can jump to a line). None means: the desktop's default app for the file."""
    template = load_settings().get("editor")
    if template:
        return [a.replace("{path}", path).replace("{line}", str(line or 1)) for a in template.split()]
    for var in ("VISUAL", "EDITOR"):
        cmd = os.environ.get(var, "").split()
        if cmd and os.path.basename(cmd[0]) in GUI_EDITORS:
            return editor_args(cmd, path, line)
    if shutil.which("code"):
        return editor_args(["code"], path, line)
    return None


GUI_EDITORS = {"code", "codium", "cursor", "zed", "subl", "gedit", "gnome-text-editor", "kate", "kwrite", "geany"}


def editor_args(cmd, path, line):
    name = os.path.basename(cmd[0])
    if not line:
        return cmd + [path]
    if name in ("code", "codium", "cursor"):
        return cmd + ["-g", f"{path}:{line}"]
    if name in ("zed", "subl"):
        return cmd + [f"{path}:{line}"]
    return cmd + [f"+{line}", path]  # gedit, gnome-text-editor, kate, geany


def open_in_editor(path, line=None, widget=None):
    if os.path.isdir(path):  # folders: the side panel's Files page (the file manager, outside a Baude window)
        win = widget.get_root() if widget is not None else None
        if hasattr(win, "show_folder"):
            win.show_folder(path)
        else:
            Gtk.FileLauncher(file=Gio.File.new_for_path(path)).launch(win, None, None)
        return
    cmd = editor_command(path, line)
    if cmd is None:
        Gtk.FileLauncher(file=Gio.File.new_for_path(path)).launch(widget.get_root() if widget else None, None, None)
        return
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        Gtk.FileLauncher(file=Gio.File.new_for_path(path)).launch(widget.get_root() if widget else None, None, None)


def on_label_link(label, uri):
    """Labels' link handler: editor links open the editor, Signal links what they name;
    everything else, the default."""
    if uri.startswith(REF_SCHEME):
        win = label.get_root()
        if hasattr(win, "open_ref"):
            name, _, message = uri[len(REF_SCHEME):].partition("#")
            win.open_ref(urllib.parse.unquote(name), label, message or None)
        return True
    if not uri.startswith(FILE_SCHEME):
        if (hit := resolve_link(uri)) is not None:  # (a path that wasn't there yet when the reply was drawn)
            open_code(*hit, label)
            return True
        win = label.get_root()  # (a file on the server of a session on the network: Baude's open_remote_link)
        return bool(hasattr(win, "open_remote_link") and win.open_remote_link(uri))
    path, _, line = uri[len(FILE_SCHEME):].partition("#")
    open_code(urllib.parse.unquote(path), int(line) if line.isdigit() else None, label)
    return True


def open_code(path, line=None, widget=None):
    """A file link: shown in Baude (the side panel's Code page), unless settings.json says "code_viewer":
    false or it isn't text to read (a folder, a binary or huge file): then the editor or its app."""
    win = widget.get_root() if widget is not None else None
    if hasattr(win, "show_code") and load_settings().get("code_viewer", True) and win.show_code(path, line):
        return
    open_in_editor(path, line, widget)


ARTIFACT_TITLES = {}  # artifact url -> title, filled in as Claude publishes (see BaudeWindow.register_artifact)


_ARTIFACT_URL = re.compile(r"https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]+")


def link_markup(url, text=None, markup=None):
    """A Pango link. Artifact links read as their title, inline at text size: 'Ops Dashboard ↗'. markup: the text
    as markup already (a [link](…)'s, with its code and bold)."""
    global _checked_since
    inner = markup if markup is not None else html.escape(text or url, quote=False)
    if "://" not in url and (hit := resolve_path(url) or resolve_link(url)):  # [text](relative/or/abs/path)
        return f'<a href="{html.escape(file_href(*hit))}" title="{html.escape(hit[0])}">{inner}</a>'
    href = html.escape(url)
    if _ARTIFACT_URL.fullmatch(url):
        if text and text != url:
            label = inner
        else:  # (a title that may come later: what _inline draws with it isn't kept)
            label, _checked_since = html.escape(ARTIFACT_TITLES.get(url, "Claude artifact"), quote=False), float("-inf")
        return f'<a href="{href}" title="{href}">{label} ↗</a>'
    return f'<a href="{href}">{inner}</a>'


# -------------------------------------------------------------- signal --
# Signal: a second pass over Claude's finished replies that works out where the
# things they name live (files, commits, branches, PRs, env vars, constants, HTTP routes) and keeps the
# answers in <the chat's folder>/.baude/signal.db, so those names become links. Every location keeps an
# anchor (the file's blob hash, its git state, the lines around it), so it can be found again after the code
# changes under it. A name it can't find is shown as not found, with a Refresh to search again.

REF_SCHEME = "baude-ref:"


_REF_METHODS = "GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS"


_REF_MORE_EXT = "lock|txt|log|csv|png|jpe?g|gif|webp|pdf|xml|tpl|j2|mako|dockerfile|env|example|sample|bak"


_REF_EXT = "py|pyi|ts|tsx|js|jsx|mjs|cjs|rs|go|java|kt|rb|sh|bash|ya?ml|toml|json|md|css|scss|html|sql|tf|tsl|tsp|mmd|svg|ini|cfg|conf"


# names worth looking up in plain text (outside backticks): only shapes that are rarely ordinary words
_REF_PLAIN = re.compile(
    r"(?<![\w/.@`$-])(?:"
    r"(?P<symbol>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\(\))"
    rf"|(?P<route>(?:{_REF_METHODS})\s+(?:…|\.\.\.)?/[\w./{{}}<>:~*-]*[\w}}>*-])"
    r"|(?P<pr>(?:PR|pull request) #\d+|[\w.-]+/[\w.-]+#\d+)"
    r"|(?P<env>[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)"
    r"|(?P<commit>[0-9a-f]{7,40})"
    rf"|(?P<file>(?:[\w.@+-]+/)*[\w@+-][\w.@+-]*\.(?:{_REF_EXT})(?::\d+(?::\d+)?)?)"
    r")(?![\w/-])")


REF_MESSAGE = None  # the hash of the reply being drawn (markdown_blocks): its own decisions apply


SIGNAL = None  # the active window's Signal while it's on: _inline turns the names it knows into links


def ref_kind(text):
    """What a name may be (a guess the resolvers settle), or None if it doesn't look like one."""
    t = ref_key(text)
    if not t or len(t) > 200 or "\n" in t:
        return None
    if re.fullmatch(rf"(?:{_REF_METHODS})\s+(?:…|\.\.\.)?/\S*", t) or \
            re.fullmatch(r"(?:…|\.\.\.)/[\w.{}<>:~*-]+(?:/[\w.{}<>:~*-]*)*", t) or \
            re.fullmatch(r"/(?:v\d+|api|private|internal|auth|__[\w-]+)(?:/[\w.{}<>:~*-]*)*(?:…|\.\.\.)?", t):
        return "route"  # (…/jobs/{job}/retry: the end of one; /v1/private/jobs/…: every route under it)
    if ref_hashlike(t) or ref_commit_range(t):
        return "commit"
    if re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+", t):
        return "env"
    if re.fullmatch(r"(?:(?:PR|pull request) )?#\d+|[\w.-]+/[\w.-]+#\d+", t, re.I):
        return "pr"
    if re.fullmatch(r"--[A-Za-z][\w-]*", t):
        return "cli"  # a flag: where it's defined
    if kube := ref_kube(t):  # (deploy/x, a kebab-case name, .Values.image.tag, spec.strategy.type: ref_kube)
        return "config" if kube[0] in ("values", "field") else "k8s"
    if " " in t:  # a call with its arguments, or a span ref_span reads (an object's shape, code), or a command
        if re.match(r"(?:[\w./~-]*/)?(?:kubectl|kubectx|oc|helm)\s", t):  # (-l app=x, --set k=v: still a command)
            return "cli" if ref_command(t) else None
        return "symbol" if ("(" in t and ref_symbolish(t)) or ref_span(t) else "cli" if ref_command(t) else None
    path = re.sub(r":\d+(?::\d+)?$", "", t)
    if path.startswith("@"):  # an npm scope (@orchard/ui): a package, not a path
        return None
    if "/" in path:
        if not re.fullmatch(r"[\w.@+~/-]+", path):
            return None
        last = path.rstrip("/").rsplit("/", 1)[-1]
        if not path.endswith("/") and not path.startswith(("/", "~", ".")) and \
                not re.search(r"\.[A-Za-z][A-Za-z0-9]{0,7}$", last):
            return "branch"  # dev/release-merge, origin/staging; else a folder (vendor/orchard-ui)
        return "file"
    if re.fullmatch(rf"[\w@+-][\w.@+-]*\.(?:{_REF_EXT}|{_REF_MORE_EXT})", path, re.I):
        return "file"  # a known extension: ui.fill and runs.unpack are code, not files
    return "symbol" if ref_symbolish(t) or ref_span(t) else None


def ref_symbolish(t):
    """Whether a name looks like code: a call (installs(), _install(..., upgrade=True)), a dotted name
    (quick_edit.start, previous.job_id, bundle.apps[].installed_installation_id), snake_case, camelCase, or a
    PascalCase compound (EditPane). Plain words (done, target, Generation) aren't: too many are ordinary words."""
    m = re.fullmatch(r"([A-Za-z_]\w*(?:\[\d*\*?\])?(?:\.[A-Za-z_]\w*(?:\[\d*\*?\])?)*)(\([^()]*\))?", t)
    if not m:
        return False
    name = re.sub(r"\[[^\]]*\]", "", m.group(1))
    return bool(m.group(2)) or "." in name or bool(re.search(r"[a-z0-9]_\w|^_+[a-z]", name)) or \
        bool(re.fullmatch(r"[a-z]+[A-Z]\w*", name)) or bool(re.fullmatch(r"[A-Z][a-z0-9]+[A-Z]\w*", name))


_REF_ID = r"[A-Za-z_]\w*"


_REF_DOTTED = rf"{_REF_ID}(?:\.{_REF_ID})*"


def ref_span(t):
    """A backticked span that's more than one name, as the code-symbol resolver reads it (None if it isn't one).
    The span as a whole is the glossary's entry, so the plain words in it (name, app, target) are looked up only
    together with the rest, never on their own:
      ("shape", key, names): an object's shape, {installation_id, member_id, name} or previous: {job_id, seq}
      ("pair", key, value): a key and a value that tells its definitions apart, preview.reason: crashed
      ("query", name, value): a URL's query parameter, ?orchard_theme=dark
      ("excerpt", text, None): code as it's written, keep_dist=package == "orchard-ui" (linked only if it is)"""
    t = ref_key(t)
    if ref_symbolish(t):
        return None
    if m := re.fullmatch(rf"(?:({_REF_DOTTED})\s*[:=]\s*)?\[?\{{\s*({_REF_ID}\??(?:\s*,\s*{_REF_ID}\??)+)"
                         r"(?:\s*,\s*(?:\.\.\.|…))?\s*\}\]?", t):
        names = [n.strip().rstrip("?") for n in m.group(2).split(",")]
        return ("shape", m.group(1), names) if len(set(names)) == len(names) else None
    if m := re.fullmatch(rf"([a-z_]\w*(?:\.{_REF_ID})*):\s+([\"']?)([\w.-]+)\2", t):  # (Error: ENOENT is a log line)
        value = m.group(3)  # (a word or a long number: true, 1 or 0 tell no definitions apart)
        if re.search(r"[A-Za-z]{2}", value) and value.lower() not in ("true", "false", "null", "none", "yes", "no") \
                or re.fullmatch(r"\d{3,}", value):
            return "pair", m.group(1), value
        return None
    if m := re.fullmatch(r"\?(\w+)=([^&\s]*)(?:&\w+=[^&\s]*)*", t):
        return "query", m.group(1), m.group(2)
    if re.search(r"[A-Za-z_]\w", t) and re.search(r"[=\[\"'(]|->", t) and "://" not in t and \
            not re.match(rf"(?:-|\$|(?:{_REF_METHODS})\s)", t) and len(re.sub(r"\.\.\.|…|\W", "", t)) >= 3:
        return "excerpt", t, None
    return None


def ref_command(t):
    """A command as replies write one, read for what it runs (None if it isn't one Signal can place):
      ("module", "pkg.mod"): python -m pkg.mod ...
      ("file", "scripts/shot.mjs"): node scripts/shot.mjs, build.mjs --check, vite --config web/vite.config.ts:
        the first file it names (the script, else the file it's given)
      ("flag", "--set"): a flag with its value, --set imageTag=$SHA
      ("flag", "--check", "mytool"): a program's flag, mytool --check: where it's defined, in that program
      ("kube", ...): what a kubectl or helm command acts on (ref_kube_command)"""
    t = ref_key(t)
    words = t.split()
    while words and re.fullmatch(r"[A-Z_][A-Z0-9_]*=\S*", words[0]):  # (FOO=1 node x.js)
        words = words[1:]
    if len(words) < 2 or len(t) > 160 or not re.fullmatch(r"[\w./~-]+", words[0]):
        return None
    if os.path.basename(words[0]) in ("kubectl", "kubectx", "oc", "helm"):
        return ref_kube_command(words)
    if re.fullmatch(r"--[A-Za-z][\w-]*", words[0]) and len(words) == 2:
        return "flag", words[0]
    if (i := next((i for i, w in enumerate(words[:-1]) if w == "-m" and i), None)) is not None and \
            re.fullmatch(r"python[\d.]*", os.path.basename(words[i - 1])) and \
            re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", words[i + 1]):
        return "module", words[i + 1]
    for w in words:
        if w.startswith("-") or "<" in w or "$" in w or "*" in w or "://" in w:
            continue
        w = w.split("=", 1)[-1].strip("'\"")
        if re.fullmatch(rf"[\w./~@+-]*[\w@+-]\.(?:{_REF_EXT})", w, re.I):
            return "file", w
    flag = next((w.split("=", 1)[0] for w in words[1:] if re.fullmatch(r"--[A-Za-z][\w-]*(?:=\S*)?", w)), None)
    if flag and re.fullmatch(r"[a-z][\w.-]*", words[0]):
        return "flag", flag, words[0]
    return None


def ref_quiet(surface):
    """Whether a backticked span is linked only once it's found: a piece of code (ref_span's excerpt), a command or
    flag (most are other programs'), or a bare kebab-case name (a Kubernetes object's, if the repository defines one,
    but as likely a package's or a command's), so not finding it says nothing."""
    return (span := ref_span(surface)) is not None and span[0] == "excerpt" or ref_kind(surface) == "cli" or \
        (kube := ref_kube(surface)) is not None and kube[0] == "name"


def ref_hashlike(t):
    """A commit hash as people write one: 7-12 hex digits, or all 40 (64 in a SHA-256 repository),
    with letters and digits both. A 32-digit id (a UUID without dashes) isn't one."""
    return bool(re.fullmatch(r"[0-9a-f]{7,12}|[0-9a-f]{40}|[0-9a-f]{64}", t)) and bool(re.search(r"[a-f]", t)) \
        and bool(re.search(r"\d", t))


def ref_commit_range(t):
    """A range of commits as git writes one (6207ca2..d9b150f, a...b): (from, "..", to), or None."""
    m = re.fullmatch(r"([0-9a-f]{7,40})(\.\.\.?)([0-9a-f]{7,40})", t)
    return m.groups() if m and ref_hashlike(m[1]) and ref_hashlike(m[3]) else None


def ref_pr_info(p):
    """The parts of a GitHub pull request (REST shape) the cards show."""
    state = "merged" if p.get("merged_at") else p.get("state", "")
    return {"number": p.get("number"), "title": p.get("title", ""), "state": state, "url": p.get("html_url"),
            "created": p.get("created_at"), "merged": p.get("merged_at"), "closed": p.get("closed_at")}


# (ref_epoch and typed_at: baudelib.transcripts, as the headless server reads transcripts too)


def last_typed(path, chunk=262144, limit=32 << 20):
    """When a transcript was last typed in (typed_at), as epoch seconds, read back from its end a chunk at a time: the
    tool results after the last prompt can run to megabytes. None if nothing typed is in its last `limit` bytes."""
    try:
        with open(path, "rb") as f:
            end = f.seek(0, os.SEEK_END)
            carry, read = b"", 0
            while end > 0 and read < limit:
                start = max(0, end - chunk)
                f.seek(start)
                data = f.read(end - start) + carry
                read += end - start
                lines = data.split(b"\n")
                carry = lines.pop(0) if start > 0 else b""  # (cut off at the chunk's start: whole with the next)
                if len(carry) > limit:
                    carry = b""  # (one enormous line: a tool's result, never something typed)
                said = None
                for raw in lines:
                    if b'"user"' not in raw[:4096] and b'"queued_command"' not in raw[:300] \
                            or b'"tool_use_id"' in raw[:1500]:  # (a tool's result: not decoded)
                        continue
                    try:
                        when = typed_at(json.loads(raw))
                    except ValueError:
                        continue
                    if when and when > (said or ""):
                        said = when
                if said:
                    return ref_epoch(said)
                end = start
    except OSError:
        pass
    return None


# ------------------------------------------------------- signal: Kubernetes --
# Replies about a cluster name its objects (`deploy/relay-svc`, "the `beacon-runner` Deployment", a pod
# `relay-svc-7d9f8b6c5-x2x4z`, `relay-svc.relay.svc.cluster.local`), their configuration (`.Values.image.tag`,
# `helm upgrade … --set image.tag=…`, `spec.strategy.type`) and the contexts commands run against
# (`kubectl --context k3d-dev …`). The rules place them from what the repository says, with no cluster: raw
# manifests; Helm charts, a templated name rendered for each release the repository's own `helm upgrade --install`
# lines name, else matched by its literal parts; kustomize overlays (namePrefix / nameSuffix, generated ConfigMaps).
# The kubeconfig gives its contexts' names, clusters and namespaces, never credentials; nothing asks a cluster
# anything (kubectl isn't run).

KUBE_KINDS = {alias: kind for kind, aliases in {
    "Pod": "po pod pods", "Deployment": "deploy deployment deployments", "Service": "svc service services",
    "StatefulSet": "sts statefulset statefulsets", "DaemonSet": "ds daemonset daemonsets",
    "ReplicaSet": "rs replicaset replicasets", "Job": "job jobs", "CronJob": "cj cronjob cronjobs",
    "ConfigMap": "cm configmap configmaps", "Secret": "secret secrets", "Ingress": "ing ingress ingresses",
    "Namespace": "ns namespace namespaces", "ServiceAccount": "sa serviceaccount serviceaccounts",
    "PersistentVolumeClaim": "pvc persistentvolumeclaim persistentvolumeclaims",
    "PersistentVolume": "pv persistentvolume persistentvolumes",
    "HorizontalPodAutoscaler": "hpa horizontalpodautoscaler horizontalpodautoscalers",
    "PodDisruptionBudget": "pdb poddisruptionbudget poddisruptionbudgets",
    "NetworkPolicy": "netpol networkpolicy networkpolicies", "Role": "role roles", "RoleBinding": "rolebinding rolebindings",
    "ClusterRole": "clusterrole clusterroles", "ClusterRoleBinding": "clusterrolebinding clusterrolebindings",
    "StorageClass": "sc storageclass storageclasses", "Endpoints": "ep endpoints", "Rollout": "rollout rollouts",
    "NodePool": "nodepool nodepools", "ScaledObject": "scaledobject scaledobjects",
    "Certificate": "cert certificate certificates", "HTTPRoute": "httproute httproutes", "Gateway": "gateway gateways",
    "CustomResourceDefinition": "crd crds"}.items() for alias in aliases.split()}


_KUBE_PASCAL = re.compile(r"[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*")  # (a custom resource's kind: NodePool, EC2NodeClass)


def ref_kube(t):
    """A name written the way Kubernetes is talked about, or None:
      ("object", kind, name, namespace): deploy/relay-svc, Deployment/relay-svc, deployment.apps/x, NodePool/builder,
        or relay-svc.relay.svc.cluster.local, a Service's DNS name (the namespace None when it isn't given)
      ("name", name): a bare kebab-case name, beacon-runner-ingress: an object's, if the repository defines one
      ("context", name): an EKS cluster's ARN, which is how kubeconfigs name its context
      ("values", "image.tag"): .Values.image.tag, a Helm chart's value
      ("field", "spec.strategy.type"): spec.strategy.type, .metadata.labels: a key path in manifests"""
    t = ref_key(t)
    if m := re.fullmatch(r"(?:\{\{-?\s*)?\$?\.Values\.([A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)*)(?:\s*-?\}\})?", t):
        return "values", m.group(1)
    if m := re.fullmatch(r"\.?((?:spec|metadata)(?:\.[A-Za-z_][\w-]*(?:\[[^\]]*\])?)+)", t):
        return "field", re.sub(r"\[[^\]]*\]", "", m.group(1))
    if re.fullmatch(r"arn:aws:eks:[\w-]+:\d+:cluster/[\w.-]+", t):
        return "context", t
    if m := re.fullmatch(r"(?:https?://)?([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\.([a-z0-9-]+)\.svc(?:\.cluster\.local)?\.?"
                         r"(?::\d+)?(?:/\S*)?", t):
        return "object", "Service", m.group(1), m.group(2)
    if (m := re.fullmatch(rf"([A-Za-z][A-Za-z0-9]*)(?:\.[\w.]+)?/({_KUBE_NAME})", t)) and \
            not re.search(rf"\.(?:{_REF_EXT}|{_REF_MORE_EXT})$", m.group(2), re.I):
        kind = KUBE_KINDS.get(m.group(1).lower()) or (m.group(1) if _KUBE_PASCAL.fullmatch(m.group(1)) else None)
        if kind:
            return "object", kind, m.group(2), None
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", t) and 4 <= len(t) <= 63 and re.search(r"[a-z]", t) and \
            not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", t):  # (not a date, nor a UUID)
        return "name", t
    return None


def ref_kube_command(words):
    """What a kubectl, kubectx or helm command (its words) acts on, as ref_command says it, or None:
      ("kube", ("object", kind, name, namespace)): kubectl rollout restart deploy/x -n ns, kubectl logs <pod>
      ("kube", ("selector", key, value, namespace)): kubectl get pods -l app=x
      ("kube", ("context", name)): kubectl config use-context x, kubectx x, kubectl --context x get pods
      ("kube", ("values", key)): helm upgrade ... --set image.tag=$SHA
      ("kube", ("release", name, namespace)): helm status x, helm rollback x
      ("file", path): the chart helm installs, the values file it's given, the manifest kubectl applies"""
    prog = os.path.basename(words[0])
    if prog == "kubectx":
        return ("kube", ("context", words[1])) if len(words) == 2 and re.fullmatch(r"[\w:./@-]+", words[1]) else None
    opts, pos = _kube_args(words)
    placeholder = re.compile(r"[$<{*`]|://")
    get = lambda *flags: next((opts[f] for f in flags if opts.get(f) and not placeholder.search(opts[f])), None)  # noqa: E731
    ns = get("-n", "--namespace")
    ns = ns if ns and re.fullmatch(_KUBE_NAME, ns) else None
    if prog == "helm":
        if key := next((opts[f] for f in ("--set", "--set-string", "--set-file", "--set-json", "--set-literal")
                        if opts.get(f)), None):  # (the key: its value may well be $SHA)
            key = key.split(",")[0].split("=")[0]
            if re.fullmatch(r"[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*|\[\d+\])*", key):
                return "kube", ("values", re.sub(r"\[\d+\]", "", key))
        verb, args = (pos[0] if pos else ""), pos[1:]
        if verb == "diff" and args:  # (helm diff upgrade REL CHART)
            verb, args = args[0], args[1:]
        chart = args[1] if verb in ("upgrade", "install") and len(args) >= 2 else \
            args[-1] if verb in ("template", "lint", "package", "dependency", "dep", "show") and args else None
        if chart and not placeholder.search(chart) and ("/" in chart or chart.startswith(".")):
            return "file", chart.rstrip("/")
        if values := get("-f", "--values"):
            return "file", values
        if verb in ("uninstall", "delete", "status", "rollback", "history", "get") and args:
            rel = args[1] if verb == "get" and len(args) > 1 else args[0]
            return ("kube", ("release", rel, ns)) if re.fullmatch(_KUBE_NAME, rel) else None
        return None
    if pos[:1] == ["config"] and len(pos) > 2 and pos[1] in ("use-context", "set-context", "get-contexts",
                                                             "delete-context", "rename-context"):
        return "kube", ("context", pos[2])
    if (f := get("-f", "--filename", "-k", "--kustomize")) and f != "-":
        return "file", f
    verb, args = (pos[0] if pos else ""), pos[1:]
    if verb in ("rollout", "set", "top", "create") and args:  # (rollout restart deploy/x, set image deploy/x c=img)
        args = args[1:]
    kind = name = None
    if args and "/" in args[0]:
        k, _, name = args[0].partition("/")
        kind = KUBE_KINDS.get(k.split(".")[0].lower()) or (k if _KUBE_PASCAL.fullmatch(k) else None)
        name = name if kind else None
    elif args and (k := KUBE_KINDS.get(args[0].split(".")[0].lower())) and len(args) > 1:
        kind, name = k, args[1]
    elif args and verb in ("logs", "exec", "attach", "port-forward", "cp", "debug") and \
            not KUBE_KINDS.get(args[0].lower()):
        kind, name = "Pod", args[0].split(":")[0]  # (kubectl cp pod:/path .)
    if name and re.fullmatch(_KUBE_NAME, name):
        return "kube", ("object", kind, name, ns)
    if (sel := get("-l", "--selector")) and (m := re.fullmatch(r"([\w./-]+)=([\w.-]+)", sel.split(",")[0])):
        return "kube", ("selector", m.group(1), m.group(2), ns)
    if ns:
        return "kube", ("object", "Namespace", ns, None)
    if ctx := get("--context"):
        return "kube", ("context", ctx)
    return None


def ref_icon_slot(icon):
    """Where a link's logo goes, in markup: an invisible character LinkLabel makes the logo's width, naming the logo.
    It's U+2063, which Unicode breaks lines around as a letter: a line may end before the logo, never between the
    logo and its name. (A zero-width space with a word joiner after it still let the line end between them.)"""
    return f'<span font_family="baude-icon-{icon}">\u2063</span>' if icon else ""


def pr_pill_markup(inner, href, title, state):
    """inner (markup, no link in it) as a pull request's pill, linked to href: git's merge logo before it, on a rounded
    patch (LinkLabel draws them), in the colour of where it stands (PR_COLORS; state None: not known yet, a link's).
    Its places for the logo and the patch's ends are as wide whatever the state, so a state that comes later only
    colours it."""
    if color := PR_COLORS.get(state):
        inner = f'<span foreground="{color}">{inner}</span>'
    if state == "closed":
        inner = f"<s>{inner}</s>"
    return (f'<a href="{html.escape(href)}" title="{html.escape(title)}"><span underline="none">'
            f'<span font_family="baude-pill-{state or "unknown"}">\u2063</span>{inner}'
            f'<span font_family="baude-pill-end">\u2063</span></span></a>')


def pr_title(p, repo, number):
    """A pull request's pill's tooltip: its number, where it stands and its name."""
    said = PR_SAID.get((p or {}).get("state"))
    return " · ".join(x for x in (f"{repo}#{number}", said, (p or {}).get("title")) if x)


def chat_repo():
    """The GitHub repository (owner/name) the reply being drawn is in, what a bare #12 in it means: its Signal's (the
    chat's checkout's origin), else its folder's as GITHUB has read it. None if there's none."""
    return getattr(SIGNAL, "github", None) or \
        lookup.GITHUB.own_repository(getattr(SIGNAL, "folder", None) or MarkdownView.base_dir)


def pr_pill(repo, number, inner, href=None, known=False, any_repo=False):
    """A pull request of repo's as a pill (pr_pill_markup), linked to href (else its page); None if repo isn't the
    chat's (chat_repo: another's keeps its link as it was) unless any_repo (a link to one, whose shape says it is: its
    pill, in where it stands once that's read, and a link's colour till then), with no gh or gh signed out, if
    nothing's been read of its pull requests (and not any_repo), or (known) if number isn't one of them as far as is
    known: an issue, or nothing (a bare #12 may be either, or a colour). Its own #12 in inner reads 12 (pr_numbered)."""
    if not repo or not any_repo and repo.lower() != (chat_repo() or "").lower():
        return None
    if lookup.PULLS.states(repo) is None and not (any_repo and lookup.gh_path() and not lookup.PULLS.signed_out()):
        return None
    p = lookup.PULLS.pull(repo, number)
    if p is None and known:
        return None
    return pr_pill_markup(pr_numbered(inner, number),
                          href or (p or {}).get("url") or f"https://github.com/{repo}/pull/{number}",
                          pr_title(p, repo, number), (p or {}).get("state"))


_PR_NAMED = re.compile(r"(?<![\w&;#/.-])(?:(?:PR|pull request) ?)?(?:[\w.-]+/[\w.-]+)?#(\d+)(?![\w-])(?![^<]*>)", re.I)


def pr_numbered(markup, number):
    """markup with what names pull request number in it (#12, PR #12, pull request #12, owner/name#12) as the number
    alone, for after its logo: the logo says it's one, so a # would say it twice. Its tags' own left alone."""
    return _PR_NAMED.sub(lambda m: m[1] if int(m[1]) == number else m[0], markup)


def pr_unbroken(markup):
    """A pill's text (markup) with its spaces no place to end a line (PR #12 on one), its tags' own left alone."""
    return re.sub(r" (?![^<]*>)", "\u00a0", markup)


def pr_unremembered():
    """The text being drawn names a pull request: _inline doesn't remember its markup (where it stands may change, and
    its repository's pull requests may not be read yet). True."""
    global _checked_since
    _checked_since = float("-inf")
    return True


_PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)(?:[/?#]\S*)?")


_BARE_PR = re.compile(r"(?<![\w&#/.-])#(\d{1,7})(?![\w-])")  # (#12; not &#12; nor owner/name#12)


def pr_link_markup(url, markup=None):
    """A link to a pull request, any repository's (pr_pill's any_repo), as its pill: what the link says (markup), its
    #12 or PR #12 the number alone, else its number. None for any other link, and when pr_pill makes none."""
    m = _PR_URL.fullmatch(url)
    if not m or not pr_unremembered():
        return None
    return pr_pill(m[1], int(m[2]), markup or m[2], href=url, any_repo=True)  # (the logo says it's one: no "#")


def ref_markup(ref, inner, icon=True):
    """inner (markup) as a Signal link, drawn by how far its lookup got: plain when found, a count
    when there are several matches, faint while pending, grey when not found, amber and wavy when stale. A pull
    request found is its pill (pr_pill), if its repository's have been read; else its logo and its number."""
    href = html.escape(REF_SCHEME + urllib.parse.quote(ref.surface, safe=""))
    pr = icon and ref.kind == "pr" and ref.status == "resolved" and not ref.data.get("issue") and \
        isinstance(ref.data.get("number"), int)
    if pr and (pill := pr_pill(ref.data.get("repo"), ref.data["number"], str(ref.data["number"]),
                               href=REF_SCHEME + urllib.parse.quote(ref.surface, safe=""))) is not None:
        return pill
    if pr:  # (after its logo, as on a pill: PR #12 and owner/name#12 read 12)
        inner = pr_numbered(inner, ref.data["number"])
    if ref.status == "pending":
        inner = f'<span alpha="55%">{inner}</span>'
    elif ref.status == "not_found":
        inner = f'<span foreground="#8a8a8a" underline="single" underline_color="#c0c0c0">{inner}</span>'
    elif ref.status == "stale":
        inner = f'<span foreground="#c27b00" underline="error" underline_color="#c27b00">{inner}</span>'
    elif ref.status == "ambiguous":
        inner += f"<sup><small>{len(ref.locations)}</small></sup>"
    title = "Looking it up…" if ref.status == "pending" else ref.summary()
    # (its logo once it's found: a name still being looked up, or not there, claims no kind)
    slot = ref_icon_slot(ref_icon(ref.kind, ref.data, [l["role"] for l in ref.locations])) \
        if icon and ref.status in ("resolved", "ambiguous", "stale") else ""
    return f'{slot}<a href="{href}" title="{html.escape(title)}">{inner}</a>'


REF_BARE_NUMBER = re.compile(r"#\d+")  # (in code it's no link: a colour, a comment; owner/name#12 says what it is)


def ref_code_markup(code, message=None):
    """A code block's text as markup with the names linked: as the reply decided them (message: its hash), else
    the ones Signal placed (None: nothing to link)."""
    if SIGNAL is None or not code or len(code) > 200_000:
        return None

    def markup(s, inner):
        if (dec := SIGNAL.decision(message, s)) is not None:  # (no logos in code: its columns stay put)
            return ref_decision_markup(s, dec, inner, icon=False) if dec["verdict"] != "plain" else None
        r = SIGNAL.refs.get(s)  # (a plain word: not in code blocks, where nothing decides it)
        if r is None or r.status not in ("resolved", "ambiguous") or re.fullmatch(r"[A-Za-z][a-z]+", s):
            return None
        return ref_markup(r, inner, icon=False)
    names = [s for s in set(SIGNAL.refs) | (set(SIGNAL._by_surface) if message else set())
             if len(s) >= 3 and s in code and not REF_BARE_NUMBER.fullmatch(s) and markup(s, "") is not None]
    if not names:
        return None
    pattern = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) +
                         r")(?![\w/-])")
    out, at = [], 0
    for m in pattern.finditer(code):
        out.append(html.escape(code[at:m.start()], quote=False))
        out.append(markup(m.group(0), html.escape(m.group(0), quote=False)))
        at = m.end()
    if not at:
        return None
    out.append(html.escape(code[at:], quote=False))
    return "".join(out)


def ref_link(text, inner, backticked):
    """inner linked to what SIGNAL knows about text, or None. Names that appear only in plain text
    are linked once found; a backticked one shows every state, unless it's a piece of code (ref_quiet)."""
    if SIGNAL is None:
        return None
    key = ref_key(text)
    if backticked and REF_BARE_NUMBER.fullmatch(key):  # (code: a colour's #333 as like as not)
        return None
    git = SIGNAL.refs.get(key)
    if git is not None and git.kind in ("commit", "branch", "pr") and git.status == "resolved":
        return ref_markup(git, inner)  # (a git ref: Baude knows it exactly)
    if (dec := SIGNAL.decision(REF_MESSAGE, key)) is not None:  # (what it means in this reply)
        return ref_decision_markup(key, dec, inner) if dec["verdict"] != "plain" else None  # (plain English here)
    ref = SIGNAL.refs.get(key)
    if ref is None or not (backticked or ref.surface in SIGNAL.strong or ref.status in ("resolved", "ambiguous", "stale")):
        return None
    if ref.data.get("plain") or (SIGNAL.reading() and re.fullmatch(r"[A-Za-z][a-z]{2,}", ref.surface)):
        return None  # (a plain word: the reader decides, per reply)
    if ref.status not in ("resolved", "ambiguous", "stale") and ref_quiet(ref.surface) and \
            not (ref.kind == "k8s" and ref.surface in SIGNAL.strong):  # (one a reply called a Deployment is missing)
        return None  # (code that isn't in the repository isn't a name that's missing)
    return ref_markup(ref, inner)


REF_ABSENT_WHY = {"proposed": "Proposed, not written yet", "external": "An outside tool or product",
                  "elsewhere": "Somewhere else (another checkout, branch or machine)", "not_found": "Not in the code"}


def ref_decision_markup(surface, dec, inner, icon=True):
    """inner linked to where the reader (or the evidence) placed this name in this reply; or, for one it says isn't
    in the code, to what it is: an outside tool as a link, anything else grey, like a name not found. A pull request
    it placed is its pill (pr_pill), if its repository's have been read; else its logo and its number."""
    href = html.escape(REF_SCHEME + urllib.parse.quote(surface, safe="") + "#" + dec["message"])
    pr = icon and dec.get("kind") == "pr" and dec["verdict"] != "not_in_code" and \
        re.fullmatch(r"(?:(?:PR|pull request) )?(?:([\w.-]+/[\w.-]+))?#(\d+)", surface, re.I)
    if pr and (pill := pr_pill(pr[1] or chat_repo(), int(pr[2]), pr[2], known=True,
                               href=REF_SCHEME + urllib.parse.quote(surface, safe="") + "#" + dec["message"])):
        return pill
    if pr:  # (after its logo, as on a pill: PR #12 reads 12)
        inner = pr_numbered(inner, int(pr[2]))
    if dec["verdict"] == "not_in_code":
        why = REF_ABSENT_WHY.get(dec.get("why"), REF_ABSENT_WHY["not_found"])
        title = html.escape(" · ".join(x for x in (why, dec.get("note")) if x))
        if dec.get("why") != "external":
            inner = f'<span foreground="#8a8a8a" underline="single" underline_color="#c0c0c0">{inner}</span>'
        slot = ref_icon_slot("external") if icon and dec.get("why") == "external" else ""
        return f'{slot}<a href="{href}" title="{title}">{inner}</a>'
    where = (dec.get("path") or "") + (f":{dec['line']}" if dec.get("line") else "")
    title = html.escape(" · ".join(x for x in (dec.get("note"), where) if x))
    slot = ref_icon_slot(ref_icon(dec.get("kind"), None, [dec.get("role") or ""])) if icon else ""
    return f'{slot}<a href="{href}" title="{title}">{inner}</a>'


_CODE_SPAN = re.compile(r"`([^`\n]+)`")


_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


_BARE_URL = re.compile(r"(?<![\w/])(https?://[^\s<>()\[\]`*\x00]+[^\s<>()\[\]`*.,;:!?'\"\x00])")


_BOLD_ITALIC = re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", re.S)


_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)


_STAR_ITALIC = re.compile(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])")


_UNDERSCORE_ITALIC = re.compile(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)")


_STRIKE = re.compile(r"~~(.+?)~~")


_KEPT_LINK = re.compile(r"\x00(\d+)\x00")


EM_DASH = re.compile(r"(?<=\s)--(?=\s)")  # (-- on its own, whitespace both sides: an em dash, outside code)


INLINE_MEMO_BYTES = 512 * 1024  # what _inline remembers, at most: its texts and their markup


_inline_memo, _inline_memo_bytes = {}, 0  # (text, folder) -> (good until, markup), oldest first


def _memo_bytes(text, markup):
    return sys.getsizeof(text) + sys.getsizeof(markup) + 200  # (and the tuples, the float, the dict's slot)


def _inline(text):
    """Escape and apply inline markdown (code, bold, italic, links) as Pango markup. With no Signal links shown,
    the same text in the same folder is drawn the same, so it's remembered (a streaming reply draws its last
    lines every frame): till an answer it used about a path is due to be looked at again (resolve_path), and not
    at all if it drew an artifact's title (a title may come later)."""
    global _checked_since, _inline_memo_bytes
    if SIGNAL is not None:  # (names are drawn as Signal knows them now, in this reply: never remembered)
        return _inline_markup(text)
    key = (text, MarkdownView.base_dir)
    if (hit := _inline_memo.get(key)) is not None and hit[0] > time.monotonic():
        return hit[1]
    _checked_since = float("inf")
    markup = _inline_markup(text)
    size = _memo_bytes(text, markup)
    if _checked_since > float("-inf") and size <= INLINE_MEMO_BYTES // 16:
        if (old := _inline_memo.pop(key, None)) is not None:
            _inline_memo_bytes -= _memo_bytes(text, old[1])
        _inline_memo[key] = (_checked_since + EXISTS_FOR, markup)
        _inline_memo_bytes += size
        while _inline_memo_bytes > INLINE_MEMO_BYTES:
            oldest = next(iter(_inline_memo))
            _inline_memo_bytes -= _memo_bytes(oldest[0], _inline_memo.pop(oldest)[1])
    return markup


def _inline_markup(text):
    """_inline's markup. Code spans and links are set aside first, as placeholders, so emphasis goes round them:
    **`deployer-svc`** is bold code, and a link's text can be code ([`app.py`](app.py)). (Split at the code spans
    instead, a bold with code in it showed its **.)"""
    marks, bare = [], []  # (what each placeholder stands for, and the same without a link of its own)

    def keep(markup, plain=None):
        marks.append(markup)
        bare.append(markup if plain is None else plain)
        return f"\x00{len(marks) - 1}\x00"

    def code(m):
        markup = (f'<span font_family="monospace" size="{code_markup_size()}" bgcolor="#7f7f7f" bgalpha="22%">'
                  f'{html.escape(m[1], quote=False)}</span>')
        hit = resolve_path(m[1])
        return keep(f'<a href="{html.escape(file_href(*hit))}" title="{html.escape(hit[0])}">{markup}</a>' if hit
                    else ref_link(m[1], markup, True) or markup, markup)

    def link(m):  # (its text as markup: code and bold in it, but no link inside a link)
        inner = _KEPT_LINK.sub(lambda k: bare[int(k[1])], _emphasis(html.escape(m[1], quote=False)))
        return keep(pr_link_markup(m[2], inner) or link_markup(m[2], m[1], inner))  # (a pull request's: its pill)
    p = _CODE_SPAN.sub(code, text) if "`" in text else text  # (each pattern only where it could match: most
    if "](" in p:                                             # lines have none of them)
        p = _MD_LINK.sub(link, p)
    if "://" in p:
        p = _BARE_URL.sub(lambda m: keep(pr_link_markup(m[1]) or link_markup(m[1])), p)
    if SIGNAL is not None:  # names Signal found (commits, env vars, routes...) outside backticks
        p = _REF_PLAIN.sub(lambda m: keep(ref) if (ref := ref_link(m[0], html.escape(m[0], quote=False), False))
                           else m[0], p)
        phrases = SIGNAL.message_phrases(REF_MESSAGE) if SIGNAL.reading() else SIGNAL.phrase_re
        if phrases is not None:  # and the ones the reader linked in this reply's prose ("the Scheduler")
            p = phrases.sub(lambda m: keep(ref) if (ref := ref_link(m[0], html.escape(m[0], quote=False),
                                                                            False)) else m[0], p)
        if catalogue := getattr(SIGNAL, "catalogue", None):  # and the distinctive names the catalogue has
            p = _CATALOGUE_TOKEN.sub(lambda m: keep(ref) if catalogue_shaped(m[0]) and m[0] in catalogue and (
                ref := ref_link(m[0], html.escape(m[0], quote=False), False)) else m[0], p)
        if (unexplained := getattr(SIGNAL, "unexplained", None)) is not None and \
                (said := unexplained(REF_MESSAGE)) is not None:  # (Record's: the "the X"s nothing introduced, red)
            p = said.sub(lambda m: keep(unexplained_markup(m[0])), p)
    if "#" in p and _BARE_PR.search(p) and pr_unremembered() and (repo := chat_repo()):  # (a bare #12: a pill if
        # it's one of the repository's pull requests)
        p = _BARE_PR.sub(lambda m: keep(pill) if (pill := pr_pill(repo, int(m[1]), m[1], known=True)) else m[0], p)
    if "--" in p:  # (the code spans are set aside by now: -- in them stays)
        p = EM_DASH.sub("\u2014", p)
    s = _emphasis(link_paths(html.escape(p, quote=False)))
    return _KEPT_LINK.sub(lambda m: marks[int(m[1])], s) if "\x00" in s else s


def unexplained_markup(text):
    """text in red, the theme's (light or dark): a noun its chat takes as known that nothing introduced (nouns.py)."""
    red = "#ff7b63" if Adw.StyleManager.get_default().get_dark() else "#c01c28"
    return f'<span foreground="{red}">{html.escape(text, quote=False)}</span>'


def _emphasis(s):
    """Bold, italic and strike-through in escaped text. As CommonMark has it, a run that opens isn't followed by a
    space and one that closes doesn't follow one (2 * 3 * 4 isn't italic), and _ doesn't work inside a word."""
    if "*" in s:
        s = _BOLD_ITALIC.sub(r"<b><i>\1</i></b>", s)
        s = _BOLD.sub(r"<b>\1</b>", s)
        s = _STAR_ITALIC.sub(r"<i>\1</i>", s)
    if "_" in s:
        s = _UNDERSCORE_ITALIC.sub(r"<i>\1</i>", s)
    return _STRIKE.sub(r"<s>\1</s>", s) if "~~" in s else s


_ITEM = re.compile(r"^(\s*)([-*+•◦]|\d+[.)])\s+(.*)$")


BULLET, SUB_BULLET = "•", "◦"


def bullet_for(depth):
    """• at the top level, ◦ one level in, alternating further down."""
    return BULLET if depth % 2 == 0 else SUB_BULLET


INDENT_STEP = 18  # px per nesting level, in the chat and in the input box


_IMAGE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)\s]+)\)\s*$")


_FENCE = re.compile(r"^\s*```\s*(\S*)")
_FENCE_LINE = re.compile(r"\s*(```|~~~)\s*(\S*)")


def fence_spans(lines, tildes=True, typing=()):
    """Where the code blocks are in text being written, of its lines: [(first line, last line, closed)], in order. A fence
    that a later bare one closes goes to it. One that nothing closes has only the lines right after it, up to a blank one:
    never all the rest of the page. With an odd number, one's left over: one on a line being typed on (typing) if there is
    one, as it's the new one; else the one that leaves the fewest lines in code, so a fence typed above blocks that were
    there doesn't turn them inside out."""
    fences = []
    for n, line in enumerate(lines):
        if "```" in line or tildes and "~~~" in line:
            m = _FENCE_LINE.match(line)
            if m and (tildes or m.group(1) == "```"):
                fences.append((n, not m.group(2)))

    def paired(some):
        closed, loose, i = [], [], 0
        while i < len(some):
            if i + 1 < len(some) and some[i + 1][1]:
                closed.append((some[i][0], some[i + 1][0]))
                i += 2
            else:
                loose.append(some[i][0])
                i += 1
        return closed, loose
    closed, loose = paired(fences)
    if len(fences) % 2 and len(fences) <= 64:
        def without(j):
            kept, left = paired(fences[:j] + fences[j + 1:])
            return kept, left + [fences[j][0]]
        here = [j for j, f in enumerate(fences) if f[0] in typing]
        if here:
            closed, loose = without(here[-1])
        else:
            closed, loose = min((without(j) for j in reversed(range(len(fences)))),
                                key=lambda found: (len(found[1]), sum(b - a for a, b in found[0])))
    at_fence = {f[0] for f in fences}
    spans = [(a, b, True) for a, b in closed]
    for first in loose:
        last = first
        while last + 1 < len(lines) and lines[last + 1].strip() and last + 1 not in at_fence:
            last += 1
        spans.append((first, last, False))
    return sorted(spans)


_TABLE_RULE = re.compile(r":?-{2,}:?")


_HEADING = re.compile(r"^(#{1,6})\s+(.*)")


_RULE = re.compile(r"\s*([-*_])(\s*\1){2,}\s*")


_QUOTE_MARK = re.compile(r"^>\s?")


def markdown_blocks(md):
    """Parse markdown into a flat list of blocks:
    ('para', markup) ('heading', level, markup) ('item', depth, marker, markup)
    ('quote', markup) ('hr',) ('code', lang, raw) ('image', alt, src) ('table', rows)
    """
    return markdown_parse(md)[0]


def message_key(md):
    """A reply's hash, as REF_MESSAGE has it while the reply is drawn."""
    return hashlib.sha1(md.encode("utf-8", "replace")).hexdigest()[:16]


def markdown_parse(md, since=None, message=None):
    """markdown_blocks, and the place to read on from once more is appended to md (a reply streaming in is read
    from there each frame, not from the top). since: that place, from an earlier parse of the start of md (None:
    the top); message: md's hash, if it's known (REF_MESSAGE). The place is the end of the last whole line after
    which nothing was left open (a paragraph's or a table's lines, a fence): no line after it can change the blocks
    before it but the last (a list item a line carries on, a quote the next > line joins), kept as it was there."""
    global REF_MESSAGE
    REF_MESSAGE = message or message_key(md)  # (this reply's decisions)
    at, before, n, last = since or (0, (), 0, None)
    blocks, para, code, lang, table = [*before[:n - 1], last] if n else [], [], None, "", []
    mark = (at, n, last)

    def flush():
        nonlocal para, table
        if para:
            blocks.append(("para", "\n".join(_inline(l) for l in para)))
            para = []
        if table:
            blocks.append(("table", table))
            table = []

    lines = md[at:].split("\n") if at else md.split("\n")
    for i, line in enumerate(lines):
        if i:  # (the line before is whole: if it left nothing open, what's read so far is settled up to here)
            at += len(lines[i - 1]) + 1
            if code is None and not para and not table:
                mark = (at, len(blocks), blocks[-1] if blocks else None)
        fence = _FENCE.match(line) if "```" in line else None
        if code is not None:
            if fence and not fence.group(1):
                blocks.append(("code", lang, "\n".join(code)))
                code = None
            else:
                code.append(line)
            continue
        if fence:
            flush()
            code, lang = [], fence.group(1)
            continue
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
            if para:
                flush()
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            if not all(_TABLE_RULE.fullmatch(c) for c in cells):  # skip the |---| rule
                table.append(cells)
            continue
        if table:
            flush()
        if not stripped:
            flush()
            continue
        if line[:1] == "#" and (h := _HEADING.match(line)):
            flush()
            blocks.append(("heading", len(h.group(1)), _inline(h.group(2))))
        elif _RULE.fullmatch(line):
            flush()
            blocks.append(("hr",))
        elif m := _ITEM.match(line):
            flush()
            depth = len(m.group(1).expandtabs(4)) // 2
            marker = bullet_for(depth) if m.group(2) in "-*+•◦" else m.group(2)
            blocks.append(("item", depth, marker, _inline(m.group(3))))
        elif line.startswith(">"):
            flush()
            text = _inline(_QUOTE_MARK.sub("", line))
            if blocks and blocks[-1][0] == "quote":
                blocks[-1] = ("quote", blocks[-1][1] + "\n" + text)
            else:
                blocks.append(("quote", text))
        elif m := _IMAGE.match(line):
            flush()
            blocks.append(("image", m.group(1), m.group(2)))
        elif blocks and blocks[-1][0] == "item" and not para and line[:1].isspace():
            kind, depth, marker, markup = blocks[-1]  # wrapped continuation of a list item
            blocks[-1] = (kind, depth, marker, markup + " " + _inline(stripped))
        else:
            para.append(line)
    if code is not None:  # unterminated fence while streaming
        blocks.append(("code", lang, "\n".join(code)))
    flush()
    return blocks, (mark[0], blocks, mark[1], mark[2])


class TableScroll(Gtk.ScrolledWindow):
    """A markdown table's scroller. Its long cells wrap at the width there is: the short columns keep their natural
    width and the long ones share what's left of the chat's width, never under MIN_CHARS characters; past that the
    table scrolls sideways instead of squashing its columns. (A scrolled table measures its height at the cells'
    *minimum* widths, so a long cell's minimum is always its real width: its width in characters is set, not left
    to wrap, and set again when the width there is changes.)"""

    MIN_CHARS = 40

    def __init__(self, grid, cells):
        super().__init__(child=grid, vscrollbar_policy=Gtk.PolicyType.NEVER,
                         hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                         propagate_natural_height=True, propagate_natural_width=True)
        self.add_css_class("md-table-scroll")
        self.grid, self.cells = grid, cells  # (cells: [(label, its text, a header cell, column, long)])
        self.fitted, self.fitting = None, None
        self.chars = self.MIN_CHARS

    def do_size_allocate(self, width, height, baseline):
        Gtk.ScrolledWindow.do_size_allocate(self, width, height, baseline)
        if self.fitted != width and self.fitting is None:  # (after this allocation, not inside it)
            self.fitting = GLib.idle_add(self.fit)

    def available(self):
        """The width a table may take: its parent's (the chat column's), less this scroller's margins."""
        parent = self.get_parent()
        return (parent.get_width() if parent is not None else self.get_width()) - self.get_margin_start() - \
            self.get_margin_end()

    def fit(self):
        self.fitting = None
        width = self.available()
        long_cols = sorted({col for _l, _t, _h, col, long in self.cells if long})
        if width <= 0 or not long_cols:
            self.fitted = self.get_width()
            return False
        short = {}
        for lbl, _t, _h, col, long in self.cells:
            if col not in long_cols:
                short[col] = max(short.get(col, 0), lbl.measure(Gtk.Orientation.HORIZONTAL, -1)[1])
        metrics = self.cells[0][0].get_pango_context().get_metrics(None, None)  # (as a label's width_chars counts)
        char = max(1.0, max(metrics.get_approximate_char_width(), metrics.get_approximate_digit_width()) / Pango.SCALE)
        pad = 26  # (a cell's padding and rule)
        share = (width - 4 - sum(short.values())) / len(long_cols) - pad
        chars = max(self.MIN_CHARS, int(share / char))
        if chars != self.chars:
            self.chars = chars
            for lbl, text, _h, col, long in self.cells:
                if long:
                    n = min(chars, len(text))  # (a cell shorter than that is as wide as its text)
                    lbl.set_width_chars(n)
                    lbl.set_max_width_chars(n)
        self.fitted = self.get_width()
        return False


def markdown_table(rows):
    """A grid with a shaded header and light row rules (TableScroll: long cells wrap at the width there is, and a
    table wider than the chat scrolls sideways instead of squashing its columns)."""
    grid = Gtk.Grid(halign=Gtk.Align.START)
    grid.add_css_class("md-table")
    width = max(len(r) for r in rows)
    labels = []  # (label, its cell's text, a header cell, its column, a long column's): relinked as Signal finds
    for r, cells in enumerate(rows):  # names (relink_table), fitted to the width there is (TableScroll.fit)
        for c in range(width):
            cell = cells[c] if c < len(cells) else ""
            lbl = sel_label(f"<b>{_inline(cell)}</b>" if r == 0 else _inline(cell))
            long = len(cell) > TableScroll.MIN_CHARS
            labels.append((lbl, cell, r == 0, c, long))
            if long:  # (wraps at exactly this many characters: its minimum is its real width)
                lbl.set_width_chars(TableScroll.MIN_CHARS)
                lbl.set_max_width_chars(TableScroll.MIN_CHARS)
            else:
                lbl.set_wrap(False)
            lbl.set_valign(Gtk.Align.FILL)
            lbl.set_yalign(0)
            lbl.add_css_class("md-head" if r == 0 else "md-cell")
            grid.attach(lbl, c, r, 1, 1)
    return TableScroll(grid, labels)


def relink_table(scroll):
    """A table's cells drawn again with the links as they are now (its text is the same; what Signal knows may
    not be): only the cells whose markup changed are touched."""
    for lbl, cell, head, *_ in getattr(scroll, "cells", ()):
        markup = f"<b>{_inline(cell)}</b>" if head else _inline(cell)
        if markup != lbl.get_label():
            set_markup_safe(lbl, markup)


def set_markup_safe(label, markup):
    try:
        label.set_markup(markup)
    except GLib.Error:
        label.set_text(markup)


class UpdateBar(Gtk.Box):
    """At the foot of a sidebar, under a rule from what's listed above it: Update Available, while a new version's
    waiting to be restarted onto (what it waits for in its tooltip: say); clicked, on_click (restart now, asked)."""

    def __init__(self, on_click):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False)
        self.add_css_class("update-bar")
        self.append(Gtk.Separator())
        self.button = Gtk.Button(label="Update Available", halign=Gtk.Align.START, margin_start=6, margin_end=6,
                                 margin_top=4, margin_bottom=4)
        self.button.add_css_class("flat")
        self.button.connect("clicked", lambda *_: on_click())
        self.append(self.button)

    def say(self, waiting):
        """waiting: what the restart waits for (shown in the tooltip), or None: no update waiting, no bar."""
        self.set_visible(waiting is not None)
        if waiting is not None:
            self.button.set_tooltip_text("Waiting for:\n" + "\n".join(f"• {w}" for w in waiting) if waiting else
                                         "A new version is ready")


TASK_ENDINGS_SAID = {"completed": "finished", "failed": "failed", "killed": "stopped", "error": "failed"}


def task_note_line(note):
    """A background task's ending (a <task-notification>: baudelib.transcripts.task_notification's), as one quiet line,
    as Baude's chat says one whose call isn't drawn: (markup "<b>Background command</b> · what it was — finished (exit
    0)", plain text, whether it failed)."""
    status = note.get("status") or "completed"
    summary = html.unescape(note.get("summary") or "")
    what = "Agent" if summary.startswith(("Agent", "Subagent")) else "Background command"
    quoted = re.match(r'[^"]*"(.*)"', summary, re.S)
    desc = (quoted.group(1) if quoted else summary).strip().splitlines()[:1]
    desc = desc[0] if desc else "a background task"
    if len(desc) > 80:
        desc = desc[:79] + "…"
    ending = TASK_ENDINGS_SAID.get(status, status)
    if (code := re.search(r"exit code (\d+)", summary)) is not None:
        ending += f" (exit {code.group(1)})"
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
    return f"{n / 1_000_000:.1f}M".replace(".0M", "M") if n >= 1_000_000 else \
        f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


class ContextMeter(Gtk.LevelBar):
    """The context bar: WIDTH wide given the room, narrower as its column is (a width_request held it open)."""

    WIDTH = 90  # px

    def __init__(self, **kw):
        super().__init__(**kw)
        self.set_layout_manager(WidthLayout())

    def width_of(self, least, natural):
        return least, max(natural, self.WIDTH)


class ContextUsage(Gtk.Box):
    """How much of its context window a session's used, Baude's chat's and Meta's alike: "context 24k / 200k · 12%" and
    a bar, amber past 60%, red past 85%; its tip, what the tokens are (set_usage: get_context_usage's answer). Past the
    window ("context 12.4k / 8.2k · 151%"), the bar's full: over."""

    def __init__(self, **kw):
        super().__init__(spacing=8, tooltip_text="Context window usage", **kw)
        self.label = Gtk.Label(xalign=1, ellipsize=Pango.EllipsizeMode.END)
        self.bar = ContextMeter(min_value=0, max_value=1, value=0, valign=Gtk.Align.CENTER)
        self.bar.add_offset_value("ctx-mid", 0.6)
        self.bar.add_offset_value("ctx-high", 0.85)
        self.append(self.label)
        self.append(self.bar)
        self.reset()

    def set_usage(self, usage):
        used, total = usage.get("totalTokens") or 0, usage.get("maxTokens") or 0
        if not total:
            return
        self.over = used > total
        self.label.set_text(f"context {fmt_tokens(used)} / {fmt_tokens(total)} · {used / total:.0%}")
        self.bar.set_value(min(1.0, used / total))
        lines = [f"{c['name']}: {fmt_tokens(c.get('tokens', 0))}" for c in usage.get("categories") or []
                 if c.get("tokens") and c.get("kind") != "deferred"]
        self.set_tooltip_text("Context window usage\n" + "\n".join(lines))

    def reset(self):
        """No session's: nothing used yet."""
        self.over = False
        self.label.set_text("context —")
        self.bar.set_value(0)
        self.set_tooltip_text("Context window usage")


class Exhausted:
    """A chat whose conversation is more than its model's window holds (ContextUsage.over): "Context Exhausted" said
    over its box, in plain grey, and the box off, till a model with a bigger window's picked or the conversation's
    cleared. box: the Composer, already in its column; chat: what has the focus instead while it's off."""

    def __init__(self, chat, box):
        self.chat, self.box, self.on = chat, box, False
        self.label = Gtk.Label(label="Context Exhausted", xalign=0, margin_start=10, visible=False)
        self.label.add_css_class("dim-label")
        box.get_parent().insert_child_after(self.label, box.get_prev_sibling())

    def set(self, on, text="Context Exhausted"):
        """On or off; on, what's said (This model is unavailable, Meta's: its model's gone from the mesh)."""
        self.on = on
        self.label.set_text(text)
        self.label.set_visible(on)
        if on and self.box.input.is_focus():
            self.chat.grab_focus()
        self.box.input.set_cursor_visible(not on)
        self.box.set_sensitive(not on)


class FindBar(Gtk.SearchBar):
    """A search bar that, closed, is no width, as ShutBar."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.set_layout_manager(WidthLayout())

    def width_of(self, least, natural):
        if self.get_search_mode() or self.get_first_child().get_child_revealed():  # (its revealer)
            return least, natural
        return 0, 0


class WrapLabel(Gtk.Label):
    """A label that wraps, broken anywhere (WORD_CHAR), whose width is had without Pango breaking it at every point it
    can. GTK asks every label how narrow it can go (as a window works out how small it can be, each frame that lays
    anything out, and before it asks how tall it is at a width), and Pango answers by laying it out at no width at all:
    a line a word, each line shaping all that's left of the text. In a long chat that's most of the time a change of its
    text's size takes. Broken anywhere it can go down to about a character, whatever its text; how wide it would be on
    one line, its natural width, is one pass over the text."""

    NARROWEST = 24  # px: about a character, at any size that's read

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return Gtk.Label.do_measure(self, orientation, for_size)
        if for_size < 0 and self.get_wrap() and self.get_wrap_mode() == Pango.WrapMode.WORD_CHAR and \
                self.get_width_chars() < 0 and self.get_max_width_chars() < 0:
            layout = self.get_layout().copy()
            layout.set_width(-1)
            natural = math.ceil(layout.get_size()[0] / Pango.SCALE)
            return min(natural, self.NARROWEST), natural, -1, -1
        least, natural, _, _ = Gtk.Label.do_measure(self, orientation, for_size)
        return least, natural, -1, -1  # (chained up, a width comes with 0s for its baselines: GTK wants none)


class LinkLabel(WrapLabel):
    """A label whose Signal links each have their logo before them (ref_icon): git's, Kubernetes', a database's, a
    file's… The markup keeps a place for it (ref_icon_slot), which this makes the logo's width (a Pango shape) and
    draws the logo in, at the text's size. Copied, the text has only invisible characters there. A chip (chip_markup:
    the Git page's branch names, in the line a commit's subject's in) and a pull request's pill (pr_pill_markup) keep
    a place at each end, their margins (a pill's logo too), and this draws their patches behind them, a piece on each
    line if one's broken across two; a chip's name it gives its colour."""

    GAP = 3  # px between a logo and its name
    CHIP_PAD, CHIP_SCALE, CHIP_RADIUS = 5, 0.78, 4  # px either side of a chip's name, its size of the text's, and its
    # corners' px: .git-ref's, as a chip on its own had them (a pill's corners too)
    PILL_IN, PILL_OUT = 4, 5  # px: a pill's margin before its logo, and after its name
    pill_scale = 1.0  # (a pill's size of the text's: a log row's are a chip's size)

    def __init__(self, **kw):
        super().__init__(**kw)
        # ([(byte index, logo)], [(start, end, kind)], [(start, end, state)], the logos' size)
        self.icons, self.chips, self.pills, self.icon_px = [], [], [], 0

    def set_markup(self, markup):
        Gtk.Label.set_markup(self, markup)
        self.place_icons()

    def set_text(self, text):
        Gtk.Label.set_text(self, text)
        self.place_icons()

    def text_px(self):
        m = self.get_pango_context().get_metrics(None, None)
        return m.get_ascent(), m.get_descent(), max(10, round((m.get_ascent() + m.get_descent()) / Pango.SCALE * 0.78))

    def pill_px(self):
        """A pill's logo's size, px."""
        return max(8, round(self.icon_px * self.pill_scale))

    def place_icons(self):
        """Find the places kept for logos, chips' and pills' ends, and make each its width (again, when the text's size
        changed); a chip's name in its colour (again, when the theme's changed)."""
        text = self.get_text()
        if "\u2063" not in text:
            if self.icons or self.chips or self.pills:
                self.icons, self.chips, self.pills = [], [], []
                self.set_attributes(None)
            return False
        names = re.findall(r'font_family="baude-(icon|chip|pill)-(\w+)">\u2063</span>', self.get_label())
        spots = [len(text[:m.start()].encode()) for m in re.finditer("\u2063", text)]
        ascent, descent, px = self.text_px()
        self.icon_px = px
        attrs, icons, chips, pills, opened, fg = Pango.AttrList(), [], [], [], None, self.get_color()
        for at, (kind, name) in zip(spots, names):
            if kind == "icon":
                width = px + self.GAP
                icons.append((at, name))
            elif name == "end":  # (a chip's or a pill's end)
                width = self.CHIP_PAD if kind == "chip" else self.PILL_OUT
                if opened is not None and opened[0] == "chip":  # (a chip's name, between the two, in its colour)
                    chips.append((opened[1], at + 3, opened[2]))
                    color = chip_colors(opened[2], fg)[0]
                    for a in (Pango.attr_foreground_new(*(round(v * 65535) for v in (color.red, color.green, color.blue))),
                              Pango.attr_foreground_alpha_new(max(1, round(color.alpha * 65535)))):
                        a.start_index, a.end_index = opened[1] + 3, at
                        attrs.insert(a)
                elif opened is not None:
                    pills.append((opened[1], at + 3, opened[2]))
                opened = None
            else:  # (a start: a chip's, name its kind; a pill's, its logo's place too, name where it stands)
                width = self.CHIP_PAD if kind == "chip" else self.PILL_IN + self.pill_px() + self.GAP
                opened = (kind, at, name)
            r = Pango.Rectangle()
            r.x, r.y, r.width, r.height = 0, -ascent, width * Pango.SCALE, ascent + descent
            a = Pango.attr_shape_new(r, r)
            a.start_index, a.end_index = at, at + 3  # (U+2063: 3 bytes)
            attrs.insert(a)
        self.icons, self.chips, self.pills = icons, chips, pills
        self.set_attributes(attrs)
        return False

    def do_css_changed(self, change):
        Gtk.Label.do_css_changed(self, change)
        # zoomed: the places take the text's new size as it's styled, before the frame's layout (resized once drawn,
        # the text came at its new size and the logos a frame later). Chips: their colours, light or dark, too
        if self.chips or (self.icons or self.pills) and self.text_px()[2] != self.icon_px:
            self.place_icons()

    def do_snapshot(self, snapshot):
        if self.chips or self.pills:
            self.draw_patches(snapshot)
        Gtk.Label.do_snapshot(self, snapshot)
        if not (self.icons or self.pills):
            return
        # in the text's colour, as vectors (a bitmap of it, shown at another scale than it was made at, or zoomed into,
        # went blocky), its corner on one of the screen's pixels so its straight lines stay sharp
        layout, (ox, oy) = self.get_layout(), self.get_layout_offsets()
        scale = screen_scale(self)
        ascent, descent, _px = self.text_px()
        fg = self.get_color()
        native = self.get_native()
        ok, origin = self.compute_point(native, Graphene.Point().init(0, 0)) if native is not None else (False, None)
        tx, ty = native.get_surface_transform() if native is not None else (0.0, 0.0)
        bx, by = (origin.x + tx, origin.y + ty) if ok else (0.0, 0.0)  # (where the label is on the surface)

        def pixel(v, base):
            return round((base + v) * scale) / scale - base
        small = (ascent - descent) / Pango.SCALE / 2 * (1 - self.pill_scale)  # (a smaller pill's middle: lower)
        for at, icon, pad, color, px, lower in [(at, icon, 0, fg, self.icon_px, 0) for at, icon in self.icons] + \
                [(at, "merge", self.PILL_IN, pr_rgba(state, fg), self.pill_px(), small) for at, _end, state in self.pills]:
            size = max(8, round(px * scale))
            r = layout.index_to_pos(at)
            x = pixel(ox + r.x / Pango.SCALE + pad, bx)
            y = pixel(oy + (r.y + r.height / 2) / Pango.SCALE + lower - size / scale / 2, by)
            draw_ref_icon(snapshot, icon, x, y, size / scale, color)

    def draw_patches(self, snapshot):
        """The chips' and pills' patches, behind the text, their corners rounded as .git-ref's. A chip's as tall as
        its name's smaller text and a pixel more each way, in its kind's colour; a pill's the line's height less a
        little (a chip's, at a chip's size), in the colour of where its pull request stands, faint."""
        layout, (ox, oy) = self.get_layout(), self.get_layout_offsets()
        ascent, descent, _px = self.text_px()
        ascent, descent, fg = ascent / Pango.SCALE, descent / Pango.SCALE, self.get_color()

        def chip_box(scale):  # (top, height) of a patch, from the baseline
            return -(ascent * scale + 1), (ascent + descent) * scale + 2
        tall = round((ascent + descent) * 0.86)
        pill_box = chip_box(self.pill_scale) if self.pill_scale < 1 else (-(ascent - descent) / 2 - tall / 2, tall)
        patches = [(s, e, chip_colors(kind, fg)[1], chip_box(self.CHIP_SCALE)) for s, e, kind in self.chips]
        for s, e, state in self.pills:
            color = pr_rgba(state, fg)
            color.alpha = 0.09 if state in ("closed", "unknown") else 0.16
            patches.append((s, e, color, pill_box))
        for start, end, color, (top, h) in patches:
            for x, baseline, w in layout_runs(layout, start, end):
                rect = Graphene.Rect().init(ox + x, oy + baseline + top, w, h)
                rr = Gsk.RoundedRect()
                rr.init_from_rect(rect, self.CHIP_RADIUS)
                snapshot.push_rounded_clip(rr)
                snapshot.append_color(color, rect)
                snapshot.pop()


def pr_rgba(state, fg):
    """A pill's colour (PR_COLORS); for one whose state isn't known (yet, or at all: one older than those read), a
    link's, the accent's, as its number is (a link), so it's all one colour, logo, patch and number."""
    c = Gdk.RGBA()
    if not c.parse(PR_COLORS.get(state) or ""):
        c = accent_text_rgba()  # (@accent_color: a link's)
    return c


CHIP_TINTS = {"tag": ("#c88800", 0.16), "chat": ("#7f77dd", 0.18)}  # (.git-ref's: a tag's amber, this chat's violet)


def chip_colors(kind, fg):
    """A chip's name's colour and its patch's, as .git-ref has them: the accent's for a branch (local, head), the text's
    own faint for a remote's (fg: the text's colour), a tag's amber, this chat's violet. Light or dark, as it's now."""
    if kind in CHIP_TINTS:
        text = Gdk.RGBA()
        text.parse(CHIP_TINTS[kind][0])
        patch = text.copy()
        patch.alpha = CHIP_TINTS[kind][1]
    elif kind == "remote":
        text, patch = fg.copy(), fg.copy()
        text.alpha, patch.alpha = fg.alpha * 0.7, fg.alpha * 0.08
    else:
        text, patch = accent_text_rgba(), accent_rgba()  # (@accent_color, @accent_bg_color)
        patch.alpha = 0.14
    return text, patch


def chip_markup(text, kind):
    """text (plain) as a chip in a LinkLabel's markup: kind local, head (bold), remote, tag or chat, as .git-ref's are.
    Never broken across lines; a place kept at each end for its margins."""
    weight = "bold" if kind == "head" else "normal"
    return (f'<span allow_breaks="false"><span font_family="baude-chip-{kind}">\u2063</span>'
            f'<span size="{round(LinkLabel.CHIP_SCALE * 100)}%" weight="{weight}">{html.escape(text, quote=False)}'
            f'</span><span font_family="baude-chip-end">\u2063</span></span>')


def layout_runs(layout, start, end):
    """Where the text from byte start to end is on each line it's on: [(x, baseline, width)], px from the layout's
    corner."""
    out, it = [], layout.get_iter()
    while True:
        line = it.get_line_readonly()
        a, b = max(start, line.start_index), min(end, line.start_index + line.length)
        if a < b:
            _ink, logical = it.get_line_extents()
            x0, x1 = line.index_to_x(a, False), line.index_to_x(b, False)
            out.append(((logical.x + min(x0, x1)) / Pango.SCALE, it.get_baseline() / Pango.SCALE,
                        abs(x1 - x0) / Pango.SCALE))
        if not it.next_line():
            return out


def sel_label(markup="", css=None, mono=False):
    lbl = LinkLabel(xalign=0, wrap=True, selectable=True)
    lbl.connect("activate-link", on_label_link)
    lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if mono:
        lbl.add_css_class("mono")
    if css:
        lbl.add_css_class(css)
    try:
        lbl.set_markup(markup)
    except GLib.Error:
        lbl.set_text(markup)
    return lbl


def image_dims(data):
    """(width, height) from an image's header, without decoding it (PNG, GIF, JPEG, WebP)."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:4] == b"GIF8":
            return struct.unpack("<HH", data[6:10])
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            kind = data[12:16]
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if kind == b"VP8L":
                b = int.from_bytes(data[21:25], "little")
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
            if kind == b"VP8X":
                return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + length
    except (struct.error, IndexError):
        pass
    return None


def fit_size(w, h, max_edge):
    k = min(1.0, max_edge / max(w, h, 1))
    return max(1, round(w * k)), max(1, round(h * k))


def screen_scale(widget=None):
    """Device pixels per logical pixel where widget is shown: fractional (1.25 on a laptop panel)
    from its surface, else the integer scale factor, else the densest monitor."""
    try:
        native = widget.get_native() if widget is not None else None
        surface = native.get_surface() if native is not None else None
        if surface is not None and hasattr(surface, "get_scale"):
            return max(1.0, surface.get_scale())
        if widget is not None and widget.get_scale_factor():
            return float(widget.get_scale_factor())
        monitors = Gdk.Display.get_default().get_monitors()
        return max([1.0] + [monitors.get_item(i).get_scale() for i in range(monitors.get_n_items())])
    except (AttributeError, TypeError):
        return 1.0


def scaled_texture(data, max_edge, scale=1.0):
    """Texture from image bytes whose longest side is max_edge *logical* pixels on a screen of
    this scale: at 1.25x a 480 px image needs 600 real pixels, or GTK stretches it and it blurs.
    Shrinking is done in two steps: a cheap decode-time reduction to twice the target, then a
    high-quality (HYPER) filter; decode-time scaling alone is bilinear, which softens screenshots."""
    target = max_edge * scale
    loader = GdkPixbuf.PixbufLoader()
    loader.connect("size-prepared", lambda ld, w, h: ld.set_size(*fit_size(w, h, target * 2)))
    loader.write(data)
    loader.close()
    pb = loader.get_pixbuf()
    tw, th = fit_size(pb.get_width(), pb.get_height(), target)
    if (tw, th) != (pb.get_width(), pb.get_height()):
        pb = pb.scale_simple(tw, th, GdkPixbuf.InterpType.HYPER)
    fmt = Gdk.MemoryFormat.R8G8B8A8 if pb.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(pb.get_width(), pb.get_height(), fmt, pb.read_pixel_bytes(), pb.get_rowstride())


class WorkerPool:
    """A few daemon threads running submitted functions (concurrent.futures would do, but
    importing it pulls in logging and traceback: ~40 ms of start-up)."""

    def __init__(self, workers):
        self.jobs, self.workers, self.started = queue.SimpleQueue(), workers, 0

    def submit(self, fn):
        self.jobs.put(fn)
        if self.started < self.workers:
            self.started += 1
            threading.Thread(target=self._run, daemon=True, name="baude-worker").start()

    def _run(self):
        while True:
            job = self.jobs.get()
            try:
                job()
            except Exception:  # noqa: BLE001  (said, and the worker goes on: one gone, its pool did less and less)
                traceback.print_exc()
            job = None  # (held till the next, a picture's decoding kept the picture alive, pixels and all)


IMAGE_POOL = WorkerPool(2)


class Fade:
    """Claude's fade, for swapping what a view shows (the chat; a cluster, a kind, a namespace; a database, a schema):
    the widgets go lighter at once and stay so until the new content is in, then come back at full strength. Asked
    again while they're light, they stay light: no new fade, no flicker. hold() … done() is for a swap read in the
    background (its stale results are the caller's to drop); swap(action) for one that's instant, run after the fade
    so it's seen. Either way the last one asked for wins. Never back sooner than MS after it went, so a quick read
    is still seen to swap (at once with reduced motion, which GTK's transitions skip too). Only these widgets fade:
    the controls beside them stay as they are, to pick another while it reads."""
    MS = 130

    def __init__(self, *widgets):
        self.widgets, self.since, self.timer, self.next = widgets, None, None, None
        self.reading = self.swapping = False
        for w in widgets:
            w.add_css_class("switchable")

    @property
    def faded(self):
        return self.since is not None

    @staticmethod
    def animated():
        return Gtk.Settings.get_default().get_property("gtk-enable-animations")

    def out(self):
        if self.timer is not None:  # (on its way back: stays)
            GLib.source_remove(self.timer)
            self.timer = None
        if self.since is None:
            self.since = time.monotonic()
            for w in self.widgets:
                w.add_css_class("switching")

    def hold(self):
        """Faded until done(): a swap read in the background. An instant swap still waiting is dropped (this one's
        later)."""
        self.reading, self.next = True, None
        self.out()

    def done(self):
        """The new content is in (or its error is): back, unless an instant swap is still to run."""
        self.reading = False
        self.back()

    def swap(self, action):
        """Fade, run action (an instant swap) once faded, and come back. Asked again before it ran, or while it ran
        (a slow chat loading), the last one runs next, still faded."""
        self.next = action
        self.out()
        if not self.swapping:
            self.swapping = True
            GLib.timeout_add(self.MS if self.animated() else 1, self._swap)

    def _swap(self):
        action, self.next = self.next, None
        if action is not None:
            action()
        GLib.idle_add(self._swapped)  # (after the clicks that arrived while it was busy)
        return False

    def _swapped(self):
        if self.next is not None:  # another one was asked for meanwhile: swap again, still faded
            GLib.timeout_add(1, self._swap)
            return False
        self.swapping = False
        self.back()
        return False

    def back(self):
        if self.reading or self.swapping or self.since is None or self.timer is not None:
            return
        left = self.MS - (time.monotonic() - self.since) * 1000
        if left >= 1 and self.animated():
            self.timer = GLib.timeout_add(int(left), self._back)
        else:
            self._back()

    def _back(self):
        self.since = self.timer = None
        for w in self.widgets:
            w.remove_css_class("switching")
        return False


class Turning:
    """How something turns while a load runs and comes to rest when it ends (RefreshButton's icon), as a function of the
    time: at(now) says where it is, in turns (a whole number is upright), and how fast it's going, worked out from the
    legs it's on with nothing added up frame by frame, so a late frame or a while hidden costs it nothing. Each leg eases
    from one speed to another, with no jolt at either end, and the next starts where it ended, so whenever start and stop
    come there's no jump in where it is or how fast it's going. A load that ends part way round carries on to where it
    can slow down and stop upright; one that's over at once still gets up to speed and goes round once; one that starts
    while it's slowing picks up speed from there."""
    SPEED = 1.25  # (turns a second, while it reads)
    UP_S, DOWN_S = 0.25, 0.6  # (from standing to that speed; from that speed to standing)

    def __init__(self):
        self.loading, self.legs, self.rest = False, [], 0  # (a leg: when it starts, where, its two speeds, how long)

    def at(self, now):
        """(turns, turns a second) at time now, in seconds: a function of the time and the legs, and of nothing else."""
        for start, turns, v0, v1, seconds in self.legs:
            if now < start + seconds:
                dt = max(now - start, 0.0)
                u = dt / seconds  # (the speed goes by smoothstep; where it is, by what that adds up to)
                return turns + v0 * dt + (v1 - v0) * dt * u * u * (1 - u / 2), v0 + (v1 - v0) * u * u * (3 - 2 * u)
        return self.rest, 0.0

    def over(self, now):
        """At rest by then, with nothing more to draw."""
        return not self.loading and (not self.legs or now >= self.legs[-1][0] + self.legs[-1][4])

    def plan(self, start, turns, *legs):
        """Legs (speed at its start, at its end, how long) one after the other, from there."""
        self.legs = []
        for v0, v1, seconds in legs:
            if seconds > 0:
                self.legs.append((start, turns, v0, v1, seconds))
                start, turns = start + seconds, turns + seconds * (v0 + v1) / 2

    def start(self, now):
        if not self.loading:
            self.loading = True
            turns, speed = self.at(now)
            self.plan(now, turns, (speed, self.SPEED, self.UP_S * (1 - speed / self.SPEED)),
                      (self.SPEED, self.SPEED, math.inf))

    def stop(self, now):
        if self.loading:
            self.loading = False
            *up, (start, turns, *_) = self.legs  # (still getting up to speed: that goes on as it was)
            if now > start:
                up, start, turns = [], now, self.at(now)[0]
            slowing = self.SPEED * self.DOWN_S / 2  # (how far it goes while it slows)
            self.rest = math.ceil(turns + slowing)
            self.plan(start, turns, (self.SPEED, self.SPEED, (self.rest - turns - slowing) / self.SPEED),
                      (self.SPEED, 0.0, self.DOWN_S))
            self.legs[:0] = up


def turned_path(path, turned, x, y):
    """A path turned about (x, y), by that much of a turn: its own points moved, so it's drawn afresh as it now lies, as
    sharp at any angle and scale as it is upright. (A transform round it has the renderer draw it upright and turn the
    picture: softer, differently at each angle, so the circle of an icon shimmers.)"""
    cos, sin = math.cos(turned * math.tau), math.sin(turned * math.tau)
    builder = Gsk.PathBuilder.new()
    ways = {Gsk.PathOperation.LINE: builder.line_to, Gsk.PathOperation.QUAD: builder.quad_to,
            Gsk.PathOperation.CUBIC: builder.cubic_to}

    def moved(points):
        return [v for p in points for v in (x + (p.x - x) * cos - (p.y - y) * sin, y + (p.x - x) * sin + (p.y - y) * cos)]

    def add(op, points, _n, _weight, *_):
        if op == Gsk.PathOperation.CLOSE:
            builder.close()
        elif op == Gsk.PathOperation.MOVE:
            builder.move_to(*moved(points[:1]))
        else:
            ways[op](*moved(points[1:]))
        return True
    path.foreach(Gsk.PathForeachFlags.QUAD | Gsk.PathForeachFlags.CUBIC, add)
    return builder.to_path()


class RefreshButton(Gtk.Button):
    """A flat button with the refresh icon, for something read in the background: set_loading(True) as the read starts
    and the icon turns, in its full colour (it's still a button: a press meanwhile is the caller's to make harmless);
    set_loading(False) as it ends and it comes to rest upright (Turning). The icon's the theme's, and at rest it's just
    the button's own, drawn by GTK as any other is, to the pixel. A tick callback only while it's moving and mapped: at
    rest or hidden nothing runs, and shown again it's where the time says. With animations off it still turns, as a
    spinner does (it's what says something's happening), but it's upright at once when it ends, as it is if nobody's
    there to see it slow."""
    MIDDLE = 7.5, 8.5  # (in the 16 an icon's drawn in: the centre of Adwaita's circle, which so stays still, no wobble)

    def __init__(self, **kw):
        super().__init__(icon_name="view-refresh-symbolic", **kw)
        self.add_css_class("flat")
        self.turning, self.tick = Turning(), None
        self.connect("map", RefreshButton._mapping)
        self.connect("unmap", RefreshButton._mapping)

    def now(self):
        """The time as the frames have it, in seconds (with no frames to go by, the same clock's)."""
        clock = self.get_frame_clock()
        return (clock.get_frame_time() if clock is not None else GLib.get_monotonic_time()) / 1e6

    def set_loading(self, on):
        now = self.now()
        if on:
            self.turning.start(now)
        elif self.get_mapped() and Fade.animated():
            self.turning.stop(now)
        else:
            self.turning = Turning()
        self.sync(now)

    def sync(self, now):
        """Ticking if it's moving and can be seen, and not otherwise."""
        moving = self.get_mapped() and not self.turning.over(now)
        if moving and self.tick is None:
            self.tick = self.add_tick_callback(RefreshButton._tick)
        elif not moving and self.tick is not None:
            self.remove_tick_callback(self.tick)
            self.tick = None
        self.queue_draw()

    @staticmethod
    def _mapping(button):
        button.sync(button.now())

    @staticmethod
    def _tick(button, clock):
        button.frame(clock.get_frame_time() / 1e6)
        return GLib.SOURCE_CONTINUE  # (frame removes it, when that's it)

    def frame(self, now):
        """A frame at time now (a tick's whole job: a test can call it with a time of its own): drawn, the last if it's
        at rest by then."""
        self.queue_draw()
        if self.turning.over(now):
            self.sync(now)

    def do_snapshot(self, snapshot):
        turned = self.turning.at(self.now())[0] % 1
        if not turned:
            Gtk.Button.do_snapshot(self, snapshot)
            return
        icon = Gtk.Snapshot()
        Gtk.Button.do_snapshot(self, icon)
        if (node := icon.to_node()) is not None:
            self.draw_turned(snapshot, node, turned)

    def draw_turned(self, snapshot, node, turned):
        """What the icon draws, turned: its paths (turned_path), in what's round them, but not its clip, the square it's
        drawn in, which the circle touches and which would cut into it at an angle. Anything else (an icon that's a
        picture) is turned as it is."""
        if isinstance(node, Gsk.ClipNode):
            self.draw_turned(snapshot, node.get_child(), turned)
        elif isinstance(node, Gsk.TransformNode):
            snapshot.save()
            snapshot.transform(node.get_transform())
            self.draw_turned(snapshot, node.get_child(), turned)
            snapshot.restore()
        elif isinstance(node, Gsk.ContainerNode):
            for i in range(node.get_n_children()):
                self.draw_turned(snapshot, node.get_child(i), turned)
        elif isinstance(node, Gsk.FillNode):
            snapshot.append_fill(turned_path(node.get_path(), turned, *self.MIDDLE), node.get_fill_rule(),
                                 self.get_child().get_color())
        else:
            x, y = self.MIDDLE
            snapshot.save()
            snapshot.translate(Graphene.Point().init(x, y))
            snapshot.rotate(turned * 360)
            snapshot.translate(Graphene.Point().init(-x, -y))
            snapshot.append_node(node)
            snapshot.restore()


class FixedPicture(Gtk.Picture):
    """A picture that is always exactly `size` (logical pixels). A plain Gtk.Picture is
    height-for-width: inside a vertical box it is asked for its height at the box's full
    width and scales up to fill it, and its minimum size is 0x0, which some layouts hand it.
    Its texture is made by load(scale) at the screen's pixel density (so it's sharp on a scaled
    display), and made again if the window moves to a denser screen. In a scroller it holds that
    texture only while it's near what's shown (PictureWatch: a long chat's pictures are all
    mapped, wherever it's scrolled to); elsewhere it's made when the picture is first shown."""

    def __init__(self, size, load):
        super().__init__()
        self.size, self.factory, self.pending, self.loaded_scale, self.loading = size, load, True, 0.0, False
        self.wanted, self.watch, self.spot, self.surface_watch = False, None, None, None
        # (plain functions, given the picture by the signal: a closure over it would be a cycle PyGObject can't free)
        self.connect("map", FixedPicture._mapped)
        self.connect("unmap", FixedPicture._unmapped)
        self.connect("notify::scale-factor", FixedPicture.rescale)
        self.connect("realize", FixedPicture._watch_surface)
        self.connect("unrealize", FixedPicture._unwatch_surface)

    def _mapped(self):
        self.watch = PictureWatch.of(self)
        if self.watch is not None:
            self.watch.add(self)  # (decoded once it's found near the view)
        else:
            self.ensure_soon()

    def _unmapped(self):
        if self.watch is not None:
            self.watch.pictures.discard(self)
            self.watch = None

    def _watch_surface(self):
        native = self.get_native()
        surface = native.get_surface() if native is not None else None
        if surface is not None and surface.find_property("scale") is not None:  # fractional scales
            me = weakref.ref(self)  # (the window outlives its pictures: it mustn't keep them, and their pixels)
            self.surface_watch = surface, surface.connect("notify::scale", lambda *_: (p := me()) and p.rescale())

    def _unwatch_surface(self):
        if self.surface_watch is not None:
            surface, handler = self.surface_watch
            self.surface_watch = None
            surface.disconnect(handler)

    def rescale(self, *_):
        if not self.pending and screen_scale(self) > self.loaded_scale + 0.01:
            self.pending = True
            if self.get_mapped() and self.wanted:
                self.ensure_soon()

    def ensure_soon(self):
        """Decode in the background (a big screenshot takes ~60ms), then show it."""
        self.wanted = True
        if not self.pending or self.loading:
            return
        self.loading, scale = True, screen_scale(self)

        def work():
            if not self.wanted:  # (a fling carried it past before its turn came)
                GLib.idle_add(done, None, False)
                return
            try:
                tex = self.factory(scale)
            except GLib.Error:
                tex = None
            GLib.idle_add(done, tex, True)

        def done(tex, tried):
            self.loading = False
            if not (self.pending and self.wanted):  # loaded synchronously meanwhile, or gone far away again
                return False
            if tried:
                self.pending, self.loaded_scale = False, scale
                if tex is not None:
                    Gtk.Picture.set_paintable(self, tex)
            else:
                self.ensure_soon()  # (skipped, but it's back)
            return False
        IMAGE_POOL.submit(work)

    def release(self):
        """Far out of view: give the pixels back (decoded again when it comes near). Its size stays,
        so nothing around it moves."""
        self.wanted = False
        if Gtk.Picture.get_paintable(self) is not None:
            self.pending, self.loaded_scale = True, 0.0
            Gtk.Picture.set_paintable(self, None)

    def ensure(self):
        if self.pending:
            scale = screen_scale(self)
            self.pending, self.loaded_scale = False, scale
            Gtk.Picture.set_paintable(self, self.factory(scale))

    def get_paintable(self):
        self.ensure()
        return Gtk.Picture.get_paintable(self)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, orientation, for_size):
        n = self.size[0] if orientation == Gtk.Orientation.HORIZONTAL else self.size[1]
        return n, n, -1, -1


class PictureWatch:
    """Which of a scroller's pictures are near what it shows. A picture is decoded once it's within
    NEAR screens of the visible part and gives its pixels back past FAR (in between it stays as it
    is, so one at the edge isn't decoded over and over). One per scroller, joined by its pictures
    while they're mapped. It follows the scroller's vadjustment, and looks once a frame at most,
    after layout: scrolled, the positions it has are still good (they're in the content's
    coordinates); the content changed size, they're measured again. Never a walk of the widgets."""

    NEAR, FAR = 1.0, 3.0  # screens

    def __init__(self, scroller):
        self.scroller, self.pictures = weakref.ref(scroller), weakref.WeakSet()
        self.queued = self.moved = False
        adj = scroller.get_vadjustment()
        adj.connect("value-changed", self.queue)
        adj.connect("changed", self.relayout)  # (upper, page size: something grew, shrank, or the window did)

    @classmethod
    def of(cls, widget):
        """The watch of the scroller widget is in (made on first use), or None: in none, or in one
        whose child places its own children (a list or text view: positions in view coordinates,
        stale after each scroll). One that only scrolls sideways (a wide table) is looked past."""
        scroller = widget.get_ancestor(Gtk.ScrolledWindow)
        while scroller is not None and scroller.get_policy()[1] == Gtk.PolicyType.NEVER:
            parent = scroller.get_parent()
            scroller = parent.get_ancestor(Gtk.ScrolledWindow) if parent is not None else None
        if scroller is None or not isinstance(scroller.get_child(), Gtk.Viewport):
            return None
        watch = getattr(scroller, "picture_watch", None)
        if watch is None:
            watch = scroller.picture_watch = cls(scroller)
        return watch

    def add(self, pic):
        pic.spot = None  # (where it is: measured at the next look)
        self.pictures.add(pic)
        self.queue()

    def relayout(self, *_):
        self.moved = True
        self.queue()

    def queue(self, *_):
        scroller = self.scroller()
        if not self.queued and scroller is not None:
            self.queued = True
            scroller.add_tick_callback(self._frame)

    def _frame(self, *_):
        GLib.idle_add(self.look)  # (queued at the start of a frame, it runs once the frame is laid out)
        return GLib.SOURCE_REMOVE

    def look(self):
        self.queued = False
        scroller = self.scroller()
        view = scroller.get_child() if scroller is not None else None
        content = view.get_child() if isinstance(view, Gtk.Viewport) else None
        adj = scroller.get_vadjustment() if content is not None else None
        if adj is None or adj.get_page_size() <= 0:
            return False
        moved, self.moved = self.moved, False
        top, page = adj.get_value(), adj.get_page_size()
        for pic in list(self.pictures):
            if moved or pic.spot is None:
                ok, r = pic.compute_bounds(content)
                pic.spot = (r.get_y(), r.get_y() + r.get_height()) if ok and pic.get_width() else None
            if pic.spot is None:  # (not laid out, or not under the content after all: shown, as outside a scroller)
                pic.ensure_soon()
                continue
            away = max(top - pic.spot[1], pic.spot[0] - top - page)  # past the view's edge (< 0: in it)
            if away < page * self.NEAR:
                pic.ensure_soon()
            elif away > page * self.FAR:
                pic.release()
        return False


def image_widget(data, max_edge=480, path=None, name=None, url=None):
    """A picture whose natural size is bounded (a bare Gtk.Picture grows to the image size).
    Click to view it large; right-click to copy it or open it / its folder. A small button in
    the top-left corner opens it: in the browser (a web image, globe) or the default app (file)."""
    dims = image_dims(data)
    if not dims:  # (a format whose header image_dims can't read: decode it once to learn its size)
        tex = scaled_texture(data, 100_000)
        dims = (tex.get_width(), tex.get_height())
    # sized from the header now, decoded when shown (in a scroller: while near what's shown)
    pic = FixedPicture(size=fit_size(*dims, max_edge), load=lambda scale: scaled_texture(data, max_edge, scale))
    pic.add_css_class("md-image")
    acts = make_image_interactive(pic, data, path, name, url)
    corner = Gtk.Button(icon_name=globe_icon() if url else "document-open-symbolic",
                        tooltip_text="Open in browser" if url else "Open with default app",
                        halign=Gtk.Align.START, valign=Gtk.Align.START, margin_start=6, margin_top=6)
    corner.add_css_class("osd")
    corner.add_css_class("circular")
    corner.add_css_class("image-corner")
    corner.connect("clicked", lambda *_: acts.open_in_browser() if url else acts.open())
    box = Gtk.Overlay(child=pic, halign=Gtk.Align.START)
    box.add_overlay(corner)
    box.image_actions, box.picture, box.corner = acts, pic, corner
    return box


# Icons baude draws itself follow the theme's symbolic style (16x16 grid, 2px strokes as filled
# shapes), so GTK recolours them like any other icon and their weight matches the rest.
BAUDE_SHAPES = {"baude-dot-symbolic": '<circle cx="8" cy="8" r="4.5"/>'}  # filled, not line icons


BAUDE_ICONS = {
    # Claude's logo (REF_ICON_SVG's "claude", its three strokes filled bars here): Baude's chat tab, Record's chats
    "baude-claude-symbolic": ('<rect x="7.05" y="1.5" width="1.9" height="13" rx="0.95"/>'
                              '<rect x="7.05" y="1.5" width="1.9" height="13" rx="0.95" transform="rotate(60 8 8)"/>'
                              '<rect x="7.05" y="1.5" width="1.9" height="13" rx="0.95" transform="rotate(-60 8 8)"/>'),
    # open: a box, its top right corner left open for an arrow out of it
    "baude-open-symbolic": '<path d="M2 3h6v2H4v7h7V8h2v6H2z"/><path d="M9 1h6v6l-2.2-2.2-4.1 4.1-1.6-1.6 4.1-4.1z"/>',
    # Enter / return: a stem down from the top right, then left to an arrowhead
    "baude-return-symbolic": '<path d="M12 2h2v9H6.5V9H12z"/><path d="M2 10l5-4.5v9z"/>',
    # a map pin as a 2px outline (the theme's is a solid blob, much heavier than its line icons)
    "baude-map-symbolic": '<path fill-rule="evenodd" d="M8 1a5.5 5.5 0 0 1 5.5 5.5C13.5 10.4 8 15.5 8 15.5S2.5 '
                          '10.4 2.5 6.5A5.5 5.5 0 0 1 8 1zm0 2a3.5 3.5 0 0 0-3.5 3.5c0 2.3 2.3 5.2 3.5 6.5 '
                          '1.2-1.3 3.5-4.2 3.5-6.5A3.5 3.5 0 0 0 8 3z"/><circle cx="8" cy="6.5" r="1.5"/>',
    # Meta's: a spanner, filled (Material's build icon, Apache-2.0, its 24-px drawing scaled to 16), in the right bar
    "baude-meta-symbolic": '<g transform="scale(0.666667)"><path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4'
                           '-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 '
                           '0l2.3-2.3c.5-.4.5-1.1.1-1.4z"/></g>',
    # a log: four lines of text of different lengths (a pod's logs, in the Kubernetes view)
    "baude-logs-symbolic": '<rect x="1" y="1" width="14" height="2" rx="1"/><rect x="1" y="5" width="9" height="2" '
                           'rx="1"/><rect x="1" y="9" width="12" height="2" rx="1"/><rect x="1" y="13" width="7" '
                           'height="2" rx="1"/>',
}


# the Kubernetes view's sidebar categories, as Freelens draws them: its own nodes, workloads and helm (MIT), and
# the Material icons it uses for the rest (Apache-2.0): list, device_hub, storage, layers, access_time,
# security, extension. Their 24-px (Helm's 500) drawings, scaled to 16
KUBE_ICONS = {
    "baude-kube-nodes-symbolic": '<g transform="scale(0.666667)"><path d="m8.7 4v16h6.7v-16zm3.3 13.3c-.3 0-.6-.3-.6-.6s.3-.6.6-.6.6.3.6.6-.3.6-.6.6zm2-6.8h-4v-1.2h4zm0-2.4h-4v-1.2h4z"/><path d="m16.6 5.1v13.8h6.2v-13.8zm2.9 11.2c-.3 0-.6-.3-.6-.6s.3-.6.6-.6.6.3.6.6-.1.6-.6.6zm1.7-4.9h-3.2v-1.3h3.2zm0-2.4h-3.2v-1.3h3.2z"/><path d="m1.2 5.1v13.8h6.2v-13.8zm3.3 11.2c-.3 0-.6-.3-.6-.6s.3-.7.6-.7.6.3.6.6-.3.7-.6.7zm1.5-4.9h-3.2v-1.3h3.2zm0-2.4h-3.2v-1.3h3.2z"/></g>',
    "baude-kube-workloads-symbolic": '<g transform="scale(0.666667)"><path d="m12 7.47 4.25-2.47-4.25-2.44-4.25 2.44z"/><path d="m11.66 8.06-4.25-2.46v4.91l4.25 2.49z"/><path d="m12.34 8.06v4.94l4.25-2.45v-4.95z"/><path d="m16.93 15.94 4.26-2.45-4.26-2.49-4.25 2.45z"/><path d="m16.59 16.53-4.25-2.45v4.92l4.25 2.46z"/><path d="m17.26 16.53v4.91l4.26-2.44v-4.93z"/><path d="m7.07 15.94 4.25-2.45-4.25-2.49-4.25 2.49z"/><path d="m6.74 16.53-4.26-2.46v4.93l4.26 2.46z"/><path d="m7.41 16.53v4.91l4.25-2.44v-4.93z"/></g>',
    "baude-kube-helm-symbolic": '<g transform="scale(0.032)"><path d="M136.52938,121.13478c-.572-.54252-1.19462-1.12636-1.81-1.71761-12.61677-12.12115-22.38092-26.13637-28.279-42.702-1.65073-4.63637-2.89692-9.36515-2.67587-14.35871.021-.4739.01959-.94915.05226-1.42214.445-6.44594,4.75912-9.70322,11.05839-8.17669a27.325,27.325,0,0,1,5.73149,2.19653c6.89022,3.45538,12.5062,8.56359,17.67051,14.16571a112.52651,112.52651,0,0,1,21.722,33.42965,8.29635,8.29635,0,0,0,.38946.861c.07116.12855.22232.21282.55927.51883A176.357,176.357,0,0,1,241.968,79.06864c-.17651-.8761-.28195-1.54571-.44772-2.2a112.49436,112.49436,0,0,1-2.65292-36.95637,84.07478,84.07478,0,0,1,4.44444-21.76378,31.32555,31.32555,0,0,1,5.47651-10.17139,15.687,15.687,0,0,1,3.16336-2.82149,7.026,7.026,0,0,1,8.03255-.056,17.27854,17.27854,0,0,1,5.8402,6.73185A53.05435,53.05435,0,0,1,271.08657,26.508a112.50469,112.50469,0,0,1,2.12263,33.00356,95.59806,95.59806,0,0,1-3.49052,19.91081c7.12171,1.31193,14.20955,2.32869,21.1473,3.97713a186.37909,186.37909,0,0,1,20.44069,6.0033A188.31671,188.31671,0,0,1,331.0769,97.9721c6.34538,3.16433,12.38553,6.94066,18.71757,10.53829.20571-.433.50439-.94982.706-1.50212A108.65959,108.65959,0,0,1,383.40128,60.246a37.75787,37.75787,0,0,1,11.822-6.883,17.24558,17.24558,0,0,1,3.67827-.84512c6.264-.71729,8.89351,3.2244,9.35653,7.93183a29.94372,29.94372,0,0,1-.77381,10.35466A87.90551,87.90551,0,0,1,396.75426,95.492c-6.79016,10.97178-14.85015,20.85494-25.09307,28.83042-.30234.2354-.56784.51814-1.07988.99029a177.77993,177.77993,0,0,1,26.59293,30.88244,10.96227,10.96227,0,0,1-1.689.29762c-10.59546.015-21.1911-.01829-31.78622.04607a4.004,4.004,0,0,1-3.17246-1.69,147.87522,147.87522,0,0,0-88.17776-46.54846,143.35862,143.35862,0,0,0-30.27955-1.16923,146.40735,146.40735,0,0,0-82.53728,31.81054,140.06663,140.06663,0,0,0-16.97616,15.84186,4.72839,4.72839,0,0,1-3.86326,1.75742c-10.12056-.07028-20.24188-.035-30.36293-.03495h-2.15212c.618-2.408,6.84026-10.93786,13.88352-18.55281C125.31349,132.2744,130.87768,126.8839,136.52938,121.13478Z"/><path d="M394.52934,347.9123a176.63854,176.63854,0,0,1-23.97343,27.16338c.70941.59068,1.28594,1.07041,1.86212,1.55057A108.31456,108.31456,0,0,1,406.10131,424.772a34.61831,34.61831,0,0,1,2.202,14.42026,14.88544,14.88544,0,0,1-.74786,3.69206,7.20762,7.20762,0,0,1-8.15793,5.02308,22.23329,22.23329,0,0,1-6.76276-2.00629,51.23237,51.23237,0,0,1-9.18151-5.8151,107.59183,107.59183,0,0,1-32.936-46.7072c-.18746-.51334-.39218-1.0204-.72243-1.87691a194.65008,194.65008,0,0,1-25.01223,14.00774,181.66925,181.66925,0,0,1-26.6869,9.72442,187.55649,187.55649,0,0,1-28.3045,5.38805c.16807.84015.26446,1.5098.43745,2.15907a109.172,109.172,0,0,1,2.9708,36.44311,80.804,80.804,0,0,1-4.42286,22.4773,78.24971,78.24971,0,0,1-4.16475,8.74473,13.39,13.39,0,0,1-2.33865,2.97083c-3.98009,4.109-8.73225,4.144-12.61157-.07366a27.28012,27.28012,0,0,1-3.907-5.61776c-3.07685-5.77569-4.6604-12.056-5.791-18.46021a116.86329,116.86329,0,0,1-1.35893-26.465,94.4795,94.4795,0,0,1,2.88466-19.18513c.14009-.53269.268-1.0696.37134-1.61034.02629-.13754-.06342-.2973-.17067-.73825a176.12108,176.12108,0,0,1-80.96855-24.99386c-.40992.90921-.76206,1.67473-1.10168,2.44579a110.47729,110.47729,0,0,1-30.90112,41.42041,38.16071,38.16071,0,0,1-12.04706,6.95909,12.08987,12.08987,0,0,1-6.51516.70023,7.11858,7.11858,0,0,1-5.40329-4.4892c-1.41614-3.424-1.16526-6.985-.68438-10.51691a55.45267,55.45267,0,0,1,4.30768-14.25037A112.49985,112.49985,0,0,1,134.88761,380.779c.459-.43461.92981-.857,1.38087-1.29954a3.76029,3.76029,0,0,0,.36534-.65529,178.90466,178.90466,0,0,1-28.469-31.31672c.98458-.08018,1.64327-.18,2.30205-.1806,10.51436-.00976,21.029.02736,31.54284-.04358a4.70554,4.70554,0,0,1,3.70344,1.62615,146.94611,146.94611,0,0,0,39.40276,28.88494,139.94667,139.94667,0,0,0,49.70395,14.77367q70.68048,6.87067,121.59971-42.85452a7.64571,7.64571,0,0,1,5.99261-2.44347c9.80139.12121,19.60512.04986,29.408.04986h2.53353Z"/><path d="M350.73576,197.76167c2.78711,0,5.47037.18919,8.11487-.0501,2.9951-.271,5.139.8001,7.32354,2.81308,12.61275,11.62214,25.35732,23.10129,38.05863,34.62729.63855.57948,1.29039,1.14432,2.1101,1.8701.76445-.65718,1.48119-1.24318,2.16483-1.8655Q428.14636,217.27914,447.756,199.37a5.44772,5.44772,0,0,1,4.20358-1.64576c3.21888.13038,6.44673.03746,9.8412.03746V303.13036c-1.72309.50368-24.8756.60366-27.63911.0611v-53.362l-.53656-.25427c-9.01079,8.21784-18.0216,16.43564-27.23786,24.8408-9.22584-8.31119-18.34194-16.52347-27.458-24.73577l-.52449.19192c-.023,4.45359-.00774,8.90828-.01046,13.36233q-.00408,6.63942-.00058,13.27882v26.87211h-27.428C350.45161,301.61243,350.21289,203.72342,350.73576,197.76167Z"/><path d="M97.63436,197.88229h27.26325c.55054,1.75251.65821,102.97139.09469,105.525H97.70468c-.15021-6.70344-.04736-13.38394-.06622-20.06131-.01871-6.62261-.0041-13.24531-.0041-20.03958H63.84679V303.047c-2.05946.61518-25.33374.67417-27.64758.123V197.89436H63.73742v37.18876c1.96793.56844,30.92319.67339,33.872.12942.00818-2.97713.02-6.02646.024-9.0758q.0063-4.7433.00094-9.4866,0-4.625,0-9.25C97.63437,204.32179,97.63436,201.24346,97.63436,197.88229Z"/><path d="M157.5757,303.3683V198.1946c1.617-.52913,61.545-.73586,65.46219-.20457v22.41432c-.87869.063-1.7857.18341-2.69279.18454q-16.0086.01994-32.01725.00881l-2.96767,0v17.43353H218.7067V261.2229H185.65838c-.55339,1.98509-.70531,15.81681-.25622,19.64583.84488.05679,1.75.16952,2.6553.17059q16.00862.01863,32.01727.00822h2.96945V303.3683Z"/><path d="M254.28291,303.40855c-.50074-2.82232-.39948-103.60181.09747-105.51735h27.16208v77.76453c1.17173.06084,2.09122.14921,3.01077.15005q16.128.01468,32.2561.00658c.92562,0,1.85125,0,2.90733,0v27.59622Z"/></g>',
    "baude-kube-config-symbolic": '<g transform="scale(0.666667)"><path d="M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z"/></g>',
    "baude-kube-network-symbolic": '<g transform="scale(0.666667)"><path d="m17 16-4-4V8.82C14.16 8.4 15 7.3 15 6c0-1.66-1.34-3-3-3S9 4.34 9 6c0 1.3.84 2.4 2 2.82V12l-4 4H3v5h5v-3.05l4-4.2 4 4.2V21h5v-5h-4z"/></g>',
    "baude-kube-storage-symbolic": '<g transform="scale(0.666667)"><path d="M2 20h20v-4H2v4zm2-3h2v2H4v-2zM2 4v4h20V4H2zm4 3H4V5h2v2zm-4 7h20v-4H2v4zm2-3h2v2H4v-2z"/></g>',
    "baude-kube-namespaces-symbolic": '<g transform="scale(0.666667)"><path d="m11.99 18.54-7.37-5.73L3 14.07l9 7 9-7-1.63-1.27-7.38 5.74zM12 16l7.36-5.73L21 9l-9-7-9 7 1.63 1.27L12 16z"/></g>',
    "baude-kube-events-symbolic": '<g transform="scale(0.666667)"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8z"/><path d="M12.5 7H11v6l5.25 3.15.75-1.23-4.5-2.67z"/></g>',
    "baude-kube-users-symbolic": '<g transform="scale(0.666667)"><path d="M12 1 3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 10.99h7c-.53 4.12-3.28 7.79-7 8.94V12H5V6.3l7-3.11v8.8z"/></g>',
    "baude-kube-crds-symbolic": '<g transform="scale(0.666667)"><path d="M20.5 11H19V7c0-1.1-.9-2-2-2h-4V3.5a2.5 2.5 0 0 0-5 0V5H4c-1.1 0-1.99.9-1.99 2v3.8H3.5c1.49 0 2.7 1.21 2.7 2.7s-1.21 2.7-2.7 2.7H2V20c0 1.1.9 2 2 2h3.8v-1.5c0-1.49 1.21-2.7 2.7-2.7 1.49 0 2.7 1.21 2.7 2.7V22H17c1.1 0 2-.9 2-2v-4h1.5a2.5 2.5 0 0 0 0-5z"/></g>',
    # the known custom resources' categories, Material's too: open_in_full (KEDA's scaling), lock (cert-manager's
    # certificates), show_chart (the Prometheus Operator's monitoring)
    "baude-kube-scaling-symbolic": '<g transform="scale(0.666667)"><path d="M21 11V3h-8l3.29 3.29-10 10L3 13v8h8l-3.29-3.29 10-10z"/></g>',
    "baude-kube-certificates-symbolic": '<g transform="scale(0.666667)"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/></g>',
    "baude-kube-monitoring-symbolic": '<g transform="scale(0.666667)"><path d="M3.5 18.49l6-6.01 4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z"/></g>',
}


def ensure_icons():
    """Write baude's icons where the icon theme looks for them, and put Adwaita's behind the desktop's theme
    (once per run)."""
    display = Gdk.Display.get_default()
    if display is None or getattr(ensure_icons, "done", False):
        return
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    folder = os.path.join(base, "baude", "icons")
    theme = Gtk.IconTheme.get_for_display(display)
    try:
        os.makedirs(folder, exist_ok=True)
        for name, body in {**BAUDE_ICONS, **BAUDE_SHAPES, **KUBE_ICONS}.items():
            svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
                   f'<g fill="#2e3436">{body}</g></svg>')
            path = os.path.join(folder, f"{name}.svg")
            try:
                with open(path) as f:
                    same = f.read() == svg
            except OSError:
                same = False
            if not same:
                with open(path, "w") as f:
                    f.write(svg)
    except OSError as e:  # (a cache that can't be written: the app starts all the same, Adwaita's icons still found)
        print("baude: couldn't write its icons:", e, file=sys.stderr)
    theme.add_search_path(folder)
    # GTK 4 looks only in the desktop's icon theme and hicolor, not Adwaita, so under another theme (Breeze,
    # Papirus…) an Adwaita name that theme lacks draws nothing. Adwaita's own folders, searched after every theme,
    # fill those in; a name the desktop's theme has is still drawn from it.
    for root in theme.get_search_path():
        for sub in sorted(glob.glob(os.path.join(root, "Adwaita", "symbolic", "*"))):
            if os.path.isdir(sub):
                theme.add_search_path(sub)
    ensure_icons.done = True


def globe_icon():
    """A globe where the icon theme has one (Yaru does); Adwaita only has the browser compass."""
    ensure_icons()
    display = Gdk.Display.get_default()
    if display is not None and Gtk.IconTheme.get_for_display(display).has_icon("globe-symbolic"):
        return "globe-symbolic"
    return "web-browser-symbolic"


# ------------------------------------------------------------- composer --
# The box a message is written in (Baude's, Record's): its markdown shown as it's written.

HEADING_SCALE = {1: 1.44, 2: 1.2}  # Pango's x-large / large; h3+ are bold at body size


WRAP_MARKS = {"*": "**", "`": "`", "/": "*"}  # (a selection, and one of these typed: bold, code, italic)
_LINE_LEAD = re.compile(r"[ \t]*(?:(?:[-*+•◦]|\d+[.)])[ \t]+(?:\[[ xX]\][ \t]+)?|#{1,6}[ \t]+|>[ \t]?)?")


def wrap_selection(buf, ch):
    """A selection, and *, ` or / typed (ch): what's selected made bold (**…**), code (`…`) or italic (*…*), as
    Markdown has them, each of its lines on its own (a list's marker, a box, a heading's #s left out), and still
    selected, so another can go round it too (* then /: bold italic). Marked so already (the marks just outside it):
    they're taken off. False (nothing done: the key types as ever) for another key, or nothing selected but space."""
    mark = WRAP_MARKS.get(ch)
    if mark is None or not buf.get_has_selection():
        return False
    start, end = buf.get_selection_bounds()
    a, b, n = start.get_offset(), end.get_offset(), len(mark)
    full = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
    if "\n" not in full[a:b]:  # (marked so already: unmarked)
        run = lambda i, step: next((k for k in range(8) if not (0 <= i + step * k < len(full)) or  # noqa: E731
                                    full[i + step * k] != mark[0]), 8)
        left, right = run(a - 1, -1), run(b, 1)
        if (left >= 1 and right >= 1) if mark == "`" else (left >= 2 and right >= 2) if mark == "**" else \
                (left in (1, 3) and right in (1, 3)):
            buf.begin_user_action()
            buf.delete(buf.get_iter_at_offset(b), buf.get_iter_at_offset(b + n))
            buf.delete(buf.get_iter_at_offset(a - n), buf.get_iter_at_offset(a))
            buf.end_user_action()
            buf.select_range(buf.get_iter_at_offset(a - n), buf.get_iter_at_offset(b - n))
            buf.just_wrapped = (a - n, b - n)
            return True
    pieces, at = [], a
    for line in full[a:b].split("\n"):
        s, e = at, at + len(line)
        at = e + 1
        if s == full.rfind("\n", 0, s) + 1:  # (from the line's start: its marker, #s or > left out)
            s = max(s, min(e, _LINE_LEAD.match(full, s).end()))
        while s < e and full[s].isspace():
            s += 1
        while e > s and full[e - 1].isspace():
            e -= 1
        if s < e:
            pieces.append((s, e))
    if not pieces:
        return False
    buf.begin_user_action()
    for s, e in reversed(pieces):
        buf.insert(buf.get_iter_at_offset(e), mark)
        buf.insert(buf.get_iter_at_offset(s), mark)
    buf.end_user_action()
    first, last = pieces[0][0] + n, pieces[-1][1] + n * (2 * len(pieces) - 1)
    buf.select_range(buf.get_iter_at_offset(first), buf.get_iter_at_offset(last))
    buf.just_wrapped = (first, last)
    return True


def leave_wrapped(buf, keyval, state):
    """Left or Right on its own with what wrap_selection just formatted still selected: the caret outside its marks, all
    that are round it (before the first, or after the last), so what's typed next is plain. True if so. A selection made
    any other way collapses as ever, to its own end."""
    if keyval not in (Gdk.KEY_Left, Gdk.KEY_Right) or not buf.get_has_selection() or \
            state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK |
                     Gdk.ModifierType.ALT_MASK):
        return False
    start, end = buf.get_selection_bounds()
    a, b = start.get_offset(), end.get_offset()
    if getattr(buf, "just_wrapped", None) != (a, b):
        return False
    buf.just_wrapped = None
    full = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
    while mark := next((m for m in ("**", "`", "*") if full[max(a - len(m), 0):a] == m and full[b:b + len(m)] == m), None):
        a, b = a - len(mark), b + len(mark)
    buf.place_cursor(buf.get_iter_at_offset(b if keyval == Gdk.KEY_Right else a))
    return True


def no_text_drag(view):
    """A text view whose selection isn't dragged away: GTK's, pressed in and dragged, moves the text, which
    surprises. A press in the selection puts the caret there, as a press anywhere else does, so a drag selects
    anew (Shift, Ctrl, ⌘ or Alt held, and a double or triple click: as GTK has them). view.press_at(x, y): the
    same, for a press at (x, y) in the view."""
    def press_at(x, y):
        buf = view.get_buffer()
        if not buf.get_has_selection():
            return False
        bx, by = view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        ok, at = view.get_iter_at_location(bx, by)
        start, end = buf.get_selection_bounds()
        if not ok or not start.compare(at) <= 0 < end.compare(at):
            return False
        buf.place_cursor(at)
        return True

    def pressed(gesture, n, x, y):
        held = Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | \
            Gdk.ModifierType.ALT_MASK
        if n == 1 and gesture.get_current_button() == Gdk.BUTTON_PRIMARY and \
                not gesture.get_current_event_state() & held:
            press_at(x, y)
    click = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)  # (before the view's own)
    click.connect("pressed", pressed)
    view.add_controller(click)
    view.press_at = press_at
    return view


def word_stop(text, at, forward=True, hidden=lambda _i: False):
    """Where Option (Ctrl elsewhere) and an arrow goes from at: to the end of the next word, or back to the start of the
    last, and also to each mark that follows a word where nothing else does (a closing }, a full stop at a line's end), so
    a line can be selected to exactly there. A mark inside a word (orders-api, a.b) or leading one (@{, an opening quote)
    isn't a stop, nor is what's the same over again (... **). hidden(i): the character at i is unseen, as if it weren't
    there; a word's stop is outside what's unseen around it, so a link's marks go with it."""
    n = len(text)

    def seen(i, step):
        while 0 <= i < n and hidden(i):
            i += step
        return i

    def word(i):
        if not 0 <= i < n:
            return False
        if text[i] in "'’":  # (don't, inside a word)
            return 0 < i < n - 1 and text[i - 1].isalnum() and text[i + 1].isalnum()
        return text[i].isalnum() or text[i] == "_"

    def space(i):
        return 0 <= i < n and text[i].isspace()

    if forward:
        i = seen(at, 1)
        while True:
            while space(i):
                i = seen(i + 1, 1)
            if i >= n:
                return n
            if word(i):
                while word(i):
                    i = seen(i + 1, 1)
                return i
            j = i
            while j < n and not space(j) and not word(j):
                j = seen(j + 1, 1)
            if word(j):  # (marks before a word, or inside one: on to its end)
                i = j
                continue
            mark, i = text[i], seen(i + 1, 1)
            while i < n and text[i] == mark:
                i = seen(i + 1, 1)
            return i

    def start(i):  # (and any unseen before it, as what they mark is what's being reached)
        while i > 0 and hidden(i - 1):
            i -= 1
        return i
    k = seen(at - 1, -1)
    while True:
        while space(k):
            k = seen(k - 1, -1)
        if k < 0:
            return 0
        if word(k):
            while word(seen(k - 1, -1)):
                k = seen(k - 1, -1)
            return start(k)
        j, first = k, k
        while j >= 0 and not space(j) and not word(j):
            first, j = j, seen(j - 1, -1)
        after = seen(k + 1, 1)
        if word(after):  # (caret in or just before a word: marks inside it are passed, ones leading it the stop)
            if word(j):
                k = j
                continue
            return start(first)
        mark = text[k]
        while seen(k - 1, -1) >= 0 and text[seen(k - 1, -1)] == mark:
            k = seen(k - 1, -1)
        return k  # (just before it: an unseen } before a full stop stays with what it closes)


def word_steps(view):
    """A text view's word steps by word_stop, its selection going with them with Shift, what its "hidden" tag hides (the
    notes' markup) not counted."""
    def step(view, movement, count, extend):
        if movement != Gtk.MovementStep.WORDS or not count:
            return
        buf = view.get_buffer()
        tag = buf.get_tag_table().lookup("hidden")
        hidden = (lambda i: buf.get_iter_at_offset(i).has_tag(tag)) if tag is not None else (lambda _i: False)
        text = buf.get_slice(buf.get_start_iter(), buf.get_end_iter(), True)
        at = buf.get_iter_at_mark(buf.get_insert()).get_offset()
        for _ in range(abs(count)):
            at = word_stop(text, at, count > 0, hidden)
        where = buf.get_iter_at_offset(at)
        if extend:
            buf.move_mark(buf.get_insert(), where)
        else:
            buf.place_cursor(where)
        view.reset_im_context()
        view.scroll_mark_onscreen(buf.get_insert())
        view.stop_emission_by_name("move-cursor")  # (GTK's: Pango's words, to the next line's past these)
    view.connect("move-cursor", step)


_LIST_ITEM = re.compile(r"(\s*)([-*+•◦]|\d+[.)])\s")


def swap_list_item(lines, at, by):
    """The list item the line at is in, with all under it (its sub-items, the lines going on with it), swapped with its
    sibling above (by -1) or below (by 1): the nearest item that way at its level, with nothing shallower, no blank line
    and no other text between, so it never leaves its list. Numbers stay in order, the items taking each other's.
    (the new lines, the first and past the last line replaced, where the line at is now), or None: not in an item, or
    no sibling that way. In a code block it's not an item, whatever it looks like."""
    fenced = sum(bool(re.match(r"\s*(```|~~~)", line)) for line in lines[:at + 1]) % 2 == 1

    def indent(i):
        return len(_LIST_ITEM.match(lines[i]).group(1).expandtabs(4))

    def goes_on(i):  # (a line going on with the item above: spaces first, and not an item itself)
        return lines[i].startswith((" ", "\t")) and bool(lines[i].strip()) and not _LIST_ITEM.match(lines[i])

    def past(i):
        end = i + 1
        while end < len(lines) and (goes_on(end) or _LIST_ITEM.match(lines[end]) and indent(end) > indent(i)):
            end += 1
        return end
    start = at
    while start >= 0 and goes_on(start):
        start -= 1
    if fenced or start < 0 or not _LIST_ITEM.match(lines[start]):
        return None
    end = past(start)
    if by > 0:
        if end >= len(lines) or not _LIST_ITEM.match(lines[end]) or indent(end) != indent(start):
            return None
        one, two, three = start, end, past(end)
    else:
        above = start - 1
        while above >= 0 and (goes_on(above) or _LIST_ITEM.match(lines[above]) and indent(above) > indent(start)):
            above -= 1
        if above < 0 or not _LIST_ITEM.match(lines[above]) or indent(above) != indent(start):
            return None
        one, two, three = above, start, end
    first, second = lines[one:two], lines[two:three]
    n1, n2 = _LIST_ITEM.match(first[0]).group(2), _LIST_ITEM.match(second[0]).group(2)
    if n1[0].isdigit() and n2[0].isdigit():  # (each takes the other's number: they stay in order)
        i1, i2 = len(_LIST_ITEM.match(first[0]).group(1)), len(_LIST_ITEM.match(second[0]).group(1))
        first[0], second[0] = first[0][:i1] + n2 + first[0][i1 + len(n1):], second[0][:i2] + n1 + second[0][i2 + len(n2):]
    moved = at - start + (one if by < 0 else one + len(second))
    return second + first, one, three, moved


class ComposerView(Gtk.TextView):
    """The input TextView. Code blocks get one rounded background drawn behind the text:
    GTK doesn't paint a paragraph background on empty lines, so per-line backgrounds made
    a new, empty code line flash unshaded until its first character was typed."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.code_blocks = []  # (first line, last line) of each code block, fences included
        self.block_rgba = Gdk.RGBA()

    def do_snapshot_layer(self, layer, snapshot):
        if layer != Gtk.TextViewLayer.BELOW_TEXT or not self.code_blocks:
            return
        buf = self.get_buffer()
        visible = self.get_visible_rect()
        x = self.get_left_margin() + 2
        width = max(0, visible.x + visible.width - self.get_right_margin() + 8 - x)
        for first, last in self.code_blocks:
            y0, _ = self.get_line_yrange(buf.get_iter_at_line(first)[1])
            y1, h1 = self.get_line_yrange(buf.get_iter_at_line(last)[1])
            rect = Graphene.Rect().init(x, y0, width, y1 + h1 - y0)
            rounded = Gsk.RoundedRect()
            rounded.init_from_rect(rect, 6)  # in place: the returned object is not usable from Python
            snapshot.push_rounded_clip(rounded)
            snapshot.append_color(self.block_rgba, rect)
            snapshot.pop()


class ComposerMarkdown:
    """Live preview for the input TextView, styled like messages in the chat: real bullets,
    headings at the chat's sizes, bold/italic/inline code, shaded code blocks. Markdown
    symbols (#, **, `) are hidden except on the line being edited. Bullets are stored as
    "•" while editing and turned back into "-" by to_markdown() when the message is sent.
    Also continues lists on Shift+Enter."""

    def __init__(self, view):
        self.view, self.buf = view, view.get_buffer()
        t = self.buf.create_tag
        left = view.get_left_margin()
        t("code", family="monospace", scale=code_scale(), left_margin=left + 8)
        t("fence", family="monospace", scale=code_scale(), left_margin=left + 8)
        t("inline", family="monospace", scale=code_scale())
        t("bold", weight=Pango.Weight.BOLD)
        t("italic", style=Pango.Style.ITALIC)
        for level, scale in HEADING_SCALE.items():
            t(f"h{level}", weight=Pango.Weight.BOLD, scale=scale)
        t("h3", weight=Pango.Weight.BOLD)
        t("marker", weight=Pango.Weight.BOLD)
        t("syntax")
        t("hidden", invisible=True)
        for depth in range(6):  # hanging indent so wrapped lines align with the item text
            t(f"item{depth}", left_margin=left + 6 + INDENT_STEP * depth + 18, indent=-18)
        self.pending, self.cursor_line, self.styling = False, -1, False
        # style synchronously so a new line is drawn styled in the same frame it appears
        # (an idle restyle let it flash unstyled first); bullet conversion edits text, so
        # it stays in an idle callback
        self.buf.connect_after("changed", lambda *_: (self.restyle_tags(), self.queue()))
        self.buf.connect("notify::cursor-position", self.on_cursor)

    def colours(self):
        """Tag colours follow the view's text colour, so they work in light and dark themes."""
        fg = self.view.get_color()
        def tone(alpha):
            c = fg.copy()
            c.alpha = alpha
            return c
        tags = self.buf.get_tag_table()
        if isinstance(self.view, ComposerView):
            self.view.block_rgba = tone(0.07)  # same shade as the chat's code blocks
        tags.lookup("fence").set_property("foreground-rgba", tone(0.45))
        tags.lookup("inline").set_property("background-rgba", tone(0.10))
        tags.lookup("marker").set_property("foreground-rgba", tone(0.7))
        tags.lookup("syntax").set_property("foreground-rgba", tone(0.4))

    def on_cursor(self, *_):
        line = self.buf.get_iter_at_mark(self.buf.get_insert()).get_line()
        if line != self.cursor_line:  # symbols show/hide as the cursor changes lines
            self.restyle_tags()

    def queue(self, *_):
        if not self.pending:
            self.pending = True
            GLib.idle_add(self.restyle)

    def _tag(self, name, line, start, end):
        it_s = self.buf.get_iter_at_line_offset(line, start)[1]
        it_e = self.buf.get_iter_at_line_offset(line, end)[1]
        self.buf.apply_tag_by_name(name, it_s, it_e)

    def _tag_line(self, name, line):
        """Tag a whole line including its line break: paragraph styling (margins, background)
        comes from the tags on a line's first character, which for an empty line is the break."""
        it_s = self.buf.get_iter_at_line(line)[1]
        it_e = it_s.copy()
        it_e.forward_line()
        self.buf.apply_tag_by_name(name, it_s, it_e)

    def _spans(self, text):
        return fence_spans(text.split("\n"), tildes=False,
                           typing=(self.buf.get_iter_at_mark(self.buf.get_insert()).get_line(),))

    def _block_here(self):
        """The code block the caret's in (on an opening fence, or inside): (first line, last line, closed), else None."""
        buf = self.buf
        cur_line = buf.get_iter_at_mark(buf.get_insert()).get_line()
        for first, last, closed in self._spans(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)):
            if first <= cur_line and (cur_line < last if closed else cur_line <= last):
                return first, last, closed
        return None

    def code_state(self):
        """(cursor is on an opening fence or inside a code block, that block is closed)"""
        block = self._block_here()
        return block is not None, bool(block and block[2])

    def newline(self):
        """Enter/Shift+Enter handling. In a code block: a plain new line, and an unclosed block
        gets its closing fence, so the new line is never the (unstylable) last line.
        On a list item: the next item. Returns False to let the TextView insert a newline."""
        block = self._block_here()
        if block is not None:
            buf = self.buf
            buf.begin_user_action()
            buf.insert_at_cursor("\n")
            if not block[2]:  # (its closing fence where its lines end: not after what's below them)
                mark = buf.create_mark(None, buf.get_iter_at_mark(buf.get_insert()), True)
                end = buf.get_iter_at_line(block[1] + 1)[1]
                if not end.ends_line():
                    end.forward_to_line_end()
                buf.insert(end, "\n```")
                buf.place_cursor(buf.get_iter_at_mark(mark))
                buf.delete_mark(mark)
            buf.end_user_action()
            return True
        return self.continue_list()

    def move_item(self, by):
        """Alt and Up or Down: the list item the caret's in moved past its sibling that way (swap_list_item). False if
        it's not in one."""
        buf = self.buf
        ins = buf.get_iter_at_mark(buf.get_insert())
        lines = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).split("\n")
        line, column = ins.get_line(), ins.get_line_offset()
        at = line
        while at >= 0 and lines[at].startswith((" ", "\t")) and lines[at].strip() and not _LIST_ITEM.match(lines[at]):
            at -= 1
        if at < 0 or not _LIST_ITEM.match(lines[at]):
            return False
        swapped = swap_list_item(lines, line, by)
        if swapped is None:
            return True  # (no sibling that way: nothing to do, and the key's still this)
        new, one, three, moved = swapped
        buf.begin_user_action()
        start = buf.get_iter_at_line(one)[1]
        end = buf.get_iter_at_line(three)[1] if three < len(lines) else buf.get_end_iter()
        buf.delete(start, end)
        buf.insert(start, "\n".join(new) + ("\n" if three < len(lines) else ""))
        buf.place_cursor(buf.get_iter_at_line_offset(moved, column)[1])
        buf.end_user_action()
        return True

    def _lines(self, text):
        """(line number, line, inside a code fence?, is a fence line?)"""
        kinds = {}
        for first, last, closed in self._spans(text):
            kinds.update((n, (True, False)) for n in range(first + 1, last + 1))
            kinds[first] = False, True
            if closed:
                kinds[last] = True, True
        for n, line in enumerate(text.split("\n")):
            yield (n, line, *kinds.get(n, (False, False)))

    def make_bullets(self, text):
        """'- ', '* ' or '+ ' starting a line becomes a real bullet: • at the top level, ◦ one
        level in (re-picked when a line is indented or outdented). Returns True if it edited."""
        edits = []
        for n, line, code, fence in self._lines(text):
            m = None if code or fence else re.match(r"^(\s*)([-*+•◦]) ", line)
            if m and m.group(2) != (want := bullet_for(len(m.group(1).expandtabs(4)) // 2)):
                edits.append((n, len(m.group(1)), want))
        if not edits:
            return False
        self.buf.begin_user_action()
        for n, col, glyph in edits:
            start = self.buf.get_iter_at_line_offset(n, col)[1]
            end = start.copy()
            end.forward_char()
            self.buf.delete(start, end)
            self.buf.insert(self.buf.get_iter_at_line_offset(n, col)[1], glyph)
        self.buf.end_user_action()
        return True

    @staticmethod
    def to_markdown(text):
        """Editor text -> markdown to send: bullets go back to '-' (code blocks untouched)."""
        lines = text.split("\n")
        code = {n for first, last, _closed in fence_spans(lines, tildes=False) for n in range(first, last + 1)}
        return "\n".join(line if n in code else re.sub(rf"^(\s*)[{BULLET}{SUB_BULLET}](?= )", r"\1-", line)
                         for n, line in enumerate(lines))

    def restyle(self):
        self.pending = False
        buf = self.buf
        self.make_bullets(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True))
        return False

    def restyle_tags(self):
        if self.styling:  # applying tags can re-enter through notify::cursor-position
            return
        self.styling = True
        try:
            self._restyle_tags()
        finally:
            self.styling = False

    def _restyle_tags(self):
        buf = self.buf
        self.colours()
        buf.remove_all_tags(buf.get_start_iter(), buf.get_end_iter())
        self.cursor_line = buf.get_iter_at_mark(buf.get_insert()).get_line()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        blocks = [(first, last) for first, last, _closed in self._spans(text)]
        if isinstance(self.view, ComposerView):
            self.view.code_blocks = blocks
            self.view.queue_draw()
        for n, line, in_code, fence in self._lines(text):
            hide = "syntax" if n == self.cursor_line else "hidden"
            if fence or in_code:
                self._tag_line("fence" if fence else "code", n)
                continue
            if h := re.match(r"^(#{1,6})\s+", line):
                self._tag_line(f"h{min(len(h.group(1)), 3)}", n)
                self._tag(hide, n, 0, h.end())
            elif m := _ITEM.match(line):
                depth = min(5, len(m.group(1).expandtabs(4)) // 2)
                self._tag_line(f"item{depth}", n)
                self._tag("marker", n, m.start(2), m.end(2))
            for rx, tag, mark in ((r"`[^`\n]+`", "inline", 1), (r"\*\*[^*\n]+\*\*", "bold", 2),
                                  (r"(?<![\w*])\*(?!\s)[^*\n]+(?<!\s)\*(?![\w*])", "italic", 1)):
                for m in re.finditer(rx, line):
                    self._tag(tag, n, m.start(), m.end())
                    self._tag(hide, n, m.start(), m.start() + mark)
                    self._tag(hide, n, m.end() - mark, m.end())
        return False

    def continue_list(self):
        """Shift+Enter on a list item starts the next item (or ends the list if this one is
        empty). Returns False when the cursor isn't on a list item."""
        buf = self.buf
        cur = buf.get_iter_at_mark(buf.get_insert())
        start = cur.copy()
        start.set_line_offset(0)
        m = _ITEM.match(buf.get_text(start, cur, True))
        if not m:
            return False
        if not m.group(3).strip():  # empty item: drop the marker instead
            buf.delete(start, cur)
            return True
        marker = m.group(2)
        if marker[0].isdigit():
            marker = f"{int(marker[:-1]) + 1}{marker[-1]}"
        elif marker in "-*+":
            marker = bullet_for(len(m.group(1).expandtabs(4)) // 2)
        buf.insert_at_cursor(f"\n{m.group(1)}{marker} ")
        return True


class Composer(Gtk.Overlay):
    """The box a message is written in, Baude's and Record's alike: its text (ComposerView, its markdown shown as it's
    written: ComposerMarkdown) in a card, at least two lines tall and scrolling past nine, with its buttons in its
    bottom right corner, the send button last; the text's right margin keeps it from running under them. buttons:
    any to go before the send button (Baude's attach). head: what's over the text, in the card, the card as tall again
    as it needs (Record's quote). Its owner connects the send button and the keys."""

    def __init__(self, buttons=(), send_tip="Send (Enter)\nNew line: Shift+Enter"):
        self.input = ComposerView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=10, bottom_margin=10,
                                  left_margin=10, right_margin=20 + 38 * (len(buttons) + 1), accepts_tab=False)
        self.input.add_css_class("zoomable")
        no_text_drag(self.input)  # (a selection isn't dragged away: a press in it places the caret)
        word_steps(self.input)
        self.markdown = ComposerMarkdown(self.input)
        scroll = Gtk.ScrolledWindow(min_content_height=56, max_content_height=220, propagate_natural_height=True,
                                    hexpand=True, child=self.input)
        self.head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.head.add_css_class("composer-head")
        click = Gtk.GestureClick()  # (a click there puts the caret in the text, as one in the card's margins does)
        click.connect("released", lambda *_: self.input.grab_focus())
        self.head.add_controller(click)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.head)
        body.append(scroll)
        super().__init__(child=body, hexpand=True)
        self.send, self.send_tip = Gtk.Button(), send_tip
        actions = Gtk.Box(spacing=4, halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=10, margin_bottom=10)
        actions.add_css_class("composer-actions")
        for button in buttons:
            actions.append(button)
        actions.append(self.send)
        self.add_overlay(actions)
        self.add_css_class("card")
        self.add_css_class("composer-box")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.set_busy(False)

    def set_busy(self, busy):
        """The send button: grey, to send; red, to stop, while the reply's coming."""
        if busy:
            self.send.set_icon_name("media-playback-stop-symbolic")
            self.send.set_tooltip_text("Stop (Esc)")
            self.send.add_css_class("destructive-action")
        else:
            ensure_icons()
            self.send.set_icon_name("baude-return-symbolic")  # an icon, not a text glyph
            self.send.set_tooltip_text(self.send_tip)
            self.send.remove_css_class("destructive-action")

    def text(self):
        """What's written, as markdown to send (ComposerMarkdown.to_markdown: its bullets back to -)."""
        buf = self.input.get_buffer()
        return ComposerMarkdown.to_markdown(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True))


# ------------------------------------------------------------ web images --
# Markdown can point at images on the web. They are downloaded (once per URL, off the main
# thread) and shown like local ones; until then, or if that fails, a link is shown.

WEB_IMAGE_MAX = 15_000_000


_web_images = {}    # url -> bytes, or an error message


_web_waiting = {}   # url -> callbacks waiting for it


def fetch_web_image(url, done):
    """done(data_or_None, error_or_None) on the main loop."""
    if url in _web_images:
        hit = _web_images[url]
        done(hit, None) if isinstance(hit, bytes) else done(None, hit)
        return
    if url in _web_waiting:
        _web_waiting[url].append(done)
        return
    _web_waiting[url] = [done]

    def work():
        import urllib.error  # (only now: http/ssl/email cost ~25 ms at start-up)
        import urllib.request
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "baude"})
            with urllib.request.urlopen(req, timeout=15) as r:
                kind = r.headers.get_content_type()
                data = r.read(WEB_IMAGE_MAX + 1)
            if not kind.startswith("image/"):
                raise ValueError(f"not an image ({kind})")
            if len(data) > WEB_IMAGE_MAX:
                raise ValueError("image too large")
            result, error = data, None
        except (OSError, ValueError, urllib.error.URLError) as e:
            result, error = None, str(e)
        GLib.idle_add(deliver, result, error)

    def deliver(result, error):
        _web_images[url] = result if result is not None else error
        for cb in _web_waiting.pop(url, []):
            cb(result, error)
        return False

    threading.Thread(target=work, daemon=True).start()


# ----------------------------------------------------------- image actions --

def image_cache_path(data):
    """Images that only exist in memory (pasted, or returned by a tool) are written here on
    first use, so they can be opened by other apps and shown in a folder."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    path = os.path.join(base, "baude", "images", hashlib.sha1(data).hexdigest()[:16] + _image_ext(data))
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    return path


def _image_ext(data):
    for magic, ext in ((b"\x89PNG", ".png"), (b"\xff\xd8", ".jpg"), (b"GIF8", ".gif"), (b"RIFF", ".webp")):
        if data.startswith(magic):
            return ext
    return ".png"


class ImageActions:
    """What the image click / right-click menu does. One per image widget."""

    def __init__(self, widget, data, path=None, name=None, url=None):
        self.widget, self.data, self.name, self.url = widget, data, name, url
        self.path = path if path and os.path.isfile(path) else None

    def file(self):
        return Gio.File.new_for_path(self.path or image_cache_path(self.data))

    def texture(self):
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(self.data))

    def view(self):
        root = self.widget.get_root()
        if hasattr(root, "lightbox"):
            root.lightbox.open(self.widget)
        else:  # (a window with no large view: a folder's viewers on their own)
            self.open()

    def copy(self):
        self.widget.get_clipboard().set(self.texture())
        toast(self.widget, "Image copied")

    def open(self):
        Gtk.FileLauncher(file=self.file()).launch(self.widget.get_root(), None, None)

    def open_folder(self):
        Gtk.FileLauncher(file=self.file()).open_containing_folder(self.widget.get_root(), None, None)

    def open_in_browser(self):
        Gtk.UriLauncher(uri=self.url).launch(self.widget.get_root(), None, None)


def toast(widget, text):
    root = widget.get_root()
    if root is not None and hasattr(root, "toasts"):
        root.toasts.add_toast(Adw.Toast(title=text, timeout=2))


def make_image_interactive(widget, data, path=None, name=None, url=None):
    acts = widget.image_actions = ImageActions(widget, data, path, name, url)
    group = Gio.SimpleActionGroup()
    for action, fn in (("view", acts.view), ("copy", acts.copy), ("open", acts.open),
                       ("folder", acts.open_folder), ("browser", acts.open_in_browser)):
        a = Gio.SimpleAction.new(action, None)
        a.connect("activate", lambda *_, fn=fn: fn())
        group.add_action(a)
    widget.insert_action_group("img", group)
    menu = Gio.Menu()
    menu.append("View", "img.view")
    if url:
        menu.append("Open in browser", "img.browser")
    menu.append("Copy image", "img.copy")
    menu.append("Open with default app", "img.open")
    menu.append("Open in folder", "img.folder")
    popover = widget.image_menu = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
    popover.set_parent(widget)
    widget.connect("destroy", lambda *_: popover.unparent())

    def pressed(gesture, _n, x, y):
        if gesture.get_current_button() == Gdk.BUTTON_SECONDARY:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            popover.set_pointing_to(rect)
            popover.popup()
        else:
            acts.view()
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    click = Gtk.GestureClick(button=0)  # any button
    click.connect("pressed", pressed)
    widget.add_controller(click)
    pointers.magnifier(widget)
    widget.set_tooltip_text((path or name or "image") + "\nClick to view · right-click for more")
    return acts


def settings_page():
    """A page of Settings, its rows as wide as the dialog has room for, as the feedback box is (its clamp would hold them to
    about two thirds of that)."""
    page = Adw.PreferencesPage()
    for clamp in walk(page):
        if isinstance(clamp, Adw.Clamp):
            clamp.set_maximum_size(100_000)
            clamp.set_tightening_threshold(100_000)
    return page


def paned_rule(paned):
    """The rule between a paned's children."""
    ends = (paned.get_start_child(), paned.get_end_child())
    handle = paned.get_first_child()
    while handle is not None and handle in ends:
        handle = handle.get_next_sibling()
    return handle


def paned_grip(paned):
    """paned, its rule picked before its end child, once both children are in: a press 6px either side of the rule
    drags it (GTK's handle area), but over the end child's 6px the pointer was the end child's, not the resize arrows
    a press there would get. (A child set again later goes back in front of the rule.)"""
    ends = (paned.get_start_child(), paned.get_end_child())
    handle = paned_rule(paned)
    if handle is not None and ends[1] is not None:
        handle.insert_after(paned, ends[1])
    return paned


def typed_text(view):
    """All that's written in a text view, what its markup hides too (the buffer's "text" property leaves that out)."""
    buf = view.get_buffer()
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)


def caret_of(view):
    """Where a text view's caret and the other end of its selection are, as offsets (put_caret puts them back)."""
    buf = view.get_buffer()
    return [buf.get_iter_at_mark(buf.get_insert()).get_offset(), buf.get_iter_at_mark(buf.get_selection_bound()).get_offset()]


def put_caret(view, caret):
    if caret and len(caret) == 2:  # (past the end, it's the end)
        buf = view.get_buffer()
        buf.select_range(buf.get_iter_at_offset(caret[0]), buf.get_iter_at_offset(caret[1]))


def walk(widget):
    """widget and all its descendants, in document order."""
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def focus_shown(widget):
    """The focus put in widget if it's on screen (GTK would put it in one that isn't), an entry's text not all selected
    by it: whether it took it."""
    if widget is None or not widget.get_mapped():
        return False
    return (getattr(widget, "grab_focus_without_selecting", None) or widget.grab_focus)()


def discard(widget):
    """Free a widget taken out for good (already out of its parent), and everything in it. PyGObject can't collect a
    widget whose signal handlers (its own, its children's, its controllers') reach back to it from Python, and most
    here do (a click that opens its own menu, a card's listeners): a chat switched away from lived on, pictures and
    all. Disposing each widget drops those handlers, and Python frees the rest. Done once the main loop is idle: what
    took it out is often its own click's handler, and GTK goes on through its controllers after that."""
    GLib.idle_add(_dispose_tree, widget)


def _dispose_tree(widget):
    def parts(w):  # (parents first: a container's dispose lets go of its children. Not a scrollbar's insides: none
        yield w      # are ours, and its scrolled window asks them for its adjustment as it goes)
        c = w.get_first_child()
        while c is not None:
            if not isinstance(c, Gtk.Scrollbar):
                yield from parts(c)
            c = c.get_next_sibling()
    for w in list(parts(widget)):
        if w is not widget and w.get_parent() is not None:
            continue  # (a part its parent still holds: it goes with it)
        controllers = w.observe_controllers()
        for ctl in [controllers.get_item(i) for i in range(controllers.get_n_items())]:
            w.remove_controller(ctl)  # (a click's handler is the controller's: dispose leaves those till the end)
        if isinstance(w, Gtk.Picture):  # (a picture's dispose lets go of its paintable but keeps pointing at it: the
            Gtk.Picture.set_paintable(w, None)  # second, as it's freed, let go of it again, and crashed)
        w.run_dispose()
        for c in [c for c in walk(w) if isinstance(c, Gtk.Popover) and c.get_parent() is w]:
            c.unparent()  # (a menu set_parent'ed on it, which only its owner lets go of)
    return False


class CodeBlock(Gtk.Overlay):
    """Monospace code, its long lines scrolled sideways (or wrapped: wrap). The language tag and copy button
    float in the top-right corner, so the block is only as tall as its code.
    diff=[("del"|"add"|"ctx", line), ...] shows a red / green background per line instead.
    A long block is a sub-pane: at most ~2/3 of the transcript's height, scrolled inside itself.
    syntax (a language's name: bash, regex, a code fence's tag) or else path (a file's: its name picks the language)
    highlights it (code_spans), in the light or dark style's colours; numbered: a Read's output, its lines numbered.
    wrap: prose's, its lines wrapped at the block's width."""
    max_height = 600  # set from the window (CodeBlock.set_max_height) as it's resized
    live = weakref.WeakSet()
    styled = False  # (the theme watched: a block's colours follow it)

    @classmethod
    def set_max_height(cls, h):
        h = max(160, int(h))
        if h != cls.max_height:
            cls.max_height = h
            for block in list(cls.live):
                block.scroll.set_max_content_height(h)

    def __init__(self, code, lang="", markup=None, diff=None, syntax=None, path="", numbered=False, wrap=False):
        super().__init__()
        self.add_css_class("codeblock")
        if wrap:
            self.add_css_class("wrapped")
        self.code, self.diff = code, diff
        self.how = (syntax or "", path or "", numbered) if (syntax or path) and markup is None else None
        self.looks, self.generation, self.soon = None, 0, None  # ([(label, its text, its spans)]; a highlight due)
        label_type = WrapLabel if wrap else Gtk.Label  # (wrapped, measured without Pango breaking it at every point)
        if diff is not None:
            self.label = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            for kind, line in diff:
                lbl = label_type(label=line or " ", xalign=0, selectable=True, hexpand=True, wrap=wrap,
                                 wrap_mode=Pango.WrapMode.WORD_CHAR)
                lbl.add_css_class("mono")
                lbl.add_css_class(f"diff-{kind}")
                self.label.append(lbl)
        else:
            self.label = label_type(xalign=0, yalign=0, selectable=True, wrap=wrap, wrap_mode=Pango.WrapMode.WORD_CHAR)
            self.label.add_css_class("mono")
            self.own_markup = markup is not None  # (a block drawn with its own markup is left as it is)
            if markup is not None:
                self.label.set_markup(markup)
            else:
                self.label.set_text(code)
        scroll = Gtk.ScrolledWindow(child=self.label, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER if wrap else Gtk.PolicyType.AUTOMATIC,
                                    propagate_natural_height=True, max_content_height=CodeBlock.max_height)
        scroll.add_css_class("code-scroll")
        self.scroll = scroll
        CodeBlock.live.add(self)
        self.set_child(scroll)
        corner = Gtk.Box(spacing=4, halign=Gtk.Align.END, valign=Gtk.Align.START)
        corner.add_css_class("code-corner")
        if lang:
            tag = Gtk.Label(label=lang)
            tag.add_css_class("code-lang")
            corner.append(tag)
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy")
        copy.add_css_class("flat")
        copy.add_css_class("code-copy")
        copy.connect("clicked", lambda *_: self.get_clipboard().set(self.code))
        corner.append(copy)
        self.add_overlay(corner)
        if self.how is not None:
            if not CodeBlock.styled:
                CodeBlock.styled = True
                Adw.StyleManager.get_default().connect("notify::dark", lambda *_: CodeBlock.restyle())
            self.highlight()

    @classmethod
    def restyle(cls):
        """Every block's colours, light or dark as the theme is now."""
        for block in list(cls.live):
            if block.looks:
                block.paint()

    def set_code(self, code):  # a plain block growing while it streams
        grew = code.startswith(self.code)
        self.code = code
        self.label.set_text(code)
        if not grew:  # (its colours, and a highlight on its way, were for other text; grown, it keeps them)
            self.generation += 1
            self.looks = None
            self.label.set_attributes(None)
        self.linked = False
        self.relink()
        if self.how is not None and self.soon is None:  # (as it streams: a few times a second, a long one less often)
            ref = weakref.ref(self)

            def due():
                if (block := ref()) is not None:
                    block.soon = None
                    block.highlight()
                return False
            self.soon = GLib.timeout_add(150 + min(len(code), CODE_HIGHLIGHT_MAX) // 40, due)

    def highlight(self):
        """Colour the code (self.how), at most its first CODE_HIGHLIGHT_MAX characters, as it's made: a long label is
        laid out once, coloured (coloured a moment later, it was laid out twice, and 2,000 lines take half a second
        each time). Off the main thread only while Pygments is loaded, the first time."""
        self.generation += 1
        gen, (syntax, path, numbered) = self.generation, self.how
        if self.diff is not None:  # (each side of the change read as the code it is, then cut into its lines)
            labels, rows = [], []
            child = self.label.get_first_child()
            for kind, line in self.diff:
                labels.append(child)
                rows.append((kind, line or ""))
                child = child.get_next_sibling()
            texts = None
        else:
            labels, texts = [self.label], [self.code]

        def work():
            if texts is None:
                sides = {"old": [r for r in rows if r[0] != "add"], "new": [r for r in rows if r[0] != "del"]}
                spans = {}
                for side, lines in sides.items():
                    src = _highlight_part("\n".join(line for _k, line in lines))
                    for (kind, _line), found in zip(lines, line_spans(src, code_spans(src, path, syntax))):
                        if kind != "ctx" or side == "new":
                            spans.setdefault(kind, []).append(found)
                per = {k: iter(v) for k, v in spans.items()}  # (none past what was highlighted)
                return [(lbl, line, next(per.get(kind, iter(())), [])) for lbl, (kind, line) in zip(labels, rows)]
            src = _highlight_part(texts[0])
            return [(labels[0], texts[0], numbered_spans(src, path) if numbered else code_spans(src, path, syntax))]
        ref = weakref.ref(self)

        def done(looks):
            if (block := ref()) is not None and gen == block.generation:
                block.looks = looks
                block.paint()
            return False
        if _pygments_loaded():
            done(work())
        else:
            IMAGE_POOL.submit(lambda: GLib.idle_add(done, work()))

    def paint(self):
        """Draw its colours (again, as the theme or its links changed). A link in it keeps its own."""
        if not self.looks:
            return
        dark = Adw.StyleManager.get_default().get_dark()
        for label, text, spans in self.looks:
            markup = label.get_label() if label.get_use_markup() else ""
            skip = [(s, e) for s, e, _ in markup_links(markup)] if "<a " in markup else ()
            label.set_attributes(code_attrs(text, spans, dark, skip) if spans else None)

    def relink(self):
        """Link the names Signal placed that this (plain) block's code mentions, as its reply (message, the hash a
        MarkdownView gives it) decided them; unlink when it's off."""
        if not isinstance(self.label, Gtk.Label) or getattr(self, "own_markup", False):
            return
        if not getattr(self, "link_handler", None):
            self.link_handler = self.label.connect("activate-link", on_label_link)
        message = getattr(self, "message", None)
        markup = ref_code_markup(self.code, message)
        if markup is not None and message:  # (a click opens the name as this reply decided it)
            markup = re.sub(r'href="(baude-ref:[^"#]*)"', lambda m: f'href="{m[1]}#{message}"', markup)
        if markup is not None:
            set_markup_safe(self.label, markup)
            self.linked = True
            self.paint()  # (its links keep their own colour)
        elif getattr(self, "linked", False):
            self.label.set_text(self.code)
            self.linked = False
            self.paint()


def diff_block(old, new, path="", wrap=False):
    """An edit's hunk, as Baude's chat shows an Edit's and Meta a change to a page's text: the lines it replaced on red,
    then those it put in on green, highlighted as path's code; wrap: prose's (CodeBlock)."""
    lines = [("del", l) for l in (old or "").splitlines()] + [("add", l) for l in (new or "").splitlines()]
    return CodeBlock(new or "", "", diff=lines or [("ctx", "")], path=path, wrap=wrap)


def _highlight_part(text):
    """What of a block's text is highlighted: its first CODE_HIGHLIGHT_MAX characters, to a line's end (a carriage
    return a space, as Pygments would drop it and the offsets after it be off by one)."""
    if len(text) > CODE_HIGHLIGHT_MAX:
        text = text[:max(text.rfind("\n", 0, CODE_HIGHLIGHT_MAX), 0)]
    return text.replace("\r", " ")


_PYGMENTS = []  # [whether Pygments can be imported], once it's been looked for


def _pygments_loaded():
    """Whether highlighting needs no Pygments loaded first (its lexers take ~0.1 s to import: done off the main
    thread, the first time): it's loaded, or not there to load."""
    if "pygments.lexers" in sys.modules:
        return True
    if not _PYGMENTS:
        import importlib.util
        _PYGMENTS.append(importlib.util.find_spec("pygments") is not None)
    return not _PYGMENTS[0]


def group_header(label, collapsed, toggle, plus=None, tooltip=None, current=False):
    """A list's group header, as Baude's sessions have them (Today, a folder) and Record's pages and notebooks: its
    name small and faint (current: in the accent's colour), an arrow just right of it that folds the group (toggle:
    called), shown while the pointer's near or while it's folded, and plus ((icon, tooltip, fn)), a + at its end. No
    button's card behind any of it. set_collapsed(on) turns its arrow."""
    box = Gtk.Box(spacing=2)
    box.add_css_class("sidebar-header")
    name = Gtk.Label(label=label, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, tooltip_text=tooltip or label)
    name.add_css_class("sidebar-folder")
    if current:
        name.add_css_class("current")
    box.append(name)
    arrow = Gtk.Button(valign=Gtk.Align.CENTER)
    arrow.add_css_class("flat")
    arrow.add_css_class("folder-arrow")
    arrow.connect("clicked", lambda *_: toggle())
    box.append(arrow)
    box.append(Gtk.Box(hexpand=True))
    near = Gtk.EventControllerMotion()
    near.connect("enter", lambda *_: box.add_css_class("hover"))
    near.connect("leave", lambda *_: box.remove_css_class("hover"))
    box.add_controller(near)
    if plus is not None:
        icon, tip, fn = plus
        btn = Gtk.Button(icon_name=icon, tooltip_text=tip)
        btn.add_css_class("flat")
        btn.add_css_class("sidebar-plus")
        btn.connect("clicked", lambda *_: fn())
        box.append(btn)
        box.plus = btn

    def set_collapsed(on):
        arrow.set_icon_name("pan-end-symbolic" if on else "pan-down-symbolic")
        arrow.set_tooltip_text("Expand" if on else "Collapse")
        (arrow.add_css_class if on else arrow.remove_css_class)("collapsed")
    set_collapsed(collapsed)
    box.arrow, box.name, box.set_collapsed = arrow, name, set_collapsed
    return box


# Back, forward and up through what's been shown (MainTabs.back, forward; a window's up): ⌥⌘ and an arrow on macOS
# (⌘ alone moves the caret in text), Alt and an arrow elsewhere, as browsers have them
NAV_MODS = "<Meta><Alt>" if sys.platform == "darwin" else "<Alt>"
NAV_KEYS = {"back": NAV_MODS + "Left", "forward": NAV_MODS + "Right", "up": NAV_MODS + "Up"}


_MARKUP_PART = re.compile(r"<(/?)([a-zA-Z]+)([^>]*)>|&(#x?[0-9a-fA-F]+|[a-zA-Z]+);|[^<&]+", re.S)
_LINKS_OF = {}  # markup -> [(start, end, href)] (labels' links, as hover_uri reads them)


def markup_links(markup):
    """A label's links: [(start, end, href)], start and end bytes of its text as Pango lays it out (the markup's tags
    gone, its entities read)."""
    if (hit := _LINKS_OF.get(markup)) is not None:
        return hit
    out, at, opened = [], 0, None
    for m in _MARKUP_PART.finditer(markup):
        if m.group(2) is not None:
            if m.group(2) == "a":
                if m.group(1) and opened is not None:
                    out.append((opened[0], at, opened[1]))
                    opened = None
                elif not m.group(1):
                    href = re.search(r'href="([^"]*)"', m.group(3))
                    opened = (at, html.unescape(href.group(1)) if href else "")
        else:
            at += len((html.unescape(m.group(0)) if m.group(4) is not None else m.group(0)).encode("utf-8"))
    if len(_LINKS_OF) > 2000:
        _LINKS_OF.clear()
    _LINKS_OF[markup] = out
    return out


def hover_uri(label, x, y):
    """The link of label's under (x, y) (label's coordinates), or None. (Gtk.Label.get_current_uri is the focused
    link's, not the one the pointer is on.)"""
    if not label.get_use_markup() or "<a " not in (markup := label.get_label()):
        return None
    ox, oy = label.get_layout_offsets()
    inside, index, _trailing = label.get_layout().xy_to_index(int((x - ox) * Pango.SCALE), int((y - oy) * Pango.SCALE))
    if not inside:
        return None
    return next((href for start, end, href in markup_links(markup) if start <= index < end), None)


COPY_LINK = "Copy Link to This Page"
COPY_LINK_KEY = "<Meta><Shift>l" if sys.platform == "darwin" else "<Control><Shift>l"  # (<primary> is Control here, on a Mac too)


class TabBody(Gtk.Widget):
    """What's open in one of MainTabs' tabs, as its Adw.TabView has it: as large as what's in it while its tab is the
    one shown, and no size at all while it isn't. (The tab view is as large as the largest of all its tabs, the ones
    not shown too: a Feedback tab open behind the chat held the chat at the Feedback's least, 755px, and the side bars
    beside it back, or under them it went.)"""

    def __init__(self, child):
        super().__init__()
        self.child, self.shown = child, False
        child.set_parent(self)

    def set_shown(self, shown):
        if shown != self.shown:
            self.shown = shown
            self.queue_resize()

    def do_measure(self, orientation, for_size):
        if not self.shown or self.child is None:
            return 0, 0, -1, -1
        least, natural, least_base, natural_base = self.child.measure(orientation, for_size)
        return least, natural, least_base, natural_base

    def do_size_allocate(self, width, height, baseline):
        if self.child is not None:
            self.child.allocate(width, height, baseline, None)

    def do_dispose(self):
        if self.child is not None:
            self.child.unparent()
            self.child = None
        Gtk.Widget.do_dispose(self)


def tab_child(page):
    """What's open in a tab (an Adw.TabPage of MainTabs'): its TabBody's child."""
    return page.get_child().child


class MainTabs(Gtk.Box):
    """The main area's tabs, Baude's and Record's alike: what's open in it (a chat, a page of notes, a file's code),
    each in a tab of its own (Adw.TabView), and opened again, shown again, not opened twice (open: by its key). bar
    (an Adw.TabBar) is the window's to put where it wants it. A pinned one (Baude's chat) is first, and never closed;
    shown_alone False: the bar's hidden while there's nothing but that one. closed(key, child): called as one is
    closed (its ×, close_current). copy_links (Record's): right-click on one to copy its link (copy_link); rename
    (Record's, with it): (can(child), ask(child)), Rename… on those it can rename (a chat's). Only the
    tab shown has a size: the main area's least is that one's, not the widest of all that's open (TabBody).

    What's been shown is kept as a browser keeps its history (each tab shown, a link followed to it; a file's code at
    each line it was shown at: remember): back and forward show it again, reopen(key, line) asked to open it again
    (its tab closed since; a file's code, always, at its line) and False if it can't be (gone)."""

    HISTORY = 100

    def __init__(self, closed=None, shown_alone=True, reopen=None, copy_links=False, rename=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.view = Adw.TabView(vexpand=True, hexpand=True)
        self.bar = Adw.TabBar(view=self.view, autohide=False)
        # (its scrolling ScrollGlide's, as the chat's is, ahead of the tab box's own: on macOS every scroll is a
        # mouse's, and the bar took each of a touchpad's pixels up or down for a wheel's click, some 80 px, and its
        # sideways ones 2.5 times over as they came. Its scrollers: the pinned tabs', the others')
        self.bar_glides = [ScrollGlide(sw, sideways=True)
                           for sw in walk(self.bar) if isinstance(sw, Gtk.ScrolledWindow)]
        self.pages, self.closed, self.shown_alone = {}, closed, shown_alone
        self.menu_page = None  # (the tab its menu was opened on, if it has one: copy_links)
        self.history, self.at, self.moving, self.reopen = [], -1, False, reopen
        self.view.connect("notify::selected-page", lambda *_: self._sized())
        self.view.connect("notify::selected-page", lambda *_: self.remember())
        self.view.connect("close-page", self._close)
        for sig in ("page-attached", "page-detached"):
            self.view.connect(sig, lambda *_: self._bar_shown())
        self.append(self.view)
        if copy_links:
            menu, acts, copy = Gio.Menu(), Gio.SimpleActionGroup(), Gio.SimpleAction(name="copy-link")
            menu.append("Copy Link", "tab.copy-link")
            self.view.set_menu_model(menu)
            self.view.connect("setup-menu", lambda _v, page: page is not None and setattr(self, "menu_page", page))
            copy.connect("activate", lambda *_: self.menu_page is not None and self.copy_link(tab_child(self.menu_page)))
            acts.add_action(copy)
            if rename is not None:
                can, ask = rename
                menu.append("Rename…", "tab.rename")
                named = Gio.SimpleAction(name="rename")
                named.connect("activate", lambda *_: self.menu_page is not None and ask(tab_child(self.menu_page)))
                acts.add_action(named)
                self.view.connect("setup-menu", lambda _v, page: page is not None and named.set_enabled(
                    bool(can(tab_child(page)))))
            self.bar.insert_action_group("tab", acts)

    def _bar_shown(self):
        # (Adw.TabBar's own autohide never hides with a pinned tab: by hand)
        if not self.shown_alone:
            self.bar.set_visible(self.view.get_n_pages() > self.view.get_n_pinned_pages())

    def _sized(self):
        """The tab shown is the one with a size (TabBody)."""
        shown = self.view.get_selected_page()
        for i in range(self.view.get_n_pages()):
            page = self.view.get_nth_page(i)
            page.get_child().set_shown(page is shown)

    def open(self, key, make, title, icon=None, tooltip=None, pinned=False, select=True, quiet=False):
        """What's open under key (made by make() the first time), its tab titled title (icon: a themed icon's name),
        shown (select). quiet: not kept in the history yet (a file's code, kept at its line once it's there:
        remember). Returns its widget."""
        page = self.pages.get(key)
        if page is None:
            child = make()
            child.tab_key = key
            body = TabBody(child)
            self.pages[key] = page = self.view.append_pinned(body) if pinned else self.view.append(body)
            self._sized()  # (the first: shown as it's put in; another: shown once it's picked)
        page.set_title(title)
        page.set_tooltip(GLib.markup_escape_text(tooltip or title))
        if icon:
            ensure_icons()  # (baude-…-symbolic: Baude's own, written where the icon theme looks)
            page.set_icon(Gio.ThemedIcon.new(icon))
        if select:
            was, self.moving = self.moving, self.moving or quiet
            try:
                self.view.set_selected_page(page)
            finally:
                self.moving = was
        return tab_child(page)

    def get(self, key):
        return tab_child(page) if (page := self.pages.get(key)) is not None else None

    def current(self):
        return tab_child(page) if (page := self.view.get_selected_page()) is not None else None

    def children(self):
        """What's open, in the tabs' order."""
        return [tab_child(self.view.get_nth_page(i)) for i in range(self.view.get_n_pages())]

    def show(self, key):
        if (page := self.pages.get(key)) is not None:
            self.view.set_selected_page(page)
        return page is not None

    def retitle(self, key, title, new_key=None, tooltip=None):
        """A tab's title (and key: what's open in it renamed) changed."""
        if (page := self.pages.get(key)) is None:
            return
        if new_key is not None and new_key != key:
            self.pages[new_key] = self.pages.pop(key)
            tab_child(page).tab_key = new_key
        page.set_title(title)
        page.set_tooltip(GLib.markup_escape_text(tooltip or title))

    def close(self, key):
        if (page := self.pages.get(key)) is not None:
            self.view.close_page(page)

    def copy_link(self, child=None):
        """A tab's absolute link (its child's tab_link(); child: the one shown) on the clipboard, to paste into an agent: a path
        it can read, a page of notes' file, a chat's transcript, a file's code at its line. Returns it (None: it has none)."""
        child = child or self.current()
        link = child.tab_link() if hasattr(child, "tab_link") else None
        if link:
            self.get_clipboard().set(link)
        toast(self, "Link copied" if link else "This page has no link to copy")
        return link

    # ---- history
    def remember(self):
        """What's shown now, kept (unless it's what was last, or it's being gone back or forward to): forward from
        here is forgotten, as a browser forgets it."""
        child = self.current()
        if self.moving or child is None or getattr(child, "tab_key", None) is None:
            return
        entry = (child.tab_key, getattr(child, "line", None) if child.tab_key[0] == "code" else None)
        if 0 <= self.at < len(self.history) and self.history[self.at] == entry:
            return
        del self.history[self.at + 1:]
        self.history.append(entry)
        del self.history[:-self.HISTORY]
        self.at = len(self.history) - 1

    def back(self):
        """What was shown before (⌥⌘←): shown again. False if there's nothing before."""
        return self._go(-1)

    def forward(self):
        """What was shown after it, gone back from (⌥⌘→): shown again. False if there's nothing after."""
        return self._go(1)

    def _go(self, step):
        i = self.at + step
        while 0 <= i < len(self.history):
            key, line = self.history[i]
            self.moving = True
            try:
                if key[0] == "code" and self.reopen is not None:  # (at its line: the window's show_code)
                    done = self.reopen(key, line)
                else:
                    done = self.show(key) or (self.reopen is not None and bool(self.reopen(key, line)))
            finally:
                self.moving = False
            if done:
                self.at = i
                return True
            del self.history[i]  # (gone: the next one along)
            if step < 0:
                self.at -= 1
                i -= 1
        return False

    def close_current(self):
        """The tab shown closed (⌘W), unless it's pinned. True if one was."""
        page = self.view.get_selected_page()
        if page is None or page.get_pinned():
            return False
        self.view.close_page(page)
        return True

    def _close(self, view, page):
        child = tab_child(page)
        key = getattr(child, "tab_key", None)
        if self.pages.get(key) is page:
            del self.pages[key]
        if self.menu_page is page:
            self.menu_page = None
        view.close_page_finish(page, True)
        if self.closed is not None:
            self.closed(key, child)
        return True  # (handled: finished here)


def image_description(acts, tex):
    kb = len(acts.data) / 1024
    size = f"{kb / 1024:.1f} MB" if kb > 1024 else f"{kb:.0f} kB"
    return f"{tex.get_width()}×{tex.get_height()} · {size}"


class ZoomPicture(Gtk.Picture):
    """The large view's picture: the image fitted into it (zoom 1, in the middle), or zoom times that and moved by
    (pan_x, pan_y) from the middle, past the picture's own edges as far as its parent lets it. On its way in or out
    (flight: Lightbox.fly) it's painted between there and where it came from: the decoded image moved and scaled, each
    frame, nothing decoded or rescaled for it."""

    FROM_MIDDLE = 0.08  # (with nowhere to come from: this much smaller, as it starts to show)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.zoom, self.pan_x, self.pan_y = 1.0, 0.0, 0.0
        self.flight = None  # (how far in, 0 to 1, and where from: Lightbox.where_from)
        self.set_overflow(Gtk.Overflow.VISIBLE)  # (a Gtk.Picture clips to itself: its parent does here)

    def fitted(self):
        """The image's size fitted (it's letterboxed; a small one isn't blown up): (width, height)."""
        tex, W, H = self.get_paintable(), self.get_width(), self.get_height()
        iw, ih = tex.get_intrinsic_width(), tex.get_intrinsic_height()
        k = min(W / iw, H / ih)
        if self.get_content_fit() == Gtk.ContentFit.SCALE_DOWN:
            k = min(k, 1.0)
        return iw * k, ih * k

    def rect(self):
        """Where the image is painted: (x, y, width, height), in the picture's own coordinates."""
        w, h = self.fitted()
        w, h = w * self.zoom, h * self.zoom
        return (self.get_width() - w) / 2 + self.pan_x, (self.get_height() - h) / 2 + self.pan_y, w, h

    def flown(self):
        """Where it is at this point of its flight, in the picture's own coordinates: the image's (x, y, width, height),
        the part of that that shows (it's cut off where it was, and by what's around the picture where it's going), and
        its opacity. Worked out as it's painted: the picture's place isn't known until it's laid out."""
        rect = x, y, w, h = self.rect()
        start, top = self.get_margin_start(), self.get_margin_top()
        room = (-start, -top, self.get_width() + start + self.get_margin_end(),
                self.get_height() + top + self.get_margin_bottom())
        t, source = self.flight
        if source is None:  # (out of the middle, fading in)
            k = 1 - self.FROM_MIDDLE * (1 - t)
            return (x + w * (1 - k) / 2, y + h * (1 - k) / 2, w * k, h * k), room, t
        _ok, at = self.compute_point(self.get_root(), Graphene.Point().init(0, 0))

        def between(there, here):  # (there: in the window)
            there = there[0] - at.x, there[1] - at.y, there[2], there[3]
            return tuple(a + (b - a) * t for a, b in zip(there, here))
        return between(source[0], rect), between(source[1], room), 1.0

    def do_snapshot(self, snap):
        tex = self.get_paintable()
        if tex is None or not self.get_width() or not self.get_height():
            return
        (x, y, w, h), room, opacity = self.flown() if self.flight is not None else (self.rect(), None, 1.0)
        if room is not None:
            snap.push_clip(Graphene.Rect().init(*room))
            snap.push_opacity(opacity)
        snap.save()
        snap.translate(Graphene.Point().init(x, y))
        tex.snapshot(snap, w, h)
        snap.restore()
        if room is not None:
            snap.pop()
            snap.pop()


class Lightbox(Gtk.Box):
    """Full-window image view over a dimmed window, Baude's and Record's alike (win: image_source, what its images are
    in; input, what has the focus when it closes). Click anywhere outside the image (or press
    Esc) to close; ←/→ (or the side arrows) step through every image in the conversation. Zoomed in (scrolled, pinched, a
    double click, + and −; 0 fits it again), smoothly, around where you're pointing: dragged, scrolled on a trackpad or
    moved with the arrow keys to go around it, the arrows no longer stepping between images. It opens out of the image
    clicked and closes back into it (fly), while what's behind it dims and comes back."""

    FLY_MS = 240  # (on its way in or out. The tests set it to 0: test_lightbox_flies has it fly)
    SHADE = 0.94  # (how far what's behind it is dimmed)
    ZOOM_MS = 160
    ZOOM_KEY = 1.5  # (+ and −: this much at a time)
    ZOOM_WHEEL = 1.25  # (a mouse wheel's notch)
    MOST_PIXELS = 8  # (zoomed in as far as this many screen pixels to one of the image's)

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False, focusable=True)
        self.add_css_class("lightbox")
        self.win, self.items, self.index = win, [], 0
        self.shown, self.flying, self.closing, self.source, self.hidden = 0.0, None, False, None, None
        bar = view_bar(spacing=8, margin_start=16, margin_end=12, margin_top=10, margin_bottom=6)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True)
        self.title.add_css_class("heading")
        self.info = Gtk.Label()
        self.info.add_css_class("dim-label")
        bar.append(self.title)
        bar.append(self.info)
        self.buttons = {}
        for key, label, tip in (("copy", "Copy", "Copy image"), ("folder", "Show in folder", "Open its folder"),
                                ("open", "Open", "Open with the default app"),
                                ("browser", "Open in browser", "Open the original in the browser")):
            btn = Gtk.Button(label=label, tooltip_text=tip)
            btn.connect("clicked", lambda *_, k=key: self.act(k))
            self.buttons[key] = btn
            bar.append(btn)
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close (Esc)")
        close.add_css_class("circular")
        close.connect("clicked", lambda *_: self.close())
        bar.append(close)
        handle = Gtk.WindowHandle(child=bar)  # (dragged, it moves the window; double-clicked, maximizes)
        self.append(handle)
        stage = self.stage = Gtk.Overlay(vexpand=True, hexpand=True)
        stage.set_overflow(Gtk.Overflow.HIDDEN)  # (zoomed in, it stops at the bar above it and the window's edges)
        self.picture = ZoomPicture(can_shrink=True, margin_start=56, margin_end=56, margin_bottom=40, margin_top=8)
        stage.set_child(self.picture)
        self.target, self.anim, self.pointer = (1.0, 0.0, 0.0), None, None  # (where it's going: zoom, pan_x, pan_y)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self.on_scroll)
        stage.add_controller(scroll)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: setattr(self, "pointer", (x, y)))
        stage.add_controller(motion)
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag)
        stage.add_controller(drag)
        pinch = Gtk.GestureZoom()
        pinch.connect("begin", lambda *_: setattr(self, "pinched_from", self.target[0]))
        pinch.connect("scale-changed", self.on_pinch)
        stage.add_controller(pinch)
        self.prev_btn = self._arrow("go-previous-symbolic", Gtk.Align.START, -1)
        self.next_btn = self._arrow("go-next-symbolic", Gtk.Align.END, 1)
        stage.add_overlay(self.prev_btn)
        stage.add_overlay(self.next_btn)
        self.chrome = (handle, self.prev_btn, self.next_btn)  # (what fades as it flies)
        self.append(stage)
        twice = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        twice.connect("pressed", self.on_press)
        self.picture.add_controller(twice)
        click = Gtk.GestureClick()
        click.connect("released", self.on_click)
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

    def _arrow(self, icon, align, step):
        btn = Gtk.Button(icon_name=icon, halign=align, valign=Gtk.Align.CENTER, margin_start=10, margin_end=10)
        btn.add_css_class("circular")
        btn.add_css_class("osd")
        btn.add_css_class("lightbox-arrow")
        btn.connect("clicked", lambda *_: self.step(step))
        return btn

    def open(self, widget):
        """Show `widget`'s image; the others where it is (win.image_source: a chat, the Feedback tab) become the ←/→
        sequence."""
        self.items = [w for w in walk(self.win.image_source())
                      if getattr(w, "image_actions", None) is not None and not isinstance(w, Gtk.Overlay)]
        if widget not in self.items:
            self.items.append(widget)
        self.index = self.items.index(widget)
        self.show_current()
        self.set_visible(True)
        self.grab_focus()
        self.fly(True)

    def do_snapshot(self, snap):
        """What's behind it dimmed, here and not in the CSS: it fades by itself, the image over it at full strength."""
        shade = Gdk.RGBA()
        shade.parse("black")
        shade.alpha = self.SHADE * self.shown
        snap.append_color(shade, Graphene.Rect().init(0, 0, self.get_width(), self.get_height()))
        Gtk.Box.do_snapshot(self, snap)

    def where_from(self):
        """Where the image shown sits in the window, to fly out of and back into: the (x, y, width, height) of the image
        as it's painted there and of the part of it in sight. None if none of it is (scrolled away, or on a page not
        shown), or it's gone."""
        tex, widget = self.picture.get_paintable(), self.items[self.index] if self.items else None
        if tex is None or widget is None or not widget.get_mapped() or widget.get_root() is not self.win:
            return None
        ok, bounds = widget.compute_bounds(self.win)
        if not ok or bounds.get_width() <= 0 or bounds.get_height() <= 0:
            return None
        x, y, w, h = bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height()
        left, top, right, bottom = x, y, x + w, y + h
        around = widget.get_parent()
        while around is not None:  # (a scroller cuts it off)
            ok, bounds = around.compute_bounds(self.win)
            if ok:
                left, right = max(left, bounds.get_x()), min(right, bounds.get_x() + bounds.get_width())
                top, bottom = max(top, bounds.get_y()), min(bottom, bounds.get_y() + bounds.get_height())
            around = around.get_parent()
        if right <= left or bottom <= top:
            return None
        iw, ih = tex.get_intrinsic_width(), tex.get_intrinsic_height()
        # (an attachment's thumbnail is cropped; a picture's letterboxed)
        k = (max if isinstance(widget, Gtk.Image) else min)(w / iw, h / ih)
        return (x + (w - iw * k) / 2, y + (h - ih * k) / 2, iw * k, ih * k), (left, top, right - left, bottom - top)

    def fly(self, opening):
        """On its way in, or out, from wherever it's got to (turned back part way, it goes back the way it came). Out of
        the image it's of and back into it, if that's in sight (hidden meanwhile: it's the one image that moves); or out
        of the middle, fading. Going, nothing's waiting for it: the focus has gone back and clicks go through to what's
        behind it, a click on an image bringing it back. With animations off (Adw's: it goes straight to its end), at
        once."""
        if self.flying is not None:
            self.flying.pause()
            self.flying = None
        self.closing = not opening
        self.set_can_target(opening)
        self.show_source()
        self.source = self.where_from()
        if self.source is not None:
            widget = self.items[self.index]
            holder = widget.get_parent()  # (image_widget's overlay: its corner button goes with it)
            self.hidden = holder if getattr(holder, "image_actions", None) is not None else widget
            self.hidden.set_opacity(0.0)
        self.stage.set_overflow(Gtk.Overflow.VISIBLE)  # (it starts where it was, not only inside here)
        self.fly_frame(self.shown)
        if not self.FLY_MS or not Adw.get_enable_animations(self):
            self.landed()
            return
        flying = self.flying = Adw.TimedAnimation.new(self, self.shown, 1.0 if opening else 0.0, self.FLY_MS,
                                                      Adw.CallbackAnimationTarget.new(self.fly_frame))
        flying.set_easing(Adw.Easing.EASE_IN_OUT_CUBIC)
        flying.connect("done", self.landed)

        def start(*_):  # (next frame: it counts from a frame's time, so a large image's decoding would come off it)
            if self.flying is flying:
                flying.play()
            return GLib.SOURCE_REMOVE
        self.add_tick_callback(start)

    def fly_frame(self, t):
        self.shown = t
        if self.source is not None:  # (the chat scrolled, or the window resized, on the way: to where it is now)
            self.source = self.where_from() or self.source
        self.picture.flight = (t, self.source)
        for widget in self.chrome:
            widget.set_opacity(t * t)  # (late: not read over the window's own bar while that's still showing)
        self.picture.queue_draw()
        self.queue_draw()

    def show_source(self):
        if self.hidden is not None:
            self.hidden.set_opacity(1.0)
            self.hidden = None

    def landed(self, *_):
        self.flying = None
        self.fly_frame(0.0 if self.closing else 1.0)
        self.picture.flight = self.source = None
        self.stage.set_overflow(Gtk.Overflow.HIDDEN)
        self.show_source()
        if self.closing:
            self.closing = False
            self.set_can_target(True)
            self.set_visible(False)
            if self.anim is not None:
                self.anim.pause()
                self.anim = None
            self.picture.set_paintable(None)  # (a full-size image: let it go)
            self.items = []

    def current(self):
        return self.items[self.index].image_actions

    def show_current(self):
        acts = self.current()
        tex = acts.texture()
        self.picture.set_paintable(tex)
        self.move_to(1.0, 0.0, 0.0, animate=False)  # (each image from fitted)
        # big images fill the window; small ones aren't blown up into a blur
        big = max(tex.get_width(), tex.get_height()) >= 400
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN if big else Gtk.ContentFit.SCALE_DOWN)
        self.title.set_text(os.path.basename(acts.path) if acts.path else (acts.name or acts.url or "Image"))
        self.buttons["browser"].set_visible(bool(acts.url))
        self.zoom_shown()

    def zoom_shown(self):
        """What says how far it's zoomed: the size in the bar, the arrows (they step between images, so not while it's
        zoomed in) and the pointer."""
        n, zoom = len(self.items), self.target[0]
        self.info.set_text(image_description(self.current(), self.picture.get_paintable()) +  # (not decoded again)
                           (f"  ·  {self.index + 1} / {n}" if n > 1 else "") +
                           (f"  ·  {round(zoom * 100)}%" if self.zoomed() else ""))
        self.prev_btn.set_visible(n > 1 and not self.zoomed())
        self.next_btn.set_visible(n > 1 and not self.zoomed())
        pointers.set_pointer(self.picture, "grab" if self.zoomed() else None)

    def zoomed(self):
        return self.target[0] > 1.001

    def most_zoom(self):
        tex = self.picture.get_paintable()
        if tex is None or not self.picture.get_width():
            return 1.0
        return max(2.0, self.MOST_PIXELS * tex.get_intrinsic_width() / self.picture.fitted()[0])

    @staticmethod
    def kept_in(size, view, before, after, pan):
        """How far an image of size can be moved from the middle of a picture of view, with margins before and after it
        that it can use too: its edges never come inside what's shown, and it stays in the middle if it all fits."""
        if size <= view + before + after:
            return 0.0
        middle = (view - size) / 2
        return min(max(pan, view + after - size - middle), -before - middle)

    def move_to(self, zoom, x, y, animate=True):
        """The image zoom times its fitted size, moved by (x, y): as far as it can go that way, smoothly unless it's
        following your hand."""
        pic = self.picture
        zoom = max(1.0, min(zoom, self.most_zoom()))
        if pic.get_paintable() is not None and pic.get_width():
            w, h = pic.fitted()
            x = self.kept_in(w * zoom, pic.get_width(), pic.get_margin_start(), pic.get_margin_end(), x)
            y = self.kept_in(h * zoom, pic.get_height(), pic.get_margin_top(), pic.get_margin_bottom(), y)
        self.target = (zoom, x, y)
        if self.anim is not None:
            self.anim.pause()
            self.anim = None
        if not animate or not pic.get_mapped():
            pic.zoom, pic.pan_x, pic.pan_y = zoom, x, y
            pic.queue_draw()
        else:
            start = (pic.zoom, pic.pan_x, pic.pan_y)

            def frame(t):
                pic.zoom = start[0] * (zoom / start[0]) ** t  # (evenly, as it looks: each step the same ratio)
                pic.pan_x, pic.pan_y = start[1] + (x - start[1]) * t, start[2] + (y - start[2]) * t
                pic.queue_draw()
            self.anim = Adw.TimedAnimation.new(pic, 0.0, 1.0, self.ZOOM_MS, Adw.CallbackAnimationTarget.new(frame))
            self.anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
            self.anim.play()
        if self.items:
            self.zoom_shown()

    def zoom_to(self, zoom, at=None, animate=True):
        """Zoom times its fitted size, what's under at (in the picture; its middle if there's none) staying there."""
        pic = self.picture
        now, x, y = self.target
        zoom = max(1.0, min(zoom, self.most_zoom()))
        ax, ay = at if at is not None else (pic.get_width() / 2, pic.get_height() / 2)
        ax, ay = ax - pic.get_width() / 2, ay - pic.get_height() / 2  # (from the middle, as the pan is)
        self.move_to(zoom, ax - (ax - x) * zoom / now, ay - (ay - y) * zoom / now, animate)

    def in_picture(self, widget, x, y):
        ok, point = widget.compute_point(self.picture, Graphene.Point().init(x, y))
        return (point.x, point.y) if ok else None

    def on_scroll(self, controller, dx, dy):
        """A wheel zooms, around the pointer. A trackpad goes around the image while it's zoomed in, and zooms while it
        isn't, or with Ctrl or ⌘ held."""
        at = self.in_picture(self.stage, *self.pointer) if self.pointer else None
        held = controller.get_current_event_state() & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK)
        if controller.get_unit() == Gdk.ScrollUnit.WHEEL:
            self.zoom_to(self.target[0] * self.ZOOM_WHEEL ** -dy, at)
        elif self.zoomed() and not held:
            self.move_to(self.target[0], self.target[1] - dx, self.target[2] - dy, animate=False)
        elif dy:
            self.zoom_to(self.target[0] * 1.01 ** -dy, at, animate=False)
        return True

    def on_drag_begin(self, _gesture, _x, _y):
        self.dragged_from = self.target

    def on_drag(self, _gesture, dx, dy):
        if self.dragged_from[0] > 1.001:
            self.move_to(self.dragged_from[0], self.dragged_from[1] + dx, self.dragged_from[2] + dy, animate=False)

    def on_pinch(self, gesture, scale):
        ok, x, y = gesture.get_bounding_box_center()
        self.zoom_to(self.pinched_from * scale, self.in_picture(self.stage, x, y) if ok else None, animate=False)

    def on_press(self, _gesture, n, x, y):
        """Twice on the image: zoomed to twice its size around there, or fitted again."""
        if n == 2 and self.on_image(self.picture, x, y):
            self.zoom_to(1.0 if self.zoomed() else 2.0, (x, y))

    def step(self, delta):
        if len(self.items) > 1:
            if self.flying is not None:  # (flying out of one image: there, before it's another)
                self.flying.skip()
            self.index = (self.index + delta) % len(self.items)
            self.show_current()

    def act(self, key):
        acts = self.current()
        {"copy": acts.copy, "folder": acts.open_folder, "open": acts.open, "browser": acts.open_in_browser}[key]()

    def close(self):
        if not self.closing:  # (already going: it goes)
            self.fly(False)
            self.win.input.grab_focus()

    def image_rect(self):
        """Where the image is actually painted inside the picture widget (it's letterboxed, or zoomed)."""
        return self.picture.rect()

    def on_image(self, widget, x, y):
        point = self.in_picture(widget, x, y)
        if point is None:
            return False
        ix, iy, iw, ih = self.image_rect()
        return ix <= point[0] <= ix + iw and iy <= point[1] <= iy + ih

    def on_click(self, gesture, _n, x, y):
        """A click that isn't on the image (or a button, which handles its own) closes the view."""
        if not self.on_image(self, x, y):
            self.close()

    def on_key(self, _ctl, keyval, _code, _state):
        if self.closing:  # (going: they're the window's, if the focus didn't get back there)
            return False
        zoom, x, y = self.target
        nudge = {Gdk.KEY_Left: (1, 0), Gdk.KEY_KP_Left: (1, 0), Gdk.KEY_Right: (-1, 0), Gdk.KEY_KP_Right: (-1, 0),
                 Gdk.KEY_Up: (0, 1), Gdk.KEY_KP_Up: (0, 1), Gdk.KEY_Down: (0, -1), Gdk.KEY_KP_Down: (0, -1)}.get(keyval)
        if keyval == Gdk.KEY_Escape:
            self.close()
        elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self.zoom_to(zoom * self.ZOOM_KEY)
        elif keyval in (Gdk.KEY_minus, Gdk.KEY_underscore, Gdk.KEY_KP_Subtract):
            self.zoom_to(zoom / self.ZOOM_KEY)
        elif keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
            self.zoom_to(1.0)
        elif nudge is not None and self.zoomed():  # (a fifth of what's shown at a time; the image moves the other way)
            self.move_to(zoom, x + nudge[0] * self.picture.get_width() / 5, y + nudge[1] * self.picture.get_height() / 5)
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left):
            self.step(-1)
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
            self.step(1)
        else:
            return False
        return True


SLIDE_MS = 180  # (a side bar opening or closing: SideSlide. The tests set it to 0)


class SideSlide:
    """A side bar opened and closed smoothly, whatever opens it: toggles (kept in step), set_shown, toggle. It's visible
    from the moment it starts to open until it has finished closing, and what's beside it moves over it meanwhile; its
    `visible` set directly just takes effect, the toggles following. A subclass says how far open it is: value, apply,
    closed_value, open_value, and host (the widget on screen whatever the bar's doing)."""

    def __init__(self, pane, toggles=()):
        self.pane, self.toggles, self.anim, self.quiet = pane, list(toggles), None, False
        self.target = pane.get_visible()
        self.glide, self.held_shut, self.held_at, self.when_glided = None, False, None, None  # (a drag's: hold)
        self.moving = None  # (told True as it starts to slide or be dragged, False once it's still: motion)
        for toggle in self.toggles:
            toggle.set_active(self.target)
            toggle.connect("toggled", self._toggled)
        pane.connect("notify::visible", self._visible)

    def _toggled(self, toggle):
        if not self.quiet:
            self.set_shown(toggle.get_active())

    def _visible(self, *_):
        if not self.quiet:  # (set directly)
            self.target = self.pane.get_visible()
            self._sync_toggles()

    def _sync_toggles(self):
        self.quiet = True
        for toggle in self.toggles:
            toggle.set_active(self.target)
        self.quiet = False

    def _set_visible(self, on):
        was, self.quiet = self.quiet, True
        self.pane.set_visible(on)
        self.quiet = was

    def toggle(self):
        self.set_shown(not self.target)

    def set_shown(self, show):
        if show == self.target:
            return
        self.target = show
        self._sync_toggles()
        moving = self.anim is not None
        if moving:  # (turned back part way: from where it's got to)
            self.anim.pause()
            self.anim = None
        if not SLIDE_MS or not self.host.get_mapped():
            self._set_visible(show)
            if moving:
                self.settle()
            return
        if not moving:
            self.begin()
            self.motion(True)
            if show and not self.pane.get_visible():
                self._set_visible(True)
                self.remember_open()
                self.apply(self.closed_value())
            elif not show:
                self.remember_open()
        self.anim = Adw.TimedAnimation.new(self.host, self.value(), self.open_value() if show else self.closed_value(),
                                           SLIDE_MS, Adw.CallbackAnimationTarget.new(self.apply))
        self.anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self.anim.connect("done", self._done)
        self.anim.play()

    def hold(self, shut, at):
        """A drag going on. Dragged shut, it slides shut, but it's all still there (hidden, what's being dragged would go, and
        the drag with it) and the toggles are as they were; dragged out again, it slides back to at. Only let_go settles it.
        True while the drag's not to move it itself."""
        self.held_at = at
        if shut != self.held_shut:
            self.held_shut = shut
            self._glide(self.closed_value() if shut else at)
        return shut or self.glide is not None

    def _glide(self, to):
        if self.glide is not None:
            self.glide.pause()
            self.glide = None
        self.begin()
        if not SLIDE_MS or not self.host.get_mapped():
            self.apply(to)
            self._glided()
            return
        self.glide = Adw.TimedAnimation.new(self.host, self.value(), to, SLIDE_MS, Adw.CallbackAnimationTarget.new(self.apply))
        self.glide.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self.glide.connect("done", self._glided)
        self.glide.play()

    def _glided(self, *_):
        self.glide = None
        if not self.held_shut:  # (out again)
            self.open_at(self.held_at)
            self.settle()
        if self.when_glided is not None:
            self.when_glided()
            self.when_glided = None

    def let_go(self, at):
        """The drag's over. Shut as it ended: closed now (True), and it'll open at at."""
        if not self.held_shut:
            return False
        self.held_shut = False
        if self.glide is not None:
            self.glide.pause()
            self.glide = None
        self.set_shown(False)
        self.open_at(at)
        if self.anim is None:  # (no slide: settle's not to come)
            self.settle()
        return True

    def _done(self, _anim):
        self.anim = None
        if not self.target:
            self._set_visible(False)
        self.settle()
        self.motion(False)

    def motion(self, on):
        """It's started moving (on: a slide, a drag), or it's still again: moving told, if anything's to know."""
        if self.moving is not None:
            self.moving(on)

    def begin(self):
        """About to move: whatever lets it go narrower than it can be."""

    def settle(self):
        """Moved: as it was."""


class PaneSlide(SideSlide):
    """A side bar that's one side of a Gtk.Paned (end: its end child): the rule between them moved. Its child can be
    clipped narrower than its minimum only while it's moving; afterwards as it was set. The rule's drag is this one's, not
    the paned's (that would have the rule jump by what CSS moves it, paned.main-split): it stops at the bar's least, and only
    dragged on till that would leave the bar CLOSE_WITHIN wide does the bar slide shut, and only let go there does it stay shut
    (hold), opening again at the width it was."""

    CLOSE_WITHIN = 24  # px

    def __init__(self, paned, pane, toggles=(), end=True):
        self.host, self.end, self.open_size, self.begun = paned, end, None, False
        self.dragged_from, self.limits = None, None  # (where the rule was as a drag of it began, and where it can go)
        self.closed_by_drag = None  # (told the width it was then, on closing)
        super().__init__(pane, toggles)
        for controller in list(paned.observe_controllers()):
            if isinstance(controller, Gtk.GestureDrag):  # (GTK's: the pointer's, and touch's)
                controller.set_propagation_phase(Gtk.PropagationPhase.NONE)
        drag = Gtk.GestureDrag(button=1, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        paned.add_controller(drag)

    def _drag_begin(self, gesture, x, y):
        if self.anim is not None or self.glide is not None or \
                self.host.pick(x, y, Gtk.PickFlags.DEFAULT) is not paned_rule(self.host):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.dragged_from = self.host.get_position()
        self.limits = self.host.get_property("min-position"), self.host.get_property("max-position")
        self.motion(True)

    def _drag_update(self, _gesture, dx, _dy):
        if self.dragged_from is None:
            return
        wanted = self.dragged_from + dx
        at = round(max(self.limits[0], min(self.limits[1], wanted)))
        if not self.hold((self.host.get_width() - wanted if self.end else wanted) <= self.CLOSE_WITHIN, at):
            self.host.set_position(at)

    def _drag_end(self, _gesture, _dx, _dy):
        if self.dragged_from is None:
            return
        if self.held_shut:
            position, self.dragged_from = self.dragged_from, None
            if self.closed_by_drag is not None:
                self.closed_by_drag(self.host.get_width() - position if self.end else position)
            self.let_go(position)
        elif self.glide is not None:  # (on its way back to where it was let go: still the drag's)
            self.when_glided = lambda: (setattr(self, "dragged_from", None), self.motion(False))
        else:
            self.dragged_from = None
            self.motion(False)

    def following(self):
        """The rule's where a drag of it has put it, this moment: not sliding shut, or back, nor slid by a toggle meanwhile."""
        return self.dragged_from is not None and not self.held_shut and self.glide is None and self.anim is None

    def open_at(self, position):
        self.open_size = self.host.get_width() - position if self.end else position
        self.host.notify("position")  # (its slide's last wasn't the drag's, to what keeps track of that: following; this is)

    def value(self):
        return float(self.host.get_position())

    def apply(self, value):
        self.host.set_position(round(value))

    def closed_value(self):
        return float(self.host.get_width()) if self.end else 0.0

    def remember_open(self):
        position = self.host.get_position()
        size = self.host.get_width() - position if self.end else position
        # (never less than it can be: closed since it started there's no position yet, and it'd open at nothing, nothing to drag)
        self.open_size = max(size, self.pane.measure(Gtk.Orientation.HORIZONTAL, -1)[0])

    def open_value(self):
        return float(self.host.get_width() - self.open_size if self.end else self.open_size)

    def begin(self):
        if not self.begun:
            self.begun = True
            self.shrink = self.host.get_shrink_end_child() if self.end else self.host.get_shrink_start_child()
            (self.host.set_shrink_end_child if self.end else self.host.set_shrink_start_child)(True)

    def settle(self):
        self.begun = False
        (self.host.set_shrink_end_child if self.end else self.host.set_shrink_start_child)(self.shrink)
        if self.open_size is not None:  # (shown again directly: at the width it was)
            self.host.set_position(round(self.open_value()))


INSIDE_GAP = 6  # (the inside, set this far into the frame: from the panes beside it and the window's edge)


def inside(widget, start=True, end=True):
    """widget made the inside of the window (the box the work's read and written in, square, set into the frame:
    CSS's .baude-inside), its edges INSIDE_GAP in (start, end: on that side; a pane beside it has its own gap), its
    contents kept inside it. Returns it."""
    widget.add_css_class("baude-inside")
    widget.set_overflow(Gtk.Overflow.HIDDEN)
    widget.set_margin_start(INSIDE_GAP if start else 0)
    widget.set_margin_end(INSIDE_GAP if end else 0)
    widget.set_margin_bottom(INSIDE_GAP)
    return widget


def markdown_scroller(child, **kw):
    """The scrolled window markdown (MarkdownView) is read in: Baude's chat, Record's Read and its Record's answers.
    Its child at its natural height (at its minimum, GTK's default, a code block is squashed to a line), nothing
    scrolled sideways, and a long code block a pane of at most 2/3 of what's visible (CodeBlock.set_max_height)."""
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=child, **kw)
    scroller.get_child().set_vscroll_policy(Gtk.ScrollablePolicy.NATURAL)
    scroller.get_vadjustment().connect("notify::page-size", lambda adj, _p: adj.get_page_size() > 0 and
                                       CodeBlock.set_max_height(adj.get_page_size() * 2 / 3))
    return scroller


def scroll_place(scroller):
    """Where a scrolled window has been read to, for hold_place to put back: None if it's never been laid out (not
    shown yet: nothing to put back)."""
    adj = scroller.get_vadjustment()
    if adj.get_page_size() <= 0:
        return None
    return {"value": adj.get_value(), "at_end": adj.get_value() + adj.get_page_size() >= adj.get_upper() - 40}


def hold_place(scroller, place, seconds=2.0):
    """A scrolled window put back where it was (scroll_place), and held there as what's in it lays out, a few frames or
    in a long chat more: for seconds from when it's first shown, or till it's scrolled by hand, or till it's held somewhere
    else. scroller.held says so, for what would take it to the end as it fills (a chat's follow)."""
    if not place:
        return
    if (earlier := getattr(scroller, "let_go", None)) is not None:
        earlier()
    adj = scroller.get_vadjustment()
    hand = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL,
                                     propagation_phase=Gtk.PropagationPhase.CAPTURE)
    scroller.held = True

    def put(*_):
        adj.set_value(adj.get_upper() if place["at_end"] else place["value"])

    def let_go(*_):
        if scroller.let_go is let_go:  # (once: its timer may come after it's been let go)
            scroller.held, scroller.let_go = False, None
            adj.disconnect(changed)
            scroller.remove_controller(hand)
            if mapped[0] is not None:
                scroller.disconnect(mapped[0])
                mapped[0] = None
        return False

    def shown(*_):
        if mapped[0] is not None:
            scroller.disconnect(mapped[0])
            mapped[0] = None
        put()
        GLib.timeout_add(int(seconds * 1000), let_go)
    scroller.let_go = let_go
    changed = adj.connect("changed", put)
    hand.connect("scroll", lambda *_: let_go())
    scroller.add_controller(hand)
    mapped = [None]
    if scroller.get_mapped():
        shown()
    else:
        mapped[0] = scroller.connect("map", shown)


class ScrollGlide:
    """A scrolled window's own scrolling: a wheel's or touchpad's scrolls, and the glide after a fling, each a step from
    where the view is now. GTK's own glide puts the view where its reckoning of the fling says each frame, and takes no
    notice of it being moved meanwhile: a chunk of a chat's history drawn above mid-glide, the view moved by its height
    to keep what's read where it was, was undone the next frame, the view thrown thousands of pixels, and the glide went
    on from there. Its scrolls are taken after a code block's own scroller's (bubbling up to the viewport), and Ctrl's
    (zoom) and Shift's (sideways) are left to others. more(): whether there's more to draw above, a glide that reaches
    the top waiting a moment for it. A wheel's click glides there over WHEEL_S rather than jumping a fifth of a screen in
    one frame (clicks in a row add up), and a touchpad's pixels are moved by once a frame, together: they come at their
    own rate, two in one frame and none in the next. rested(): a glide or a wheel's click has come to rest by itself.
    moved(): it's moved the view (the reader's scroll, as against the view being put somewhere). sideways: it scrolls
    left and right instead (a tab strip): scrolls either way move it, Shift's too, a touchpad's swipe the way it
    began."""

    FRICTION = 4.0  # (GTK's: the speed falls by e each quarter second)
    STOP = 10.0  # px/s
    WAIT = 0.5  # s at the top, for what's drawn above it
    WHEEL_S = 0.12  # s a wheel's click takes to get there (most of the way: it eases out)

    def __init__(self, scroller, more=None, rested=None, moved=None, sideways=False):
        self.scroller, self.more, self.rested = scroller, more or (lambda: False), rested or (lambda: None)
        self.moved, self.sideways, self.axis = moved or (lambda: None), sideways, None  # (axis: a swipe's way, x or y)
        self.v, self.tick, self.last, self.waited = 0.0, None, None, 0.0
        self.wheel = self.pan = 0.0  # (px a wheel's clicks have still to go; a touchpad's, since the last frame)
        scroller.set_kinetic_scrolling(False)  # (its glide off: this one's)
        flags = Gtk.EventControllerScrollFlags
        ctl = Gtk.EventControllerScroll(flags=(flags.BOTH_AXES if sideways else flags.VERTICAL) | flags.KINETIC)
        ctl.connect("scroll-begin", self._begin)
        ctl.connect("scroll", self._scroll)
        ctl.connect("decelerate", self._decelerate)
        scroller.get_child().add_controller(ctl)  # (the viewport: after what's in it, before the scroller's own)
        press = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", lambda *_: self.stop())  # (a click or the scrollbar taken: the glide's over)
        scroller.add_controller(press)

    def _adj(self):
        return self.scroller.get_hadjustment() if self.sideways else self.scroller.get_vadjustment()

    def _detent(self, ctl):
        """What a wheel's click scrolls, as GTK has it; 1 for a touchpad's pixels."""
        if ctl.get_unit() != Gdk.ScrollUnit.WHEEL:
            return 1.0
        return max(1.0, self._adj().get_page_size()) ** (2 / 3)

    def _begin(self, _ctl):
        self.axis = None  # (a new swipe: its way is its first move's)
        self.stop()

    def _scroll(self, ctl, dx, dy):
        state, wheel = ctl.get_current_event_state(), ctl.get_unit() == Gdk.ScrollUnit.WHEEL
        if self.sideways:  # (a wheel's click goes its own way; a touchpad's swipe, the way it began)
            if state & Gdk.ModifierType.CONTROL_MASK:
                return False
            self.axis = way = (not wheel and self.axis) or ("x" if abs(dx) >= abs(dy) else "y")
            dy = dx if way == "x" else dy
        elif dy == 0 or state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
            return False
        if wheel:
            self.wheel_by(dy * self._detent(ctl))
        else:
            self.pan_by(dy)
        return True

    def _decelerate(self, ctl, vx, vy):
        self.glide((vx if self.sideways and self.axis == "x" else vy) * self._detent(ctl))

    def step(self, dy):
        adj = self._adj()
        before = adj.get_value()
        adj.set_value(before + dy)
        if adj.get_value() != before:
            self.moved()

    def wheel_by(self, dy):
        """A wheel's click: glide dy px over WHEEL_S, on from where the clicks before it have still to go."""
        self.v, self.pan = 0.0, 0.0  # (a glide's over)
        self.wheel += dy
        self._run()

    def pan_by(self, dy):
        """A touchpad's dy px, moved by in the next frame with any others that come before it."""
        self.v, self.wheel = 0.0, 0.0
        self.pan += dy
        self._run()

    def glide(self, v):
        """Glide on at v px/s (> 0 down, or right), slowing as GTK's does (a touchpad's pixels not moved by yet, moved
        by first)."""
        pan = self.pan
        self.stop()
        self.pan = pan
        if abs(v) >= self.STOP:
            self.v = v
        if self.v or self.pan:
            self._run()

    def _run(self):
        if self.tick is None:
            self.last, self.waited = None, 0.0  # (its first frame's time: _first_dt's)
            self.tick = self.scroller.add_tick_callback(self._frame)

    def _first_dt(self, clock, now):
        """The time a first frame moves it by: since the frame before (a frame's, after a while with none), less what a
        touchpad's last pixels moved by in it took at the glide's speed. (The clock's time between frames is the next
        frame's once that's nearer: a glide begun then stood still for a frame as the fingers left the touchpad.)"""
        before = clock.get_timings(clock.get_frame_counter() - 1)
        dt = now - before.get_frame_time() / 1e6 if before is not None else 0.0
        if not 0.0 < dt < 0.1:
            dt = clock.get_refresh_info(0)[0] / 1e6
        return max(0.0, dt - abs(self.pan / self.v)) if self.pan and self.v else dt

    def gliding(self):
        return self.tick is not None

    def stop(self):
        self.v = self.wheel = self.pan = 0.0
        if self.tick is not None:
            self.scroller.remove_tick_callback(self.tick)
            self.tick = None

    def _frame(self, _widget, clock):
        now = clock.get_frame_time() / 1e6
        dt = min(max(now - self.last, 0.0), 0.1) if self.last is not None else self._first_dt(clock, now)
        self.last = now
        moving = bool(self.v or self.wheel)  # (what comes to rest by itself, as against a touchpad's pixels)
        dy, self.pan = self.pan, 0.0
        if self.wheel:
            part = self.wheel * (1 - math.exp(-3 * dt / self.WHEEL_S))  # (95% of the way in WHEEL_S)
            if abs(self.wheel - part) < 0.5:
                part = self.wheel
            self.wheel -= part
            dy += part
        glided = 0.0
        if self.v:
            decay = math.exp(-self.FRICTION * dt)
            glided, self.v = self.v * (1 - decay) / self.FRICTION, self.v * decay
            dy += glided
        adj = self._adj()
        before = adj.get_value()
        if dy:
            adj.set_value(before + dy)
            if adj.get_value() != before:
                self.moved()
        if adj.get_value() == before and dy:  # (at an end)
            self.wheel = 0.0
            self.waited += dt
            if not (glided < 0 and self.more()) or self.waited > self.WAIT:
                self.v = 0.0
        if abs(self.v) < self.STOP:
            self.v = 0.0
        if self.v or self.wheel or self.pan:
            return GLib.SOURCE_CONTINUE
        self.tick = None
        if moving:
            self.rested()
        return GLib.SOURCE_REMOVE


def layout_origin(label):
    """Where a label's text is drawn in it (GTK's get_layout_location, which GTK 4 doesn't give): its alignment's."""
    layout = label.get_layout()
    _ink, logical = layout.get_pixel_extents()
    xalign = label.get_xalign() if label.get_direction() != Gtk.TextDirection.RTL else 1 - label.get_xalign()
    x = math.floor(xalign * (label.get_width() - logical.width) - logical.x)
    if (baseline := label.get_baseline()) != -1:
        return x, baseline - layout.get_baseline() // Pango.SCALE
    return x, math.floor((label.get_height() - logical.height) * label.get_yalign())


def char_at(label, x, y):
    """The character of a label's text at (x, y), in its coordinates: the nearest, (x, y) off its text."""
    ox, oy = layout_origin(label)
    layout = label.get_layout()
    _inside, index, trailing = layout.xy_to_index(int((x - ox) * Pango.SCALE), int((y - oy) * Pango.SCALE))
    return len(layout.get_text().encode()[:index].decode("utf-8", "ignore")) + trailing


def passage_rects(label, start, end, target):
    """Where characters start to end of a label's text are, a rectangle a line, in target's coordinates."""
    layout = label.get_layout()
    text = layout.get_text()
    sb, eb = len(text[:start].encode()), len(text[:end].encode())
    ox, oy = layout_origin(label)
    ok, o = label.compute_point(target, Graphene.Point().init(ox, oy))
    if not ok or eb <= sb:
        return []
    rects, it = [], layout.get_iter()
    while True:
        line = it.get_line_readonly()
        a, b = line.start_index, line.start_index + line.length
        if a < eb and b > sb:
            s, e = max(sb, a), min(eb, b)
            x0 = layout.index_to_pos(s).x
            x1 = layout.index_to_pos(e).x if e < b else (lambda r: r.x + r.width)(it.get_line_extents()[1])
            y0, y1 = it.get_line_yrange()
            if x1 > x0:
                rects.append(Graphene.Rect().init(o.x + x0 / Pango.SCALE, o.y + y0 / Pango.SCALE,
                                                  (x1 - x0) / Pango.SCALE, (y1 - y0) / Pango.SCALE))
        if not it.next_line():
            return rects


def inner_scroller(label, root):
    """The scroller a label of root's is seen through, a code block's or a table's; None if it's only root's own."""
    scroller = label.get_ancestor(Gtk.ScrolledWindow)
    return scroller if scroller is not None and scroller.is_ancestor(root) else None


def shade_passage(snapshot, fill, label, rects, target, root):
    """A passage's rectangles (passage_rects) filled, no further than the scroller it's seen through."""
    if not rects:
        return
    clip = inner_scroller(label, root)
    if clip is not None:
        snapshot.push_clip(clip.compute_bounds(target)[1])
    for rect in rects:
        snapshot.append_color(fill, rect)
    if clip is not None:
        snapshot.pop()


class SelectionMarks(Gtk.Widget):
    """A ChatSelection's passages, drawn over the chat."""

    def __init__(self, selection):
        super().__init__(can_target=False, hexpand=True, vexpand=True)
        self.selection = selection

    def do_snapshot(self, snapshot):
        self.selection.draw(snapshot, self)


class ChatSelection:
    """A selection across a chat's text. Each paragraph, list item, table cell and code block is a label of its own
    (MarkdownView), and GTK's selection stops at the end of the one it began in. A drag that stays in its label is GTK's
    as ever (a double click's word, a triple click's line, a link's click). One that goes on to another label is this
    one's from there: that label hears no more of it, what's passed of each label is shaded by this (only one label
    can hold a selection in GTK: each takes the primary clipboard, and that lets the last one's go), the view scrolls at
    its edges, and Copy takes it all, in order. Shift and a click extends it, and Ctrl+A or ⌘A takes the whole chat.

    root: what holds the labels. area: what's heard, its scroller, so a drag can begin beside the text. over: an overlay
    with root as its child, that the shading's put in. on_selected(text): a selection made across labels, once made.
    on_own(label): a drag or a click let go in the label it began in, that label's selection (GTK's) settled."""

    EDGE = 28  # px from the view's top or bottom where a drag scrolls it
    DRAG_PX = 8  # (begun beside the text: moved this far before it's a drag)
    TICK_MS = 16
    SPEED = 0.35  # px a tick for each px past that
    TOP_SPEED = 48
    NOT_TEXT = (Gtk.Button, Gtk.Scrollbar, Gtk.Picture, Gtk.Editable, Gtk.TextView, Gtk.Expander, Gtk.ListBoxRow)
    HEARD = (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE, Gdk.EventType.MOTION_NOTIFY)
    OTHERS = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.SUPER_MASK

    def __init__(self, root, area, over, on_selected=None, on_own=None):
        self.root, self.area, self.on_selected, self.on_own = root, area, on_selected, on_own
        self.anchor, self.pressed_label, self.dragging, self.across = None, None, False, False
        self.shown = {}  # (what's selected, in order: label -> (start, end, its text as it was))
        self.labels, self.lengths, self.index, self.tops, self.bottoms, self.size = [], [], {}, [], [], None
        self.pointer, self.pressed_at, self.ticking = (0, 0), (0, 0), None
        self.menu_at = None  # (what a right click there offers: (label, character) -> [(its name, its function)], or None)
        self.marks = SelectionMarks(self)
        if isinstance(view := area.get_child(), Gtk.Viewport):  # (the focus moving doesn't move the chat: a press on a
            view.set_scroll_to_focus(False)                     # paragraph half in view, or a menu giving the focus back)
        over.add_overlay(self.marks)
        over.connect("get-child-position", self._laid_out)
        area.get_vadjustment().connect("value-changed", lambda *_: self.shown and self.marks.queue_draw())
        legacy = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        legacy.connect("event", self._event)
        area.add_controller(legacy)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        area.add_controller(keys)

    def _laid_out(self, _over, child, _alloc):
        if child is self.marks and self.shown:  # (the chat laid out again: its passages elsewhere, perhaps)
            self.marks.queue_draw()
        return False

    def parts(self):
        """What's selected across labels, in order, each still as it was: [(label, start, end)]."""
        return [(label, start, end) for label, (start, end, text) in self.shown.items()
                if label.is_ancestor(self.root) and label.get_text() == text]

    def text(self):
        """What's selected across labels, as it reads: a blank line between paragraphs, a list's items on their lines
        with their markers, a table's cells apart by tabs, its rows on their lines, a diff's lines as they were."""
        out, before = [], None
        for label, start, end in self.parts():
            piece = label.get_text()[start:end].replace("\u2063", "")  # (a logo's place: LinkLabel)
            if before is not None:
                out.append(self._between(before, label))
            prefix = getattr(label, "list_prefix", None)
            out.append((prefix if prefix is not None and start == 0 else "") + piece)
            before = label
        return "".join(out)

    @staticmethod
    def _between(a, b):
        parent = a.get_parent()
        if isinstance(parent, Gtk.Grid) and b.get_parent() is parent:
            return "\t" if parent.query_child(a)[1] == parent.query_child(b)[1] else "\n"
        if parent is b.get_parent() and any(a.has_css_class(f"diff-{kind}") for kind in ("add", "del", "ctx")):
            return "\n"
        if getattr(a, "list_prefix", None) is not None and getattr(b, "list_prefix", None) is not None:
            return "\n"
        return "\n\n"

    def copy(self):
        self.root.get_clipboard().set(self.text())

    def clear(self):
        """Nothing selected: what this drew, and what GTK did in the label pressed last. (A press in a selection is
        then a new one, not GTK's dragging of the text away: no_text_drag's choice, for labels.)"""
        self._own_selection_gone()
        self.forget()

    def forget(self):
        """What it drew let go (Reader's passages draw theirs once there's more than one), and the chat's labels with
        it: read again for the next one, and not kept from going with their chat till then."""
        self.labels, self.lengths, self.index, self.tops, self.bottoms, self.size = [], [], {}, [], [], None
        if self.shown:
            self.shown = {}
            self.marks.queue_draw()

    def _own_selection_gone(self):
        if (label := self.pressed_label) is not None and label.is_ancestor(self.root) and \
                label.get_selection_bounds()[0]:
            label.select_region(0, 0)

    def select_all(self):
        self._collect()
        if self.labels:
            self._own_selection_gone()
            self.anchor = (self.labels[0], 0)
            self._select_to((self.labels[-1], self.lengths[-1]))
            self._told()

    def draw(self, snapshot, target):
        if not self.shown:
            return
        self._collect()
        ok, at = self.area.compute_point(self.root, Graphene.Point().init(0, 0))
        top, bottom = (at.y, at.y + self.area.get_height()) if ok else (-math.inf, math.inf)
        fill = accent_rgba()
        fill.alpha = 0.3
        for label, start, end in self.parts():
            i = self.index.get(label)
            if i is None or self.bottoms[i] < top or self.tops[i] > bottom:
                continue  # (off the view: not worked out, and a selection can be the whole chat)
            shade_passage(snapshot, fill, label, passage_rects(label, start, end, target), target, self.root)

    # ---- the pointer

    def _event(self, controller, _event):
        event = controller.get_current_event()  # (its argument's None under PyGObject 3.58)
        kind = event.get_event_type() if event is not None else None
        native = self.root.get_native()
        if kind not in self.HEARD or native is None:
            return False
        tx, ty = native.get_surface_transform()
        _ok, x, y = event.get_position()
        self.pointer = x - tx, y - ty
        state = event.get_modifier_state()
        if kind == Gdk.EventType.BUTTON_RELEASE:
            return self._release()
        if kind == Gdk.EventType.BUTTON_PRESS:
            button = event.get_button()
            if button == Gdk.BUTTON_SECONDARY:
                return self._context_menu()
            if button != Gdk.BUTTON_PRIMARY or state & self.OTHERS:
                self.dragging = False
                return False
            return self._press(bool(state & Gdk.ModifierType.SHIFT_MASK))
        if not self.dragging:
            return False
        if not state & Gdk.ModifierType.BUTTON1_MASK:  # (let go where this didn't hear it)
            return self._release()
        return self._move()

    def _in(self, widget):
        native = self.root.get_native()
        ok, p = native.compute_point(widget, Graphene.Point().init(*self.pointer))
        return (p.x, p.y) if ok else (0, 0)

    def _hit(self, dragging=False):
        """(label, character) under the pointer, else the nearest there is. Not dragging, None on what isn't text (a
        button, a picture, the scrollbar): a press there is its own."""
        x, y = self._in(self.area)
        w = self.area.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w is not self.area:
            if isinstance(w, Gtk.Label) and w.get_selectable() and w.is_ancestor(self.root):
                return w, char_at(w, *self._in(w))
            if not dragging and isinstance(w, self.NOT_TEXT):
                return None
            w = w.get_parent()
        return self._nearest()

    def _collect(self):
        """The chat's labels in order, with where each is: read again once the chat's another size."""
        size = self.root.get_width(), self.root.get_height()
        if size == self.size and self.labels:
            return
        self.size = size
        self.labels = [w for w in walk(self.root) if isinstance(w, Gtk.Label) and w.get_selectable() and w.get_mapped()]
        self.lengths, self.tops, self.bottoms = [], [], []
        self.index = {w: i for i, w in enumerate(self.labels)}
        low = -math.inf
        for w in self.labels:
            ok, b = w.compute_bounds(self.root)
            top, bottom = (b.get_y(), b.get_y() + b.get_height()) if ok else (low, low)
            low = max(low, bottom)
            self.tops.append(top)
            self.bottoms.append(low)  # (never going back up, for bisect: a table's cells share a row)
            self.lengths.append(len(w.get_text()))

    def _nearest(self):
        """Off every label: in the gap above one, its start (so a drag down has all before it, and a drag up all of
        it); level with one, the character across from the pointer; past them all, the end."""
        self._collect()
        if not self.labels:
            return None
        y = self._in(self.root)[1]
        i = bisect.bisect_left(self.bottoms, y)
        if i >= len(self.labels):
            return self.labels[-1], self.lengths[-1]
        if y < self.tops[i]:
            return self.labels[i], 0
        return self.labels[i], char_at(self.labels[i], *self._in(self.labels[i]))

    def _press(self, shift):
        hit = self._hit()
        if hit is None:  # (not text, the scrollbar or a button: theirs, and what's selected stays)
            self.dragging = False
            return False
        if shift and self.anchor is not None and self.anchor[0].is_ancestor(self.root):
            self.dragging = True
            self._begin_across()
            self._select_to(hit)
            return True  # (the label would begin a selection of its own)
        self.clear()
        self.across = False
        self.anchor, self.dragging, self.pressed_at = hit, True, self.pointer
        x, y = self._in(self.area)
        w = self.area.pick(x, y, Gtk.PickFlags.DEFAULT)
        self.pressed_label = hit[0] if w is hit[0] or (w is not None and w.is_ancestor(hit[0])) else None
        return False

    def _move(self):
        hit = self._hit(dragging=True)
        if hit is None:
            return False
        if not self.across:
            if hit[0] is self.pressed_label:
                return False  # (in the label it began in: GTK's)
            if self.pressed_label is None and math.dist(self.pointer, self.pressed_at) < self.DRAG_PX:
                return False  # (a click beside the text that moved a little: not a drag)
            self._begin_across()
        self._select_to(hit)
        return True

    def _release(self):
        was = self.dragging
        self.dragging = False
        if self.ticking is not None:
            GLib.source_remove(self.ticking)
            self.ticking = None
        if was:  # (the release has to reach the label, for its gestures to end: its drag's last selects to here)
            GLib.idle_add(self._settled, priority=GLib.PRIORITY_HIGH_IDLE)
        return False

    def _settled(self):
        if self.across:
            self._own_selection_gone()
            self._told()
        elif self.pressed_label is not None and self.on_own is not None:
            self.on_own(self.pressed_label)
        return False

    def _told(self):
        if self.on_selected is not None and (text := self.text()).strip():
            self.on_selected(text)

    def _begin_across(self):
        """From here it's this one's: GTK's selection in the label it began in gone, and the focus in the chat so Copy
        reaches it (a press on a label that can't take the focus, or beside the text, left it where it was)."""
        self.across = True
        self.size = None
        self._own_selection_gone()
        window = self.root.get_root()
        focus = window.get_focus() if window is not None else None
        if focus is None or not (focus is self.root or focus.is_ancestor(self.root)):
            self.root.set_focusable(True)
            self.root.grab_focus()
        if self.ticking is None:
            self.ticking = GLib.timeout_add(self.TICK_MS, self._tick)

    def _select_to(self, hit):
        self._collect()
        if self.anchor is None or self.anchor[0] not in self.index or hit[0] not in self.index:
            return  # (drawn again under the drag)
        (a, at), (b, to) = self.anchor, hit
        first, last = self.index[a], self.index[b]
        if (first, at) > (last, to):
            first, last, at, to = last, first, to, at
        want = {}
        for i in range(first, last + 1):
            start, end = at if i == first else 0, to if i == last else self.lengths[i]
            if end > start:
                want[self.labels[i]] = (start, end, self.labels[i].get_text())
        if want != self.shown:
            self.shown = want
            self.marks.queue_draw()

    def _tick(self):
        """While a drag across is at the view's top or bottom, or past it: the view scrolled, faster the further."""
        if self.root.get_native() is None:  # (the chat gone under the drag)
            self.ticking, self.dragging = None, False
            return False
        y, height = self._in(self.area)[1], self.area.get_height()
        past = y - self.EDGE if y < self.EDGE else y - (height - self.EDGE) if y > height - self.EDGE else 0
        if past:
            adj = self.area.get_vadjustment()
            before = adj.get_value()
            adj.set_value(before + max(-self.TOP_SPEED, min(self.TOP_SPEED, past * self.SPEED)))
            if adj.get_value() != before and (hit := self._hit(dragging=True)) is not None:
                self._select_to(hit)
        return True

    def _key(self, _controller, keyval, _code, state):
        if not state & self.OTHERS or state & Gdk.ModifierType.SHIFT_MASK or \
                keyval not in (Gdk.KEY_c, Gdk.KEY_C, Gdk.KEY_a, Gdk.KEY_A):
            return False
        window = self.root.get_root()
        if isinstance(window.get_focus() if window is not None else None, (Gtk.Editable, Gtk.TextView)):
            return False  # (a box in the chat: its own)
        if keyval in (Gdk.KEY_a, Gdk.KEY_A):
            self.select_all()
            return True
        if self.parts():
            self.copy()
            return True
        return False

    def _context_menu(self):
        """A right click on what's selected across labels: this menu, as a label's own has no selection to copy."""
        hit = self._hit(dragging=True)
        if hit is None:
            return False
        items = self.menu_at(*hit) if self.menu_at is not None else \
            [("Copy", self.copy)] if hit[0] in self.shown else None
        if not items:
            return False
        menu, group = Gio.Menu(), Gio.SimpleActionGroup()
        for i, (label, fn) in enumerate(items):
            action = Gio.SimpleAction.new(f"item{i}", None)
            action.connect("activate", lambda _a, _p, fn=fn: fn())
            group.add_action(action)
            menu.append(label, f"chat-text.item{i}")
        self.root.insert_action_group("chat-text", group)
        pop = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        pop.set_parent(self.root)
        x, y = self._in(self.root)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()
        return True


class MarkdownView(Gtk.Box):
    """Markdown as widgets, one per block. While a reply streams in, only what changed is
    touched: finished blocks keep their widgets, the one still growing is updated in place,
    and deltas that arrive between two frames are drawn once. Nor is the text read again
    from the top each frame: only from where the blocks before were settled (markdown_parse)."""

    base_dir = os.getcwd()  # for relative image paths; the window keeps this in sync with its cwd

    def __init__(self, text=""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.text, self.blocks, self.widgets, self.pending = "", [], [], None
        self.streaming = None  # while text is appended: [where to read on from (markdown_parse), its hash so far]
        if text:
            self.set_text(text)

    def relink(self):
        """Draw the same text again with the links as they are now: blocks whose markup changed are
        updated in place (a table's cells, a code block's names, the same way); nothing else is touched."""
        if self.streaming is not None:
            self.streaming[0] = None  # (the blocks it settled have the links as they were: the next frame reads all)
        if self.pending is not None:
            return  # the next frame draws it anyway
        blocks = markdown_blocks(self.text)
        if len(blocks) != len(self.blocks):
            self.set_text(self.text)
            return
        for i, (old, new) in enumerate(zip(self.blocks, blocks)):
            if old != new and not self.update(self.widgets[i], old, new):
                w = self.render(new)
                self.insert_child_after(w, self.widgets[i])
                self.remove(self.widgets[i])
                self.widgets[i] = w
            elif new[0] == "code" and isinstance(self.widgets[i], CodeBlock):
                self.widgets[i].message = REF_MESSAGE
                self.widgets[i].relink()  # (its text is the same; what Signal knows may not be)
            elif new[0] == "table":
                relink_table(self.widgets[i])  # (likewise: a table's blocks hold its text, not its markup)
        self.blocks = blocks

    def append(self, delta):
        if self.streaming is None:
            self.streaming = [None, hashlib.sha1(self.text.encode("utf-8", "replace"))]
        self.streaming[1].update(delta.encode("utf-8", "replace"))  # (the hash markdown_parse would take of it all)
        self.text += delta
        if self.pending is None:  # drawn before the next frame, however many deltas come first
            self.pending = GLib.idle_add(self.flush, priority=GLib.PRIORITY_HIGH_IDLE)

    def flush(self):
        self.pending = None
        if self.streaming is None:
            self.set_text(self.text)
            return False
        since, digest = self.streaming
        blocks, self.streaming[0] = markdown_parse(self.text, since, digest.hexdigest()[:16])
        # (the blocks before the last it settled are the very ones shown: not compared again)
        self.draw(blocks, since[2] - 1 if since is not None and since[1] is self.blocks and since[2] else 0)
        return False

    def finish(self):
        """The message is complete: draw what's still waiting for the next frame now. A streamed text is
        read once more from the top, as every frame once was: the blocks settled while it came in were read
        with what was known then (a path that exists now, a name Signal has found since)."""
        if self.pending is not None or self.streaming is not None:
            self.set_text(self.text)

    def set_text(self, text):
        if self.pending is not None:
            GLib.source_remove(self.pending)
            self.pending = None
        self.text, self.streaming = text, None
        self.draw(markdown_blocks(text))

    def draw(self, blocks, same=0):
        """Show blocks in place of the ones shown: the widgets of the blocks that are the same are kept, and
        the first that changed is updated in place if it can be. same: how many at the start are known to be."""
        while same < min(len(blocks), len(self.blocks)) and blocks[same] == self.blocks[same]:
            same += 1
        stale = self.widgets[same:]
        del self.widgets[same:]
        widgets = self.widgets
        if stale and same < len(blocks) and self.update(stale[0], self.blocks[same], blocks[same]):
            widgets.append(stale.pop(0))
        for w in stale:
            self.remove(w)
            discard(w)
        for b in blocks[len(widgets):]:
            w = self.render(b)
            Gtk.Box.append(self, w)
            widgets.append(w)
        self.blocks, self.widgets = blocks, widgets

    @staticmethod
    def update(widget, old, new):
        """Change a block's widget to show new content of the same kind; False if it can't."""
        kind = new[0]
        if kind != old[0]:
            return False
        if kind in ("para", "quote"):
            set_markup_safe(widget, new[1])
        elif kind == "heading" and new[1] == old[1]:
            set_markup_safe(widget, f"<b>{new[2]}</b>")
        elif kind == "item" and new[1:3] == old[1:3]:
            set_markup_safe(widget.body, new[3])
        elif kind == "code" and new[1] == old[1] and isinstance(widget, CodeBlock):
            widget.message = REF_MESSAGE
            widget.set_code(new[2])
        else:
            return False
        return True

    def render(self, b):
        kind = b[0]
        if kind == "para":
            return sel_label(b[1])
        if kind == "heading":
            lbl = sel_label(f"<b>{b[2]}</b>")
            lbl.add_css_class(f"md-h{min(b[1], 3)}")
            return lbl
        if kind == "item":
            row = Gtk.Box(spacing=6, margin_start=6 + INDENT_STEP * b[1])
            marker = Gtk.Label(label=b[2], yalign=0, xalign=1, width_chars=2)
            marker.add_css_class("md-marker")
            body = sel_label(b[3])
            body.set_hexpand(True)
            body.list_prefix = "  " * b[1] + b[2] + " "  # (copied with it: ChatSelection)
            row.append(marker)
            row.append(body)
            row.body = body
            return row
        if kind == "quote":
            lbl = sel_label(b[1])
            lbl.add_css_class("md-quote")
            return lbl
        if kind == "hr":
            return Gtk.Separator(margin_top=4, margin_bottom=4)
        if kind == "code":
            block = CodeBlock(b[2], b[1], syntax=b[1] or None)  # (highlighted as its fence says: ```bash)
            block.message = REF_MESSAGE  # (its reply: markdown_blocks just read it)
            block.relink()
            return block
        if kind == "image":
            return self.render_image(b[1], b[2])
        if kind == "table":
            return markdown_table(b[1])
        return sel_label(html.escape(str(b)))

    def render_image(self, alt, src):
        path = src[7:] if src.startswith("file://") else src
        path = os.path.join(self.base_dir, os.path.expanduser(path))
        if "://" not in src or src.startswith("file://"):
            try:
                with open(path, "rb") as f:
                    return image_widget(f.read(), path=path, name=alt)
            except (OSError, GLib.Error):
                pass
        link = sel_label(f'Image: <a href="{html.escape(src)}">{html.escape(alt or src)}</a>')
        if not src.startswith(("http://", "https://")):
            return link
        holder = Gtk.Box()  # the link until the image arrives (or if it can't be loaded)
        holder.append(link)

        def done(data, error):
            if data is not None:
                try:
                    pic = image_widget(data, url=src, name=alt)
                except GLib.Error as e:
                    link.set_tooltip_text(f"Couldn't show the image: {e.message}")
                    return
                holder.remove(link)
                holder.append(pic)
            else:
                link.set_tooltip_text(f"Couldn't load the image: {error}")
        fetch_web_image(src, done)
        return holder


def themed_icon(name, fallback):
    """name if the current icon theme has it, else fallback (themes differ: Yaru vs Adwaita)."""
    ensure_icons()
    display = Gdk.Display.get_default()
    if name and display is not None and Gtk.IconTheme.get_for_display(display).has_icon(name):
        return name
    return fallback


HOME_NAME = "~"


def time_ago(at, now=None):
    """How long ago a time was, in short: just now, 5m ago, 3h ago, 2d ago, 3w ago, 5mo ago, 2y ago (a clock that's behind:
    just now). To show one, say_time: it says the absolute time too."""
    secs = max(0, int((time.time() if now is None else now) - at))
    for unit, n in (("y", 365 * 86400), ("mo", 30 * 86400), ("w", 7 * 86400), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n:
            return f"{secs // n}{unit} ago"
    return "just now"


def full_time(at):
    """A time in full, local: 00:00 Mon 4th May 2020. What every relative time says under the pointer (time_tip)."""
    t = time.localtime(at)
    suffix = "th" if 10 <= t.tm_mday % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(t.tm_mday % 10, "th")
    return time.strftime("%H:%M %a ", t) + f"{t.tm_mday}{suffix} " + time.strftime("%B %Y", t)


def say_time(label, at, before="", after="", markup=False):
    """A label saying how long ago a time was, between what's before and after it (markup: those are Pango markup), and
    the time in full with the pointer on it (time_tip). Said again by retime. The label, back."""
    ago = time_ago(at)
    if getattr(label, "timed", None) != (at, before, after, markup, ago):
        (label.set_markup if markup else label.set_text)(before + ago + after)
        time_tip(label, at, ago)
        label.timed = (at, before, after, markup, ago)
    return label


def time_tip(label, at, part=None):
    """The time in full as a label's tooltip, with the pointer on its relative time: part of what it says (None: all of
    it). Elsewhere in it, its own tooltip or its parent's, as ever. at None: no more of that (a cell used again, the words
    changed). After any set_tooltip_text, which turns tooltips off without one. The label, back."""
    if at is None and getattr(label, "tip_time", None) is None:
        return label
    label.tip_time, label.timed = None if at is None else (at, part), None
    if at is not None and not getattr(label, "tip_watched", False):
        label.tip_watched = True
        label.connect("query-tooltip", _time_tooltip)
    label.set_has_tooltip(at is not None or bool(label.get_tooltip_text()))
    return label


def time_tip_at(label, x, y):
    """What a time_tip label says with the pointer at (x, y) in it: the time in full, or None off its relative time."""
    at, part = getattr(label, "tip_time", None) or (None, None)
    if at is None:
        return None
    if part is not None:
        layout = label.get_layout()
        text, (left, top) = layout.get_text(), label.get_layout_offsets()
        found = text.rfind(part)
        inside, index, _trailing = layout.xy_to_index(int((x - left) * Pango.SCALE), int((y - top) * Pango.SCALE))
        start = len(text[:found].encode())  # (Pango's indexes are in bytes)
        if found < 0 or not inside or not start <= index < start + len(part.encode()):
            return None
    return full_time(at)


def _time_tooltip(label, x, y, _keyboard, tooltip):
    text = time_tip_at(label, x, y)
    if text is None:
        return False
    tooltip.set_text(text)
    return True


def subtitle_time(row, at, before=""):
    """say_time for an Adw.ActionRow's subtitle (its label is the row's own)."""
    ago = time_ago(at)
    row.set_subtitle(GLib.markup_escape_text(before + ago))
    label = next((w for w in walk(row) if isinstance(w, Gtk.Label) and w.has_css_class("subtitle")), None)
    if label is None:  # (a libadwaita that lays its rows out otherwise: the whole row says it)
        row.set_tooltip_text(full_time(at))
    else:
        time_tip(label, at, ago)
    return row


def retime(widget):
    """The say_time labels in a widget say how long ago again (where it's read again anyway, or keep_times)."""
    for w in walk(widget):
        if isinstance(w, Gtk.Label) and (timed := getattr(w, "timed", None)):
            say_time(w, *timed[:4])


def keep_times(widget):
    """A widget's say_time labels kept right while it's shown, for what's only read again when something changes: as it
    comes back, and each minute."""
    def tick():
        if widget.get_mapped():
            retime(widget)
            return True
        widget.times_tick = None
        return False

    def shown(*_):
        retime(widget)
        if widget.times_tick is None:
            widget.times_tick = GLib.timeout_add_seconds(60, tick)
    widget.times_tick = None
    widget.connect("map", shown)


def pretty_path(path):
    """A folder as people say it: the home folder is ~ and what's in it starts with ~/."""
    home = os.path.expanduser("~").rstrip("/")
    if not path:
        return path
    if path.rstrip("/") == home:
        return HOME_NAME
    if path.startswith(home + "/"):
        return "~" + path[len(home):]
    return path


FIND_RGB = (0xF6, 0xD3, 0x2D)  # what Find matches are highlighted with in labels and the schema's cards: Adwaita's yellow 3


def find_attrs(text, spans, attrs=None):
    """Pango attributes (added to attrs, if given) that highlight spans of text as Find's matches are: black on
    yellow, legible in the dark theme too. Pango counts in UTF-8 bytes."""
    attrs = attrs or Pango.AttrList()
    for i, j in spans:
        b0, b1 = len(text[:i].encode()), len(text[:j].encode())
        for a in (Pango.attr_background_new(*(c * 257 for c in FIND_RGB)), Pango.attr_foreground_new(0, 0, 0)):
            a.start_index, a.end_index = b0, b1
            attrs.insert(a)
    return attrs


def find_label(label, text, words, letters=False):
    """A label showing text with what was typed in it highlighted (find_spans' words and letters)."""
    label.set_label(text)
    label.set_attributes(find_attrs(text, find_spans(text, words, letters)) if words else None)


# ---------------------------------------------------------------- code view --
# Code shown in Baude itself (the side panel's Code page, and the chat's code blocks: a tool call's command, a file
# it read or wrote, an edit's diff, a reply's fenced code): highlighted. Highlighting is a backend that turns text
# into (start, end, token type) spans: Pygments (500+ languages) when it's installed, plain text when it isn't.
# Another backend (GtkSourceView once gir1.2-gtksource-5 is installed, tree-sitter) only has to produce the same
# spans. A regex's are Baude's own (regex_spans: Pygments has no lexer for one), in the same style's colours.

CODE_STYLES = {False: "default", True: "github-dark"}  # Pygments style, by dark mode
CODE_HIGHLIGHT_MAX = 20_000  # characters of a code block highlighted (~500 lines): its first, a longer one's rest plain
SHELL_LANGS = {"bash", "sh", "shell", "zsh", "ksh"}  # (a command's; and code fences': a grep's pattern a regex in them)
LINE_NUMBER = "line-number"  # the token type of a Read's line numbers: dim, as the Code page's gutter has them


def code_spans(text, path="", lang=""):
    """Token spans for text: [(start, end, token type)] in character offsets, by lang (a language's name or alias:
    bash, py, a code fence's tag; regex: regex_spans) or else path's file name. [] without Pygments, or for a language
    it has no lexer for. text must already use \\n line ends (Pygments normalizes them). In a shell command a grep's
    or rg's pattern is a regex (grep_patterns), Pygments or not."""
    if lang.lower() == "regex":
        return regex_spans(text)
    spans, lexer = [], None
    try:
        from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
        from pygments.token import Text, Whitespace
        from pygments.util import ClassNotFound
    except ImportError:
        pass
    else:
        opts = {"stripnl": False, "ensurenl": False, "tabsize": 0}
        try:
            lexer = get_lexer_by_name(lang, **opts) if lang else \
                get_lexer_for_filename(os.path.basename(path) or "x.txt", text[:4096], **opts)
        except ClassNotFound:
            pass
    if lexer is not None:
        pos = 0
        for ttype, value in lexer.get_tokens(text):
            end = pos + len(value)
            if value.strip() and ttype not in (Text, Whitespace):
                if spans and spans[-1][2] is ttype and spans[-1][1] == pos:
                    spans[-1] = (spans[-1][0], end, ttype)  # one span for a run of the same token
                else:
                    spans.append((pos, end, ttype))
            pos = end
        if pos != len(text):  # (Pygments dropped something, a byte-order mark: its offsets aren't text's)
            spans = []
    if lang.lower() in SHELL_LANGS or lexer is not None and "bash" in lexer.aliases:
        spans = _with_regexes(text, spans)
    return spans


_REGEX_TOKENS = {"escape": "Literal.String.Escape", "class": "Literal.String", "group": "Keyword",
                 "quantifier": "Name.Function", "anchor": "Name.Entity"}  # (Pygments' names: the code's colours)
# what CODE_STYLES give those (light, dark), for a regex drawn without Pygments
_REGEX_LOOKS = {"Literal.String.Escape": (("#AA5D1F", True, False), ("#79c0ff", False, False)),
                "Literal.String": (("#BA2121", False, False), ("#a5d6ff", False, False)),
                "Keyword": (("#008000", True, False), ("#ff7b72", False, False)),
                "Name.Function": (("#0000FF", False, False), ("#d2a8ff", True, False)),
                "Name.Entity": (("#717171", True, False), ("#ffa657", False, False))}
_RE_ESCAPE = re.compile(r"\\(?:x\{[0-9A-Fa-f]*\}|x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|[pPN]\{[^}]*\}|[pP][A-Za-z]|k<\w+>"
                        r"|\d+|.)", re.S)
_RE_GROUP = re.compile(r"\((?:\?(?:P?<\w+>|'\w+'|P=\w+\)|<[=!]|[:=!>|]|#[^)]*\)|[a-zA-Z^-]+[:)]))?")
_RE_COUNT = re.compile(r"\{(?:\d+(?:,\d*)?|,\d+)\}")


def regex_spans(text, bre=False):
    """A regex's parts as code_spans' spans: escapes, character classes, groups and alternation, quantifiers, anchors
    and the dot (what's left is literal: plain). bre: grep's basic syntax, where \\( \\) \\| \\{ \\} \\+ \\? are the
    operators and the bare ones literal."""
    spans, i, n, unclosed = [], 0, len(text), False  # (unclosed: a "[" that never closed; none after it does)

    def add(a, b, kind):
        spans.append((a, b, _REGEX_TOKENS[kind]))
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            d = text[i + 1]
            if bre and d in "()|":
                add(i, i + 2, "group")
                i += 2
            elif bre and d in "+?":
                add(i, i + 2, "quantifier")
                i += 2
            elif bre and d == "{" and (end := text.find("\\}", i + 2)) > 0 and \
                    re.fullmatch(r"\d+(,\d*)?|,\d+", text[i + 2:end]):
                add(i, end + 2, "quantifier")
                i = end + 2
            else:
                end = _RE_ESCAPE.match(text, i).end()
                add(i, end, "escape")
                i = end
        elif c == "[" and not unclosed and (end := _class_end(text, i, bre)):
            j, k = i, i + 1  # (escapes in it drawn as escapes, the rest the class's)
            while k < end - 1:
                if text[k] == "\\" and not bre and k + 1 < end - 1:
                    e = min(_RE_ESCAPE.match(text, k).end(), end - 1)
                    if k > j:
                        add(j, k, "class")
                    add(k, e, "escape")
                    j = k = e
                else:
                    k += 1
            add(j, end, "class")
            i = end
        elif not bre and c == "(":
            end = _RE_GROUP.match(text, i).end()
            add(i, end, "group")
            i = end
        elif not bre and c in ")|":
            add(i, i + 1, "group")
            i += 1
        elif c == "*" or not bre and (c in "+?" or c == "{" and _RE_COUNT.match(text, i)):
            end = _RE_COUNT.match(text, i).end() if c == "{" else i + 1
            if not bre and end < n and text[end] in "?+":  # (lazy, possessive)
                end += 1
            add(i, end, "quantifier")
            i = end
        elif c in "^$.":
            add(i, i + 1, "anchor")
            i += 1
        else:
            unclosed = unclosed or c == "["
            i += 1
    return spans


def _class_end(text, i, bre=False):
    """Where the character class opened at text[i] ("[") ends (just after its "]"), or None if it doesn't."""
    j, n = i + 1, len(text)
    if j < n and text[j] == "^":
        j += 1
    if j < n and text[j] == "]":  # (first, a "]" is one of its characters)
        j += 1
    while j < n:
        if text[j] == "\\" and not bre:
            j += 2
        elif text.startswith(("[:", "[=", "[."), j) and (k := text.find(text[j + 1] + "]", j + 2)) > 0:
            j = k + 2  # ([:alpha:])
        elif text[j] == "]":
            return j + 1
        else:
            j += 1
    return None


# A shell command's grep and rg: which of their options take a value (the word after them, or the rest of the word),
# and so which word's the pattern
_GREP_SHORT = {"grep": "ABCDdefm", "rg": "ABCEMTdefgjmrt"}
_GREP_LONG = {"--regexp", "--file", "--include", "--exclude", "--exclude-dir", "--max-count", "--context",
              "--after-context", "--before-context", "--label", "--binary-files", "--devices", "--directories",
              "--glob", "--iglob", "--type", "--type-not", "--type-add", "--max-depth", "--replace", "--encoding",
              "--max-columns", "--threads", "--sort", "--sortr", "--engine", "--max-filesize", "--pre", "--ignore-file"}
_RG_LONG = _GREP_LONG | {"--color", "--colors", "--context-separator", "--path-separator", "--field-match-separator",
                         "--pre-glob", "--dfa-size-limit", "--regex-size-limit"}
_GREP_DIALECT = {"grep": "bre", "zgrep": "bre", "egrep": "ere", "fgrep": None, "rg": "ere"}
_SH_TOKEN = re.compile(r"(?P<sep>&&|\|\||\$\(|[;&|()`\n])|(?P<space>[ \t]+|\\\n)|#[^\n]*"
                       r"|(?P<word>(?:'[^']*'?|\"(?:[^\"\\]|\\.)*\"?|\\.|[^\s'\"\\;&|()`])+)|.", re.S)
_SH_PREFIX = {"sudo", "xargs", "time", "nice", "env", "command", "exec", "then", "do", "else", "if", "elif", "while",
              "until", "!", "{"}  # (words before a command that aren't it)


def grep_patterns(cmd):
    """Where the patterns of a shell command's grep, egrep, git grep and rg calls are: [(start, end, bre)], the text
    inside a pattern's quotes. None for a fixed string (-F), a pattern read from a file (-f), or one the shell changes
    before grep sees it (a $ or ` in double quotes, a backslash or $ in a bare word)."""
    words, out, at_cmd = [], [], True
    for m in _SH_TOKEN.finditer(cmd):
        if m.group("sep"):
            words.append(None)
        elif m.group("word"):
            words.append((m.start(), m.end(), m.group()))
    k = 0
    while k < len(words):
        w = words[k]
        k += 1
        if w is None:
            at_cmd = True
            continue
        if not at_cmd:
            continue
        name = os.path.basename(w[2])
        if name in _SH_PREFIX or re.fullmatch(r"\w+=.*", w[2], re.S) or w[2].startswith("-"):
            continue  # (still before the command: sudo, an option of xargs', VAR=value)
        at_cmd = False
        if name == "git" and k < len(words) and words[k] and words[k][2] == "grep":
            name, k = "grep", k + 1
        if name not in _GREP_DIALECT:
            continue
        end = next((j for j in range(k, len(words)) if words[j] is None), len(words))
        out += _grep_args(cmd, "rg" if name == "rg" else "grep", _GREP_DIALECT[name], words[k:end])
        k = end
    return out


def _grep_args(cmd, tool, dialect, args):
    """A grep's (or rg's) patterns, of its words: [(start, end, bre)] (grep_patterns)."""
    given, first, i = [], None, 0  # (-e's patterns: (start, end) in cmd; the first word that's no option's)
    takes = _RG_LONG if tool == "rg" else _GREP_LONG
    while i < len(args):
        s, e, w = args[i]
        i += 1
        if w == "--":
            if first is None and i < len(args):
                first = args[i][:2]
            break
        if w.startswith("--"):
            opt, eq, _ = w.partition("=")
            dialect = {"--fixed-strings": None, "--extended-regexp": "ere", "--basic-regexp": "bre",
                       "--perl-regexp": "ere", "--pcre2": "ere", "--files": None, "--type-list": None}.get(opt, dialect)
            value = (s + len(opt) + 1, e) if eq else args[i][:2] if opt in takes and i < len(args) else None
            if opt in takes and not eq:
                i += 1
            if opt in ("--regexp", "--file") and value:
                given.append(value if opt == "--regexp" else None)
        elif w.startswith("-") and len(w) > 1:
            for j, ch in enumerate(w[1:], 1):
                if ch == "F":
                    dialect = None
                elif ch in "EGP" and not (tool == "rg" and ch == "E"):
                    dialect = "bre" if ch == "G" else "ere"
                if ch in _GREP_SHORT[tool]:
                    value = (s + j + 1, e) if j + 1 < len(w) else args[i][:2] if i < len(args) else None
                    if j + 1 == len(w):
                        i += 1
                    if ch in "ef" and value:
                        given.append(value if ch == "e" else None)
                    break
        elif first is None:
            first = (s, e)
    if dialect is None:
        return []
    out = []
    for span in given if given else [first] if first else []:
        if span is not None and (inside := _shell_literal(cmd, *span)):
            out.append((*inside, dialect == "bre"))
    return out


def _shell_literal(cmd, s, e):
    """(start, end) of what a shell word (cmd[s:e]) is to the command it's given to, if that's the word's own text
    inside its quotes, else None."""
    w = cmd[s:e]
    if len(w) >= 2 and w[0] == w[-1] == "'" and "'" not in w[1:-1]:
        return s + 1, e - 1
    if len(w) >= 2 and w[0] == w[-1] == '"' and not re.search(r'[$`"]|\\["\\$`]', w[1:-1]):
        return s + 1, e - 1
    if w and not re.search(r"[\\$'\"`]", w):
        return s, e
    return None


def _with_regexes(text, spans):
    """A shell command's spans with its greps' patterns drawn as regexes, in place of the strings they're in."""
    cuts = [(a, b, regex_spans(text[a:b], bre)) for a, b, bre in grep_patterns(text) if a < b]
    if not cuts:
        return spans
    out, starts = [], [a for a, _, _ in cuts]
    for s, e, t in spans:  # (what of each is outside the patterns, their quotes say: the patterns are in order)
        k, x = max(bisect.bisect_right(starts, s) - 1, 0), s
        while k < len(cuts) and cuts[k][0] < e:
            a, b, _ = cuts[k]
            if b > x:
                if a > x:
                    out.append((x, a, t))
                x = b
            k += 1
        if x < e:
            out.append((x, e, t))
    for a, _, found in cuts:
        out += [(a + x, a + y, t) for x, y, t in found]
    return sorted(out, key=lambda span: span[0])


_READ_NUMBER = re.compile(r" *\d+(?:→|\t)")  # Claude Code's Read: "   186→    text" (or a tab, as cat -n has it)


def numbered_spans(text, path):
    """A Read's output highlighted as path's code: each line its number (dim: LINE_NUMBER), then the file's line. A
    line that has no number (a note after the file) isn't code: it's left plain."""
    cuts, code, at = [], [], 0
    for line in text.split("\n"):
        m = _READ_NUMBER.match(line)
        cuts.append((at, m.end() if m else None))
        code.append(line[m.end():] if m else "")
        at += len(line) + 1
    src = "\n".join(code)
    out = []
    for (at, cut), spans in zip(cuts, line_spans(src, code_spans(src, path))):
        if cut is not None:
            out.append((at, at + cut, LINE_NUMBER))
            out += [(at + cut + s, at + cut + e, t) for s, e, t in spans]
    return out


def line_spans(text, spans):
    """spans (in text) cut at its line ends: for each line, its [(start, end, token type)] from the line's start."""
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    out = [[] for _ in starts]
    for s, e, t in spans:
        i = bisect.bisect_right(starts, s) - 1
        while i < len(starts) and s < e:
            stop = min(e, starts[i + 1] - 1 if i + 1 < len(starts) else len(text))
            if s < stop:
                out[i].append((s - starts[i], stop - starts[i], t))
            i += 1
            s = max(s, starts[i]) if i < len(starts) else e
    return out


_LOOKS = {}  # (token type, dark) -> code_style's answer


def code_style(ttype, dark):
    """(foreground, bold, italic) for a token type (Pygments', or its name: a regex's) in the light or dark Pygments
    style. Without Pygments, a regex's parts are in the colours those styles give them, and nothing else is coloured."""
    if (look := _LOOKS.get((ttype, dark))) is not None:
        return look
    try:
        from pygments.styles import get_style_by_name
        from pygments.token import string_to_tokentype
    except ImportError:
        look = _REGEX_LOOKS[ttype][dark] if ttype in _REGEX_LOOKS else (None, False, False)
    else:
        try:
            st = get_style_by_name(CODE_STYLES[dark]).style_for_token(
                string_to_tokentype(ttype) if isinstance(ttype, str) else ttype)
            look = (f"#{st['color']}" if st.get("color") else None), bool(st.get("bold")), bool(st.get("italic"))
        except Exception:  # noqa: BLE001 (a style Pygments doesn't have: plain)
            look = None, False, False
    _LOOKS[(ttype, dark)] = look
    return look


def code_attrs(text, spans, dark, skip=()):
    """Pango attributes drawing text's spans (code_spans') in the light or dark style's colours, but none in skip's
    (start, end) byte ranges (a link's: it has its own). Pango counts in UTF-8 bytes."""
    attrs, simple, pos, at = Pango.AttrList(), text.isascii(), 0, 0
    for s, e, ttype in spans:
        if simple:
            b0, b1 = s, e
        else:  # (spans come in order, none in another: each one's bytes counted on from the last's)
            at += len(text[pos:s].encode())
            b0 = at
            at += len(text[s:e].encode())
            b1, pos = at, e
        if skip and any(x < b1 and b0 < y for x, y in skip):
            continue
        if ttype == LINE_NUMBER:
            made = [Pango.attr_foreground_alpha_new(0x7000)]
        else:
            fg, bold, italic = code_style(ttype, dark)
            made = [Pango.attr_foreground_new(*(int(fg[k:k + 2], 16) * 257 for k in (1, 3, 5)))] if fg else []
            if bold:
                made.append(Pango.attr_weight_new(Pango.Weight.BOLD))
            if italic:
                made.append(Pango.attr_style_new(Pango.Style.ITALIC))
        for a in made:
            a.start_index, a.end_index = b0, b1
            attrs.insert(a)
    return attrs


class LineGutter(Gtk.Widget):
    """Line numbers beside a TextView (its left gutter), the marked line's in bold."""

    def __init__(self, view):
        super().__init__()
        self.view, self.marked = view, None
        self.drawn = []  # (line, y) of the numbers drawn last (tests hold them to their lines)
        self.add_css_class("code-gutter")

    def do_measure(self, orientation, _for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return 0, 0, -1, -1
        digits = len(str(max(1, self.view.get_buffer().get_line_count())))
        w = self.create_pango_layout("0" * (digits + 2)).get_pixel_size()[0]
        return w, w, -1, -1

    def do_snapshot(self, snap):
        view, width = self.view, self.get_width()
        rect = view.get_visible_rect()
        it, _top = view.get_line_at_y(rect.y)
        color = self.get_color()
        dim = Gdk.RGBA()
        dim.red, dim.green, dim.blue, dim.alpha = color.red, color.green, color.blue, color.alpha * 0.45
        layout = self.create_pango_layout("")
        self.drawn = []
        while True:
            y, h = view.get_line_yrange(it)
            if y > rect.y + rect.height:
                break
            n = it.get_line() + 1
            _x, wy = view.buffer_to_window_coords(Gtk.TextWindowType.LEFT, 0, y)
            layout.set_markup(f"<b>{n}</b>" if n == self.marked else str(n), -1)
            w, lh = layout.get_pixel_size()
            first = view.get_iter_location(it).height or h  # (a wrapped line: its first display line's height)
            self.drawn.append((n, wy))
            snap.save()
            snap.translate(Graphene.Point().init(width - w - 8, wy + max(0, (min(h, first) - lh) / 2)))
            snap.append_layout(layout, color if n == self.marked else dim)
            snap.restore()
            if not it.forward_line():
                break


def view_bar(spacing=6, margin_start=12, margin_end=10, margin_top=6, margin_bottom=6):  # (as tall as a header bar)
    """The top bar of a view that covers the window (Kubernetes, Database schema, an image large). On macOS the
    window's traffic lights still sit on top of it, over the header bar it covers: room for them. Its view puts it in a
    Gtk.WindowHandle, as the header bar is: dragged, it moves the window; double-clicked, maximizes it. (Not a second native
    Gtk.WindowControls: two of them fight over the titlebar's height, and the header bar can no longer be dragged or
    double-clicked)"""
    bar = Gtk.Box(spacing=spacing, margin_start=margin_start, margin_end=margin_end, margin_top=margin_top,
                  margin_bottom=margin_bottom)
    if sys.platform == "darwin":
        bar.append(Gtk.Box(width_request=80))
    return bar


def ellipsize_dropdown(dd, chars=30):
    """dd's button shows its choice shortened with … (a long server or database name would widen the bar past the
    window); its list shows every choice whole."""
    def factory(short):
        f = Gtk.SignalListItemFactory()
        f.connect("setup", lambda _f, item: item.set_child(Gtk.Label(
            xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE if short else Pango.EllipsizeMode.NONE,
            max_width_chars=chars if short else -1)))
        f.connect("bind", lambda _f, item: (item.get_child().set_label(item.get_item().get_string()),
                                            item.get_child().set_tooltip_text(item.get_item().get_string())))
        return f
    dd.set_factory(factory(True))
    dd.set_list_factory(factory(False))
    return dd


class HostPicker(Gtk.MenuButton):
    """A picker of choices by host, as ⋮'s menu lists its sections: each host's name a header, its choices under it,
    a rule between hosts. Its button shows the choice picked after its host's name (local / MLX), shortened with … past
    chars. on_pick(key) for the user's picks only, not for select() or set_choices()."""

    def __init__(self, tooltip, on_pick, chars=30):
        super().__init__(tooltip_text=tooltip, always_show_arrow=True, css_classes=["choice"])
        self.on_pick, self.labels, self.quiet = on_pick, {}, False
        self.shown = Gtk.Label(ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=chars)
        self.set_child(self.shown)
        self.action = Gio.SimpleAction.new_stateful("pick", GLib.VariantType.new("s"), GLib.Variant.new_string(""))
        self.action.connect("change-state", self._changed)
        group = Gio.SimpleActionGroup()
        group.add_action(self.action)
        self.insert_action_group("hostpicker", group)

    def _changed(self, action, value):
        action.set_state(value)
        self.shown.set_label(self.labels.get(value.get_string(), ""))
        if not self.quiet and value.get_string() in self.labels:
            self.on_pick(value.get_string())

    def set_choices(self, hosts):
        """hosts: [(host name, [(key, label)])], in order. The key picked stays picked if it's still there."""
        menu, self.labels = Gio.Menu(), {}
        for host, choices in hosts:
            section = Gio.Menu()
            for key, label in choices:
                item = Gio.MenuItem.new(label, None)
                item.set_action_and_target_value("hostpicker.pick", GLib.Variant.new_string(key))
                section.append_item(item)
                self.labels[key] = f"{host} / {label}"
            menu.append_section(host, section)
        self.set_menu_model(menu)
        self.select(self.value())

    def select(self, key):
        """Show key as picked (None, or one not listed: nothing), without on_pick."""
        self.quiet = True
        try:
            self.action.change_state(GLib.Variant.new_string(key if key in self.labels else ""))
        finally:
            self.quiet = False

    def value(self):
        key = self.action.get_state().get_string()
        return key if key in self.labels else None

    def label(self):
        return self.shown.get_label()


def empty_label(text):
    """A list's placeholder: a dim line of text."""
    lbl = Gtk.Label(label=text, margin_top=24, margin_bottom=24)
    lbl.add_css_class("dim-label")
    return lbl


# Baude's stylesheet: the chat's, and the viewers' (kubeview, schemaview, gitview, codeview), so wherever they run
# (Record's viewers.ViewerWindow too) they look as they do in Baude. Its classes are its own: none is Record's.
CSS = """
.update-bar button { font-weight: normal; }  /* (the text's own colour: white on a dark sidebar, as its rows are) */
.cut-note { margin-top: 2px; }
.cut-note button { min-height: 0; padding: 1px 8px; font-size: 0.9em; }
/* One look for Baude and Record: the frame round the work (the bars, the lists on the left, the panels on the
   right: one colour, no lines between them), the inside where it's read and written (a box set into the frame),
   one rule for every edge (the inside's, a card's, a table's), and one fill for what's raised inside it (a code
   block, a table's head, a bubble). libadwaita's own colours, so light and dark and the accent follow. */
@define-color baude_frame @sidebar_bg_color;
@define-color baude_inside @view_bg_color;
@define-color baude_rule alpha(@borders, 0.9);
@define-color baude_raised alpha(@view_fg_color, 0.06);
.baude-frame, .baude-frame headerbar { background-color: @baude_frame; box-shadow: none; }
/* (square; its edge a border, inside it: a ring outside it was clipped where it meets the bar above) */
.baude-inside { background-color: @baude_inside; border: 1px solid @baude_rule; }
/* the main area's tabs (MainTabs) on a window's bar (Record's), as tall as it, on the frame */
headerbar.tabs-head tabbar, headerbar.tabs-head tabbar .box { background: none; box-shadow: none; min-height: 0;
  padding-top: 0; padding-bottom: 0; }
headerbar.tabs-head tabbar tabbox { padding-top: 0; padding-bottom: 0; min-height: 0; }
headerbar.tabs-head tabbar scrolledwindow { min-height: 0; }
/* no focus ring: not on what's clicked, nor on what the keys move to, nor round a box being typed in. The theme's is on
   what has the focus or, these others, on a part of it. */
:focus:focus-visible, expander-widget:focus:focus-visible > box > title,
notebook:focus:focus-visible > header > tabs > tab, scale:focus:focus-visible > trough > slider,
tabbar tabbox > tabboxchild:focus-visible, tabbar tabbox > tabboxchild:focus-within:focus-visible,
tabgrid > tabgridchild:focus:focus-visible .card, .navigation-sidebar > row:focus-visible:focus-within,
entry:focus-within, spinbutton:focus-within, spinbutton.vertical > text:focus-within,
row.entry.focused, row.spin.focused, editablelabel:focus-within { outline: none; }
.user-bubble { background: alpha(@accent_bg_color, 0.15); border-radius: 12px; padding: 8px 12px; }
.tool-card { border: 1px solid @baude_rule; border-radius: 8px; padding: 6px 10px; }
.api-error { margin-left: 11px; margin-right: 11px; }  /* (its text where a .tool-card's is: border and padding in) */
.tool-card.error { border-color: @error_color; }
.tool-card.asking { border-color: @warning_color; }  /* (waiting for you to allow it: the approval's colour) */
/* every box's border is 1px, a card's that waits on you too (its colour says so); a quote's rule at the left 3px */
.perm-card { border: 1px solid @warning_color; border-radius: 8px; padding: 8px 12px; }
.thinking { opacity: 0.62; }  /* Claude's narration between steps: part of the conversation, quieter */
.mono { font-family: monospace; font-size: 0.9em; }
.statusbar { padding: 4px 10px; font-size: 0.85em; opacity: 0.75; }
.statusbar .chat-host { padding: 0 5px; border-radius: 4px; background: alpha(currentColor, 0.1); }
.sysnote { font-size: 0.85em; opacity: 0.6; }
.composer-actions button { min-width: 34px; min-height: 34px; padding: 0; }
.attach-chip { background: @baude_raised; border-radius: 8px; padding: 2px 2px 2px 4px; }
.attach-thumb { border-radius: 6px; }
.attach-chip.image { background: none; padding: 0; }
.codeblock { background: @baude_raised; border-radius: 6px; }
.kube-error-fix { background: @baude_raised; border-radius: 6px; padding: 2px 2px 2px 10px; }
.codeblock .code-scroll > viewport > label, .codeblock .code-scroll > label { padding: 6px 8px; }
.codeblock .code-scroll box { padding: 6px 0; }
/* the tag and copy button sit over the code's first line: give them the block's own colour (opaque,
   with a soft edge) so a long line runs under them instead of through them */
.codeblock .code-corner { padding: 3px 4px 3px 4px; border-radius: 0 6px 0 6px;
  background: mix(@baude_inside, @view_fg_color, 0.06);
  box-shadow: -10px 0 8px -2px mix(@baude_inside, @view_fg_color, 0.06); }
/* a wrapped block's lines can't be scrolled out from under them: they show while the pointer's on it */
.codeblock.wrapped .code-corner { opacity: 0; transition: opacity 150ms; }
.codeblock.wrapped:hover .code-corner, .codeblock.wrapped .code-corner:focus-within { opacity: 1; }
/* a one-line block stays one line tall: a vertical scrollbar's minimum would pad it out */
.codeblock .code-scroll > scrollbar.vertical, .codeblock .code-scroll > scrollbar.vertical > range,
.codeblock .code-scroll > scrollbar.vertical > range > trough { min-height: 0; }
.codeblock .code-scroll > scrollbar.vertical slider { min-height: 16px; }
.codeblock .code-lang { font-size: 0.8em; opacity: 0.5; }
.codeblock .code-copy { min-height: 22px; min-width: 22px; padding: 0; }
/* secondary icons share one fade: a fainter stroke reads as a thinner one */
.soft-icon, .codeblock .code-copy, .sidebar-plus { opacity: 0.75; }
.diff-del { background: alpha(@error_color, 0.16); padding: 0 8px; }
.diff-add { background: alpha(@success_color, 0.18); padding: 0 8px; }
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
.session-row { padding: 6px 4px; }
/* a compact list: rows 28px apart instead of libadwaita's 38 */
.session-sidebar list.navigation-sidebar > row { min-height: 26px; padding-top: 1px; padding-bottom: 1px;
  margin-top: 1px; margin-bottom: 0; }
.session-sidebar list.navigation-sidebar > row:first-child > box.sidebar-header { margin-top: 0; }
.session-sidebar .sidebar-action { min-height: 28px; padding-top: 0; padding-bottom: 0; font-weight: normal; }
dropdown.choice > button, menubutton.choice > button { font-weight: normal; }
/* a provider picker with one provider to pick (Baude's beside its model picker, Meta's in Settings): there, a little
   greyed, as there's nothing to choose */
.one-provider { opacity: 0.55; }
banner label.heading { font-weight: normal; }
.session-sidebar .sidebar-folder { font-size: 0.85em; font-weight: bold; opacity: 0.6; }
/* the Files page's tree: as compact as the sessions list */
listview.file-tree > row { min-height: 24px; padding-top: 1px; padding-bottom: 1px; margin-top: 1px; margin-bottom: 0; }
.session-sidebar .sidebar-folder.current { opacity: 1; color: @accent_color; }
.session-sidebar .fork-title { opacity: 0.85; }
.session-sidebar .row-tag { font-size: 0.75em; padding: 0 5px; border-radius: 4px;
  background: @baude_raised; color: alpha(@view_fg_color, 0.7); }
.feedback-thumb { padding: 0; border-radius: 8px; border: 1px solid @baude_rule; }
.feedback-tag { font-size: 0.8em; padding: 1px 6px; border-radius: 6px; }
/* (as .git-ref, Git's bubbles, a shade or two of its colours; its weight, not a head's bold) */
.fix-status { font-size: 0.78em; padding: 0 5px; border-radius: 4px; background: alpha(@window_fg_color, 0.08);
              color: alpha(@window_fg_color, 0.7); }
.fix-status.working { background: alpha(@accent_bg_color, 0.14); color: @accent_color; }
.fix-status.ready { background: alpha(#c88800, 0.16); color: #c88800; }
.fix-status.merged, .fix-status.resolved { background: alpha(@success_color, 0.14); color: @success_color; }
.fix-status.rejected { background: alpha(@error_color, 0.14); color: @error_color; }
.fix-round { border: 1px solid @baude_rule; border-radius: 8px; padding: 10px 12px; }
.fix-round.failed { border-color: @error_color; }
.fix-shot { border: 1px solid @baude_rule; }
.capture-bar { padding: 4px 6px; }
.tool-card.flash { background: alpha(@accent_bg_color, 0.12); border-radius: 8px; transition: background 400ms; }
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
/* the side panel sits on the window background, split by a 1px rule; lists are outlined, not shadowed */
.side-panel { background: transparent; }
.side-question { border: 1px solid @baude_rule; border-radius: 10px; padding: 10px 12px; }
.side-panel .boxed-list { box-shadow: none; border: 1px solid @baude_rule; }
/* (the inside's edge is the line. Filled as the frame is: left clear, the pixel between the panes shows the darker window
   behind them, a second line beside it. The paned too, as the rule's moved off that pixel, below) */
paned.main-split { background-color: @baude_frame; }
paned.main-split > separator { min-width: 1px; background: @baude_frame; box-shadow: none; }
/* where it's dragged from, GTK's 6px either side of the rule: on the inside's edge, not that gap short of it */
paned.main-split.inside-after > separator { margin-left: 6px; margin-right: -6px; }
paned.main-split.inside-before > separator { margin-left: -6px; margin-right: 6px; }
.composer-box { border-radius: 12px; box-shadow: 0 0 0 1px @baude_rule; }
.composer-head { background-color: @view_bg_color; }  /* (as the text under it is: Composer.head) */
.composer-box:disabled { opacity: 0.55; }
.ref-pop contents { padding: 4px; }  /* (a list to pick from as you type: PickList) */
.ref-pop listview { background: none; }
.lightbox { color: white; }  /* (what's behind it dimmed: Lightbox.do_snapshot) */
.lightbox .dim-label { color: alpha(white, 0.7); }
.lightbox-arrow { min-width: 44px; min-height: 44px; }
.image-corner { min-width: 26px; min-height: 26px; padding: 0; opacity: 0.85; }
.md-table-scroll { margin: 2px 0; }
.md-table { border: 1px solid @baude_rule; border-radius: 8px; }
.md-table .md-head { background: @baude_raised; padding: 5px 12px;
                     border-bottom: 1px solid @baude_rule; }
.md-table .md-cell { padding: 4px 12px; border-bottom: 1px solid alpha(@baude_rule, 0.5); }
.artifact-chip { border: 1px solid @baude_rule; border-radius: 10px; padding: 8px 10px; }
.artifact-chip .artifact-icon { color: @accent_color; }
.tasks-indicator { padding: 0 8px; min-height: 0; font-size: 0.95em; }
.ref-kind { font-size: 0.85em; font-weight: bold; opacity: 0.6; }
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
.code-view { font-family: monospace; font-size: 0.9em; }
.code-view text { background: transparent; }
.code-gutter { font-family: monospace; font-size: 0.9em; }
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
columnview.kube-table > listview > row > cell { padding-top: 5px; padding-bottom: 5px; }
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
"""


class TreeList(Gtk.ListBox):
    """A ListBox whose nested rows hang off one joined line per parent, like a file tree: down from
    under the parent's anchor to its last (visible) child, then a corner across to it. Rows nest by
    `tree_parent` (a row) and are indented by INDENT per level; `tree_anchor` is the widget the line
    starts under (the parent's) and points at (the child's)."""
    INDENT = 18

    def __init__(self, line_x=5, **kw):
        super().__init__(**kw)
        self.line_x = line_x  # how far into the parent's anchor the line runs down
        self.lines = None  # the lines' rectangles, worked out at the first draw after each layout

    def do_size_allocate(self, width, height, baseline):
        Gtk.ListBox.do_size_allocate(self, width, height, baseline)
        self.lines = None  # (not every draw: a working session's pulsing dot redraws the list every frame)

    def tree_lines(self):
        last, row, out = {}, self.get_first_child(), []
        while row is not None:
            p = getattr(row, "tree_parent", None)
            if p is not None and row.is_drawable() and p.is_drawable():
                last[p] = row
            row = row.get_next_sibling()
        for p, child in last.items():
            ok, pb = p.tree_anchor.compute_bounds(self)
            ok2, cb = child.tree_anchor.compute_bounds(self)
            if not (ok and ok2):
                continue
            x = round(pb.get_x() + self.line_x)
            top = round(pb.get_y() + pb.get_height() + 1)
            mid = round(cb.get_y() + cb.get_height() / 2)
            end = round(cb.get_x() - 4)
            if mid > top and end > x:
                out += [Graphene.Rect().init(x, top, 1, mid - top + 1), Graphene.Rect().init(x + 1, mid, end - x - 1, 1)]
        return out

    def do_snapshot(self, snapshot):
        Gtk.ListBox.do_snapshot(self, snapshot)
        if self.lines is None:
            self.lines = self.tree_lines()
        if self.lines:
            color = self.get_color()
            color.alpha *= 0.3
            for rect in self.lines:
                snapshot.append_color(color, rect)


def sidebar_action(icon, label, tip, fn):
    """A list's New … button, as Baude's sessions have New session at their top: an icon and a word, flat."""
    box = Gtk.Box(spacing=10)
    box.append(Gtk.Image.new_from_icon_name(icon))
    box.append(Gtk.Label(label=label, xalign=0))
    btn = Gtk.Button(child=box, tooltip_text=tip)
    btn.add_css_class("flat")
    btn.add_css_class("sidebar-action")
    btn.connect("clicked", lambda *_: fn())
    return btn


# A name too long for its row in a list on the left (a chat's, a page's, a notebook's): it fades out at the end it's cut
# at, instead of ending in "…" (FadeLabel)
CUT_FADE = 28  # px: how far in from the cut the fade begins


def _fade_stops():
    if not hasattr(_fade_stops, "stops"):
        stops = []
        for offset, alpha in ((0.0, 1.0), (1.0, 0.0)):  # (what's drawn: as it is, then gone)
            stop = Gsk.ColorStop()
            stop.offset, stop.color = offset, Gdk.RGBA(red=0, green=0, blue=0, alpha=alpha)  # (a copy: set whole)
            stops.append(stop)
        _fade_stops.stops = stops
    return _fade_stops.stops


def snapshot_faded(snapshot, draw, width, height, at_start):
    """draw(snapshot), what's cut at width, with its last CUT_FADE px faded out: the right end, or at_start the left
    (right-to-left text). The mask is only over the end that fades: one over the whole of it costs the renderer more."""
    fade = min(CUT_FADE, width / 3)
    if at_start:
        strip, start, end = Graphene.Rect().init(0, 0, fade, height), Graphene.Point().init(fade, 0), \
            Graphene.Point().init(0, 0)
        rest = Graphene.Rect().init(fade, 0, width - fade, height)
    else:
        strip, start, end = Graphene.Rect().init(width - fade, 0, fade, height), \
            Graphene.Point().init(width - fade, 0), Graphene.Point().init(width, 0)
        rest = Graphene.Rect().init(0, 0, width - fade, height)
    snapshot.push_clip(rest)  # (the most of it, as it is)
    draw(snapshot)
    snapshot.pop()
    snapshot.push_mask(Gsk.MaskMode.ALPHA)  # (the mask, then what it masks)
    snapshot.append_linear_gradient(strip, start, end, _fade_stops())
    snapshot.pop()
    draw(snapshot)
    snapshot.pop()


class FadeLabel(Gtk.Label):
    """A one-line label that, too long for the width it's given, is cut and fades out at the cut instead of ending in
    "…": as narrow as anything (its minimum width is nothing), the whole of it its natural width (or max_chars
    characters' worth, the most). Right-to-left text shows its start, at the right, and fades at the left. Whatever's
    behind it (a row hovered or selected, light or dark) shows through the fade: nothing's painted over it. Its text
    isn't laid out again as its width changes, as an ellipsized label's is."""

    __gtype_name__ = "BaudeFadeLabel"

    def __init__(self, max_chars=0, **kw):
        super().__init__(**kw)
        self.max_chars = max_chars
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def do_measure(self, orientation, for_size):
        low, nat, low_base, nat_base = Gtk.Label.do_measure(self, orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            low, low_base, nat_base = 0, -1, -1
            if self.max_chars:
                metrics = self.get_pango_context().get_metrics(None, None)
                nat = min(nat, math.ceil(self.max_chars * metrics.get_approximate_char_width() / Pango.SCALE))
        return low, nat, low_base, nat_base

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        layout = self.get_layout()
        wide = layout.get_pixel_extents()[1].width
        if wide <= width:
            Gtk.Label.do_snapshot(self, snapshot)
            return
        # (cut: its start shown, whichever way it reads. GtkLabel holds it to the left, xalign 0; to the right where the
        # widgets are laid out right to left)
        rtl = layout.get_direction(0) == Pango.Direction.RTL
        held_right = self.get_direction() == Gtk.TextDirection.RTL
        shift = 0 if rtl == held_right else (wide - width if held_right else width - wide)

        def draw(s):
            if shift:
                s.save()
                s.translate(Graphene.Point().init(shift, 0))
            Gtk.Label.do_snapshot(self, s)
            if shift:
                s.restore()
        snapshot_faded(snapshot, draw, width, height, at_start=rtl)


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


# ---- popovers on X11 under picom. A popover's window is its box with GTK's own shadow round it (transparent, ~20px a
# side) and its arrow's room; picom (or compton, or xcompmgr -c) puts a shadow of its own round the whole window, a
# larger rectangle round the box. There they're drawn without GTK's shadow (an outline ring inside the box) and with no
# arrow, so the window is the box and picom's shadow is the popover's. Their corners are libadwaita's (15px round);
# picom's shadow is square unless its corner-radius rounds the window too.
SHADOWING_COMPOSITORS = ("picom", "compton", "xcompmgr")
FLAT_POPOVER_CSS = """
popover:not(.touch-selection):not(.magnifier) > contents { box-shadow: inset 0 0 0 1px alpha(currentColor, 0.15); }
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
    """Popovers drawn without GTK's shadow (FLAT_POPOVER_CSS) or an arrow while display is X11 composited by picom:
    looked at again as the compositor comes and goes. Once a widget exists (the hook is on GtkWidget's realize)."""
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


# The app's own sizes (Settings → Size), the same in every window and pane, Baude's and Record's: the text's and the
# icons'. 1.0 is the usual. And light or dark as macOS is (macos_appearance).
SCALE_MIN, SCALE_MAX = 0.7, 2.0


ZOOM_MODS = ("<Control>", "<Meta>") if sys.platform == "darwin" else ("<Control>",)


SETTINGS_KEY = "<Meta>comma" if sys.platform == "darwin" else "<Control>comma"  # (⌘, as a Mac app's Settings…)
FEEDBACK_KEY = "<Meta>period" if sys.platform == "darwin" else "<Control>period"  # (Send Feedback…, over whatever's open)
META_KEY = "<Control>m"  # (Meta shown and focused, or closed if open; Control on macOS too: ⌘M is Minimize)


def zoom_keys(*keys):
    """The zoom's triggers for these keys, with each of ZOOM_MODS."""
    return " ".join(mod + key for mod in ZOOM_MODS for key in keys)


def scale_setting(key):
    """Settings → Size: "text_scale" or "icon_scale" (1.0 the usual)."""
    return round(min(SCALE_MAX, max(SCALE_MIN, float(load_settings().get(key) or 1.0))), 2)


# ---- code's size beside the prose: the code font's own, the size it looks the prose's at (fontsetup's, measured once
# and for all for the fonts it offers; matched_code_scale's otherwise). What's drawn asks code_scale()
CODE_SAMPLE = "The table's built in price_table and cached; check it with the tests, then push the branch."
CODE_HEIGHT_WEIGHT = 2 / 3  # (the lowercase's height counts twice what the letters' width does: it's what size reads as)
_code = {"scale": None, "css": None, "provider": None}


def matched_code_scale():
    """The size the code font ("monospace": fontsetup's) looks the prose's size at, measured, as a share of the
    prose's: its lowercase as tall as the prose's (x-height), and its letters, which are all one width, as wide as the
    prose's are on average, met between: a geometric mean of the two that weighs the height twice the width
    (CODE_HEIGHT_WEIGHT), as size reads mostly by the lowercase's height. Menlo's, beside macOS's system font, is about
    0.83; Courier New's 1.0."""
    ctx = Gtk.Label().get_pango_context()  # (the prose's: the app's font)

    def measure(desc):
        desc = desc.copy()
        desc.set_size(100 * Pango.SCALE)
        layout = Pango.Layout.new(ctx)
        layout.set_font_description(desc)
        layout.set_text("x", -1)
        x = layout.get_extents()[0].height
        layout.set_text(CODE_SAMPLE, -1)
        return x, layout.get_extents()[1].width / len(CODE_SAMPLE)
    (px, pw), (mx, mw) = measure(ctx.get_font_description()), measure(Pango.FontDescription.from_string("monospace"))
    if not (px > 0 and pw > 0 and mx > 0 and mw > 0):
        return 1.0
    a = CODE_HEIGHT_WEIGHT
    return round(min(1.2, max(0.6, (px / mx) ** a * (pw / mw) ** (1 - a))), 3)


def code_scale():
    """What code is drawn at, beside the prose: the code font's matched size, fontsetup's for the fonts it offers,
    measured here (once) for any other."""
    if _code["scale"] is None:
        _code["scale"] = fontsetup.known_scale() or matched_code_scale()
    return _code["scale"]


def code_markup_size():
    """Code's size in markup, beside the text around it: a span's size="…%"."""
    return f"{round(code_scale() * 100)}%"


def apply_code_size(display):
    """Code's sizes in the CSS (code blocks, the code viewer and its gutter, a member's chip a little under):
    code_scale's."""
    s = code_scale()
    css = (f".mono, .code-view, .code-gutter {{ font-size: {s:.3f}em; }}\n"
           f".code-member {{ font-size: {s * 0.94:.3f}em; }}\n")
    if _code["provider"] is None:
        _code["provider"] = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, _code["provider"], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    if css != _code["css"]:
        _code["css"] = css
        _code["provider"].load_from_string(css)


def apply_scales(display, also=()):
    """The text's size and the icons' (Settings → Size), app-wide. The text's is the display's font resolution, so all
    of it follows, libadwaita's headings, the panes' headers and its rem-sized labels too (a font size on a pane reaches
    none of those); the icons' is their usual 16px and libadwaita's 32px .large-icons. Only what changed is set: either
    restyles every window."""
    settings = Gtk.Settings.get_for_display(display)
    apply_code_size(display)
    scale = scale_setting("text_scale")
    if scale != 1.0:
        if _scales["dpi"] is None:
            _scales["dpi"] = settings.props.gtk_xft_dpi if settings.props.gtk_xft_dpi > 0 else 96 * 1024
        if settings.props.gtk_xft_dpi != round(_scales["dpi"] * scale):
            settings.props.gtk_xft_dpi = round(_scales["dpi"] * scale)
    elif _scales["dpi"] is not None:
        _scales["dpi"] = None
        settings.reset_property("gtk-xft-dpi")  # (the system's again, and its changes followed)
    icons = scale_setting("icon_scale")
    css = "" if icons == 1.0 else (f"window, .normal-icons {{ -gtk-icon-size: {round(16 * icons)}px; }} "
                                   f".large-icons {{ -gtk-icon-size: {round(32 * icons)}px; }}")
    if _scales["icons"] is None:
        _scales["icons"] = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, _scales["icons"], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    if css != _scales["icons_css"]:
        _scales["icons_css"] = css
        _scales["icons"].load_from_string(css)
    _scales["text"] = scale
    if round(16 * icons) != _scales["icon_px"]:
        _scales["icon_px"] = round(16 * icons)
        for widget in [*also, *RefIcon.live]:  # (sized in Python, not by the CSS: told)
            widget.queue_resize()


# The accent (Settings → Accent Colour), Baude's and Record's, the one setting: one of libadwaita's, or the system's.
# The CSS follows it (@accent_bg_color, the system's otherwise, and what libadwaita makes of it: --accent-bg-color,
# @accent_color); what's drawn here asks accent_rgba and accent_text_rgba.
ACCENTS = {"blue": Adw.AccentColor.BLUE, "teal": Adw.AccentColor.TEAL, "green": Adw.AccentColor.GREEN,
           "yellow": Adw.AccentColor.YELLOW, "orange": Adw.AccentColor.ORANGE, "red": Adw.AccentColor.RED,
           "pink": Adw.AccentColor.PINK, "purple": Adw.AccentColor.PURPLE, "slate": Adw.AccentColor.SLATE}
_accent = {"name": None, "provider": None, "css": None}  # (the one picked, None for the system's, and its CSS)


def accent():
    """The accent, an Adw.AccentColor: the one picked, or the system's."""
    name = _accent["name"]
    return ACCENTS[name] if name in ACCENTS else Adw.StyleManager.get_default().get_accent_color()


def accent_rgba():
    """The accent's colour as @accent_bg_color has it: a patch's, a selection's."""
    return Adw.AccentColor.to_rgba(accent())


def accent_text_rgba():
    """The accent's colour as @accent_color has it, for text on the window's background: light or dark, as it's now."""
    return Adw.AccentColor.to_standalone_rgba(accent(), Adw.StyleManager.get_default().get_dark())


def apply_accent(display):
    """The accent settings.json has, in every window. Changed, what's drawn here is drawn again, a chip's name
    recoloured, what keeps its own colours told (accent_changed), as the CSS is restyled."""
    name = load_settings().get("accent")
    name = name if name in ACCENTS else None
    rgba = Adw.AccentColor.to_rgba(ACCENTS[name]).to_string() if name else None
    css = f"@define-color accent_bg_color {rgba};\n:root {{ --accent-bg-color: {rgba}; }}" if name else ""
    if _accent["provider"] is None:
        _accent["provider"] = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, _accent["provider"], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    if css == _accent["css"]:
        return
    again = _accent["css"] is not None
    for win in Gtk.Window.list_toplevels() if again else ():
        if isinstance(win, AccentFade):
            win.fade_accent()
    _accent["name"], _accent["css"] = name, css
    _accent["provider"].load_from_string(css)
    for win in Gtk.Window.list_toplevels() if again else ():
        for widget in walk(win):
            if isinstance(widget, LinkLabel) and widget.chips:
                widget.place_icons()
            if (changed := getattr(widget, "accent_changed", None)) is not None:  # (its own colours: Record's links)
                changed()
            widget.queue_draw()


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
    row = Adw.ComboRow(title="Accent Colour", model=Gtk.StringList.new(names),
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
    row = Adw.ComboRow(title="Code Font", model=Gtk.StringList.new(fonts), selected=fonts.index(fontsetup.code_font()))
    restart = Gtk.Button(label="Restart", valign=Gtk.Align.CENTER)
    # (in Settings, closed first; under a reply of Meta's, there's no dialog to close)
    restart.connect("clicked", lambda b: ((d := b.get_ancestor(Adw.Dialog)) is not None and d.close(),
                                          win.activate_action("win.restart", None)))
    row.add_suffix(restart)

    def show(font):
        restart.set_visible(font != running)
        row.set_subtitle("" if font == running else f"{font} from {app}'s next start")

    def changed(r, _p):
        if not r.syncing:
            save_settings(code_font=fonts[r.get_selected()])
        show(fonts[r.get_selected()])
    row.connect("notify::selected", changed)
    show(fontsetup.code_font())
    return follow_setting(row, "code_font", lambda v: v in fonts and row.set_selected(fonts.index(v)))


def _data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


# the glossary's search: ⌘K on macOS (GTK calls Command Meta there), Ctrl+K elsewhere, as editors have it
PALETTE_KEY = "<Meta>k" if sys.platform == "darwin" else "<Control>k"


class RefPaletteRow(GObject.Object):
    """A row of the glossary's search: one name (a ref_palette_items item), and the words typed that found it."""
    __gtype_name__ = "BaudeRefPaletteRow"

    def __init__(self, item, words=()):
        super().__init__()
        self.item, self.words = item, list(words)


class RefPalette(Adw.Dialog):
    """⌘K / Ctrl+K: every name in the glossary, and every one Signal's catalogue has (files, branches, Kubernetes
    objects and contexts, running workloads, database tables: with Signal off too), searched as you type (each word,
    in any order; the best match first), in a table as the Kubernetes view's: Name (with the logo its links have),
    Kind, Where. Enter, or a click, opens it (on_open(item): Baude opens a looked-up name as its link does, Record
    where it is, in its viewers); Up and Down (Page Up, Page Down) pick; Esc closes. The catalogue is made afresh if
    it's old, and the table follows. space: a lookup.SearchSpace (Baude's of its chat's Signal, Record's of its
    notebook's folders)."""
    LIMIT = 400  # rows shown at most (a glossary has thousands)

    def __init__(self, win, space, on_open):
        super().__init__(title="Glossary", content_width=760, content_height=520)
        self.win, self.space, self.on_open = win, space, on_open
        self.signal = getattr(space, "source", None)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=12, margin_end=12, margin_top=12,
                      margin_bottom=8)
        self.entry = Gtk.SearchEntry(placeholder_text="Search", hexpand=True)
        self.entry.connect("search-changed", lambda *_: self.fill())
        self.entry.connect("activate", lambda *_: self.choose(self.selection.get_selected_item()))
        self.entry.connect("stop-search", lambda *_: self.close())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.entry.add_controller(keys)
        box.append(self.entry)
        self.store = Gio.ListStore(item_type=RefPaletteRow)
        self.selection = Gtk.SingleSelection(model=self.store, autoselect=True, can_unselect=False)
        self.table = self.make_table(self.selection, (300, 150, 220),
                                     lambda pos: self.choose(self.store.get_item(pos)))
        self.scroll = Gtk.ScrolledWindow(child=self.table, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        box.append(self.scroll)
        self.count = Gtk.Label(xalign=0)
        self.count.add_css_class("dim-label")
        self.count.add_css_class("caption")
        # (spinning while the catalogue is made: the count isn't final yet)
        self.spinner = Adw.Spinner(visible=False, width_request=12, height_request=12)
        foot = Gtk.Box(spacing=6)
        foot.append(self.count)
        foot.append(self.spinner)
        box.append(foot)
        self.set_child(box)
        # (while the catalogue is made: checked for, to list it once it's in)
        self.waiting = self.space.refresh(self.catalogue_in)
        self.spinner.set_visible(self.waiting is not None)
        self.connect("closed", lambda *_: self.waiting and GLib.source_remove(self.waiting))
        self.items = self.space.items()
        self.fill()
        self.connect("map", lambda *_: self.entry.grab_focus())

    # ---- the table (the multiple-matches popover's too)
    @classmethod
    def make_table(cls, model, widths, on_activate):
        """A ColumnView of RefPaletteRows: Name (with its logo), Kind, Where, at these widths; a click activates."""
        table = Gtk.ColumnView(model=model, show_row_separators=False, show_column_separators=False,
                               single_click_activate=True, reorderable=False)
        table.add_css_class("data-table")
        table.add_css_class("kube-table")
        for (title, cell), width in zip((("Name", cls.name_cell), ("Kind", cls.kind_cell), ("Where", cls.where_cell)),
                                        widths):
            f = Gtk.SignalListItemFactory()
            f.connect("setup", cell)
            f.connect("bind", lambda _f, li, title=title: cls.bind(li, title))
            col = Gtk.ColumnViewColumn(title=title, factory=f, expand=title != "Kind", resizable=True)
            col.set_fixed_width(width)
            table.append_column(col)
        header = table.get_first_child()  # (no headings: the columns say what they are)
        if header is not None and header.get_css_name() == "header":
            header.set_visible(False)
        table.connect("activate", lambda _t, pos: on_activate(pos))
        return table

    @staticmethod
    def name_cell(_f, li):
        line = Gtk.Box(spacing=8)
        line.append(RefIcon())
        line.append(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE))
        li.set_child(line)

    @staticmethod
    def kind_cell(_f, li):
        label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        label.add_css_class("dim-label")
        li.set_child(label)

    where_cell = kind_cell

    @staticmethod
    def bind(li, title):
        row, w = li.get_item(), li.get_child()
        item, words = row.item, row.words  # (what was typed: highlighted where it matched)
        if title == "Name":
            icon, label = w.get_first_child(), w.get_last_child()
            icon.icon = item["icon"]
            icon.queue_draw()
            find_label(label, item.get("label") or item["surface"], words, letters=True)
            label.set_tooltip_text(item["surface"] if len(item["surface"]) > 40 or item.get("label") else None)
        else:
            text = item["what"] if title == "Kind" else item["summary"]
            find_label(w, text, words)
            w.set_tooltip_text(text if len(text) > 30 else None)
            if title != "Kind":
                time_tip(w, item.get("at"), item.get("ago"))
        w.set_opacity(0.6 if item["faint"] else 1.0)  # (a place's older version: fainter)

    # ---- filling and picking
    def catalogue_in(self):
        self.waiting = None
        self.spinner.set_visible(False)
        self.items = self.space.items()
        self.fill()

    def fill(self):
        q = self.entry.get_text().strip().lower()
        self.items = self.space.items()
        shown, matched = self.space.search(q, self.LIMIT)
        self.store.splice(0, self.store.get_n_items(), [RefPaletteRow(it, q.split()) for it in shown])
        if self.store.get_n_items():
            self.selection.set_selected(0)
            self.table.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)
        self.count.set_label(f"{matched} out of {len(self.items)}")

    def shown(self):
        """The names shown, in order."""
        return [self.store.get_item(i).item["surface"] for i in range(self.store.get_n_items())]

    def selected(self):
        row = self.selection.get_selected_item()
        return row.item if row is not None else None

    def on_key(self, _c, keyval, _code, _state):
        """Up and Down (and a page of them) pick in the table while you type."""
        step = {Gdk.KEY_Up: -1, Gdk.KEY_Down: 1, Gdk.KEY_Page_Up: -10, Gdk.KEY_Page_Down: 10}.get(keyval)
        if step is None:
            return False
        n = self.store.get_n_items()
        if n:
            at = self.selection.get_selected()
            i = min(n - 1, max(0, (0 if at == Gtk.INVALID_LIST_POSITION else at) + step))
            self.selection.set_selected(i)
            self.table.scroll_to(i, None, Gtk.ListScrollFlags.NONE, None)
        return True

    def choose(self, row):
        """Open the name, once the search is gone."""
        if row is None:
            return
        item = row.item
        self.close()
        GLib.idle_add(lambda: self.on_open(item) and False)


# ---- GitHub's pull requests and issues (lookup.GITHUB), kept up with while a window's shown
GITHUB_TICK_S = 5


def window_shown(win):
    """Whether a window's on show, as far as can be told: on the screen, not minimised or hidden from it."""
    surface = win.get_surface() if win.get_mapped() else None
    return surface is not None and not (isinstance(surface, Gdk.Toplevel) and surface.get_state() & (
        Gdk.ToplevelState.MINIMIZED | Gdk.ToplevelState.SUSPENDED))


def watch_github(win, folders, changed=None):
    """While win's shown: GitHub's lists of the folders in use (folders(win): (attached, tops), lookup.GitHub.look)
    looked at when it's time, or the folders are others, and once they've changed, what's on show drawn afresh: ⌘K if
    it's open and its first row is what's picked (changed(win), the rest). Where the pull requests of those folders'
    repositories stand (lookup.PULLS, once something's shown them) likewise. folders and changed are plain functions,
    as this holds nothing of the window: it goes when the window does."""
    window, seen, looked, asked = weakref.ref(win), [lookup.GITHUB.generation], [-lookup.GITHUB_LOOK_S], [None]
    pulls = [lookup.PULLS.generation]

    def tick():
        win = window()
        if win is None:
            return False
        if not window_shown(win):
            return True
        now, in_use = time.monotonic(), folders(win)
        if now - looked[0] >= lookup.GITHUB_LOOK_S or in_use != asked[0]:
            looked[0], asked[0] = now, in_use
            lookup.GITHUB.look(*in_use)
        lookup.PULLS.look(lookup.GITHUB.own_repository(f) for f in (*in_use[0], *in_use[1]))
        if seen[0] != lookup.GITHUB.generation or pulls[0] != lookup.PULLS.generation:
            seen[0], pulls[0] = lookup.GITHUB.generation, lookup.PULLS.generation
            dialog = win.get_visible_dialog()
            if isinstance(dialog, RefPalette) and dialog.selection.get_selected() in (0, Gtk.INVALID_LIST_POSITION):
                dialog.fill()
            if changed is not None:
                changed(win)
        return True
    GLib.timeout_add_seconds(GITHUB_TICK_S, tick)


# ---------------------------------------------------------------- a tool call's card
# (Baude's chat's and Meta's: a tool card's toggles and status, and what moves while something works)

class Ticker:
    """What moves while something works: a working session's dot pulsing (its opacity eased between 1 and 0.25 and
    back every 0.9 s) and the spinners of calls still running (Spinner). One timer moves them all, FPS times a
    second: each frame GTK draws costs 1.5-3 ms of CPU on a Mac (most of it GSK readying the window's surface), and
    a CSS animation or an Adw.Spinner draws 60 a second, so one working session's dot, or one long-running command,
    took a sixth of a core. In a window you aren't looking at they hold still."""

    FPS, IDLE_MS, PERIOD, LOW, STILL = 20, 500, 0.9, 0.25, 0.6
    # (held, not weakly: a widget Python keeps no wrapper of, Signal's button's icon, lost its own at once; let go of
    # as each is unmapped, which taking it out of the window does, and taken back as it's mapped again)
    pulsing, spinning, source, interval, start = set(), set(), None, 0, 0.0

    @classmethod
    def run(cls, ms=None):
        """The timer: FPS times a second while something it moves is seen, else every IDLE_MS (to notice when it is)."""
        ms = ms or 1000 // cls.FPS
        if cls.source is not None:
            if cls.interval == ms:
                return
            GLib.source_remove(cls.source)
        else:
            cls.start = time.monotonic()
        cls.interval, cls.source = ms, GLib.timeout_add(ms, cls.tick)

    @classmethod
    def pulse(cls, widget):
        cls.pulsing.add(widget)
        cls.run()

    @classmethod
    def still(cls, widget):
        cls.pulsing.discard(widget)
        widget.set_opacity(1.0)

    @staticmethod
    def seen(widget):
        """Mapped, in the window you're looking at."""
        root = widget.get_root() if widget.get_mapped() else None
        return isinstance(root, Gtk.Window) and root.is_active()

    @classmethod
    def tick(cls):
        for held in (cls.pulsing, cls.spinning):
            held.difference_update([w for w in held if not w.get_mapped()])
        if not cls.pulsing and not cls.spinning:
            cls.source = None
            return False
        phase = ((time.monotonic() - cls.start) / cls.PERIOD) % 2
        ease = (1 - math.cos(math.pi * (phase if phase < 1 else 2 - phase))) / 2  # (CSS's ease-in-out, near enough)
        level, seen = round(1 - (1 - cls.LOW) * ease, 2), False
        for w in cls.pulsing:
            here = cls.seen(w)
            seen = seen or here
            w.set_opacity(level if here else cls.STILL)
        for w in cls.spinning:
            if cls.seen(w):
                seen = True
                w.queue_draw()
        if (ms := 1000 // cls.FPS if seen else cls.IDLE_MS) != cls.interval:  # (the pulse's phase goes on)
            cls.interval, cls.source = ms, GLib.timeout_add(ms, cls.tick)
            return False
        return True


class Spinner(Gtk.Widget):
    """Adw.Spinner's look (a faint ring, and an arc going round it that grows and shrinks), moved by Ticker at its
    frame rate rather than by a frame-clock animation of its own (see Ticker)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        # (plain functions, given the spinner by the signal: a closure over it would be a cycle PyGObject can't free)
        self.connect("map", Spinner._mapped)
        self.connect("unmap", Spinner._unmapped)

    def _mapped(self):
        Ticker.spinning.add(self)
        Ticker.run()

    def _unmapped(self):
        Ticker.spinning.discard(self)

    def do_measure(self, orientation, for_size):
        return scales["icon_px"], scales["icon_px"], -1, -1  # (an icon's size: Settings → Size)

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        width = max(1.5, min(w, h) / 8)
        r = min(w, h) / 2 - width / 2
        if r <= 0:
            return
        cx, cy, color = w / 2, h / 2, self.get_color()
        track = color.copy()
        track.alpha *= 0.15
        stroke = Gsk.Stroke.new(width)
        ring = Gsk.PathBuilder.new()
        ring.add_circle(Graphene.Point().init(cx, cy), r)
        snapshot.append_stroke(ring.to_path(), stroke, track)
        t = time.monotonic()
        a = (t % 1.0) * 2 * math.pi  # (a turn a second, the arc a fifth to two thirds of the ring, and back)
        span = (0.2 + 0.47 * (1 - math.cos(2 * math.pi * t / 1.6)) / 2) * 2 * math.pi
        arc = Gsk.PathBuilder.new()
        arc.move_to(cx + r * math.cos(a), cy + r * math.sin(a))
        arc.svg_arc_to(r, r, 0, span > math.pi, True, cx + r * math.cos(a + span), cy + r * math.sin(a + span))
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        snapshot.append_stroke(arc.to_path(), stroke, color)


ICON_SLOT = 16  # status icon and disclosure arrow share one square column


class WorkingDots(Gtk.Label):
    """A turn running, at the bottom left (Baude's status bar, Meta's foot): •, ••, •••, and again; otherwise it isn't there, nor the room it takes.
    All three are laid out, the rest see-through, so what's after it doesn't move with each dot. In a window you aren't
    looking at it holds still (Ticker)."""

    STEP_MS = 350

    def __init__(self):
        super().__init__(xalign=0, visible=False)
        self.working, self.shown, self.source = False, 0, None
        self.connect("map", WorkingDots._run)  # (plain functions: see Spinner)
        self.connect("unmap", WorkingDots._stop)
        self._show(0)

    def set_working(self, working):
        if working == self.working:
            return
        self.working = working
        self.set_visible(working)
        self.set_tooltip_text("Working" if working else None)
        self._show(1 if working else 0)
        self._stop()
        self._run()

    def _show(self, count):
        self.shown = count
        self.set_markup("•" * count + f'<span alpha="1">{"•" * (3 - count)}</span>')

    def _run(self):
        if self.working and self.source is None and self.get_mapped():
            self.source = GLib.timeout_add(self.STEP_MS, self._step)

    def _stop(self):
        if self.source is not None:
            GLib.source_remove(self.source)
            self.source = None

    def _step(self):
        if Ticker.seen(self):
            self._show(self.shown % 3 + 1)
        return True


def _slot(widget):
    widget.set_size_request(ICON_SLOT, ICON_SLOT)  # (at least: an image is at the icons' size, Settings → Size)
    widget.set_halign(Gtk.Align.CENTER)
    widget.set_valign(Gtk.Align.CENTER)
    return widget


class Disclosure:
    """What a toggle opens under a line, built when it's first opened (on_expand). The toggle is a button after a tool
    card's name, Command (button=True), or an arrow and its label, › 5 tool calls; it goes in toggles, a box on that
    line. What it opens goes in grid's row, under the line."""

    def __init__(self, toggles, grid, row, label="details", button=False):
        self.on_expand = []  # run on every opening: content built only once someone looks
        self.grid, self.row = grid, row
        self.revealer, self.expanded = None, False  # (the revealer: once there's something in it)
        text = self.text = Gtk.Label(label=label, xalign=0.5 if button else 0)
        if button:
            self.arrow = None
            # (a click leaves the focus where it was, in what you're typing)
            self.button = Gtk.ToggleButton(child=text, valign=Gtk.Align.CENTER, focus_on_click=False)
            self.button.add_css_class("tool-toggle")
            self.button.connect("toggled", lambda b: b.get_active() != self.expanded and self.clicked(b.get_active()))
        else:
            self.arrow = _slot(Gtk.Image.new_from_icon_name("pan-end-symbolic"))
            self.arrow.add_css_class("soft-icon")
            text.add_css_class("dim-label")
            self.button = Gtk.Box(spacing=4)
            self.button.append(self.arrow)
            self.button.append(text)
            click = Gtk.GestureClick()
            click.connect("released", lambda *_: self.clicked(not self.expanded))
            self.button.add_controller(click)
            pointers.hand(self.button)
        toggles.append(self.button)

    def clicked(self, expanded):
        """Opened or closed by the reader: the toggle stays where it is on screen, and what's under it moves down or up
        (the chat's hold_toggle: following its end, the end would stay put and the toggle move instead)."""
        hold = getattr(self.button.get_root(), "hold_toggle", None)
        if hold is not None:
            hold(self)
        self.set_expanded(expanded)

    def sliding(self):
        """Still sliding open or closed."""
        return self.revealer is not None and self.revealer.get_child_revealed() != self.revealer.get_reveal_child()

    def set_child(self, child):
        if self.revealer is None:  # (a long session has thousands of these, most never opened: made when needed)
            self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                         transition_duration=120, visible=self.button.get_visible(),
                                         reveal_child=self.expanded)
            self.grid.attach(self.revealer, 0, self.row, 1, 1)
        self.revealer.set_child(child)

    def set_label(self, label):
        self.text.set_label(label)

    def set_visible(self, visible):
        for w in (self.button, self.revealer):
            if w is not None:
                w.set_visible(visible)

    def get_visible(self):
        return self.button.get_visible()

    def get_child(self):
        return self.revealer.get_child() if self.revealer is not None else None

    def get_expanded(self):
        return self.expanded

    def set_expanded(self, expanded):
        self.expanded = expanded
        if expanded:
            for fn in self.on_expand:
                fn()
        if self.revealer is not None:  # (nothing to show yet: what comes is shown as it comes, set_child)
            self.revealer.set_reveal_child(expanded)
        if self.arrow is not None:
            self.arrow.set_from_icon_name("pan-down-symbolic" if expanded else "pan-end-symbolic")
        elif self.button.get_active() != expanded:
            self.button.set_active(expanded)


def _status_icon(state):
    """The 16px status mark shared by tool cards and groups."""
    if state in ("running", "background"):
        return _slot(Spinner())  # (not Adw.Spinner, nor Gtk.Spinner: see Ticker)
    if state == "interrupted":  # a saved call that never finished: no spinner forever
        icon = _slot(Gtk.Image.new_from_icon_name("process-stop-symbolic"))
        icon.add_css_class("dim-label")
        return icon
    icon = _slot(Gtk.Image.new_from_icon_name(
        "window-close-symbolic" if state == "error" else "object-select-symbolic"))
    icon.add_css_class("error" if state == "error" else "success")
    return icon


def _card_status(state):
    """What a tool card shows of its state at the end of its line: a spinner while it runs; nothing once it's done
    (most are), nor for a failed or interrupted one, whose border is red."""
    if state in ("running", "background"):
        return _status_icon(state)
    return None
