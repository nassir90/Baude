"""meshui — the mesh in a window: GitHub's lists watched while it's shown (watch_github), a banner while an app
and its host process run different code (HostApart), and ☰ → Mesh: the machines, their versions, a machine
renamed or removed (mesh_dialog).
"""
import weakref
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402,F401

import lookupgit  # noqa: E402
from baudelib import wording  # noqa: E402
from widgets import pretty_path, settings_page  # noqa: E402
from pickers import RefPalette  # noqa: E402


# ---- GitHub's pull requests and issues (lookupgit.GITHUB), kept up with while a window's shown
GITHUB_TICK_S = 5


def window_shown(win):
    """Whether a window's on show, as far as can be told: on the screen, not minimised or hidden from it."""
    surface = win.get_surface() if win.get_mapped() else None
    return surface is not None and not (isinstance(surface, Gdk.Toplevel) and surface.get_state() & (
        Gdk.ToplevelState.MINIMIZED | Gdk.ToplevelState.SUSPENDED))


def watch_github(win, folders, changed=None):
    """While win's shown: GitHub's lists of the folders in use (folders(win): a list, lookupgit.GitHub.look)
    looked at when it's time, or the folders are others, and once they've changed, what's on show drawn afresh: ⌘K if
    it's open and its first row is what's picked (changed(win), the rest). Where the pull requests of those folders'
    repositories stand (lookupgit.PULLS, once something's shown them) likewise. folders and changed are plain functions,
    as this holds nothing of the window: it goes when the window does."""
    window, seen, looked, asked = weakref.ref(win), [lookupgit.GITHUB.generation], [-lookupgit.GITHUB_LOOK_S], [None]
    pulls = [lookupgit.PULLS.generation]

    def tick():
        win = window()
        if win is None:
            return False
        if not window_shown(win):
            return True
        now, in_use = time.monotonic(), folders(win)
        if now - looked[0] >= lookupgit.GITHUB_LOOK_S or in_use != asked[0]:
            looked[0], asked[0] = now, in_use
            lookupgit.GITHUB.look(in_use)
        lookupgit.PULLS.look(lookupgit.GITHUB.own_repository(f) for f in in_use)
        if seen[0] != lookupgit.GITHUB.generation or pulls[0] != lookupgit.PULLS.generation:
            seen[0], pulls[0] = lookupgit.GITHUB.generation, lookupgit.PULLS.generation
            dialog = win.get_visible_dialog()
            if isinstance(dialog, RefPalette) and dialog.selection.get_selected() in (0, Gtk.INVALID_LIST_POSITION):
                dialog.fill()
            if changed is not None:
                changed(win)
        return True
    GLib.timeout_add_seconds(GITHUB_TICK_S, tick)


