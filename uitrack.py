"""What the user does in Baude and Record, named as schema/ui names it: the index is the spec itself. Each widget is
the entity of the nearest widget holding it whose class an entry's code names (`file.py:Class`, or one marked with
mark()), and a button, row or menu item used is the part of that entity whose copy says what it shows (its label or
tooltip, by schema/copy's key). What's on screen (screen()) and the user's last few actions (crumbs) go with each
error this process has (baudelib.errorbook), as do GLib's and GTK's criticals and warnings (a log writer) and
exceptions in main-loop callbacks. install(app) is the one call an app makes, before its windows. Nothing here runs
till something's used: a click is looked at as it's made."""
import ctypes
import os
import sys
import threading
import time

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from baudelib import counters, errorbook, wire, wording
from baudelib.uiindex import index

CRUMBS_MAX = 8
PART_RECENT_S = 5  # (an error this soon after a part was used: that part's)
SCREEN_KINDS = ("page", "dialog", "panel", "menu", "region")  # (what an entity on screen is: not one of a chat's elements)
_LEVELS = {GLib.LogLevelFlags.LEVEL_ERROR: "ERROR", GLib.LogLevelFlags.LEVEL_CRITICAL: "CRITICAL",
           GLib.LogLevelFlags.LEVEL_WARNING: "WARNING"}
STATE = {"app": None, "crumbs": [], "used": None, "screen": None, "lock": threading.RLock(),
         "shown": {}, "looking": False}  # (shown: each screen in front now, since when)
# (screen and crumbs are kept by the main thread as they change; context() only reads them, from any thread, and
# never calls GTK: inside GLib's log writer one more GLib message aborts the process)
FRONT_IDLE_S = 300  # (no input for this long: nobody's at it, and no screen's time counted)
HANDLED = ("clicked", "activated", "row-activated", "activate", "toggled")  # (the signals a use runs: their
# handlers kept, to be counted as each is used)
_ACTIONS = {}  # (an action's name: the functions connected to its activate)
_GLIB = {}  # (GLib's own idle_add, timeout_add and timeout_add_seconds, before they were counted)


def mark(widget, entity):
    """widget is entity's (a schema/ui entry's name, "HeaderBar.region"), where no class of it names it."""
    widget.ui_entity = entity
    return widget


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


def screens(win=None):
    """The screens in front: the active window's (screen()), and its side panel's page while it shows one."""
    if win is None:
        app = Gio.Application.get_default()
        win = app.get_active_window() if isinstance(app, Gtk.Application) else None
    if win is None or not win.is_active():
        return set()
    out = {screen(win)} - {None}
    panel = getattr(win, "panel", None)
    stack = getattr(panel, "stack", None)
    if isinstance(stack, (Adw.ViewStack, Gtk.Stack)) and panel.get_mapped() and (page := stack.get_visible_child()):
        kinds = index()["kinds"]
        named = next((e for e in chain(page) if kinds.get(e) == "panel"), None)
        if named is None and isinstance(stack, Adw.ViewStack):
            name = (stack.get_visible_child_name() or "").replace("_", " ").title().replace(" ", "")
            named = f"{name}.panel" if f"{name}.panel" in kinds else None
        if named is not None:
            out.add(named)
    return out


def look(win=None):
    """The screens in front looked at again: one come counted as opened, one gone given its time in front (only
    while someone's at the machine)."""
    import widgets
    now = time.time()
    try:
        here = screens(win) if widgets.user_present(FRONT_IDLE_S) is not False else set()
    except Exception:  # noqa: BLE001 (as windows go: looked at again next time)
        return
    shown = STATE["shown"]
    for gone in set(shown) - here:
        took = now - shown.pop(gone)
        counters.COUNTERS.add("screen", gone, took, count=0)
    for came in here - set(shown):
        shown[came] = now
        counters.COUNTERS.add("screen", came)
    if here and STATE["screen"] not in here:
        crumb("open", None, None)


def look_soon():
    """look once the main loop's free (what a click opened, drawn by then)."""
    if not STATE["looking"]:
        STATE["looking"] = True
        _GLIB["idle_add"](lambda: STATE.update(looking=False) or look() or False)


def counts_now():
    """The screens in front given their time so far (so each minute's row has its own), and what's counted since
    the last time taken."""
    now = time.time()
    for name, since in list(STATE["shown"].items()):
        counters.COUNTERS.add("screen", name, now - since, count=0)
        STATE["shown"][name] = now
    return counters.COUNTERS.take(now)


def context():
    """What goes with an error: what's on screen, the part just used, the last actions. Any thread; no GTK."""
    with STATE["lock"]:
        used = STATE["used"]
        recent = used is not None and time.time() - used[0] <= PART_RECENT_S
        return {"screen": STATE["screen"], "part": used[2] if recent else None,
                "crumbs": [dict(c) for c in STATE["crumbs"]]}


