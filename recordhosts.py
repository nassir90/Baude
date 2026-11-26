"""recordhosts — Record's window's hosts (RecordHosts, Window's): another machine's notebooks through its host
process (baudelib.notes), listed, written and picked, and the window's notebooks, names, cards, viewers and Meta's
column.
"""
import os
import threading
import time
import urllib.parse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

import lookupgit  # noqa: E402
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; beside this file)
import pickers  # noqa: E402
import meshui  # noqa: E402  (a daemon's pending restart and what it waits for, as Baude shows them)
from baudelib import wording  # noqa: E402  (its words, Baude's: schema/copy)
import baudelib.client  # noqa: E402  (the hosts, as Baude has them: another's notebooks, through its host process)
import baudelib.hosts  # noqa: E402  (this machine's host process started again: Restart Daemon)
import baudelib.serverstate  # noqa: E402  (the sessions this machine's host process keeps: bot_started)
from baudelib import notes  # noqa: E402  (this machine's notebooks, as its host process gives them to another's Record)
import fixview  # noqa: E402  (☰ → Ongoing Feedback, as Baude's)
import viewers  # noqa: E402  (Baude's viewers, in Record's window: beside this file)
from recordstore import (  # noqa: E402
    HERE_HOST, HOME, Store, THIS_MACHINE, TO_LEARN, _NETWORK, add_to_learn, base_name, book, full_name, host_key,
    local_remote, network, ref_text, remote_name, short_dir,
)
from recordui import ChatDialog, NotesNames, search_spaces  # noqa: E402
from diaryeditor import DiaryEditor  # noqa: E402
from recordreader import Reader  # noqa: E402


class Unreached(ValueError):
    """An edit op's host wasn't reached (edit_call): what was asked is kept to ask again."""


