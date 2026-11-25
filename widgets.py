"""GTK pieces Baude and Record both use (Record imports this, never baude.py), and the viewers (kubeview, schemaview, gitview, codeview) too: markdown, links, images, code, settings; the links' logos, drawn as the
vectors they are in the text's colour (draw_ref_icon, RefIcon), at the icons' size (scales: Baude's Settings → Size
sets it, as baude's _scales, the same dict).

Its parts over this: what a Signal ref is and LinkLabel, which draws its logo, in refkinds.py; code's colours in
codespans.py; a chat's pictures in imageviews.py; markdown, links and MarkdownView in markdownview.py; a chat's bars
and turns in chatparts.py; the box a message is written in, in composerview.py; tabs, side bars and what slides in
panels.py; the large view in lightbox.py; a chat's selection and ⌘F in chatfind.py; dropdowns, pickers and ⌘K in
pickers.py; the CSS, the theme and the accent in theming.py; the mesh's dialogs in meshui.py.
"""
import glob
import json
import math
import os
import struct
import weakref
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import queue

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "vendor"))  # (Pygments: vendor/README.md)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402,F401
from lookup import find_spans, full_time  # noqa: E402,F401
import pointers  # noqa: E402
import fontsetup  # noqa: E402  (the code font, and its size beside the prose)
from baudelib import tokens  # noqa: E402  (the measures and colours below: schema/tokens, the Android app's too)
from baudelib import icons  # noqa: E402  (schema/icons: every app's icons, the Android app's too)
from baudelib import wording  # noqa: E402


# the sizes for what's drawn here: the text's, and an icon's in px (16 at 1.0); Baude's Settings → Size sets them
scales = {"dpi": None, "icons": None, "icons_css": "", "text": 1.0, "icon_px": 16}
_scales = scales


# A Signal link's logo, drawn just before it (LinkLabel): what it links to at a glance (git's, Kubernetes' helm,
# a database, a graph for ArangoDB, a file…), in the text's own colour (currentColor). 16×16, drawn at the text's size:
# schema/icons/links, by name.
REF_ICON_SVG = {name: icons.svg("links", name) for name in icons.names("links")}
# A pull request has a branch's logo, an issue its #, in a colour of their own for where they stand (lookupgit.github_icon),
# light or dark: green while it's open, purple once merged, grey if it came to nothing (closed unmerged, or an issue
# closed: an open issue is the text's colour, as most logos are).
REF_ICON_SVG.update(pr_open=REF_ICON_SVG["git"], pr_merged=REF_ICON_SVG["git"], pr_closed=REF_ICON_SVG["git"],
                    issue_closed=REF_ICON_SVG["issue"])
REF_ICON_TINTS = {"pr_open": "#2da44e", "pr_merged": "#8957e5", "pr_closed": "#8a8f98", "issue_closed": "#8a8f98"}
# A pull request's pill, in a reply and on the Git page, is in the colour of where it stands (lookupgit.pull_state), the
# same light or dark (each reads on either), its patch that colour faint: green while it's open, grey while it's a
# draft, red once a review asks for changes (whatever else it is: it's held up), purple once merged; closed without
# being merged, the same grey fainter and struck through. One not known yet is a plain link on a patch of the text's
# colour, and only takes its own when it's known: nothing moves.
PR_COLORS = {"open": "#2da44e", "draft": "#8a8f98", "changes": "#e5484d", "merged": "#8957e5", "closed": "#8a8f98"}
PR_SAID = {"open": wording.say("common.open"), "draft": wording.say("references.pr_draft"),
           "changes": wording.say("references.pr_changes_requested"), "merged": wording.say("references.pr_merged"),
           "closed": wording.say("references.pr_closed_unmerged")}


_REF_ICON_OPS = {}


