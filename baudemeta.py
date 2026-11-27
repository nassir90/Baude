"""baudemeta — Meta in Baude: its prompt and tools, its store of chats, the hosts' folders and names @ lists
(HostFolder, MetaNames), and BaudeMeta.
"""
import contextlib
import json
import os
import re
import shutil
import weakref
import sys
import time
import uuid

import lookup
from baudelib.claude import claude_exe
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from lookup import CATALOGUE_TTL

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

import fixview  # noqa: E402  (☰ → Ongoing Feedback: the fixes, approved or rejected)
import meta  # noqa: E402  (Meta, the helper in the side panel: Record's too; see meta.py)
import metalook  # noqa: E402
import metachat  # noqa: E402
import metaagent  # noqa: E402  (a tool that answers later: Later)
import inboxview  # noqa: E402  (the Inbox: the ask selected, for read_ask and fill_ask)
from widgets import load_settings, pretty_path, save_settings, time_ago, toast  # noqa: E402,F401
from markdownview import open_code  # noqa: E402,F401

from baudelib.client import chat_name  # noqa: E402
from sessionlist import (  # noqa: E402
    HERE_HOST, THIS_MACHINE, chat_group, chat_groups, host_key, is_desktop, last_used, made_groups, network,
    remote_info, remote_name, remote_path,
)


# ------------------------------------------------------------- side panel --

# ------------------------------------------------------------------ Meta --
# Meta (meta.py, Record's too), the side panel's first page: Baude's helper, which changes the sidebar (groups, hosts
# listed, archiving, deleting and renaming chats, how they're grouped) and the settings, each change an action under
# its reply, undone there, and marked in the sidebar till it's kept or undone (SessionSidebar.session_row).

BAUDE_PROMPT = """Chats: each has an id (its first 8 characters will do) and a title, and is in a folder, on a host and \
in a group. Hosts are the machines chats run on; groups are the user's own division of the chats into whole lists. \
The left bar lists one host's chats, the one picked beside New session, which is the user's to pick: to show them \
another's, show button:host (items: its name). ⋮ there picks which groups are listed: show_groups (none given: all \
of them). move_chats puts chats \
in a group (made if it's new; "" for none, whatever host they're on); make_group, rename_group and delete_group change \
the groups. highlight_chats highlights chats in the list without changing them: use it to show which you mean before a \
change the user should check. archive_chats and unarchive_chats; delete_chats moves chats of this machine's own (not \
a host's sessions) to Baude's trash, where undo finds them; a host's session you don't delete: show button:trash. How \
the list is grouped (by folder, by the day each chat was made, or by the day it was last used) is the setting \
group_by.

Inbox: tasks ask the user things there (a deploy to approve, a choice to make), each ask a message with a form and the \
task's buttons. read_ask reads the ask the user has selected (its body, buttons, form and the values in it now, its \
task's run log and its Bot's chat); fill_ask fills its form for them to check. Pressing a button answers it: that's \
always the user's to do, never yours."""

