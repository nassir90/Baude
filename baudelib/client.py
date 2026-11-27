"""Hosts as Baude sees them: this machine's host process (its socket), and another machine's over ssh, given by hand
(Add Host…, BAUDE_HOSTS) or remembered from before: none looked for on the network; the sessions each lists; a
session's transcript, read where it is on this machine, else mirrored here, its subagents' beside it, so what reads a
transcript reads it as it reads a local one; and a session's claude, talked to as Baude talks to its own (RemoteProc).
wire.py says how each is reached.

A host that goes (its connection drops, or it says nothing for wire.SILENT_S) isn't forgotten: it's "dead", its
sessions still listed as they were, until it's back or it's been gone for FORGET_S. Calls to what's given (on_change,
a request's callback, RemoteProc's on_msg and on_exit) come through dispatch, as ClaudeProc's do."""
import base64
import itertools
import json
import os
import shutil
import socket
import threading
import time
import urllib.parse

from baudelib import claude, mesh, selfupdate, toolnames, wire, wording

FORGET_S = 7 * 86400
RETRY_S = (1, 2, 5, 10, 30)  # a dead host is tried again after these, then every last one
RETRY_SSH_S = (2, 10, 30, 120, 300)  # (one over ssh: a login each time, on another machine)

UNREACHED = ("the server isn't there", "the server didn't answer", "the server's gone")  # (a request's error when
# its host wasn't reached, or went before it answered: Remote.request's and call's)


def network_home():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "network")


def _subagent_file(name):
    """A name a subagents folder's file may have (and one to write here: no path in it)."""
    return isinstance(name, str) and name.endswith((".jsonl", ".meta.json")) and not name.startswith(".") \
        and os.path.basename(name) == name and "\\" not in name


def is_here(info):
    """Is a server on this machine, as this user (its folders are this one's)?"""
    if not info or (info.get("host") or "").split(".")[0].lower() != socket.gethostname().split(".")[0].lower():
        return False
    home = info.get("home")
    return not isinstance(home, str) or os.path.realpath(home) == os.path.realpath(os.path.expanduser("~"))


APART_S = 3 * selfupdate.CHECK_S  # (how long a head and its host process run different code before it's said: an
# update that one has picked up, and the other will in its next look at the checkout, isn't)


def code_apart(info, started, root=selfupdate.HERE):
    """(git: off the main loop) How a head whose code is commit started, from the checkout root, and this machine's
    host process (info: as it says) run different code: None, the same (or nothing to tell by); "head", the head's
    behind the host's; "host", the host's behind; "both", both apart from what's checked out; "elsewhere", the host's
    code is another checkout's (its checkout, as it says; or a commit this checkout hasn't got, from one that doesn't
    say). OSError if git couldn't tell."""
    commit = info.get("commit")
    if not commit or not started or commit == started:
        return None
    if isinstance(info.get("checkout"), str) and os.path.realpath(info["checkout"]) != os.path.realpath(root):
        return "elsewhere"
    if not _git_ok(root, "cat-file", "-e", f"{commit}^{{commit}}"):
        return "elsewhere"
    if _git_ok(root, "merge-base", "--is-ancestor", started, commit):
        return "head"
    if _git_ok(root, "merge-base", "--is-ancestor", commit, started):
        return "host"
    head = selfupdate.git(root, "rev-parse", "HEAD", timeout=5)  # (neither after the other: a branch changed)
    if head is None:
        raise OSError("git couldn't say what's checked out")
    return "host" if head == started else "head" if head == commit else "both"


def _git_ok(root, *args):
    """Did git say yes (0) or no (1)? OSError for anything else: it couldn't tell."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", root, *args], env=selfupdate.GIT_ENV, stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=5, start_new_session=True)
    except subprocess.TimeoutExpired as e:
        raise OSError(f"git {args[0]} took too long") from e
    if r.returncode not in (0, 1):
        raise OSError(f"git {args[0]}: {r.stderr.decode(errors='replace').strip()}")
    return r.returncode == 0


STOPPING_SAID_S = 60  # (how long a host's last word as it stopped is said, while it isn't back)


def chat_name(info):
    """What a chat is called wherever it's named: a Bot, by its name alone; else its title (None if it has none)."""
    return (info.get("bot") or {}).get("name") or info.get("title")


def stopping_line(name, restart, left, total):
    """A host stopping (or restarting), how far along, as its menu bar item and the heads' banners say it."""
    if left is not None and left > 0:
        return wording.say("host_status.restarting_left", host=name, left=left, total=total) if restart else \
            wording.say("host_status.stopping_left", host=name, left=left, total=total)
    if left is not None and total:
        return wording.say("host_status.restarting_all_stopped", host=name) if restart else \
            wording.say("host_status.stopping_all_stopped", host=name)
    return wording.say("host_status.restarting", host=name) if restart else \
        wording.say("host_status.stopping", host=name)


