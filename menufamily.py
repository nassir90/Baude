"""☰'s views and programs on a host (Baude's and Record's): Kubernetes, Docker, Databases, Terminal and Browser, each a
row (FamilyRow) opening it on the host picked beside New session, or, from its caret, on another. Browser on this
machine asks its host for Emission's window on its own screen (browser_open: emissionlink.open_window), brought to the
front or started; on another host, that host's Emission streamed into a window here (browserpane.browser_window); what
went wrong said in a toast."""
import threading

from gi.repository import Adw, Gio, GLib, Gtk

from baudelib import wire, wording


def ask_host(where, msg, done):
    """msg (a request) to the host process at where, on a connection of its own: done(reply) on the main thread, an
    error's as {"error": why}."""
    def run():
        from baudelib import hosts
        try:
            if where.get("via") == "local" and not hosts.start_here():
                raise OSError(wording.say("host_status.none_started", log=hosts.log_path()))
            ch, _info = wire.reach(where, timeout=10)
        except (OSError, ValueError) as e:
            return GLib.idle_add(lambda: done({"error": str(e) or wording.say("host_status.no_answer")}) and False)
        reply = None
        try:
            ch.sock.settimeout(wire.SILENT_S)
            ch.send({**msg, "req": 1})
            for m in ch:
                if m.get("req") == 1:
                    reply = {"error": wire.said(m) or wording.say("host_status.no_answer")} if m.get("op") == "error" \
                        else m
                    break
        except OSError:
            pass
        finally:
            ch.close()
        got = reply or {"error": wording.say("host_status.connection_dropped")}
        GLib.idle_add(lambda: done(got) and False)
    threading.Thread(target=run, daemon=True, name="menu-family").start()


UNSET = object()


def open_browser(win, remote=UNSET):
    """☰ → Browser on a host (remote: another's Remote, None this machine; the one picked beside New session if not
    given). On this machine's own host, Emission's window here brought to the front, else started (browser_open);
    on another, that host's Emission streamed into a window here, as a Bot's browser is watched
    (browserpane.browser_window). What went wrong said in a toast; a click while the last is asked does nothing."""
    if remote is UNSET:
        remote = win.view_remote()
    if remote is not None and not remote.mine:
        import browserpane
        from sessionlist import remote_name
        return browserpane.browser_window(win, remote, remote_name(remote))
    if getattr(win, "browser_asking", False):
        return None

    def done(reply):
        win.browser_asking = False
        if reply.get("error"):
            toasts = getattr(win, "toasts", None)
            if isinstance(toasts, Adw.ToastOverlay):
                toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(reply["error"]), timeout=4))
    win.browser_asking = True
    ask_host({"via": "local"}, {"op": "browser_open"}, done)
    return None


# ---- the family's rows in ☰: each a split, as New session's is: its name opens it on the host picked beside New
# session; its caret, New session's own host dropdown (pickers.ChoiceDropDown, its hosts and greyed ones as there),
# opens it on the host picked in it

FAMILY = (("kube", "window.kubernetes"), ("docker", "window.docker"), ("db", "window.database_schemas"),
          ("terminal", "multiplex.terminal_menu"), ("browser", "bot_browser.title"))


def hosts(win):
    """(choices [(key, name)], the active host's key, the keys greyed): New session's hosts (Baude's), or the notebooks'
    host dropdown's (Record's), as each lists them."""
    if hasattr(win, "sidebar"):
        side = win.sidebar
        return side.new_hosts(), side.shown_host(), {k for k, _name, note in side.known_hosts() if note == "gone"}
    from recordstore import _NETWORK, HERE_HOST, host_key, network
    return win.note_hosts(), win.store.picked or HERE_HOST, {
        host_key(r) for r in (network().remotes() if _NETWORK else ())
        if r.state == "dead" and not r.deactivated and not r.mine}


def remote_of(win, key):
    """The Remote a host's key names (None: this machine), or False: not known."""
    from sessionlist import HERE_HOST, host_key, network
    if key == HERE_HOST:
        return None
    if hasattr(win, "host_remote"):  # (Record's: its own network, once it's looked)
        return win.host_remote(key) or False
    return next((r for r in network().remotes() if host_key(r) == key), False)


