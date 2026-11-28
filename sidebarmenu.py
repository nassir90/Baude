"""sidebarmenu — what the sessions sidebar does with its sessions (SidebarMenu): a row's right-click menu and F2
(rename, make a Bot, archive, move to trash), the hosts listed and their state, groups, a new chat, the rows' dots
(file_state) and their refreshing.
"""
import base64
import json
import os
import shutil
import time

import baudelib.client  # noqa: E402  (servers on the network and their sessions: see baudelib/client.py)
import baudelib.hosts  # noqa: E402  (this machine's host process, started when it's wanted: see baudelib/hosts.py)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from baudelib.models import model_program  # noqa: E402,F401

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from livefeed import TURN_OPEN_SECONDS, external_state, transcript_turn_open  # noqa: E402
import imageviews  # noqa: E402
from widgets import load_settings, save_settings, toast  # noqa: E402,F401

from baudelib.client import chat_name  # noqa: E402
from sessionlist import (  # noqa: E402
    HERE_HOST, background_proc, bot_picture, can_be_bot, can_rotate, chat_group, host_key, network, remote_info,
    remote_name, stopping_said,
)


class SidebarMenu:
    """The sessions sidebar's right-click menu / F2 (rename, archive, move to trash), its hosts, groups and new
    chats, and its rows' dots."""

    # ---- right-click menu / F2: rename, archive, move to trash
    def find_info(self, sid):
        """(a server on this machine keeps its sessions' transcripts here too: what the network says of one comes first)"""
        return next((remote_info(s) for s in network().sessions() if s["id"] == sid), None) or \
            next((i for i in self.sessions if i["id"] == sid), None)

    def show_menu(self, row, x, y):
        info, sid = row.info, GLib.Variant.new_string(row.info["id"])
        top = not getattr(row, "depth", 0) and not info.get("fork_parent")  # (a Bot's a top-level chat)
        menu = Gio.Menu()
        items = [(wording.say("common.rename_ellipsis"), "rename"),
                 (wording.say("sidebar.unarchive") if info["id"] in self.archived_ids() else wording.say("sidebar.archive"),
                  "unarchive" if info["id"] in self.archived_ids() else "archive"),
                 (wording.say("trash.move_dots"), "trash")]
        if info.get("remote") is not None:  # (on a host: renamed, archived, stopped or trashed there while it's there;
            # while it's gone, only not listed here till it shows up again)
            active = not info.get("dead") and info.get("remote_state") in ("working", "waiting")
            archived = bool(info.get("archived")) or info["id"] in self.archived_ids()  # (here too: from before)
            rename = (wording.say("common.rename_ellipsis"), "rename")
            items = [(wording.say("common.delete"), "hide")] if info.get("dead") or \
                info["remote"].state != "alive" else \
                [rename] + ([(wording.say("sidebar.unarchive"), "unarchive")] if archived else [] if active else
                            [(wording.say("sidebar.archive"), "archive")]) + \
                ([(wording.say("common.stop"), "stop")] if active else []) + \
                [(wording.say("trash.move_dots"), "trash")]  # (Archive stops it: not at a turn)
        items.insert(1 if items[0][1] == "rename" else 0, (wording.say("sidebar.move_to_group"), None))  # (any chat's, on a host or not)
        if can_be_bot(info) and (top or info.get("bot")):  # (a Bot lives on its chat's host: one there, only)
            items[1:1] = [(wording.say("sidebar.bot_settings_ellipsis"), "make_bot"),
                                (wording.say("sidebar.unbot"), "unbot")] if info.get("bot") else \
                [(wording.say("sidebar.make_bot_ellipsis"), "make_bot")]
        if info.get("bot") and can_rotate(info) and info.get("remote_state") not in ("working", "waiting"):
            # (after Bot Settings…; not at a turn, as Archive isn't)
            at = next((i + 1 for i, (_label, action) in enumerate(items) if action == "make_bot"), 1)
            items.insert(at, (wording.say("sidebar.rotate"), "rotate"))
        if info.get("bot") and info.get("remote") is not None and info["remote"].state == "alive" and \
                not info["remote"].deactivated and not info.get("dead"):
            items.insert(1, (wording.say("sidebar.new_session"), "new_session"))
        if info.get("remote") is not None and not info.get("dead") and info["remote"].state == "alive" and \
                info.get("taken_up") is not False:  # (who in the mesh may manage it; a Claude Desktop chat only listed:
            items.insert(len(items) - 1, (wording.say("access.access_ellipsis"), "access"))  # the host's rule)
        for label, action in items:
            if action is None:
                menu.append_submenu(label, self.group_submenu(info))
                continue
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(f"sb.{action}", sid)
            menu.append_item(item)
        self.menu = self.menu_at(menu, row, x, y)

    def group_submenu(self, info):
        """A chat's Move to Group: each group, its own ticked, then No Group; and New Group…, which it's moved to."""
        now = chat_group(info)
        self.actions.lookup_action("move_group").set_state(GLib.Variant("(ss)", (info["id"], now)))
        menu, known, new = Gio.Menu(), Gio.Menu(), Gio.Menu()
        for key, label, _note in self.known_groups():
            item = Gio.MenuItem.new(label.replace("_", "__") if key else wording.say("sidebar.no_group_item"), None)  # (no mnemonics)
            item.set_action_and_target_value("sb.move_group", GLib.Variant("(ss)", (info["id"], key)))
            known.append_item(item)
        item = Gio.MenuItem.new(wording.say("sidebar.new_group_ellipsis"), None)
        item.set_action_and_target_value("sb.new_group", GLib.Variant.new_string(info["id"]))
        new.append_item(item)
        menu.append_section(None, known)
        menu.append_section(None, new)
        return menu

    def on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_F2:
            row = self.list.get_focus_child() or self.list.get_selected_row()
            if row is not None and getattr(row, "info", None) and not row.info.get("unsaved") \
                    and not row.info.get("dead"):  # (a network session's renamed by its server: not while it's gone)
                self.ask_rename(row.info)
                return True
        return False

    def ask_rename(self, info):
        if info is None:
            return
        bot = info.get("bot") if info.get("remote") is not None else None  # (a Bot: its name, as it's known by)
        entry = Gtk.Entry(text=(bot or {}).get("name") or info.get("title") or "", activates_default=True)
        dlg = Adw.AlertDialog(heading=wording.say("sidebar.rename_bot") if bot else wording.say("sidebar.rename_session"), extra_child=entry,
                              default_response="rename", close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("rename", wording.say("common.rename"))
        dlg.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, r):
            name = " ".join(entry.get_text().split())[:80]
            if r != "rename" or not name:
                return
            if bot:
                info["remote"].request({"op": "bot", "session": info["id"], "bot": {"name": name, "picture": None}},
                                       lambda reply: self.bot_set(info, reply))
            else:
                self.rename(info, entry.get_text())
        dlg.connect("response", answered)
        self.dialog = dlg
        dlg.present(self.win)
        entry.grab_focus()

    def ask_bot(self, info):
        """Make Bot… (or Bot Settings…): a name and a picture for a chat on a host, which then lists it as a Bot, gives
        its agent its name and its memory each time it starts, and keeps both there."""
        if info is None or info.get("remote") is None:
            return
        bot = info.get("bot") or {}
        named = bot.get("name") or " ".join((info.get("title") or "").split())[:80]  # (a new one: the chat's title)
        entry = Gtk.Entry(text=named, placeholder_text=wording.say("common.name"), activates_default=True)
        pick = {"data": None}  # (bytes: a picture chosen; "": its picture removed; None: as it is)
        avatar = Adw.Avatar(size=64, text=named or wording.say("sidebar.bot"), show_initials=True, halign=Gtk.Align.CENTER)
        choose = Gtk.Button(label=wording.say("bot_settings.choose_picture"))
        remove = Gtk.Button(label=wording.say("bot_settings.remove_picture"), visible=False)

        def shown(tex):
            if tex is not None and pick["data"] is None:
                avatar.set_custom_image(tex)
                remove.set_visible(True)
        bot_picture(info, shown)

        def chosen(dialog, result):
            try:
                f = dialog.open_finish(result)
            except GLib.Error:
                return
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(f.get_path(), 256, 256, True)
                ok, data = pix.save_to_bufferv("png", [], [])
            except GLib.Error as e:
                return self.win.note(wording.say("bot_settings.unreadable", why=e.message))
            pick["data"] = bytes(data)
            avatar.set_custom_image(Gdk.Texture.new_for_pixbuf(pix))
            remove.set_visible(True)

        def removed(*_a):
            pick["data"] = "" if bot.get("picture") else None
            avatar.set_custom_image(None)
            remove.set_visible(False)
        remove.connect("clicked", removed)

        def open_picker(*_a):
            images = Gtk.FileFilter(name="Pictures")
            images.add_pixbuf_formats()
            store = Gio.ListStore.new(Gtk.FileFilter)
            store.append(images)
            Gtk.FileDialog(title=wording.say("bot_settings.choose_a_picture"), filters=store).open(self.win, None, chosen)
        choose.connect("clicked", open_picker)
        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        buttons.append(choose)
        buttons.append(remove)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.append(imageviews.shaded_picture(avatar))
        body.append(buttons)
        body.append(entry)
        dlg = Adw.AlertDialog(heading=wording.say("bot_settings.settings") if bot else wording.say("bot_settings.make_bot"),
                              body=None if bot else wording.say("bot_settings.body"),
                              extra_child=body, default_response="save", close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("save", wording.say("common.save") if bot else wording.say("bot_settings.make_bot"))
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def typed(e):
            name = " ".join(e.get_text().split())
            avatar.set_text(name or wording.say("sidebar.bot"))
            dlg.set_response_enabled("save", bool(name))
        entry.connect("changed", typed)
        typed(entry)

        def answered(_d, response):
            name = " ".join(entry.get_text().split())
            if response != "save" or not name:
                return
            sent = {"name": name}
            if pick["data"] is not None:
                sent["picture"] = base64.b64encode(pick["data"]).decode() if pick["data"] else ""
            info["remote"].request({"op": "bot", "session": info["id"], "bot": sent},
                                   lambda reply: self.bot_set(info, reply))
        dlg.connect("response", answered)
        self.dialog = dlg
        dlg.present(self.win)
        entry.grab_focus()

    def ask_access(self, info):
        """Access…: a chat's own rule for who in the mesh may manage it, on its host (accessview.chat_dialog)."""
        if info is not None and info.get("remote") is not None:
            import accessview
            bot = info.get("bot") or {}
            accessview.chat_dialog(self.win, info["remote"], info["id"], bot.get("name") or info.get("title"))

    def unbot(self, info):
        """Turn Back into a Chat: listed as a chat; its memory and picture kept on its host, for if it's made a Bot again."""
        if info is not None and info.get("remote") is not None:
            info["remote"].request({"op": "bot", "session": info["id"], "bot": None},
                                   lambda reply: self.bot_set(info, reply))

    def new_agent_session(self, info):
        """A Bot's right-click → New session: a blank child session, configured through the usual header controls."""
        if info is not None and info.get("bot") and info.get("remote") is not None:
            self.win.new_agent_session(info)

    def rotate(self, info):
        """Rotate: a Bot's conversation archived on its host as a past thread ("<Bot> / <its title>"), and the Bot
        carried on in a new, empty one: listed so as the host answers; a window with the Bot open shows the new one
        (on_network: its listed rotated)."""
        if info is None or info.get("remote") is None:
            return
        r = info["remote"]

        def done(reply):
            if reply.get("op") != "rotated":
                toast(self, wording.say("trash.host_refused", host=remote_name(r), op="rotate",
                                        why=baudelib.wire.said(reply)))
                return
            for listed in (reply.get("session"), reply.get("thread")):
                if isinstance(listed, dict) and baudelib.wire.is_id(listed.get("id") or ""):
                    if listed.get("rotated") != (r.sessions.get(listed["id"]) or {}).get("rotated"):
                        r.forget_mirror(listed["id"])
                    r.sessions[listed["id"]] = listed
            r.network.changed()
        r.request({"op": "rotate", "session": info["id"]}, done)

    def bot_set(self, info, reply):
        """A host answered Make Bot…, Bot Settings…, a Bot's Rename… or Turn Back into a Chat: listed as it answered
        (as renamed_remote lists a rename), not as it was till the host next says what it has."""
        if reply.get("op") == "error":
            return self.win.note(wording.say("bot_settings.failed", why=baudelib.wire.said(reply)))
        listed = reply.get("session")
        if isinstance(listed, dict) and listed.get("id") == info["id"]:
            info["remote"].sessions[info["id"]] = listed
        self.fill()

    def rename(self, info, title):
        title = " ".join((title or "").split())
        if not title or title == info.get("title"):
            return
        win = self.win
        if (r := info.get("remote")) is not None:  # (on a server: its server renames it, and lists it so)
            r.request({"op": "rename", "session": info["id"], "title": title},
                      lambda reply: self.renamed_remote(r, info, title, reply))
            return
        if info["id"] == win.session_id and win.proc and not win.resume_pending:
            win.request({"subtype": "rename_session", "title": title}, lambda _r: None)  # the live session
        else:  # what the CLI itself writes for a rename
            with open(info["path"], "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": info["id"]},
                                   separators=(",", ":")) + "\n")
        if info["id"] == win.session_id:
            win.show_title(title)
        self.refresh()

    def renamed_remote(self, r, info, title, reply):
        """A network session's server answered a rename: listed by its new title here before it next says what it has
        (the title it has there: its claude may have made it its own)."""
        if reply.get("op") != "renamed":
            toast(self, wording.say("sidebar.host_didnt_rename", host=remote_name(r), why=baudelib.wire.said(reply)))
            return
        listed = reply.get("session") or {}
        if listed.get("title") in (None, info.get("title")):  # (its claude's yet to write it: as asked, till it has)
            listed = {**listed, "title": title}
        if listed.get("id") == info["id"]:
            r.sessions[info["id"]] = listed
        if info["id"] == self.win.session_id and self.win.remote is r:
            self.win.show_title(listed["title"])
        self.fill()

    def archive(self, info):
        if info and info.get("remote") is not None:  # (a host's: archived there, for every head)
            self.ask_remote(info, "archive")
            return
        if info:
            st = load_settings()
            save_settings(archived=sorted(set(st.get("archived", [])) | {info["id"]}),
                          unarchived=sorted(set(st.get("unarchived", [])) - {info["id"]}))
            self.fill()

    def unarchive(self, info):
        if info and info.get("remote") is not None:  # (archived here, before hosts did, too: not any more)
            st = load_settings()
            if info["id"] in st.get("archived", []):
                save_settings(archived=sorted(set(st.get("archived", [])) - {info["id"]}))
            if info.get("archived"):
                self.ask_remote(info, "unarchive")
            else:
                self.fill()
            return
        if info:
            st = load_settings()  # (archived in Claude Desktop: shown here anyway from now on)
            save_settings(archived=sorted(set(st.get("archived", [])) - {info["id"]}),
                          unarchived=sorted(set(st.get("unarchived", [])) | {info["id"]}))
            self.fill()

    def ask_trash(self, info):
        if info is None:
            return
        n = info.get("copies", 0)
        r = info.get("remote")
        where = wording.say("trash.yours") if r is None or host_key(r) == HERE_HOST else \
            wording.say("trash.on_host", host=remote_name(r))
        body = wording.say("trash.body_copies", n=n, where=where) if n else wording.say("trash.body", where=where)
        if model_program(info.get("model")) == "codex":
            body += " " + wording.say("trash.codex_copy")
        dlg = Adw.AlertDialog(heading=wording.say("trash.heading", title=chat_name(info) or
                                                  wording.say("trash.this_session")), body=body,
                              default_response="cancel", close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("trash", wording.say("trash.move"))
        dlg.set_response_appearance("trash", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda d, r: r == "trash" and self.trash(info))
        self.dialog = dlg
        dlg.present(self.win)

    def ask_remote(self, info, op):
        """A host's session archived, unarchived or deleted there (wire's archive, unarchive, delete): as it says it's
        done, listed so here; the open chat, if it's the one deleted, left for a new one."""
        r = info["remote"]

        def done(reply):
            if reply.get("op") == "error":
                toast(self, wording.say("trash.host_refused", host=remote_name(r), op=op,
                                        why=baudelib.wire.said(reply)))
                return
            if op == "delete":
                r.sessions.pop(info["id"], None)
                self.win.carry_on(info["id"])
            elif info["id"] in r.sessions:
                r.sessions[info["id"]] = {**r.sessions[info["id"]], "archived": op == "archive"}
            self.fill()
        r.request({"op": op, "session": info["id"]}, done)

    def trash(self, info):
        if info.get("remote") is not None:  # (a host's: moved to the trash there, by its host process)
            self.ask_remote(info, "delete")
            return
        for path in [info["path"], *info.get("copy_paths", [])]:
            try:
                Gio.File.new_for_path(path).trash(None)
            except GLib.Error:
                # no trash for this filesystem (e.g. /tmp): keep it recoverable in baude's own folder
                base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
                keep = os.path.join(base, "baude", "trash")
                os.makedirs(keep, exist_ok=True)
                try:
                    shutil.move(path, os.path.join(keep, os.path.basename(path)))
                    self.win.note(wording.say("chat_notes.no_trash", name=os.path.basename(path), folder=keep))
                except OSError as e:
                    self.win.note(wording.say("chat_notes.couldnt_move", name=os.path.basename(path), why=str(e)))
        self.win.carry_on(info["id"])
        self.refresh()

    def new_hosts(self):
        """[(key, name)]: New session's hosts: this machine (local), then every other known, by its name, verbatim (one
        that's gone or deactivated too: picked, it says so, show_host_state); and the one picked, by the name it had,
        while it isn't known (forgotten, or a mesh member not listed yet)."""
        out = [(key, name) for key, name, _note in self.known_hosts()]
        if (kept := self.kept_host()) is not None and kept not in dict(out):
            out.append((kept, load_settings().get("new_session_host_name") or kept))
        return out

    def kept_host(self):
        """settings.json's new_session_host (None: this machine), by the key it has now if it's had another (kept so,
        for the next run, which won't know the old one)."""
        kept = load_settings().get("new_session_host")
        if (now := next((host_key(r) for r in network().remotes() if kept in getattr(r, "was", ())), kept)) != kept:
            save_settings(new_session_host=now)
        return now if isinstance(now, str) and now != HERE_HOST else None

    def shown_host(self):
        """The host New session's on, and whose chats are listed: the one picked (kept_host), across restarts and while
        it's gone or not known; else this machine."""
        return self.kept_host() or HERE_HOST

    def shown_remote(self):
        """The host picked's Remote (None: this machine)."""
        key = self.shown_host()
        return None if key == HERE_HOST else next((r for r in network().remotes() if host_key(r) == key), None)

    def host_state(self, r):
        """What's in the way of the host picked (a Remote; None: this machine), as a chat on it says it in its banner
        (banner_text), or None: it's there."""
        if r is None:
            key = self.shown_host()
            return None if key == HERE_HOST else wording.say("hosts.looking", host=dict(self.new_hosts()).get(key, key))
        if not r.deactivated and (said := stopping_said(r)) is not None:  # (stopping: said as it goes)
            return said
        if r.state == "alive" and not r.deactivated:
            return None
        name = remote_name(r)
        if r.deactivated:
            return wording.say("hosts.deactivated", host=name)
        if r.state == "dead":  # (why it's out of reach, as the last try to reach it said)
            return wording.say("hosts.down_because", host=name, why=r.error) if r.error else \
                wording.say("hosts.down", host=name)
        return wording.say("hosts.looking", host=name)

    def show_host_state(self):
        """The host picked isn't there (or is deactivated): said in the note over its chats, as they were last
        seen."""
        said = self.host_state(self.shown_remote())
        if said is not None:
            self.host_note.set_title(GLib.markup_escape_text(said))
        self.host_note.set_revealed(said is not None)

    def pick_host(self, key):
        """A host picked in New session's dropdown: new sessions made on it, and its chats listed (its name kept too,
        for while it isn't known: new_hosts)."""
        if getattr(self.win, "agent_draft", None) is not None:
            self.win.new_agent_host_changed(key)
            return
        save_settings(new_session_host=key, new_session_host_name=dict(self.new_hosts()).get(key))
        self.show_host_state()
        self.fill()

    def fill_new_hosts(self):
        """New session's host, as the network has them now, on the one picked (shown_host)."""
        hosts = self.new_hosts()
        agent = getattr(self.win, "agent_draft", None)
        if agent is not None and agent["host_key"] not in dict(hosts):
            hosts.append((agent["host_key"], agent.get("host_name") or agent["host_key"]))
        self.host_dd.set_choices(hosts)
        self.host_dd.select(agent["host_key"] if agent is not None else self.shown_host())
        self.host_dd.set_dimmed({key for key, _name, note in self.known_hosts() if note == "gone"})  # (out of reach:
        # greyed, and picked, why's said: show_host_state)
        (self.host_dd.add_css_class if len(hosts) == 1 else self.host_dd.remove_css_class)("one-provider")  # (dimmed:
        # no other host to pick)

    def new_group(self, host=None):
        """The group a new chat on a host (its key; None: the one picked beside New session) is put in: the one group
        picked in ⋮, or, on the host whose chats are listed, the one group they're in (fill's listed_groups: the
        others picked have none listed); with several, the open chat's if it's one of them, else none ("")."""
        picked, listed = self.group_filter(), getattr(self, "listed_groups", set())
        if picked is not None and len(picked) == 1:
            return next(iter(picked))
        if len(listed) == 1 and (host or self.shown_host()) == getattr(self, "listed_host", None):
            return next(iter(listed))
        if picked is None:
            return ""
        group = self.open_group()
        return group if group in picked else ""

    def open_group(self, assigned=None):
        """The open chat's group (chat_group): a new one's, not written in yet, the group it was started in."""
        win, sid = self.win, self.win.session_id
        r = win.remote
        if sid is None or r is not None and r.sessions.get(sid, {}).get("draft"):
            return getattr(win, "draft_group", "")
        if r is not None and sid in r.sessions:
            return chat_group(remote_info({**r.sessions[sid], "server": r}), assigned)
        return chat_group({"id": sid}, assigned)

    def put_in(self, group):
        """A new chat's group (new_group) picked in ⋮ too, if it isn't (no group, say), so the chat's listed."""
        picked = self.group_filter()
        if picked is not None and group not in picked:
            self.filtered(sidebar_groups=sorted(picked | {group}))

    def open_folder(self):
        """Open folder…: a folder chooser here (pick_folder); with another host picked beside New session, a folder on
        it (ask_host_folder); if that host is gone, deactivated or not known, a toast says why."""
        key = self.host_dd.value() or HERE_HOST
        agent = getattr(self.win, "agent_draft", None)
        if agent is not None:
            r = agent.get("host")
            if key == HERE_HOST:
                self.win.pick_agent_folder()
            elif r is not None and r.state == "alive" and not r.deactivated:
                self.win.ask_host_folder(r, agent_draft=True)
            else:
                toast(self, self.host_state(r))
            return
        if key != HERE_HOST and (said := self.host_state(r := self.shown_remote())) is not None:
            toast(self, said)
        elif key != HERE_HOST and r is not None:
            self.win.ask_host_folder(r)
        else:
            self.win.pick_folder()

    def new_chat(self):
        """New session (and Ctrl+N): a chat on the host picked beside it (new_session_on), else here. With groups
        picked in ⋮, or only one group's chats listed, it's in one of them (new_group), a draft like any other till
        it's written in, so its folder and model can still be changed (new_session's group)."""
        key = self.host_dd.value() or HERE_HOST
        if key != HERE_HOST and (said := self.host_state(r := self.shown_remote())) is not None:  # (gone, deactivated,
            # not known)
            toast(self, said)
        elif key != HERE_HOST and r is not None:
            self.win.new_session_on(r)
        else:
            group = self.new_group(HERE_HOST)
            self.put_in(group)  # (as remote_started does)
            self.win.switch_to(lambda: self.win.new_session(group=group))

    def hide_remote(self, info):
        """Delete a network session that isn't at a turn: its transcript is its server's, so it's only not listed here
        till it shows up again (unhide_remote). Archive keeps it out of the list for good."""
        if info is None or info.get("remote") is None:
            return
        hidden = self.hidden_remote()
        hidden[info["id"]] = {"updated": info.get("mtime") or 0, "dead": bool(info.get("dead")), "at": time.time()}
        save_settings(hidden_remote=hidden)
        if info["id"] == self.win.session_id:
            self.win.new_session()
        self.fill()

    def stop_remote(self, info):
        """Stop a network session's claude, at its turn or not (its server keeps it, to be carried on with)."""
        if info is not None and info.get("remote") is not None:
            info["remote"].request({"op": "stop", "session": info["id"]})

    def set_state(self, state):
        """The open session's dot (the others follow their files: see file_state)."""
        row = self.list.get_selected_row()
        if row is not None and getattr(row, "dot", None) is not None:
            row.dot.set_state(state)

    def file_state(self, info):
        """A session baude isn't running: working while another program (Claude Desktop, a
        terminal) is in the middle of a turn in it, done for a minute after, else inactive. One left mid-turn, its
        claude going on in the background: working, or waiting while it asks you something."""
        if (p := background_proc(info.get("id"), type(self.win))) is not None:
            return "waiting" if p.asks else "working"
        if info.get("remote") is not None:  # (a network session: as its server last said; between turns, done while
            # its last turn's end hasn't been seen on any head of the mesh)
            return "inactive" if info.get("dead") else \
                {"working": "working", "waiting": "waiting"}.get(info.get("remote_state"),
                                                                 "done" if info.get("unseen") else "inactive")
        path = info.get("path")
        try:
            mtime = os.stat(path).st_mtime
        except (OSError, TypeError):
            return "inactive"
        quiet = time.time() - mtime
        if quiet > TURN_OPEN_SECONDS:
            return "inactive"
        cached = self.turn_cache.get(path)
        if cached is None or cached[0] != mtime:
            cached = self.turn_cache[path] = (mtime, transcript_turn_open(path))
        return external_state(quiet, cached[1])

    def watch_rows(self):
        """File events for the listed sessions, so a dot changes the moment its session does
        (no polling); a timer only runs while some dot needs to expire after its writes stop."""
        rows = {r.info["path"]: r for r in self.session_rows() if r.info.get("remote") is None}
        for path in set(self.monitors) - set(rows):
            self.monitors.pop(path).cancel()
        for path in set(rows) - set(self.monitors):
            try:
                mon = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
            except GLib.Error:
                continue
            mon.set_rate_limit(250)
            mon.connect("changed", lambda *_a, p=path: self.restate(p))
            self.monitors[path] = mon
        self.schedule_expiry()

    def session_rows(self):
        row, out = self.list.get_first_child(), []
        while row is not None:
            info = getattr(row, "info", None)
            if info and info.get("path") and not info.get("unsaved") and info["id"] != self.win.session_id:
                out.append(row)
            row = row.get_next_sibling()
        return out

    def restate(self, path=None):
        busy = False
        for row in self.session_rows():
            if path is None or row.info["path"] == path:
                row.dot.set_state(self.file_state(row.info))
            busy = busy or row.dot.state in ("working", "done")
        return busy

    def schedule_expiry(self):
        if self.expiry is None and self.restate():
            def tick():
                if self.restate():
                    return True
                self.expiry = None
                return False
            self.expiry = GLib.timeout_add_seconds(5, tick)

    def on_activate(self, _list, row):
        if getattr(row, "more_key", None):
            self.expanded.add(row.more_key)
            self.fill()
            return
        info = getattr(row, "info", None)
        if info is None or info.get("ghost"):
            return  # (a ghost: a chat Meta's deleted, its transcript in Baude's trash)
        self.win.show_chat()  # (from the Feedback tab or a file's code: the chat picked's shown, a network one's while
        # its transcript's fetched, this one too)
        if info.get("remote") is not None and not info.get("unsaved") and info["id"] != self.win.session_id:
            self.win.open_remote_soon(info)  # (its transcript fetched first)
            return
        self.win.opening_remote = None  # (another picked meanwhile, this one too: not opened after all)
        if info.get("unsaved") or info["id"] == self.win.session_id:
            return
        self.win.switch_to(lambda: self.win.resume_session(info))

    def focus_search(self):
        self.search.grab_focus()
