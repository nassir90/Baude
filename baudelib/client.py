"""Servers as Baude sees them: each one found on the network (discovery.Browser), given by address, or remembered from
before; the sessions each lists; a session's transcript mirrored here, and its subagents' beside it, so what reads a
transcript reads it as it reads a local one; and a session's claude, talked to as Baude talks to its own (RemoteProc).

A server that goes (its connection drops, or it says nothing for wire.SILENT_S) isn't forgotten: it's "dead", its
sessions still listed as they were, until it's back or it's been gone for FORGET_S. Calls to what's given (on_change,
a request's callback, RemoteProc's on_msg and on_exit) come through dispatch, as ClaudeProc's do."""
import base64
import itertools
import json
import os
import socket
import threading
import time

from baudelib import claude, wire
from baudelib.discovery import Browser

FORGET_S = 7 * 86400
RETRY_S = (1, 2, 5, 10, 30)  # a dead server is tried again after these, then every last one


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
    """One server: its info (as hello says), its sessions (as it lists them), and whether it's there."""

    def __init__(self, network, key, host, port, cached=None):
        cached = cached or {}
        self.network, self.key, self.host, self.port = network, key, host, port
        self.info = cached.get("info") or {}
        self.sessions = {s["id"]: s for s in cached.get("sessions") or [] if isinstance(s, dict) and s.get("id")}
        self.syncing = {}  # session id -> the lock its mirror's written under (sync); (id, "subagents"): its subagents'
        self.agents_synced = {}  # session id -> the subagents stamp it listed that its mirror's caught up with
        self.seen = cached.get("seen") or 0.0  # when it last said anything
        self.by_hand = bool(cached.get("by_hand"))  # (given by address: kept, whether it's been reached or not)
        self.state = "connecting"  # then "alive", or "dead"
        self.ch = None
        self.calls, self.reqs = {}, itertools.count(1)
        self.wake, self.stopped = threading.Event(), False
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._run, daemon=True, name=f"remote-{self.key}").start()
        return self

    def address(self, host, port):
        """Where it is now (found again, maybe elsewhere): tried at once if it's dead."""
        moved = (host, port) != (self.host, self.port)
        self.host, self.port = host, port
        if moved or self.state != "alive":
            self.wake.set()

    def _run(self):
        tries = 0
        while not self.stopped:
            try:
                ch, info = wire.connect(self.host, self.port, timeout=5)
            except OSError:
                ch = None
            if ch is not None:
                tries = 0
                self._serve(ch, info)
            if self.stopped:
                break
            if self.state != "dead":
                self.state = "dead"
                self.network.changed()
            self.wake.wait(RETRY_S[min(tries, len(RETRY_S) - 1)])
            self.wake.clear()
            tries += 1

    def _serve(self, ch, info):
        with self.lock:
            self.ch = ch
        if info.get("id") and info["id"] != self.key:
            self.network.rekey(self, info["id"])
        self.info, self.seen = info, time.time()
        ch.sock.settimeout(wire.SILENT_S)
        ch.keep_alive()
        ch.send({"op": "subscribe"})
        for msg in ch:
            self.seen = time.time()
            op = msg.get("op")
            if op == "sessions":
                self.info = msg.get("server") or self.info
                self.sessions = {s["id"]: s for s in msg.get("sessions") or [] if s.get("id")}
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
        return os.path.join(self.network.home, self.key, f"{session_id}.jsonl")

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
        transcript (what reads a transcript's subagents finds them there)."""
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
        if not isinstance(listed, int):
            return False  # (a server that doesn't say)
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
        return {"host": self.host, "port": self.port, "info": self.info, "seen": self.seen, "by_hand": self.by_hand,
                "sessions": list(self.sessions.values())}


class Network:
    """Every server Baude knows, and the sessions they list. listen(fn): fn() (through dispatch) when they change."""

    def __init__(self, dispatch, home=None, discover=True, addresses=()):
        self.dispatch, self.home = dispatch, home or network_home()
        self.servers, self.listeners, self.lock = {}, [], threading.RLock()
        self.saved_at, self.pending, self.looked = 0.0, False, float("-inf")
        self.stopped, self.forgotten = threading.Event(), set()  # (forgotten: gone FORGET_S, dropped from the file)
        for key, c in self.saved().items():
            if time.time() - (c.get("seen") or 0) < FORGET_S or c.get("by_hand"):
                self.servers[key] = Remote(self, key, c["host"], c.get("port"), c)
            else:
                self.forgotten.add(key)
        for address in addresses:
            host, _, port = address.strip().rpartition(":")
            if host and port.isdigit() and not any((r.host, r.port) == (host, int(port)) for r in self.servers.values()):
                self.servers[f"{host}:{port}"] = Remote(self, f"{host}:{port}", host, int(port))
        for r in list(self.servers.values()):
            r.start()
        self.browser = Browser(self.found).start() if discover else None
        self.here()
        threading.Thread(target=self._tick, daemon=True, name="network-tick").start()

    def saved(self):
        """servers.json as it is on disk now: {key: what was kept of the server}."""
        try:
            with open(os.path.join(self.home, "servers.json"), encoding="utf-8") as f:
                cache = json.load(f)
        except (OSError, ValueError):
            return {}
        return {k: c for k, c in (cache.get("servers") or {}).items()
                if isinstance(c, dict) and c.get("host") and isinstance(c.get("port"), int)}

    def _tick(self):
        """Every few seconds: this machine's servers looked for (here), and servers.json taken up as it's edited."""
        while not self.stopped.wait(5):
            self.here()
            self.take_saved()

    def here(self):
        """The servers on this machine, from their files (wire.local_servers): one bound to it only, which multicast DNS
        doesn't say, is found too, at 127.0.0.1 (its token's read from its file as it's reached)."""
        for e in wire.local_servers():
            key = e.get("id")
            if not key:
                continue
            host = e["host"] if wire.is_loopback(e["host"]) and e["host"] != "localhost" else "127.0.0.1"
            with self.lock:  # (by its id; else one given by its address and not reached yet, so with no id of its own)
                r = self.servers.get(key) or next((r for r in self.servers.values() if r.key == f"{r.host}:{r.port}"
                                                   and (r.host, r.port) == (host, e["port"])), None)
                if r is None:
                    self.servers[key] = Remote(self, key, host, e["port"]).start()
                    self.changed()
                elif r.state != "alive" and (wire.is_loopback(r.host) or r.port != e["port"]):
                    r.address(host, e["port"])

    def take_saved(self):
        """servers.json's servers that aren't known here (put in it while Baude runs): known, and tried."""
        for key, c in self.saved().items():
            with self.lock:
                if key in self.servers or key in self.forgotten or \
                        any((r.host, r.port) == (c["host"], c["port"]) for r in self.servers.values()):
                    continue
                self.servers[key] = Remote(self, key, c["host"], c["port"], c).start()
            self.changed()

    def listen(self, fn):
        self.listeners.append(fn)

    def add(self, host, port, by_hand=False):
        """A server given by address (not found on the network): known by its id once it's said who it is. by_hand:
        kept in servers.json from now on, reached or not (Baude's Add server…)."""
        with self.lock:
            r = next((r for r in self.servers.values() if (r.host, r.port) == (host, port)), None)
            if r is None:
                r = self.servers[f"{host}:{port}"] = Remote(self, f"{host}:{port}", host, port).start()
            r.by_hand = r.by_hand or by_hand
        if by_hand:
            self.save()
            self.changed()
        return r

    def found(self, service):
        key = service["words"].get("id") or f"{service['host']}:{service['port']}"
        with self.lock:
            r = self.servers.get(key)
            if r is None:
                r = self.servers[key] = Remote(self, key, service["host"], service["port"]).start()
                self.changed()
            else:
                r.address(service["host"], service["port"])

    def look(self):
        """Look again now, as a restart would (Baude's window came forward, its ⋮ menu opened): who's there asked at
        once, not at the next discovery.QUERY_S, and every server that's gone tried again, not at its next retry."""
        if time.monotonic() - self.looked < 2:
            return
        self.looked = time.monotonic()
        if self.browser is not None:
            self.browser.ask_soon()
        with self.lock:
            servers = list(self.servers.values())
        for r in servers:
            if r.state != "alive":
                r.wake.set()

    def rekey(self, remote, key):
        """A server given by address said who it is: known by its id from now on (one remembered by it, folded in)."""
        with self.lock:
            if self.servers.get(remote.key) is remote:
                del self.servers[remote.key]
            old = self.servers.pop(key, None)
            if old is not None and old is not remote:
                old.stop()
                remote.sessions = remote.sessions or old.sessions
                remote.by_hand = remote.by_hand or old.by_hand
            if remote.key != key:
                self.forgotten.add(remote.key)  # (the address it was known by: not a server of its own in servers.json)
                remote.was = getattr(remote, "was", set()) | {remote.key}  # (what Baude may still know it by)
            remote.key = key
            self.servers[key] = remote

    def changed(self):
        """Its listeners hear soon (a burst of changes is one call), and what's known is saved."""
        with self.lock:
            if self.pending:
                return
            self.pending = True

        def tell():
            with self.lock:
                self.pending = False
            if time.time() - self.saved_at > 5:  # (a server says something changed every time a transcript grows)
                self.save()
            for fn in list(self.listeners):
                fn()
        self.dispatch(tell)

    def save(self):
        """servers.json: every server reached or given by hand, and what's in it that isn't known here (put there
        meanwhile: take_saved takes it up), not written over."""
        self.saved_at = time.time()
        on_disk = self.saved()
        with self.lock:
            servers = {k: r.cached() for k, r in self.servers.items() if r.info.get("id") or r.sessions or r.by_hand}
            for key, c in on_disk.items():
                if key not in self.servers and key not in self.forgotten and not any((r.host, r.port) == (c["host"], c["port"])
                                                       for r in self.servers.values()):
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
        """The groups the network has: those its servers' sessions were started in, and each server's own."""
        out = set()
        with self.lock:
            for r in self.servers.values():
                if r.state == "alive" and r.info.get("group"):
                    out.add(r.info["group"])
                out.update(s.get("group") for s in r.sessions.values() if s.get("group"))
        return sorted(out, key=str.lower)

    def sessions(self, group=None):
        """Every server's sessions (of a group, if given), each with "server": the Remote it's on."""
        with self.lock:
            return [{**s, "server": r} for r in self.servers.values() for s in r.sessions.values()
                    if group is None or s.get("group") == group]

    def remotes(self):
        """Every server known, there or not."""
        with self.lock:
            return list(self.servers.values())

    def find(self, session_id):
        with self.lock:
            return next((r for r in self.servers.values() if session_id in r.sessions), None)

    def alive(self, group=None):
        """The servers that are there (those whose group is group, or with sessions in it, first)."""
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


class _Remote:
    """What stands for a RemoteProc's process (Baude's debug report asks after its pid)."""

    def __init__(self, proc, args):
        self.proc, self.args, self.pid = proc, args, None

    def poll(self):
        return None if self.proc.ch is not None and not self.proc.ch.closed else self.proc.code


class RemoteProc(claude.ClaudeProc):
    """A session on a server, talked to as ClaudeProc talks to a local claude: what's sent goes to its claude (started
    on the server if it isn't running), what it says comes to on_msg. live: everything it says, as it says it; else
    only what it asks (a permission) and the answers to what's asked of it (initialize: its models and commands).
    kill() lets go of it: the session goes on, on its server. OSError if it can't be reached."""

    def __init__(self, on_msg, on_exit, host, port, session_id, live=True):
        self.on_msg, self.on_exit = on_msg, on_exit
        self.req_ids = itertools.count(1)
        self.session, self.live, self.code, self.killed = session_id, live, None, False
        self.ch, self.server = wire.connect(host, port, timeout=3)
        self.p = _Remote(self, [f"{host}:{port}", session_id])
        self.ch.sock.settimeout(5)
        self.ch.send({"op": "attach", "session": session_id, "live": live, "req": 0})
        reply = self.ch.read()
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
