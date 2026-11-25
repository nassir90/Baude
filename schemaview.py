"""The database view (SchemaView: a running database's tables as a diagram, SchemaCanvas, and their rows), with what only it needs. Baude and Record both use it; its host gives it input, kube (a table in a Kubernetes pod) and show_code."""
import argparse
import array
import base64
import glob
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
from lookup import find_spans  # noqa: E402,F401  (⌘K's engine: what a folder has)
from lookupkube import (  # noqa: E402,F401
    PG_LISTS, arango_eval, at_once, db_index, db_names, pg_databases_cached, pg_query, pg_servers_cached,
)
from widgets import (  # noqa: E402,F401
    FIND_RGB, WorkerPool, ZOOM_STEP, find_attrs, inside, paned_grip, view_bar, _scales,
)
from imageviews import Fade  # noqa: E402,F401
from pickers import ellipsize_dropdown  # noqa: E402,F401
from baudelib import tokens, wording  # noqa: E402
import kubehost  # noqa: E402
import viewwindow  # noqa: E402


# --------------------------------------------------------- database schemas --
# Every Postgres server Baude can reach (in running Docker containers), their databases, and each one's
# tables, columns, keys and foreign keys, drawn as a diagram like Supabase's schema visualizer (the
# SchemaView), with a table's first rows on demand. Read-only: every query runs as the container's own
# user with default_transaction_read_only on. Passwords in a container's environment are never kept.

DB_POOL = WorkerPool(2)


from schemadata import (  # noqa: E402,F401  (the data the view shows, with no GTK)
    PG_SCHEMA_SQL, PG_SECRET, PG_CACHE, pg_schema, _FORGOTTEN, _LOCATING, _LOCATING_LOCK, db_locate, db_find,
    db_indexed, db_label, pg_normalize, pg_rows, pg_mask, ARANGO_SCHEMA_JS, arango_schema, _arango_snake,
    arango_guess_ends, arango_normalize, arango_rows, schema_layout,
)


