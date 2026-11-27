"""What the user does in Baude and Record, named as schema/ui names it: the index is the spec itself. Each widget is
the entity of the nearest widget holding it whose class an entry's code names (`file.py:Class`, or one marked with
mark()), and a button, row or menu item used is the part of that entity whose copy says what it shows (its label or
tooltip, by schema/copy's key). What's on screen (screen()) and the user's last few actions (crumbs) go with each
error this process has (baudelib.errorbook), as do GLib's and GTK's criticals and warnings (a log writer) and
exceptions in main-loop callbacks. install(app) is the one call an app makes, before its windows. Nothing here runs
till something's used: a click is looked at as it's made."""
import ctypes
import glob
import json
import os
import sys
import threading
import time

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from baudelib import errorbook, wire, wording

CRUMBS_MAX = 8
PART_RECENT_S = 5  # (an error this soon after a part was used: that part's)
SCREEN_KINDS = ("page", "dialog", "panel", "menu", "region")  # (what an entity on screen is: not one of a chat's elements)
_LEVELS = {GLib.LogLevelFlags.LEVEL_ERROR: "ERROR", GLib.LogLevelFlags.LEVEL_CRITICAL: "CRITICAL",
           GLib.LogLevelFlags.LEVEL_WARNING: "WARNING"}
_INDEX = {}
STATE = {"app": None, "crumbs": [], "used": None, "screen": None, "lock": threading.RLock()}
# (screen and crumbs are kept by the main thread as they change; context() only reads them, from any thread, and
# never calls GTK: inside GLib's log writer one more GLib message aborts the process)


def mark(widget, entity):
    """widget is entity's (a schema/ui entry's name, "HeaderBar.region"), where no class of it names it."""
    widget.ui_entity = entity
    return widget


def index():
    """schema/ui read once: {"classes": {module.Class: [entities]}, "copy": {copy key: [(entity, part)]},
    "text": {literal text: [(entity, part)]}, "kinds": {entity: its kind}}."""
    if _INDEX:
        return _INDEX
    classes, copies, texts, kinds = {}, {}, {}, {}

    def parts(entity, items):
        for p in items or []:
            if not isinstance(p, dict) or not isinstance(p.get("name"), str):
                continue
            for key in p.get("copy") or []:
                copies.setdefault(key, []).append((entity, p["name"]))
            for t in p.get("text") or [] if isinstance(p.get("text"), list) else [p.get("text")]:
                if isinstance(t, str) and t:
                    texts.setdefault(t, []).append((entity, p["name"]))
            parts(entity, p.get("parts"))
    for path in sorted(glob.glob(os.path.join(wire.SCHEMA_DIR, "ui", "*.json"))):
        entity = os.path.basename(path)[:-5]
        try:
            with open(path, encoding="utf-8") as f:
                entry = json.load(f)
        except (OSError, ValueError):
            continue
        kinds[entity] = entity.rsplit(".", 1)[-1]
        for ref in entry.get("code") or []:
            file, _, name = ref.partition(":")
            if file.endswith(".py") and name and "." not in name and name[:1].isupper():
                classes.setdefault(f"{file[:-3].replace('/', '.')}.{name}", []).append(entity)
        parts(entity, entry.get("parts"))
    _INDEX.update(classes=classes, copy=copies, text=texts, kinds=kinds)
    return _INDEX


def _class_entities(widget):
    if (e := getattr(widget, "ui_entity", None)) is not None:
        return [e]
    classes = index()["classes"]
    for cls in type(widget).__mro__:
        mod = cls.__module__
        if mod.startswith("gi."):
            break
        if mod == "__main__":
            mod = os.path.splitext(os.path.basename(sys.argv[0] or ""))[0]
        if (got := classes.get(f"{mod}.{cls.__qualname__}")) is not None:
            return got
    return []


def chain(widget):
    """The entities widget is in, the nearest first: each widget holding it (a popover's, the widget it's on) whose
    class an entry names, one an entry of its own and of others (Composer's, in Meta) taken as the one it's in."""
    found = []
    w = widget
    while w is not None:
        if got := _class_entities(w):
            found.append(got)
        w = w.get_parent()
    out = []
    for i, these in enumerate(found):
        if len(these) == 1:
            pick = these[0]
        else:
            outer = {e for later in found[i + 1:] for e in later}
            name = type(widget).__name__
            pick = next((e for e in these if e in outer), None) or next(
                (e for e in these if e.split(".")[0] == name), these[0])
        if pick not in out:
            out.append(pick)
    return out


