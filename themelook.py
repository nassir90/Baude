"""themelook — a theme drawn in Baude, Record, Emission and Multiplex (apply: one Gtk.CssProvider at USER priority,
over all of their CSS and the accent's), Settings → Theme (settings_row), and what Meta's theme tools ask this
machine's host (ask_host). A theme is its host's (baudelib.themes: the themes, theme_save and theme_remove ops): its
colours as the host works them out, light and dark (each of libadwaita's as GTK names it, and its CSS variable;
baude's as theming.CSS names it), then its own theme.css. The one shown is the preview Meta asked for, else the one
Settings has (settings.json's theme, shared by the apps here). As an app starts its look is read here, through
baudelib.themes (the host's own code), so the first frame has it whether or not the host's up yet; it's drawn again
as the theme's files change, and as light and dark change."""
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from baudelib import themes, wire, wording  # noqa: E402
from widgets import THEMED, load_settings, save_settings, walk  # noqa: E402

_state = {"display": None, "provider": None, "preview": None, "shown": None, "errors": [], "offset": 0,
          "monitors": [], "wait": None}
VARIABLES = {"borders": "border-color"}  # (a named colour whose CSS variable isn't its name's)
DEFAULT = "default"


def picked():
    """The theme Settings has: its name, or None for Baude's own look."""
    name = load_settings().get("theme")
    return name if isinstance(name, str) and themes.NAME.fullmatch(name) else None


def shown_name():
    """The theme drawn now: the preview's, else Settings'."""
    return _state["preview"]["name"] if _state["preview"] is not None else picked()


def colours_css(theme, dark):
    """A theme's colours, light or dark, named as GTK's CSS and Baude's name them: a line each."""
    lines = []
    for path, colour in sorted((theme.get("dark" if dark else "light") or {}).items()):
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


def _load():
    theme = _state["shown"]
    _state["errors"] = []
    for context in themes.CONTEXTS:  # (what's drawn here in the accent, not by the CSS: accent_rgba's)
        THEMED[context] = dict((theme or {}).get(context) or {})
    head = colours_css(theme, Adw.StyleManager.get_default().get_dark()) if theme is not None else ""
    _state["offset"] = head.count("\n")  # (its CSS's lines after them: what GTK can't read, said by theme.css's line)
    _state["provider"].load_from_string(head + ((theme or {}).get("css") or ""))
    for win in Gtk.Window.list_toplevels():
        for widget in walk(win):
            if (changed := getattr(widget, "accent_changed", None)) is not None:
                changed()
            widget.queue_draw()


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
    _state["preview"] = None if name is None or name == picked() else {"name": name}
    apply()


def pick(name):
    """Settings' theme (None: Baude's own look): kept in settings.json, drawn here and in the other apps."""
    _state["preview"] = None
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
