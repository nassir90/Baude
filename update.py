"""Baude and Record kept up to date from git, once Settings → Auto restart on update is turned on: it's off until then,
since it runs whatever the upstream has (and off with BAUDE_UPDATE=0 whatever the setting). Every
CHECK_S the checkout they run from is fetched, never asking for a password (git's own credentials, a keychain's, an
agent's; without them only what changes here counts); behind its upstream with nothing of its own (no change to what
git tracks, no commit the upstream hasn't), it's fast-forwarded. Once the commit checked out isn't the one the app
started on, nothing's changed but what's committed and the new version imports, the app restarts onto it as soon as
it's idle: no turn running, no dialog open, and nothing typed, clicked or scrolled in it for QUIET_S, or it isn't the
window you're in (quiet). A version that won't import is never restarted onto."""
import os
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

import widgets  # noqa: E402
# (git and the look at it, with no GTK: the host process's updater uses them too)
from baudelib import selfupdate, wording  # noqa: E402
from baudelib.selfupdate import (BROKEN, CHECK_S, GIT_ENV, HERE, git, importable, look, new_commits,  # noqa: E402,F401
                                 upstream)

READY_S = 2  # (a new version found: how often it's seen whether the app's idle yet)
QUIET_S = 60  # (the window you're in: this long with nothing typed, clicked or scrolled, and it's idle)

STARTED = git(HERE, "rev-parse", "HEAD", timeout=5)  # (the commit this process's code is, as it was imported)


def enabled():
    """Only once it's been turned on in Settings (never by default: an update runs code from the upstream)."""
    return selfupdate.allowed() and widgets.load_settings().get("auto_update") is True


def watch_input(win):
    """Keys, clicks and scrolls in win kept as they come (win.last_input, a monotonic time): quiet reads it. Nothing's
    taken from what they were going to."""
    win.last_input = time.monotonic()
    legacy = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
    kinds = (Gdk.EventType.KEY_PRESS, Gdk.EventType.BUTTON_PRESS, Gdk.EventType.SCROLL, Gdk.EventType.TOUCH_BEGIN)

    def event(c, *_):  # (its argument's None under PyGObject)
        if (e := c.get_current_event()) is not None and e.get_event_type() in kinds:
            win.last_input = time.monotonic()
        return False  # (on to where it was going)
    legacy.connect("event", event)
    win.add_controller(legacy)


def quiet(win):
    """win isn't the window you're in, or nothing's been typed, clicked or scrolled in it for QUIET_S."""
    return not win.is_active() or time.monotonic() - getattr(win, "last_input", 0.0) >= QUIET_S


class Updater:
    """The app's look at git every CHECK_S (look, in a thread), and the restart once a new version's found and
    idle() says so: restart(note), note what the new one says it was updated by (restarted)."""

    def __init__(self, module, idle, restart, root=HERE, every=CHECK_S, blocked=None):
        self.module, self.idle, self.restart, self.root = module, idle, restart, root
        self.blocked = blocked or (lambda _waiting: None)  # (told while a version found waits on idle: its bar)
        self.started = STARTED if root == HERE else git(root, "rev-parse", "HEAD", timeout=5)  # (this process's code)
        self.ready = None  # (a commit found to restart onto, waiting for idle)
        self.looking = False
        self.source = GLib.timeout_add_seconds(every, self.tick) if self.started else None

    def tick(self):
        if self.ready is None and not self.looking and enabled():
            self.looking = True
            threading.Thread(target=lambda: GLib.idle_add(self.found, look(self.started, self.module, self.root)),
                             daemon=True).start()
        return True

    def found(self, commit):
        self.looking = False
        if commit is not None and self.ready is None:
            self.ready = commit
            GLib.timeout_add_seconds(READY_S, self.when_idle)
            self.when_idle()
        return False

    def when_idle(self):
        """A new version found: restarted onto it once the app's idle (and still meant to update)."""
        if self.ready is None or not enabled():
            self.ready = None
            self.blocked(False)
            return False
        if not self.idle():
            self.blocked(True)
            return True
        self.restart({"from": self.started, "to": self.ready, "commits": new_commits(self.started, self.ready, self.root)})
        return False

    def stop(self):
        if self.source is not None:
            GLib.source_remove(self.source)
            self.source = None


def front_app():
    """On macOS, the app you're in if it isn't this one (its pid), for the restarted one to give the front back to
    (give_back); else None."""
    if sys.platform != "darwin":
        return None
    try:
        import macos
        pid = macos.frontmost_pid()
    except Exception:  # noqa: BLE001  (a restart doesn't wait on this)
        return None
    return pid if pid and pid != os.getpid() else None


def show(win, state):
    """A window put back after a restart (state, what the one before left: None, a start of its own), shown. If you
    were in another app, as it was restarted, it's shown without being brought to the front (macOS: GTK's present
    makes the app the active one), and the front given back to that app if it was taken anyway; a restart you didn't
    ask for doesn't take you from what you were doing."""
    if state and state.get("front"):
        win.set_visible(True)
    else:
        win.present()


