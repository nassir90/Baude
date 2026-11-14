"""The mesh: host processes (baude_headless.py) linked to each other, so the hosts the owner has connected are one
network that shares its chats and its models.

A link is made by one host process, when a head asks it to (Add Host… in Baude, `baude_headless.py link DEST`), the
way a head reaches a host (wire.reach: over ssh, mostly). It's kept in links.json in its state folder, and made again
whenever it drops. Once it's made it's used both ways: the host linked to reaches the one that made it over the same
connection, with no way in of its own (no ssh key for it). A host that's linked, whichever end made the link, is a
member, and the mesh is every member reached through links: A linked to B, and C to B, and A, B and C each reach the
other two, C's messages to A passed on by B. A member does on another what a head on that machine does: lists its
chats, starts and steers them, reads their transcripts, and runs its models.

What goes over a link, after hello and join {host} -> joined {host} (wire.py's lines, each with an "op"):
    mesh {records}          members' records, {id, seq, info, links}: who it is (info: id, name, group, host, version,
                            providers) and the members it's linked to. A host sends its own when it changes (seq
                            higher than any it sent before) and passes each one newer than it had on to its other links;
                            all it has as a link's made. Members are reached through links both ends list (a direct
                            link of its own counts at once), so one that's gone, its links dropped, is gone for all.
    open {t, to, ttl}       a tunnel to member to, t this end's number for it: passed on to the next host on the way
                            (ttl hosts at most), and at to a connection to its host process, served as if a head had
                            connected to its socket. opened {r} once it's there; close {t | r, error?} from either end
    data {t | r, msg}       a wire message in a tunnel: t, one this end opened; r, one the other end did
    ping

A member learned of through another (reached, but not linked to here) is linked to straight (Found): with what this
machine has already, ssh to the names that member's record gives for its machine (its Bonjour name, its host, its name,
its name.local), this user's own keys and ssh config, its key already in known_hosts, and the member it's said to be
there or nothing. Then the two stay linked when the member between them goes. It's a join, as any link's, said to be
straight (refused by a member that's unlinked this one). Of two members that learn of each other, the one with the
lower id waits
DIAL_WAIT_S first, so the other's link, if it makes one, is mostly the one there is (a slow ssh can still give two,
which the mesh takes as it takes any two links between a pair). It's tried again after FOUND_RETRY_S (each a fifth
either way, at random), from the start of them whenever this machine's network changes or it wakes from sleep, while
the member's known. One that's been linked to straight, by either end, is kept in links.json (found), the name that went
first, and linked to again after a restart, till it's been out of reach for KEEP_FOUND_S. One unlinked here (kept
too, till it's linked to by hand again) isn't linked to this way, nor one that's refused a link from here this run.

A head reaches a member through its own machine's host process: relay {to} on its socket (wire.reach's {"via": "mesh",
"host": id}) -> relayed {to}, and from then on that connection is the tunnel, hello first as ever.

Models: each member's providers (local_providers: Ollama, an OpenAI-compatible server, Apple's on-device model, pi's
MLX models) are in its record. One that serves HTTP (it has a base_url) is run from any member through that member's
own host process's Proxy, on 127.0.0.1 with a key, which carries each request to the member that has the provider
(infer {provider, method, path, headers, body} -> infer_head {status, headers}, infer_data {data}..., infer_end) and
the answer back as it comes. A program's (apple-claude, pi-claude) runs where it is: a chat started on that member.
"""
import base64
import hashlib
import http.server
import itertools
import json
import math
import os
import queue
import random
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from baudelib import models, wire

RETRY_S = (2, 10, 30, 120, 300)  # a link that's dropped, or couldn't be made, is tried again after these
OFF_S = 30  # (one the other end's deactivated this host on: tried again this often)
REFUSED = ("unlinked on", "deactivated on")  # (a member that's unlinked or deactivated this one: its join's error)
DIAL_WAIT_S = 5  # (a member learned of through another, by the lower id of the two: Found waits this long first)
FOUND_RETRY_S = (2, 10, 30, 120, 300, 600)  # (a link straight to one, not made or dropped: tried again after these)
KEEP_FOUND_S = 7 * 86400  # (one linked to straight: remembered, and tried, till it's been out of reach this long)
NET_S = 5  # (this machine's network, and whether it's slept, looked at this often)
OPEN_S = 20  # a tunnel's far end has this long to say it's there
TTL = 16  # hosts a tunnel's passed through at most
PROVIDERS_S = 60  # this machine's providers looked at again this often, while it's linked
FORGET_S = 3600  # a record of a member no longer reached is let go after this long
INFER_S = 600  # a provider has this long to answer
CHUNK = 64 << 10
LINKS = "links.json"
ENDPOINTS = "mesh-endpoints.json"  # (in the runtime folder, the user's own: endpoints(), for what runs here)
_PROVIDER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}(/[A-Za-z0-9][A-Za-z0-9_.:-]{0,63})?")


def is_provider_id(value):
    """Is it a provider's id: a word, or two with a slash between (openai/<endpoint>)?"""
    return isinstance(value, str) and bool(_PROVIDER.fullmatch(value)) and ".." not in value


def endpoint_id(member, provider):
    """The id another member's provider has here as an OpenAI-compatible endpoint (endpoints()): the same every run,
    mesh-<the member's id's first 8>-<the provider's>, 31 characters at most, of a-z, 0-9, _ and -."""
    slug = re.sub(r"[^a-z0-9_-]", "-", provider.lower())
    if len(slug) > 17:
        slug = slug[:10] + "-" + hashlib.sha1(provider.encode()).hexdigest()[:6]
    return f"mesh-{re.sub(r'[^a-z0-9]', '', member.lower())[:8]}-{slug}"