class HostApart:
    """An app (Baude, Record) and this machine's host process on different code (baudelib.client.code_apart), for
    the app's windows to say in a banner that stays till it's dealt with, with the restart that puts them on the same
    code. One for the app: look, as the hosts change, at the host process (by its pid and code, once each; again
    if git couldn't tell); said (changed: every window's banner as it is now) once it's lasted APART_S (an update the
    other picks up in its next look at the checkout isn't), or at once for another checkout's code."""

    def __init__(self, app, started, changed):
        self.app, self.started, self.changed = app, started, changed
        self.looking = self.looked = self.said = None

    @staticmethod
    def key(r):
        return r.info.get("pid"), r.info.get("commit"), r.info.get("version")

    def look(self, r):
        if r is None or self.key(r) in (self.looked, self.looking):
            return
        key, info = self.key(r), dict(r.info)
        self.looking = key
        import baudelib.client

        def work():
            try:
                apart, ok = baudelib.client.code_apart(info, self.started), True
            except Exception:  # noqa: BLE001 (git gone wrong: looked at again as the hosts next change)
                apart, ok = None, False
            GLib.idle_add(lambda: self.looked_at(key, info, apart, ok) and False)
        threading.Thread(target=work, daemon=True, name="code-apart").start()

    def looked_at(self, key, info, apart, ok):
        import baudelib.client
        if self.looking == key:
            self.looking = None
        if not ok:
            return
        self.looked = key
        if apart is not None:
            GLib.timeout_add_seconds(0 if apart == "elsewhere" else baudelib.client.APART_S,
                                     lambda: self.say(key, info, apart) and False)

    def say(self, key, info, apart):
        if self.looked == key:  # (not if it's restarted, or another's come, meanwhile)
            self.said = {"key": key, "apart": apart, "checkout": info.get("checkout"), "done": False}
            self.changed()

    def now(self, r, name, app_waits):
        """(apart, title, button label) to say now, or None: nothing, or what an Update bar says already (the app's
        new version waiting, app_waits; the host process's, its info's update)."""
        said = self.said
        if said is None or said["done"] or r is None or self.key(r) != said["key"]:
            return None
        apart, host_waits = said["apart"], bool(r.info.get("update"))
        if apart == "both" and (app_waits or host_waits):
            apart = "host" if app_waits else "head"
        if apart == "head" and app_waits or apart == "host" and host_waits:
            return None
        folder = said["checkout"]
        return apart, *{
            "head": (wording.say("code_apart.app_older", app=name), wording.say("code_apart.restart_app", app=name)),
            "host": (wording.say("code_apart.daemon_older", app=name), wording.say("code_apart.restart_daemon")),
            "both": (wording.say("code_apart.both_old", app=name), wording.say("code_apart.restart_both")),
            "elsewhere": (wording.say("code_apart.elsewhere_folder", folder=pretty_path(folder)) if folder else
                          wording.say("code_apart.elsewhere"), wording.say("code_apart.dismiss"))}[apart]

    def act(self, apart, restart_app, restart_host):
        """Its banner's button, on what it said (now's apart): the restart (or, for another checkout's, nothing), and
        no more said of it."""
        said = self.said
        if said is None or said["done"]:
            return
        said["done"] = True
        self.changed()
        if apart == "head":
            restart_app()
        elif apart == "host":
            restart_host()
        elif apart == "both":
            restart_host(then=restart_app)


# ---- ☰ → Mesh: each machine in the mesh and each host known here, the code its daemon and the apps on it run

VERSION_CLIENTS = {"baude": "baude", "record": "record", "emission": "emission", "baude-android": "android"}


def mesh_machines(net):
    """☰ → Mesh's machines: (newest: (commit, made) or None, [machine]), or None with none known. They're each host in
    the mesh as the host process here has it (or, with none here, the first host reached that has a mesh), that one
    first; then each host known here it doesn't list (every one New session's dropdown has), by name; then those the
    mesh reached lately; none removed here. A machine: {id, name (as it's called here), key (its Remote's, for Rename
    and Remove; None for this machine, or one not known here), phone (a phone: its code is the Android app's, it has no
    daemon), daemon: {commit, made, behind}, apps: [(client, {commit, made, behind})] (those on that machine connected
    to it), down: what's said of one not reached now (greyed), else None}."""
    import baudelib.client
    remotes = net.remotes()
    with net.lock:
        removed = set(net.removed)
    reached = [r for r in remotes if r.state == "alive" and isinstance(r.info.get("mesh"), dict)]
    source = next((r for r in reached if r.mine), None) or next(iter(reached), None)
    by_id = {}
    for r in remotes:
        by_id.setdefault(r.info.get("id") or r.key, r)
    out, seen = [], set()

    def code(x):
        return {"commit": x.get("commit"), "made": x.get("made"), "behind": x.get("behind")}

    def add(hid, info, r, down=None):
        seen.update({hid, *([r.key] if r is not None else [])})
        here = r is not None and (r.mine or baudelib.client.is_here(r.info))
        name = r.label if r is not None else info.get("name") or hid[:8]
        if r is not None and r.deactivated:
            down = wording.say("hosts.deactivated", host=name)
        out.append({"id": hid, "name": name, "key": None if r is None or here else r.key, "down": down,
                    "phone": info.get("dial") is False or r is not None and r.phone,
                    "daemon": code(info), "apps": [(h.get("client") or "", code(h)) for h in info.get("heads") or ()]})

    def down(r, listed):
        """What's said of a host not reached from here now: one the mesh lists, once it's gone (as New session's
        dropdown greys it); one it doesn't, till it's reached."""
        return None if r is None or r.state == "alive" or r.state == "connecting" and listed else \
            wording.say("hosts.looking" if r.state == "connecting" else "hosts.down", host=r.label)
    mesh = source.info["mesh"] if source is not None else {}
    for h in [source.info, *(mesh.get("members") or [])] if source is not None else ():
        if (hid := h.get("id")) and hid not in seen and hid not in removed:
            add(hid, h, r := by_id.get(hid), down(r, True))
    for r in sorted(remotes, key=lambda r: r.label.lower()):
        if (hid := r.info.get("id") or r.key) not in seen and r.key not in seen:
            add(hid, r.info, r, down(r, False))
    for g in mesh.get("gone") or ():
        if (hid := g.get("id")) and hid not in seen and hid not in removed:
            add(hid, g, None, wording.say("hosts.down", host=g.get("name") or hid[:8]))
    if not out:
        return None
    newest = mesh.get("newest")
    made = next((c.get("made") for m in out if not m["down"] for c in [m["daemon"], *(c for _, c in m["apps"])]
                 if c.get("commit") == newest and c.get("made")), None) if newest else None
    return ((newest, made) if newest else None), out