def restarted(win, state, name):
    """After a restart (win the first window put back, shown): the app you were in given the front back, if it was
    taken from it (show), and a toast saying what the app was updated by, if it was."""
    if (pid := state.get("front")) and sys.platform == "darwin":
        import macos  # (no GTK: beside this file)

        def give_back():
            try:
                macos.give_front_to(pid)
            except Exception as e:  # noqa: BLE001
                print(f"{name.lower()}: the front not given back:", e, file=sys.stderr)
            return False
        give_back()
        GLib.timeout_add(400, give_back)  # (and again once the window's shown: GTK takes it as it maps it)
    note = state.get("updated")
    if note and getattr(win, "toasts", None) is not None:
        n = note.get("commits") or 0
        win.toasts.add_toast(Adw.Toast(title=wording.say("updates.updated_commits", app=name, n=n) if n else
                                       wording.say("updates.updated", app=name), timeout=5))


def settings_row():
    """Settings → Auto restart on update (Baude's and Record's, the one setting): off until it's turned on."""
    up = upstream()
    row = Adw.SwitchRow(title=wording.say("updates.auto_restart"),
                        subtitle=wording.say("updates.auto_restart_from", upstream=up, seconds=CHECK_S) if up else
                        wording.say("updates.no_upstream"),
                        active=enabled() and up is not None, sensitive=up is not None)
    row.connect("notify::active", lambda r, _p: r.get_sensitive() and not r.syncing
                and widgets.save_settings(auto_update=r.get_active()))
    return widgets.follow_setting(row, "auto_update", lambda v: row.set_active(v is True and up is not None))


def made_when(stamp):
    return time.strftime("%-d %B %Y, %H:%M", time.localtime(stamp))


def changelog(commit, root=HERE):
    """The commits commit has, newest first, merges left out (theirs are): (short hash, when made, subject) each."""
    out = git(root, "log", "--no-merges", "--format=%h%x1f%ct%x1f%s", commit, timeout=10) if commit else None
    return [(short, int(stamp), subject) for short, stamp, subject in
            (line.split("\x1f", 2) for line in (out or "").splitlines() if line.count("\x1f") == 2) if stamp.isdigit()]


def version(commit, root=HERE):
    """The commit's short hash and when it was made, or None (not a git checkout)."""
    made = git(root, "log", "-1", "--format=%h%x1f%ct", commit, timeout=5) if commit else None
    short, _, stamp = (made or "").partition("\x1f")
    return (short, int(stamp)) if stamp.isdigit() else None


def version_row(commit=None, root=HERE):
    """Settings → Version (Baude's and Record's): the commit this process's code is (STARTED), and when it was made."""
    made = version(commit or STARTED, root)
    return Adw.ActionRow(title=wording.say("updates.version", commit=made[0]) if made else
                         wording.say("updates.version_unknown"),
                         subtitle=made_when(made[1]) if made else wording.say("updates.not_checkout"))


def changelog_group(commit, root=HERE, limit=None, made=None):
    """The commits the version has (changelog, or made if given), newest first, each its subject and when it was made,
    as rows; past limit, a Show N more row (Meta's changelog, under its reply)."""
    group, made = Adw.PreferencesGroup(), changelog(commit, root) if made is None else made

    def add(short, stamp, subject):
        row = Adw.ActionRow(title=subject, subtitle=f"{made_when(stamp)} · {short}", use_markup=False)
        row.commit = short
        group.add(row)
    shown = made if limit is None else made[:limit]
    for entry in shown:
        add(*entry)
    if len(shown) < len(made):
        more = Adw.ButtonRow(title=wording.say("meta.show_more", n=len(made) - len(shown)))

        def rest(btn):
            group.remove(btn)
            for entry in made[len(shown):]:
                add(*entry)
        more.connect("activated", rest)
        group.add(more)
    return group


def changelog_dialog(commit=None, root=HERE):
    """☰ → Changelog (Baude's and Record's): the commits the running version has (changelog_group), newest first, in a
    dialog whose header names that version and when it was made."""
    commit = commit or STARTED
    made, entries = version(commit, root), changelog(commit, root)
    page, group = widgets.settings_page(), changelog_group(commit, root, limit=50, made=entries)
    if not entries:
        group.add(Adw.ActionRow(title=wording.say("changelog.empty"), subtitle=wording.say("changelog.empty_detail")))
    page.add(group)
    head = Adw.HeaderBar(title_widget=Adw.WindowTitle(
        title=wording.say("changelog.title"),
        subtitle=wording.say("changelog.version_made", commit=made[0], made=made_when(made[1])) if made else ""))
    view = Adw.ToolbarView(content=page)
    view.add_top_bar(head)
    return Adw.Dialog(title=wording.say("changelog.title"), content_width=640, content_height=720, child=view)
