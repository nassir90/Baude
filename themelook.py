"""themelook — a theme drawn in Baude, Record, Emission and Multiplex (apply: one Gtk.CssProvider at USER priority,
over all of their CSS and the accent's), Settings → Theme (settings_row), and what Meta's theme tools ask this
machine's host (ask_host). A theme is its host's (baudelib.themes: the themes, theme_save and theme_remove ops): its
colours as the host works them out, light and dark (each of libadwaita's as GTK names it, and its CSS variable;
baude's as theming.CSS names it), then its own theme.css. The one shown is the preview Meta asked for, else the one
Settings has (settings.json's theme, shared by the apps here). As an app starts its look is read here, through
baudelib.themes (the host's own code), so the first frame has it whether or not the host's up yet; it's drawn again
as the theme's files change, and as light and dark change. Each time it's drawn it's tried on a probe (a label and
a button in a window no one sees): if their text can't be seen or has no size, Default is drawn for this run and a
toast says why. BAUDE_NO_THEME=1 (each app's --no-theme) starts with Default whatever Settings has.

A theme's accent is drawn only while Accent Colour is System: one picked is drawn over it, everywhere."""
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402

from baudelib import themes, wire, wording  # noqa: E402
import widgets  # noqa: E402
from widgets import ACCENT_TOKENS, THEMED, load_settings, save_settings, walk  # noqa: E402

_state = {"display": None, "provider": None, "preview": None, "shown": None, "errors": [], "offset": 0,
          "monitors": [], "wait": None, "off": None, "toast": None, "tries": 0}
FAINT = 1.5  # (a probe's text against its background, as WCAG's contrast ratio: at least this, or it can't be read)
VARIABLES = {"borders": "border-color"}  # (a named colour whose CSS variable isn't its name's)
DEFAULT = "default"


def picked():
    """The theme Settings has: its name, or None for Baude's own look."""
    name = load_settings().get("theme")
    return name if isinstance(name, str) and themes.NAME.fullmatch(name) else None


def shown_name():
    """The theme drawn now: the preview's, else Settings' (none with BAUDE_NO_THEME, till one's picked)."""
    if _state["preview"] is not None:
        return _state["preview"]["name"]
    if _state["off"] is None:  # (read as it's first drawn: each app's --no-theme sets it as it starts)
        _state["off"] = os.environ.get("BAUDE_NO_THEME") == "1"
    return None if _state["off"] else picked()


def drawn():
    """The theme drawn now, as its host worked it out (Theme.json's); None for Default."""
    return _state["shown"]


def colours_css(theme, dark):
    """A theme's colours, light or dark, named as GTK's CSS and Baude's name them: a line each. Its accent's left out
    while Accent Colour has one picked."""
    lines = []
    for path, colour in sorted((theme.get("dark" if dark else "light") or {}).items()):
        if path in ACCENT_TOKENS and widgets.accent_picked():
            continue
        group, name = path.split(".", 1)
        rgba = Gdk.RGBA()
        if not rgba.parse(colour):
            continue
        value = rgba.to_string()
        if group == "libadwaita":
            lines.append(f"@define-color {name} {value};")
            lines.append(f":root {{ --{VARIABLES.get(name, name.replace('_', '-'))}: {value}; }}")
        else:
            lines.append(f"@define-color baude_{name} {value};")
    return "".join(line + "\n" for line in lines)


def apply(display=None):
    """The theme shown (the preview, else Settings') drawn in every window here, read again from its files."""
    display = display or _state["display"] or Gdk.Display.get_default()
    if display is None:
        return
    if _state["provider"] is None:
        _state["display"], _state["provider"] = display, Gtk.CssProvider()
        _state["provider"].connect("parsing-error", _parsing_error)
        Gtk.StyleContext.add_provider_for_display(display, _state["provider"], Gtk.STYLE_PROVIDER_PRIORITY_USER)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: _load())
    name = shown_name()
    theme = themes.theme(name) if name else None
    if theme is not None and "error" in theme:
        print(f"baude: the theme {name}: {theme['error']}", file=sys.stderr)
        theme = None
    _state["shown"] = theme
    _load()
    _watch(name)


def picked_accent_css(dark):
    """The accent picked in Accent Colour, drawn over what a theme's CSS makes of the accent ("" while it's System)."""
    if not widgets.accent_picked():
        return ""
    bg = Adw.AccentColor.to_rgba(widgets.accent()).to_string()
    text = Adw.AccentColor.to_standalone_rgba(widgets.accent(), dark).to_string()
    return (f"\n@define-color accent_bg_color {bg};\n@define-color accent_color {text};\n"
            f"@define-color accent_fg_color #ffffff;\n"
            f":root {{ --accent-bg-color: {bg}; --accent-color: {text}; --accent-fg-color: #ffffff; }}\n")