class SchemaCanvas(Gtk.Widget):
    """A database schema as a diagram, like Supabase's: a card per table (name, then each column with its
    type, PK / FK badges, nullable ones dimmer) and a curve per foreign key, from the column to the one it
    references. Scroll to pan, Ctrl+scroll or pinch to zoom, drag a card to move it or the background to pan;
    click a card to select it (its links and the tables they join light up), double-click for its rows.

    Zooming and panning work as d3-zoom's do (React Flow is built on it, and Supabase's visualizer on React Flow):
    Ctrl+scroll multiplies the scale by 2^(−delta × factor), so a wheel's notches and a touchpad's stream of small
    deltas zoom alike, about the pointer; the scale stays between 2× (React Flow's maxZoom) and a little past
    fitting the whole diagram in; and the view is kept over the diagram, as a translateExtent keeps it, so it can't
    be panned off the screen."""

    HEAD, ROW, PAD, MAX_W = (tokens.px(f"schema.{k}") for k in ("head-h", "row-h", "pad", "max-w"))
    MINI_W, MINI_H, MINI_PAD = (tokens.px(f"schema.{k}") for k in ("mini-w", "mini-h", "mini-pad"))  # the minimap, bottom right
    MAX_SCALE = 2.0  # (React Flow's maxZoom; Supabase's is 1.8): closer is only bigger, no more legible
    # d3-zoom's wheelDelta: 0.05 a line, 0.002 a pixel, so a browser's 100-pixel notch is 0.2 (×1.15). A notch here is
    # one wheel unit; a touchpad's pixels are d3's pixels, so a swipe the length of a notch zooms as far as one
    WHEEL_ZOOM, PIXEL_ZOOM = 0.2, 0.002
    WHEEL_PAN = 50  # pixels a notch pans (React Flow: its 100 px at panOnScrollSpeed 0.5); a touchpad's pixels 1:1
    WHEEL_GESTURE_S = 0.15  # (d3-zoom's wheelDelay: a pause this long ends a wheel gesture)

    def __init__(self, on_rows=None, on_select=None):
        super().__init__(hexpand=True, vexpand=True, focusable=True)
        self.add_css_class("schema-canvas")
        self.set_overflow(Gtk.Overflow.HIDDEN)  # (cards past the edge would draw over the rows below, and the bar)
        self.cards, self.links, self.selected = [], [], None
        self.mark_column = None  # a column to mark in the selected card (the one a link named)
        self.query, self.matches = "", []  # what Find looks for; where: [(card, row)], row None for the table's name
        self.scale, self.ox, self.oy = 1.0, 40.0, 40.0
        self.on_rows, self.on_select, self.drag = on_rows, on_select, None
        self.wheel = None  # a Ctrl+scroll gesture: (pointer, the canvas point under it at the start, last delta's time)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", lambda *_: setattr(self, "drag", None))
        self.add_controller(drag)
        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.add_controller(click)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self.on_scroll)
        self.add_controller(scroll)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda _c, x, y: setattr(self, "pointer", (x, y)))
        motion.connect("motion", lambda _c, x, y: setattr(self, "pointer", (x, y)))
        self.add_controller(motion)
        self.pointer = (0.0, 0.0)
        pinch = Gtk.GestureZoom()
        pinch.connect("begin", self.on_pinch_begin)
        pinch.connect("scale-changed", self.on_pinch)
        self.add_controller(pinch)

    # ---- content
    def set_schema(self, tables, links):
        """tables: [table dicts from pg_normalize]; links: [((s, t), col, (s, t), col)] among them."""
        # (a card's head and rows hold its text at the text's size: Settings → Size)
        self.HEAD, self.ROW, self.MAX_W = (round(v * _scales["text"]) for v in (SchemaCanvas.HEAD, SchemaCanvas.ROW,
                                                                                SchemaCanvas.MAX_W))
        self.cards, index = [], {}
        for t in tables:
            index[(t["schema"], t["name"])] = len(self.cards)
            self.cards.append(self.measure(t))
        self.links = [(index[a], ac, index[b], bc) for a, ac, b, bc in links if a in index and b in index]
        pos = schema_layout([(c["w"], c["h"]) for c in self.cards], [(a, b) for a, _, b, _ in self.links])
        for card, (x, y) in zip(self.cards, pos):
            card["x"], card["y"] = x, y
        self.selected = None
        self.set_query(self.query)  # (a schema read again, another one: its own matches)

    def set_query(self, text):
        """Find text (any case) in the tables' names and their columns': each match highlighted where it is, its row
        tinted, its table marked in the minimap. The matches, in the cards' order: [(card, row)], row None for a
        table's name."""
        self.query, self.matches = text.strip().lower(), []
        q = self.query
        for i, c in enumerate(self.cards):
            name = c["table"]["name"]
            self.mark_text(c["head"], name, q, bold=True)
            if q and q in name.lower():
                self.matches.append((i, None))
            for k, (col, layout, _typ) in enumerate(c["rows"]):
                self.mark_text(layout, col["name"], q)
                if q and q in col["name"].lower():
                    self.matches.append((i, k))
        self.queue_draw()
        return self.matches

    @staticmethod
    def mark_text(layout, text, q, bold=False):
        """layout shows text with every q in it highlighted (Pango counts in UTF-8 bytes); bold: all of it bold."""
        attrs = Pango.AttrList()
        if bold:
            attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
        layout.set_text(text, -1)
        layout.set_attributes(find_attrs(text, find_spans(text, q or ""), attrs))

    def measure(self, t):
        """A card: its text laid out once (Pango), its size from the widest row."""
        head = self.create_pango_layout(None)
        head.set_markup(f"<b>{html.escape(t['name'])}</b>", -1)
        sub = self.create_pango_layout(wording.say("databases.rows", n=t['rows']) if t["kind"] in ("r", "p") else
                                       {"v": wording.say("databases.view"),
                                        "m": wording.say("databases.materialized_view"),
                                        "f": wording.say("databases.foreign_table")}.get(t["kind"], ""))
        rows = []
        for col in t["columns"]:
            name = self.create_pango_layout(col["name"])
            typ = self.create_pango_layout(col["type"] + ("" if col["null"] is False else "?"))
            rows.append((col, name, typ))
        widest = max([head.get_pixel_size()[0] + sub.get_pixel_size()[0] + 24] +
                     [n.get_pixel_size()[0] + ty.get_pixel_size()[0] + 64 for _c, n, ty in rows])
        return {"table": t, "head": head, "sub": sub, "rows": rows, "w": min(self.MAX_W, widest + 2 * self.PAD),
                "h": self.HEAD + self.ROW * max(1, len(rows)) + 6, "x": 0.0, "y": 0.0}

    # ---- view
    def to_canvas(self, x, y):
        return (x - self.ox) / self.scale, (y - self.oy) / self.scale

    def card_at(self, x, y):
        cx, cy = self.to_canvas(x, y)
        for i in range(len(self.cards) - 1, -1, -1):
            c = self.cards[i]
            if c["x"] <= cx <= c["x"] + c["w"] and c["y"] <= cy <= c["y"] + c["h"]:
                return i
        return None

    def size(self):
        return self.get_width() or 800, self.get_height() or 600

    def bounds(self, cards=None):
        """(x0, y0, x1, y1): the box round the cards (all of them by default) on the canvas; None without any."""
        cards = cards or self.cards
        if not cards:
            return None
        return (min(c["x"] for c in cards), min(c["y"] for c in cards),
                max(c["x"] + c["w"] for c in cards), max(c["y"] + c["h"] for c in cards))

    def fit_scale(self, cards=None):
        """The scale at which the cards (all of them by default) just fit the view, 30 px clear of its edges."""
        b, (w, h) = self.bounds(cards), self.size()
        if b is None:
            return 1.0
        return max(0.01, min((w - 60) / max(1, b[2] - b[0]), (h - 60) / max(1, b[3] - b[1])))

    def min_scale(self):
        """As far out as it zooms: React Flow's 0.5, or for a diagram too big to fit at that, until it's a little
        smaller than the view; never down to nothing."""
        return min(0.5, 0.8 * self.fit_scale())

    def zoom_to(self, scale, px, py, at=None):
        """Zoom to scale (within the limits) about the point (px, py) in the view: the canvas point under it stays
        there (at: another canvas point to put there, a gesture's from its start), as in d3-zoom."""
        cx, cy = at or self.to_canvas(px, py)
        self.scale = min(self.MAX_SCALE, max(self.min_scale(), scale))
        self.ox, self.oy = px - cx * self.scale, py - cy * self.scale
        self.constrain()
        self.queue_draw()

    def zoom_by(self, factor):
        """Zoom in (factor above 1) or out about the middle of the view: the buttons, and Ctrl +/−."""
        w, h = self.size()
        self.wheel = None
        self.zoom_to(self.scale * factor, w / 2, h / 2)

    def pan_by(self, dx, dy):
        self.ox, self.oy, self.wheel = self.ox + dx, self.oy + dy, None
        self.constrain()
        self.queue_draw()

    def constrain(self, size=None):
        """Keep the diagram on the screen: d3-zoom's default constrain, with a translateExtent of the cards' box grown
        by half the view, so the view's middle stays over the box. Past that edge the view is moved back; on an axis
        where the view is wider than the extent, it's centred."""
        b = self.bounds()
        if b is None:
            return
        w, h = size or self.size()
        k = self.scale
        # how far the view's edges are past the extent's, on the canvas: d3's dx0, dx1, dy0, dy1
        dx0, dx1 = -self.ox / k - (b[0] - w / 2 / k), (w - self.ox) / k - (b[2] + w / 2 / k)
        dy0, dy1 = -self.oy / k - (b[1] - h / 2 / k), (h - self.oy) / k - (b[3] + h / 2 / k)
        self.ox += k * ((dx0 + dx1) / 2 if dx1 > dx0 else min(0, dx0) or max(0, dx1))
        self.oy += k * ((dy0 + dy1) / 2 if dy1 > dy0 else min(0, dy0) or max(0, dy1))

    def do_size_allocate(self, width, height, _baseline):
        self.constrain((width, height))  # (a smaller view, the rows opened below it: still over the diagram)

    def fit(self, cards=None):
        """Zoom and pan so the cards (all of them by default) fill the view."""
        b = self.bounds(cards)
        if b is None:
            return
        w, h = self.size()
        x0, y0, x1, y1 = b
        self.wheel = None
        self.scale = min(1.2, self.fit_scale(cards))  # (a few small tables: no closer than 1.2)
        self.ox = (w - (x1 - x0) * self.scale) / 2 - x0 * self.scale
        self.oy = (h - (y1 - y0) * self.scale) / 2 - y0 * self.scale
        self.constrain()
        self.queue_draw()

    def focus(self, index):
        """Select a card and centre it (zoomed in enough to read)."""
        self.select(index)
        c = self.cards[index]
        self.wheel = None
        self.scale = min(self.MAX_SCALE, max(self.scale, 0.9))
        w, h = self.size()
        self.ox = w / 2 - (c["x"] + c["w"] / 2) * self.scale
        self.oy = h / 3 - (c["y"] + self.HEAD) * self.scale
        self.constrain()
        self.queue_draw()

    def select(self, index):
        self.selected = index
        self.queue_draw()
        if self.on_select:
            self.on_select(None if index is None else self.cards[index]["table"])

    # ---- the minimap: every table small, the part in view a clear cutout in a greyed map; click or drag to move
    def mini_geometry(self):
        """(mx, my, mw, mh, k, bx, by): where the minimap is, and that canvas point (x, y) is drawn at
        (mx + (x - bx) * k, my + (y - by) * k) in it. None without tables, or in a small view."""
        w, h = self.get_width(), self.get_height()
        if not self.cards or w < 360 or h < 260:
            return None
        x0, y0 = min(c["x"] for c in self.cards), min(c["y"] for c in self.cards)
        x1, y1 = max(c["x"] + c["w"] for c in self.cards), max(c["y"] + c["h"] for c in self.cards)
        m = 0.04 * max(x1 - x0, y1 - y0)  # (a margin round the tables)
        x0, y0, x1, y1 = x0 - m, y0 - m, x1 + m, y1 + m
        k = min(self.MINI_W / max(1.0, x1 - x0), self.MINI_H / max(1.0, y1 - y0))
        mw, mh = (x1 - x0) * k, (y1 - y0) * k
        return w - self.MINI_PAD - mw, h - self.MINI_PAD - mh, mw, mh, k, x0, y0

    def in_mini(self, x, y):
        g = self.mini_geometry()
        return g is not None and g[0] - 4 <= x <= g[0] + g[2] + 4 and g[1] - 4 <= y <= g[1] + g[3] + 4

    def mini_centre(self, x, y):
        """Centre the view on the canvas point under (x, y) in the minimap."""
        mx, my, mw, mh, k, bx, by = self.mini_geometry()
        cx = bx + (min(max(x, mx), mx + mw) - mx) / k
        cy = by + (min(max(y, my), my + mh) - my) / k
        self.ox = self.get_width() / 2 - cx * self.scale
        self.oy = self.get_height() / 2 - cy * self.scale
        self.constrain()
        self.queue_draw()

    def draw_mini(self, snap, col):
        g = self.mini_geometry()
        if g is None:
            return
        mx, my, mw, mh, k, bx, by = g
        dark = Adw.StyleManager.get_default().get_dark()

        def rgba(c, a):
            out = Gdk.RGBA()
            out.red, out.green, out.blue, out.alpha = c.red, c.green, c.blue, a
            return out
        frame = Gsk.RoundedRect()  # (kept in a variable, as in draw_card)
        frame.init_from_rect(Graphene.Rect().init(mx - 4, my - 4, mw + 8, mh + 8), 6)
        snap.push_rounded_clip(frame)
        snap.append_color(rgba(col["card"], 0.94), Graphene.Rect().init(mx - 4, my - 4, mw + 8, mh + 8))
        # the links, as the diagram draws them, small (under the tables, as there)
        snap.save()
        snap.translate(Graphene.Point().init(mx - bx * k, my - by * k))
        snap.scale(k, k)
        for a, ac, b, bc in self.links:
            hot = self.selected in (a, b)
            snap.append_stroke(self.link_path(a, ac, b, bc), Gsk.Stroke.new((1.4 if hot else 0.9) / k),
                               col["accent"] if hot else rgba(col["fg"], 0.3))
        snap.restore()
        found = {i for i, _row in self.matches}
        for i, c in enumerate(self.cards):
            snap.append_color(col["accent"] if i == self.selected else col["find"] if i in found
                              else rgba(col["fg"], 0.38),
                              Graphene.Rect().init(mx + (c["x"] - bx) * k, my + (c["y"] - by) * k,
                                                   max(1.5, c["w"] * k), max(1.5, c["h"] * k)))
        # what's in view: a clear cutout; the rest of the map greyed out
        vx0, vy0 = self.to_canvas(0, 0)
        vx1, vy1 = self.to_canvas(self.get_width(), self.get_height())
        cx0, cy0 = max(mx - 4, mx + (vx0 - bx) * k), max(my - 4, my + (vy0 - by) * k)
        cx1, cy1 = min(mx + mw + 4, mx + (vx1 - bx) * k), min(my + mh + 4, my + (vy1 - by) * k)
        grey = Gdk.RGBA()
        grey.red = grey.green = grey.blue = 0.0
        grey.alpha = 0.45 if dark else 0.28
        L, T, R, B = mx - 4, my - 4, mx + mw + 4, my + mh + 4
        if cx1 <= cx0 or cy1 <= cy0:  # (the view is off the map: all of it greyed)
            snap.append_color(grey, Graphene.Rect().init(L, T, R - L, B - T))
        else:
            for x, y, ww, hh in ((L, T, R - L, cy0 - T), (L, cy1, R - L, B - cy1),
                                 (L, cy0, cx0 - L, cy1 - cy0), (cx1, cy0, R - cx1, cy1 - cy0)):
                if ww > 0 and hh > 0:
                    snap.append_color(grey, Graphene.Rect().init(x, y, ww, hh))
        snap.pop()
        if cx1 > cx0 and cy1 > cy0:
            cut = Gsk.RoundedRect()
            cut.init_from_rect(Graphene.Rect().init(cx0, cy0, cx1 - cx0, cy1 - cy0), 2)
            snap.append_border(cut, [1.5] * 4, [col["accent"]] * 4)
        border = Gsk.RoundedRect()
        border.init_from_rect(Graphene.Rect().init(mx - 4, my - 4, mw + 8, mh + 8), 6)
        snap.append_border(border, [1.0] * 4, [col["border"]] * 4)

    # ---- input
    def on_drag_begin(self, _g, x, y):
        if self.in_mini(x, y):  # (the minimap: the view follows the pointer)
            self.drag = ("mini", x, y)
            self.mini_centre(x, y)
            self.grab_focus()
            return
        i = self.card_at(x, y)
        self.drag = ("card", i, self.cards[i]["x"], self.cards[i]["y"]) if i is not None else ("pan", self.ox, self.oy)
        self.grab_focus()

    def on_drag_update(self, _g, dx, dy):
        if not self.drag:
            return
        if self.drag[0] == "mini":
            self.mini_centre(self.drag[1] + dx, self.drag[2] + dy)
            return
        if self.drag[0] == "card":
            _, i, x, y = self.drag
            self.cards[i]["x"], self.cards[i]["y"] = x + dx / self.scale, y + dy / self.scale
            self.queue_draw()
        else:  # (from where the drag began: dragging back past the edge comes straight back)
            self.ox, self.oy = self.drag[1], self.drag[2]
            self.pan_by(dx, dy)

    def on_click(self, gesture, n, x, y):
        if self.in_mini(x, y):
            return  # (the minimap moves the view: on_drag_begin)
        i = self.card_at(x, y)
        if n == 2 and i is not None and self.on_rows:
            self.on_rows(self.cards[i]["table"])
        elif n == 1:
            self.select(i)

    def on_scroll(self, ctl, dx, dy):
        state, pixels = ctl.get_current_event_state(), ctl.get_unit() == Gdk.ScrollUnit.SURFACE
        if state & Gdk.ModifierType.CONTROL_MASK:
            if dy:
                self.wheel_zoom(dy, pixels)
            return True
        if state & Gdk.ModifierType.SHIFT_MASK and not dx:  # (a wheel's Shift+scroll: sideways, as React Flow's)
            dx, dy = dy, 0.0
        step = 1.0 if pixels else self.WHEEL_PAN
        self.pan_by(-dx * step, -dy * step)
        return True

    def wheel_zoom(self, dy, pixels):
        """Ctrl+scroll, as d3-zoom's wheel: the scale goes by 2^(−dy × factor), in proportion to the delta, about the
        pointer. The canvas point under the pointer as a gesture starts stays the one put under it until the pointer
        moves or the deltas pause, so a limit or the edge doesn't make it drift: zoom out and back in, and it's back
        where it was."""
        now, (px, py) = time.monotonic(), self.pointer
        if self.wheel is None or self.wheel[0] != (px, py) or now - self.wheel[2] > self.WHEEL_GESTURE_S:
            self.wheel = ((px, py), self.to_canvas(px, py), now)
        self.wheel = (*self.wheel[:2], now)
        self.zoom_to(self.scale * 2 ** (-dy * (self.PIXEL_ZOOM if pixels else self.WHEEL_ZOOM)), px, py, self.wheel[1])

    def on_pinch_begin(self, gesture, _sequence):
        """A pinch, as d3-zoom's: the scale is the one it began at times how far the fingers have spread, and the canvas
        point that was between them stays between them, so it pans as they move."""
        _ok, x, y = gesture.get_bounding_box_center()
        self.drag = None  # (a touchscreen's first finger began a drag: the pinch has the view now)
        self.pinch_from = (self.scale, self.to_canvas(x, y))

    def on_pinch(self, gesture, ratio):
        _ok, x, y = gesture.get_bounding_box_center()
        self.zoom_to(self.pinch_from[0] * ratio, x, y, self.pinch_from[1])

    # ---- drawing
    def colors(self):
        dark = Adw.StyleManager.get_default().get_dark()
        fg = self.get_color()

        def rgba(r, g, b, a):
            c = Gdk.RGBA()
            c.red, c.green, c.blue, c.alpha = r, g, b, a
            return c
        accent = Adw.StyleManager.get_default().get_accent_color_rgba() if hasattr(
            Adw.StyleManager.get_default(), "get_accent_color_rgba") else rgba(0.21, 0.52, 0.89, 1)
        def themed(name):
            c = tokens.value(f"schema.{name}", "color", "dark" if dark else "light")
            return rgba(*c["components"], c.get("alpha", 1))
        return {"fg": fg, "dim": rgba(fg.red, fg.green, fg.blue, 0.55), "faint": rgba(fg.red, fg.green, fg.blue, 0.18),
                "card": themed("card"), "head": themed("head"), "border": themed("border"),
                "accent": accent, "link": rgba(fg.red, fg.green, fg.blue, 0.32),
                "pk": themed("pk"), "fk": themed("fk"),
                "find": rgba(*(c / 255 for c in FIND_RGB), 1),  # (Find's matches, as in the cards' text)
                "grid": rgba(fg.red, fg.green, fg.blue, 0.06)}

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        col = self.colors()
        # a dotted background, like a canvas
        step = 24 * self.scale
        if step >= 8:
            x0, y0 = self.ox % step, self.oy % step
            for gx in range(int(w / step) + 2):
                for gy in range(int(h / step) + 2):
                    snap.append_color(col["grid"], Graphene.Rect().init(x0 + gx * step, y0 + gy * step, 1.5, 1.5))
        snap.save()
        snap.translate(Graphene.Point().init(self.ox, self.oy))
        snap.scale(self.scale, self.scale)
        view = Graphene.Rect().init(-self.ox / self.scale, -self.oy / self.scale, w / self.scale, h / self.scale)
        near = {self.selected} | {b for a, _, b, _ in self.links if a == self.selected} | \
               {a for a, _, b, _ in self.links if b == self.selected} if self.selected is not None else set()
        for a, ac, b, bc in self.links:
            self.draw_link(snap, a, ac, b, bc, col, self.selected in (a, b))
        for i, card in enumerate(self.cards):
            r = Graphene.Rect().init(card["x"], card["y"], card["w"], card["h"])
            if r.intersection(view)[0]:
                self.draw_card(snap, card, col, i == self.selected, i in near)
        snap.restore()
        self.draw_mini(snap, col)

    def row_y(self, card, column):
        for k, (c, _n, _t) in enumerate(card["rows"]):
            if c["name"] == column:
                return card["y"] + self.HEAD + k * self.ROW + self.ROW / 2
        return card["y"] + self.HEAD / 2

    def link_ends(self, a, ac, b, bc):
        """(xa, ya, xb, yb, d): where a link leaves its column's row and meets the referenced one's, on the canvas;
        d is the way it leaves (1: right, −1: left)."""
        ca, cb = self.cards[a], self.cards[b]
        ya, yb = self.row_y(ca, ac), self.row_y(cb, bc)
        if ca["x"] + ca["w"] / 2 >= cb["x"] + cb["w"] / 2:  # the referenced table is to the left
            return ca["x"], ya, cb["x"] + cb["w"], yb, -1
        return ca["x"] + ca["w"], ya, cb["x"], yb, 1

    def link_path(self, a, ac, b, bc):
        """A link's curve on the canvas (the diagram's, and the minimap's, small)."""
        xa, ya, xb, yb, d = self.link_ends(a, ac, b, bc)
        bend = max(40.0, abs(xb - xa) / 2)
        path = Gsk.PathBuilder.new()
        path.move_to(xa, ya)
        if a == b:  # a table referencing itself: a loop out and back
            path.cubic_to(xa + 60, ya, xa + 60, yb, xa, yb)
        else:
            path.cubic_to(xa + d * bend, ya, xb - d * bend, yb, xb, yb)
        return path.to_path()

    def draw_link(self, snap, a, ac, b, bc, col, hot):
        _xa, _ya, xb, yb, _d = self.link_ends(a, ac, b, bc)
        stroke = Gsk.Stroke.new(2.2 if hot else 1.3)
        snap.append_stroke(self.link_path(a, ac, b, bc), stroke, col["accent"] if hot else col["link"])
        snap.append_color(col["accent"] if hot else col["link"], Graphene.Rect().init(xb - 3, yb - 3, 6, 6))

    def draw_card(self, snap, card, col, selected, near):
        x, y, w, h = card["x"], card["y"], card["w"], card["h"]
        outline = Gsk.RoundedRect()  # (kept in a variable: init_from_rect on a temporary returns freed memory)
        outline.init_from_rect(Graphene.Rect().init(x, y, w, h), 8)
        snap.push_rounded_clip(outline)
        snap.append_color(col["card"], Graphene.Rect().init(x, y, w, h))
        snap.append_color(col["head"], Graphene.Rect().init(x, y, w, self.HEAD))
        snap.append_color(col["border"], Graphene.Rect().init(x, y + self.HEAD, w, 1))
        self.text(snap, card["head"], x + self.PAD, y + (self.HEAD - card["head"].get_pixel_size()[1]) / 2, col["fg"])
        sw = card["sub"].get_pixel_size()[0]
        self.text(snap, card["sub"], x + w - self.PAD - sw, y + (self.HEAD - card["sub"].get_pixel_size()[1]) / 2,
                  col["dim"])
        q = self.query
        if q and q in card["table"]["name"].lower():  # (Find: its name matched)
            snap.append_color(self.find_tint(col, 0.22), Graphene.Rect().init(x, y, w, self.HEAD))
        for k, (c, name, typ) in enumerate(card["rows"]):
            ry = y + self.HEAD + k * self.ROW
            if q and q in c["name"].lower():  # (Find: this column matched)
                snap.append_color(self.find_tint(col, 0.16), Graphene.Rect().init(x, ry, w, self.ROW))
            if selected and self.mark_column and c["name"] == self.mark_column:  # (the column a link named)
                tint = Gdk.RGBA()
                tint.red, tint.green, tint.blue, tint.alpha = col["accent"].red, col["accent"].green, col["accent"].blue, 0.2
                snap.append_color(tint, Graphene.Rect().init(x, ry, w, self.ROW))
            if k:
                snap.append_color(col["faint"], Graphene.Rect().init(x + self.PAD, ry, w - 2 * self.PAD, 0.6))
            bx = x + self.PAD
            for flag, label, color in ((c["pk"], "PK", col["pk"]), (bool(c["fk"]), "FK", col["fk"])):
                if flag:
                    snap.append_color(color, Graphene.Rect().init(bx, ry + self.ROW / 2 - 3, 6, 6))
                    bx += 9
            nh = name.get_pixel_size()[1]
            self.text(snap, name, x + self.PAD + 20, ry + (self.ROW - nh) / 2, col["fg"] if c["null"] is False or c["pk"]
                      else col["dim"])
            tw = typ.get_pixel_size()[0]
            self.text(snap, typ, x + w - self.PAD - tw, ry + (self.ROW - nh) / 2, col["dim"])
        snap.pop()
        border = [col["accent"] if selected else (col["fk"] if near else col["border"])] * 4
        widths = [2.0 if selected or near else 1.0] * 4
        snap.append_border(outline, widths, border)

    @staticmethod
    def find_tint(col, alpha):
        tint = Gdk.RGBA()
        tint.red, tint.green, tint.blue, tint.alpha = col["find"].red, col["find"].green, col["find"].blue, alpha
        return tint

    @staticmethod
    def text(snap, layout, x, y, color):
        snap.save()
        snap.translate(Graphene.Point().init(x, y))
        snap.append_layout(layout, color)
        snap.restore()