def ref_icon_ops(icon):
    """A logo (REF_ICON_SVG) as what GSK draws, parsed once: [(Gsk.Path, fill or None, Gsk.Stroke or None, stroke or
    None, mask ops or None)], the colours currentColor (the text's), "#fff" or "#000". The SVG they use: path, circle and rect,
    filled or stroked (width, caps, joins), and a mask (white shows, black cuts) for a cut-out."""
    if icon in _REF_ICON_OPS:
        return _REF_ICON_OPS[icon]
    import xml.etree.ElementTree as ET
    root = ET.fromstring(REF_ICON_SVG[icon])
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
    colors = {"currentColor": rgba, "#fff": white, "#ffffff": white, "#000": black, "#000000": black}

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
    composer. A row's a logo (RefIcon), a name and a dimmed line about it; under them, how many there are, at the right,
    and at the left, dots while more are on their way. Rows are made as they're shown (a folder has thousands): bind(i,
    icon, name, about) says what row i is. ↑ ↓ Page Up and Page Down move in it (move); Enter or Tab, and a click, take
    what's picked (the caller's on_pick)."""

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
        self.note = Gtk.Label(xalign=1, hexpand=True, css_classes=["dim-label", "caption"])
        self.dots = WorkingDots()  # (more names on their way: they're listed as they come)
        self.dots.add_css_class("dim-label")
        foot = Gtk.Box(spacing=6, margin_start=4, margin_end=4)
        foot.append(self.dots)
        foot.append(self.note)
        box.append(self.scroll)
        box.append(foot)
        self.pop.set_child(box)
        set_popover(self.pop, parent)
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

    def show(self, n, at, note, pointing_to=None, coming=False):
        """n rows, row at picked (None: none), note under them at the right (how many); coming: more on their way, the
        dots at the left; up, if it wasn't, pointing at pointing_to (a Gdk.Rectangle in the parent's coordinates)."""
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
        self.dots.set_working(coming)
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
# start), Meta's model, effort, Code mode and Run commands, and Notifications. A window's own zoom (Ctrl/⌘ + − 0) stays
# its own.
SHARED_SETTINGS = ("accent", "theme", "text_scale", "icon_scale", "zoom_default", "auto_update", "code_font",
                   "meta_model", "meta_effort", "meta_code_mode", "meta_commands", "notify")
SETTINGS_WAIT_MS = 100  # (a write's events, and the file read once they've stopped)
_watch = {"monitor": None, "seen": None, "listeners": [], "wait": None}


# ---- desktop notifications: a reply finished (or a question asked) where you aren't looking, said by the desktop's
# own notifications: on Linux GLib's (Gio.Notification) or org.freedesktop.Notifications' over D-Bus (_gio_notifies),
# on macOS Notification Center's, through osascript. Settings → Notifications, in Baude and Record both.
_notified = {}  # (a notification's key: what a click on it does)
_fdo = {"ids": {}, "keys": {}, "subscribed": False}  # (org.freedesktop.Notifications': key → its id, and back)
FDO = ("org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications")


_identity = {}  # (notify_as's: the logo notifications show)


def notify_as(svg):
    """The app's logo as an SVG file: its notifications' icon (Linux)."""
    _identity["svg"] = svg


NOTIFY_PICTURE_PX = tokens.px("notification.picture")  # (a Bot's picture as its notification shows it, square)
NOTIFY_BADGE_PX = tokens.px("notification.badge")  # (the app's logo in its bottom-right corner)
NOTIFY_BADGE_GAP = tokens.px("notification.badge-gap")


def notification_picture(png, logo_svg):
    """A Bot's picture (png: its texture's PNG) as its notifications show it: round, the app's logo (logo_svg: an SVG file)
    in its bottom-right corner, cut out of it. A PNG file's path, in the cache (None: it couldn't be drawn)."""
    import hashlib
    import io
    import cairo
    from baudelib.applist import icon_png
    try:
        svg = open(logo_svg).read() if logo_svg else None
    except OSError:
        svg = None
    name = hashlib.sha1(png + (svg or "").encode()).hexdigest()[:16]
    path = os.path.join(GLib.get_user_cache_dir(), "baude", "notifications", f"{name}.png")
    if os.path.exists(path):
        return path
    try:
        pic = cairo.ImageSurface.create_from_png(io.BytesIO(png))
        size, badge = NOTIFY_PICTURE_PX, NOTIFY_BADGE_PX
        out = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(out)
        cr.arc(size / 2, size / 2, size / 2, 0, 2 * math.pi)
        cr.clip()
        k = size / min(pic.get_width(), pic.get_height())  # (filling the circle, cropped to its middle)
        cr.translate((size - pic.get_width() * k) / 2, (size - pic.get_height() * k) / 2)
        cr.scale(k, k)
        cr.set_source_surface(pic, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        if svg:
            cr = cairo.Context(out)
            at = size - badge
            cr.set_operator(cairo.OPERATOR_CLEAR)  # (a gap round the logo, whatever the banner's behind it)
            cr.arc(at + badge / 2, at + badge / 2, badge / 2 + NOTIFY_BADGE_GAP, 0, 2 * math.pi)
            cr.fill()
            cr.set_operator(cairo.OPERATOR_OVER)
            cr.set_source_surface(cairo.ImageSurface.create_from_png(io.BytesIO(icon_png(svg, badge, mac=False))),
                                  at, at)
            cr.paint()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        out.write_to_png(path + ".part")
        os.replace(path + ".part", path)
    except (GLib.Error, cairo.Error, OSError, ValueError, MemoryError):
        return None
    return path


def notify_logo():
    """The logo notifications show (notify_as's SVG file; None: none set)."""
    return _identity.get("svg")


def notifications_on():
    return load_settings().get("notify") is not False


def _gio_notifies(app):
    """Whether GLib's notifications reach the desktop for app: unique (its clicks come back to it: not Record, one
    process per start), its .desktop entry installed (GNOME's org.gtk.Notifications turns away an app without one).
    Otherwise org.freedesktop.Notifications is posted to itself."""
    if app.get_flags() & Gio.ApplicationFlags.NON_UNIQUE or not app.get_application_id():
        return False
    name = f"{app.get_application_id()}.desktop"
    return any(os.path.exists(os.path.join(d, "applications", name))
               for d in (GLib.get_user_data_dir(), *GLib.get_system_data_dirs()))


def _fdo_call(method, params, done=None):
    """org.freedesktop.Notifications' method, on the session bus, done(the answer's values) when it answers; its
    ActionInvoked and NotificationClosed heard from the first call on."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return
    if not _fdo["subscribed"]:
        _fdo["subscribed"] = True
        for signal in ("ActionInvoked", "NotificationClosed"):
            bus.signal_subscribe(FDO[0], FDO[2], signal, FDO[1], None, Gio.DBusSignalFlags.NONE,
                                 lambda _c, _s, _p, _i, sig, v: _fdo_signal(sig, *v.unpack()[:2]))

    def answered(b, res):
        try:
            out = b.call_finish(res).unpack()
        except GLib.Error:
            return
        if done is not None:
            done(out)
    bus.call(FDO[0], FDO[1], FDO[2], method, params, None, Gio.DBusCallFlags.NONE, -1, None, answered)


def _fdo_signal(signal, nid, action=None):
    """ActionInvoked (a click: its default action) or NotificationClosed, for one of ours."""
    key = _fdo["keys"].pop(nid, None)
    if key is None:
        return
    _fdo["ids"].pop(key, None)
    fn = _notified.pop(key, None)
    if signal == "ActionInvoked" and action == "default" and fn is not None:
        fn()


def macos_notification(title, body):
    """The osascript that posts a notification on macOS: the chat's name its title, what happened its body (two lines
    of it, with no subtitle above). osascript's takes no picture: its icon is the app it's credited to."""
    quote = lambda t: '"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"'  # noqa: E731
    return f"display notification {quote(body)} with title {quote(title)}"


def desktop_notify(key, title, body="", clicked=None, picture=None):
    """A desktop notification, key's (one key, one notification: a newer one takes the older's place): title the
    chat's name, body what happened; clicked(), run when it's clicked (Linux); picture, a Bot's
    (notification_picture's file), its icon in place of the app's logo (Linux). Nothing when Notifications is off. On
    macOS osascript's, credited to Script Editor (the apps run in Python's bundle, not one of their own): a click there
    opens Script Editor."""
    if not notifications_on():
        return
    body = " ".join((body or "").split())
    body = body[:157] + "…" if len(body) > 160 else body
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["osascript", "-e", macos_notification(title, body)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass
        return
    app = Gio.Application.get_default()
    if app is None:
        return
    _notified[key] = clicked or (lambda: None)
    if app.get_is_registered() and _gio_notifies(app):
        if app.lookup_action("notification-clicked") is None:
            act = Gio.SimpleAction.new("notification-clicked", GLib.VariantType.new("s"))
            act.connect("activate", lambda _a, v: (fn := _notified.pop(v.get_string(), None)) and fn())
            app.add_action(act)
        n = Gio.Notification.new(title)
        if picture or _identity.get("svg"):
            n.set_icon(Gio.FileIcon.new(Gio.File.new_for_path(picture or _identity["svg"])))
        if body:
            n.set_body(body)
        n.set_default_action_and_target("app.notification-clicked", GLib.Variant.new_string(key))
        app.send_notification(key, n)
        return

    def posted(out):
        nid = out[0]
        if _notified.get(key) is None:  # (taken back before it was up)
            _fdo_call("CloseNotification", GLib.Variant("(u)", (nid,)))
            return
        _fdo["ids"][key], _fdo["keys"][nid] = nid, key
    hints = {"urgency": GLib.Variant.new_byte(1)}
    if picture:  # (shown in place of app_icon, the logo)
        hints["image-path"] = GLib.Variant.new_string(GLib.filename_to_uri(picture))
    if app.get_application_id():  # (the desktop entry, which servers take the app's name and icon from)
        hints["desktop-entry"] = GLib.Variant.new_string(app.get_application_id())
    _fdo_call("Notify", GLib.Variant("(susssasa{sv}i)", (
        GLib.get_application_name() or "", _fdo["ids"].get(key, 0),
        GLib.filename_to_uri(_identity["svg"]) if _identity.get("svg") else app.get_application_id() or "", title, body,
        ["default", wording.say("common.open")], hints, -1)), posted)


def withdraw_notification(key):
    """key's notification taken back (what it was about seen now), if one's up."""
    if _notified.pop(key, None) is None:
        return
    if (nid := _fdo["ids"].pop(key, None)) is not None:
        _fdo["keys"].pop(nid, None)
        _fdo_call("CloseNotification", GLib.Variant("(u)", (nid,)))
    elif sys.platform != "darwin" and (app := Gio.Application.get_default()) is not None:
        app.withdraw_notification(key)


def fmt_scale(scale):
    """1.0, 1.2, 1.25: a size or a zoom as it's shown (Settings' rows show one decimal)."""
    text = f"{scale:.2f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


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


# Icons baude draws itself follow the theme's symbolic style (16x16 grid, 2px strokes as filled
# shapes), so GTK recolours them like any other icon and their weight matches the rest: schema/icons/baude. The
# Kubernetes view's sidebar categories, as Freelens draws them: schema/icons/kube.
BAUDE_ICONS = icons.names("baude")
KUBE_ICONS = icons.names("kube")


def ensure_icons():
    """Put baude's icons where the icon theme looks for them, and Adwaita's behind the desktop's theme (once per
    run)."""
    display = Gdk.Display.get_default()
    if display is None or getattr(ensure_icons, "done", False):
        return
    theme = Gtk.IconTheme.get_for_display(display)
    theme.add_search_path(icons.folder("baude"))
    theme.add_search_path(icons.folder("kube"))
    # GTK 4 looks only in the desktop's icon theme and hicolor, not Adwaita, so under another theme (Breeze,
    # Papirus…) an Adwaita name that theme lacks draws nothing. Adwaita's own folders, searched after every theme,
    # fill those in; a name the desktop's theme has is still drawn from it. Last, schema/icons/adwaita (the ones the
    # phone draws), for a machine without Adwaita.
    for root in theme.get_search_path():
        for sub in sorted(glob.glob(os.path.join(root, "Adwaita", "symbolic", "*"))):
            if os.path.isdir(sub):
                theme.add_search_path(sub)
    theme.add_search_path(icons.folder("adwaita"))
    ensure_icons.done = True


def globe_icon():
    """A globe where the icon theme has one (Yaru does); Adwaita only has the browser compass."""
    ensure_icons()
    display = Gdk.Display.get_default()
    if display is not None and Gtk.IconTheme.get_for_display(display).has_icon("globe-symbolic"):
        return "globe-symbolic"
    return "web-browser-symbolic"


def toast(widget, text):
    root = widget.get_root()
    if root is not None and hasattr(root, "toasts"):
        root.toasts.add_toast(Adw.Toast(title=text, timeout=2))


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
        if isinstance(w, Adw.ComboRow):  # are ours, and its scrolled window asks them for its adjustment as it goes.
            return  # Nor a combo row: its dispose, run again as it's freed, uses its list, freed the first time. Nor
        if type(w).__module__ != "__gi__":  # GTK's and libadwaita's own private parts (a tab, a switcher's button):
            yield w  # what they're part of disposes them, and disposed again after that, they corrupted memory;
        c = w.get_first_child()  # what's ours in them is, once they've let go of it. Those left out are freed once
        while c is not None:     # nothing holds them)
            if not isinstance(c, Gtk.Scrollbar):
                yield from parts(c)
            c = c.get_next_sibling()
    for w in list(parts(widget)):
        if w is not widget and w.get_parent() is not None:
            continue  # (a part its parent still holds: it goes with it)
        if isinstance(w, Gtk.MenuButton):  # (its dispose frees what makes its menu, set_create_popup_func's, each
            w.set_create_popup_func(None)   # time it's run, and it's run again as it's freed: let go of first)
        if isinstance(w, Gtk.Scrollable):  # (its adjustments, which its scrolled window's handlers are on: kept till
            w.set_hadjustment(None)          # it's freed, and those handlers usually reach back to its owner)
            w.set_vadjustment(None)
        if isinstance(w, Gtk.ListBox):  # (its rows' filter, headers and order: kept till it's freed, and they're
            w.set_filter_func(None)        # usually its owner's methods)
            w.set_header_func(None)
            w.set_sort_func(None)
        if isinstance(w, Gtk.Picture):  # (a picture's dispose lets go of its paintable but keeps pointing at it: the
            Gtk.Picture.set_paintable(w, None)  # second, as it's freed, let go of it again, and crashed)
        for c in [c for c in _children(w) if getattr(c, "set_on", None) is w]:
            c.unparent()  # (a popover of ours on it, set_popover's: a text view's dispose takes out its children one
            # by one, and one that isn't its own it can't, and tried again for ever. GTK's own popovers it sees to)
        w.run_dispose()
        # (its event controllers: a click's handler is the controller's, and dispose leaves those till the end. Taken
        # out after it: GTK's and libadwaita's own widgets use theirs in their dispose (a text view ends a selection's
        # drag with its drag gesture, a tab view's or navigation view's swipe takes its own out), and freed before, they
        # were used freed, or not found)
        controllers = w.observe_controllers()
        for ctl in [controllers.get_item(i) for i in range(controllers.get_n_items())]:
            w.remove_controller(ctl)
        for c in [c for c in walk(w) if isinstance(c, Gtk.Popover) and c.get_parent() is w]:
            c.unparent()  # (a menu set_parent'ed on it, which only its owner lets go of)
    return False


def set_popover(pop, parent):
    """pop set_parent'ed on parent, as ours: let go of before parent's disposed (_dispose_tree)."""
    pop.set_parent(parent)
    pop.set_on = parent


def free_closed_windows(app):
    """Each of app's windows freed once it's closed, as discard frees a widget, and each dialog shown in one once it's
    closed: closing one lets go of it, but what in it has handlers reaching back to it kept it all alive (GTK disposes
    a closed window only once nothing holds it), and with it whatever it held on to (a browser pane's timer, its
    chat's pictures), a window's worth each time. A window only taken out of the app, not closed, is left as it is."""
    def added(_app, window):
        if isinstance(window, (Adw.ApplicationWindow, Adw.Window)):
            window.connect("notify::visible-dialog", dialog_shown)

    def dialog_shown(window, _pspec):
        dialog = window.get_visible_dialog()
        if dialog is not None and not getattr(dialog, "freed_when_closed", False):
            dialog.freed_when_closed = True
            dialog.connect("closed", discard)

    def removed(_app, window):
        GLib.idle_add(closed, window)

    def closed(window):
        if any(w is window for w in Gtk.Window.list_toplevels()):
            return False
        window.freeing = True  # (what its parts say as they're disposed (a list's row unselected, a tab view's page
        # gone) isn't acted on: getattr(win, "freeing"))
        adw = isinstance(window, (Adw.ApplicationWindow, Adw.Window))
        # (what's ours in it, taken out and disposed: not the window's own insides, which its dispose sees to)
        parts = [window.get_content() if adw else window.get_child(), window.get_titlebar(),
                 *[c for c in _children(window) if isinstance(c, Gtk.Popover)]]
        (window.set_content if adw else window.set_child)(None)
        window.set_titlebar(None)
        for w in parts:
            if w is not None:
                if w.get_parent() is not None:
                    w.unparent()
                _dispose_tree(w)
        if isinstance(window, Gio.ActionMap):  # (its ☰'s actions: their handlers are mostly its own methods)
            for name in window.list_actions():
                window.remove_action(name)
        window.run_dispose()
        controllers = window.observe_controllers()
        for ctl in [controllers.get_item(i) for i in range(controllers.get_n_items())]:
            window.remove_controller(ctl)
        return False

    app.connect("window-added", added)
    app.connect("window-removed", removed)


def _children(widget):
    c = widget.get_first_child()
    while c is not None:
        yield c
        c = c.get_next_sibling()


# Back, forward and up through what's been shown (MainTabs.back, forward; a window's up): ⌥⌘ and an arrow on macOS
# (⌘ alone moves the caret in text), Alt and an arrow elsewhere, as browsers have them
NAV_MODS = "<Meta><Alt>" if sys.platform == "darwin" else "<Alt>"
NAV_KEYS = {"back": NAV_MODS + "Left", "forward": NAV_MODS + "Right", "up": NAV_MODS + "Up"}


INSIDE_GAP = tokens.px("gap.inside")  # (the inside, set this far into the frame: from the panes beside it and the
# window's edge)


def inside(widget, start=True, end=True, under_bar=False):
    """widget made the inside of the window (the box the work's read and written in, square, set into the frame:
    CSS's .baude-inside), its edges INSIDE_GAP in (start, end: on that side; a pane beside it has its own gap), its
    contents kept inside it. under_bar: right under the window's bar, where it's INSIDE_GAP down too under a theme
    whose bar isn't the frame's colour (_probe_title_bar). Returns it."""
    widget.add_css_class("baude-inside")
    if under_bar:
        widget.add_css_class("baude-under-bar")
    widget.set_overflow(Gtk.Overflow.HIDDEN)
    widget.set_margin_start(INSIDE_GAP if start else 0)
    widget.set_margin_end(INSIDE_GAP if end else 0)
    widget.set_margin_bottom(INSIDE_GAP)
    return widget


def watch_style(widget, signal, update):
    """update(widget) as the shared style manager's signal ("notify::dark", "notify::accent-color") fires, while the
    widget is realized (in a window): a closed window's widgets leave no handler on the app-wide manager, even when
    something else keeps them alive. Realized again (moved to another window) after a change it missed, it's updated
    then."""
    manager = Adw.StyleManager.get_default()
    prop = signal.split("::", 1)[1]
    target = widget.weak_ref()
    held, seen = [], [manager.get_property(prop)]

    def changed(*_):
        seen[0] = manager.get_property(prop)
        if (current := target()) is not None:
            update(current)

    def realized(w):
        if held:
            return
        held.append(manager.connect(signal, changed))
        if manager.get_property(prop) != seen[0]:
            changed()

    def unrealized(*_):
        while held:
            manager.disconnect(held.pop())

    widget.connect("realize", realized)
    widget.connect("unrealize", unrealized)
    if widget.get_realized():
        realized(widget)
    widget.weak_ref(unrealized)


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
    for n, said in ((365 * 86400, lambda k: wording.say("time.years_ago", n=k)),
                    (30 * 86400, lambda k: wording.say("time.months_ago", n=k)),
                    (7 * 86400, lambda k: wording.say("time.weeks_ago", n=k)),
                    (86400, lambda k: wording.say("time.days_ago", n=k)),
                    (3600, lambda k: wording.say("time.hours_ago", n=k)),
                    (60, lambda k: wording.say("time.minutes_ago", n=k))):
        if secs >= n:
            return said(secs // n)
    return wording.say("time.just_now")


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


def view_bar(spacing=6, margin_start=12, margin_end=10, margin_top=6, margin_bottom=6):  # (as tall as a header bar)
    """The top bar of a view that covers the window (Kubernetes, Database schema, an image large). On macOS the
    window's traffic lights still sit on top of it, over the header bar it covers: room for them. Its view puts it in a
    Gtk.WindowHandle, as the header bar is: dragged, it moves the window; double-clicked, maximizes it. (Not a second native
    Gtk.WindowControls: two of them fight over the titlebar's height, and the header bar can no longer be dragged or
    double-clicked)"""
    bar = Gtk.Box(spacing=spacing, margin_start=margin_start, margin_end=margin_end, margin_top=margin_top,
                  margin_bottom=margin_bottom)
    bar.gap = None  # (the traffic lights' room: hidden while the view's out in a window with its own)
    if sys.platform == "darwin":
        bar.gap = Gtk.Box(width_request=80)
        bar.append(bar.gap)
    return bar


def empty_label(text):
    """A list's placeholder: a dim line of text."""
    lbl = Gtk.Label(label=text, margin_top=24, margin_bottom=24)
    lbl.add_css_class("dim-label")
    return lbl


# The app's own sizes (Settings → Size), the same in every window and pane, Baude's and Record's: the text's and the
# icons'. 1.0 is the usual. And light or dark as macOS is (macos_appearance).
SCALE_MIN, SCALE_MAX = 0.7, 2.0


ZOOM_MODS = ("<Control>", "<Meta>") if sys.platform == "darwin" else ("<Control>",)


SETTINGS_KEY = "<Meta>comma" if sys.platform == "darwin" else "<Control>comma"  # (⌘, as a Mac app's Settings…)
FEEDBACK_KEY = "<Meta>period" if sys.platform == "darwin" else "<Control>period"  # (Send Feedback…, over whatever's open)
META_KEY = "<Control>m"  # (Meta shown and focused, or closed if open; Control on macOS too: ⌘M is Minimize)


def first_keys(window, keys):
    """Window shortcuts (triggers, fn) taken in the capture phase, before the focused widget sees the key: for keys
    macOS's text boxes bind themselves (Ctrl+B / Ctrl+F, Emacs's back / forward a character)."""
    sc = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
    for triggers, fn in keys:
        for t in triggers.split():
            sc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(t),
                                             Gtk.CallbackAction.new(lambda *_, fn=fn: fn() or True)))
    window.add_controller(sc)


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
ACCENTS = {name: getattr(Adw.AccentColor, name.upper()) for name in tokens.names("accent")}
_accent = {"name": None, "provider": None, "css": None}  # (the one picked, None for the system's, and its CSS)


