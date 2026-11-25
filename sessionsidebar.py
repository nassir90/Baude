"""sessionsidebar — the sessions sidebar (SessionSidebar): its status dots (StatusDot), the
sessions listed, their groups and rows.
"""
import os
import threading
import time

from baudelib import tokens  # noqa: E402  (Baude's measures: schema/tokens, the Android app's too)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

import metalook  # noqa: E402
import imageviews  # noqa: E402
import markdownview  # noqa: E402
import chatparts  # noqa: E402
import pickers  # noqa: E402
from widgets import Ticker  # noqa: E402,F401
from pickers import ChoiceDropDown  # noqa: E402  (a dropdown over (value, label): Record's hosts too)
from widgets import (  # noqa: E402,F401
    discard, ensure_icons, load_settings, pretty_path, save_settings, time_ago, toast, walk,
)
from pickers import CheckList, TreeList, sidebar_action, ellipsize_dropdown  # noqa: E402,F401

from startup import trace  # noqa: E402
from baudelib.client import chat_name  # noqa: E402
from sessionlist import (  # noqa: E402
    HERE_HOST, THIS_MACHINE, apply_desktop_index, bot_picture, chat_group, chat_groups, collapse_copies, date_group,
    desktop_index, desktop_parents, fork_tree, host_key, last_used, list_sessions, made_at, made_groups, main_folder,
    network, next_midnight, place_sessions, remote_info, remote_name, remote_path, save_info_cache, session_group,
    short_group_label, started_elsewhere,
)
from sidebarmenu import SidebarMenu  # noqa: E402


# ------------------------------------------------------- sessions dialog --

class StatusDot(Gtk.Image):
    """A session's state: working (grey, pulsing), waiting (yellow: a prompt needs an answer),
    done (blue: the turn finished, waiting for your next message); inactive shows nothing."""

    TIPS = {"working": wording.say("common.working"), "waiting": wording.say("asks.waiting"),
            "done": "Finished — waiting for input",
            "inactive": "Inactive"}

    def __init__(self, state="inactive"):
        ensure_icons()
        super().__init__(valign=Gtk.Align.CENTER)  # (the icons' size: Settings → Size)
        self.add_css_class("status-dot")
        self.state = None
        self.connect("map", StatusDot._mapped)  # (plain functions: see Spinner)
        self.connect("unmap", StatusDot._unmapped)
        self.set_state(state)

    def _mapped(self):
        if self.state == "working":
            Ticker.pulse(self)

    def _unmapped(self):
        Ticker.pulsing.discard(self)

    def set_state(self, state):
        if state == self.state:
            return
        if self.state:
            self.remove_css_class(self.state)
        self.state = state
        self.add_css_class(state)
        (Ticker.pulse if state == "working" else Ticker.still)(self)
        self.set_from_icon_name("baude-dot-symbolic")
        self.set_visible(state != "inactive")  # an inactive chat shows no dot at all
        self.set_tooltip_text(self.TIPS.get(state, state))