BAUDE_TOOLS = [
    {"name": "highlight_chats", "description": "Highlight chats in the left bar, changing nothing, to show the user "
     "which you mean (till their next message).",
     "inputSchema": {"type": "object", "required": ["chats"], "properties": {
         "chats": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "move_chats", "description": "Put chats in a group (a new name makes it; \"\" for no group).",
     "inputSchema": {"type": "object", "required": ["chats", "group"], "properties": {
         "chats": {"type": "array", "items": {"type": "string"}}, "group": {"type": "string"}}}},
    {"name": "make_group", "description": "Make a group, empty till chats are moved to it.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}},
    {"name": "rename_group", "description": "Rename a group; to a group there already, its chats join that one.",
     "inputSchema": {"type": "object", "required": ["group", "name"], "properties": {
         "group": {"type": "string"}, "name": {"type": "string"}}}},
    {"name": "delete_group", "description": "Delete a group; its chats are in no group then.",
     "inputSchema": {"type": "object", "required": ["group"], "properties": {"group": {"type": "string"}}}},
    {"name": "show_groups", "description": "Which groups the left bar lists (\"No group\" for chats in none; none "
     "given: all).",
     "inputSchema": {"type": "object", "required": ["groups"], "properties": {
         "groups": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "archive_chats", "description": "Archive chats: listed under Archived, out of the way.",
     "inputSchema": {"type": "object", "required": ["chats"], "properties": {
         "chats": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "unarchive_chats", "description": "Bring archived chats back to the list.",
     "inputSchema": {"type": "object", "required": ["chats"], "properties": {
         "chats": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "read_ask", "description": "The ask the user has selected in the Inbox (its tab, or the one last touched in "
     "the list): its body, buttons (by index), form fields and the values in them now, its task, the end of its run's "
     "log and of its Bot's chat.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "fill_ask", "description": "Fill fields of the selected ask's form for the user to check: values "
     "{field: value}. It never presses a button (that answers the ask: the user's to do). Returns what didn't fit.",
     "inputSchema": {"type": "object", "required": ["values"], "properties": {"values": {"type": "object"}}}},
    {"name": "delete_chats", "description": "Delete chats of this machine's own: their transcripts go to Baude's "
     "trash, and undo brings them back. Not a host's session, nor the chat open.",
     "inputSchema": {"type": "object", "required": ["chats"], "properties": {
         "chats": {"type": "array", "items": {"type": "string"}}}}},
]
META_KEEP = 400  # messages and actions kept in meta.json, each (the oldest go)
_META = []


def meta_path():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "meta.json")


def meta_store():
    """Baude's conversation with Meta: its messages and its actions (the journal undo works from), one for the app,
    every window's, kept in Baude's data folder's meta.json."""
    if not _META:
        try:
            with open(meta_path()) as f:
                kept = json.load(f)
        except (OSError, ValueError):
            kept = {}
        _META.append({"messages": list(kept.get("messages") or []), "actions": list(kept.get("actions") or [])})
    return _META[0]


def save_meta_store():
    store, path = meta_store(), meta_path()
    for key in ("messages", "actions"):
        del store[key][:-META_KEEP]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump(store, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError as e:
        print(f"baude: Meta's conversation not kept: {e}", file=sys.stderr)


def meta_trash():
    """Where Meta's delete_chats puts transcripts: Baude's own trash folder (Move to Trash's, where there's no
    system trash), a folder each."""
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "trash")


def all_chats(sidebar):
    """Every chat Baude knows: this machine's own (the sidebar's), and the hosts' sessions."""
    net = [remote_info(s) for s in network().sessions() if not s.get("draft")]  # (a new chat not written in yet)
    theirs = {i for s in network().sessions() for i in (s["id"], s.get("cli_id")) if i}
    return net + [i for i in (sidebar.sessions if sidebar is not None else []) if i["id"] not in theirs]


class HostFolder:
    """A folder on another host, as SearchSpace reads a Signal: its names (lookup.items) made there and fetched
    (catalogue, wire.py), afresh once they're CATALOGUE_TTL old; catalogue_busy while they're on their way. One per host
    and folder (on)."""

    _instances = {}

    @classmethod
    def on(cls, remote, folder):
        if (remote.key, folder) not in cls._instances:
            cls._instances[(remote.key, folder)] = cls(remote, folder)
        found = cls._instances[(remote.key, folder)]
        found.remote = remote  # (rekeyed, reached again: the one there is now)
        return found

    def __init__(self, remote, folder):
        self.remote, self.root = remote, folder
        self.refs, self.catalogue, self.catalogue_at, self.catalogue_busy = {}, (), 0.0, False

    def catalogue_soon(self):
        if not self.catalogue_busy and time.time() - self.catalogue_at > CATALOGUE_TTL:
            self.catalogue_busy = True
            self.remote.request({"op": "catalogue", "cwd": self.root}, self._got)

    # (what a list reads of an item, should a host send less: lookup.items')
    DEFAULTS = {"canonical": "", "faint": False, "icon": "code", "what": "Name", "summary": "", "kind": "symbol"}

    def _got(self, reply):
        ok = reply.get("op") == "catalogue" and not reply.get("error") and isinstance(reply.get("items"), list)
        if ok:
            here = host_key(self.remote)  # (that host's: its paths are paths there)
            self.catalogue = tuple(
                {**self.DEFAULTS, **{k: v for k, v in it.items() if isinstance(v, type(self.DEFAULTS.get(k, v)))},
                 "path": None, "line": None, "host": here,
                 "host_path": it.get("path") if isinstance(it.get("path"), str) else None,  # (there, for ⌘K: read_file)
                 "host_line": it.get("line") if isinstance(it.get("line"), int) else None}
                for it in reply["items"] if isinstance(it, dict) and isinstance(it.get("surface"), str))
        if ok and reply.get("busy"):  # (not all made yet: what there is listed, the rest waited for there)
            return self.remote.request({"op": "catalogue", "cwd": self.root, "wait": True}, self._got)
        # (the host not reached: asked again soon; anything else, a refusal too, once it's old)
        gone = reply.get("op") == "error" and reply.get("error") in GONE
        self.catalogue_at = time.time() - (CATALOGUE_TTL - 5 if gone else 0)
        self.catalogue_busy = False


GONE = ("the server isn't there", "the server's gone", "the server didn't answer")  # (baudelib.client's)


class HostSpace(lookup.SearchSpace):
    """SearchSpace over a HostFolder: the items as they came."""

    def make_items(self):
        return list(self.source.catalogue)


class MetaNames(lookup.SearchSpace):
    """What @ lists in Meta's box and a chat's: every host's chats by their titles (all_chats), then the names the open
    chat's folder has (the glossary's search: SearchSpace, of catalogue_signal; a chat on another host's, made there:
    HostSpace). Those of the open chat's host come first, other hosts' chats after them."""

    def __init__(self, win):
        super().__init__(None, GLib.timeout_add)
        self.win, self._chats, self._space = win, (None, None, []), None

    def host(self):
        return self.win.chat_host()

    def folder_space(self):
        win = self.win
        if win.chat_host() != HERE_HOST:
            folder = win.remote_folder()
            source = HostFolder.on(win.remote, folder) if folder else None
            make = HostSpace
        else:
            source, make = win.catalogue_signal(), SearchSpace
        if source is None:
            return None
        if self._space is None or self._space.source is not source:
            self._space = make(source)
        return self._space

    @property
    def root(self):
        return self.win.cwd

    @property
    def busy(self):
        return (space := self.folder_space()) is not None and space.busy

    def refresh(self, on_ready=None):
        return space.refresh(on_ready) if (space := self.folder_space()) is not None else None

    def search(self, q, limit=None):
        if isinstance(space := self.folder_space(), HostSpace):  # (the list up: another host's names asked for)
            space.source.catalogue_soon()
        return super().search(q, limit)

    def later(self, item):
        return item.get("host", self.host()) != self.host()

    def chat_items(self):
        sidebar = getattr(self.win, "sidebar", None)
        sessions = sidebar.sessions if sidebar is not None else None
        net = tuple((x["id"], x["server"].key, x.get("title"), (x.get("bot") or {}).get("name"), x.get("cwd"),
                     x.get("said"), x.get("draft")) for x in network().sessions())
        if self._chats[0] is not sessions or self._chats[1] != net:
            items = []
            for info in sorted(all_chats(sidebar), key=last_used, reverse=True):
                name = chat_name(info)
                if not name:
                    continue
                r = info.get("remote")
                host = host_key(r) if r is not None else HERE_HOST
                folder = info.get("folder") or info.get("cwd") or ""
                at = last_used(info)
                where = (remote_path(folder, r) if r is not None else pretty_path(folder)) or \
                    wording.say("chats.no_folder")
                if host != HERE_HOST:  # (another host's: by its name, as the status line says it)
                    where = f"{remote_name(r)} · {where}"
                items.append({"surface": name, "canonical": "", "icon": "claude",
                              "what": wording.say("chats.chat"),
                              "kind": "chat", "at": at, "ago": (ago := time_ago(at)), "where": where,
                              "summary": f"{where} · {ago}", "path": info.get("path"), "id": info["id"], "line": None,
                              "entry": None, "faint": False, "host": host})
            self._chats = (sessions, net, items)
        return self._chats[2]

    def items(self):
        space = self.folder_space()
        parts = (self.chat_items(), space.items() if space is not None else [])
        if self._items is None or any(a is not b for a, b in zip(parts, getattr(self, "_parts", ()))):
            self._items, self._parts, self._by = parts[0] + parts[1], parts, {}
            for it in self._items:
                self._by.setdefault(it["surface"], it)
        return self._items


class BaudeMeta(metachat.MetaChat):
    """Meta in Baude (metachat.MetaChat), the side panel's first page: one conversation for the app (meta_store), each
    window's session its own, started in its chat's folder. Its tools change the sidebar (BAUDE_TOOLS) and the
    settings; what it changes in the list is marked there (SessionSidebar.session_row), a chat it deletes listed still,
    struck through, till that's kept or undone (ghosts)."""

    APP = "Baude"
    WHAT = "a client for Claude Code chats, listed in its left bar."
    LISTED = "the chats"
    CHAT_ROWS = True
    every = weakref.WeakSet()  # (each window's: the conversation's theirs too, shown again as it changes)

    def __init__(self, win):
        super().__init__(win)
        self.names = MetaNames(win)
        self.settings = self.make_settings()
        BaudeMeta.every.add(self)
        self.render()

    # ---- what metachat.MetaChat asks of Baude
    def messages(self):
        return meta_store()["messages"]

    def actions(self):
        return meta_store()["actions"]

    def save(self, now=False):
        save_meta_store()
        for other in list(BaudeMeta.every):  # (another window's, not at a turn: shown as it is now)
            if other is not self and other.ask is None and not getattr(other.win, "let_go_done", False):
                GLib.idle_add(other.render)

    def told(self):
        return meta_store().setdefault("told", [])

    def can_revert(self):
        """Not while any window's Meta is answering: the conversation's theirs too."""
        return super().can_revert() and all(o.ask is None for o in list(BaudeMeta.every))

    def close_agents(self):
        """Every window's session stopped (a revert sent): each one's next message starts one told what's left."""
        for other in list(BaudeMeta.every):
            other.close_agent()

    def space(self):
        return self.names

    def scope(self):
        return self.win.cwd

    def agent_setup(self):
        cwd = self.win.cwd if self.win.cwd and os.path.isdir(self.win.cwd) else os.path.expanduser("~")
        return {"cwd": cwd, "roots": [self.win.cwd] if self.win.cwd else [], "exe": claude_exe()}

    def app_prompt(self):
        return BAUDE_PROMPT

    def app_tools(self):
        return BAUDE_TOOLS

    def app_handlers(self):
        return {"highlight_chats": self.highlight_chats, "move_chats": self.move_chats, "make_group": self.make_group,
                "rename_group": self.rename_group, "delete_group": self.delete_group, "show_groups": self.show_groups,
                "archive_chats": lambda t, a: self.archive_chats(t, a, True),
                "unarchive_chats": lambda t, a: self.archive_chats(t, a, False), "delete_chats": self.delete_chats,
                "read_ask": self.read_ask, "fill_ask": self.fill_ask}

    # ---- the Inbox
    def _selected_ask(self):
        got = self.win.selected_ask() if hasattr(self.win, "selected_ask") else None
        if got is None:
            raise ValueError("No ask is selected: the user hasn't opened or touched one in the Inbox.")
        return got

    def read_ask(self, turn, args):
        """The selected ask, its run's log (asked of its host, off the main loop) and the end of its Bot's chat."""
        ask, view = self._selected_ask()
        text = inboxview.ask_text(ask, view)
        chat = self.find_chat(ask["session"])
        bot = self.chat_text(chat)[-4000:] if chat is not None else "(its Bot's chat isn't here)"
        return metaagent.Later(lambda: {"text": f"{text}\n\n<run_log>\n{inboxview.run_log(ask)}\n</run_log>\n\n"
                                                f'<bot_chat name="{meta.attr(ask.get("bot") or "")}">\n{bot}\n</bot_chat>'})

    def fill_ask(self, turn, args):
        ask, view = self._selected_ask()
        if view is None:
            self.win.open_ask(ask["server"].key, ask["id"])
            ask, view = self._selected_ask()
        if view is None or not view.live():
            raise ValueError("That ask can't be filled in now: it isn't waiting.")
        values = args.get("values") if isinstance(args.get("values"), dict) else {}
        wrong = view.fill(values)
        if wrong:
            return {"text": "Filled what fits; not: " + "; ".join(f"{k}: {v}" for k, v in wrong.items())}
        return {"text": "Filled: the user checks it and presses a button."}

    def make_settings(self):
        win = self.win
        out = meta.shared_settings(win, self, "Baude", lambda: win.zoom_default)
        by = ["folder", "created", "updated"]

        def group_by(v):
            if (sidebar := getattr(win, "sidebar", None)) is not None:
                sidebar.actions.lookup_action("group").change_state(GLib.Variant.new_string(v))
            else:
                save_settings(sidebar_group=v)

        def row():
            r = Adw.ComboRow(title=wording.say("sidebar.group_by"),
                             model=Gtk.StringList.new([wording.say("common.folder"), wording.say("sidebar.created"),
                                                       wording.say("sidebar.updated")]),
                             selected=by.index(now()))
            r.connect("notify::selected", lambda r, _p: group_by(by[r.get_selected()]))
            return r
        now = lambda: "updated" if (b := load_settings().get("sidebar_group")) == "date" else b if b in by \
            else "folder"  # noqa: E731
        out["group_by"] = meta.Setting("group_by", wording.say("sidebar.group_by"), now, group_by, row, by,
                                       note="the left bar's chats, under their folders, the days they were made, or "
                                            "the days they were last used")

        return out

    def marks_changed(self):
        if (sidebar := getattr(self.win, "sidebar", None)) is not None:
            sidebar.fill()

    def ghosts(self):
        """The chats Meta's deleted and that aren't kept so: listed still, struck through, where they were."""
        return [{**info, "ghost": True} for a in self.unkept() for p in metalook.action_parts(a)
                if p["kind"] == "delete_chats" for info in p["redo"].get("infos") or ()]  # (a bundle's parts too)

    # ---- chats
    def all_infos(self):
        return all_chats(getattr(self.win, "sidebar", None))

    def find_chat(self, ref):
        ref = ref.strip().removeprefix("s:")
        if not ref:
            return None
        infos = self.all_infos()
        for test in (lambda i: i["id"] == ref, lambda i: len(ref) >= 4 and i["id"].startswith(ref),
                     lambda i: (chat_name(i) or "").lower() == ref.lower()):
            found = [i for i in infos if test(i)]
            if len(found) == 1:
                info = found[0]
                return {"key": info["id"], "title": chat_name(info) or wording.say("common.untitled"),
                        "path": info.get("path"),
                        "info": info}
        return None

    def _infos(self, refs):
        infos = [self._chat(ref)["info"] for ref in refs or ()]
        if not infos:
            raise ValueError("Which chats? chats is empty.")
        return infos

    @staticmethod
    def said(infos):
        titles = [wording.say("senders.chat", name=chat_name(i) or wording.say("common.untitled")) for i in infos]
        return titles[0] if len(titles) == 1 else wording.say("meta_chats.chats", n=len(titles))

    def open_chat(self, info):
        """A chat's row clicked (show chats): it's opened, as the sidebar opens one."""
        win = self.win
        # (from the Feedback tab or a file's code: the chat picked's shown, a network one's while its transcript's
        # fetched; another picked meanwhile isn't opened after all, this one picked again too)
        win.show_chat()
        if info["id"] == win.session_id:
            win.opening_remote = None
            return
        if info.get("remote") is not None:
            win.open_remote_soon(info)
        else:
            win.opening_remote = None  # (another host's, still being fetched: not opened after all, as the sidebar's)
            win.switch_to(lambda: win.resume_session(info))

    def chats_rows(self, refs):
        rows, assigned = [], chat_groups()
        for ref in refs:
            info = self._chat(ref)["info"]
            group = chat_group(info, assigned)
            in_group = group and wording.say("meta_chats.group_named", name=group)
            title = chat_name(info) or wording.say("common.untitled")
            rows.append({"icon": "baude-claude-symbolic", "title": title,
                         "subtitle": " · ".join(filter(None, [pretty_path(info.get("folder") or info.get("cwd") or ""),
                                                             time_ago(last_used(info)), in_group])),
                         "open": lambda info=info: self.open_chat(info)})
        return rows

    def rename_chat_to(self, chat, title):
        self.win.sidebar.rename({**chat["info"], "title": None}, title)  # (its title as listed may be one behind)

    def forms(self):
        win = self.win

        def spawn():  # (this machine's host first; none there: it, started as the agent's started)
            return win.spawn_form(sorted(network().alive(), key=lambda r: host_key(r) != HERE_HOST) or [None])
        return {"add_host": ("add a host Baude reaches over ssh", win.host_form),
                "new_group": ("make a group of chats", win.group_form),
                "spawn_agent": ("start an agent (a session) on a host: the host, then what it offers to run, the "
                                "folder, group and prompt", spawn)}

    def open_mark(self, kind, key, how, action):
        """A chat in a change's box clicked: opened, as the sidebar opens one; one deleted is in Baude's trash."""
        if (chat := self.find_chat(key)) is not None:
            self.open_chat(chat["info"])
        elif how == "removed":
            toast(self, "It's in Baude's trash: undo brings it back")

    def chat_text(self, chat):
        path = chat.get("path")
        return meta.transcript_text(path) if path and os.path.exists(path) else "(its transcript isn't here)"

    def buttons(self):
        return {"trash": "Move to Trash… a chat (items: its id), to the system's trash or its host's; asks first, "
                         "and can't be undone here",
                "host": "Open a host (items: its name): the left bar lists its chats, and New session's on it"}

    def button(self, action, items):
        if action == "host" and items:
            sidebar = self.win.sidebar
            key = next((k for k, n in sidebar.new_hosts() if n.lower() == str(items[0]).strip().lower()), None)
            if key is None:
                raise ValueError(f"No host {items[0]!r}: they're {', '.join(n for _k, n in sidebar.new_hosts())}.")
            btn = Gtk.Button(label=wording.say("meta.open_item", name=dict(sidebar.new_hosts())[key]))
            btn.add_css_class("suggested-action")
            btn.connect("clicked", lambda *_: (sidebar.pick_host(key), sidebar.fill_new_hosts()))
            return btn
        if action != "trash" or not items:
            raise ValueError(f"There's no button {action}{'' if items else ' without items'}: app_index buttons.")
        info = self._chat(items[0])["info"]
        btn = Gtk.Button(label=wording.say("trash.move_title",
                                           title=chat_name(info) or wording.say("common.untitled")))
        btn.add_css_class("destructive-action")
        btn.connect("clicked", lambda *_: self.win.sidebar.ask_trash(self.find_chat(info["id"])["info"]
                                                                     if self.find_chat(info["id"]) else info))
        return btn

    # ---- its tools
    def highlight_chats(self, turn, args):
        infos = self._infos(args.get("chats"))
        self.lit = {**self.lit, "chat": {i["id"] for i in infos}}
        self.marks_changed()
        return {"text": f"Highlighted {self.said(infos)} in the left bar."}

    def move_chats(self, turn, args):
        infos, group = self._infos(args.get("chats")), " ".join(str(args.get("group") or "").split())
        sidebar = self.win.sidebar
        made = bool(group) and group not in {k for k, *_ in sidebar.known_groups()}
        if made:
            sidebar.make_group(group)
        ids = [i["id"] for i in infos]
        before = sidebar.move_to_group(ids, group)
        where = wording.say("meta_chats.group_named", name=group) if group else wording.say("meta_chats.no_group")
        a = self.act(turn, wording.say("meta_chats.moved", chats=self.said(infos), where=where), "move_chats",
                     {"before": before, "group": group, "made": made}, {"ids": ids, "group": group, "made": made},
                     [("chat", i, "moved", wording.say("meta_chats.moved_note", group=group or wording.say("meta_chats.no_group")))
                      for i in ids])
        return {"text": f"Moved to {where} (action {a['id']})."}

    def make_group(self, turn, args):
        name = " ".join(str(args.get("name") or "").split())
        if not name:
            raise ValueError("A group's name is needed.")
        if name in {k for k, *_ in self.win.sidebar.known_groups()}:
            return {"text": f"There's a group {name} already."}
        self.win.sidebar.make_group(name)
        a = self.act(turn, wording.say("meta_chats.made_group", name=name), "groups", {"op": "unmake", "name": name},
                     {"op": "make", "name": name})
        return {"text": f"Made {name} (action {a['id']})."}

    def _group(self, name):
        name = " ".join(str(name or "").split())
        if name.lower() == "no group":
            raise ValueError("No group isn't a group: it's the chats in none.")
        if name not in {k for k, *_ in self.win.sidebar.known_groups()}:
            raise ValueError(f"No group {name!r}: app_index groups lists them.")
        return name

    def rename_group(self, turn, args):
        old, new = self._group(args.get("group")), " ".join(str(args.get("name") or "").split())
        if not new or new == old:
            raise ValueError("A new name is needed.")
        state = self.win.sidebar.rename_group(old, new)
        undo = {"op": "unrename", "old": old, "new": new, "entries": state["chat_groups"],
                "made": old in state["group_names"], "new_made": new in state["group_names"],
                "picked": old in (state["sidebar_groups"] or ()), "new_picked": new in (state["sidebar_groups"] or ())}
        a = self.act(turn, wording.say("meta_chats.renamed_group", old=old, new=new), "groups", undo,
                     {"op": "rename", "old": old, "new": new})
        return {"text": f"Renamed (action {a['id']})."}

    def delete_group(self, turn, args):
        name = self._group(args.get("group"))
        state = self.win.sidebar.delete_group(name)
        undo = {"op": "undelete", "name": name, "entries": state["chat_groups"], "made": name in state["group_names"],
                "picked": name in (state["sidebar_groups"] or ())}
        a = self.act(turn, wording.say("meta_chats.deleted_group", name=name), "groups", undo,
                     {"op": "delete", "name": name})
        return {"text": f"Deleted {name}; its chats are in no group (action {a['id']})."}

    def _filter(self, turn, key, said, known, names):
        """Which groups are listed (⋮), by their names (known: [(key, name, note)]); none: all."""
        by_name = {name.lower(): k for k, name, _note in known}
        keys = []
        for n in names or ():
            if str(n).strip().lower() not in by_name:
                raise ValueError(f"No {said} {n!r}: they're {', '.join(name for _k, name, _n in known)}.")
            keys.append(by_name[str(n).strip().lower()])
        before = load_settings().get(key)
        self.win.sidebar.filtered(**{key: sorted(set(keys)) or None})
        now = load_settings().get(key)
        if now == before:
            return {"text": "That's what's listed already."}
        a = self.act(turn, wording.say("meta_chats.listed", names=", ".join(map(str, names))) if keys else
                     wording.say("meta_chats.listed_every", what=said), "filter",
                     {"key": key, "value": before, "expect": now}, {"key": key, "value": now, "expect": before})
        return {"text": f"Listed (action {a['id']})."}

    def show_groups(self, turn, args):
        return self._filter(turn, "sidebar_groups", "group", self.win.sidebar.known_groups(), args.get("groups"))

    def archive_chats(self, turn, args, archive):
        sidebar, archived = self.win.sidebar, self.win.sidebar.archived_ids()
        infos = [i for i in self._infos(args.get("chats"))
                 if (i["id"] in archived or bool(i.get("archived"))) != archive]
        if not infos:
            return {"text": f"They're {'archived' if archive else 'listed'} already."}
        busy = [i for i in infos if (i.get("remote_state") if i.get("remote") is not None else
                                     sidebar.file_state(i)) in ("working", "waiting")]
        if archive and busy:
            raise ValueError(f"{self.said(busy)} {'is' if len(busy) == 1 else 'are'} at a turn: archiving would stop "
                             "it, which can't be undone. Say so to the user.")
        ids, st = [i["id"] for i in infos], load_settings()
        lists = {i["id"]: [i["id"] in st.get("archived", []), i["id"] in st.get("unarchived", [])]
                 for i in infos if i.get("remote") is None}  # (this machine's: put back as they were, undone)
        self.apply_app("archive", {"ids": ids, "archived": archive, "expect": not archive})
        a = self.act(turn, wording.say("meta_chats.archived", chats=self.said(infos)) if archive else
                     wording.say("meta_chats.unarchived", chats=self.said(infos)), "archive",
                     {"ids": ids, "archived": not archive, "lists": lists, "expect": archive},
                     {"ids": ids, "archived": archive, "expect": not archive},
                     [("chat", i, "archived" if archive else "added",
                      wording.say("meta_chats.archived_note" if archive else "meta_chats.back_note")) for i in ids])
        return {"text": f"{'Archived' if archive else 'Unarchived'} (action {a['id']})."}

    def delete_chats(self, turn, args):
        infos = self._infos(args.get("chats"))
        if hosted := [i for i in infos if i.get("remote") is not None]:
            raise ValueError(f"{self.said(hosted)}: a host's session, whose deleting can't be undone here. Show "
                             "button:trash for the user instead.")
        if desktop := [i for i in infos if is_desktop(i["id"])]:
            raise ValueError(f"{self.said(desktop)}: Claude Desktop's, which keeps its own record of it. Show "
                             "button:trash for the user instead.")
        if any(i["id"] == self.win.session_id for i in infos):
            raise ValueError("One of them is the chat open: the user switches to another first.")
        recent = [i for i in infos if time.time() - (os.path.getmtime(i["path"]) if os.path.exists(i["path"]) else 0)
                  < self.DELETE_QUIET]
        if recent:
            raise ValueError(f"{self.said(recent)}: used in the last {self.DELETE_QUIET // 60} minutes, perhaps open "
                             "elsewhere (another window, a terminal), which would write it again. Say so to the user.")
        folder = os.path.join(meta_trash(), f"meta-{uuid.uuid4().hex[:10]}")
        moved = [[path, os.path.join(folder, info["id"], os.path.basename(path))]
                 for info in infos for path in [info["path"], *info.get("copy_paths", [])] if os.path.exists(path)]
        keep = ("id", "title", "cwd", "folder", "worktree", "mtime", "said", "prompts", "desktop_used")
        infos_kept = [{k: i[k] for k in keep if k in i} for i in infos]
        self.apply_app("delete_chats", {"moved": moved})
        a = self.act(turn, wording.say("meta_app.deleted", what=self.said(infos)), "delete_chats",
                     {"moved": [[dst, src] for src, dst in moved]}, {"moved": moved, "infos": infos_kept},
                     [("chat", i["id"], "removed", "deleted") for i in infos])
        return {"text": f"Deleted: in Baude's trash; undo brings {'it' if len(infos) == 1 else 'them'} back "
                        f"(action {a['id']})."}

    DELETE_QUIET = 600  # s since a chat's transcript was last written before delete_chats takes it

    def _groups_put(self, assigned=None, names=None, picked=...):
        """chat_groups, group_names and sidebar_groups as given (any left out as they are), and the list shown so."""
        changes = {}
        if assigned is not None:
            changes["chat_groups"] = assigned or None
        if names is not None:
            changes["group_names"] = names or None
        if picked is not ...:
            changes["sidebar_groups"] = picked
        save_settings(**changes)
        self.win.sidebar.groups_changed()

    def _entries_back(self, entries, now):
        """Chats' chat_groups entries put back (entries: {id: entry, None: none}), each only while it's now (else
        ValueError: moved since)."""
        assigned, moved = chat_groups(), 0
        for i in entries:
            chat = self.find_chat(i)
            moved += chat_group(chat["info"] if chat else {"id": i}, assigned) != now
        if moved:
            raise ValueError(f"{moved} of its chats moved since")
        for i, entry in entries.items():
            if entry is None:
                assigned.pop(i, None)
            else:
                assigned[i] = entry
        return assigned

    def apply_app(self, kind, data):
        sidebar = self.win.sidebar
        st = load_settings()
        names, picked = made_groups(st), st.get("sidebar_groups") if isinstance(st.get("sidebar_groups"), list) else None
        if kind == "move_chats":
            if "before" in data:  # (undone: each chat back where it was, while it's still where it was moved to)
                assigned = self._entries_back(data["before"], data["group"])
                if data.get("made") and data["group"] not in assigned.values():
                    names = [g for g in names if g != data["group"]]
                self._groups_put(assigned, names)
            else:
                if data["group"] and data["group"] not in {k for k, *_ in sidebar.known_groups()}:
                    sidebar.make_group(data["group"])
                sidebar.move_to_group(data["ids"], data["group"])
        elif kind == "groups":
            op = data["op"]
            if op == "make":
                sidebar.make_group(data["name"])
            elif op == "unmake":
                self._groups_put(names=[g for g in names if g != data["name"]])
            elif op == "rename":
                sidebar.rename_group(data["old"], data["new"])
            elif op == "delete":
                sidebar.delete_group(data["name"])
            elif op == "unrename":
                old, new = data["old"], data["new"]
                assigned = self._entries_back(data["entries"], new)
                names = [g for g in names if g != new or data["new_made"]] + \
                    ([old] if data["made"] and old not in names else [])
                if picked is not None and data["picked"]:
                    picked = sorted({*picked, old} - (set() if data["new_picked"] else {new}))
                self._groups_put(assigned, names, picked)
            elif op == "undelete":
                name = data["name"]
                assigned = self._entries_back(data["entries"], "")
                if data["made"] and name not in names:
                    names = names + [name]
                if picked is not None and data["picked"] and name not in picked:
                    picked = sorted({*picked, name})
                self._groups_put(assigned, names, picked)
        elif kind == "filter":
            if data["key"] != "sidebar_groups":  # (an old show_hosts': the host listed is New session's now)
                raise ValueError("hosts aren't picked in ⋮ any more: the one listed is New session's")
            if load_settings().get(data["key"]) != data["expect"]:
                raise ValueError("what's listed changed since")
            sidebar.filtered(**{data["key"]: data["value"]})
        elif kind == "archive":
            archived_now = sidebar.archived_ids()
            for i in data["ids"]:
                if (chat := self.find_chat(i)) is None:
                    raise ValueError("that chat isn't here any more")
                if (i in archived_now or bool(chat["info"].get("archived"))) != data["expect"]:
                    raise ValueError("archived or unarchived since")
            lists = data.get("lists") or {}
            if lists:  # (this machine's chats: archived and unarchived as they were)
                archived, unarchived = set(st.get("archived", [])), set(st.get("unarchived", []))
                for i, (was_archived, was_unarchived) in lists.items():
                    (archived.add if was_archived else archived.discard)(i)
                    (unarchived.add if was_unarchived else unarchived.discard)(i)
                save_settings(archived=sorted(archived), unarchived=sorted(unarchived))
                sidebar.fill()
            for i in data["ids"]:
                if i in lists:
                    continue
                if (chat := self.find_chat(i)) is None:
                    raise ValueError("that chat isn't here any more")
                (sidebar.archive if data["archived"] else sidebar.unarchive)(chat["info"])
        elif kind == "delete_chats":
            if clash := [dst for _src, dst in data["moved"] if os.path.exists(dst)]:
                raise ValueError(f"{clash[0]} is there again (the chat was written to since); what was deleted is "
                                 f"still at {data['moved'][0][0]}")
            done = []
            try:
                for src, dst in data["moved"]:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                    done.append((src, dst))
            except OSError as e:  # (part way: what was moved, put back)
                for src, dst in reversed(done):
                    with contextlib.suppress(OSError):
                        shutil.move(dst, src)
                raise ValueError(f"couldn't move {os.path.basename(src)}: {e}")
            for src, _dst in data["moved"]:  # (the folders a chat's left in Baude's trash, empty now: gone)
                if src.startswith(meta_trash() + os.sep):
                    for folder in (os.path.dirname(src), os.path.dirname(os.path.dirname(src))):
                        with contextlib.suppress(OSError):
                            os.rmdir(folder)
            sidebar.refresh()
        else:
            raise ValueError(f"No such action: {kind}")

    # ---- the index: Baude's part
    def index_lines(self):
        win, sidebar, lines = self.win, self.win.sidebar, []
        if win.session_id and win.chat_begun():
            lines.append(f"chat open: {win.session_id[:8]} {json.dumps(getattr(win, 'session_title', None) or '')} · "
                         f"{pretty_path(win.cwd or '')} · {win.model or 'default model'} · {win.mode} · "
                         f"{win.session_state()}")
        infos, assigned = self.all_infos(), chat_groups()
        archived = sidebar.archived_ids()
        counts = {}
        for i in infos:
            counts[chat_group(i, assigned)] = counts.get(chat_group(i, assigned), 0) + 1
        hosts, groups = sidebar.host_filter(), sidebar.group_filter()
        lines.append("hosts: " + ", ".join(name + (f" ({note})" if note else "") for _k, name, note in
                                           sidebar.known_hosts()) +
                     f" · listed: {', '.join(n for k, n, _n in sidebar.known_hosts() if k in hosts)}")
        lines.append("groups: " + ", ".join(f"{label} {counts.get(k, 0)}" for k, label, _n in sidebar.known_groups()) +
                     " · listed: " + ("all" if groups is None else
                                      ", ".join(g or "No group" for g in sorted(groups)) or "none"))
        lines.append(f"chats: {len(infos)}, {sum(i['id'] in archived or bool(i.get('archived')) for i in infos)} "
                     f"archived; grouped by {sidebar.grouping()} (app_index chats, filter by a "
                     "word, a folder or a group)")
        return lines

    def running_lines(self):
        win, out = self.win, []
        if win.proc is not None:
            pid = getattr(getattr(win.proc, "p", None), "pid", None)
            out.append(f"the open chat's claude ({win.session_state()}" + (f", pid {pid})" if pid else ")"))
        if busy := [s for s in network().sessions() if s.get("state") in ("working", "waiting")]:
            out.append(f"{len(busy)} host session{'s' if len(busy) != 1 else ''} at a turn: " +
                       ", ".join(f"{s['id'][:8]} {s.get('state')}" for s in busy[:8]))
        panel = win.panel
        for n, said in ((len(panel.agent_rows), "subagent"), (len(panel.proc_rows), "background process"),
                        (len([q for q in panel.side_questions() if q.proc is not None]), "side question")):
            if n:
                out.append(f"{n} {said}{'s' if n != 1 else ''} in the open chat")
        if self.agent is not None and not self.agent.closed:
            out.append(f"{meta.NAME}'s {self.agent.runs} (pid {self.agent.proc.p.pid}, {self.agent.model}"
                       f"{f', {self.agent.effort} effort' if self.agent.effort else ''})")
        return out

    def sections(self):
        sidebar = self.win.sidebar

        def chats(_f):
            assigned, archived = chat_groups(), sidebar.archived_ids()
            return [{"id": i["id"][:8], "title": chat_name(i) or wording.say("common.untitled"),
                     "folder": pretty_path(i.get("folder") or i.get("cwd") or "") or wording.say("chats.no_folder"),
                     "group": chat_group(i, assigned),
                     "host": remote_name(i["remote"]) if i.get("remote") and host_key(i["remote"]) != HERE_HOST else
                     THIS_MACHINE, "ago": time_ago(last_used(i)),
                     "state": i.get("remote_state") if i.get("remote") is not None else sidebar.file_state(i),
                     "archived": i["id"] in archived or bool(i.get("archived")), "account": i.get("account")}
                    for i in sorted(self.all_infos(), key=last_used, reverse=True)]

        def groups(_f):
            picked = sidebar.group_filter()
            return [{"name": label, "listed": picked is None or k in picked} for k, label, _n in sidebar.known_groups()]

        def hosts(_f):
            picked = sidebar.host_filter()
            return [{"name": name, "note": note, "listed": picked is None or k in picked}
                    for k, name, note in sidebar.known_hosts()]
        return {
            "chats": (chats, lambda r: f"{r['id']} {json.dumps(r['title'])} · {r['folder']} · " +
                      (f"group {r['group']} · " if r["group"] else "") + f"{r['host']} · {r['ago']}" +
                      (f" · account {r['account']}" if r["account"] else "") +
                      (f" · {r['state']}" if r["state"] not in (None, "inactive") else "") +
                      (" · archived" if r["archived"] else "")),
            "groups": (groups, lambda r: r["name"] + (" · listed" if r["listed"] else "")),
            "hosts": (hosts, lambda r: r["name"] + (f" ({r['note']})" if r["note"] else "") +
                      (" · listed" if r["listed"] else "")),
        }

    def context(self, agent, msg):
        """What's @-referenced: a chat's conversation, a file's text, or what it is."""
        said = []
        for item in self.referenced(msg["text"]):
            if item.get("kind") == "chat" and item.get("path"):
                said.append(f'<chat title="{meta.attr(item["surface"])}" id="{item.get("id", "")[:8]}">\n'
                            f'{meta.transcript_text(item["path"], 12000)}\n</chat>')
                agent.allowed.add(os.path.realpath(item["path"]))
            elif (path := item.get("path")) and os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        text = f.read(meta.REF_LIMIT + 1)
                except OSError:
                    text = "(not read)"
                said.append(f'<file name="{meta.attr(item["surface"])}" path="{meta.attr(path)}">\n'
                            f'{meta.trimmed(text, meta.REF_LIMIT, path)}\n</file>')
                agent.allowed.add(os.path.realpath(path))
            else:
                said.append(f'<ref name="{meta.attr(item["surface"])}">{meta.attr(item.get("what") or "")}, '
                            f'{meta.attr(item.get("summary") or "")}</ref>')
        return ["<referenced>\n" + "\n".join(said) + "\n</referenced>"] if said else []


def ref_palette_items(signal):
    """What the glossary's search lists (lookup.items: the names Signal looked up, and the catalogue's), each with
    open(win): a looked-up name as its link opens, a catalogued one where it is (ref_palette_open)."""
    items = lookup.items(signal)
    for it in items:
        if it["entry"] is None:
            it["open"] = lambda win, s=it["surface"]: win.open_ref(s, win.title_label)
        else:
            it["open"] = lambda win, n=it["surface"], e=it["entry"], root=signal.root: ref_palette_open(win, n, e, root)
    return items


def ref_palette_open(win, name, entry, root):
    """Open a catalogued name (one no reply has looked up): a running workload or a context in the Kubernetes view,
    filtered to it; a manifest's object or a file at its file; a branch in the Git page; a table in its database's
    diagram; a pull request or an issue on GitHub (or on the Feedback tab, one of Baude's own: fixview.open_github)."""
    if (listed := entry.get("github")) is not None:
        return fixview.open_github(win, listed)
    sources = entry.get("sources") or []
    if entry["kind"] == "k8s":
        running = next((w for what, w in sources if what.endswith(" running")), None)
        if running is not None:
            context, _, namespace = running.partition(" · ")
            return win.kube.open(context=context, search=name, namespace=namespace or None)
        if entry.get("hint") == "context":
            made = next((w for what, w in sources if w), None)  # (a cluster a tool made: its context)
            return win.kube.open(context=made or name)
        path = next((w for _what, w in sources if w), None)
        if path:
            return open_code(os.path.join(root, path), None, win)
    elif entry["kind"] == "branch":
        return win.show_git(rev=name)
    elif entry["kind"] == "file":
        path = sources[0][1] if sources else ""
        if path and not re.fullmatch(r"\d+ files", path):
            return open_code(os.path.join(root, path), None, win)
        return win.ref_toast(f"{path or 'Several files'} are called {name}", show=True)
    elif sources and sources[0][0] == "Database table":
        return win.open_db_name(name, None, "", None, win.title_label, name)
    elif sources and sources[0][0] == "Database":
        return win.schema.open()


class SearchSpace(lookup.SearchSpace):
    """⌘K's search (lookup.SearchSpace) of a Signal, its items opened in this window (ref_palette_items)."""

    def make_items(self):
        return ref_palette_items(self.source)