def accent():
    """The accent, an Adw.AccentColor: the one picked, or the system's."""
    name = _accent["name"]
    return ACCENTS[name] if name in ACCENTS else Adw.StyleManager.get_default().get_accent_color()


def logo(app):
    """app's logo (schema/icons/apps: "baude", "record") in the accent settings.json has: icons.logo's, as it's drawn
    while the system's is."""
    return icons.logo(app, load_settings().get("accent"))


THEMED = {"light": {}, "dark": {}}  # (the theme drawn's colours, light and dark, by token's path: themelook's)
ACCENT_TOKENS = ("libadwaita.accent_bg_color", "libadwaita.accent_color", "libadwaita.accent_fg_color")


def accent_picked():
    """Whether Accent Colour has one picked (not System): then it's drawn over a theme's accent, everywhere."""
    return _accent["name"] in ACCENTS


def _themed(path, picked_too=False):
    """The theme drawn's colour for a token (libadwaita.accent_bg_color), light or dark as it's now; None if it has
    none. A theme's accent is only while Accent Colour is System (picked_too: it, whatever's picked)."""
    if path in ACCENT_TOKENS and accent_picked() and not picked_too:
        return None
    hex_ = THEMED["dark" if Adw.StyleManager.get_default().get_dark() else "light"].get(path)
    rgba = Gdk.RGBA()
    return rgba if hex_ and rgba.parse(hex_) else None