class Remote:
    """One host: where it is (where: wire.reach's), its info (as hello says), its sessions (as it lists them), and
    whether it's there. found_by: "local" (this machine's socket), "hand" (or, kept from before, "network" or
    "tailscale", as it was found then), "mesh" (a member of the mesh this machine's host process is in: reached
    through it, and tried again when it's there, Network.meshed). mesh: what the host process says of a member
    (hops, through). alias: the name it's given here (Mesh's Rename), if it's been given one: label."""

    def __init__(self, network, key, where, cached=None, found_by="hand"):
        cached = cached or {}
        self.network, self.key, self.where = network, key, dict(where)
        self.info = cached.get("info") or {}
        toolnames.learn(self.info.get("tool_names"))  # (how its MCP tools are named: every chat's cards say them so)
        self.sessions = {s["id"]: s for s in cached.get("sessions") or [] if isinstance(s, dict) and wire.is_id(s.get("id"))}
        self.syncing = {}  # session id -> the lock its mirror's written under (sync); (id, "subagents"): its subagents'
        self.agents_synced = {}  # session id -> the subagents stamp it listed that its mirror's caught up with
        self.senders = {}  # session id -> (its listed senders revision, {uuid: sender}): its sender store, as fetched
        self.fetching = set()  # (sessions whose sender stores are being fetched: sync_senders)
        self.seen = cached.get("seen") or 0.0  # when it last said anything
        self.by_hand = bool(cached.get("by_hand"))  # (given by hand: kept, whether it's been reached or not)
        self.found_by = cached.get("found_by") or found_by
        self.alias = cached.get("alias") if isinstance(cached.get("alias"), str) and cached["alias"].strip() else None
        self.tried, self.error = 0.0, None
        self.mesh = None  # (a member of this machine's mesh: {hops, through, providers, ...})
        self.deactivated = bool(cached.get("deactivated"))  # (not reached till it's activated: Network.deactivate)
        self.untold = bool(cached.get("untold"))  # (this machine's host process not told of it yet)
        self.toggled = float("-inf")  # (when it was deactivated or activated here, or the host process told)
        self.state = "connecting"  # then "alive", or "dead"
        self.stopping = None  # (what it says as it stops: {procs, left, restart, at}; till it's back)
        self.ch = None
        self.calls, self.reqs = {}, itertools.count(1)
        self.wake, self.stopped = threading.Event(), False
        self.lock = threading.Lock()

    @property
    def local(self):
        """Is it on this machine, reached by its socket (its transcripts read where they are, not mirrored)?"""
        return wire.is_local(self.where)

    @property
    def mine(self):
        """Is it this machine's own host process (its socket, wire.socket_path()): started when it's wanted, so one
        that isn't running is asleep, not gone?"""
        return self.where.get("via") == "local"

    @property
    def host(self):
        """Where it is, as people say it: user@host, or this machine."""
        return wire.said_where(self.where)

    @property
    def phone(self):
        """Is it a phone (a member that links out only: it has no host process, its app is its host)?"""
        return (self.mesh or {}).get("dial") is False or self.info.get("dial") is False

    @property
    def label(self):
        """What it's called here: the name it's given here (alias), else its own, else where it is."""
        return self.alias or self.info.get("name") or self.host

    @property
    def through(self):
        """The member of the mesh it's reached through (by name), if it's reached through the mesh and not linked to
        this machine's host process itself."""
        return (self.mesh or {}).get("through") if self.where.get("via") == "mesh" else None

    def start(self):
        threading.Thread(target=self._run, daemon=True, name=f"remote-{self.key}").start()
        return self

    def address(self, where):
        """Where it is now (found again, maybe elsewhere): tried at once if it's dead."""
        moved = dict(where) != self.where
        self.where = dict(where)
        if moved or self.state != "alive":
            self.wake.set()

    def _run(self):
        tries = 0
        while not self.stopped:
            if self.deactivated:  # (listed as it was last seen, grayed, and not reached till it's activated)
                if self.state != "dead":
                    self.state = "dead"
                    self.network.changed()
                self.wake.wait()
                self.wake.clear()
                tries = 0
                continue
            self.tried = time.monotonic()
            try:
                ch, info = wire.reach(self.where, timeout=5)
                self.error = None
            except OSError as e:
                ch, self.error = None, str(e)
            if ch is not None and self.stopped:  # (let go of while it connected: rekey's)
                ch.close()
                break
            if ch is not None and self.deactivated:  # (deactivated while it connected: not served)
                ch.close()
                continue
            if ch is not None:
                tries = 0
                self._serve(ch, info)
            if self.stopped:
                break
            if self.state != "dead":
                self.state = "dead"
                self.network.changed()
            if self.where.get("via") == "mesh":  # (tried again soon while the mesh lists it, else once it does)
                self.wake.wait(10 if self.mesh else None)
            else:
                retry = RETRY_SSH_S if self.where.get("via") == "ssh" else RETRY_S
                self.wake.wait(retry[min(tries, len(retry) - 1)])
            self.wake.clear()
            tries += 1

    def _serve(self, ch, info):
        with self.lock:
            self.ch = ch
        if not wire.is_id(info.get("id")) or info["id"] != self.key and not self.network.rekey(self, info["id"]):
            with self.lock:  # (no id fit to name its files by; or this machine's, reached by its socket already)
                self.ch = None
            ch.close()
            if not wire.is_id(info.get("id")):
                self.error = wording.say("host_status.no_id")
            return
        self.info, self.seen, self.stopping = info, time.time(), None  # (back: whatever it said as it went, done with)
        ch.sock.settimeout(wire.SILENT_S)
        ch.keep_alive()
        ch.refused_here = self.answered  # (a request refused before it went: answered at once, its reader in readline)
        ch.send({"op": "subscribe", "archived": True})  # (Baude lists those archived too: under Archived)
        for msg in ch:
            self.seen = time.time()
            op = msg.get("op")
            if op == "sessions":
                self.info = msg.get("server") or self.info
                toolnames.learn(self.info.get("tool_names"))
                # (this machine's Claude Desktop chats its host only lists: Baude lists them itself, from Desktop's
                # own list, as its own claude runs them; Record has no chats)
                now = {s["id"]: s for s in msg.get("sessions") or []
                       if isinstance(s, dict) and wire.is_id(s.get("id"))
                       and not (self.mine and s.get("taken_up") is False)}
                for sid, s in now.items():  # (a Bot rotated since: its conversation's another, fetched from nothing)
                    if (s.get("rotated") or None) != ((self.sessions.get(sid) or {}).get("rotated") or None):
                        self.forget_mirror(sid)
                self.sessions = now
                if self.state != "alive":
                    self.state = "alive"
                if self.mine:
                    self.network.meshed(self)
                self.network.changed()
            elif op == "stopping":  # (it's going: how many claudes it has still to stop, said till it's back)
                if self.stopping is None:  # (what's said of it no more, unless it's back by then: said_stopping)
                    gone = threading.Timer(STOPPING_SAID_S + 1, self.network.changed)
                    gone.daemon = True
                    gone.start()
                self.stopping = {"procs": msg.get("procs") or 0, "left": msg.get("left"),
                                 "restart": bool(msg.get("restart")),
                                 "at": self.stopping["at"] if self.stopping else time.monotonic()}
                self.network.changed()
            elif op != "ping":
                self.answered(msg)
        with self.lock:
            self.ch = None
            calls, self.calls = self.calls, {}
        ch.close()
        for done in calls.values():
            done({"op": "error", "error": "the server's gone"})

    def said_stopping(self, name):
        """What it says as it stops (stopping: how many processes it has still to stop), as a banner says it, under
        name; None if it said nothing, or long enough ago (STOPPING_SAID_S) it isn't coming back of
        itself."""
        if self.stopping is None or time.monotonic() - self.stopping["at"] > STOPPING_SAID_S:
            return None
        q = self.stopping
        return stopping_line(name, q["restart"], q["left"], q["procs"])

    def answered(self, msg):
        """A request's answer (its req's), to what's waiting on it, if anything is."""
        if (req := msg.get("req")) is not None and (then := self.calls.pop(req, None)) is not None:
            then(msg)

    def request(self, msg, done=None):
        """Send a request; done(reply) through dispatch (an error reply if the server isn't there, or the schema's
        refusal of it, at once, if it isn't one the schema has)."""
        reply = (lambda r: self.network.dispatch(lambda: done(r))) if done else (lambda r: None)
        with self.lock:
            ch = self.ch
            req = next(self.reqs)
            if ch is not None:
                self.calls[req] = reply
        if ch is None or not ch.send({**msg, "req": req}):
            self.calls.pop(req, None)
            reply({"op": "error", "error": "the server isn't there"})

    def senders_of(self, session_id):
        """Who a session's messages from others than its user are from, by their uuids, as its host's sender store
        had them when it was last fetched (sync_senders): {uuid: sender}."""
        return (self.senders.get(session_id) or (0, {}))[1]

    def heard_sender(self, session_id, said, sender):
        """Who a message just given a session's agent (uuid said) is from, as its host told it with it (_said): kept
        with its sender store as fetched, till a newer one's fetched (sync_senders)."""
        if wire.is_id(said or "") and isinstance(sender, dict) and (c := wire.clean_sender(sender)):
            rev, have = self.senders.get(session_id) or (0, {})
            self.senders[session_id] = (rev, {**have, said: {
                **c, **({"shown": sender["shown"]} if isinstance(sender.get("shown"), str) else {})}})

    def sync_senders(self, session_id, done):
        """Its host's sender store for a session fetched, if what's here is older than it lists (its senders): done()
        once a newer one's here (through dispatch)."""
        listed = (self.sessions.get(session_id) or {}).get("senders") or 0
        have = self.senders.get(session_id)
        if (have[0] if have else 0) == listed or session_id in self.fetching:
            return
        self.fetching.add(session_id)

        def got(r):
            self.fetching.discard(session_id)
            if r.get("op") != "senders" or not isinstance(r.get("senders"), dict):
                return
            self.senders[session_id] = (r.get("rev") or 0, {  # (as this knows them, with what's shown of one in
                k: {**c, **({"shown": v["shown"]} if isinstance(v.get("shown"), str) else {})}  # place of its text)
                for k, v in r["senders"].items() if wire.is_id(k) and (c := wire.clean_sender(v))})
            done()
        self.request({"op": "senders", "session": session_id}, got)

    def call(self, msg, timeout=30):
        """A request, waited for (not on the main loop): the reply, or an error one."""
        got, ready = [], threading.Event()
        with self.lock:
            ch = self.ch
            req = next(self.reqs)
            if ch is not None:
                self.calls[req] = lambda r: (got.append(r), ready.set())
        if ch is None or not ch.send({**msg, "req": req}):
            self.calls.pop(req, None)
            return {"op": "error", "error": "the server isn't there"}
        if not ready.wait(timeout):
            self.calls.pop(req, None)
            return {"op": "error", "error": "the server didn't answer"}
        return got[0]

    def mirror(self, session_id):
        """Where a session's transcript is read here: on this machine (local), its own file, as the host lists it (none
        before it's there: no copy of what's here is ever made); else its copy here, fetched as it grows (sync)."""
        if not wire.is_id(session_id) or not wire.is_id(self.key) and not self.key.startswith(("ssh:", "unix:", "local")):
            raise ValueError(f"not a session's id: {session_id!r}")
        if self.local:
            path = (self.sessions.get(session_id) or {}).get("transcript")
            if isinstance(path, str) and path.endswith(".jsonl") and os.path.isfile(path):
                return path
        return os.path.join(self.network.home, self.key, f"{session_id}.jsonl")

    def forget_mirror(self, session_id):
        """A session's copy here let go of (a Bot rotated: its conversation's a new one), its subagents' and who its
        messages were from with it: fetched again from nothing."""
        self.agents_synced.pop(session_id, None)
        self.senders.pop(session_id, None)
        if self.local:
            return  # (read where it is: nothing copied)
        try:
            with open(self.mirror(session_id), "wb"):
                pass
        except OSError:
            pass
        shutil.rmtree(self.subagents_mirror(session_id), ignore_errors=True)

    def direct(self, session_id):
        """Is a session's transcript read where it is (mirror), not copied?"""
        return not self.mirror(session_id).startswith(os.path.join(self.network.home, ""))

    def mirrored(self, session_id):
        """How much of a session's transcript its mirror has (it's the transcript's first bytes, as they are)."""
        try:
            return os.path.getsize(self.mirror(session_id))
        except OSError:
            return 0

    def sync(self, session_id, timeout=30, subagents=True):
        """Bring a session's mirror up to what the server has, then its subagents' if they're behind (one sync of a
        session's transcript at a time, whichever window asks, and of its subagents'; timeout: what each request's
        waited for). True if its transcript's is."""
        if self.local:
            return True  # (on this machine: read where it is, once it's there; nothing to fetch)
        path = self.mirror(session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with self.lock:
            lock = self.syncing.setdefault(session_id, threading.Lock())
        with lock:
            while True:
                at = self.mirrored(session_id)
                r = self.call({"op": "transcript", "session": session_id, "offset": at}, timeout)
                if r.get("op") != "transcript":
                    return False
                data = base64.b64decode(r.get("data") or "")
                with open(path, "ab" if r["offset"] == at and at else "wb") as f:  # (shorter there: written again)
                    f.write(data)
                if not data or r["next"] >= r["size"]:
                    break
        if subagents and self.subagents_behind(session_id):  # (not under the transcript's lock: a window that
            self.sync_subagents(session_id, timeout)          # waits for that, on its main loop, doesn't wait for these)
        return True

    def subagents_mirror(self, session_id):
        """Where a session's subagents' files are mirrored: beside its mirror, as claude keeps them beside its
        transcript (what reads a transcript's subagents finds them there); read where they are, for a transcript
        that is."""
        if self.direct(session_id):
            return self.mirror(session_id)[:-len(".jsonl")] + os.sep + "subagents"
        return os.path.join(self.network.home, self.key, session_id, "subagents")

    def subagents_had(self, session_id):
        """What the mirror has of a session's subagents' files: {name: [size, mtime_ns]}, each one's mtime the
        server's as it was when it was fetched."""
        out = {}
        try:
            with os.scandir(self.subagents_mirror(session_id)) as found:
                for e in found:
                    if _subagent_file(e.name) and e.is_file():
                        st = e.stat()
                        out[e.name] = [st.st_size, st.st_mtime_ns]
        except OSError:
            pass
        return out

    def subagents_behind(self, session_id):
        """Has the server changed a session's subagents' files since they were mirrored (as it last listed it)? Checked
        against the stamp the mirror last caught up with, else (before one, this run) its files' mtimes, the server's."""
        listed = (self.sessions.get(session_id) or {}).get("subagents")
        if not isinstance(listed, int) or self.local:
            return False  # (a host that doesn't say; or they're read where they are)
        synced = self.agents_synced.get(session_id)
        if synced is None:
            synced = max((m for _size, m in self.subagents_had(session_id).values()), default=0)
        return listed != synced

    def sync_subagents(self, session_id, timeout=30):
        """(sync's) A session's subagents' files mirrored as the server has them, each one's mtime set to its own
        there. True if they are."""
        with self.lock:
            lock = self.syncing.setdefault((session_id, "subagents"), threading.Lock())
        with lock:
            return self._sync_subagents(session_id, timeout)

    def _sync_subagents(self, session_id, timeout):
        folder = self.subagents_mirror(session_id)
        while True:
            had = self.subagents_had(session_id)
            r = self.call({"op": "subagents", "session": session_id, "have": had}, timeout)
            if r.get("op") != "subagents":
                return False
            got, listed = False, set()
            for f in r.get("files") or []:
                name = f.get("name") if isinstance(f, dict) else None
                if not _subagent_file(name):
                    continue
                listed.add(name)
                path, at = os.path.join(folder, name), (had.get(name) or [0])[0]
                data = base64.b64decode(f.get("data") or "")
                got |= bool(data)
                if f.get("offset") == at and at:
                    if data:
                        with open(path, "ab") as fh:
                            fh.write(data)
                elif data or f.get("offset") != at or name not in had:  # (new, changed, or shorter there)
                    os.makedirs(folder, exist_ok=True)
                    with open(os.path.join(folder, f".{name}.tmp"), "wb") as fh:
                        fh.write(data)
                    os.replace(os.path.join(folder, f".{name}.tmp"), path)
                if isinstance(f.get("mtime"), int) and os.path.exists(path) and \
                        (not data or (f.get("next") or 0) >= (f.get("size") or 0)):  # (as it is there, all of it)
                    os.utime(path, ns=(f["mtime"], f["mtime"]))
            if r.get("more") and got:
                continue
            if not r.get("more"):
                for name in set(had) - listed:  # (gone there)
                    try:
                        os.remove(os.path.join(folder, name))
                    except OSError:
                        pass
                self.agents_synced[session_id] = max(  # (what the server lists now: its files' newest)
                    (f["mtime"] for f in r.get("files") or [] if isinstance(f, dict) and isinstance(f.get("mtime"), int)),
                    default=0)
            return True

    def stop(self):
        self.stopped = True
        self.wake.set()
        with self.lock:
            ch = self.ch
        if ch is not None:
            ch.close()

    def cached(self):
        return {"where": self.where, "info": self.info, "seen": self.seen, "by_hand": self.by_hand,
                "found_by": self.found_by, "sessions": list(self.sessions.values()),
                **({"alias": self.alias} if self.alias else {}),
                **({"deactivated": True} if self.deactivated else {}), **({"untold": True} if self.untold else {})}

    def let_go(self):
        """Its connection closed, if it has one (deactivated: not made again till it's activated)."""
        with self.lock:
            ch = self.ch
        if ch is not None:
            ch.close()


class Network:
    """Every host Baude knows, and the sessions they list. listen(fn): fn() (through dispatch) when they change.
    hosts: ssh destinations to know too. None is looked for on the network: this machine's is found by its socket,
    another's is given by where ssh goes."""

    def __init__(self, dispatch, home=None, hosts=(), readonly=False):
        self.dispatch, self.home = dispatch, home or network_home()
        self.readonly = readonly  # (servers.json read, never written: Record's, Baude's being the one that keeps it)
        self.servers, self.listeners, self.lock = {}, [], threading.RLock()
        self.saved_at, self.pending, self.looked = 0.0, False, float("-inf")
        self.stopped, self.forgotten = threading.Event(), set()  # (forgotten: gone FORGET_S, dropped from the file)
        self.linked_pid = None  # (the host process here that's been asked to link to the hosts given by hand)
        self.removed_known = self.removed_saved()  # (what servers.json listed as removed when last read)
        self.removed = set(self.removed_known)  # (removed here, by its id or key: not known again till it's added by hand)
        self.unremoved = set()  # (no longer removed: added again by hand, this run)
        for key, c in self.saved().items():
            if self.is_removed(key, c):
                continue
            if time.time() - (c.get("seen") or 0) < FORGET_S or c.get("by_hand") or c.get("deactivated"):
                self.servers[key] = Remote(self, key, c["where"], c)
            else:
                self.forgotten.add(key)
        for dest in hosts:
            if wire.is_dest(dest := dest.strip()) and not any(r.where.get("dest") == dest for r in self.servers.values()):
                self.servers[f"ssh:{dest}"] = Remote(self, f"ssh:{dest}", {"via": "ssh", "dest": dest})
        for r in list(self.servers.values()):
            r.start()
        self.here()
        threading.Thread(target=self._tick, daemon=True, name="network-tick").start()

    def _file(self):
        """servers.json as it is on disk now, whole ({} if it can't be read)."""
        try:
            with open(os.path.join(self.home, "servers.json"), encoding="utf-8") as f:
                cache = json.load(f)
        except (OSError, ValueError):
            return {}
        return cache if isinstance(cache, dict) else {}

    def saved(self):
        """servers.json as it is on disk now: {key: what was kept of the host} (one from before hosts were reached by
        ssh or their socket, at a port: none)."""
        servers = self._file().get("servers")
        return {k: c for k, c in (servers if isinstance(servers, dict) else {}).items()
                if isinstance(c, dict) and _where(c.get("where"))}

    def removed_saved(self):
        """servers.json's hosts removed here (remove), by id or key."""
        removed = self._file().get("removed")
        return {k for k in removed if isinstance(k, str)} if isinstance(removed, list) else set()

    def is_removed(self, key, cached=None):
        """Is a host (by its key, and what was kept of it) one that's been removed here?"""
        return key in self.removed or ((cached or {}).get("info") or {}).get("id") in self.removed

    def _tick(self):
        """Every few seconds: this machine's host process looked for (here), and servers.json taken up as it's edited."""
        while not self.stopped.wait(5):
            self.here()
            self.take_removed()
            self.take_saved()

    def here(self):
        """This machine's host process, once its socket's there: known, by its id once it's said it."""
        if not os.path.exists(wire.socket_path()):
            return
        with self.lock:
            if any(r.where.get("via") == "local" for r in self.servers.values()):
                return
            self.servers["local"] = Remote(self, "local", {"via": "local"}, found_by="local").start()
        self.changed()

    def take_saved(self):
        """servers.json's hosts that aren't known here (put in it while Baude runs): known, and tried."""
        for key, c in self.saved().items():
            with self.lock:
                if key in self.servers or key in self.forgotten or self.is_removed(key, c) or \
                        any(r.where == c["where"] for r in self.servers.values()):
                    continue
                self.servers[key] = Remote(self, key, c["where"], c).start()
            self.changed()

    def unremove(self, key):
        """(lock held) A host added again by hand: no longer removed."""
        if key in self.removed:
            self.removed.discard(key)
            self.unremoved.add(key)

    def sync_removed(self, on_disk):
        """(lock held) What servers.json lists as removed is the truth: a host it no longer lists (added again by hand
        in the other app) is no longer removed here; one removed here and not written yet stays."""
        self.removed -= self.removed_known - on_disk
        self.removed_known = set(on_disk)

    def take_removed(self):
        """servers.json's hosts removed as it's edited (by Baude or Record): let go of here too."""
        on_disk = self.removed_saved()
        with self.lock:
            self.sync_removed(on_disk)
            on_disk = on_disk - self.unremoved
            new = on_disk - self.removed
            self.removed |= on_disk
            gone = [r for r in self.servers.values() if not r.mine and {r.key, r.info.get("id")} & new]
        for r in gone:
            self.drop(r)
        if gone:
            self.changed()

    def listen(self, fn):
        self.listeners.append(fn)

    def add(self, where, by_hand=False):
        """A host given by where it is (wire.reach's where: an ssh destination, mostly), known by its id once it's said
        who it is. by_hand: kept in servers.json from now on, reached or not (Baude's Add host…)."""
        with self.lock:
            r = next((r for r in self.servers.values() if r.where == dict(where)), None)
            if r is None:
                key = f"ssh:{where['dest']}" if where.get("via") == "ssh" else f"unix:{where.get('path')}"
                if by_hand:  # (added again: no longer removed)
                    self.unremove(key)
                if (old := self.servers.get(key)) is not None:  # (the same destination, reached otherwise: this one)
                    old.stop()
                r = self.servers[key] = Remote(self, key, where, found_by="hand")
                r.start()
            r.by_hand = r.by_hand or by_hand
        if by_hand:
            self.link(r)
            self.save()
            self.changed()
        return r

    def rename(self, r, alias):
        """A host's name here (Mesh's Rename): alias, or its own again with none. Only here: the host keeps its own."""
        r.alias = (alias or "").strip()[:120] or None
        self.save(force=True, only=r.key)
        self.changed()

    def remove(self, r):
        """A host removed here (Mesh's Remove): this machine's host process's link to it let go of (a phone's, which
        links here, kept), and it's not reached, listed or kept here from now on, even while the mesh reaches it
        through another member, till it's added again by hand. Nothing on the host changes."""
        if r.mine:
            return
        rid = r.info.get("id") if wire.is_id(r.info.get("id")) else None
        if (here := self.here_alive()) is not None:
            linked = r.by_hand and r.where.get("via") in ("ssh", "unix")
            straight = rid if rid and not r.phone else None
            if linked or straight:
                here.request({"op": "unlink", **({"where": r.where} if linked else {}),
                              **({"id": straight} if straight else {})})
        with self.lock:
            self.removed |= {k for k in (r.key, rid, *getattr(r, "was", ())) if k}
        self.drop(r)
        self.save(force=True)
        self.changed()

    def drop(self, r):
        """A host let go of here, and not kept in servers.json."""
        r.stop()
        with self.lock:
            if self.servers.get(r.key) is r:
                del self.servers[r.key]
            self.forgotten.add(r.key)

    # ---- the mesh: this machine's host process linked to each host given by hand, the link used both ways, and
    # every member it reaches known here, reached through it (baudelib/mesh.py)
    def here_alive(self):
        """This machine's host process, if it's there."""
        with self.lock:
            return next((r for r in self.servers.values() if r.mine and r.state == "alive"), None)

    def deactivate(self, r, off=True):
        """A host deactivated (off) or activated again: deactivated, it isn't reached, by this window or this
        machine's host process, which lets go of its links and refuses its joins and tunnels; its chats stay listed as
        they were last seen. Kept in servers.json, and by the host process."""
        r.deactivated, r.toggled = off, time.monotonic()
        if off:
            r.let_go()
        r.wake.set()
        r.untold = True
        if (here := self.here_alive()) is not None and not r.mine:
            self.tell(r, here)
        self.save(force=True, only=r.key)
        self.changed()

    def tell(self, r, here):
        """This machine's host process told whether a host's deactivated here (untold: not yet, kept till it is)."""
        r.untold, r.toggled = False, time.monotonic()
        here.request({"op": "deactivate" if r.deactivated else "activate",
                      **({"id": r.info["id"]} if wire.is_id(r.info.get("id")) else {}),
                      **({"where": r.where} if r.where.get("via") in ("ssh", "unix") else {})})

    def link(self, r):
        """A host given by hand, linked to from this machine's host process: it, and every member it
        reaches, can reach here too. Kept there; asked again each time it's started."""
        if r.by_hand and r.where.get("via") in ("ssh", "unix") and (here := self.here_alive()) is not None:
            here.request({"op": "link", "where": r.where})

    def meshed(self, here):
        """(this machine's host process said what its info is) Each member of its mesh known here, reached through it
        (one known otherwise, over ssh, say, reached so), and tried again if it's gone; and, once each time it's
        started, every host given by hand linked to from it."""
        state = here.info.get("mesh")
        if not isinstance(state, dict):
            return
        if here.info.get("pid") != self.linked_pid:
            self.linked_pid = here.info.get("pid")
            with self.lock:
                given = [r for r in self.servers.values() if r.by_hand]
            threading.Thread(target=lambda: [self.link(r) for r in given], daemon=True, name="link").start()
        members = [m for m in state.get("members") or [] if isinstance(m, dict) and wire.is_id(m.get("id"))]
        # (what's deactivated is as the host process has it: here, or its command line's; once it's told what was
        # changed here meanwhile)
        with self.lock:
            remotes = [r for r in self.servers.values() if not r.mine]
        for r in remotes:
            if r.untold:
                self.tell(r, here)
        off = set(state.get("inactive") or [])
        off_where = [x["where"] for x in state.get("links") or []
                     if isinstance(x, dict) and x.get("active") is False]
        for r in remotes:
            was = r.deactivated
            now = (r.info.get("id") or r.key) in off or any(mesh.same_place(w, r.where) for w in off_where)
            if now != was and time.monotonic() - r.toggled > 5:  # (not one just changed here, not told yet)
                r.deactivated = now
                if now:
                    r.let_go()
                else:
                    r.state = "connecting"
                r.wake.set()
                self.save()
                self.changed()
        with self.lock:
            listed = {m["id"] for m in members}
            for r in self.servers.values():
                if r.mesh is not None and r.key not in listed and r.info.get("id") not in listed:
                    r.mesh = None  # (gone from the mesh: tried again once it's back)
            known = {r.info.get("id") for r in self.servers.values() if r.where.get("via") != "mesh"}
            for m in members:
                if m["id"] == here.info.get("id") or m["id"] in self.removed:
                    continue
                r = self.servers.get(m["id"])
                if r is None and m["id"] not in known:
                    r = self.servers[m["id"]] = Remote(self, m["id"], {"via": "mesh", "host": m["id"]}, found_by="mesh")
                    r.info = {k: m.get(k) for k in ("id", "name", "group", "host")}
                    r.start()
                if r is None:
                    continue
                back, r.mesh = r.mesh is None, m  # (back: listed again, as it wasn't)
                if r.where.get("via") == "mesh" and r.state != "alive" and (back or time.monotonic() - r.tried > 5):
                    r.wake.set()

    def members(self):
        """The mesh's members, as this machine's host process has them: [{id, name, group, host, hops, through,
        providers}] ([] with none, or no host process here)."""
        here = self.here_alive()
        return list(((here.info.get("mesh") or {}).get("members") or [])) if here is not None else []

    def links(self):
        """The links this machine's host process makes: [{where, id, name, state ("linked", "connecting",
        "dropped"), error}]."""
        here = self.here_alive()
        return list(((here.info.get("mesh") or {}).get("links") or [])) if here is not None else []

    def providers(self):
        """Every model provider in the mesh, this machine's first, each {host: {id, name, here}, id, kind, name,
        models: [{value, displayName, id, tools, window, input, thinking}], base_url, api_key, endpoint}. Another
        member's that serves HTTP has base_url and api_key for the proxy in this machine's host process, an
        OpenAI-compatible server here that runs the model there, and endpoint, its id among mesh.endpoints() (the same
        every run); a program's (apple-claude, pi-claude) has none: a chat started on that member (Remote) runs it.
        This machine's own have their base_url, and no key. [] with no host process here."""
        here = self.here_alive()
        if here is None:
            return []
        me = {"id": here.info.get("id"), "name": here.info.get("name"), "here": True}
        out = [{**p, "host": me, "base_url": p.get("base_url"), "api_key": None, "endpoint": None}
               for p in here.info.get("providers") or [] if isinstance(p, dict)]
        proxy = (here.info.get("mesh") or {}).get("proxy") or {}
        for m in self.members():
            host = {"id": m.get("id"), "name": m.get("name"), "here": False}
            for p in m.get("providers") or []:
                served = bool(p.get("http") and proxy.get("url"))
                out.append({**p, "host": host, "api_key": proxy.get("key") if served else None,
                            "base_url": f"{proxy['url']}/{m['id']}/{urllib.parse.quote(p['id'], safe='')}"
                            if served else None, "endpoint": mesh.endpoint_id(m["id"], p["id"]) if served else None})
        return out

    def look(self):
        """Look again now, as a restart would (Baude's window came forward, its ⋮ menu opened): every host that's gone
        tried again, not at its next retry."""
        if time.monotonic() - self.looked < 2:
            return
        self.looked = time.monotonic()
        with self.lock:
            servers = list(self.servers.values())
        for r in servers:
            if r.state != "alive":
                r.wake.set()

    def rekey(self, remote, key):
        """A host given by where it is said who it is: known by its id from now on (one remembered by it, folded in).
        This machine's, by its socket, stays so: the same one reached over ssh too (by hand, say) is let go."""
        with self.lock:
            if key in self.removed and not remote.by_hand:  # (removed here: taken up again only by hand)
                remote.stop()
                if self.servers.get(remote.key) is remote:
                    del self.servers[remote.key]
                return False
            self.unremove(key)
            old = self.servers.get(key)
            if old is not None and old is not remote and old.local and not remote.local and old.state == "alive":
                remote.stop()
                if self.servers.get(remote.key) is remote:
                    del self.servers[remote.key]
                self.forgotten.add(remote.key)  # (not kept in servers.json either)
                return False
            if self.servers.get(remote.key) is remote:
                del self.servers[remote.key]
            old = self.servers.pop(key, None)
            if old is not None and old is not remote:
                old.stop()
                remote.sessions = remote.sessions or old.sessions
                remote.by_hand = remote.by_hand or old.by_hand
            if remote.key != key:
                self.forgotten.add(remote.key)  # (where it was known by: not a host of its own in servers.json)
                remote.was = getattr(remote, "was", set()) | {remote.key}  # (what Baude may still know it by)
            remote.key = key
            self.servers[key] = remote
        return True

    def changed(self):
        """Its listeners hear soon (a burst of changes is one call), and what's known is saved."""
        with self.lock:
            if self.pending:
                return
            self.pending = True

        def tell():
            with self.lock:
                self.pending = False
            if time.time() - self.saved_at > 5:  # (a host says something changed every time a transcript grows)
                self.save()
            for fn in list(self.listeners):
                fn()
        self.dispatch(tell)

    def save(self, force=False, only=None):
        """servers.json: every host reached or given by hand, and what's in it that isn't known here (put there
        meanwhile: take_saved takes it up), not written over; and those removed. (readonly: not written, but for
        force: a change the user made here to one host, only, its key, or a host removed, the rest left as they are
        there.)"""
        self.saved_at = time.time()
        if self.readonly and not force:
            return
        if self.readonly:
            return self._save_one(only)
        on_disk, removed = self.saved(), self.removed_saved()
        with self.lock:
            self.sync_removed(removed)
            removed = removed - self.unremoved
            self.removed |= removed  # (Record's, meanwhile, kept too)
            servers = {k: r.cached() for k, r in self.servers.items() if r.info.get("id") or r.sessions or r.by_hand}
            for key, c in on_disk.items():
                if key not in self.servers and key not in self.forgotten and not self.is_removed(key, c) and \
                        not any(r.where == c["where"] for r in self.servers.values()):
                    servers[key] = c
            data = {"servers": servers, **({"removed": sorted(self.removed)} if self.removed else {})}
        target = os.path.join(self.home, "servers.json")
        try:
            os.makedirs(self.home, exist_ok=True)
            tmp = f"{target}.{os.getpid()}.tmp"  # (another process's write at once: not into the same file)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, target)
        except OSError:
            pass

    def _save_one(self, key):
        """servers.json with one host's entry as it is here (key; None: those removed here only), every other as it is
        there (a read-only Network's: what it knows of the rest may be older than Baude's)."""
        on_file = self.removed_saved()
        with self.lock:
            self.sync_removed(on_file)
            r = self.servers.get(key) if key else None
            mine = r.cached() if r is not None else None
            removed = self.removed | (on_file - self.unremoved)
        if mine is None and key:
            return
        on_disk = {k: c for k, c in self.saved().items() if not (k in removed or (c.get("info") or {}).get("id") in removed)}
        if mine is not None:
            on_disk[key] = mine
        target = os.path.join(self.home, "servers.json")
        try:
            os.makedirs(self.home, exist_ok=True)
            tmp = f"{target}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"servers": on_disk, **({"removed": sorted(removed)} if removed else {})}, f)
            os.replace(tmp, target)
        except OSError:
            pass

    def groups(self):
        """The groups the network has: those its hosts' sessions were started in, and each host's own."""
        out = set()
        with self.lock:
            for r in self.servers.values():
                if r.state == "alive" and r.info.get("group"):
                    out.add(r.info["group"])
                out.update(s.get("group") for s in r.sessions.values() if s.get("group"))
        return sorted(out, key=str.lower)

    def sessions(self, group=None):
        """Every host's sessions (of a group, if given), each with "server": the Remote it's on."""
        with self.lock:
            return [{**s, "server": r} for r in self.servers.values() for s in r.sessions.values()
                    if group is None or s.get("group") == group]

    def remotes(self):
        """Every host known, there or not: those reached once, or given by hand."""
        with self.lock:
            return [r for r in self.servers.values() if r.info.get("id") or r.by_hand]

    def find(self, session_id):
        with self.lock:
            return next((r for r in self.servers.values() if session_id in r.sessions), None)

    def alive(self, group=None):
        """The hosts that are there (those whose group is group, or with sessions in it, first)."""
        with self.lock:
            alive = [r for r in self.servers.values() if r.state == "alive"]
        return sorted(alive, key=lambda r: (group is not None and r.info.get("group") != group and not any(
            s.get("group") == group for s in r.sessions.values()), (r.info.get("name") or "").lower()))

    def stop(self):
        self.stopped.set()
        with self.lock:
            servers = list(self.servers.values())
        for r in servers:
            r.stop()


def _where(where):
    """Is it somewhere wire.reach can go?"""
    return wire.is_where(where)


class _Remote:
    """What stands for a RemoteProc's process (Baude's debug report asks after its pid)."""

    def __init__(self, proc, args):
        self.proc, self.args, self.pid = proc, args, None

    def poll(self):
        """None while it's attached; then its claude's exit, or -1 once it's let go of (killed: the session goes on, on
        its host, or goes with it: a side question's)."""
        if self.proc.ch is not None and not self.proc.ch.closed:
            return None
        return self.proc.code if self.proc.code is not None else -1 if self.proc.killed else None


class Refused(OSError):
    """A host that answered, but wouldn't attach a session (gone from it, being deleted…): RemoteProc's."""


class RemoteProc(claude.ClaudeProc):
    """A session on a host (where: wire.reach's), talked to as ClaudeProc talks to a local claude: what's sent goes to its claude (started
    on the server if it isn't running), what it says comes to on_msg. live: everything it says, as it says it; else
    only what it asks (a permission) and the answers to what's asked of it (initialize: its models and commands).
    kill() lets go of it: the session goes on, on its server. OSError if it can't be reached. on_exit's code: its
    claude's exit code, as its host said it; "stopped", its host told to stop it (Stop); "deleted", the session
    deleted there; "lost", the connection gone with none of those said (its host process went, or the link fell
    silent)."""

    def __init__(self, on_msg, on_exit, where, session_id, live=True, env=None, always_allow=None):
        self.on_msg, self.on_exit = on_msg, on_exit
        self.req_ids = itertools.count(1)
        self.session, self.live, self.code, self.killed = session_id, live, None, False
        self.answering = {}  # (requests of its own sent on it, held's: their req -> what's done with the answer)
        self.ch, self.server = wire.reach(where, timeout=3)
        self.p = _Remote(self, [wire.said_where(where), session_id])
        self.ch.sock.settimeout(5)
        # (env: this machine's head's environment, for its claude, as Baude's own would have had: a host here's only)
        self.ch.send({"op": "attach", "session": session_id, "live": live, "req": 0, **({"env": env} if env else {}),
                      **({"always_allow": list(always_allow)} if always_allow else {})})
        reply = self.ch.reply()
        if not reply or reply.get("op") != "attached":
            self.ch.close()
            raise (Refused if reply else OSError)(wire.said(reply) or "the server didn't answer")
        self.ch.sock.settimeout(wire.SILENT_S)  # (the server pings: silence this long, and it's gone)
        self.ch.keep_alive()
        self.ch.refused_here = lambda r: (done := self.answering.pop(r.get("req"), None)) and \
            self.dispatch(lambda: done(r))  # (held refused before it went: answered at once)
        self.listing = reply.get("session") or {}
        self.asks = {m["request_id"]: m for m in reply.get("asks") or [] if m.get("request_id")}
        self.login = reply.get("login")  # (its sign-in, as its host has it: ChatLogin, or None)
        self.keeps_login = "login" in reply  # (a host that keeps it: one that doesn't, its heads' cards their own)
        self.writes = reply.get("writes") is not False  # (false: its host lets this head only read it)
        threading.Thread(target=self._read_remote, daemon=True, name=f"remote-proc-{session_id}").start()
        self.init_rid = self.control({"subtype": "initialize", "hooks": None}) if self.writes else None

    def _read_remote(self):
        for msg in self.ch:
            op = msg.get("op")
            if (done := self.answering.pop(msg.get("req"), None)) is not None:  # (held's answer: its own, not a turn's)
                self.dispatch(lambda m=msg, done=done: done(m))
            elif op == "claude" and msg.get("session") == self.session:
                self.dispatch(lambda m=msg["msg"]: self.on_msg(self, m))
            elif op == "login_state" and msg.get("session") == self.session:  # (its sign-in, its host's: told
                self.login = msg.get("login")  # as {type: "_login"}, as a note of its host's is "_note")
                self.dispatch(lambda m=msg: self.on_msg(self, {"type": "_login", "login": m.get("login")}))
            elif op == "exited" and msg.get("session") == self.session:  # ("stopped": its host was told to stop it)
                self.code = "stopped" if msg.get("stopped") else msg.get("code")
                break
            elif op == "deleted" and msg.get("session") == self.session:  # (gone from its host, for good)
                self.code = "deleted"
                break
            elif op == "error":  # (what was sent went nowhere: said, and the turn's over; plain, as the server put it;
                said = msg.get("error") if msg.get("plain") else \
                    wording.say("host_status.server_said", why=wire.said(msg))  # gone: a folder's)
                self.dispatch(lambda e=said, g=msg.get("gone"): self.on_msg(self, {
                    "type": "result", "subtype": "error", "is_error": True, "result": e, **({"gone": g} if g else {})}))
        self.ch.close()
        answering, self.answering = self.answering, {}  # (asked on it, not answered: no answer's coming)
        for done in answering.values():
            dropped = wording.say("host_status.connection_dropped")
            self.dispatch(lambda done=done: done({"op": "error", "error": dropped}))
        if not self.killed:  # ("lost": let go of with no exit said, its claude's or its host's: not a code of claude's)
            code = "lost" if self.code is None else self.code
            self.dispatch(lambda: self.on_exit(code))

    def send(self, obj):
        if self.writes:  # (one only read here: nothing of this head's goes to its claude)
            self.ch.send({"op": "input", "session": self.session, "msg": obj})

    def held(self, held_id, send, done):
        """A message its host holds for it (held) sent (send) or dropped, on this connection, so what its claude says
        of it comes here as it's said: done(answer) with the host's answer (op held, or error, why), on the main
        loop."""
        req = next(self.req_ids)
        self.answering[req] = done
        if not self.ch.send({"op": "held", "session": self.session, "id": held_id, "send": bool(send), "req": req}):
            self.answering.pop(req, None)
            self.dispatch(lambda: done({"op": "error", "error": wording.say("host_status.host_absent")}))

    def close_stdin(self):
        pass  # (nothing to close here: the server's claude goes on)

    def kill(self):
        self.killed = True
        self.ch.send({"op": "detach", "session": self.session})
        self.ch.close()