def _watch(win):
    """win's focus, dialog and tab changes looked at as they happen."""
    for prop in ("focus-widget", "visible-dialog", "is-active"):
        if win.find_property(prop) is not None:
            win.connect(f"notify::{prop}", refresh)
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
    GLib.timeout_add_seconds(2, lambda: refresh() or True)  # (a dialog's page, a panel: what has no signal here)


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
            name = f"{entity}/{part}" if part else f"{entity}/({hint})"
            ran, unnamed = handlers(widget, args[0] if row else None)
            calls = _calls_of(unnamed) if unnamed else None
            began = time.perf_counter()

            def done():  # (the main loop free again: its handlers, and what they did at once, done)
                took = time.perf_counter() - began
                if calls is not None:  # (a lambda's: what it called, by name, in its place)
                    sys.setprofile(None)
                    ran.extend(n for n in calls if n not in ran)
                counters.COUNTERS.add(action if action == "menu" else "part", name, took)
                for h in ran:
                    counters.COUNTERS.add("handler", h, took)
                look()
                return False
            _GLIB["idle_add"](done, priority=GLib.PRIORITY_HIGH)
        except Exception:  # noqa: BLE001 (a click is never held up by what's kept of it)
            errorbook.caught("callback")
        return True
    return hook


def handlers(widget, box=None):
    """The functions a use of widget runs (connected to it as HANDLED: a row's, its list's; a menu item's, its
    action's): (the names of those reached by their names, the code of those that aren't: a lambda's, a nested
    function's)."""
    found = list(getattr(widget, "ui_handlers", ()))
    if box is not None:
        found += getattr(box, "ui_handlers", ())
    if GObject.type_name(widget.__gtype__) == "GtkModelButton":
        action = (widget.props.action_name or "").partition(".")[2]
        found += _ACTIONS.get(action, ())
    named = list(dict.fromkeys(n for n, _ in found if "<" not in n))
    unnamed = {getattr(getattr(f, "__func__", f), "__code__", None) for n, f in found if "<" in n} - {None}
    return named, unnamed


def _calls_of(codes):
    """The functions the code in codes calls (a lambda handler's: the first of Baude's own it calls, each), by their
    names, gathered from now till sys.setprofile(None)."""
    called, depth = [], [0]

    def watch(frame, event, _arg):
        if event == "call":
            if frame.f_code in codes:
                depth[0] += 1
            elif depth[0] and frame.f_back is not None and frame.f_back.f_code in codes:
                mod = frame.f_globals.get("__name__", "?")
                name = f"{mod}.{frame.f_code.co_qualname}"
                if "<" not in name and errorbook.ours(frame.f_code.co_filename) and name not in called:
                    called.append(name)
        elif event == "return" and frame.f_code in codes:
            depth[0] -= 1
    sys.setprofile(watch)
    return called


def _keep_handlers():
    """Each function connected to a signal a use runs (HANDLED) kept on what it's connected to, by its name."""
    before = GObject.Object.connect

    def connect(self, signal, fn, *args, **kw):
        if signal in HANDLED and callable(fn):
            try:
                name = counters.callable_name(fn)
                if isinstance(self, Gio.SimpleAction):
                    _ACTIONS.setdefault(self.get_name(), []).append((name, fn))
                else:
                    self.ui_handlers = (*getattr(self, "ui_handlers", ()), (name, fn))
            except Exception:  # noqa: BLE001 (connected all the same)
                pass
        return before(self, signal, fn, *args, **kw)
    GObject.Object.connect = connect


def _timed(fn):
    """fn, its runs counted and timed as a callback's (counters' callable_name)."""
    name = counters.callable_name(fn)

    def run(*args):
        began = time.perf_counter()
        try:
            return fn(*args)
        finally:
            counters.COUNTERS.add("callback", name, time.perf_counter() - began)
    return run


def _count_callbacks():
    """Every main-loop callback this process adds from here on (GLib.idle_add, timeout_add, timeout_add_seconds)
    counted and timed."""
    for fname in ("idle_add", "timeout_add", "timeout_add_seconds"):
        _GLIB[fname] = before = getattr(GLib, fname)

        def counted(*args, _before=before, _fname=fname, **kw):
            args = list(args)
            at = 0 if _fname == "idle_add" else 1
            if len(args) > at and callable(args[at]):
                args[at] = _timed(args[at])
            elif "function" in kw:
                kw["function"] = _timed(kw["function"])
            return _before(*args, **kw)
        setattr(GLib, fname, counted)


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


def prepare():
    """Counting from here on: every main-loop callback, the functions connected to what's used, every program
    started. Before an app's windows are made; once a process."""
    if _GLIB:
        return
    _count_callbacks()
    _keep_handlers()
    counters.count_programs()


def install(app_name, wake):
    """This process's errors kept as app_name's (told by wake, from any thread), with what's on screen and the
    user's last actions; what's used looked at as it's used. Once a process."""
    if STATE["app"] is not None:
        return
    STATE["app"] = app_name
    prepare()
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
    _hook(Adw.Dialog, "closed", lambda *_: look_soon() or True, Adw.Dialog)


def watch(win):
    """win's screens followed: as it comes in front or goes, and as its tabs are switched."""
    win.connect("notify::is-active", lambda *_: look_soon())
    if (tabs := getattr(win, "tabs", None)) is not None and hasattr(tabs, "view"):
        tabs.view.connect("notify::selected-page", lambda *_: look_soon())


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