class SchemaView(Gtk.Box):
    """Full-window database schemas (☰ → Database Schemas…, or a table name in a reply): pick a server, a
    database and a schema; the diagram; a table's first rows below it. Esc closes."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False, focusable=True)
        self.add_css_class("baude-frame")  # (the bar: theming.CSS; the diagram and its rows the inside, set into it)
        self.win, self.servers, self.databases, self.schema = win, [], [], None
        self.server = self.database = self.focus_name = self.focus_column = self.code_link = None
        self.dbs, self.namespace, self.hint, self.syncing = {}, "public", "", False
        self.generation = self.rows_generation = 0  # (the last schema and rows asked for: older ones aren't shown)
        self.host = kubehost.Host(None, {"via": "local"}, wording.say("senders.local"))  # (where it reads: open)
        bar = view_bar()
        title = Gtk.Label(label=wording.say("databases.title"))
        title.add_css_class("heading")
        bar.append(title)
        self.host_label = viewwindow.host_label()
        bar.append(self.host_label)
        self.server_dd = ellipsize_dropdown(Gtk.DropDown.new_from_strings([]), 34)
        self.db_dd = ellipsize_dropdown(Gtk.DropDown.new_from_strings([]), 26)
        self.ns_dd = ellipsize_dropdown(Gtk.DropDown.new_from_strings([]), 18)
        for dd, tip in ((self.server_dd, wording.say("databases.server_tip")),
                        (self.db_dd, wording.say("databases.database")), (self.ns_dd, wording.say("databases.schema_tip"))):
            dd.set_tooltip_text(tip)
            dd.connect("notify::selected", self.on_pick)
            bar.append(dd)
        # Find (Ctrl+F): tables and columns by name, every match highlighted; Enter (Ctrl+G) for the next one,
        # Shift+Enter (Ctrl+Shift+G) for the one before, Esc to clear it (then to close the view)
        self.find = Gtk.SearchEntry(placeholder_text=wording.say("common.search"), width_chars=22)
        self.match_at = -1  # (which of the canvas' matches is shown)
        self.find.connect("search-changed", lambda e: self.find_changed(e.get_text()))
        self.find.connect("activate", lambda e: self.next_match(1))
        self.find.connect("next-match", lambda e: self.next_match(1))
        self.find.connect("previous-match", lambda e: self.next_match(-1))
        self.find.connect("stop-search", lambda e: e.set_text("") if e.get_text() else self.escape())
        shift_enter = Gtk.EventControllerKey()
        shift_enter.connect("key-pressed", lambda _c, kv, _k, state: (self.next_match(-1), True)[1]
                            if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.SHIFT_MASK
                            else False)
        self.find.add_controller(shift_enter)
        bar.append(self.find)
        spacer = Gtk.Box(hexpand=True)
        bar.append(spacer)
        self.code_btn = Gtk.Button(label=wording.say("databases.definition"), visible=False,
                                   tooltip_text=wording.say("databases.definition_tip"))
        self.code_btn.connect("clicked", lambda *_: self.show_definition())
        bar.append(self.code_btn)
        self.rows_btn = Gtk.Button(label=wording.say("databases.rows_button"), sensitive=False,
                                   tooltip_text=wording.say("databases.rows_tip"))
        self.rows_btn.connect("clicked", lambda *_: self.canvas.selected is not None and
                              self.load_rows(self.canvas.cards[self.canvas.selected]["table"]))
        bar.append(self.rows_btn)
        for icon, tip, fn in (("zoom-out-symbolic", wording.say("databases.zoom_out_tip"), lambda: self.zoom(-1)),
                              ("zoom-in-symbolic", wording.say("databases.zoom_in_tip"), lambda: self.zoom(+1)),
                              ("zoom-fit-best-symbolic", wording.say("databases.fit_tip"), lambda: self.zoom(0))):
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_, fn=fn: fn())
            bar.append(btn)
        self.out = viewwindow.Out(self, bar, wording.say("databases.title"), self.find, zoom=self.zoom)  # (out to a
        # window of its own, as ☰ → Terminal's goes; its close)
        self.append(Gtk.WindowHandle(child=bar))  # (dragged, it moves the window; double-clicked, maximizes)
        stage = Gtk.Overlay(vexpand=True)
        self.canvas = SchemaCanvas(on_rows=self.load_rows, on_select=self.on_select)
        self.fade = Fade(self.canvas)  # (another server, database or schema: the diagram fades until it's in)
        stage.set_child(self.canvas)
        self.status = Gtk.Label(xalign=0, halign=Gtk.Align.START, valign=Gtk.Align.END, margin_start=12, margin_bottom=10)
        self.status.add_css_class("schema-status")
        stage.add_overlay(self.status)
        self.split = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, start_child=stage, resize_start_child=True,
                               shrink_end_child=False, vexpand=True)
        self.rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
        self.rows_box.add_css_class("schema-rows")
        rows_head = Gtk.Box(spacing=6, margin_start=12, margin_end=8, margin_top=6, margin_bottom=6)
        self.rows_title = Gtk.Label(xalign=0, hexpand=True)
        self.rows_title.add_css_class("heading")
        rows_head.append(self.rows_title)
        hide = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=wording.say("databases.hide_rows"))
        hide.add_css_class("flat")
        hide.connect("clicked", lambda *_: self.rows_box.set_visible(False))
        rows_head.append(hide)
        self.rows_box.append(rows_head)
        self.rows_scroll = Gtk.ScrolledWindow(vexpand=True)
        self.rows_fade = Fade(self.rows_scroll)
        self.rows_box.append(self.rows_scroll)
        self.split.set_end_child(self.rows_box)
        paned_grip(self.split)
        self.append(inside(self.split))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, _k, _s: (self.escape(), True)[1] if kv == Gdk.KEY_Escape else False)
        self.add_controller(keys)

    # ---- opening
    def open(self, table=None, column=None, hint=None, code=None, found=None):
        """Show the schemas; with table (or a column name), find the database that has it (hint: a path that
        names it, like a migration's) and select it there, or at found: (server, database, schema, table), where
        db_locate found it. code: (path, line) of its definition. They're the databases of the host picked in the
        window's host dropdown (as ☰ → Terminal's new tabs are); out in a window of its own, that window's brought up."""
        host = kubehost.picked(self.win)
        if host.key() != self.host.key():  # (another host's: nothing of the last one's shown)
            self.servers, self.dbs, self.schema, self.server, self.database = [], {}, None, None, None
            self.generation += 1
            self.rows_box.set_visible(False)
            self.canvas.set_schema([], [])
        self.host = host
        self.host_label.set_label(host.name)
        self.focus_name, self.focus_column, self.hint, self.code_link = table, column, hint or "", code
        self.preset = found if host.here else None
        self.canvas.mark_column = column
        self.code_btn.set_visible(code is not None)
        if not self.out.present():
            self.set_visible(True)
            self.grab_focus()
        self.say(wording.say("databases.looking_for_servers"))
        self.fade.hold()
        if host.here:
            DB_POOL.submit(self._discover)
            return

        def got(r):  # (there, the first database read; a table wanted is found in it if it's there)
            if host is self.host:
                self._discovered(r.get("servers") or [], r.get("databases") or {}, None, r.get("error"),
                                 searched=False)
        host.ask("databases", {"what": "servers"}, got)

    def close(self):
        self.set_visible(False)
        self.win.input.grab_focus()

    def escape(self):
        if self.out.window is None:  # (out in a window of its own: that window's to close)
            self.close()

    def say(self, text):
        self.status.set_label(text)

    def _discover(self):
        try:
            servers = pg_servers_cached()
            dbs = {s["container"]: pg_databases_cached(s) for s in servers}
            if getattr(self, "preset", None) and self.preset[0] in servers:
                GLib.idle_add(self._discovered, servers, dbs, self.preset, None)
                return
            if self.focus_name or self.focus_column:
                n = sum(map(len, dbs.values()))
                GLib.idle_add(self.say, wording.say("databases.looking_for_table", table=self.focus_name, n=n,
                                                    servers=len(servers)) if self.focus_name else
                              wording.say("databases.looking_for_column", column=self.focus_column, n=n,
                                          servers=len(servers)))
            found = self._find(servers, dbs) if (self.focus_name or self.focus_column) else None
            GLib.idle_add(self._discovered, servers, dbs, found, None)
        except RuntimeError as e:
            GLib.idle_add(self._discovered, [], {}, None, str(e))

    def _find(self, servers, dbs):
        """(server, database, schema, table) holding the wanted table (or column): the one whose database name
        the hint path mentions, else the first. (db_locate's: by name first, not every database read whole.)"""
        hits = db_locate(self.focus_name, None if self.focus_name else self.focus_column, self.hint)["hits"]
        if not hits:
            return None
        return max(hits, key=lambda h: (h[1] in re.split(r"[^a-z0-9]+", self.hint.lower()) or
                                        any(h[1] in part for part in re.split(r"[/_.-]", self.hint.lower()) if part),
                                        h[2] == "public"))

    def _discovered(self, servers, dbs, found, error, searched=True):
        if error or not servers:
            self.say(error or wording.say("databases.no_postgres"))
            self.fade.done()
            return False
        self.servers, self.dbs = servers, dbs
        self.syncing = True
        engine = lambda s: "ArangoDB" if s.get("engine") == "arango" else "Postgres"  # noqa: E731
        self.server_dd.set_model(Gtk.StringList.new([f"{s['container']} ({engine(s)} {s['version']})" if s["version"]
                                                     else s["container"] for s in servers]))
        self.db_list_for = None
        self.syncing = False
        if found:
            s, db, ns, name = found
            self.load(s, db, ns, name)
        else:
            if searched and (self.focus_name or self.focus_column):
                self.say(wording.say("databases.no_table_here", table=self.focus_name) if self.focus_name else
                         wording.say("databases.no_column_here", column=self.focus_column))
            s = servers[0]
            dbl = [d for d in dbs.get(s["container"], []) if d != "postgres"] or dbs.get(s["container"], [])
            if dbl:
                self.load(s, dbl[0], None, None, quiet=bool(self.focus_name or self.focus_column))
            else:
                self.fade.done()
        return False

    def load(self, server, database, namespace=None, table=None, quiet=False):
        """Read a database's schema (in the background) and draw one of its schemas; select table. The diagram
        shown fades until it's in."""
        self.server, self.database = server, database
        gen = self.generation = self.generation + 1
        self.fade.hold()
        self.syncing = True
        if self.server_dd.get_selected() != self.servers.index(server):
            self.server_dd.set_selected(self.servers.index(server))
        names = self.dbs.get(server["container"], [])
        if getattr(self, "db_list_for", None) != server["container"]:  # (only a new server has a new list)
            self.db_dd.set_model(Gtk.StringList.new([db_label(d) for d in names]))
            self.db_list_for = server["container"]
        if database in names and self.db_dd.get_selected() != names.index(database):
            self.db_dd.set_selected(names.index(database))
        self.syncing = False
        if not quiet:
            self.say(wording.say("databases.reading", database=db_label(database)))
        t0 = time.monotonic()
        if not self.host.here:
            return self.host.ask("databases", {"what": "schema", "container": server["container"], "database": database},
                                 lambda r: self._loaded(gen, database, None if r.get("error") else kubehost.schema(r),
                                                        namespace, table, r.get("took") or time.monotonic() - t0,
                                                        r.get("error")))

        def work():
            try:
                schema = pg_schema(server, database)
                GLib.idle_add(self._loaded, gen, database, schema, namespace, table, time.monotonic() - t0, None)
            except (RuntimeError, ValueError) as e:
                GLib.idle_add(self._loaded, gen, database, None, namespace, table, 0, str(e))
        DB_POOL.submit(work)

    def _loaded(self, gen, database, schema, namespace, table, took, error):
        if gen != self.generation:
            return False  # another one was picked meanwhile
        self.fade.done()
        if error:
            self.say(f"{db_label(database)}: {error}")
            return False
        self.schema = schema
        spaces = sorted({ns for ns, _ in schema["tables"]}, key=lambda ns: (ns != "public", ns))
        ns = namespace if namespace in spaces else (spaces[0] if spaces else "public")
        self.show_namespace(ns, table, took)
        return False

    def show_namespace(self, ns, table=None, took=None):
        spaces = sorted({s for s, _ in self.schema["tables"]}, key=lambda s: (s != "public", s)) or ["public"]
        model = self.ns_dd.get_model()
        self.syncing = True
        if model is None or [model.get_string(i) for i in range(model.get_n_items())] != spaces:
            self.ns_dd.set_model(Gtk.StringList.new(spaces))
        if ns in spaces and self.ns_dd.get_selected() != spaces.index(ns):
            self.ns_dd.set_selected(spaces.index(ns))
        self.syncing = False
        tables = [t for (s, _n), t in sorted(self.schema["tables"].items()) if s == ns]
        links = [l for l in self.schema["links"] if l[0][0] == ns and l[2][0] == ns]
        self.canvas.set_schema(tables, links)
        self.namespace = ns
        n_links = len(links)
        self.summary = " · ".join([db_label(self.database), ns, wording.say("databases.tables", n=len(tables)),
                                   wording.say("databases.foreign_keys", n=n_links),
                                   wording.say("databases.read_only")] +
                                  ([wording.say("databases.read_in", seconds=f"{took:.2f}")] if took else []))
        self.show_summary()
        GLib.idle_add(self._frame, table)

    def show_summary(self):
        self.say(getattr(self, "summary", ""))

    def _frame(self, table):
        if self.canvas.get_width() == 0:  # not laid out yet: next frame
            GLib.timeout_add(30, self._frame, table)
            return False
        target = next((i for i, c in enumerate(self.canvas.cards) if c["table"]["name"] == table), None) if table else None
        if target is None and self.focus_column and table is None:
            target = next((i for i, c in enumerate(self.canvas.cards)
                           if any(col["name"] == self.focus_column for col in c["table"]["columns"])), None)
        if target is not None:
            self.canvas.focus(target)
        else:
            self.canvas.fit()
        return False

    def on_pick(self, dd, _pspec):
        if self.syncing or not self.servers:
            return
        # after the signal: changing a drop-down's list from inside its own notify spins GTK forever
        GLib.idle_add(lambda: self.picked(dd) and False)

    def picked(self, dd):
        if dd is self.server_dd:
            s = self.servers[dd.get_selected()]
            dbl = self.dbs.get(s["container"], [])
            if dbl:
                self.load(s, ([d for d in dbl if d != "postgres"] or dbl)[0])
        elif dd is self.db_dd:
            names = self.dbs.get(self.server["container"], [])
            if dd.get_selected() < len(names):
                self.load(self.server, names[dd.get_selected()])
        elif dd is self.ns_dd and self.schema:
            item, schema = dd.get_selected_item(), self.schema
            if item is not None:  # (once faded; not on another database's schema, read meanwhile)
                self.fade.swap(lambda ns=item.get_string(): self.schema is schema and self.show_namespace(ns))

    def start_find(self):
        """Ctrl+F while the view is on top: Find, focused, its text selected to type over. False if it isn't."""
        if not self.on_top():
            return False
        self.find.grab_focus()
        self.find.select_region(0, -1)
        return True

    def find_changed(self, text):
        """Find's text changed: every match highlighted, and the first shown (a table named just so before any)."""
        matches = self.canvas.set_query(text)
        q = self.canvas.query
        self.match_at = -1
        if not q:
            self.canvas.mark_column = self.focus_column
            self.show_summary()
            return
        if not matches:
            self.say(wording.say("glossary.count", matched=0, total=0))
            return
        exact = next((n for n, (i, row) in enumerate(matches)
                      if row is None and self.canvas.cards[i]["table"]["name"].lower() == q), 0)
        self.match_at = exact - 1
        self.next_match(1)

    def next_match(self, step):
        """Show the next match (step 1) or the one before (−1), round the end: its table selected and centred, a
        column's row marked."""
        matches = self.canvas.matches
        if not matches:
            return
        self.match_at = (self.match_at + step) % len(matches)
        i, row = matches[self.match_at]
        self.canvas.mark_column = self.canvas.cards[i]["rows"][row][0]["name"] if row is not None else None
        self.canvas.focus(i)
        self.say(wording.say("glossary.count", matched=self.match_at + 1, total=len(matches)))

    def find_table(self, text, quiet=False):
        text = text.strip().lower()
        hit = next((i for i, c in enumerate(self.canvas.cards) if c["table"]["name"].lower() == text), None)
        if hit is None:
            hit = next((i for i, c in enumerate(self.canvas.cards) if text in c["table"]["name"].lower()), None)
        if hit is not None:
            self.canvas.focus(hit)
        elif not quiet:
            self.say(wording.say("databases.no_table_like", text=text, schema=self.namespace))

    ZOOM_STEP = 1.2  # (React Flow's zoomIn and zoomOut)

    def zoom(self, steps):
        """The − and + buttons and Ctrl +/−: zoom in (steps above 0) or out about the diagram's middle; 0 fits it
        all in, as Ctrl+0 does."""
        if steps:
            self.canvas.zoom_by(self.ZOOM_STEP ** steps)
        else:
            self.canvas.fit()

    def on_top(self):
        """Whether it's open and on top (the Kubernetes or Terminal view isn't over it): Ctrl+scroll and Ctrl +/−/0 are its
        diagram's then, not the chat's under it."""
        return self.out.here() and not self.win.kube.out.here() and not self.win.terminal.get_visible()

    def on_select(self, table):
        self.rows_btn.set_sensitive(table is not None)

    def show_definition(self):
        if self.code_link:
            if self.out.window is None:
                self.close()
            self.win.show_code(*self.code_link)
            self.win.present()

    # ---- rows
    def load_rows(self, table):
        """Read table's first rows in the background; the rows shown fade until they're in."""
        server, database = self.server, self.database
        gen = self.rows_generation = self.rows_generation + 1
        self.rows_fade.hold()
        self.rows_title.set_label(f"{table['schema']}.{table['name']}: " + wording.say("databases.reading_rows"))
        self.rows_box.set_visible(True)
        if self.split.get_position() <= 0 or self.split.get_position() > (self.get_height() or 800) - 120:
            self.split.set_position(int((self.get_height() or 800) * 0.6))
        if not self.host.here:
            return self.host.ask("databases", {"what": "rows", "container": server["container"], "database": database,
                                               "schema": table["schema"], "table": table["name"]},
                                 lambda r: self._rows(table, r.get("rows") or [], r.get("error"), gen))

        def work():
            try:
                rows = pg_rows(server, database, table["schema"], table["name"])
                GLib.idle_add(self._rows, table, rows, None, gen)
            except (RuntimeError, ValueError) as e:
                GLib.idle_add(self._rows, table, [], str(e), gen)
        DB_POOL.submit(work)

    def _rows(self, table, rows, error, gen=None):
        if gen is not None and gen != self.rows_generation:
            return False  # another table's were asked for meanwhile
        cols = [c["name"] for c in table["columns"]]
        self.rows_title.set_label(f"{table['schema']}.{table['name']}: " + (error or
                                  wording.say("databases.first_rows", n=len(rows))))
        grid = Gtk.Grid(column_spacing=0, row_spacing=0, margin_start=12, margin_end=12, margin_bottom=12)
        grid.add_css_class("md-table")
        for c, name in enumerate(cols):
            head = Gtk.Label(label=name, xalign=0)
            head.add_css_class("md-head")
            grid.attach(head, c, 0, 1, 1)
        for r, row in enumerate(rows, 1):
            for c, name in enumerate(cols):
                v = row.get(name)
                text = wording.say("databases.null") if v is None else (json.dumps(v) if isinstance(v, (dict, list)) else str(v))
                cell = Gtk.Label(label=text[:120] + ("…" if len(text) > 120 else ""), xalign=0, selectable=True)
                cell.add_css_class("md-cell")
                if v is None:
                    cell.add_css_class("dim-label")
                grid.attach(cell, c, r, 1, 1)
        self.rows_scroll.set_child(grid)
        self.rows_box.set_visible(True)
        self.rows_fade.done()
        return False
