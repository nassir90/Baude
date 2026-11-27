"""The SDK's wire: a task's script's connections to the hosts of the mesh, and what the generated client
(generated.py) is made of. Hand-written; nothing here but Python's own library.

Each call goes to a host (host=: any host of the mesh, by its name or id; this one if none's given) on a connection of
its thread's own: this host's socket (BAUDE_SOCKET), its hello naming the run (BAUDE_TASK_RUN, BAUDE_TASK_TOKEN), so the
host judges every call as the run's Bot would be judged (the access rules, as for the Bot's own tools); another host
through this one's relay, as from the Bot. Outside a task, this user's own host process, as a head here is."""
import base64
import json
import os
import socket
import threading
import time

import baudetask

PING_S = 5  # (a long wait: the host told it's still here this often)
IDLE_S = 10  # (a connection quiet this long between calls: another made for the next; the host lets one go at 16 s)
WAIT_S = 130  # (a call's answer waited for this long, by default: Emission's start and a page's load, and then some)


class Refused(baudetask.Refused):
    """The host refused the call, or couldn't do it: what it said (code: access_denied, unsupported_operation,
    invalid_data_type, or None; rule: the access rule that refused it, if one did)."""

    def __init__(self, text, code=None, rule=None, op=None, host=None):
        super().__init__(text)
        self.code, self.rule, self.op, self.host = code, rule, op, host


def runtime_dir():
    """Where this user's host process listens (as baudelib.wire.runtime_dir has it)."""
    if os.environ.get("BAUDE_RUNTIME_DIR"):
        return os.environ["BAUDE_RUNTIME_DIR"]
    if os.environ.get("XDG_RUNTIME_DIR"):
        return os.path.join(os.environ["XDG_RUNTIME_DIR"], "baude")
    path = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "baude", "run")
    if len(os.path.join(path, "host.sock.lock").encode()) <= 100:
        return path
    return f"/tmp/baude-{os.getuid()}"


def socket_path():
    return os.environ.get("BAUDE_SOCKET") or os.path.join(runtime_dir(), "host.sock")


def bot():
    """The run's Bot ({id, name}), as its host gave it (BAUDE_TASK_BOT), or None outside a task."""
    try:
        got = json.loads(os.environ.get("BAUDE_TASK_BOT") or "null")
    except ValueError:
        return None
    return got if isinstance(got, dict) and got.get("id") else None


class Conn:
    """One connection to a host (where: None for this one, else a member's id), a request at a time."""

    def __init__(self, where=None):
        self.where, self.sock, self.buf, self.req, self.sent, self.info = where, None, b"", 0, time.monotonic(), {}

    def _send(self, msg):
        self.sock.sendall((json.dumps(msg) + "\n").encode())
        self.sent = time.monotonic()

    def line(self, end):
        """The next message from the host, by end (monotonic), the host pinged meanwhile. TimeoutError past end."""
        while b"\n" not in self.buf:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError("the host didn't answer in time")
            self.sock.settimeout(max(0.05, min(left, self.sent + PING_S - time.monotonic())))
            try:
                part = self.sock.recv(1 << 16)
            except socket.timeout:
                if time.monotonic() - self.sent >= PING_S:
                    self._send({"op": "ping"})
                continue
            if not part:
                raise ConnectionError("the host closed the connection")
            self.buf += part
        got, self.buf = self.buf.split(b"\n", 1)
        return json.loads(got)

    def _hello(self, end, task=True):
        version = int(os.environ.get("BAUDE_WIRE_VERSION") or 0)
        run, token = os.environ.get("BAUDE_TASK_RUN"), os.environ.get("BAUDE_TASK_TOKEN")
        self._send({"op": "hello", "client": "task", "req": 0, **({"version": version} if version else {}),
                    **({"task": {"run": run, "token": token}} if task and run and token else {})})
        got = self.line(end)
        while got.get("op") not in ("hello", "error"):  # (a ping, or what a tunnel says first: passed by)
            got = self.line(end)
        if got.get("op") != "hello":
            raise refused(got, "hello", self.where)
        return got.get("server") or {}

    def connect(self, timeout=15):
        end = time.monotonic() + timeout
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.connect(socket_path())
            self.info = self._hello(end)
            if self.where is not None:  # (through this host to a member of the mesh: from the run's Bot)
                self._send({"op": "relay", "to": self.where, "req": 0})
                got = self.line(end + 20)
                if got.get("op") != "relayed":
                    raise refused(got, "relay", self.where)
                self.info = self._hello(end + 20, task=False)
        except BaseException:
            self.close()
            raise
        return self

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock, self.buf = None, b""

    def request(self, msg, timeout=WAIT_S):
        """The host's answer to msg (its req's), or Refused."""
        if self.sock is not None and time.monotonic() - self.sent > IDLE_S:
            self.close()  # (quiet so long the host may have let go of it: another)
        if self.sock is None:
            self.connect()
        self.req += 1
        req = self.req
        try:
            self._send({**msg, "req": req})
            end = time.monotonic() + timeout
            while True:
                got = self.line(end)
                if got.get("req") == req:
                    break
        except (OSError, ValueError):
            self.close()  # (the next call connects again)
            raise
        if got.get("op") == "error":
            raise refused(got, msg.get("op"), self.where)
        return got

    def stream(self, msg, until, timeout=WAIT_S):
        """The messages answering msg (its req's), as they come, till until(message) is true (that one too)."""
        if self.sock is None:
            self.connect()
        self.req += 1
        req = self.req
        self._send({**msg, "req": req})
        end = time.monotonic() + timeout
        while True:
            got = self.line(end)
            if got.get("req") != req:
                continue
            if got.get("op") == "error":
                raise refused(got, msg.get("op"), self.where)
            yield got
            if until(got):
                return