def _load():
    theme = _state["shown"]
    _state["errors"] = []
    for context in themes.CONTEXTS:  # (what's drawn here in the accent, not by the CSS: accent_rgba's)
        THEMED[context] = dict((theme or {}).get(context) or {})
    dark = Adw.StyleManager.get_default().get_dark()
    head = colours_css(theme, dark) if theme is not None else ""
    _state["offset"] = head.count("\n")  # (its CSS's lines after them: what GTK can't read, said by theme.css's line)
    _state["provider"].load_from_string(head + ((theme or {}).get("css") or "") +
                                        (picked_accent_css(dark) if theme is not None else ""))
    if theme is not None and (why := unusable()) is not None:  # (Default, for this run, and a toast saying why)
        _state["shown"] = None
        refused(theme["name"], why)
        _load()
        return
    for win in Gtk.Window.list_toplevels():
        for widget in walk(win):
            if (changed := getattr(widget, "accent_changed", None)) is not None:
                changed()
            widget.queue_draw()


def accent_changed():
    """Accent Colour changed (theming.apply_accent): a theme's accent is drawn only while it's System."""
    if _state["provider"] is not None and _state["shown"] is not None:
        _load()


def setting_changed():
    """Settings' theme changed in another app here: drawn here too (BAUDE_NO_THEME's Default too)."""
    _state["off"] = False
    apply()


class _Probe(Gtk.Widget):
    """unusable's: what's in it drawn by hand, never on a screen."""

    def do_snapshot(self, snapshot):
        if (child := self.get_first_child()) is not None:
            self.snapshot_child(child, snapshot)


def _pixels(texture):
    down = Gdk.TextureDownloader.new(texture)
    down.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = down.download_bytes()
    return data.get_data(), stride