def _texts(widget):
    """What widget shows that its copy would say: its label, title or text, its tooltip, its labels'."""
    out = []
    for getter in ("get_label", "get_title", "get_tooltip_text"):
        fn = getattr(widget, getter, None)
        if fn is not None:
            try:
                v = fn()
            except TypeError:
                continue
            if isinstance(v, str) and v:
                out.append(v)
    if GObject.type_name(widget.__gtype__) == "GtkModelButton":
        out.append(widget.props.text or "")
    child = widget.get_first_child()
    seen = 0
    while child is not None and seen < 4:
        if isinstance(child, (Gtk.Label, Adw.ButtonContent)):
            out.append(child.get_label() or "")
        elif isinstance(child, Gtk.Box):
            out += [c.get_label() or "" for c in _children(child) if isinstance(c, Gtk.Label)]
        child, seen = child.get_next_sibling(), seen + 1
    return [t for t in out if t]


def _children(w):
    c = w.get_first_child()
    while c is not None:
        yield c
        c = c.get_next_sibling()


def part_of(widget, entities=None):
    """(entity, part, widget) widget is: its entity and the part of it whose copy says what it shows; where none
    does, the nearest entity, no part, and what tells it apart (its copy key, its icon's name, its kind)."""
    entities = chain(widget) if entities is None else entities
    idx, key = index(), None
    for text in _texts(widget):
        k = wording.key_of(text.replace("_", "", 1) if "_" in text else text) or wording.key_of(text)
        candidates = idx["copy"].get(k, []) if k else idx["text"].get(text, [])
        key = key or k
        if candidates:
            pick = next((c for e in entities for c in candidates if c[0] == e), candidates[0])
            return pick[0], pick[1], None
    icon = getattr(widget, "get_icon_name", lambda: None)() if not isinstance(widget, Gtk.Image) else None
    hint = key or icon or GObject.type_name(widget.__gtype__)
    return (entities[0] if entities else None), None, hint


def screen(win=None):
    """The schema/ui entity on screen (a page, dialog, panel or region: SCREEN_KINDS): in the active window, its
    dialog's page if one's open, else where its focus is, else its tab in front."""
    if win is None:
        app = Gio.Application.get_default()
        win = app.get_active_window() if isinstance(app, Gtk.Application) else None
    if win is None:
        return None
    kinds = index()["kinds"]
    looks = []
    dialog = win.get_visible_dialog() if isinstance(win, Adw.ApplicationWindow) else None
    if dialog is not None:
        page = dialog.get_visible_page() if isinstance(dialog, Adw.PreferencesDialog) else None
        looks += [page, dialog] if page is not None else [dialog]
    else:
        looks.append(win.get_focus())
        tabs = getattr(win, "tabs", None)
        if tabs is not None and hasattr(tabs, "current"):
            looks.append(tabs.current())
        looks.append(win)
    for w in looks:
        if w is None:
            continue
        for e in chain(w):
            if kinds.get(e) in SCREEN_KINDS:
                return e
    return None


def refresh(*_):
    """(main thread) What's on screen looked at again and kept for context()."""
    try:
        shown = screen()
    except Exception:  # noqa: BLE001 (kept as it was)
        return
    if shown is not None:  # (none while no window of this app is active: where it was stays)
        with STATE["lock"]:
            STATE["screen"] = shown


def crumb(action, entity, part, hint=None, at=None):
    """An action kept among the last CRUMBS_MAX (screen opened first, if it's changed). Main thread."""
    now = time.time() if at is None else at
    shown = screen()
    with STATE["lock"]:
        crumbs = STATE["crumbs"]
        if shown is not None and shown != STATE["screen"]:
            crumbs.append({"at": round(now, 3), "action": "open", "screen": shown, "part": None, "widget": None})
        STATE["screen"] = shown or STATE["screen"]
        if action != "open":
            crumbs.append({"at": round(now, 3), "action": action, "screen": entity, "part": part, "widget": hint})
            STATE["used"] = (now, entity, part)
        del crumbs[:-CRUMBS_MAX]


def context():
    """What goes with an error: what's on screen, the part just used, the last actions. Any thread; no GTK."""
    with STATE["lock"]:
        used = STATE["used"]
        recent = used is not None and time.time() - used[0] <= PART_RECENT_S
        return {"screen": STATE["screen"], "part": used[2] if recent else None,
                "crumbs": [dict(c) for c in STATE["crumbs"]]}


def _dialog(win, *_):
    dialog = win.get_visible_dialog()
    if isinstance(dialog, Adw.PreferencesDialog) and not getattr(dialog, "_tracked", False):
        dialog._tracked = True  # (its pages: a page shown is the screen)
        dialog.connect("notify::visible-page", refresh)
    refresh()