def open_family(win, family, key=None):
    """family (FAMILY's key) opened on the host key names; None, or the active host's: the usual way, on the active
    host. The views read their host as they open (win.terminal_host, view_remote): win.family_host says it then."""
    if key is not None and key == hosts(win)[1]:
        key = None
    if key is None:
        if family == "browser":
            return open_browser(win)
        return {"kube": win.kube, "docker": win.docker, "db": win.schema, "terminal": win.terminal}[family].open()
    from sessionlist import THIS_MACHINE, remote_name
    r = remote_of(win, key)
    if r is False:
        name = dict(hosts(win)[0]).get(key, key)
        toasts = getattr(win, "toasts", None)
        if isinstance(toasts, Adw.ToastOverlay):
            toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(wording.say("hosts.looking", host=name)), timeout=3))
        return None
    if family == "browser":
        return open_browser(win, r)
    where, name = ({"via": "local"}, THIS_MACHINE) if r is None else (r.where, remote_name(r))
    if family == "terminal":
        return win.terminal.open(where, name)
    win.family_host = key
    try:
        return {"kube": win.kube, "docker": win.docker, "db": win.schema}[family].open()
    finally:
        win.family_host = None


class FamilyRow(Gtk.Box):
    """One of ☰'s family rows: its name, a flat button opening it on the active host; at its end New session's host
    dropdown, its button a caret, opening it on the host picked there. Its window held weakly."""

    def __init__(self, win, popover, family, label):
        super().__init__(css_classes=["baude-family-row"])
        import weakref
        from pickers import ChoiceDropDown
        self.win, self.popover, self.family = weakref.ref(win), popover, family
        main = Gtk.Button(child=Gtk.Label(label=label, xalign=0), hexpand=True, css_classes=["flat", "baude-family-main"])
        main.connect("clicked", FamilyRow._clicked)
        self.append(main)
        self.hosts = ChoiceDropDown([], None, self.picked)
        self.hosts.add_css_class("flat")
        self.hosts.add_css_class("baude-family-hosts")
        caret = Gtk.SignalListItemFactory()  # (its button: the caret alone; its list New session's rows)
        caret.connect("setup", lambda _f, item: item.set_child(Gtk.Box()))
        self.hosts.set_factory(caret)
        self.hosts.set_dimmed(set())
        self.hosts.pick_again()  # (the host ticked, picked: opened there too, the menu closed)
        self.hosts.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.append(self.hosts)

    @staticmethod
    def _clicked(button):
        self = button.get_parent()
        self.popover.popdown()
        if (win := self.win()) is not None:
            open_family(win, self.family)

    def fill(self):
        """Its hosts as New session lists them now, the active one ticked."""
        if (win := self.win()) is None:
            return
        choices, active, dimmed = hosts(win)
        self.hosts.set_choices(choices)
        self.hosts.select(active)
        self.hosts.set_dimmed(dimmed)
        (self.hosts.add_css_class if len(choices) == 1 else self.hosts.remove_css_class)("one-provider")  # (as New
        # session's: dimmed, no other host to pick)

    def picked(self, key):
        """A host picked in its caret's list (the one ticked too): it opened there, the menu closed (its tick put back
        on the active host as it next opens: fill)."""
        self.popover.popdown()
        if (win := self.win()) is not None:
            GLib.idle_add(lambda: open_family(win, self.family, key) and False)


def attach(win, menu_button, section):
    """The family's rows (FamilyRow) put in section (a Gio.Menu of menu_button's model), in its popover; filled with
    the hosts each time it opens. The rows, by family."""
    popover = menu_button.get_popover()
    rows = {}
    for family, copy in FAMILY:
        item = Gio.MenuItem()
        item.set_attribute_value("custom", GLib.Variant.new_string(f"family-{family}"))
        section.append_item(item)
        rows[family] = FamilyRow(win, popover, family, wording.say(copy))
        popover.add_child(rows[family], f"family-{family}")
    popover.connect("show", lambda *_: [row.fill() for row in rows.values()])
    return rows
