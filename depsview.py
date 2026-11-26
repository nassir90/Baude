"""What Baude and Record show of a dependency (baudelib.deps, schema/deps): Missing, the state a feature shows in its
place when one it needs isn't there (☰ → Terminal without VTE, Kubernetes without kubectl on its host, Database Schemas
without Docker, the Git page without git; the phone's MissingDep the same), and Settings → Dependencies (Page: the host
picked beside New session's, as its deps op says them, under what needs them)."""
import threading

from gi.repository import Adw, GLib, Gtk, Pango

from baudelib import deps, wire, wording


class Missing(Gtk.ScrolledWindow):
    """A feature without a dependency: its icon, "Kubernetes needs kubectl", the command that installs it with Copy
    (or, with none for this platform, its install page), and Try Again (a host's: again()) or Restart (the app's own,
    which it loads as it starts)."""

    def __init__(self, again=None):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, hexpand=True)
        from widgets import _scales
        self.again, self.dep = again, None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER,
                      halign=Gtk.Align.CENTER, margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
        box.add_css_class("dep-missing")
        icon = Gtk.Image(icon_name="package-x-generic-symbolic", pixel_size=2 * _scales["icon_px"])
        icon.add_css_class("dim-label")
        box.append(icon)
        self.title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.title.add_css_class("title-3")
        box.append(self.title)
        self.fix = Gtk.Label(selectable=True, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.fix.add_css_class("mono")
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text=wording.say("common.copy"),
                          valign=Gtk.Align.CENTER)
        copy.add_css_class("flat")
        copy.connect("clicked", lambda *_: self.copy())
        self.fix_row = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        self.fix_row.add_css_class("dep-fix")
        self.fix_row.append(self.fix)
        self.fix_row.append(copy)
        box.append(self.fix_row)
        self.link = Gtk.LinkButton(label=wording.say("deps.install_page"), halign=Gtk.Align.CENTER)
        box.append(self.link)
        self.button = Gtk.Button(halign=Gtk.Align.CENTER)
        self.button.add_css_class("pill")
        self.button.connect("clicked", lambda *_: self.pressed())
        box.append(self.button)
        self.set_child(box)

    def show(self, dep, feature):
        """dep (a Dep: deps.info's, or a host's) missing for feature (its copy key: window.kubernetes)."""
        self.dep = dep
        self.title.set_label(deps.title(dep, feature))
        self.fix.set_label(dep.get("fix") or "")
        self.fix_row.set_visible(bool(dep.get("fix")))
        self.link.set_uri(dep.get("link") or "")
        self.link.set_visible(not dep.get("fix") and bool(dep.get("link")))
        own = dep.get("where") == "app"
        self.button.set_label(wording.say("shared_settings.restart") if own else wording.say("common.try_again"))
        self.button.set_visible(own or self.again is not None)

    def copy(self):
        copy_fix(self, {**self.dep, "fix": self.fix.get_label()})

    def pressed(self):
        if self.dep.get("where") == "app":
            root = self.get_root()
            if root is not None:
                root.activate_action("win.restart", None)
        elif self.again is not None:
            self.again()


def ask(where, done, msg=None):
    """The host there's dependencies (its deps op; or msg, another request it answers with one reply of its op):
    done(reply) on the main thread, an error's as {"error": why}."""
    msg = msg or {"op": "deps"}

    def run():
        from baudelib import hosts
        reply = None
        try:
            if where.get("via") == "local" and not hosts.start_here():  # (this machine's: started if need be)
                raise OSError(wording.say("host_status.none_started", log=hosts.log_path()))
            ch, _info = wire.reach(where, timeout=10)
            try:
                ch.sock.settimeout(wire.SILENT_S)
                ch.send({**msg, "req": 1})
                for m in ch:
                    if m.get("req") == 1:
                        reply = m if m.get("op") == msg["op"] else {"error": wire.said(m)}
                        break
            finally:
                ch.close()
        except (OSError, ValueError) as e:
            reply = {"error": str(e) or wording.say("host_status.no_answer")}
        GLib.idle_add(done, reply or {"error": wording.say("host_status.connection_dropped")})
    threading.Thread(target=run, daemon=True, name=msg["op"]).start()


