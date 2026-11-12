"""Hosts as Baude sees them: this machine's host process (its socket), another machine's over ssh, found on the network
(discovery.Browser: a host that says Baude is on it, and its ssh user), among Tailscale's peers, given by hand, or
remembered from before; the sessions each lists; a session's transcript, read where it is on this machine, else mirrored
here, its subagents' beside it, so what reads a transcript reads it as it reads a local one; and a session's claude,
talked to as Baude talks to its own (RemoteProc). wire.py says how each is reached.

A host that goes (its connection drops, or it says nothing for wire.SILENT_S) isn't forgotten: it's "dead", its
sessions still listed as they were, until it's back or it's been gone for FORGET_S. Calls to what's given (on_change,
a request's callback, RemoteProc's on_msg and on_exit) come through dispatch, as ClaudeProc's do."""
import base64
import itertools
import json
import os
import shutil
import socket
import subprocess
import threading
import time

from baudelib import claude, wire
from baudelib.discovery import Browser

FORGET_S = 7 * 86400
RETRY_S = (1, 2, 5, 10, 30)  # a dead host is tried again after these, then every last one
RETRY_SSH_S = (2, 10, 30, 120, 300)  # (one over ssh: a login each time, on another machine)
PEERS_S = 5 * 60  # Tailscale's peers looked at again, and those Baude wasn't on tried again, at most this often
PEERS_MAX = 16  # (of them, those tried, one at a time)
OFFERED_MAX = 32  # hosts found on the network, offered till one's taken up (Network.accept)
TAILSCALE_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"  # (macOS's, where tailscale isn't on PATH)


def network_home():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "network")


def _subagent_file(name):
    """A name a subagents folder's file may have (and one to write here: no path in it)."""
    return isinstance(name, str) and name.endswith((".jsonl", ".meta.json")) and not name.startswith(".") \
        and os.path.basename(name) == name and "\\" not in name


def is_here(info):
    """Is a server on this machine (its folders are this machine's)?"""
    return bool(info) and (info.get("host") or "").split(".")[0].lower() == socket.gethostname().split(".")[0].lower()


class Remote:
    """One host: where it is (where: wire.reach's), its info (as hello says), its sessions (as it lists them), and
    whether it's there. found_by: "local" (this machine's socket), "network" (multicast DNS), "tailscale", "hand";
    probe: one not reached yet that may not be one (a Tailscale peer): tried when the network's looked at, not again
    and again. command: its host process's command, as given here (what it's said to be otherwise)."""

    def __init__(self, network, key, where, cached=None, found_by="hand", probe=False):
        cached = cached or {}
        self.network, self.key, self.where = network, key, dict(where)
        self.info = cached.get("info") or {}
        self.sessions = {s["id"]: s for s in cached.get("sessions") or [] if isinstance(s, dict) and wire.is_id(s.get("id"))}
        self.syncing = {}  # session id -> the lock its mirror's written under (sync); (id, "subagents"): its subagents'
        self.agents_synced = {}  # session id -> the subagents stamp it listed that its mirror's caught up with
        self.seen = cached.get("seen") or 0.0  # when it last said anything
        self.by_hand = bool(cached.get("by_hand"))  # (given by hand: kept, whether it's been reached or not)
        self.found_by = cached.get("found_by") or found_by
        self.command = cached.get("command") if isinstance(cached.get("command"), str) else None
        self.probe, self.tried, self.error = probe and not self.info, 0.0, None
        self.attempted = threading.Event()  # (set as each try's done: peers waits for one before the next)
        self.offered = False  # (found on the network: listed, not reached, till it's taken up: Network.accept)
        self.state = "connecting"  # then "alive", or "dead"
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

    def reach_where(self):
        return {**self.where, "command": self.command} if self.command and self.where.get("via") == "ssh" else self.where

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
            self.tried = time.monotonic()
            try:
                ch, info = wire.reach(self.reach_where(), timeout=5)
                self.error = None
            except OSError as e:
                ch, self.error = None, str(e)
            if ch is not None and self.stopped:  # (let go of while it connected: rekey's)
                ch.close()
                break
            if ch is not None:
                tries, self.probe = 0, False
                self.attempted.set()
                self._serve(ch, info)
            self.attempted.set()
            if self.stopped:
                break
            if self.state != "dead":
                self.state = "dead"
                self.network.changed()
            if self.probe:  # (not one, or not yet: tried again when the network's next looked at, not before)
                self.wake.wait()
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
                self.error = "it gave no id Baude can use"
            return
        self.info, self.seen = info, time.time()
        ch.sock.settimeout(wire.SILENT_S)
        ch.keep_alive()
        ch.send({"op": "subscribe", "archived": True})  # (Baude lists those archived too: under Archived)
        for msg in ch:
            self.seen = time.time()
            op = msg.get("op")
            if op == "sessions":
                self.info = msg.get("server") or self.info
                self.sessions = {s["id"]: s for s in msg.get("sessions") or []
                                 if isinstance(s, dict) and wire.is_id(s.get("id"))}
                if self.state != "alive":
                    self.state = "alive"
                self.network.changed()
            elif op != "ping" and (req := msg.get("req")) is not None and req in self.calls:
                self.calls.pop(req)(msg)
        with self.lock:
            self.ch = None
            calls, self.calls = self.calls, {}
        ch.close()
        for done in calls.values():
            done({"op": "error", "error": "the server's gone"})

    def request(self, msg, done=None):
        """Send a request; done(reply) through dispatch (an error reply if the server isn't there)."""
        reply = (lambda r: self.network.dispatch(lambda: done(r))) if done else (lambda r: None)
        with self.lock:
            ch = self.ch
            req = next(self.reqs)
            if ch is not None:
                self.calls[req] = reply
        if ch is None or not ch.send({**msg, "req": req}):
            self.calls.pop(req, None)
            reply({"op": "error", "error": "the server isn't there"})

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
                "found_by": self.found_by, "command": self.command, "sessions": list(self.sessions.values())}