class RecordHosts:
    """Window's hosts (another machine's notebooks, through its host process), and its notebooks, names and
    cards, viewers and Meta's column."""

    # ---- hosts: another machine's notebooks, through its host process (baudelib.notes), as Baude has its chats
    def note_hosts(self):
        """[(key, name)]: the hosts whose notebooks can be listed, as Baude's New session lists them: this machine
        (local), then every other known, by its name (one that's gone or deactivated too: picked, it says so,
        show_host_state); and the one picked, by the name it had, while it isn't known."""
        out, seen = [(HERE_HOST, THIS_MACHINE)], {HERE_HOST}
        for r in sorted(network().remotes() if _NETWORK else (), key=lambda r: remote_name(r).lower()):
            if not baudelib.client.wire.takes(r.info, "notes"):  # (a phone's keeps none; one not heard from yet: listed)
                continue
            if (key := host_key(r)) not in seen:
                seen.add(key)
                out.append((key, remote_name(r)))
        if self.store.picked not in seen:
            out.append((self.store.picked, self.store.picked_name or self.store.picked))
        return out

    def place(self):
        """Where you are, as Meta's told it: host / notebook / page (the page in the tab shown, if it's one)."""
        if self.current is None:
            return f"{self.store.host_name} / no {book().lower()} open"
        shown = self.tabs.current()
        page = shown.page if isinstance(shown, DiaryEditor) and shown.notebook is self.current else None
        return " / ".join([self.store.host_name, full_name(self.current), *([page] if page else [])])

    def terminal_host(self):
        """(where, name): the host picked in the dropdown, for ☰ → Terminal (this machine's, if none)."""
        r = self.view_remote()
        return ({"via": "local"}, THIS_MACHINE) if r is None else (r.where, remote_name(r))

    def view_remote(self):
        """The host picked's Remote (None: this machine), for the Kubernetes and database views (kubehost.picked); while
        ☰ opens one on another host from its caret, that host's (family_host: menufamily.open_family)."""
        return self.host_remote(getattr(self, "family_host", None) or self.store.picked or HERE_HOST)

    def host_remote(self, key):
        """A host's Remote, by its key, there or not (None for this machine, or one not known)."""
        if key == HERE_HOST or not _NETWORK:
            return None
        return next((r for r in network().remotes() if host_key(r) == key), None)

    @staticmethod
    def reachable(r):
        return r is not None and r.state == "alive" and not r.deactivated

    def start_network(self):
        """The hosts looked for (baudelib.client.Network): once, as the dropdown's first opened, or at the start with
        another host picked. (Not before: a Record that's only this machine's reaches no other.)"""
        if not self.net_on:
            self.net_on = True
            network().listen(self.on_network)
            self.on_network()

    def on_network(self):
        """The hosts changed: the dropdown's as they are, on the host picked; the one shown, back, listed again (what
        wasn't sent it, sent first: list_notes); what's in its way said (show_host_state); this machine's host process
        on other code than Record's, said (check_host_code)."""
        self.check_host_code()
        self.show_restart()
        for reader in [c for c in self.tabs.children() if isinstance(c, Reader) and c.chat]:
            reader.bot_changed()
        store = self.store
        for r in network().remotes():  # (the one picked, known by another key now: by that one)
            if store.picked != HERE_HOST and store.picked in getattr(r, "was", ()) and host_key(r) != store.picked:
                self.rekey_host(store.picked, host_key(r))
        hosts = self.note_hosts()
        self.host_dd.set_choices(hosts)
        self.host_dd.select(store.picked)
        self.host_dd.set_dimmed({host_key(r) for r in network().remotes()  # (out of reach: greyed, and picked, why's
                                 if r.state == "dead" and not r.deactivated and not r.mine})  # said: host_state)
        (self.host_dd.add_css_class if len(hosts) == 1 else self.host_dd.remove_css_class)("one-provider")  # (dimmed:
        # no other host to pick)
        if store.host != HERE_HOST and (r := self.host_remote(store.host)) is not None \
                and remote_name(r) != store.host_name:
            store.host_name = store.picked_name = remote_name(r)
        for key in {store.host, *store.remote_base} - {HERE_HOST}:  # (each host listed here: live)
            r = self.host_remote(key)
            up = self.reachable(r)
            if up and (not self.host_up.get(key) or (r.info.get("notes") or "") != self.notes_seen.get(key)):
                self.list_notes(key)  # (back, or its notebooks written since they were listed: listed again; what
            self.host_up[key] = up  # it didn't take sent first)
        for key in {HERE_HOST, store.host}:  # (the edits waiting on the pages shown, and this machine's: as they change)
            r = self.edits_remote(key)
            if r is not None and (key not in self.edits_seen or r.info.get("edits") != self.edits_seen[key]):
                self.fetch_edits(key)
        for key in {d["host"] for d in store.decisions} | ({HERE_HOST} if store.moves.get(HERE_HOST) else set()):
            if self.edits_remote(key) is not None:  # (there again: what it wasn't told, told it)
                self.send_unsent(key)
        self.show_host_state()

    def restart_host(self, then=None, joining=0, now=False):
        """This machine's host process stopped and started again, as Baude's Restart Daemon does: kept by it as its
        restart (restart free) till nothing's running there, or now (now); toasted while it's pending
        (show_restart), and once it's back, or that it isn't. then: what's done once it's back (Record
        restarted too). One that's running but not yet connected to (the app's hosts are looked at when first
        wanted) is waited for, up to 15 s, before it's asked; one on older code asked as it was then
        (meshui.ask_restart)."""
        r = local_remote()
        if r is None and joining < 15 and baudelib.serverstate.running_pid() is not None:
            network().here()
            GLib.timeout_add(250, lambda: self.restart_host(then, joining + 0.25, now) and False)
            return
        if r is None:
            return self.start_host(then)
        meshui.ask_restart(r, now, self.toast, lambda grace: self.wait_host_gone(r, then, grace))

    def wait_host_gone(self, r, then=None, grace=5):
        """r (this machine's host process) restarting: once it's gone, it back (start_host: the one it started itself,
        reached), then(); given up on if, grace seconds on, its restart's dropped and it's still there. One wait at a
        time."""
        r.restart_until = max(getattr(r, "restart_until", 0), time.monotonic() + grace)
        if getattr(r, "restart_waited", False):
            return
        r.restart_waited = True

        def gone():
            if r.state == "alive" and (r.info.get("restart") or time.monotonic() < r.restart_until):
                return True
            r.restart_waited = False
            if r.state != "alive":
                self.start_host(then)
            return False
        GLib.timeout_add(250, gone)

    def show_restart(self):
        """Each host's pending daemon restart (this machine's, and those of the hosts reached), toasted
        (meshui.RestartToasts)."""
        if getattr(self, "restart_toasts", None) is None:
            return
        here = local_remote()
        hosts = network().alive() if _NETWORK else [here] if here is not None else []
        self.restart_toasts.say(meshui.restart_lines(hosts, remote_name))

    def show_restart_blockers(self, key=None):
        """Details on a pending restart's toast (key: its host's): what that restart waits for (meshui.restart_dialog),
        the restart held meanwhile; See Thread on one shows it (see_blocker), the toast back saying it's paused
        (RestartToasts.hold)."""
        here = local_remote()
        r = here if key is None or here is not None and here.key == key else \
            next((r for r in network().alive() if r.key == key), None)
        if r is None or not r.info.get("restart"):
            return

        def ask(now):
            if r.mine:
                return self.restart_host(now=now)
            meshui.ask_restart(r, now, self.toast)
        meshui.restart_dialog(r, self, ask, lambda b: self.see_blocker(r, b), lambda: self.restart_toasts.hold(r.key))

    def see_blocker(self, r, b):
        """What r's restart waits for, shown (See Thread): a terminal, ☰ → Terminal on it; a chat (a task's Bot's, an
        agent run's on its host), read in a tab of its own, as Open a Chat… reads one, once its transcript's here."""
        if b.get("kind") == "terminal":
            return self.terminal.show(r.where, remote_name(r), b["terminal"])
        sid = b.get("session")
        on = next((x for x in network().remotes() if x.info.get("id") == b["host"]), None) if b.get("host") else r
        on = on if on is not None and sid in on.sessions else network().find(sid)
        if on is None:
            return

        def read():
            path = on.mirror(sid)
            if os.path.isfile(path):
                GLib.idle_add(lambda: self.open_chat(path) and False)
        threading.Thread(target=lambda: (on.sync(sid), read()), daemon=True, name="see-thread").start()

    def start_host(self, then=None):
        def start():
            ok = baudelib.hosts.start_here()
            GLib.idle_add(lambda: done(ok) and False)

        def done(ok):
            if not ok:
                self.toast(f"The daemon didn't start again: {widgets.pretty_path(baudelib.hosts.log_path())} may say why")
                return
            network().here()  # (its socket's there: no need to wait for the next look)
            self.toast(wording.say("hosts.daemon_restarted"))
            if then is not None:
                then()
        threading.Thread(target=start, daemon=True, name="start-host").start()

    def rekey_host(self, old, new):
        store = self.store
        for d in (store.remote, store.remote_base, store.unsent, self.sends, self.sent, self.host_up, self.notes_seen,
                  self.listed_at, store.edits, store.moves, store.moving, self.edits_seen):
            if old in d:
                d[new] = d.pop(old)
        for s in (store.loading, self.asked, self.list_again, self.list_later):
            if old in s:
                s.discard(old)
                s.add(new)
        for d in store.decisions:
            if d["host"] == old:
                d["host"] = new
        if store.host == old:
            store.host = new
        if store.picked == old:
            store.picked = new
            store.save()

    def host_state(self, key=None):
        """What's in the way of the host shown (or key's), as Baude's banner says it over a host's chats, or None: it's
        there (or it's this machine)."""
        store = self.store
        key = store.host if key is None else key
        if key == HERE_HOST:
            return None
        r, name = self.host_remote(key), dict(self.note_hosts()).get(key, store.host_name)
        if r is None:
            return wording.say("hosts.looking", host=name)
        if not r.deactivated and (said := r.said_stopping(name)) is not None:  # (stopping: said as it goes)
            return said
        if self.reachable(r):
            return None
        if r.deactivated:
            return wording.say("hosts.deactivated", host=name)
        if r.state != "dead":
            return wording.say("hosts.looking", host=name)
        if r.error:  # (why it's out of reach, as the last try to reach it said)
            return wording.say("hosts.down_because", host=name, why=r.error)
        return wording.say("hosts.down", host=name)

    def show_host_state(self):
        """The host shown isn't there (or is deactivated): said in the note over the left bar, its notebooks as
        they were last listed; what's changed in them is sent once it's back."""
        said = self.host_state()
        if said is not None:
            self.host_note.set_title(GLib.markup_escape_text(said))
        self.host_note.set_revealed(said is not None)

    def host_menu(self, x, y):
        """Right-click the notebooks' host (another machine's), as Baude's New session's: Deactivate, or Activate for
        one that's deactivated."""
        if (r := self.host_remote(self.store.picked)) is None:
            return
        menu = Gio.Menu()
        item = Gio.MenuItem.new(wording.say("hosts.activate") if r.deactivated else wording.say("hosts.deactivate"),
                                None)
        item.set_action_and_target_value("win.deactivate_host", GLib.Variant("(sb)", (host_key(r), not r.deactivated)))
        menu.append_item(item)
        pop = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        pop.set_parent(self.host_dd)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()
        self.host_menu_shown = pop

    def deactivate_host(self, key, off):
        """The host right-clicked → Deactivate (off), or Activate: as Baude's (Network.deactivate, kept in
        servers.json and by this machine's host process); the note over them says so while it is."""
        if (r := self.host_remote(key)) is not None:
            network().deactivate(r, off)
            self.toast(wording.say("hosts.now_deactivated" if off else "hosts.now_activated", host=remote_name(r)))
            self.show_host_state()

    def pick_host(self, key):
        """A host picked in the dropdown (or with Meta's button:host): its notebooks listed; kept, across restarts
        and while it's gone."""
        self.store.picked, self.store.picked_name = key, dict(self.note_hosts()).get(key, THIS_MACHINE)
        self.store.save()
        if key != HERE_HOST:
            self.start_network()
        self.show_host(key)

    def show_host(self, key):
        """The notebooks listed, a host's: the open one closed (its changes sent first), the first of the new host's
        opened once they're listed (list_notes)."""
        store = self.store
        if key == store.host:
            return
        store.flush()  # (what's typed: saved, or sent to the host it's on)
        self.notebook_list.unselect_all()
        if self.current is not None:
            self._show(None)
        store.host, store.host_name = key, dict(self.note_hosts()).get(key, store.picked_name)
        if key != HERE_HOST and key not in store.remote_base:
            store.loading.add(key)  # (nothing sent to it till what it has is listed)
        store.mirror_pages()
        self.fill_sidebar()
        if key != HERE_HOST:
            self.host_up[key] = self.reachable(self.host_remote(key))
            self.list_notes(key)
        self.fetch_edits(key)
        self.open_first()
        self.host_dd.select(key)
        self.show_host_state()

    def open_first(self):
        first = self.notebook_list.get_row_at_index(0)
        if first is not None:
            self.notebook_list.select_row(first)
        else:
            self._show(None)

    LIST_EVERY_S = 1.0  # a host's notebooks listed at most this often, while they're being written to

    def list_notes(self, key):
        """A host's notebooks asked for (op notes), and taken in as it lists them (Store.take). Not while what's
        been changed here isn't on it: what wasn't sent, sent again first (Store.failed), and any list asked for
        before a change was sent, asked for again once the changes are answered: none's undone by an older list."""
        r = self.host_remote(key)
        if not self.reachable(r) or key in self.asked or key in self.list_later:
            return
        if (wait := self.listed_at.get(key, float("-inf")) + self.LIST_EVERY_S - time.monotonic()) > 0:
            self.list_later.add(key)  # (one listing a second at most, as it's written to: once more after)
            GLib.timeout_add(int(wait * 1000) + 1, lambda: (self.list_later.discard(key), self.list_notes(key)) and False)
            return
        self.listed_at[key] = time.monotonic()
        self.asked.add(key)
        at, self.notes_seen[key] = self.sent.get(key, 0), r.info.get("notes") or ""
        base = self.store.remote_base.get(key) or {}
        have = {i: notes.digest(b) for i, b in base.items()}  # (only those changed since are sent)
        if key in self.list_whole:
            self.list_whole.discard(key)
            have = {}

        def got(reply):
            self.asked.discard(key)
            store = self.store
            if key == store.host:
                store.flush()  # (what's typed since: sent first, and listed again after)
            store.push(key)  # (what wasn't sent it before: sent now)
            if self.sends.get(key) or self.sent.get(key, 0) != at or store.unsent.get(key) is not None:
                if self.sends.get(key) or self.sent.get(key, 0) != at:  # (not taken: listed again once they're
                    self.notes_seen.pop(key, None)  # answered; one that won't take them: as it's written next)
                self.list_again.add(key)
                if not self.sends.get(key) and key == store.host:  # (nothing on its way: asked again now)
                    self.list_again.discard(key)
                    GLib.timeout_add(500, lambda: self.list_notes(key) and False)
                return
            if reply.get("op") != "notes" or reply.get("error"):
                store.loading.discard(key)
                self.toast(f"{remote_name(r)}'s {book(plural=True).lower()} weren't listed: " +
                           (baudelib.client.wire.said(reply) or wording.say("agent_proxy.no_answer")))
                return
            books = [b for b in reply.get("notebooks") or () if isinstance(b, dict) and isinstance(b.get("id"), str)
                     and isinstance(b.get("pages"), dict)]
            first = key in store.loading
            ids = [i for i in reply.get("ids") or () if isinstance(i, str)] if "ids" in reply else None
            have_now, sent_ids = store.remote_base.get(key) or {}, {b["id"] for b in books}
            by_id = {t.get("id") for t in store.remote.get(key) or ()}
            if ids is not None and any(i not in sent_ids and (i not in have_now or i not in by_id) for i in ids):
                self.list_whole.add(key)  # (one it left out as had here, that isn't: listed whole)
                self.notes_seen.pop(key, None)
                GLib.timeout_add(100, lambda: self.list_notes(key) and False)
                return
            merged = store.take(key, remote_name(r), [notes.shared(b) for b in books], ids)
            if key == store.host:
                self.notes_changed(first)
            if merged:  # (changed here and there: what's merged sent to it)
                store.save()
            if (r.info.get("notes") or "") != self.notes_seen.get(key):  # (written again while it was listed)
                self.list_notes(key)
        r.request({"op": "notes", "have": have}, got)

    def send_notes(self, key, changes, moved=None):
        """Changes to a host's notebooks, sent it (Store.push), with its pages moved here (moved: the edits waiting on
        them moved there too; this machine's host is sent only these). Those it doesn't take (it's gone, or says no):
        kept to send again (Store.failed, Store.moves_sent), with the next change or once it's back."""
        r, store = local_remote() if key == HERE_HOST else self.host_remote(key), self.store
        if key == HERE_HOST:
            def moved_done(reply):
                store.moves_sent(key, moved, reply.get("op") == "notes" and not reply.get("error"))
                store.save()
            if not self.reachable(r):
                return moved_done({})
            return r.request({"op": "notes", "action": "write", "changes": [], "moved": moved}, moved_done)
        self.sent[key] = seq = self.sent.get(key, 0) + 1
        if not self.reachable(r):
            store.failed(key, changes)
            if moved:
                store.moves_sent(key, moved, False)
            store.unsent[key] = seq
            return
        self.sends[key] = self.sends.get(key, 0) + 1

        def done(reply):
            self.sends[key] -= 1
            if moved:
                store.moves_sent(key, moved, reply.get("op") == "notes" and not reply.get("error"))
            if reply.get("op") != "notes" or reply.get("error"):
                if store.unsent.get(key) is None:
                    self.toast(f"{remote_name(r)} didn't save the change: "
                               f"{baudelib.client.wire.said(reply) or wording.say('agent_proxy.no_answer')}")
                store.failed(key, changes)
                store.unsent[key] = self.sent[key]
                self.list_again.discard(key)  # (listed again as it comes back, or the window does: not at once)
            elif store.unsent.get(key) is not None and seq > store.unsent[key]:
                del store.unsent[key]  # (sent after what failed was marked: it had it)
            if not self.sends[key] and key in self.list_again:
                self.list_again.discard(key)
                self.list_notes(key)
        r.request({"op": "notes", "action": "write", "changes": changes, **({"moved": moved} if moved else {})}, done)

    def notes_here(self, changes):
        """Changes another machine's Record made to this one's notebooks (record.listen): made, and shown."""
        if not notes.changes_ok(changes):
            return False
        self.store.apply_here(changes)
        if self.store.host == HERE_HOST:
            self.notes_changed()
        return True

    # ---- the edits proposed to a host's notebooks' pages: kept by that host (wire: edits, edit_*), shown here
    def edits_remote(self, key):
        """The Remote the edits of a host's notebooks are asked of (this machine's host process for this machine's),
        or None if it isn't there now."""
        r = local_remote() if key == HERE_HOST else self.host_remote(key)
        return r if self.reachable(r) else None

    def fetch_edits(self, key):
        """A host's edits asked for (op edits, the decided too), and shown once it answers (edits_changed)."""
        if (r := self.edits_remote(key)) is None:
            return
        self.edits_seen[key] = r.info.get("edits")

        def got(reply):
            if reply.get("op") == "edits":
                listed = [*(reply.get("edits") or ()), *(reply.get("decided") or ())]
                self.store.take_edits(key, listed)
                self.edits_changed()
                self.hand_edits(key, {e.get("id") for e in listed if isinstance(e, dict)})
                self.send_unsent(key)
        r.request({"op": "edits", "all": True}, got)

    def hand_edits(self, key, have):
        """The first time a host's edits are fetched here: Meta's edits waiting on its notebooks that this Record kept
        (in records.json, from before edits were their host's) and it hasn't (have: the ids it listed), handed to it
        (edit_adopt), with their own ids; each marked handed once it answers, so it's never handed again (one decided
        there since, and no longer kept there, isn't made to wait again)."""
        store = self.store
        if key in self.handed or (r := self.edits_remote(key)) is None:
            return
        self.handed.add(key)
        books = {t["id"]: t for t in store.every_book() if t.get("id") and store.host_of(t) == key}
        kept = [(p, p.get("notebook", t["id"] if t is not None else None))
                for t, messages in [(None, store.meta["messages"])] +
                [(t, t.get("messages") or ()) for t in books.values()]
                for m in messages if isinstance(m, dict) for p in m.get("proposals") or ()
                if isinstance(p, dict) and p.get("status") == "pending" and not p.get("handed")]
        kept = [(p, nb) for p, nb in kept if nb in books and isinstance(p.get("id"), str)
                and isinstance(p.get("page"), str)]
        for p, _nb in kept:
            if p["id"] in have:  # (it has it: never handed again)
                p["handed"] = True
        hand = [(p, nb) for p, nb in kept if p["id"] not in have]
        if not hand:
            return store.save() if kept else None

        def done(reply):
            if reply.get("op") != "edits":
                self.handed.discard(key)  # (handed again next time it's fetched)
                return
            for p, _nb in hand:
                p["handed"] = True
            store.save()
            self.fetch_edits(key)
        r.request({"op": "edit_adopt", "edits": [
            {"id": p["id"], "notebook": nb, "page": p["page"], "old": str(p.get("old") or ""),
             "new": str(p.get("new") or ""), "start": int(p.get("start") or 0), "status": "pending",
             "t": float(p.get("t") or time.time()), "by": str(p.get("by") or "Meta")} for p, nb in hand]}, done)

    def edits_changed(self):
        """The edits as the hosts last listed them, shown: on the pages open, in Meta's bar and its cards."""
        for child in self.tabs.children():
            if isinstance(child, DiaryEditor) and child.notebook is self.current:
                child.show_pending()
        if (chat := getattr(self, "chat", None)) is not None:
            chat.show_pending_bar()
            chat.edit_cards_changed()

    def edit_call(self, key, msg):
        """An edit op asked of a host, waited for (not on the main loop: a Later's work): its answer, or ValueError.
        This machine's host process started first if it isn't running."""
        r = local_remote() if key == HERE_HOST else self.host_remote(key)
        if key == HERE_HOST and not self.reachable(r):
            if not baudelib.hosts.start_here():
                raise Unreached(f"The daemon here isn't running: {baudelib.hosts.log_path()} may say why.")
            network().here()
            end = time.monotonic() + 15
            while not self.reachable(r := local_remote()) and time.monotonic() < end:
                time.sleep(0.2)
        if not self.reachable(r):
            raise Unreached(self.host_state(key) or "its host isn't there")
        got = r.call(msg, timeout=30)
        if (got.get("op") == "error" or got.get("error")) and "req" not in got:  # (not the host's: no way to it)
            raise Unreached(baudelib.client.wire.said(got) or "no answer")
        if got.get("op") == "error" or got.get("error"):
            raise ValueError(baudelib.client.wire.said(got) or "no answer")
        return got

    def start_host_here(self):
        """This machine's host process, started as Record opens if it isn't running (the edits waiting on its notebooks'
        pages are its), off the main loop: they're fetched once it's there (on_network)."""
        def start():
            if baudelib.hosts.start_here():
                network().here()
        if local_remote() is None:
            threading.Thread(target=start, daemon=True, name="start-host").start()

    def decide_edit(self, p, accept):
        """An edit accepted (its text put in its page here: saved, or sent to its host, first) or rejected: kept so here
        (Store.decisions, saved) till its notebook's host has it (edit_decide: send_unsent), now or once it's back;
        till then the host's listing it waiting doesn't make it wait here again (Store.take_edits)."""
        store = self.store
        nb = next((t for t in store.every_book() if t.get("id") == p.get("notebook")), None)
        key = store.host_of(nb) if nb is not None else None
        if key is None:
            return
        store.decisions = [d for d in store.decisions if d["id"] != p["id"]] + [
            {"host": key, "notebook": p["notebook"], "id": p["id"], "accept": bool(accept)}]
        store.save_now()  # (the page as accepted: written, or sent to its host, before it's told; the decision kept)
        self.send_unsent(key)

    def send_unsent(self, key):
        """What a host wasn't told yet, told it: its pages moved here (this machine's: Store.moves; another's go with
        its notes writes), and the edits decided here (edit_decide, through edit_call: this machine's host started if
        it isn't running). A decision it takes, or refuses (the edit's not waiting there: decided or taken back
        elsewhere), is done with, and its edits asked for again; one it wasn't reached for is kept, and sent once it's
        back (on_network)."""
        store = self.store
        if key == HERE_HOST and store.moves.get(key) and self.edits_remote(key) is not None:
            self.send_notes(key, [], store.take_moves(key))

        def done(msg, how, said):
            self.deciding.discard(msg["id"])
            if how == "unreached":
                return
            if how == "refused":
                self.toast(f"{dict(self.note_hosts()).get(key, key)} wasn't told: {said}")
            store.decisions = [d for d in store.decisions if d["id"] != msg["id"]]
            store.save()
            self.fetch_edits(key)

        for d in [d for d in store.decisions if d["host"] == key and d["id"] not in self.deciding]:
            self.deciding.add(d["id"])
            msg = {"op": "edit_decide", "notebook": d["notebook"], "id": d["id"], "accept": bool(d["accept"])}

            def work(msg=msg):
                try:
                    out = ("done", self.edit_call(key, msg))
                except Unreached:
                    out = ("unreached", None)
                except ValueError as e:
                    out = ("refused", str(e))
                GLib.idle_add(lambda: done(msg, *out) and False)
            threading.Thread(target=work, daemon=True, name="edit-decide").start()

    def notes_changed(self, fresh=False):
        """The notebooks listed changed under the window (another host's listed, or changed from elsewhere): rows added
        and gone, the open one's pages listed and shown again, its gone pages' tabs closed (fresh: the first listing,
        the first notebook opened)."""
        if fresh:
            self.fill_sidebar()
            self.open_first()
            return
        self.books_changed()
        self.show_title()  # (the open one renamed on its host, or from another machine)
        if self.current is not None and not any(t is self.current for t in self.store.notebooks):
            self.notebook_list.unselect_all()
            self._show(None)
            return
        if self.current is None:
            return
        for child in list(self.tabs.children()):
            if isinstance(child, DiaryEditor) and child.notebook is self.current:
                if child.page != HOME and child.page not in self.current["pages"]:
                    self.tabs.close(("note", child.page))
                else:
                    child.reload()
        self.pages.fill()

    # actions
    def free_name(self, cwd=None):
        """A new one's name: named for cwd's folder (else Untitled), "Shop 2" if that's taken."""
        used = {full_name(t).lower() for t in self.store.notebooks}
        base = os.path.basename((cwd or "").rstrip(os.sep)) or "Untitled"
        name, n = base, 2
        while name.lower() in used:
            name, n = f"{base} {n}", n + 1
        return name

    def new_notebook(self):
        """New Notebook (Ctrl+N): a new one, to name. While one made with New is still blank (Store.is_blank), that one
        again, not another: blank ones don't pile up, as Baude lists a new chat only once it's begun."""
        blank = next((t for t in self.store.notebooks if Store.is_blank(t)), None)
        if blank is None:
            self.store.new(self.free_name())
            self.fill_sidebar()
            self.notebook_list.select_row(self.notebook_list.get_row_at_index(0))
        else:
            self.select(blank["id"])
        self.open_note(HOME)
        self.rename(f"New {book()}", wording.say("common.create"))

    def drop_blank(self, notebook):
        """One made with New and left blank, gone to another from: let go of (a blank one isn't kept)."""
        if notebook is not self.current and Store.is_blank(notebook) and notebook in self.store.notebooks:
            self.store.delete(notebook)
            i = 0
            while (r := self.notebook_list.get_row_at_index(i)) is not None:
                if r.notebook is notebook:
                    self.notebook_list.remove(r)
                    break
                i += 1
        return False

    def choose_chat(self):
        """☰ → Open a Chat… (or Read's button): a Claude Code chat picked, read on the Read tab (open_chat)."""
        ChatDialog(lambda s: self.open_chat(s["path"])).present(self)

    def rename(self, heading=None, doing=None):
        if not self.current:
            return
        notebook = self.current
        d = Adw.AlertDialog(heading=heading or wording.say("common.rename"))
        e = Gtk.Entry(text=base_name(notebook["name"]), activates_default=True, hexpand=True)
        box = Gtk.Box(spacing=6)
        box.append(e)
        d.set_extra_child(box)
        d.add_response("cancel", wording.say("common.cancel"))
        d.add_response("ok", doing or wording.say("common.rename"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")

        def done(_d, r):
            if r == "ok" and base_name(e.get_text()):
                notebook["name"] = base_name(e.get_text())
                notebook.pop("fresh", None)  # (named: kept, Store.is_blank)
                self.store.save_now()
                self.refresh_sidebar_row(notebook)
        d.connect("response", done)
        d.present(self)
        e.grab_focus()

    def delete(self):
        if not self.current:
            return
        notebook = self.current
        d = Adw.AlertDialog(heading=f"Delete {full_name(notebook)}?",
                            body=f"Its notes ({len(notebook['pages'])} pages) go with it.")
        d.add_response("cancel", wording.say("common.cancel"))
        d.add_response("delete", wording.say("common.delete"))
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, r):
            if r == "delete":
                self.store.delete(notebook)
                self.fill_sidebar()
                first = self.notebook_list.get_row_at_index(0)
                if first:
                    self.notebook_list.select_row(first)
                else:
                    self._show(None)
        d.connect("response", done)
        d.present(self)

    def add_unknown(self, term):
        notebook = self.current
        if notebook is None:
            return
        notebook["pages"][HOME] = add_to_learn(notebook["pages"].get(HOME, ""), term)
        self.store.save()
        if (home := self.tabs.get(("note", HOME))) is not None:
            home.reload()
        toast = Adw.Toast(title=f"“{term}” added to {TO_LEARN}", timeout=4)
        toast.set_button_label(wording.say("common.open"))
        toast.connect("button-clicked", lambda *_: self.open_note(HOME))
        self._toast(toast)

    def toast(self, text):
        self._toast(Adw.Toast(title=text, timeout=4))

    def space(self):
        """⌘K's search of the chat read's folder (read_folders), every chat by its title, and the notebooks and their
        pages (what @ lists, in the notes and in the Record's box)."""
        return search_spaces(self.read_folders(), self.notes)

    def names_in(self, text):
        """The names in a text that the chat read's folder has (to link): none until they're catalogued."""
        space = self.space()
        return {it["surface"] for it in space.mentions(text)} if space is not None and not space.busy else set()

    def follow(self, uri, from_chat=False):
        """A link in the chat or a reply: a name (ref:) opened where it is (open_ref); a web link opens as links do."""
        if not uri.startswith("ref:"):
            return False
        self.open_ref(urllib.parse.unquote(uri[4:]), from_chat=from_chat)
        return True

    def open_ref(self, name, from_chat=False, _message=None):
        """A name clicked: opened where it is (open_item: its file on the Code tab, a branch on the Git tab, a
        Kubernetes object in its view, a table in the database diagram, a chat to read). One that has nowhere to
        open says what it is (ref_card), as the pointer over it does. from_chat: a name in the chat or a reply,
        not an @name in the notes (its names the Read tab's: a chat read there has its folder's too)."""
        space = self.reader.space if from_chat and self.reader is not None and self.reader.space is not None \
            else self.space()
        item = space.find(name) if space is not None else None
        if item is None and from_chat:  # (a page or a notebook, in the Record's reply: the notes')
            item = self.notes.find(name)
        if item is not None and item.get("kind") in NotesNames.KINDS:
            return self.open_notes_item(item)
        if item is None and (url := lookupgit.github_url(name)) is not None:
            return fixview.web(self, url)  # (not listed now: its page is still there)
        if item is None:
            where = ", ".join(short_dir(f) for f in self.read_folders())
            return self.toast(f"@{name}: not found in {where}" if where
                              else f"@{name}: open a chat to look it up in its folder")
        if item.get("kind") in ("branch", "k8s", "chat") or (item.get("entry") or {}).get("db") or \
                (item.get("path") and os.path.exists(item["path"])) or item.get("label"):
            self.open_item(item)
        else:
            self.ref_card(item, from_chat)

    def hover_ref(self, name, from_chat=False, at=None):
        """The pointer come over a name: what it is, just under the pointer (at: (the widget it's over, x, y)), or
        with no pointer, at the bottom (ref_card), unless that's showing already."""
        space = self.reader.space if from_chat and self.reader is not None and self.reader.space is not None \
            else self.space()
        if (item := space.find(name) if space is not None else None) is None:
            return
        self._card_stay()
        if self.card is None or self.card.surface != item["surface"]:
            self.ref_card(item, from_chat, at)

    CARD_LINGER = 350  # ms a name's card stays once the pointer's off it (time to come onto it, for To notes)

    def hover_left(self):
        """The pointer gone off a name: its card goes in a moment, unless the pointer comes onto the card."""
        if isinstance(self.card, Gtk.Popover) and self.card_going is None:
            self.card_going = GLib.timeout_add(self.CARD_LINGER, self._card_gone)

    def _card_stay(self):
        if self.card_going is not None:
            GLib.source_remove(self.card_going)
            self.card_going = None

    def _card_gone(self):
        self.card_going = None
        self.hide_card()
        return False

    def hide_card(self):
        card, self.card = self.card, None
        if isinstance(card, Gtk.Popover):
            card.popdown()
            GLib.idle_add(lambda: card.get_parent() is not None and card.unparent())
        elif card is not None:
            card.dismiss()

    def ref_card(self, item, from_chat=False, at=None):
        """What a name is, in place of the last one: just under the pointer (at: (widget, x, y)), a card that stays
        while the pointer's on the name or on it; with no pointer, at the bottom. A name in the chat's with To notes,
        to write it into the notes' page."""
        self.hide_card()
        what = f"{item['surface']}: {item['what']} · {item['summary']}"
        if at is not None and not at[0].get_mapped():  # (nothing on screen to put it under: GTK would crash)
            at = None
        if at is None:
            toast = Adw.Toast(title=GLib.markup_escape_text(what), timeout=6)
            if from_chat:
                toast.set_button_label("To notes")
                toast.connect("button-clicked", lambda *_: self.diary_ref(item["surface"], item.get("label")))
            toast.surface = item["surface"]
            toast.connect("dismissed", lambda t: setattr(self, "card", None) if self.card is t else None)
            self.card = toast
            self._toast(toast)
            return
        widget, x, y = at
        pop = Gtk.Popover(autohide=False, has_arrow=False, position=Gtk.PositionType.BOTTOM, can_focus=False)
        pop.add_css_class("ref-card")
        box = Gtk.Box(spacing=8, margin_start=4, margin_end=4, margin_top=2, margin_bottom=2)
        box.append(widgets.time_tip(Gtk.Label(label=what, xalign=0, wrap=True, max_width_chars=64,
                                              wrap_mode=Pango.WrapMode.WORD_CHAR), item.get("at"), item.get("ago")))
        if from_chat:
            btn = Gtk.Button(label="To notes", valign=Gtk.Align.CENTER)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_: (self.hide_card(), self.diary_ref(item["surface"], item.get("label"))))
            box.append(btn)
        pop.set_child(box)
        stay = Gtk.EventControllerMotion()  # (onto the card, for To notes: it stays; off it, it goes)
        stay.connect("enter", lambda *_: self._card_stay())
        stay.connect("leave", lambda *_: self.hover_left())
        pop.add_controller(stay)
        pop.surface = item["surface"]
        pop.set_parent(widget)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 14  # (below the pointer, clear of its arrow)
        pop.set_pointing_to(rect)
        self.card = pop
        pop.popup()

    def viewer_folder(self):
        """The folder the viewers show (viewers.ViewerHost): the chat read's; None without one."""
        folders = self.read_folders()
        return folders[0] if folders else None

    def git_chat(self):
        """The chat the Git page keeps its state for (GitPage.chat_check): the notebook's."""
        return (self.current or {}).get("id")

    def viewers(self, folder=None):
        """The viewers on a folder in a window of their own (viewers.ViewerWindow), not Record's: for when they're
        wanted apart. None, said, if there's no folder."""
        folder = folder or self.viewer_folder()
        if not folder:
            self.toast("Open a chat first: the viewers show its folder")
            return None
        return viewers.ViewerWindow.for_folder(folder, self.get_application())

    def open_viewer(self, page):
        """☰ → Kubernetes…, Databases… or Terminal (over the window), or a page of the right bar's (files, git)."""
        if page == "kube":
            self.kube.open()
        elif page == "docker":
            self.docker.open()
        elif page == "db":
            self.schema.open()
        elif page == "terminal":
            self.terminal.open()
        else:
            self.right_slide.set_shown(True)
            self.right_stack.set_visible_child_name(page)

    def toggle_meta(self):
        """Ctrl+M (widgets.META_KEY), as Baude's, from anywhere in the window, a page's text too: Meta shown
        (show_meta), or closed if it's open already (close_meta)."""
        if self.right_slide.target and self.right_stack.get_visible_child_name() == "meta" \
                and self.capture_cover() is None:
            self.close_meta()
        else:
            self.show_meta()

    def show_meta(self):
        """Meta's page of the right bar, the bar shown if it was hidden, and the focus in Meta's box."""
        cover = self.capture_cover()  # (the large image, the Kubernetes or the database view: over Meta too)
        if cover is not None:
            cover.close()
        page = self.tabs.current()
        if isinstance(page, DiaryEditor) and page.pop.get_visible():
            page._hide_pop()  # (@'s or #'s list, not left over the page)
        self.right_slide.set_shown(True)
        self.right_stack.set_visible_child_name("meta")
        self.chat.composer.grab_focus()

    def _focus_moved(self, _pspec):
        focus = self.get_focus()
        if focus is not None and not focus.is_ancestor(self.right):
            self.meta_from = focus

    def close_meta(self):
        """The right bar hidden, Meta's @ list with it, and the focus, if it was in the bar, back where it was last
        outside it if that's still on screen (a page's text at its caret), else in the tab open: a page's text, a
        chat's transcript, a file's code."""
        focus = self.get_focus()
        if self.chat.refs.visible():
            self.chat.refs.hide()
        self.right_slide.set_shown(False)
        if focus is not None and not focus.is_ancestor(self.right):
            return
        page = self.tabs.current()
        if not (widgets.focus_shown(self.meta_from)
                or widgets.focus_shown(page.view if isinstance(page, DiaryEditor) else
                                       page.scroll if isinstance(page, Reader) else
                                       page.code.view if isinstance(page, viewers.CodePage) else None)
                or (page is not None and page.child_focus(Gtk.DirectionType.TAB_FORWARD))):
            self.set_focus(None)  # (not left in Meta's box as it slides shut)

    def open_palette(self):
        """⌘K / Ctrl+K, as Baude's: every name the chat read's folder has, and the notebooks and their pages, searched
        as you type, a name opened where it is (open_item: its page, its file, the Git page, the Kubernetes view, the
        database diagram)."""
        pickers.RefPalette(self, self.space(), self.open_item).present(self)

    def open_notes_item(self, item):
        """A notebook (shown) or a page (open, in its notebook) from @ or ⌘K."""
        if item.get("notebook") != (self.current or {}).get("id"):
            self.select(item.get("notebook"))
        if item.get("kind") == "page" and self.current is not None and item.get("page") in self.current["pages"]:
            self.open_note(item["page"])

    def open_item(self, item):
        """An @name opened where it is, in Record's tabs and views as Baude's ⌘K opens one: its file on the Code tab,
        a branch on the Git tab, a Kubernetes object in its view, a table in the database diagram; else its file in
        its app."""
        if item.get("kind") in NotesNames.KINDS:  # (a notebook, or a page of one)
            return self.open_notes_item(item)
        if item.get("kind") == "chat":  # (a chat by its title: on the Read tab)
            return self.open_chat(item["path"])
        if (listed := (item.get("entry") or {}).get("github")) is not None:  # (a pull request or an issue)
            return fixview.open_github(self, listed)
        if viewers.ViewerHost.open_item(self, item):
            return
        if item.get("path") and os.path.exists(item["path"]):
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(item["path"], None), None)

    def diary_ref(self, surface, label=None):
        """A name from the chat, written into the page of notes last shown (else Home) as "- @name: ", the caret
        after it for what it means to you."""
        editor = self.diary
        if editor is None:
            return
        self.tabs.show(editor.tab_key)
        buf = editor.buf
        text = editor.kept()
        at = editor.buf_offset(len(text), past=False)  # (the page's end: before an addition proposed there)
        written = ("" if not text or text.endswith("\n") else "\n") + f"- {ref_text(surface, label)}: "
        buf.insert(buf.get_iter_at_offset(at), written)
        buf.place_cursor(buf.get_iter_at_offset(at + len(written)))
        editor.view.grab_focus()
        editor.follow_soon()

    def chat_read(self):
        """The chat read: the one shown, else the one shown last ({path, title, cwd}), or None."""
        return (self.reader.chat if self.reader is not None else None) or self.store.reading

    def read_folders(self, chat=None):
        """The folder names are from (chat: else the one read, chat_read): the chat's own, [] without one. What @, ⌘K,
        the viewers, GitHub's lists and Meta's Read know besides the notes."""
        cwd = (chat or self.chat_read() or {}).get("cwd")
        return [os.path.realpath(cwd)] if cwd and os.path.isdir(cwd) else []

    def _names_in(self):
        """The chat read's folder catalogued (reading_changed): the open pages' @names drawn with their logos, which
        were left plain while it was being read (DiaryEditor._highlight)."""
        self.names_wait = None
        if (space := self.space()).busy:  # (one in, another on its way)
            self.names_wait = space.refresh(self._names_in)
            return
        for child in self.tabs.children():
            if isinstance(child, DiaryEditor):
                child.github_changed()