def _luminance(rgb):
    lin = [c / 255 / 12.92 if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _over(top, under):
    """An RGBA pixel (0 to 255) over an opaque RGB one."""
    a = top[3] / 255
    return tuple(top[i] * a + under[i] * (1 - a) for i in range(3))


def _region_why(data, stride, rect):
    """A probe's text, in rect of what was drawn: None if it can be read, else why not (its copy's key)."""
    pixels = []
    for y in range(max(0, int(rect.origin.y)), int(rect.origin.y + rect.size.height)):
        row = y * stride
        for x in range(max(0, int(rect.origin.x)), int(rect.origin.x + rect.size.width)):
            pixels.append(bytes(data[row + 4 * x:row + 4 * x + 4]))
    if not pixels or max(p[3] for p in pixels) < 26:
        return "theme_invisible"
    counts = {}
    for p in pixels:
        counts[p] = counts.get(p, 0) + 1
    bg = _over(max(counts, key=counts.get), (255, 255, 255))
    under = _luminance(bg)
    best = max((max(lum, under) + 0.05) / (min(lum, under) + 0.05)
               for lum in {_luminance(_over(p, bg)) for p in counts})
    return None if best >= FAINT else "theme_faint"


def unusable():
    """The CSS loaded now tried on a probe, a label and a button in a window never shown, drawn by hand: None if
    their text can be seen, else why not (its copy's key: no size, invisible, or faint against its background)."""
    win = Gtk.Window()
    probe = _Probe()
    frame = Gtk.Box(css_name="window", css_classes=["background"], spacing=12)
    label, button = Gtk.Label(label="Theme"), Gtk.Button(label="Theme")
    frame.append(label)
    frame.append(button)
    frame.set_parent(probe)
    win.set_child(probe)
    try:
        for w in (label, button):
            if min(w.measure(o, -1)[1] for o in (Gtk.Orientation.HORIZONTAL, Gtk.Orientation.VERTICAL)) <= 0:
                return "theme_no_size"
        if label.get_color().alpha < 0.1 or button.get_child().get_color().alpha < 0.1:
            return "theme_invisible"
        _min, size = frame.get_preferred_size()
        width, height = max(size.width, 1), max(size.height, 1)
        probe.realize()
        probe.map()
        rect = Gdk.Rectangle()
        rect.width, rect.height = width, height
        frame.size_allocate(rect, -1)
        snap = Gtk.Snapshot()
        probe.do_snapshot(snap)
        node = snap.to_node()
        if node is None:
            return "theme_invisible"
        renderer = Gsk.CairoRenderer()
        renderer.realize_for_display(win.get_display())
        try:
            data, stride = _pixels(renderer.render_texture(node, Graphene.Rect().init(0, 0, width, height)))
        finally:
            renderer.unrealize()
        for w in (label, button.get_child()):
            ok, bounds = w.compute_bounds(frame)
            if ok and (why := _region_why(data, stride, bounds)) is not None:
                return why
        return None
    except Exception as e:  # noqa: BLE001 (a probe that can't be drawn isn't a reason to refuse a theme)
        print("baude: a theme's probe:", e, file=sys.stderr)
        return None
    finally:
        frame.unparent()
        win.destroy()


def refused(name, why):
    """The theme name couldn't be used (why: its copy's key): said in a toast in each window here that has toasts, as
    soon as one's shown."""
    _state["toast"] = wording.say("shared_settings.theme_refused", name=name, why=wording.say(f"shared_settings.{why}"))
    print(f"baude: {_state['toast']}", file=sys.stderr)
    if _state["tries"] == 0:
        _tell()


def _tell():
    wins = [w for w in Gtk.Window.list_toplevels() if hasattr(w, "toasts") and w.get_mapped()]
    if not wins:
        _state["tries"] += 1
        if _state["tries"] < 120:  # (its windows not up yet: a minute's tries)
            GLib.timeout_add(500, _tell)
        else:
            _state["tries"] = 0
        return False
    _state["tries"] = 0
    for w in wins:
        w.toasts.add_toast(Adw.Toast(title=_state["toast"], timeout=10))
    return False


def _parsing_error(_provider, section, error):
    line = section.get_start_location().lines + 1 - _state["offset"]
    said = f"line {line}: {error.message}" if line > 0 else error.message
    if said not in _state["errors"]:
        _state["errors"].append(said)


def errors():
    """What GTK couldn't read of the theme shown's CSS, each with its line in theme.css."""
    return list(_state["errors"])


def _watch(name):
    """The themes folder (and the theme's own) watched: a change to the theme shown's files is drawn."""
    for mon in _state["monitors"]:
        mon.cancel()
    _state["monitors"] = []
    if name is None:
        return
    for path in (themes.folder(), f"{themes.folder()}/{name}"):
        try:
            mon = Gio.File.new_for_path(path).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            continue
        mon.connect("changed", _changed)
        _state["monitors"].append(mon)


def _changed(*_):
    if _state["wait"] is not None:
        GLib.source_remove(_state["wait"])
    _state["wait"] = GLib.timeout_add(150, _reread)


def _reread():
    _state["wait"] = None
    apply()
    return False


def preview(name):
    """An installed theme drawn here for a look, Settings left as it is (None: Settings' again)."""
    _state["preview"] = None if name is None or name == picked() and not _state["off"] else {"name": name}
    apply()


def pick(name):
    """Settings' theme (None: Baude's own look): kept in settings.json, drawn here and in the other apps."""
    _state["preview"], _state["off"] = None, False
    save_settings(theme=name)
    apply()


def ask_host(msg, timeout=10, start=True):
    """A request to this machine's host (started if need be; start False: only if it's running), waited for: its
    answer, or {"error": why}."""
    from baudelib import hosts
    try:
        if not start and not os.path.exists(wire.socket_path()):
            raise OSError(wording.say("host_status.no_answer"))
        if start and not hosts.start_here():
            raise OSError(wording.say("host_status.none_started", log=hosts.log_path()))
        ch, _info = wire.reach({"via": "local"}, timeout=timeout)
        try:
            ch.sock.settimeout(timeout)
            ch.send({**msg, "req": 1})
            for m in ch:
                if m.get("req") == 1:
                    return m if m.get("op") == msg["op"] else {"error": wire.said(m)}
        finally:
            ch.close()
    except (OSError, ValueError) as e:
        return {"error": str(e) or wording.say("host_status.no_answer")}
    return {"error": wording.say("host_status.connection_dropped")}


def listed():
    """The themes this machine's host keeps, by name ([] if it isn't running or can't say)."""
    return [t["name"] for t in ask_host({"op": "themes"}, timeout=3, start=False).get("themes") or []]


def said(name):
    return wording.say("meta_settings.effort_default") if name in (None, DEFAULT) else name


def settings_row():
    """Settings → Theme: Default (Baude's own look), then the themes this machine's host keeps. Picking one draws
    it at once, in every window here and the other apps', and keeps it."""
    import depsview
    from theming import follow_setting
    names = [DEFAULT] + ([picked()] if picked() else [])
    row = Adw.ComboRow(title=wording.say("shared_settings.theme"), model=Gtk.StringList.new([said(n) for n in names]),
                       selected=len(names) - 1)
    row.syncing = False

    def show(listed):
        row.syncing = True
        try:
            names[:] = listed
            row.set_model(Gtk.StringList.new([said(n) for n in names]))
            row.set_selected(names.index(picked()) if picked() in names else 0)
        finally:
            row.syncing = False

    def took(reply):
        if "error" not in reply:
            show([DEFAULT] + [t["name"] for t in reply.get("themes") or []] +
                 ([picked()] if picked() and picked() not in [t["name"] for t in reply.get("themes") or []] else []))
        return False

    def changed(r, _p):
        if r.syncing or r.get_selected() >= len(names):
            return
        name = names[r.get_selected()]
        pick(None if name == DEFAULT else name)
    row.connect("notify::selected", changed)
    depsview.ask({"via": "local"}, took, {"op": "themes"})
    return follow_setting(row, "theme", lambda v: show(names + ([v] if v and v not in names else [])))
