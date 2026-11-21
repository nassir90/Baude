"""What a GTK window shows, as a list of items a user sees, top to bottom then left to right: the parity rig's dump
(the phone's rig writes the same shape from Compose's semantics). Each item:

    {"kind": "text" | "button" | "row" | "input" | "switch" | "check" | "image" | "graphic",
     "text": what it says, "sub": a row's subtitle, "icon": an icon's name, "tip": its tooltip,
     "on": a switch's or check's state, "dim": greyed or insensitive,
     "area": the part of the window it's in (the areas given; "dialog", "popover"),
     "at": [x, y, w, h] in the window's points}

Only what's on screen: mapped, shown, and inside every scrolled view it's in (a chat's scrolled-off messages aren't).
A button's or row's own labels and icon are part of it, not items of their own."""
from gi.repository import Adw, Gtk

BUTTONS = (Gtk.Button, Gtk.MenuButton, Gtk.DropDown, Adw.SplitButton)
CLIPS = (Gtk.ScrolledWindow, Gtk.Viewport)
GRAPHICS = (Gtk.Spinner, Gtk.LevelBar, Gtk.ProgressBar)
OVERLAYS = {"Repaint", "SelectionMarks", "FindMarks", "GtkWindowButtonsQuartz", "LineGutter"}  # (drawn over a whole
# view, or the window's own buttons: nothing a user reads)


def _bounds(widget, to):
    ok, r = widget.compute_bounds(to)
    if not ok:
        return None
    return [r.get_x(), r.get_y(), r.get_width(), r.get_height()]


def _cut(a, b):
    x, y = max(a[0], b[0]), max(a[1], b[1])
    w, h = min(a[0] + a[2], b[0] + b[2]) - x, min(a[1] + a[3], b[1] + b[3]) - y
    return [x, y, w, h] if w > 0.5 and h > 0.5 else None


def _texts(widget):
    """The labels' text inside a widget, in order."""
    out = []
    for w in _walk(widget):
        if isinstance(w, (Gtk.Label, Gtk.Inscription)) and w.get_mapped():
            t = (w.get_text() or "").strip()
            if t:
                out.append(t)
    return out


def _icons(widget):
    out = []
    for w in _walk(widget):
        if isinstance(w, Gtk.Image) and w.get_mapped() and w.get_icon_name():
            out.append(w.get_icon_name())
    return out


def _walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


def _dim(widget):
    return not widget.is_sensitive() or widget.has_css_class("dim-label") or widget.get_opacity() < 0.7


def _item(kind, widget, at, **kw):
    it = {"kind": kind}
    it.update({k: v for k, v in kw.items() if v not in (None, "", [], False) and k != "area"})
    it["area"] = kw.get("area")
    tip = widget.get_tooltip_text()
    if tip and tip != it.get("text"):
        it["tip"] = tip
    if _dim(widget):
        it["dim"] = True
    it["at"] = [round(v) for v in at]
    return it


def items(window, areas=()):
    """What window shows. areas: (widget, name) pairs, outermost first: an item's area is the innermost it's in."""
    out = []
    names = {id(w): n for w, n in areas if w is not None}
    order = [n for _, n in areas] + ["dialog", "popover"]
    full = [0, 0, window.get_width(), window.get_height()]
    _visit(window, window, full, out, "window", names=names)
    for pop in _popovers(window):
        _visit(pop, pop, [0, 0, pop.get_width(), pop.get_height()], out, "popover", origin=_origin(pop), names=names)
    rank = {n: i for i, n in enumerate(["window"] + order)}
    out.sort(key=lambda i: (rank.get(i["area"], 0), round(i["at"][1] / 6), i["at"][0]))
    return out


def _popovers(window):
    return [w for w in _walk(window) if isinstance(w, Gtk.Popover) and w.get_mapped() and w.get_visible()]


def _origin(pop):
    x = y = 0
    outer = pop
    while isinstance(outer, Gtk.Popover) and outer.get_surface() is not None:
        x, y = x + outer.get_surface().get_position_x(), y + outer.get_surface().get_position_y()
        outer = outer.get_parent().get_native() if outer.get_parent() is not None else None
    return x, y