def version_standing(entry, newest):
    """What a daemon's or an app's code is against the newest: ("newest" | "behind" | "older" | "unknown" | None,
    what's said of it)."""
    if not entry.get("commit"):
        return "unknown", ""
    if newest is None:
        return None, ""
    if entry["commit"] == newest[0]:
        return "newest", wording.say("mesh_versions.newest")
    if isinstance(entry.get("behind"), int) and entry["behind"] > 0:
        return "behind", wording.say("mesh_versions.behind", n=entry["behind"])
    if entry.get("made") and newest[1] and entry["made"] < newest[1]:
        return "older", wording.say("mesh_versions.older")
    return None, ""


def mesh_dialog(net):
    """☰ → Mesh (Baude's and Record's): a group for each machine (mesh_machines), its daemon and each app on it a line
    each (version_row; a phone's, its app's), greyed with what's in the way for one not reached now; and its ⋮, to
    rename it here or remove it (another machine's). Looked at again every MESH_VERSIONS_S while it's shown (what the
    hosts say comes as it changes: a host's record, a head come or gone)."""
    view = Adw.ToolbarView()
    view.add_top_bar(Adw.HeaderBar())
    dialog = Adw.Dialog(title=wording.say("mesh_versions.title"), content_width=640, content_height=720, child=view)
    shown = {"got": object(), "open": True}

    def remote(key):
        return next((r for r in net.remotes() if r.key == key), None)

    actions = Gio.SimpleActionGroup()
    for name, ask in (("rename", mesh_rename), ("remove", mesh_remove)):
        act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
        act.connect("activate", lambda _a, key, ask=ask: (r := remote(key.get_string())) is not None and
                    ask(dialog, net, r, fill))
        actions.add_action(act)
    dialog.insert_action_group("mesh", actions)

    def menu(key):
        items = Gio.Menu()
        for label, action in ((wording.say("common.rename_ellipsis"), "mesh.rename"),
                              (wording.say("common.remove_ellipsis"), "mesh.remove")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(action, GLib.Variant.new_string(key))
            items.append_item(item)
        return Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=items, valign=Gtk.Align.CENTER,
                              css_classes=["flat"], tooltip_text=wording.say("chat_header.menu"))

    def fill():
        got = mesh_machines(net)
        if got == shown["got"]:
            return
        shown["got"] = got
        prefs = settings_page()
        if got is None:
            prefs.add(Adw.PreferencesGroup(description=wording.say("mesh_versions.none")))
        else:
            newest, machines = got
            for m in machines:
                group = Adw.PreferencesGroup(title=GLib.markup_escape_text(m["name"]))
                if m["key"] is not None:
                    group.set_header_suffix(menu(m["key"]))
                if m["down"]:
                    group.set_description(GLib.markup_escape_text(m["down"]))
                    group.add_css_class("dim-label")  # (out of reach: greyed, as New session's dropdown has it)
                elif m["phone"]:
                    group.add(version_row(wording.say("mesh_versions.android"), m["daemon"], newest))
                else:
                    group.add(version_row(wording.say("host_menu.daemon"), m["daemon"], newest))
                    for client, x in m["apps"]:
                        known = VERSION_CLIENTS.get(client)
                        group.add(version_row(wording.say(f"mesh_versions.{known}") if known else client, x, newest))
                prefs.add(group)
        view.set_content(prefs)

    def look():
        if shown["open"]:
            fill()
        return shown["open"]
    dialog.connect("closed", lambda _d: shown.update(open=False))
    fill()
    GLib.timeout_add_seconds(MESH_VERSIONS_S, look)
    return dialog