class Network:
    """Every host Baude knows, and the sessions they list. listen(fn): fn() (through dispatch) when they change.
    discover: look on the network (multicast DNS) and among Tailscale's peers; hosts: ssh destinations to know too."""

    def __init__(self, dispatch, home=None, discover=True, hosts=()):
        self.dispatch, self.home = dispatch, home or network_home()
        self.servers, self.listeners, self.lock = {}, [], threading.RLock()
        self.saved_at, self.pending, self.looked, self.peered = 0.0, False, float("-inf"), float("-inf")
        self.stopped, self.forgotten = threading.Event(), set()  # (forgotten: gone FORGET_S, dropped from the file)
        for key, c in self.saved().items():
            if time.time() - (c.get("seen") or 0) < FORGET_S or c.get("by_hand"):
                self.servers[key] = Remote(self, key, c["where"], c)
            else:
                self.forgotten.add(key)
        for dest in hosts:
            if wire.is_dest(dest := dest.strip()) and not any(r.where.get("dest") == dest for r in self.servers.values()):
                self.servers[f"ssh:{dest}"] = Remote(self, f"ssh:{dest}", {"via": "ssh", "dest": dest})
        for r in list(self.servers.values()):
            r.start()
        self.discover = discover
        self.browser = Browser(self.found, self.lost).start() if discover else None
        self.here()
        if discover:
            threading.Thread(target=self.peers, daemon=True, name="tailscale-peers").start()
        threading.Thread(target=self._tick, daemon=True, name="network-tick").start()

    def saved(self):
        """servers.json as it is on disk now: {key: what was kept of the host} (one from before hosts were reached by
        ssh or their socket, at a port: none)."""
        try:
            with open(os.path.join(self.home, "servers.json"), encoding="utf-8") as f:
                cache = json.load(f)
        except (OSError, ValueError):
            return {}
        return {k: c for k, c in (cache.get("servers") or {}).items() if isinstance(c, dict) and _where(c.get("where"))}

    def _tick(self):
        """Every few seconds: this machine's host process looked for (here), and servers.json taken up as it's edited."""
        while not self.stopped.wait(5):
            self.here()
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
                if key in self.servers or key in self.forgotten or \
                        any(r.where == c["where"] for r in self.servers.values()):
                    continue
                self.servers[key] = Remote(self, key, c["where"], c).start()
            self.changed()

    def listen(self, fn):
        self.listeners.append(fn)

    def add(self, where, by_hand=False, command=None):
        """A host given by where it is (wire.reach's where: an ssh destination, mostly), known by its id once it's said
        who it is. by_hand: kept in servers.json from now on, reached or not (Baude's Add host…); command: its host
        process's, there."""
        with self.lock:
            r = next((r for r in self.servers.values() if r.where == dict(where)), None)
            if r is None:
                key = f"ssh:{where['dest']}" if where.get("via") == "ssh" else f"unix:{where.get('path')}"
                if (old := self.servers.get(key)) is not None:  # (the same destination, reached otherwise: this one)
                    old.stop()
                r = self.servers[key] = Remote(self, key, where, found_by="hand")
                r.command = command or r.command
                r.start()
            elif command is not None:
                r.command = command or None
                r.wake.set()
            r.by_hand = r.by_hand or by_hand
        if by_hand or command is not None:
            self.save()
            self.changed()
        return r

    def set_command(self, r, command):
        """A host's process's command there, as given here (None: what it says, or SSH_COMMAND): tried again with it."""
        r.command = command or None
        r.wake.set()
        self.save()

    def found(self, service):
        """A host on the network said Baude is on it: offered (listed, as found), not reached: anyone on the network
        can say anything, so it's reached only once it's taken up (accept), over ssh, by its own name on the network
        (name.local) and as the user it says, its key known already. None's command is taken from what it says."""
        words = service["words"]
        key, user = words.get("id"), words.get("user")
        target = (service.get("target") or "").rstrip(".")
        if not wire.is_id(key) or not target.lower().endswith(".local") or not wire.is_dest(target) or "@" in target \
                or user is not None and not wire._NAME.fullmatch(str(user)):
            return
        where = {"via": "ssh", "dest": f"{user}@{target}" if user else target, "strict": True}
        with self.lock:
            r = self.servers.get(key)
            if r is not None:
                if r.offered:
                    r.where, r.seen, r.instance = where, time.time(), service.get("instance")
                return  # (one known already is reached as it was: not wherever the network now says)
            if sum(x.offered for x in self.servers.values()) >= OFFERED_MAX:
                return
            r = self.servers[key] = Remote(self, key, where, found_by="network")
            r.offered, r.seen, r.instance = True, time.time(), service.get("instance")
            r.info = {"name": str(service.get("instance") or target).split(".")[0][:64]}
            r.state = "dead"  # (not reached)
        self.changed()

    def lost(self, instance):
        """A host on the network said it's gone: if it was only offered, not offered any more."""
        with self.lock:
            gone = [k for k, r in self.servers.items() if r.offered and getattr(r, "instance", None) == instance]
            for k in gone:
                del self.servers[k]
        if gone:
            self.changed()

    def accept(self, r):
        """A host offered (found on the network) taken up: reached from now on, and kept, as one given by hand is."""
        with self.lock:
            if not r.offered:
                return
            r.offered, r.by_hand, r.info = False, True, {}
            r.state = "connecting"
        r.start()
        self.save()
        self.changed()

    def peers(self):
        """Tailscale's peers (`tailscale status --json`, where it's installed), as multicast DNS doesn't cross a tailnet:
        each one online that isn't a phone, PEERS_MAX at most, tried over ssh by its name there (its key must be known
        already), one at a time, and a host once it's answered as one. Again at most every PEERS_S (look), those
        Baude wasn't on tried again then."""
        self.peered = time.monotonic()
        exe = os.environ.get("BAUDE_TAILSCALE") or shutil.which("tailscale") or \
            (TAILSCALE_APP if os.path.exists(TAILSCALE_APP) else None)
        if not exe:
            return
        try:
            out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=10)
            peers = (json.loads(out.stdout).get("Peer") or {}).values()
        except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
            return
        tried = 0
        for p in peers:
            if tried >= PEERS_MAX or self.stopped.is_set():
                break
            if not isinstance(p, dict) or not p.get("Online") or str(p.get("OS") or "").lower() in ("ios", "android"):
                continue
            dest = str(p.get("DNSName") or "").rstrip(".") or str(p.get("HostName") or "")
            if not wire.is_dest(dest) or "@" in dest:
                continue
            with self.lock:
                r = next((r for r in self.servers.values() if r.where.get("dest") == dest), None)
                if r is None:
                    r = self.servers[f"ssh:{dest}"] = Remote(self, f"ssh:{dest}", {"via": "ssh", "dest": dest,
                                                             "strict": True}, found_by="tailscale", probe=True)
                    r.start()
                elif r.probe and r.state != "alive":
                    r.attempted.clear()
                    r.wake.set()
                else:
                    continue
            tried += 1
            r.attempted.wait(30)  # (one at a time: not a burst of logins)
            r.attempted.clear()

    def look(self):
        """Look again now, as a restart would (Baude's window came forward, its ⋮ menu opened): who's there asked at
        once, not at the next discovery.QUERY_S, every host that's gone tried again, not at its next retry, and
        Tailscale's peers, if it's been PEERS_S."""
        if time.monotonic() - self.looked < 2:
            return
        self.looked = time.monotonic()
        if self.browser is not None:
            self.browser.ask_soon()
        if self.discover and time.monotonic() - self.peered > PEERS_S:
            threading.Thread(target=self.peers, daemon=True, name="tailscale-peers").start()
        with self.lock:
            servers = list(self.servers.values())
        for r in servers:
            if r.state != "alive" and not r.probe and not r.offered:
                r.wake.set()

    def rekey(self, remote, key):
        """A host given by where it is said who it is: known by its id from now on (one remembered by it, folded in).
        This machine's, by its socket, stays so: the same one reached over ssh too (by hand, say) is let go."""
        with self.lock:
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
                remote.command = remote.command or old.command
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

    def save(self):
        """servers.json: every host reached or given by hand, and what's in it that isn't known here (put there
        meanwhile: take_saved takes it up), not written over."""
        self.saved_at = time.time()
        on_disk = self.saved()
        with self.lock:
            servers = {k: r.cached() for k, r in self.servers.items() if r.info.get("id") or r.sessions or r.by_hand}
            for key, c in on_disk.items():
                if key not in self.servers and key not in self.forgotten and \
                        not any(r.where == c["where"] for r in self.servers.values()):
                    servers[key] = c
            data = {"servers": servers}
        target = os.path.join(self.home, "servers.json")
        try:
            os.makedirs(self.home, exist_ok=True)
            with open(target + ".tmp", "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(target + ".tmp", target)
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
        """Every host known, there or not: those reached once, given by hand, or offered (found on the network, not
        reached: Remote.offered); not a Tailscale peer that's never answered as one."""
        with self.lock:
            return [r for r in self.servers.values() if r.info.get("id") or r.by_hand or r.offered]

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
        if self.browser is not None:
            self.browser.stop()
        with self.lock:
            servers = list(self.servers.values())
        for r in servers:
            r.stop()


def _where(where):
    """Is it somewhere wire.reach can go?"""
    if not isinstance(where, dict):
        return False
    via = where.get("via")
    return via == "local" or via == "unix" and isinstance(where.get("path"), str) or \
        via == "ssh" and wire.is_dest(where.get("dest")) and isinstance(where.get("command") or "", str)


class _Remote:
    """What stands for a RemoteProc's process (Baude's debug report asks after its pid)."""

    def __init__(self, proc, args):
        self.proc, self.args, self.pid = proc, args, None

    def poll(self):
        return None if self.proc.ch is not None and not self.proc.ch.closed else self.proc.code


class RemoteProc(claude.ClaudeProc):
    """A session on a host (where: wire.reach's), talked to as ClaudeProc talks to a local claude: what's sent goes to its claude (started
    on the server if it isn't running), what it says comes to on_msg. live: everything it says, as it says it; else
    only what it asks (a permission) and the answers to what's asked of it (initialize: its models and commands).
    kill() lets go of it: the session goes on, on its server. OSError if it can't be reached."""

    def __init__(self, on_msg, on_exit, where, session_id, live=True):
        self.on_msg, self.on_exit = on_msg, on_exit
        self.req_ids = itertools.count(1)
        self.session, self.live, self.code, self.killed = session_id, live, None, False
        self.ch, self.server = wire.reach(where, timeout=3)
        self.p = _Remote(self, [wire.said_where(where), session_id])
        self.ch.sock.settimeout(5)
        self.ch.send({"op": "attach", "session": session_id, "live": live, "req": 0})
        reply = self.ch.reply()
        if not reply or reply.get("op") != "attached":
            self.ch.close()
            raise OSError((reply or {}).get("error") or "the server didn't answer")
        self.ch.sock.settimeout(wire.SILENT_S)  # (the server pings: silence this long, and it's gone)
        self.ch.keep_alive()
        self.listing = reply.get("session") or {}
        self.asks = {m["request_id"]: m for m in reply.get("asks") or [] if m.get("request_id")}
        threading.Thread(target=self._read_remote, daemon=True, name=f"remote-proc-{session_id}").start()
        self.init_rid = self.control({"subtype": "initialize", "hooks": None})

    def _read_remote(self):
        for msg in self.ch:
            op = msg.get("op")
            if op == "claude" and msg.get("session") == self.session:
                self.dispatch(lambda m=msg["msg"]: self.on_msg(self, m))
            elif op == "exited" and msg.get("session") == self.session:
                self.code = msg.get("code")
                break
            elif op == "error":  # (what was sent went nowhere: said, and the turn's over)
                self.dispatch(lambda e=msg.get("error"): self.on_msg(self, {
                    "type": "result", "subtype": "error", "is_error": True, "result": f"the server: {e}"}))
        self.ch.close()
        if not self.killed:
            code = -1 if self.code is None else self.code
            self.dispatch(lambda: self.on_exit(code))

    def send(self, obj):
        self.ch.send({"op": "input", "session": self.session, "msg": obj})

    def close_stdin(self):
        pass  # (nothing to close here: the server's claude goes on)

    def kill(self):
        self.killed = True
        self.ch.send({"op": "detach", "session": self.session})
        self.ch.close()