def _visit(widget, root, clip, out, where, origin=(0, 0), names=None):
    if not (widget.get_visible() and widget.get_mapped() and widget.get_child_visible()) or widget.get_opacity() == 0:
        return
    if isinstance(widget, Gtk.Popover) and widget is not root:
        return  # (its own surface: items() walks it apart)
    b = _bounds(widget, root)
    if b is None:
        return
    b = [b[0] + origin[0], b[1] + origin[1], b[2], b[3]]
    seen = _cut(b, clip)
    if seen is None and not isinstance(widget, (Gtk.Window, Gtk.Popover)):
        return
    if isinstance(widget, CLIPS):
        clip = seen or clip
    where = "dialog" if isinstance(widget, Adw.Dialog) else names.get(id(widget), where)
    made = _made(widget, b, where)
    if made is not None:
        out.append(made)
        if made["kind"] in ("button", "row", "switch", "check", "input", "image", "graphic", "text"):
            if made["kind"] != "row":
                return  # (a button's labels and icon are the button's)
    child = widget.get_first_child()
    while child is not None:
        _visit(child, root, clip, out, where, origin, names)
        child = child.get_next_sibling()
    if made is not None and made["kind"] == "row":
        # (a row's title and subtitle are the row's; what else it holds, a switch or a button, is its own)
        own = {made.get("text"), made.get("sub")}
        tail = [i for i in out[out.index(made) + 1:] if not (i["kind"] == "text" and i.get("text") in own)]
        del out[out.index(made) + 1:]
        out.extend(tail)


def _made(w, at, where):
    kw = {"area": where}
    if isinstance(w, Gtk.Switch):
        return _item("switch", w, at, on=w.get_active(), **kw)
    if isinstance(w, Gtk.CheckButton):
        return _item("check", w, at, text=" ".join(_texts(w)), on=w.get_active(), **kw)
    if isinstance(w, BUTTONS):
        icon = (w.get_icon_name() if hasattr(w, "get_icon_name") else None) or next(iter(_icons(w)), None)
        return _item("button", w, at, text=" ".join(_texts(w)), icon=icon, **kw)
    if isinstance(w, Adw.PreferencesRow) and not isinstance(w, Adw.ExpanderRow):
        sub = w.get_subtitle() if hasattr(w, "get_subtitle") else None
        if isinstance(w, Adw.EntryRow):
            return _item("input", w, at, text=w.get_text(), sub=w.get_title(), **kw)
        return _item("row", w, at, text=w.get_title(), sub=sub,
                     icon=w.get_icon_name() if hasattr(w, "get_icon_name") else None,
                     button=bool(getattr(w, "get_activatable", lambda: False)()), **kw)
    if isinstance(w, Gtk.ListBoxRow) and w.get_activatable() and type(w) is not Gtk.ListBoxRow:
        return None
    if isinstance(w, Gtk.TextView):
        buf = w.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        return _item("input" if w.get_editable() else "text", w, at, text=text.strip(), **kw)
    if isinstance(w, Gtk.Editable) and isinstance(w, (Gtk.Entry, Gtk.Text, Gtk.SearchEntry, Gtk.PasswordEntry)):
        return _item("input", w, at, text=w.get_text(),
                     sub=w.get_placeholder_text() if hasattr(w, "get_placeholder_text") else None, **kw)
    if isinstance(w, (Gtk.Label, Gtk.Inscription)):
        t = (w.get_text() or "").strip()
        if not t:
            return None
        return _item("text", w, at, text=t, mono=w.has_css_class("monospace") or None, **kw)
    if isinstance(w, Gtk.Image):
        return _item("image", w, at, icon=w.get_icon_name() or "picture", **kw)
    if isinstance(w, Gtk.Picture):
        return _item("image", w, at, icon="picture", **kw)
    if isinstance(w, GRAPHICS) or (w.get_first_child() is None and type(w).__module__ not in ("gi.repository.Gtk",
                                                                                              "gi.repository.Adw",
                                                                                              "gi.overrides.Gtk")
                                   and at[2] > 2 and at[3] > 2):
        return None if type(w).__name__ in OVERLAYS else _item("graphic", w, at, icon=type(w).__name__, **kw)
    return None