def copy_fix(widget, dep, settings=None):
    """Copy's: the command on the clipboard, and a toast saying what to do next (Restart for the app's own; Try Again on a feature's page; in Settings, which has none, only to run it; settings: the Settings dialog a row in a dialog over it toasts on)."""
    from widgets import toast
    widget.get_clipboard().set(dep["fix"])
    dialog = settings or widget.get_ancestor(Adw.PreferencesDialog)  # (Settings: its toast is over its own page)
    text = wording.say("deps.copied_restart" if dep.get("where") == "app" else "deps.copied" if dialog else "kube.copied_try_again")
    if dialog is not None:
        dialog.add_toast(Adw.Toast(title=text, timeout=2))
    else:
        toast(widget, text)


def row(dep, title=None, settings=None):
    """A dependency's row: its name (or title: "Codex CLI isn't installed", where a feature's page lists it), then what's said of it while it's there (Installed, Signed in, Running, 3 models),
    or the command that installs or readies it with Copy (else its install page; a check with no command, its status only)."""
    out = Adw.ActionRow(title=GLib.markup_escape_text(title or dep["name"]))
    if dep.get("present"):
        out.add_suffix(Gtk.Label(label=dep.get("ready") or wording.say("deps.installed"), css_classes=["dim-label"]))
    elif dep.get("fix"):
        fix = Gtk.Label(label=dep["fix"], selectable=True, ellipsize=Pango.EllipsizeMode.START, css_classes=["mono"])
        out.add_suffix(fix)
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text=wording.say("common.copy"),
                          valign=Gtk.Align.CENTER, css_classes=["flat"])
        copy.connect("clicked", lambda b: copy_fix(b, dep, settings))
        out.add_suffix(copy)
    elif dep.get("ready"):
        out.add_suffix(Gtk.Label(label=dep["ready"], css_classes=["dim-label"]))
    elif dep.get("link") and not dep.get("check"):
        out.add_suffix(Gtk.LinkButton(uri=dep["link"], valign=Gtk.Align.CENTER, label=wording.say("deps.install_page")))
    return out


def grouped(listed):
    """A deps answer's dependencies by what needs them: [(its group's copy key, [Dep])], in the order it lists them."""
    out = {}
    for dep in listed or []:
        out.setdefault(dep.get("group") or "", []).append(dep)
    return list(out.items())


class Page(Adw.PreferencesPage):
    """Settings → Dependencies: the host picked beside New session (as Subscriptions has it), then what its features
    and its machine's apps need and whether it's there, a group for each thing that needs them (Providers, Git and
    GitHub, Kubernetes, …), as its deps op says them."""

    def __init__(self, win):
        super().__init__(title=wording.say("deps.title"), icon_name="package-x-generic-symbolic")
        from widgets import walk
        for clamp in walk(self):  # (as widgets.settings_page: rows as wide as the dialog)
            if isinstance(clamp, Adw.Clamp):
                clamp.set_maximum_size(100_000)
                clamp.set_tightening_threshold(100_000)
        where, host = win.terminal_host()
        top = Adw.PreferencesGroup()
        name = Adw.ActionRow(title=wording.say("new_session.host"))
        name.add_suffix(Gtk.Label(label=host, css_classes=["dim-label"]))
        top.add(name)
        self.add(top)
        self.groups = []  # (the groups shown, once the host's answered)
        ask(where, self.took)

    def took(self, reply):
        for group in self.groups:
            self.remove(group)
        self.groups = []
        if reply.get("error"):
            group = Adw.PreferencesGroup()
            group.add(Adw.ActionRow(title=GLib.markup_escape_text(reply["error"]), sensitive=False))
            self.groups.append(group)
        for key, listed in grouped(deps.for_viewer(reply.get("deps"))):
            group = Adw.PreferencesGroup(title=GLib.markup_escape_text(wording.say(key) if wording.has(key) else key))
            for dep in listed:
                group.add(row(dep))
            self.groups.append(group)
        for group in self.groups:
            self.add(group)
        return False