def _watch(win):
    """win's focus, dialog (and its pages) and tab changes looked at as they happen."""
    for prop in ("focus-widget", "is-active"):
        if win.find_property(prop) is not None:
            win.connect(f"notify::{prop}", refresh)
    if isinstance(win, Adw.ApplicationWindow):
        win.connect("notify::visible-dialog", _dialog)
    view = getattr(getattr(win, "tabs", None), "view", None)
    if view is not None:
        view.connect("notify::selected-page", refresh)
    GLib.idle_add(refresh)


def _watching():
    app = Gio.Application.get_default()
    if not isinstance(app, Gtk.Application):
        return
    for win in app.get_windows():
        _watch(win)
    app.connect("window-added", lambda _app, win: _watch(win))


# ---- hooks
def _used(action, row=False):
    def hook(widget, *args):
        try:
            if row:  # (a list's: the row activated)
                widget = args[0]
            if not _texts(widget) and isinstance(widget.get_parent(), (Gtk.MenuButton, Adw.SplitButton)):
                widget = widget.get_parent()  # (a menu button's own button: the menu button, as it's named)
            entity, part, hint = part_of(widget)
            crumb(action, entity, part, hint)
        except Exception:  # noqa: BLE001 (a click is never held up by what's kept of it)
            errorbook.caught("callback")
        return True
    return hook


def _hook(cls, signal, fn, make=None):
    """fn on every emission of cls's signal (its class made first, so its signals are there)."""
    if make is not None:
        make()
    gtype = cls if isinstance(cls, GObject.GType) else cls.__gtype__
    GObject.add_emission_hook(gtype.pytype or cls, signal, fn)


def _text(field):
    if not field.value:
        return ""
    raw = ctypes.string_at(field.value) if field.length < 0 else ctypes.string_at(field.value, field.length)
    return raw.decode("utf-8", "replace")


def _writer(level, fields, _n, user_data):
    """GLib's and GTK's log: criticals and warnings kept as errors, everything written as GLib would."""
    for flag, name in _LEVELS.items():
        if level & flag:
            try:
                got = {f.key: f for f in fields if f.key in ("GLIB_DOMAIN", "MESSAGE")}
                domain = _text(got["GLIB_DOMAIN"]) if "GLIB_DOMAIN" in got else "GLib"
                message = _text(got["MESSAGE"]) if "MESSAGE" in got else ""
                errorbook.note("log", f"{domain}-{name}", message)
            except Exception:  # noqa: BLE001 (written all the same)
                pass
            break
    return GLib.log_writer_default(level, fields, user_data)


def install(app_name, wake):
    """This process's errors kept as app_name's (told by wake, from any thread), with what's on screen and the
    user's last actions; what's used looked at as it's used. Once a process."""
    if STATE["app"] is not None:
        return
    STATE["app"] = app_name
    errorbook.install()
    errorbook.BOOK.claim(app_name, wake, context, in_loop=lambda: GLib.main_depth() > 0)
    try:
        GLib.log_set_writer_func(_writer, None)
    except Exception:  # noqa: BLE001 (GLib's own writer kept: criticals not counted)
        errorbook.caught()
    _watching()
    _hook(Gtk.Button, "clicked", _used("click"), Gtk.Button)
    _hook(Gtk.ListBox, "row-activated", _used("click", row=True), Gtk.ListBox)
    _hook(Gtk.Switch, "activate", _used("click"), Gtk.Switch)
    menu = Gio.Menu()
    menu.append("·", "app.none")
    Gtk.PopoverMenu.new_from_model(menu)  # (GtkModelButton, a menu's item, made: its type's there from now)
    _hook(GObject.type_from_name("GtkModelButton"), "clicked", _used("menu"))


def alone(app_name):
    """install for a process that tells its host nothing else (Emission's): its errors told to this machine's host
    in a moment as each comes, on a thread of their own (the host there, or not: kept for the next)."""
    import threading
    due = [False]

    def tell():
        due[0] = False
        errors = errorbook.BOOK.take()
        if not errors:
            return
        try:
            from baudelib import selfupdate
            if "commit" not in STATE:
                STATE["commit"] = selfupdate.git(selfupdate.HERE, "rev-parse", "HEAD", timeout=5)
            ch, _ = wire.reach({"via": "local"}, timeout=5)
            try:
                ch.sock.settimeout(5)
                ch.send({"op": "telemetry", "req": 1, "app": app_name, "pid": os.getpid(), "errors": errors,
                         "commit": (STATE["commit"] or "")[:12] or None, "wire": wire.VERSION})
                if next((m for m in ch if m.get("req") == 1), {}).get("op") != "telemetry_kept":
                    errorbook.BOOK.give_back(errors)
            finally:
                ch.close()
        except (OSError, ValueError):
            errorbook.BOOK.give_back(errors)

    def wake():
        if not due[0]:
            due[0] = True
            GLib.timeout_add(1000, lambda: threading.Thread(target=tell, daemon=True, name="errors").start() and False)
    install(app_name, wake)