def _kept(path=None):
    """What this machine's host process keeps of other members' providers (changed), if it's running: {} if not."""
    try:
        with open(path or os.path.join(wire.runtime_dir(), ENDPOINTS), encoding="utf-8") as f:
            kept = json.load(f)
        os.kill(int(kept["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return kept if isinstance(kept, dict) else {}


def agents(path=None):
    """Other members' providers that serve no API (Apple's, MLX's), whose models run there as agents (agentproxy):
    [{provider, provider_name, host (the member's id), host_name, models: [{value, displayName, window, tools, input,
    thinking}]}], from the file this machine's host process keeps (endpoints'). [] if it isn't running."""
    return [a for a in _kept(path).get("agents") or [] if isinstance(a, dict) and wire.is_id(a.get("host"))]


def endpoints(path=None):
    """Other members' providers that serve HTTP, as OpenAI-compatible endpoints on this machine (its host process's
    Proxy), for anything running here (no GTK, no connection): [{id (endpoint_id), name, provider, provider_name, host
    (the member's id), host_name, url, key, models: [{id, displayName, window, tools, input}]}], from the file this
    machine's host process keeps in its runtime folder.
    [] if it isn't running."""
    try:
        with open(path or os.path.join(wire.runtime_dir(), ENDPOINTS), encoding="utf-8") as f:
            kept = json.load(f)
        os.kill(int(kept["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [e for e in kept.get("endpoints") or [] if isinstance(e, dict)]


def local_providers():
    """This machine's model providers, each {id, kind, name, base_url (an OpenAI-compatible server's base, /v1 and
    all; None for a program's), models: [{value, displayName, id (its name in the server's API), tools, window,
    input, thinking}]}: providers.local_providers' once that's here, else the programs models.runnable_models has."""
    try:
        from baudelib import providers
        found = providers.local_providers()
    except Exception:  # (none here yet, or it failed: what this machine runs as programs)
        found = None
    if not isinstance(found, list):
        found = []
        if models.apple_exe():
            found.append({"id": "apple", "kind": "apple", "name": "Apple on-device", "base_url": None,
                          "models": [models.APPLE_MODEL]})
        if pi := models.local_models():
            found.append({"id": "mlx", "kind": "mlx", "name": "MLX", "base_url": None, "models": pi})
    return [p for p in found if isinstance(p, dict) and is_provider_id(p.get("id"))]


def provider_key(p):
    """(on the host that has it) What a provider's server is asked with: its endpoint's key, if it has one."""
    try:
        from baudelib import providers
        return providers.endpoint_key(p["id"]) or None
    except Exception:  # (none here, or no key for it)
        return None


def _model(m):
    """A model as the mesh is told of it: value, displayName, and what's said of it that's plain (id, tools,
    window, input, thinking)."""
    out = {"value": m["value"][:200], "displayName": str(m.get("displayName") or m["value"])[:200]}
    if isinstance(m.get("id"), str):
        out["id"] = m["id"][:200]
    for k in ("tools", "thinking"):
        if isinstance(m.get(k), bool):
            out[k] = m[k]
    if isinstance(m.get("window"), int) and not isinstance(m.get("window"), bool):
        out["window"] = m["window"]
    if isinstance(m.get("version"), str):
        out["version"] = m["version"][:32]
    if isinstance(m.get("digest"), str):
        out["digest"] = m["digest"][:80]
    if isinstance(m.get("input"), list):
        out["input"] = [x[:32] for x in m["input"][:8] if isinstance(x, str)]
    return out


def published(p, http=None):
    """A provider as the mesh is told of it: what it is and its models (http: it's run through Proxy; as it's said, for
    one another member's told of), no address or key of this machine's."""
    return {"id": p["id"], "kind": str(p.get("kind") or "openai")[:32], "name": str(p.get("name") or p["id"])[:64],
            "http": bool(p.get("base_url")) if http is None else http,
            "models": [_model(m) for m in (p.get("models") or [])[:200]
                       if isinstance(m, dict) and isinstance(m.get("value"), str)]}


def _hello_of(host):
    """A host's hello on a link, if it's one: {id, name, group, host, version, bonjour (its name on the local
    network, if it said one)}, cut to size."""
    if not isinstance(host, dict) or not wire.is_id(host.get("id")):
        return None
    out = {"id": host["id"], "name": str(host.get("name") or "")[:64] or host["id"][:8],
           "group": str(host.get("group") or "")[:64], "host": str(host.get("host") or "")[:255],
           "version": host.get("version") if isinstance(host.get("version"), int) else 0}
    if isinstance(host.get("bonjour"), str):
        out["bonjour"] = host["bonjour"][:255]
    return out


def _record(r):
    """A member's record as it came, if it's one, cut to size: {id, seq, info, links}."""
    if not isinstance(r, dict) or not wire.is_id(r.get("id")) or not isinstance(r.get("seq"), int):
        return None
    info = _hello_of(r.get("info"))
    if info is None or info["id"] != r["id"]:
        return None
    raw = r["info"].get("providers")
    info["providers"] = [published(p, p.get("http") is True) for p in raw[:32]
                         if isinstance(p, dict) and is_provider_id(p.get("id"))] \
        if isinstance(raw, list) else []
    links = r.get("links") if isinstance(r.get("links"), list) else []
    return {"id": r["id"], "seq": r["seq"], "info": info, "links": sorted({x for x in links[:256] if wire.is_id(x)})}


def bonjour_name():
    """This machine's name on the local network (macOS's LocalHostName, .local), if it has one: what's reached by
    ssh at home or in an office, where its hostname (as gethostname has it, Mac, say) may be no one's."""
    if sys.platform != "darwin":
        return None
    try:
        name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True,
                              timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{name}.local" if name and wire.is_dest(name) and "." not in name and "@" not in name else None


def local_address():
    """This machine's address on its network now (what its default route goes out from), or None with no network:
    only the route's looked up, nothing's sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


class _Out:
    """What's sent on a channel, from a queue, on a thread of its own: a slow reader holds up nobody else."""

    def __init__(self, ch, name):
        self.ch, self.q = ch, queue.Queue()
        threading.Thread(target=self._run, daemon=True, name=name).start()

    def put(self, msg):
        self.q.put(("msg", msg))

    def close(self, then=None):
        """No more: then() once what's queued is sent."""
        self.q.put(("end", then))

    def _run(self):
        while True:
            kind, item = self.q.get()
            if kind == "end":
                if item is not None:
                    item()
                return
            if not self.ch.send(item):  # (gone: the rest dropped, till the end's put)
                while (got := self.q.get())[0] != "end":
                    pass
                if got[1] is not None:
                    got[1]()
                return


class Link:
    """A link to another member's host process, made by either end: its frames read on the thread that runs it (run),
    sent from a queue, and the tunnels through it, ("out", t) those this end opened and ("in", t) the other end's."""

    def __init__(self, mesh, ch, peer, made):
        self.mesh, self.ch, self.peer, self.made = mesh, ch, peer, made
        self.id = peer["id"]
        self.out = _Out(ch, f"link-{self.id[:8]}")
        self.tunnels = {}
        self.ts = itertools.count(1)
        self.since = time.time()
        self.straight = False  # (made straight, by a Found at either end)

    def send(self, frame):
        self.out.put(frame)

    def tsend(self, key, frame):
        self.send({**frame, ("t" if key[0] == "out" else "r"): key[1]})

    def new_key(self):
        return "out", next(self.ts)

    def run(self):
        """Its frames, till it drops (or it's closed): then the mesh lets go of it."""
        self.ch.sock.settimeout(wire.SILENT_S)
        self.ch.keep_alive()
        try:
            for frame in self.ch:
                try:
                    self.mesh.frame(self, frame)
                except Exception as e:  # (one bad frame doesn't end the link)
                    self.mesh.log(f"mesh: from {self.peer['name']}: {type(e).__name__}: {e}")
        finally:
            self.out.close(self.ch.close)
            self.ch.close()
            self.mesh.dropped(self)

    def close(self):
        self.ch.close()


class Here:
    """A tunnel's end at a channel on this machine: a head's (relay), or the one this host process serves the tunnel's
    other end on (its far end opened it to here)."""

    def __init__(self, mesh, ch):
        self.mesh, self.ch = mesh, ch
        self.out = _Out(ch, "tunnel-out")
        self.link = self.key = None
        self.origin = self.to = None  # (the member that opened it, to here; the member it's to, from here)
        self.up, self.error, self.pumping = threading.Event(), None, False

    def ready(self):
        self.up.set()

    def deliver(self, msg):
        if isinstance(msg, dict):
            self.out.put(msg)

    def closed(self, error=None):
        self.error = error or "the tunnel was closed"
        self.up.set()
        if self.pumping:  # (else whoever's opening it says why, first)
            self.out.close(self.ch.close)

    def pump(self):
        """What comes in on the channel, through the tunnel, till either end's done."""
        self.pumping = True
        if self.error is not None:  # (closed as it opened)
            self.out.close(self.ch.close)
            return
        try:
            for msg in self.ch:
                self.link.tsend(self.key, {"op": "data", "msg": msg})
        finally:
            if self.mesh.forget(self.link, self.key) is not None:
                self.link.tsend(self.key, {"op": "close"})
            self.out.close(self.ch.close)


class Passed:
    """A tunnel's end that's passed on, to another link's tunnel: toward the member it goes to, or back toward the
    one that opened it."""

    def __init__(self, mesh, link, key):
        self.mesh, self.link, self.key = mesh, link, key

    def ready(self):
        self.link.tsend(self.key, {"op": "opened"})

    def deliver(self, msg):
        self.link.tsend(self.key, {"op": "data", "msg": msg})

    def closed(self, error=None):
        if self.mesh.forget(self.link, self.key) is not None:
            self.link.tsend(self.key, {"op": "close", **({"error": error} if error else {})})


class Out:
    """A link this host process makes (links.json's): to where (wire.reach's), made again whenever it drops, while
    it's active (deactivated: not made, and let go of if it is, till it's activated again)."""

    def __init__(self, mesh, where, id=None, name=None, active=True):
        self.mesh, self.where = mesh, dict(where)
        self.id, self.name = id, name  # (the member it's to, once it's said)
        self.state, self.error, self.link = "connecting", None, None
        self.stopped, self.wake, self.active = False, threading.Event(), active
        self.expect, self.made = None, 0  # (the member it must be, if it's said: Found's; how many times it's linked)
        self.again = False  # (tried again now, from the start of its waits: retry)

    def start(self):
        threading.Thread(target=self._run, daemon=True, name=f"link-to-{wire.said_where(self.where)}").start()
        return self

    def stop(self):
        self.stopped = True
        self.wake.set()
        if self.link is not None:
            self.link.close()

    def pause(self):
        """Deactivated: let go of, and not made again till it's resumed."""
        self.active = False
        self.wake.set()
        if self.link is not None:
            self.link.close()

    def retry(self):
        """Tried again now, from the start of its waits (this machine's network has changed)."""
        self.again = True
        self.wake.set()

    def resume(self):
        if not self.active:
            self.active = True
            self.wake.set()

    def _run(self):
        tries = 0
        while not self.stopped:
            if not self.active:
                self.state, self.error, tries = "inactive", None, 0
                self.mesh.changed()
                self.wake.wait()
                self.wake.clear()
                continue
            try:
                ch, info = wire.reach(self.where, timeout=10)
            except OSError as e:
                ch, self.error, self.state = None, str(e), "dropped"
            if ch is not None:
                if self.stopped:
                    ch.close()
                    break
                since = time.monotonic()
                self.take(ch, info)
                if time.monotonic() - since > 60:  # (it was linked a while: tried again soon, as at first)
                    tries = 0
            if self.stopped:
                break
            self.mesh.changed()
            off = (self.error or "").startswith("deactivated on")  # (it's left this one out: tried every OFF_S, so
            self.wake.wait(OFF_S if off else RETRY_S[min(tries, len(RETRY_S) - 1)])  # it's back soon once it's
            self.wake.clear()                                                         # activated there)
            tries, self.again = 0 if self.again else tries + 1, False

    def take(self, ch, info):
        """A connection to where: made a link, and run till it drops."""
        peer = _hello_of(info)
        if peer is None or peer["id"] == self.mesh.id or peer["version"] < 3:
            self.error = "it gave no id Baude can use" if peer is None else "that's this host" \
                if peer["id"] == self.mesh.id else "its Baude is older, with no mesh: update it"
            self.state = "dropped"
            ch.close()
            return
        if self.expect is not None and peer["id"] != self.expect:  # (another host there than the member it's to)
            self.error, self.state = f"another host, {peer['name']}, is there", "dropped"
            ch.close()
            return
        if peer["id"] in self.mesh.inactive:  # (deactivated here by its id: not linked to, wherever it's reached)
            self.id, self.name, self.active = peer["id"], peer["name"], False
            ch.close()
            self.mesh.save()
            return
        ch.sock.settimeout(OPEN_S)
        ch.send({"op": "join", "req": 0, "host": self.mesh.hello(), **({"straight": True} if self.expect else {})})
        reply = ch.reply()
        theirs = _hello_of((reply or {}).get("host"))
        if not reply or reply.get("op") != "joined" or theirs is None or theirs["id"] != peer["id"]:
            self.error, self.state = (reply or {}).get("error") or "it didn't answer", "dropped"
            ch.close()
            return
        self.id, self.name, self.error, self.state = peer["id"], peer["name"], None, "linked"
        self.link, self.made = Link(self.mesh, ch, theirs, made=True), self.made + 1
        if self.stopped or not self.active:  # (unlinked or deactivated as it was made: stop or pause saw no link to
            # close, or closed this one)
            self.link, self.state = None, "dropped"
            ch.close()
            return
        self.mesh.save()
        self.mesh.add(self.link)
        self.link.run()
        self.link, self.state = None, "dropped"
        self.error = f"the link to {peer['name']} dropped"

    def kept(self):
        return {"where": self.where, "id": self.id, "name": self.name, **({} if self.active else {"active": False})}


class Found(Out):
    """A link straight to a member this host learned of through another (Mesh.reroute), or linked to straight before
    (Mesh.remembered), made from what this machine has already: ssh to each name it's known by here (Mesh.wheres),
    with this user's own keys and ssh config and the machine's key already in known_hosts (strict), to that member and
    no other (expect). Tried after delay, then after FOUND_RETRY_S (from the start of them once it's been linked a
    while, or the network's changed: again), while it's wanted (Mesh.wants: known, not deactivated or unlinked, not
    linked otherwise)."""

    def __init__(self, mesh, member, wheres, delay=0):
        super().__init__(mesh, wheres[0], id=member, name=mesh.name_of(member))
        self.expect, self.wheres, self.delay = member, wheres, delay

    def pause(self):  # (deactivated: let go of; activate starts another)
        self.stop()

    def _run(self):
        tries, said = 0, None
        if self.delay:
            self.state = "waiting"
            self.wake.wait(self.delay)
            self.wake.clear()
        while not self.stopped and self.mesh.wants(self.expect, self):
            self.state, self.again = "connecting", False
            with self.mesh.lock:
                self.wheres = self.mesh.wheres(self.expect) or self.wheres
            for where in self.wheres:
                try:
                    ch, info = wire.reach(where, timeout=10)
                except OSError as e:
                    self.error, self.state = str(e), "dropped"
                    continue
                if self.stopped or not self.mesh.wants(self.expect, self):
                    self.error = None  # (no longer wanted: nothing went wrong)
                    ch.close()
                    break
                self.where, since, made = where, time.monotonic(), self.made
                self.take(ch, info)
                if self.made > made:  # (it was linked, and dropped)
                    said = None
                    tries = 0 if time.monotonic() - since > 60 else tries
                    break
                if (self.error or "").startswith(REFUSED):  # (it's there, and won't: no other name tried)
                    break
            self.mesh.changed()
            if self.stopped:
                break
            if (self.error or "").startswith(REFUSED):  # (refused there: not tried again this run)
                self.mesh.refused.add(self.expect)
                self.mesh.log(f"mesh: no link straight to {self.name}: {self.error}")
                break
            if self.error and self.error != said and not self.made:  # (said once, not every try)
                said = self.error
                self.mesh.log(f"mesh: no link straight to {self.name} yet: {self.error}")
            self.wake.wait(FOUND_RETRY_S[min(tries, len(FOUND_RETRY_S) - 1)] * random.uniform(0.8, 1.2))
            self.wake.clear()
            tries = 0 if self.again else tries + 1
        self.state = "stopped"
        self.mesh.unfound(self)


def same_place(a, b):
    """Do two wheres go to the same place (a command given or not)?"""
    return {k: v for k, v in (a or {}).items() if k != "command"} == {k: v for k, v in (b or {}).items()
                                                                       if k != "command"}


class Mesh:
    """The mesh as this host process sees it (server: the Server it's in): the links it's made (outs) and those made to
    it, every member's record, the way to each member (routes: the member each one's reached through, linked to
    here), and Proxy for other members' models."""

    def __init__(self, server):
        self.server = server
        self.id = server.me["id"]
        self.log = server.log
        self.lock = threading.RLock()
        self.links = {}  # member id -> [Link] (both ends may have made one)
        self.records = {}  # member id -> {"rec": its record, "got": when it came}
        self.routes, self.hops = {}, {}  # member id -> the linked member it's reached through; how many links away
        self.seq = time.time_ns()  # (higher than any this host sent before it started, clocks allowing)
        self.local = local_providers()
        self.proxy = None
        self.outs = []
        self.found, self.refused = {}, set()  # (member id -> Found; those that refused a link straight, this run)
        self.bonjour = bonjour_name()
        self.stopped, self.linked = threading.Event(), threading.Event()  # (linked: set while there's a link)
        self.told = None  # (the endpoints what runs here was last told of: endpoints())
        self.records[self.id] = {"rec": self.mine(), "got": time.time()}
        try:
            with open(os.path.join(server.state_dir, LINKS), encoding="utf-8") as f:
                saved = json.load(f)
            kept, off, found, unlinked = (v if isinstance(v := saved.get(k), list) else []
                                          for k in ("links", "inactive", "found", "unlinked"))
        except (OSError, ValueError, AttributeError):
            kept, off, found, unlinked = [], [], [], []
        self.inactive = {x for x in off if wire.is_id(x)}  # (members deactivated here: deactivate)
        self.unlinked = {x for x in unlinked if wire.is_id(x)}  # (members unlinked here: not linked to straight)
        self.remembered = {}  # (member id -> {name, names, seen}: those linked to straight, Found's)
        for k in found:
            if isinstance(k, dict) and wire.is_id(k.get("id")) and isinstance(k.get("seen"), (int, float)) and \
                    math.isfinite(k["seen"]) and isinstance(k.get("names"), list):
                self.remembered[k["id"]] = {"name": str(k.get("name") or k["id"][:8])[:64], "seen": k["seen"],
                                            "names": [n for n in k["names"][:4] if wire.is_dest(n) and "@" not in n]}
        for k in kept:
            if isinstance(k, dict) and wire.is_where(k.get("where")) and k["where"].get("via") != "mesh":
                self.outs.append(Out(self, k["where"], k.get("id") if wire.is_id(k.get("id")) else None,
                                     k.get("name") if isinstance(k.get("name"), str) else None,
                                     k.get("active") is not False))

    def start(self):
        for o in self.outs:
            o.start()
        with self.lock:
            self.reroute()  # (those linked to straight before: linked to again)
        threading.Thread(target=self._providers, daemon=True, name="mesh-providers").start()
        threading.Thread(target=self._network, daemon=True, name="mesh-network").start()

    def _network(self):
        """This machine's network changed (its address), or it's woken from sleep (the wall clock jumped past the
        monotonic one): every link that's down tried again now (network_changed). And every minute, members
        remembered too long out of reach let go of (reroute), when nothing else has."""
        address, gap = local_address(), time.time() - time.monotonic()
        for n in itertools.count(1):
            if self.stopped.wait(NET_S):
                return
            if n % 12 == 0:
                with self.lock:
                    self.reroute()
            now, was = local_address(), address
            woke = abs((time.time() - time.monotonic()) - gap) > 30
            address, gap = now, time.time() - time.monotonic()
            if now != was or woke:
                self.network_changed("woke" if woke else "the network changed")

    def network_changed(self, why):
        """Links that are down tried again now: those made from here, and those straight to members found."""
        with self.lock:  # (one waiting on the other end's link first: left to)
            down = [o for o in self.outs if o.state == "dropped"] + \
                [f for f in self.found.values() if f.state not in ("linked", "waiting")]
        if down:
            self.log(f"mesh: {why}: {len(down)} link{'s' if len(down) > 1 else ''} tried again")
        for o in down:
            o.retry()

    def stop(self):
        self.stopped.set()
        self.linked.set()
        with self.lock:
            outs, links = list(self.outs) + list(self.found.values()), [x for ls in self.links.values() for x in ls]
        for o in outs:
            o.stop()
        for x in links:
            x.close()
        if self.proxy:
            self.proxy.stop()
        try:
            os.unlink(self.endpoints_path())
        except OSError:
            pass

    # ---- what this host is
    def hello(self):
        me = self.server.me
        return {"id": self.id, "name": me["name"], "group": me["group"], "host": socket.gethostname(),
                "version": wire.VERSION, **({"bonjour": self.bonjour} if self.bonjour else {})}

    def mine(self):
        """(lock held) This host's record."""
        return {"id": self.id, "seq": self.seq,
                "info": {**self.hello(), "providers": [published(p) for p in self.local]},
                "links": sorted(self.links)}

    def publish(self):
        """(lock held) This host's record, anew (its links, or what it is, changed), to every link."""
        self.seq = max(self.seq + 1, time.time_ns())
        rec = self.mine()
        self.records[self.id] = {"rec": rec, "got": time.time()}
        for ls in self.links.values():
            for x in ls:
                x.send({"op": "mesh", "records": [rec]})
        self.reroute()

    def _providers(self):
        """This machine's providers looked at again every PROVIDERS_S while it's linked (refresh), and not at all while
        it isn't."""
        while not self.stopped.is_set():
            if not self.links:
                self.linked.wait()
                continue
            if not self.stopped.wait(PROVIDERS_S):
                self.refresh()

    def refresh(self):
        """This machine's providers looked at now: the mesh told if they've changed, and heads here."""
        found = local_providers()
        with self.lock:
            if [published(p) for p in found] != [published(p) for p in self.local]:
                self.local = found
                self.publish()
            else:
                self.local = found
        self.changed()

    # ---- links
    def link(self, where):
        """A link made from here to where (a head asked), kept in links.json: the one there already, if there's one
        (a command given anew taken up)."""
        if not wire.is_where(where) or where.get("via") in ("local", "mesh"):
            raise ValueError(f"nowhere to link to: {where!r}")
        with self.lock:
            out = next((o for o in self.outs if same_place(o.where, where)), None)
            if out is not None:
                if out.where != dict(where):
                    out.where = dict(where)
                    out.wake.set()
                elif out.state != "linked":
                    out.wake.set()
            else:
                out = Out(self, where)
                self.outs.append(out)
                out.start()
        self.save()
        self.changed()
        return out

    def unlink(self, where=None, id=None):
        """The links made from here to where, or to member id, let go of and forgotten, those straight to it too (one
        found: Found, or remembered, by its id or a name it was reached by); and it's not linked to straight again,
        from here or from there, till it's linked to by hand: True if there were any."""
        id = id if wire.is_id(id) else None
        dest = where.get("dest") if isinstance(where, dict) and where.get("via") == "ssh" else None
        with self.lock:
            gone = [o for o in self.outs if where is not None and same_place(o.where, where) or id and o.id == id]
            self.outs = [o for o in self.outs if o not in gone]
            ids = {o.id for o in gone if o.id} | ({id} if id else set())
            ids |= {f.expect for f in self.found.values()
                    if where is not None and any(same_place(w, where) for w in [f.where, *f.wheres])}
            ids |= {k for k, r in self.remembered.items() if dest and dest in r["names"]}
            self.unlinked |= ids  # (never linked to straight, till it's linked to by hand again)
            for i in ids:
                self.remembered.pop(i, None)
                if (f := self.found.pop(i, None)) is not None:
                    gone.append(f)
            links = [x for i in ids for x in self.links.get(i) or [] if getattr(x, "straight", False)]
        for o in gone:
            o.stop()
        for x in links:  # (a link straight from there, too)
            x.close()
        if gone or ids:
            self.save()
            self.changed()
        return bool(gone or ids)

    def deactivate(self, id=None, where=None):
        """A member deactivated here (by its id, or by where a link from here goes): its links let go of, those made
        from here not made again, and its joins and tunnels refused, till it's activated. Kept in links.json. True if
        there was one to deactivate."""
        id = id if wire.is_id(id) else None
        with self.lock:
            outs = [o for o in self.outs if where is not None and same_place(o.where, where) or id and o.id == id]
            found = [f for f in self.found.values() if f.id == id or f.id in {o.id for o in outs}]
            for f in found:  # (let go of now: activate's free to start another)
                del self.found[f.expect]
            # (an id given is taken as one, unless it's only what a link's said to be where it goes: an alias)
            ids = {o.id for o in outs if o.id} | ({id} if id and (self.known(id) or not outs) else set())
            self.inactive |= ids
            links = [x for i in ids for x in self.links.get(i) or []]
            tunnels = [(x, key, end) for ls in self.links.values() for x in ls for key, end in x.tunnels.items()
                       if isinstance(end, Here) and (end.origin in ids or end.to in ids)]
        for o in outs + found:
            o.pause()
        for x in links:
            x.close()
        for x, key, end in tunnels:  # (those it opened to here through others, and those from here to it)
            if self.forget(x, key) is not None:
                x.tsend(key, {"op": "close", "error": f"deactivated on {self.server.me['name']}"})
                end.closed(f"deactivated on {self.server.me['name']}")
        if outs or ids:
            self.save()
            with self.lock:
                self.reroute()
            self.changed()
        return bool(outs or ids)

    def activate(self, id=None, where=None):
        """A member deactivated here activated again: its links made again, its joins taken. True if there was one."""
        id = id if wire.is_id(id) else None
        with self.lock:
            outs = [o for o in self.outs if where is not None and same_place(o.where, where) or id and o.id == id]
            ids = ({id} | {o.id for o in outs}) & self.inactive
            self.inactive -= ids
            self.refused -= ids
            self.reroute()  # (reached again through others at once, its record kept meanwhile)
        for o in outs:
            o.resume()
        if outs or ids:
            self.save()
            self.changed()
        return bool(outs or ids)

    def known(self, member):
        """(lock held) Is it a member's id this host has heard of?"""
        return member in self.records or member in self.links or any(o.id == member for o in self.outs)

    def save(self):
        """links.json: the links made from here (one save at a time)."""
        path = os.path.join(self.server.state_dir, LINKS)
        with self.lock:
            try:
                with open(path + ".tmp", "w", encoding="utf-8") as f:
                    json.dump({"links": [o.kept() for o in self.outs], "inactive": sorted(self.inactive),
                               "found": [{"id": k, **r} for k, r in sorted(self.remembered.items())],
                               "unlinked": sorted(self.unlinked)}, f)
                os.replace(path + ".tmp", path)
            except OSError as e:
                self.log(f"mesh: {path}: {e}")

    def take(self, ch, msg):
        """A link made to here (join {host}, on a connection to this host process's socket): run on this thread, till
        it drops."""
        peer = _hello_of(msg.get("host"))
        straight = msg.get("straight") is True  # (a link straight from a member found through others: Found's)
        if peer is None or peer["id"] == self.id or peer["id"] in self.inactive or \
                straight and peer["id"] in self.unlinked:
            ch.send({"op": "error", "req": msg.get("req"), "error": "no host given" if peer is None else
                     "that's this host" if peer["id"] == self.id else f"deactivated on {self.server.me['name']}"
                     if peer["id"] in self.inactive else f"unlinked on {self.server.me['name']}"})
            return
        if not ch.send({"op": "joined", "req": msg.get("req"), "host": self.hello()}):
            return
        self.log(f"mesh: {peer['name']} linked to here")
        link = Link(self, ch, peer, made=False)
        link.straight = straight
        self.add(link)
        link.run()

    def add(self, link):
        """A link up: it's told every record there is, and the mesh this host's new record."""
        with self.lock:
            if link.id in self.inactive:  # (deactivated as it joined)
                link.close()
                return
            self.links.setdefault(link.id, []).append(link)
            self.linked.set()
            remember = False
            if (f := self.found.get(link.id)) is not None and f.link is link:  # (straight: remembered, that way first)
                link.straight = True
                was = (self.remembered.get(link.id) or {}).get("names") or []
                self.remembered[link.id] = {"name": link.peer["name"], "seen": time.time(),
                                            "names": [f.where["dest"]] + [n for n in was if n != f.where["dest"]][:3]}
                remember = True
            elif getattr(link, "straight", False):  # (straight from there: remembered, as its record names it)
                self.remembered[link.id] = {"name": link.peer["name"], "seen": time.time(),
                                            "names": [w["dest"] for w in self.wheres(link.id)][:4]}
                remember = True
            if any(o.link is link for o in self.outs) and link.id in self.unlinked:  # (linked to by hand again)
                self.unlinked.discard(link.id)
                remember = True
            link.send({"op": "mesh", "records": [r["rec"] for k, r in self.records.items() if k != self.id]})
            self.publish()
        if remember:
            self.save()
        self.changed()

    def dropped(self, link):
        """A link down: its tunnels closed, and the mesh told."""
        with self.lock:
            ls = self.links.get(link.id) or []
            if link in ls:
                ls.remove(link)
            if not ls:
                self.links.pop(link.id, None)
            if not self.links:
                self.linked.clear()
            tunnels, link.tunnels = link.tunnels, {}
            if link.id in self.remembered:  # (in reach till now)
                self.remembered[link.id]["seen"] = time.time()
            self.publish()
        if link.id in self.remembered:
            self.save()
        for end in tunnels.values():
            end.closed(f"the link to {link.peer['name']} dropped")
        if not self.stopped.is_set():
            self.log(f"mesh: the link {'to' if link.made else 'from'} {link.peer['name']} dropped")
        self.changed()

    def learn(self, link, records):
        """Records from a link: those newer than what's here kept, and passed on to every other link."""
        new = []
        with self.lock:
            for r in records if isinstance(records, list) else []:
                if (r := _record(r)) is None:
                    continue
                if r["id"] == self.id:  # (one of this host's: only one from before a restart that's newer than
                    if r["seq"] > self.seq:  # this run's is published over; its own, come back, is nothing new)
                        self.seq = r["seq"]
                        new.append(None)
                    continue
                have = self.records.get(r["id"])
                if have is None or r["seq"] > have["rec"]["seq"]:
                    self.records[r["id"]] = {"rec": r, "got": time.time()}
                    new.append(r)
            if None in new:
                self.publish()
            if new := [r for r in new if r is not None]:
                for peer, ls in self.links.items():
                    for x in ls if peer != link.id else ():  # (not back to the member they came from, by any link)
                        x.send({"op": "mesh", "records": new})
                self.reroute()
        if new:
            self.changed()

    def reroute(self):
        """(lock held) Who's reached, and through which link: breadth first through links both ends list, this host's
        own counted at once. Records of members not reached for FORGET_S since let go of."""
        def linked(u):
            mine = (self.records.get(u) or {}).get("rec", {}).get("links") or []
            return [v for v in mine if u in ((self.records.get(v) or {}).get("rec", {}).get("links") or [])]
        hops, via, frontier = {self.id: 0}, {}, []
        for v in sorted(self.links):
            if v not in self.inactive:
                hops[v], via[v] = 1, v
                frontier.append(v)
        while frontier:
            ahead = []
            for u in frontier:
                for v in linked(u):
                    if v not in hops and v not in self.inactive:  # (one deactivated here: neither reached nor through)
                        hops[v], via[v] = hops[u] + 1, via[u]
                        ahead.append(v)
            frontier = ahead
        self.routes, self.hops = via, {k: n for k, n in hops.items() if k != self.id}
        now = time.time()
        for k, r in list(self.records.items()):
            if k in hops or k in self.inactive:  # (one deactivated here: kept, for when it's activated)
                r.pop("lost", None)
            elif now - r.setdefault("lost", now) > FORGET_S:
                del self.records[k]
        old = False
        for k, r in list(self.remembered.items()):
            if k in hops and now - r["seen"] > 3600:  # (in reach: said so, an hour at a time)
                r["seen"], old = now, True
            elif k not in hops and now - r["seen"] > KEEP_FOUND_S:
                del self.remembered[k]
                old = True
        if old:
            self.save()
        if self.stopped.is_set():
            return
        # (reached through another, or linked to straight before, and no link straight to it being made: one made)
        for k in sorted({k for k, n in self.hops.items() if n > 1} | (set(self.remembered) - set(self.hops))):
            if self.wants(k) and (wheres := self.wheres(k)):
                self.found[k] = Found(self, k, wheres, 0 if self.id > k else DIAL_WAIT_S).start()

    def wheres(self, member):
        """(lock held) Where a link straight to member could go, from what's here: ssh to the name that went last time
        (remembered), then those its record gives for its machine (its Bonjour name, its host, its name, its
        name.local), strict (its key in known_hosts already)."""
        info = ((self.records.get(member) or {}).get("rec") or {}).get("info") or {}
        name = info.get("name")
        names = []
        for n in [*(self.remembered.get(member) or {}).get("names", []), info.get("bonjour"), info.get("host"), name,
                  f"{name}.local" if isinstance(name, str) and "." not in name else None]:
            if wire.is_dest(n) and "@" not in n and n not in names:  # (a name, not user@: this user's, or ssh config's)
                names.append(n)
        return [{"via": "ssh", "dest": n, "strict": True} for n in names]

    def wants(self, member, found=None):
        """Is a link straight to member (by found, if it's given) still wanted: it's known (its record here, or it's
        remembered, and not out of reach too long), not deactivated or unlinked here, hasn't refused one, not this host,
        and not linked to otherwise (a link of another's, or one made from here)?"""
        with self.lock:
            mine = found.link if found is not None else None
            kept = self.remembered.get(member)
            return member != self.id and (member in self.records or kept is not None and
                                          time.time() - kept["seen"] <= KEEP_FOUND_S) and \
                member not in self.inactive and member not in self.unlinked and member not in self.refused and \
                not any(x is not mine for x in self.links.get(member) or []) and \
                not any(o.id == member for o in self.outs) and self.found.get(member, found) is found

    def unfound(self, found):
        with self.lock:
            if self.found.get(found.expect) is found:
                del self.found[found.expect]
        self.changed()

    def next_link(self, to):
        with self.lock:
            ls = self.links.get(self.routes.get(to)) or []
            return ls[0] if ls else None

    def name_of(self, member):
        with self.lock:
            if member == self.id:
                return self.server.me["name"]
            rec = (self.records.get(member) or {}).get("rec")
            if rec is not None:
                return rec["info"]["name"]
            if member in self.remembered:
                return self.remembered[member]["name"]
            ls = self.links.get(member) or []
            return ls[0].peer["name"] if ls else str(member)[:8]

    def changed(self):
        """Heads hear: the host process's info (its mesh) has changed. And what runs here (endpoints()) is told of other
        members' providers, if they've changed."""
        self.server.mesh_changed()
        view = self.view()
        proxy = view["proxy"]
        eps = [{"id": endpoint_id(m["id"], p["id"]), "name": f"{p['name']} on {m['name']}", "provider": p["id"],
                "provider_name": p["name"], "host": m["id"], "host_name": m["name"],
                "url": f"{proxy['url']}/{m['id']}/{urllib.parse.quote(p['id'], safe='')}", "key": proxy["key"],
                "models": [{k: x[k] for k in ("id", "displayName", "window", "digest", "tools", "input") if k in x}
                           for x in p["models"]]}
               for m in view["members"] for p in m["providers"] if p.get("http") and proxy]
        agents = [{"provider": p["id"], "provider_name": p["name"], "host": m["id"], "host_name": m["name"],
                   "models": [{k: x[k] for k in ("value", "displayName", "id", "window", "version", "digest", "tools",
                                                 "input", "thinking") if k in x} for x in p["models"]]}
                  for m in view["members"] for p in m["providers"] if not p.get("http")]
        with self.lock:
            if (eps, agents) == self.told:
                return
            self.told = (eps, agents)
            path = self.endpoints_path()
            try:
                fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(), "endpoints": eps, "agents": agents}, f)
                os.replace(path + ".tmp", path)
            except OSError as e:
                self.log(f"mesh: {path}: {e}")

    def endpoints_path(self):
        return os.path.join(os.path.dirname(self.server.socket_path), ENDPOINTS)

    # ---- tunnels
    def frame(self, link, f):
        op = f.get("op")
        if op == "ping":
            return
        if op == "mesh":
            return self.learn(link, f.get("records"))
        if op == "open":
            return self.open_here(link, f)
        key = ("in", f["t"]) if isinstance(f.get("t"), int) else \
            ("out", f["r"]) if isinstance(f.get("r"), int) else None
        if key is None:
            return
        if op == "close":
            if (end := self.forget(link, key)) is not None:
                end.closed(f.get("error") if isinstance(f.get("error"), str) else None)
            return
        with self.lock:
            end = link.tunnels.get(key)
        if end is None:
            return
        if op == "opened":
            end.ready()
        elif op == "data":
            end.deliver(f.get("msg"))

    def forget(self, link, key):
        """A tunnel let go of on a link: its end here, if it was still there."""
        with self.lock:
            return link.tunnels.pop(key, None)

    def open_here(self, link, f):
        """open from a link: served here, if it's to here; else passed on toward the member it's to."""
        t, to, ttl, origin = f.get("t"), f.get("to"), f.get("ttl"), f.get("from")
        if not isinstance(t, int):
            return
        key = ("in", t)
        if to == self.id and origin in self.inactive:  # (from a member deactivated here)
            link.tsend(key, {"op": "close", "error": f"deactivated on {self.server.me['name']}"})
            return
        if to == self.id:
            mine, theirs = socket.socketpair()
            end = Here(self, wire.Channel.over(mine))
            end.link, end.key, end.origin = link, key, origin if wire.is_id(origin) else None
            with self.lock:
                link.tunnels[key] = end
            threading.Thread(target=self.server.serve, args=(theirs,), daemon=True, name="tunnel-served").start()
            link.tsend(key, {"op": "opened"})
            threading.Thread(target=end.pump, daemon=True, name="tunnel-in").start()
            return
        ahead = self.next_link(to) if wire.is_id(to) else None
        if ahead is None or ahead is link or not isinstance(ttl, int) or ttl <= 1:
            link.tsend(key, {"op": "close", "error": f"{self.server.me['name']} has no way to {self.name_of(to)}"})
            return
        okey = ahead.new_key()
        with self.lock:
            link.tunnels[key] = Passed(self, ahead, okey)
            ahead.tunnels[okey] = Passed(self, link, key)
        ahead.tsend(okey, {"op": "open", "to": to, "ttl": ttl - 1, **({"from": origin} if wire.is_id(origin) else {})})

    def connect(self, end, to):
        """A tunnel opened from here to member to, for end (a Here): None once it's there, else what's wrong."""
        if not wire.is_id(to):
            return "no such host"
        if to == self.id:
            return "that's this host"
        if to in self.inactive:
            return f"{self.name_of(to)} is deactivated here"
        ahead = self.next_link(to)
        if ahead is None:
            return f"{self.name_of(to)} isn't in the mesh now"
        key = ahead.new_key()
        end.link, end.key, end.to = ahead, key, to
        with self.lock:
            ahead.tunnels[key] = end
        ahead.tsend(key, {"op": "open", "to": to, "ttl": TTL, "from": self.id})
        if not end.up.wait(OPEN_S):
            if self.forget(ahead, key) is not None:
                ahead.tsend(key, {"op": "close"})
            return f"{self.name_of(to)} didn't answer"
        return end.error

    def relay(self, ch, msg):
        """relay {to} on a connection to this host process's socket: that connection, from now on, a tunnel to the
        member to (once relayed's said), run on this thread till either end's done."""
        end = Here(self, ch)
        if (error := self.connect(end, msg.get("to"))) is not None:
            end.out.close()
            ch.send({"op": "error", "req": msg.get("req"), "error": error})
            return
        end.out.put({"op": "relayed", "req": msg.get("req"), "to": msg.get("to")})
        end.pump()

    def channel(self, to, timeout=OPEN_S):
        """(channel, info) to member to's host process, from in this one, as a head's relay has it. OSError if it
        can't be had."""
        mine, theirs = socket.socketpair()
        threading.Thread(target=self.server.serve, args=(theirs,), daemon=True, name="relay-here").start()
        return wire.through(wire.Channel.over(mine), to, timeout)

    # ---- what heads are told
    def view(self):
        """info's mesh: {members: [{id, name, group, host, version, providers, hops, through (the member it's reached
        through, by name; None for one linked to here)}], links: [{where, id, name, state ("linked", "connecting",
        "dropped", "inactive"), error, active}] (those made from here), found: [{id, name, state ("waiting",
        "connecting", "linked", "dropped"), error}] (links straight to members learned of through others: Found),
        proxy: {url, key} (Proxy's: None till a member has a provider it serves), inactive: [the ids of members
        deactivated here]}."""
        with self.lock:
            members = []
            for k, n in sorted(self.hops.items(), key=lambda kv: (kv[1], self.name_of(kv[0]).lower())):
                rec = (self.records.get(k) or {}).get("rec")
                info = rec["info"] if rec else {**(self.links.get(k) or [None])[0].peer, "providers": []}
                members.append({**info, "hops": n, "through": None if n == 1 else self.name_of(self.routes.get(k))})
            links = [{"where": o.where, "id": o.id, "name": o.name, "state": o.state, "error": o.error,
                      "active": o.active} for o in self.outs]
            inactive = sorted(self.inactive)
            found = [{"id": f.expect, "name": f.name, "state": f.state, "error": f.error} for f in self.found.values()]
            if self.proxy is None and any(p.get("http") for m in members for p in m["providers"]):
                try:
                    self.proxy = Proxy(self).start()
                except OSError as e:
                    self.log(f"mesh: no proxy for other members' models: {e}")
                    self.proxy = False
            proxy = {"url": self.proxy.url, "key": self.proxy.key} if self.proxy else None
        return {"members": members, "links": links, "proxy": proxy, "inactive": inactive, "found": found}

    def providers_here(self):
        """This machine's providers, for its info: as local_providers has them, with no key."""
        return [{**published(p), "base_url": p.get("base_url")} for p in self.local]

    # ---- models
    def infer(self, conn, msg):
        """infer from a member (or a head here): an HTTP request to one of this machine's providers, its answer sent
        back as it comes. On a thread of its own."""
        threading.Thread(target=self._infer, args=(conn, msg), daemon=True, name="infer").start()

    def _infer(self, conn, msg):
        req = msg.get("req")
        reply = lambda kind, **kw: conn.send({"op": kind, "req": req, **kw})  # noqa: E731
        p = next((p for p in self.local if p["id"] == msg.get("provider") and p.get("base_url")), None)
        path, method = msg.get("path") or "/", str(msg.get("method") or "POST").upper()
        if p is None:
            return reply("error", error=f"no provider {msg.get('provider')!r} on {self.server.me['name']}")
        if not isinstance(path, str) or not path.startswith("/") or ".." in path or "\\" in path or "://" in path \
                or method not in ("GET", "POST"):
            return reply("error", error="not a request for a provider")
        heads = msg.get("headers") if isinstance(msg.get("headers"), dict) else {}
        headers = {k: str(v) for k, v in heads.items() if str(k).lower() in ("content-type", "accept")}
        if key := p.get("api_key") or provider_key(p):
            headers["Authorization"] = f"Bearer {key}"
        try:
            body = base64.b64decode(msg.get("body") or "") if method == "POST" else None
        except ValueError:
            return reply("error", error="its body isn't base64")
        request = urllib.request.Request(p["base_url"].rstrip("/") + path, data=body, method=method, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # (straight to it, no proxy between)
        try:
            resp = opener.open(request, timeout=INFER_S)
        except urllib.error.HTTPError as e:
            resp = e
        except OSError as e:
            return reply("error", error=f"{p['name']} on {self.server.me['name']}: {e}")
        with resp:
            reply("infer_head", status=resp.getcode(),
                  headers={k: v for k in ("Content-Type",) if (v := resp.headers.get(k))})
            gone = lambda: getattr(getattr(conn, "channel", None), "closed", False)  # noqa: E731
            try:
                while chunk := (resp.read1(CHUNK) if hasattr(resp, "read1") else resp.read(CHUNK)):
                    if gone():  # (whoever asked has gone: the provider's let go of, so it stops)
                        return
                    reply("infer_data", data=base64.b64encode(chunk).decode())
            except OSError as e:
                return reply("error", error=f"{p['name']} on {self.server.me['name']}: {e}")
        reply("infer_end")


class Proxy:
    """Other members' HTTP providers on 127.0.0.1, at {url}/{member}/{provider}, each an OpenAI-compatible server's
    address for what's on this machine (their models, run there): a request, with key (Authorization: Bearer, or
    x-api-key), carried to the member through the mesh and its answer back, as it comes."""

    def __init__(self, mesh):
        self.mesh, self.key = mesh, secrets.token_urlsafe(24)
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                proxy.carry(self)

            do_POST = do_GET

            def log_message(self, *args):
                pass
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mesh"

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="mesh-proxy").start()
        return self

    def stop(self):
        threading.Thread(target=self.httpd.shutdown, daemon=True).start()

    def carry(self, h):
        def fail(status, error):
            body = json.dumps({"error": {"message": error}}).encode()
            h.send_response(status)
            h.send_header("Content-Type", "application/json")
            h.send_header("Content-Length", str(len(body)))
            h.send_header("Connection", "close")
            h.end_headers()
            h.wfile.write(body)
        given = h.headers.get("Authorization", "").removeprefix("Bearer ").strip() or h.headers.get("x-api-key", "")
        if not secrets.compare_digest(given.encode(), self.key.encode()):
            return fail(401, "not this proxy's key")
        parts = h.path.split("/", 4)
        provider = urllib.parse.unquote(parts[3]) if len(parts) >= 4 else None
        if len(parts) < 4 or parts[1] != "mesh" or not wire.is_id(parts[2]) or not is_provider_id(provider):
            return fail(404, "not a member's provider: /mesh/<member>/<provider>/...")
        member, rest = parts[2], "/" + (parts[4] if len(parts) > 4 else "")
        try:
            body = h.rfile.read(int(h.headers.get("Content-Length") or 0)) if h.command == "POST" else b""
        except ValueError:
            return fail(400, "no Content-Length")
        try:
            ch, _info = self.mesh.channel(member)
        except OSError as e:
            return fail(502, str(e))
        try:
            ch.sock.settimeout(wire.SILENT_S)
            ch.keep_alive()
            ch.send({"op": "infer", "req": 1, "provider": provider, "method": h.command, "path": rest,
                     "headers": {k: v for k in ("Content-Type", "Accept") if (v := h.headers.get(k))},
                     "body": base64.b64encode(body).decode()})
            started = False
            while (msg := ch.reply()) is not None:
                op = msg.get("op")
                if op == "infer_head" and not started:
                    started = True
                    h.send_response(int(msg.get("status") or 502))
                    for k, v in (msg.get("headers") or {}).items():
                        h.send_header(k, v)
                    h.send_header("Transfer-Encoding", "chunked")
                    h.send_header("Connection", "close")
                    h.end_headers()
                elif op == "infer_data" and started:
                    data = base64.b64decode(msg.get("data") or "")
                    if data:
                        h.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                        h.wfile.flush()
                elif op == "infer_end" and started:
                    h.wfile.write(b"0\r\n\r\n")
                    return
                elif op == "error":
                    if not started:
                        return fail(502, str(msg.get("error")))
                    return  # (cut short: the client sees the answer end with no last chunk)
            if not started:
                fail(502, f"{self.mesh.name_of(member)} went")
        except OSError:
            pass  # (the client's gone)
        finally:
            h.close_connection = True
            ch.close()