def refused(reply, op=None, host=None):
    reply = reply or {}
    text = ": ".join(str(x) for x in (reply.get("error"), reply.get("detail")) if x) or "the host didn't answer"
    if reply.get("denied"):
        text = f"{op}: {text} (the task's Bot isn't let do this on that host)"
    return Refused(text, reply.get("code"), reply.get("denied"), op, host)


_local = threading.local()


def conn(where=None):
    """This thread's connection to where (None: this host; else a member's id), made if it isn't."""
    have = getattr(_local, "conns", None)
    if have is None:
        have = _local.conns = {}
    c = have.get(where)
    if c is None or c.sock is None:
        c = have[where] = Conn(where).connect()
    return c


_view = {"at": 0.0, "info": None}
_view_lock = threading.Lock()


def here(fresh=False):
    """This host's hello info (its id, name, models, mesh), read again every few seconds at most."""
    with _view_lock:
        if fresh or _view["info"] is None or time.monotonic() - _view["at"] > 5:
            c = Conn().connect()
            try:
                _view["info"], _view["at"] = c.info, time.monotonic()
            finally:
                c.close()
        return _view["info"]


def where(host):
    """Where a call for host (a name, an id, a Host, or None) goes: None for this host, else the member's id."""
    if host is None:
        return None
    if not isinstance(host, str):
        host = getattr(host, "id", None) or host.get("id")
    for fresh in (False, True):
        info = here(fresh)
        if host in (info.get("id"), info.get("name")):
            return None
        view = info.get("mesh") or {}
        for m in (view.get("members") or []) + (view.get("gone") or []) + (view.get("links") or []):
            if host in (m.get("id"), m.get("name")) and m.get("id") and m["id"] not in (view.get("inactive") or []):
                return m["id"]
    raise Refused(f"no host {host!r} in the mesh (hosts() lists them)")


def call(op, fields, host=None, timeout=WAIT_S):
    """op's request ({op, **fields}, None values left out) to host: its answer, its op and req left out."""
    msg = {"op": op, **{k: v for k, v in fields.items() if v is not None}}
    to = where(host)
    got = conn(to).request(msg, timeout)
    return {k: v for k, v in got.items() if k not in ("op", "req")}


def b64(data):
    return base64.b64encode(data.encode() if isinstance(data, str) else data).decode()


class Obj(dict):
    """An object the host answered with, as the schema has it: its fields as attributes too (obj.name is
    obj["name"]), None for one it doesn't have. Pass it back to the calls that take one (kube.logs(pod))."""
    _refs = {}  # (a field's entity, for those that are one or a list of them: its class's name)
    _types = {}  # (every entity's class, by its name: generated.py's)

    def __init__(self, data=(), **more):
        super().__init__(data, **more)
        for k, name in self._refs.items():
            cls = self._types.get(name, Obj)
            v = self.get(k)
            if isinstance(v, dict) and not isinstance(v, Obj):
                self[k] = cls(v)
            elif isinstance(v, list):
                self[k] = [cls(x) if isinstance(x, dict) and not isinstance(x, Obj) else x for x in v]

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self.get(name)

    def __setattr__(self, name, value):
        self[name] = value

    def __repr__(self):
        shown = {k: v for k, v in self.items() if not isinstance(v, (list, dict)) or len(str(v)) < 80}
        return f"{type(self).__name__}({shown})"


class Area:
    """A group of calls (kube, docker, ...): the generated client's methods are its; host: the default host of
    every call made through it (none: this one)."""

    def __init__(self, host=None):
        self.host = host

    def on(self, host):
        """The same calls, to host by default."""
        return type(self)(host)

    def _host(self, host):
        return host if host is not None else self.host

    def _call(self, op, fields, host=None, answer=Obj, timeout=WAIT_S):
        got = call(op, fields, self._host(host), timeout)
        failed(got, op, host)
        return answer(got)

    def _tool(self, op, call_, host=None, timeout=WAIT_S):
        """One of the Bot's tools' requests (emission's, multiplex's): what it answered."""
        me = bot()
        if me is None:
            raise Refused(f"{op} drives a Bot's own browser and terminals: run this as one of its tasks")
        got = call(op, {"bot": me, "call": {k: v for k, v in call_.items() if v is not None}}, self._host(host),
                   timeout)
        return Obj(got.get("result") or {})


def failed(got, op, host=None):
    """Refused for an answer that says the call couldn't be done (error, fault, missing, removed)."""
    if got.get("error"):
        raise Refused(f"{op}: {got['error']}", op=op, host=host)
    if isinstance(got.get("fault"), dict):
        f = got["fault"]
        raise Refused(f"{op}: " + ": ".join(str(x) for x in (f.get("title"), f.get("why")) if x), op=op, host=host)
    if isinstance(got.get("missing"), dict):
        m = got["missing"]
        raise Refused(f"{op}: {m.get('name') or m.get('id') or 'a program'} isn't on that host", op=op, host=host)
    if got.get("removed"):
        raise Refused(f"{op}: that context is no longer in the host's kubeconfig", op=op, host=host)