def theme_accent_rgba():
    """The theme drawn's accent (its accent_bg_color), whatever Accent Colour has; None if it sets none."""
    return _themed("libadwaita.accent_bg_color", picked_too=True)


def accent_rgba():
    """The accent's colour as @accent_bg_color has it: a patch's, a selection's (the one picked; else a theme's, if it
    has one)."""
    return _themed("libadwaita.accent_bg_color") or Adw.AccentColor.to_rgba(accent())


def accent_text_rgba():
    """The accent's colour as @accent_color has it, for text on the window's background: light or dark, as it's now."""
    dark = Adw.StyleManager.get_default().get_dark()
    if (rgba := _themed("libadwaita.accent_color")) is not None:
        return rgba
    if (bg := _themed("libadwaita.accent_bg_color")) is not None:
        return Adw.rgba_to_standalone(bg, dark)
    return Adw.AccentColor.to_standalone_rgba(accent(), dark)


# ---------------------------------------------------------------- a tool call's card
# (Baude's chat's and Meta's: a tool card's toggles and status, and what moves while something works)

class Ticker:
    """What moves while something works: a working session's dot pulsing (its opacity eased between 1 and 0.25 and
    back every 0.9 s) and the spinners of calls still running (Spinner). One timer moves them all, FPS times a
    second: each frame GTK draws costs 1.5-3 ms of CPU on a Mac (most of it GSK readying the window's surface), and
    a CSS animation or an Adw.Spinner draws 60 a second, so one working session's dot, or one long-running command,
    took a sixth of a core. In a window you aren't looking at they hold still."""

    FPS, IDLE_MS, PERIOD, LOW, STILL = 20, 500, tokens.seconds("motion.pulse"), 0.25, 0.6
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
        self.set_tooltip_text(wording.say("common.working") if working else None)
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

    def __init__(self, toggles, grid, row, label=wording.say("common.details"), button=False):
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