class SessionSidebar(SidebarMenu, Gtk.Box):
    """The left pane, like Claude Desktop's: new session / open folder, search, and every
    session grouped by folder (this folder first), or by day (⋮ → Group by). Click one to switch to it, folder
    and all."""

    GROUP_LIMIT = 8  # sessions shown per other folder before "Show N more"
    DATE_LIMITS = {"date:0": None, "date:1": 10}  # by day: all of today's, 10 of yesterday's, else DATE_LIMIT
    DATE_LIMIT = 5

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, width_request=250)
        self.add_css_class("session-sidebar")
        self.win, self.sessions, self.expanded, self.loading = win, [], set(), False
        self.monitors, self.turn_cache, self.expiry = {}, {}, None  # see watch_rows
        self.clock, self.day, self.midnight = time.time, None, None  # see track_days
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=8, margin_end=8,
                      margin_top=8, margin_bottom=4)
        self.new_btn = self._action("document-new-symbolic", wording.say("sidebar.new_session"), "Ctrl+N",
                                    self.new_chat)
        self.new_btn.set_hexpand(True)
        self.folder_btn = self._action("folder-open-symbolic", wording.say("sidebar.open_folder"), wording.say("sidebar.open_folder_tip"),
                                       self.open_folder)
        # (beside it: the host a new session's made on, by its name, verbatim (this machine's local), and whose chats
        # are listed; kept in settings.json's new_session_host: fill_new_hosts, shown_host. A right-click, or press and
        # hold: host_menu)
        self.host_dd = ellipsize_dropdown(ChoiceDropDown([(HERE_HOST, THIS_MACHINE)],
                                                         wording.say("sidebar.host_picker_tip"),
                                                         self.pick_host), 12)
        self.host_dd.add_css_class("flat")
        self.host_dd.add_css_class("new-host")
        self.host_dd.set_valign(Gtk.Align.CENTER)
        if (popup := next((w for w in walk(self.host_dd) if isinstance(w, Gtk.Popover)), None)) is not None:
            popup.connect("show", lambda *_: network().look())  # (opened: a server started a moment ago, offered)
        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right.connect("pressed", lambda _g, _n, x, y: not (popup and popup.get_visible())  # (not in its list, open)
                      and self.host_menu(x, y))
        self.host_dd.add_controller(right)
        hold = Gtk.GestureLongPress(touch_only=False)  # (touch's right-click: the same menu, its list not opened)
        hold.connect("pressed", lambda g, x, y: not (popup and popup.get_visible())
                     and (g.set_state(Gtk.EventSequenceState.CLAIMED), self.host_menu(x, y)))
        hold.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.host_dd.add_controller(hold)
        new_row = Gtk.Box(spacing=2)
        new_row.append(self.new_btn)
        new_row.append(self.host_dd)
        top.append(new_row)
        top.append(self.folder_btn)
        find_row = Gtk.Box(spacing=2, margin_top=6)
        self.search = Gtk.SearchEntry(placeholder_text=wording.say("sidebar.search"), hexpand=True)
        self.search.connect("search-changed", lambda *_: self.fill())
        find_row.append(self.search)
        # (⋮: Group by, a choice of three; then Groups, which sessions are listed, a check each and one over them all,
        # as the Kubernetes page's Namespaces (pickers.CheckList), filled by fill_filters; a right-click on a group:
        # Rename Group…, Delete Group. Which host's: New session's dropdown, host_filter)
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        heading = Gtk.Label(label=wording.say("sidebar.group_by"), xalign=0, css_classes=["heading", "dim-label"])
        view.append(heading)
        first = None
        for by_key, label in (("folder", "common.folder"), ("created", "sidebar.created"),
                              ("updated", "sidebar.updated")):
            choice = Gtk.CheckButton(label=wording.say(label), action_name="sb.group",
                                     action_target=GLib.Variant.new_string(by_key))
            if first is None:
                first = choice
            else:
                choice.set_group(first)
            view.append(choice)
        view.append(Gtk.Separator())
        self.group_list = CheckList(wording.say("sidebar.groups"),
                                    lambda keys: self.filtered(sidebar_groups=keys or None), self.group_menu)
        view.append(self.group_list)
        view.append(Gtk.Separator())
        new_group = Gtk.Button(label=wording.say("sidebar.new_group_ellipsis"), css_classes=["flat"])  # (no chat
        # moved to it: ask_new_group)
        new_group.get_child().set_xalign(0)
        new_group.get_child().add_css_class("body")
        new_group.connect("clicked", lambda *_: (pop.popdown(), self.ask_new_group(None)))
        view.append(new_group)
        pop = Gtk.Popover(child=view)
        self.view_btn = Gtk.MenuButton(icon_name="view-more-symbolic", popover=pop, valign=Gtk.Align.CENTER,
                                       tooltip_text=wording.say("sidebar.group_by_tip"))
        self.view_btn.add_css_class("flat")
        # (opened, or the window come forward: the network looked at again now, for a server started a moment ago)
        self.view_btn.connect("notify::active", lambda b, _p: b.get_active() and network().look())
        win.connect("notify::is-active", lambda w, _p: w.is_active() and network().look())
        find_row.append(self.view_btn)
        top.append(find_row)
        self.append(top)
        # (the host picked isn't there, or is deactivated: said over its chats, as they were last seen: show_host_state)
        self.host_note = chatparts.HostState()  # (its titles: show_host_state's markup)
        self.append(self.host_note)
        self.list = TreeList(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", self.on_activate)
        self.scroller = Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(self.scroller)
        # (at the foot, under a rule from the chats: Update, while a new version waits: show_update_bars)
        self.update_bar = chatparts.UpdateBar(lambda: win.get_application().update_now(win))
        self.append(self.update_bar)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.list.add_controller(keys)
        actions = Gio.SimpleActionGroup()
        for name, fn in (("rename", self.ask_rename), ("archive", self.archive), ("unarchive", self.unarchive),
                         ("trash", self.ask_trash), ("hide", self.hide_remote), ("stop", self.stop_remote),
                         ("make_bot", self.ask_bot), ("unbot", self.unbot)):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", lambda _a, v, fn=fn: fn(self.find_info(v.get_string())))
            actions.add_action(act)
        group = Gio.SimpleAction.new_stateful("group", GLib.VariantType.new("s"),
                                              GLib.Variant.new_string(self.grouping()))
        group.connect("change-state", self.on_group_changed)
        actions.add_action(group)
        add_server = Gio.SimpleAction.new("add_server", None)
        add_server.connect("activate", lambda *_: self.win.ask_add_host())
        actions.add_action(add_server)
        off = Gio.SimpleAction.new("deactivate_host", GLib.VariantType.new("(sb)"))
        off.connect("activate", lambda _a, v: self.deactivate_host(*v.unpack()))
        actions.add_action(off)
        # (groups: a chat right-clicked → Move to Group, its group ticked (the action's state: (its id, its group));
        # New Group… (a chat's id, to be moved to it; "": none); a group right-clicked in ⋮ → Rename Group…, Delete)
        move = Gio.SimpleAction.new_stateful("move_group", GLib.VariantType.new("(ss)"), GLib.Variant("(ss)", ("", "")))
        move.connect("activate", lambda _a, v: self.move_to_group([v.unpack()[0]], v.unpack()[1]))
        actions.add_action(move)
        for name, fn in (("new_group", lambda sid: self.ask_new_group(sid or None)),
                         ("rename_group", self.ask_rename_group), ("delete_group", self.delete_group_with_undo)):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", lambda _a, v, fn=fn: (pop.popdown(), fn(v.get_string())))
            actions.add_action(act)
        self.insert_action_group("sb", actions)
        self.actions = actions
        self.remote_keys, self.remote_ids = {}, frozenset()  # (a server's folders: their servers; see groups)
        self.assigned = None  # (chat_groups, as groups last read them, for session_row)
        self.fill_filters()
        network().listen(self.on_network)
        win.connect("notify::is-active", lambda *_: self.by_date() and self.day != self.today() and self.fill())
        self.connect("unrealize", lambda *_: self.drop_midnight())  # (the window is closed: no more midnights)
        self.refresh()

    _action = staticmethod(sidebar_action)  # (widgets': Record's New page is one too)

    # ---- data
    def closed(self):
        """Its window is closed (BaudeWindow.let_go): no more lists, watches or timers for it."""
        return getattr(self.win, "let_go_done", False)

    def refresh(self):
        """Re-read the session list in the background (unchanged files come from the cache)."""
        if self.closed():
            return
        if self.loading:
            self.again = True
            return
        self.loading, self.again = True, False

        def work():
            keep = {self.win.session_id}
            index = desktop_index()
            shape = lambda found: place_sessions(  # noqa: E731
                collapse_copies(apply_desktop_index(found, index, keep), keep=keep))
            # (a slow first read shows the newest sessions as they come)
            sessions = shape(list_sessions(progress=lambda found: GLib.idle_add(partial, shape(found))))
            save_info_cache()
            GLib.idle_add(done, sessions)

        def partial(sessions):
            if self.loading and not self.closed():
                self.sessions = sessions
                self.fill()
                trace("sidebar shows the newest")
            return False

        def done(sessions):
            self.loading = False
            if self.closed():  # (closed while it read: what it found would watch files for nobody)
                return False
            self.sessions = sessions
            self.fill()
            trace("sidebar filled")
            if self.again:
                self.refresh()
            return False
        threading.Thread(target=work, daemon=True).start()

    def archived_ids(self):
        """Archived here, or in Claude Desktop (unless unarchived here)."""
        st = load_settings()
        unarchived = set(st.get("unarchived", []))
        return set(st.get("archived", [])) | {i["id"] for i in self.sessions
                                              if i.get("desktop_archived") and i["id"] not in unarchived}

    def grouping(self):
        """How the list is grouped (⋮ → Group by): "folder", "created" (by the day each chat was made) or "updated"
        (by the day it was last used; "date", from before, is that)."""
        by = load_settings().get("sidebar_group")
        return "updated" if by == "date" else by if by in ("created", "updated") else "folder"

    def by_date(self):
        """Grouped by day: "created" or "updated" (grouping), None by folder."""
        return by if (by := self.grouping()) != "folder" else None

    # ---- Which sessions are listed: one host's, New session's (the dropdown beside it), and ⋮ → Groups. A host is a
    # machine: this one (THIS_MACHINE: Claude Code's and Claude Desktop's chats, as ever, and those of a server on it),
    # or another a server's on (baudelib.server). A group is Baude's own, a list of chats of its own, whatever host
    # they're on (chat_group); any chat can be moved to another (Move to Group, move_to_group). Every group's listed
    # till some are picked (pickers.CheckList)
    def known_hosts(self):
        """[(key, name, note)]: this machine, then each other host a server's on, by name (note: "deactivated", for
        one that is; "gone", for one that is; "through" the member of the mesh it's reached through, for one this
        machine's host process isn't linked to itself)."""
        out, seen = [(HERE_HOST, THIS_MACHINE, "")], {HERE_HOST}
        for r in sorted(network().remotes(), key=lambda r: remote_name(r).lower()):
            if (key := host_key(r)) not in seen:
                seen.add(key)
                out.append((key, remote_name(r), "deactivated" if r.deactivated else "gone" if r.state == "dead"
                            else wording.say("sidebar.through", via=r.through) if r.through else ""))
        return out

    def known_groups(self):
        """[(name, label, note)]: each group, by name (those made, those chats are put in, and those hosts' sessions
        are in as they were started: chat_group), then No group ("")."""
        st = load_settings()
        assigned = chat_groups(st)
        names = set(made_groups(st)) | set(assigned.values()) | {chat_group(s, assigned) for s in network().sessions()}
        return [(g, g, "") for g in sorted(names - {""}, key=str.lower)] + [("", wording.say("sidebar.no_group"), "")]

    def picked(self, name, known):
        """The groups picked (settings' sidebar_groups) that are still there, or None: all of them (none of those picked
        there, or every one there picked)."""
        shown = load_settings().get(name)
        if not isinstance(shown, list):
            return None
        keys = {key for key, *_ in known}
        shown = set(shown) & keys
        return shown if shown and shown != keys else None

    def host_filter(self):
        """{the one host whose chats are listed}: New session's (shown_host)."""
        return {self.shown_host()}

    def group_filter(self):
        return self.picked("sidebar_groups", self.known_groups())

    def shows(self, s, hosts, groups, assigned=None):
        """Is a server's session (Network.sessions') listed, with these hosts and groups picked (None: all)? assigned:
        chat_groups, read already."""
        return (hosts is None or host_key(s["server"]) in hosts) and \
            (groups is None or chat_group(s, assigned) in groups)

    def filtered(self, **picked):
        """Groups picked (sidebar_groups=[names]; None: all of them, as is every one there): listed."""
        known = {"sidebar_groups": self.known_groups}
        save_settings(**{name: None if keys is None or set(keys) >= {k for k, *_ in known[name]()} else keys
                         for name, keys in picked.items()})
        self.fill_filters()
        self.fill()

    def fill_filters(self):
        """New session's hosts and ⋮ → Groups, as the network has them now (what's picked that the network's lost: not
        shown, and not left out either, till it's back)."""
        if (old := load_settings().get("sidebar_network")) is not None:  # (⋮ → Group's one group, from before)
            save_settings(sidebar_network=None, sidebar_groups=[old] if old else None)
        self.fill_new_hosts()
        self.show_host_state()
        self.group_list.fill([(key, label) for key, label, _note in self.known_groups()],
                             sorted(self.group_filter() or ()))

    def host_menu(self, x, y):
        """Right-click New session's host: Add Host…, always; and for another machine's, Deactivate, or Activate for one
        that's deactivated."""
        menu, add = Gio.Menu(), Gio.Menu()
        if (r := self.shown_remote()) is not None:
            item = Gio.MenuItem.new(wording.say("hosts.activate") if r.deactivated else wording.say("hosts.deactivate"),
                                    None)
            item.set_action_and_target_value("sb.deactivate_host", GLib.Variant("(sb)", (host_key(r), not r.deactivated)))
            menu.append_item(item)
        # (another machine, by where ssh goes: ask_add_host)
        add.append(wording.say("sidebar.add_host"), "sb.add_server")
        menu.append_section(None, add)
        self.host_menu_shown = self.menu_at(menu, self.host_dd, x, y)

    def deactivate_host(self, key, off):
        """New session's host right-clicked → Deactivate (off), or Activate: Network.deactivate, its chats grayed and
        the note over them saying so while it is."""
        if (r := next((r for r in network().remotes() if host_key(r) == key), None)) is not None:
            network().deactivate(r, off)
            toast(self, wording.say("hosts.now_deactivated" if off else "hosts.now_activated", host=remote_name(r)))

    def group_menu(self, key, check, x, y):
        """Right-click a group (⋮ → Groups): Rename Group…, Delete Group. No group has none."""
        if not key:
            return
        menu = Gio.Menu()
        for label, action in ((wording.say("sidebar.rename_group_ellipsis"), "rename_group"),
                              (wording.say("sidebar.delete_group"), "delete_group")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(f"sb.{action}", GLib.Variant.new_string(key))
            menu.append_item(item)
        self.group_menu_shown = self.menu_at(menu, check, x, y)

    def menu_at(self, menu, parent, x, y):
        """A right-click's menu, shown at x, y in parent (gone once it's closed)."""
        pop = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        pop.set_parent(parent)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        # (on an idle, after the one its sections' separators are shown in: popped up before it, it's sized without
        # them, and macOS never sizes it again, so its last item was cut off)
        GLib.idle_add(self.popup_on_screen, pop)
        return pop

    @staticmethod
    def popup_on_screen(pop):
        """menu_at's idle: pop shown while its parent's still on screen. The list rebuilt before it (a chat archived,
        trashed or refreshed) takes the row out of the window, and a popover over a widget with no window has no
        surface to open from: GTK crashes. Then it's dropped instead."""
        parent = pop.get_parent()
        if parent is not None and parent.get_mapped():
            pop.popup()
        elif parent is not None:
            pop.unparent()
        return False

    # ---- groups: chats moved to one, and groups made, renamed and deleted (settings' chat_groups and group_names).
    # Each saves, keeps what's picked in ⋮ → Groups (a group renamed: picked by its new name; deleted: not), lists it
    # so, and returns what undoes it: move_to_group's for restore_groups, the others' for restore_group_state
    def group_state(self, ids=None):
        """What restore_group_state puts back: {"chat_groups": {id: its entry, None if it has none} (ids', or every
        chat's with one), "group_names": the groups made, "sidebar_groups": the groups picked (None: all)}."""
        st = load_settings()
        assigned, picked = chat_groups(st), st.get("sidebar_groups")
        return {"chat_groups": {i: assigned.get(i) for i in (assigned if ids is None else ids)},
                "group_names": made_groups(st), "sidebar_groups": list(picked) if isinstance(picked, list) else None}

    def restore_group_state(self, state):
        """What group_state said put back, each of its keys there: its chats' entries (None: none, so a chat's group
        is the one chat_group gives it without), the groups made, the groups picked."""
        changes = {}
        if "chat_groups" in state:
            assigned = chat_groups()
            for i, group in state["chat_groups"].items():
                if group is None:
                    assigned.pop(i, None)
                else:
                    assigned[i] = group
            changes["chat_groups"] = assigned or None
        if "group_names" in state:
            changes["group_names"] = list(state["group_names"] or []) or None
        if "sidebar_groups" in state:
            changes["sidebar_groups"] = state["sidebar_groups"]
        save_settings(**changes)
        self.groups_changed()

    def groups_changed(self):
        self.fill_filters()
        self.fill()

    def move_to_group(self, ids, group):
        """Chats (their session ids) put in a group; "": in none, a host's session too, whatever it was started in.
        Returns {id: its chat_groups entry before, None if it had none}, for restore_groups."""
        ids = [ids] if isinstance(ids, str) else list(ids)
        assigned = chat_groups()
        before = {i: assigned.get(i) for i in ids}
        save_settings(chat_groups={**assigned, **dict.fromkeys(ids, group or "")})
        self.groups_changed()
        return before

    def restore_groups(self, before):
        """Chats' chat_groups entries put back as move_to_group found them ({id: entry, None: none})."""
        self.restore_group_state({"chat_groups": before})

    def group_members(self, group, assigned):
        """The ids of the chats in a group: those put in it, and hosts' sessions started in it and put in none."""
        return [i for i, g in assigned.items() if g == group] + \
            [s["id"] for s in network().sessions() if s["id"] not in assigned and chat_group(s, assigned) == group]

    def make_group(self, name):
        """A group made: listed in ⋮ → Groups and Move to Group, empty or not. Returns group_state() from before."""
        state = self.group_state(())
        if name and name not in state["group_names"]:
            save_settings(group_names=state["group_names"] + [name])
            self.groups_changed()
        return state

    def rename_group(self, old, new):
        """A group renamed: its chats (group_members) put in new, which is made in its place if it was made and picked
        in its place if it was; to a group there already, they join it. Returns group_state() from before."""
        if not old or not new or old == new:
            return self.group_state(())
        st = load_settings()
        assigned, picked = chat_groups(st), st.get("sidebar_groups")
        ids = self.group_members(old, assigned)
        state = self.group_state(ids)
        save_settings(chat_groups={**assigned, **dict.fromkeys(ids, new)} or None,
                      group_names=list(dict.fromkeys(new if g == old else g for g in made_groups(st))) or None,
                      sidebar_groups=sorted(set(picked) - {old} | {new})
                      if isinstance(picked, list) and old in picked else picked)
        self.groups_changed()
        return state

    def delete_group(self, name):
        """A group deleted: its chats (group_members) in none, and it neither made nor picked. Returns group_state()
        from before."""
        if not name:
            return self.group_state(())
        st = load_settings()
        assigned, picked = chat_groups(st), st.get("sidebar_groups")
        ids = self.group_members(name, assigned)
        state = self.group_state(ids)
        save_settings(chat_groups={**assigned, **dict.fromkeys(ids, "")} or None,
                      group_names=[g for g in made_groups(st) if g != name] or None,
                      sidebar_groups=sorted(set(picked) - {name}) or None if isinstance(picked, list) else picked)
        self.groups_changed()
        return state

    def delete_group_with_undo(self, name):
        """Delete Group (⋮ → Groups, a group right-clicked): deleted, said with Undo."""
        state = self.delete_group(name)
        if (root := self.get_root()) is not None and hasattr(root, "toasts"):
            said = Adw.Toast(title=wording.say("sidebar.group_deleted", name=GLib.markup_escape_text(name)),
                             button_label=wording.say("common.undo"), timeout=5)
            said.connect("button-clicked", lambda *_: self.restore_group_state(state))
            root.toasts.add_toast(said)

    def ask_new_group(self, sid=None):
        """New Group…: a group made, and the chat sid moved to it, if given."""
        self.ask_group_name(wording.say("new_session.new_group"), wording.say("common.create"), "",
                            lambda name: (self.make_group(name), sid and self.move_to_group([sid], name)))

    def ask_rename_group(self, name):
        """Rename Group… (⋮ → Groups, a group right-clicked)."""
        self.ask_group_name(wording.say("new_session.rename_group"), wording.say("common.rename"), name,
                            lambda new: new != name and self.rename_group(name, new))

    def ask_group_name(self, heading, verb, text, then):
        """A group's name asked for, as ask_rename asks a chat's: then(the name, its spaces tidied), unless blank."""
        entry = Gtk.Entry(text=text, activates_default=True)
        dlg = Adw.AlertDialog(heading=heading, extra_child=entry, default_response="ok", close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("ok", verb)
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda d, r: r == "ok" and (name := " ".join(entry.get_text().split())) and then(name))
        self.dialog = dlg
        dlg.present(self.win)
        entry.grab_focus()

    def on_network(self):
        """The network's servers or their sessions changed: ⋮'s hosts and groups, what's hidden coming back, and the
        list, when what it shows of them changed (which sessions are a server's, too: this machine's are listed by it,
        not as its own chats)."""
        if self.closed():
            return
        self.fill_filters()
        self.unhide_remote()
        hosts, groups, assigned = self.host_filter(), self.group_filter(), chat_groups()
        ids = frozenset(i for s in network().sessions() for i in (s["id"], s.get("cli_id")) if i)
        # (only what the rows show: a server says so every time a transcript grows, and a refill drops a menu open on a row)
        # (and the host listed: this machine's again while the one picked is gone)
        seen = (sorted(hosts), sorted((s["id"], s.get("title"), s.get("state"), s["server"].state, s.get("cwd"),
                                       s.get("said"), s.get("group"), bool(s.get("archived")), len(s.get("held") or []))
                                      for s in network().sessions() if self.shows(s, hosts, groups, assigned)
                                      or s["id"] in getattr(self, "listed_remote", ())))
        if ids != self.remote_ids or seen != getattr(self, "remote_seen", None):
            self.remote_seen = seen
            self.fill()

    def hidden_remote(self):
        """{session id: what it was as it was deleted} for the network's sessions deleted here (hide_remote)."""
        hidden = load_settings().get("hidden_remote")
        return hidden if isinstance(hidden, dict) else {}

    def unhide_remote(self):
        """A deleted session that shows up again is listed again: it's been used since, it's at a turn, or its server,
        gone as it was deleted, is back."""
        hidden = self.hidden_remote()
        if not hidden:
            return
        back = set()
        for s in network().sessions():
            if (h := hidden.get(s["id"])) is None:
                continue
            if (s.get("updated") or 0) > (h.get("updated") or 0) + 1 or s.get("state") in ("working", "waiting") or \
                    h.get("dead") and s["server"].state == "alive":
                back.add(s["id"])
        if back:
            save_settings(hidden_remote={k: v for k, v in hidden.items() if k not in back} or None)

    def on_group_changed(self, action, value):
        action.set_state(value)
        save_settings(sidebar_group=value.get_string())
        self.fill()

    def today(self):
        import datetime
        return datetime.date.fromtimestamp(self.clock())

    def groups(self):
        """[(key, label, folder, [(session, depth, is_last)])], each group's rows as fork_tree places them: by folder,
        this folder first, then by latest activity (a worktree's chats are in its repository's folder: see
        place_sessions; a Desktop chat is in the group of the one it came from; a session of a server on another host
        in that host's folder: "remote:<server>:<folder>"); or by day (date_group), newest first, where a session and
        those under it (its forks, a Desktop chat's side sessions) go together, by the latest activity of any of them,
        as Claude Desktop's sidebar has them: a fork used today brings the session it came from into today, with it;
        or by the day each was made (made_at), a session and those under it by the day the session was made. The open
        chat is listed where it is (archived, say), and only a new one not saved yet is added at the top of its folder.
        Only the hosts and groups picked in ⋮ (host_filter, group_filter; a chat's group: chat_group, the open one not
        saved yet's too); a server's sessions deleted here (hide_remote): not listed till they show up again."""
        q = self.search.get_text().strip().lower()
        here = session_group(self.win.cwd)[0]
        by_date, now = self.by_date(), self.clock()
        hosts, picked = self.host_filter(), self.group_filter()
        assigned = self.assigned = chat_groups()  # (session_row's too)
        marks = self.marks = self.meta_marks()  # (Meta's changes, listed where they were till they're kept: session_row)
        mine = hosts is None or HERE_HOST in hosts  # (this machine's own chats)
        groups, listed, gone = {}, [], []
        archived, hidden = self.archived_ids(), self.hidden_remote()
        # (a server's sessions aren't this machine's own chats, though one on this machine keeps their transcripts here)
        remote = self.remote_ids = frozenset(i for s in network().sessions() for i in (s["id"], s.get("cli_id")) if i)
        ghosts = [g for g in self.meta_ghosts() if not any(i["id"] == g["id"] for i in self.sessions)]
        for info in sorted(self.sessions + ghosts if mine else [], key=last_used, reverse=True):  # (the last typed in
            if info["id"] in remote or picked is not None and chat_group(info, assigned) not in picked and \
                    info["id"] not in marks:  # first)
                continue
            folder = info.get("folder", info.get("cwd"))
            key, label = session_group(folder)
            tree = os.path.basename(info.get("worktree") or "")
            if q and q not in f"{info.get('title') or ''} {label} {tree} {(info.get('bot') or {}).get('name') or ''}" \
                    .lower():
                continue
            if info["id"] in archived and info["id"] not in marks:
                gone.append(info)
                continue
            listed.append((info, key, label, folder))
        # a chat Desktop started from another (a side session, a fork) is in that one's group, under it, whatever its
        # own folder
        by_id = {info["id"]: (info, key, label, folder) for info, key, label, folder in listed}
        for info, key, label, folder in listed:
            top, seen = info["id"], set()
            while top not in seen and (up := next((i for i in desktop_parents(by_id[top][0]) if i in by_id), None)):
                seen.add(top)
                top = up
            _info, key, label, folder = by_id[info["id"] if top in seen else top]  # (a loop: where it is)
            groups.setdefault(key, [label, folder if key != "scratch" else None, []])[2].append(info)
        # a server's sessions: one on this machine's in its folder's group, with this machine's chats there; another
        # host's in a group of its own for each folder there, its name with it
        self.remote_keys = {}
        everywhere = [s for s in network().sessions() if not s.get("draft")]  # (a draft, the open one's: not a new
        # chat not written in yet)
        far = started_elsewhere(network().sessions())  # (only ever under the agent on another host that started it)
        shown = {s["id"] for s in everywhere
                 if self.shows(s, hosts, picked, assigned) and s["id"] not in far or s["id"] in marks}
        # a chat an agent started, on another host or in another group: listed under the agent, if that's listed
        # (pulled: dropped below where the agent isn't, archived, say, or not found by a search)
        pulled = set()
        while more := {s["id"] for s in everywhere if s["id"] not in shown and s.get("spawned_by") in shown}:
            shown |= more
            pulled |= more
        self.listed_remote = shown  # (on_network: what's listed of the network's sessions)
        for info in sorted((remote_info(s) for s in everywhere if s["id"] in shown), key=last_used, reverse=True):
            r = info["remote"]
            if q and q not in " ".join([info.get("title") or "", info.get("cwd") or "", remote_name(r),
                                        (info.get("bot") or {}).get("name") or ""]).lower():
                continue
            if (info.get("archived") or info["id"] in archived) and info["id"] not in marks:  # (archived on its host;
                gone.append(info)  # or here, before hosts did)
                continue
            if info["id"] in hidden:
                continue
            folder = info.get("cwd") or "?"
            if host_key(r) == HERE_HOST:
                key, label = session_group(folder)
            else:
                key = f"remote:{r.key}:{folder}"
                label = wording.say("sidebar.folder_on_host", folder=remote_path(folder, r), host=remote_name(r))
                self.remote_keys[key] = r
            groups.setdefault(key, [label, folder, []])[2].append(info)
        # the open session may not be saved yet: shown once it's something (typed into, sent), never while it's blank
        # (one that's listed already, archived or not, is listed there only)
        sid = self.win.session_id
        if mine and not q and self.win.chat_begun() and (self.win.remote is None or self.win.remote.mine) and \
                (picked is None or self.open_group(assigned) in picked) and sid not in archived and \
                not any(sid in (i["id"], i.get("cli_id"))
                        for i in [*gone, *(i for g in groups.values() for i in g[2])]):
            key, label = session_group(self.win.cwd)
            groups.setdefault(key, [label, self.win.cwd, []])[2].insert(0, {
                "id": sid, "title": getattr(self.win, "session_title", None) or wording.say("sidebar.new_session"),
                "cwd": self.win.run_cwd, "folder": self.win.cwd, "worktree": main_folder(self.win.run_cwd)[1],
                "mtime": now, "prompts": 0, "unsaved": True})
        # (forks are found within a folder, by day too: a fork is in its session's folder. A folder with a server's
        # sessions and this machine's chats in it has them in one order: the open, unsaved one first, then the newest)
        # (a chat a Bot started, in whatever folder or on whatever host: in the Bot's group, to go under it)
        home = {i["id"]: k for k, (_label, _folder, items) in groups.items() for i in items}
        archived_ids = {i["id"] for i in gone}  # (archived: its threads are under it there, wherever they were)
        while lost := [(k, i) for k, (_label, _folder, items) in groups.items() for i in items
                       if i["id"] in pulled and i.get("spawned_by") not in home.keys() | archived_ids] + \
                      [(None, i) for i in gone if i["id"] in pulled and i.get("spawned_by") not in home.keys() | archived_ids]:
            for key, info in lost:  # (its agent not listed: nor is it)
                (gone if key is None else groups[key][2]).remove(info)
                home.pop(info["id"], None)
                archived_ids.discard(info["id"])
        while moved := [(k, i) for k, (_label, _folder, items) in groups.items() for i in items
                        if i.get("spawned_by") in archived_ids]:
            for key, info in moved:
                groups[key][2].remove(info)
                gone.append(info)
                archived_ids.add(info["id"])
        for key, (_label, _folder, items) in list(groups.items()):
            for info in list(items):
                if (up := home.get(info.get("spawned_by"))) is not None and up != key:
                    items.remove(info)
                    groups[up][2].append(info)
        groups = {k: g for k, g in groups.items() if g[2]}
        order_of = lambda items: sorted(items, key=lambda i: (not i.get("unsaved"), -last_used(i)))  # noqa: E731
        groups = {k: [label, folder, fork_tree(order_of(items))] for k, (label, folder, items) in groups.items()}
        if by_date:
            trees = [[]]
            for _label, _folder, rows in groups.values():
                for row in rows:
                    if not row[1] and trees[-1]:
                        trees.append([])
                    trees[-1].append(row)
            # (the open, unsaved one first, then the newest; a sort keeps sessions' order where it's the same. Nothing
            # listed, a search that finds nothing say, leaves the one empty tree, which has no first session. Made:
            # the day the top one was made)
            newest = (lambda rows: made_at(rows[0][0])) if by_date == "created" else \
                (lambda rows: max(last_used(info) for info, _depth, _last in rows))  # noqa: E731
            trees = sorted(filter(None, trees), key=lambda t: (not t[0][0].get("unsaved"), -newest(t)))
            groups = {}
            for rows in trees:
                key, label = date_group(newest(rows), now)
                groups.setdefault(key, [label, None, []])[2].extend(rows)
        # (days don't overlap: by day the latest activity of any session in them puts them in order (made: when its
        # top-level sessions were made), by folder a top-level session's)
        when = made_at if by_date == "created" else last_used
        order = sorted(groups.items(), key=lambda kv: (kv[0] != here, -max(
            when(i) for i, depth, _ in kv[1][2] if by_date == "updated" or not depth)))
        out = [(k, label, folder, rows) for k, (label, folder, rows) in order]
        bots = []  # (by folder, Bots first, each with what's under it: its forks, its threads, whatever host they're
        for k, label, folder, rows in ([] if by_date else out):  # on; by date, they're among the chats by their time)
            keep, under = [], False
            for row in rows:
                if not row[1]:
                    under = bool(row[0].get("bot"))
                    if under:
                        bots.append([])
                (bots[-1] if under else keep).append(row)
            rows[:] = keep
        out = [g for g in out if g[3]]
        if bots:  # (the last used first)
            out.insert(0, ("bots", wording.say("common.bots"), None,
                           [row for tree in sorted(bots, key=lambda t: -last_used(t[0][0])) for row in tree]))
        if gone:
            out.append(("archived", wording.say("sidebar.archived"), None, fork_tree(gone)))
        return out

    # ---- view
    def collapsed(self):
        c = load_settings().get("collapsed_folders")
        return set(c) if c is not None else {"archived"}

    def toggle_folder(self, key):
        c = self.collapsed()
        c.symmetric_difference_update({key})
        save_settings(collapsed_folders=sorted(c))
        self.fill()

    def limit(self, key):
        """How many of a group's top-level sessions (with their forks) show before "Show N more": all of this
        folder's, 8 of another's; by day, all of today's, 10 of yesterday's and 5 of an earlier day's."""
        if key.startswith("date:"):
            return self.DATE_LIMITS.get(key, self.DATE_LIMIT)
        return None if key == session_group(self.win.cwd)[0] else self.GROUP_LIMIT

    def track_days(self):
        """(by day) Regroup at the next local midnight. Past one (a machine that slept through it wakes to a late
        timer), the days move on: fill regroups, and so does the window coming to the front or going; what was
        expanded closes, and the timer is set again."""
        today = self.today()
        if today != self.day:
            if self.day is not None:
                self.expanded = {k for k in self.expanded if not k.startswith("date:")}
            self.drop_midnight()
            self.day = today
        if self.midnight is None:
            now = self.clock()
            self.midnight = GLib.timeout_add_seconds(int(next_midnight(now) - now) + 1, self.at_midnight)

    def drop_midnight(self):
        if self.midnight is not None:
            GLib.source_remove(self.midnight)
            self.midnight = None

    def at_midnight(self):
        self.midnight = None
        if self.by_date():
            self.fill()  # (which sets the next; one that fired a little early sets a short one)
        return False

    def fill(self):
        if self.closed():
            return
        # rebuilding the list loses the scroll position (and focus scrolls it): put it back
        adj = self.scroller.get_vadjustment()
        keep = adj.get_value()
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
            discard(row)
        current = None
        searching = bool(self.search.get_text().strip())
        collapsed = self.collapsed()
        by_date = self.by_date()
        if by_date:
            self.track_days()
        listed = self.groups()
        if not searching:  # (the groups of the chats listed on the host picked, for new_group: not the open one not
            # saved yet, an archived one, one Meta's deleted, one Meta's changed listed where it was though it's now
            # in a group not picked, nor a chat an agent started, listed under it whatever its group. While searching:
            # those listed before)
            picked, self.listed_groups = self.group_filter(), set()
            for key, *_f, rows in listed:
                for info, _d, _l in rows if key != "archived" else ():
                    if not info["id"] or info.get("unsaved") or info.get("ghost") or info.get("spawned_by"):
                        continue
                    group = chat_group(info, self.assigned)
                    if picked is None or group in picked or info["id"] not in self.marks:
                        self.listed_groups.add(group)
            self.listed_host = self.shown_host()
        for key, label, folder, rows in listed:
            shut = key in collapsed and not searching
            if key != "bots":  # (Bots, first: no heading, their rows say what they are)
                self.list.append(self.header_row(key, label, folder, key == session_group(self.win.cwd)[0], shut))
            if shut and key != "bots":
                continue
            limit = None if searching or key in self.expanded or key == "bots" else self.limit(key)
            # (the limit counts top-level sessions: their forks come with them)
            tops = [n for n, (_info, depth, _last) in enumerate(rows) if not depth]
            shown = rows if limit is None or len(tops) <= limit else rows[:tops[limit]]
            placed = {}
            for info, depth, last in shown:
                row = self.session_row(info, depth, last)
                row.tree_parent = placed.get(info.get("fork_parent"))
                if info["id"]:  # (not the open session before its first message: a root's fork_parent is None too)
                    placed[info["id"]] = row
                self.list.append(row)
                if info["id"] and info["id"] == self.win.session_id or info.get("unsaved"):
                    current = row
            if len(shown) < len(rows):
                more = Gtk.ListBoxRow(child=Gtk.Label(label=wording.say("meta.show_more", n=len(rows) - len(shown)),
                                                      xalign=0, margin_start=12), activatable=True)
                more.get_child().add_css_class("dim-label")
                more.more_key = key
                self.list.append(more)
        if getattr(self.win, "panel", None) is not None:  # (the open chat a Bot or not: its Memory page)
            self.win.panel.bot_changed(self.find_info(self.win.session_id) if self.win.session_id else None)
        if current is not None:
            self.list.select_row(current)
        self.watch_rows()

        def restore(*_):
            adj.set_value(keep)
            return False
        restore()
        GLib.idle_add(restore)  # again once the new rows are laid out

    def header_row(self, key, label, folder, is_here, collapsed=False):
        # (markdownview.group_header: Record's pages and notebooks have the same; the arrow just right of the name,
        # shown when the pointer is near, or while collapsed)
        plus = ("list-add-symbolic", wording.say("sidebar.new_session_in", folder=label),
                lambda: self.win.switch_to(lambda: self.win.start_in_folder(folder))) \
            if folder and os.path.isdir(folder) else None
        if (r := self.remote_keys.get(key)) is not None:  # (a folder on a server: a session there, on it, if it's there)
            plus = ("list-add-symbolic", wording.say("sidebar.new_session_in_on", folder=remote_path(folder, r), host=remote_name(r)),
                    lambda: self.win.new_session_on(r, folder)) if r.state == "alive" else None
        box = markdownview.group_header(short_group_label(key, label), collapsed, lambda: self.toggle_folder(key), plus,
                                   tooltip=label, current=is_here)
        box.set_margin_top(tokens.px("gap.header-top"))
        box.set_margin_start(tokens.px("gap.header-start"))
        row = Gtk.ListBoxRow(child=box, activatable=False, selectable=False)
        row.folder, row.key = folder, key
        return row

    def meta_marks(self):
        """{session id: (how, note, action)}: the chats Meta's changed and that aren't kept so yet, and those it's
        highlighted (BaudeMeta.marks)."""
        panel = getattr(self.win, "panel", None)
        return panel.meta.marks("chat") if panel is not None else {}

    def meta_ghosts(self):
        """The chats Meta's deleted and that aren't kept so: listed still, struck through (BaudeMeta.ghosts)."""
        panel = getattr(self.win, "panel", None)
        return panel.meta.ghosts() if panel is not None else []

    def bot_row(self, info):
        """A Bot: twice a chat's height, its picture (its initials till it has one) and its name, nothing else of its
        chat's; a dot as a chat's. What's under it (its threads) is nested as forks are."""
        box = Gtk.Box(spacing=tokens.px("gap.avatar-gap"), margin_start=4, margin_end=4)
        bot = info.get("bot") or {}
        name = bot.get("name") or wording.say("sidebar.bot")
        avatar = Adw.Avatar(size=tokens.px("gap.avatar"), text=name, show_initials=True, valign=Gtk.Align.CENTER)
        bot_picture(info, lambda tex: tex is not None and avatar.set_custom_image(tex))
        box.append(imageviews.shaded_picture(avatar))
        title = pickers.FadeLabel(label=name, xalign=0, hexpand=True, valign=Gtk.Align.CENTER)
        title.add_css_class("bot-name")
        box.append(title)
        is_open = info["id"] == self.win.session_id
        dot = StatusDot(self.win.session_state() if is_open else self.file_state(info))
        if info.get("held"):
            box.append(self.held_tag())
        box.append(dot)
        r = info.get("remote")
        tip = [name, info.get("cwd") or "",
               f"On {remote_name(r)} ({r.host})" if r is not None else "", self.held_tip(info)]
        if group := chat_group(info, self.assigned):
            tip.append(f"Group {group}")
        if tree := info.get("worktree"):
            tip.append(f"Worktree {os.path.basename(tree)} of {pretty_path(info.get('folder') or '')}")
        row = Gtk.ListBoxRow(child=box, tooltip_text="\n".join(t for t in tip if t))
        row.add_css_class("bot-row")
        row.info, row.dot, row.depth, row.tree_anchor = info, dot, 0, avatar
        if (mark := getattr(self, "marks", {}).get(info["id"])) is not None:  # (Meta's change: metalook.mark_row)
            metalook.mark_row(row, ("chat", info["id"]), mark, self.win.panel.meta, title)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", lambda g, n, x, y: self.show_menu(row, x, y))
        row.add_controller(click)
        return row

    @staticmethod
    def held_tag():
        """A row's mark for a chat its host holds messages for (held: a subagent's answer after a restart), there till
        each's sent or dropped in it."""
        tag = Gtk.Label(label=wording.say("sidebar.held"), valign=Gtk.Align.CENTER)
        tag.add_css_class("row-tag")
        return tag

    @staticmethod
    def held_tip(info):
        n = info.get("held") or 0
        return f"{n} message{'s' if n != 1 else ''} held for it, to send or delete in it" if n else ""

    def session_row(self, info, depth=0, last=True):
        """Title ··· [dot] — one line; the dot only when something's happening (working, waiting for an answer,
        finished and waiting for input). Forks sit under their session like files in a tree. One Meta's changed:
        marked, with Keep and Undo (metalook.mark_row). A Bot: bot_row."""
        if info.get("bot") and not depth and not info.get("dead"):
            return self.bot_row(info)
        box = Gtk.Box(spacing=6, margin_start=6 + depth * TreeList.INDENT, margin_end=4)
        is_open = info.get("unsaved") or info["id"] == self.win.session_id
        dot = StatusDot(self.win.session_state() if is_open else self.file_state(info))
        label = info.get("fork_label") if depth else None
        # (a title too long for the row fades out where it's cut: pickers.FadeLabel)
        title = pickers.FadeLabel(label=label or chat_name(info) or wording.say("common.untitled"), xalign=0,
                                  hexpand=True)
        if depth:
            title.add_css_class("fork-title")
        box.append(title)
        r = info.get("remote")
        if info.get("dead"):  # (its server's gone from the network, or deactivated: listed as it was last seen)
            title.add_css_class("dim-label")
        if info.get("dead") and not info.get("off"):  # (no connection to its host: a crossed-out cloud)
            cloud = Gtk.Image.new_from_icon_name("baude-no-connection-symbolic")
            cloud.set_tooltip_text(wording.say("sidebar.no_connection"))
            cloud.add_css_class("dim-label")
            box.append(cloud)
        elif info.get("held"):
            box.append(self.held_tag())
        box.append(dot)
        tip = [chat_name(info) or "", info.get("cwd") or "", self.held_tip(info)]
        if r is not None:
            gone = ", deactivated" if info.get("off") else \
                f", gone from the network since {time_ago(r.seen)}" if info.get("dead") and r.seen else ""
            tip.append(f"On {remote_name(r)} ({r.host}){gone}")
        if group := chat_group(info, self.assigned):
            tip.append(f"Group {group}")
        if tree := info.get("worktree"):
            tip.append(f"Worktree {os.path.basename(tree)} of {pretty_path(info.get('folder') or '')}")
        if info.get("source") and r is None:
            tip.append(info["source"])
        if info.get("copies"):
            tip.append(f"{info['copies']} earlier cop{'y' if info['copies'] == 1 else 'ies'} of this conversation hidden")
        row = Gtk.ListBoxRow(child=box, tooltip_text="\n".join(t for t in tip if t))
        row.info, row.dot, row.depth, row.tree_anchor = info, dot, depth, title
        if (mark := getattr(self, "marks", {}).get(info["id"])) is not None:  # (Meta's change: metalook.mark_row)
            metalook.mark_row(row, ("chat", info["id"]), mark, self.win.panel.meta, title)
        if not info.get("unsaved") and not info.get("ghost"):
            click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            click.connect("pressed", lambda g, n, x, y: self.show_menu(row, x, y))
            row.add_controller(click)
        return row