def mesh_rename(parent, net, r, then=None):
    """A host's ⋮ → Rename… on ☰ → Mesh: the name it has here (Network.rename; empty, its own again)."""
    own = r.info.get("name") or r.host
    entry = Gtk.Entry(text=r.alias or "", activates_default=True, width_chars=30, placeholder_text=own)
    dlg = Adw.AlertDialog(heading=wording.say("mesh_versions.rename_heading", host=r.label), extra_child=entry,
                          body=wording.say("mesh_versions.rename_body", name=own), default_response="rename",
                          close_response="cancel")
    dlg.add_response("cancel", wording.say("common.cancel"))
    dlg.add_response("rename", wording.say("common.rename"))
    dlg.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)

    def done(_d, res):
        if res == "rename":
            net.rename(r, entry.get_text())
            then and then()
    dlg.connect("response", done)
    dlg.present(parent)
    entry.grab_focus()
    return dlg


def mesh_remove(parent, net, r, then=None):
    """A host's ⋮ → Remove… on ☰ → Mesh: asked, then removed here (Network.remove)."""
    dlg = Adw.AlertDialog(heading=wording.say("mesh_versions.remove_heading", host=r.label),
                          body=wording.say("mesh_versions.remove_body"), default_response="cancel",
                          close_response="cancel")
    dlg.add_response("cancel", wording.say("common.cancel"))
    dlg.add_response("remove", wording.say("common.remove"))
    dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

    def done(_d, res):
        if res == "remove":
            net.remove(r)
            then and then()
    dlg.connect("response", done)
    dlg.present(parent)
    return dlg


MESH_VERSIONS_S = 2


def changelog_of(commit, widget):
    """The commits commit has, in ☰ → Changelog's dialog, over widget."""
    import update
    update.changelog_dialog(commit).present(widget)


def version_row(title, entry, newest):
    """A daemon's or an app's line on ☰ → Mesh, one line tall: what it is, its commit (a link to that commit's
    changelog, ☰ → Changelog's), and where that stands (Newest, dimmed; N commits behind or Older code, in the
    warning colour)."""
    row = Gtk.ListBoxRow(activatable=False, selectable=False)
    box = Gtk.Box(spacing=12, margin_top=5, margin_bottom=5, margin_start=12, margin_end=12)
    box.append(Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
    kind, said = version_standing(entry, newest)
    commit = entry.get("commit")
    if commit:
        link = Gtk.Label(label=commit[:7], css_classes=["monospace", "accent"], cursor=Gdk.Cursor.new_from_name("pointer"))
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: changelog_of(commit, link))
        link.add_controller(click)
        box.append(link)
    else:
        box.append(Gtk.Label(label=wording.say("updates.version_unknown"), css_classes=["dim-label"]))
    behind = Gtk.Label(label=said, xalign=1, width_chars=16)
    behind.add_css_class("dim-label" if kind == "newest" else "warning")
    box.append(behind)
    row.set_child(box)
    return row
