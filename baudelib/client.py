"""Servers as Baude sees them: each one found on the network (discovery.Browser), given by address, or remembered from
before; the sessions each lists; a session's transcript mirrored here, so what reads a transcript reads it as it reads
a local one; and a session's claude, talked to as Baude talks to its own (RemoteProc).

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
        self.syncing = {}  # session id -> the lock its mirror's written under (sync)
        self.seen = cached.get("seen") or 0.0  # when it last said anything
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

    def sync(self, session_id, timeout=30):
        """Bring a session's mirror up to what the server has (one sync of a session at a time, whichever window asks;
        timeout: what each request's waited for). True if it is."""
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
                    return True

    def stop(self):
        self.stopped = True
        self.wake.set()
        with self.lock:
            ch = self.ch
        if ch is not None:
            ch.close()

    def cached(self):
        return {"host": self.host, "port": self.port, "info": self.info, "seen": self.seen,
                "sessions": list(self.sessions.values())}


class Network:
    """Every server Baude knows, and the sessions they list. listen(fn): fn() (through dispatch) when they change."""

    def __init__(self, dispatch, home=None, discover=True, addresses=()):
        self.dispatch, self.home = dispatch, home or network_home()
        self.servers, self.listeners, self.lock = {}, [], threading.RLock()
        self.saved_at, self.pending, self.looked = 0.0, False, float("-inf")
        try:
            with open(os.path.join(self.home, "servers.json"), encoding="utf-8") as f:
                cache = json.load(f)
        except (OSError, ValueError):
            cache = {}
        for key, c in (cache.get("servers") or {}).items():
            if isinstance(c, dict) and time.time() - (c.get("seen") or 0) < FORGET_S and c.get("host"):
                self.servers[key] = Remote(self, key, c["host"], c.get("port"), c)
        for address in addresses:
            host, _, port = address.strip().rpartition(":")
            if host and port.isdigit() and not any((r.host, r.port) == (host, int(port)) for r in self.servers.values()):
                self.servers[f"{host}:{port}"] = Remote(self, f"{host}:{port}", host, int(port))
        for r in list(self.servers.values()):
            r.start()
        self.browser = Browser(self.found).start() if discover else None

    def listen(self, fn):
        self.listeners.append(fn)

    def add(self, host, port):
        """A server given by address (not found on the network): known by its id once it's said who it is."""
        with self.lock:
            r = next((r for r in self.servers.values() if (r.host, r.port) == (host, port)), None)
            if r is None:
                r = self.servers[f"{host}:{port}"] = Remote(self, f"{host}:{port}", host, port).start()
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
        self.saved_at = time.time()
        with self.lock:
            data = {"servers": {k: r.cached() for k, r in self.servers.items()
                                if r.info.get("id") or r.sessions}}
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
