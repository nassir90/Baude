"""Terminals the host process runs (wire's terminal op): a program in a pty here, its bytes carried to whoever's
attached (a connection each: Baude's and Record's Terminal page, the phone's), what they type carried back. One is
kept running a while (TERMINAL_KEEP_S) once no one's attached, so a page put in a window of its own, or a phone
that's been away, attaches to it again (TERMINAL_KEPT_BYTES of what it wrote lately shown first). No GTK: the
bytes are the viewer's to draw (VTE on the desktop, the phone's own)."""
import base64
import errno
import fcntl
import os
import pty
import signal
import struct
import termios
import threading
import time
import uuid

from baudelib import clearrelay, wire

KEEP_S = wire.CONSTANTS["TERMINAL_KEEP_S"]
KEPT = wire.CONSTANTS["TERMINAL_KEPT_BYTES"]
_terms = {}  # (id: HostTerminal)
_lock = threading.Lock()


def _size(fd, columns, rows):
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", max(1, rows), max(1, columns), 0, 0))
    except OSError:
        pass


class HostTerminal:
    def __init__(self, argv, cwd, title, columns, rows):
        self.id = uuid.uuid4().hex[:12]
        self.argv = argv or [os.environ.get("SHELL") or "/bin/sh", "-l"]
        self.cwd = cwd if cwd and os.path.isdir(os.path.expanduser(cwd)) else os.path.expanduser("~")
        self.title = title or os.path.basename(self.argv[0])
        self.columns, self.rows = columns or 80, rows or 24
        self.kept, self.viewers, self.status, self.left = bytearray(), {}, None, time.monotonic()
        self.lock = threading.Lock()
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", "TERM_PROGRAM": "Multiplex"}
        run = clearrelay.relayed(self.argv, env.get("PATH"))  # (its clears done without blank rows: clearrelay's)
        pid, fd = pty.fork()
        if pid == 0:  # (the child: its folder, then the program; never back into Python's host)
            try:
                os.chdir(os.path.expanduser(self.cwd))
                os.execvpe(run[0], run, env)
            except OSError as e:
                os.write(2, f"{self.argv[0]}: {e.strerror}\r\n".encode())
            os._exit(127)
        self.pid, self.fd = pid, fd
        _size(fd, self.columns, self.rows)
        threading.Thread(target=self._read, daemon=True, name="terminal").start()

    def info(self):
        return {"id": self.id, "title": self.title, "argv": self.argv, "cwd": self.cwd, "columns": self.columns,
                "rows": self.rows, "viewers": len(self.viewers), "running": self.status is None}

    def _read(self):
        while True:
            try:
                data = os.read(self.fd, 65536)
            except OSError as e:
                data = b"" if e.errno in (errno.EIO, errno.EBADF) else b""
            if not data:
                break
            with self.lock:
                self.kept += data
                if len(self.kept) > KEPT:
                    del self.kept[:len(self.kept) - KEPT]
                viewers = list(self.viewers.items())
            said = base64.b64encode(data).decode()
            for conn, req in viewers:  # (a connection that's gone: let go of by gone, as it closes)
                conn.send({"op": "terminal_out", "req": req, "data": said})
        try:
            _pid, status = os.waitpid(self.pid, 0)
            self.status = os.waitstatus_to_exitcode(status)
        except ChildProcessError:
            self.status = -1
        try:
            os.close(self.fd)
        except OSError:
            pass
        with self.lock:
            viewers = list(self.viewers.items())
        for conn, req in viewers:
            conn.send({"op": "terminal_end", "req": req, "status": self.status})
        if not viewers:
            forget(self)

    def attach(self, conn, req):
        with self.lock:
            self.viewers[conn] = req
            kept = bytes(self.kept)
        conn.terminal = self
        conn.send({"op": "terminal_opened", "req": req, "id": self.id, "title": self.title, "argv": self.argv,
                   "cwd": self.cwd})
        if kept:
            conn.send({"op": "terminal_out", "req": req, "data": base64.b64encode(kept).decode()})
        if self.status is not None:
            conn.send({"op": "terminal_end", "req": req, "status": self.status})

    def detach(self, conn):
        with self.lock:
            self.viewers.pop(conn, None)
            if not self.viewers:
                self.left = time.monotonic()
        if getattr(conn, "terminal", None) is self:
            conn.terminal = None
        if self.status is not None and not self.viewers:
            forget(self)

    def write(self, data):
        if self.status is None:
            try:
                os.write(self.fd, data)
            except OSError:
                pass

    def resize(self, columns, rows):
        self.columns, self.rows = columns, rows
        if self.status is None:
            _size(self.fd, columns, rows)

    def hang_up(self):
        if self.status is None:
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGHUP)
            except OSError:
                try:
                    os.kill(self.pid, signal.SIGHUP)
                except OSError:
                    pass


def forget(term):
    with _lock:
        _terms.pop(term.id, None)


def _reap():
    """Each minute: a terminal no one's been attached to for KEEP_S hung up."""
    while True:
        time.sleep(60)
        now = time.monotonic()
        with _lock:
            idle = [t for t in _terms.values() if not t.viewers and now - t.left > KEEP_S]
        for t in idle:
            t.hang_up()
            if t.status is not None:
                forget(t)


threading.Thread(target=_reap, daemon=True, name="terminal-reaper").start()


def take(conn, msg):
    """A terminal request on conn (terminal, terminal_in, terminal_resize, terminal_close, terminals): None, or the
    reply's fields for an error."""
    op, req = msg.get("op"), msg.get("req")
    if op == "terminals":
        with _lock:
            listed = [t.info() for t in _terms.values()]
        conn.send({"op": "terminals", "req": req, "terminals": listed})
        return None
    term = getattr(conn, "terminal", None)
    if op == "terminal":
        if term is not None:
            return "one terminal a connection"
        if msg.get("attach"):
            with _lock:
                term = _terms.get(msg["attach"])
            if term is None:
                return f"no terminal {msg['attach']} here (terminals: the ones there are)"
            if msg.get("columns") and msg.get("rows"):
                term.resize(int(msg["columns"]), int(msg["rows"]))
            term.attach(conn, req)
            return None
        argv = msg.get("argv")
        if argv is not None and (not isinstance(argv, list) or not argv or
                                 not all(isinstance(a, str) for a in argv)):
            return "argv is a list of strings"
        term = HostTerminal(argv, msg.get("cwd"), msg.get("title"), msg.get("columns"), msg.get("rows"))
        with _lock:
            _terms[term.id] = term
        term.attach(conn, req)
        return None
    if term is None:
        return f"{op}: this connection has no terminal"
    if op == "terminal_in":
        try:
            term.write(base64.b64decode(msg.get("data") or ""))
        except ValueError:
            return "terminal_in's data is base64"
    elif op == "terminal_resize":
        term.resize(int(msg["columns"]), int(msg["rows"]))
    elif op == "terminal_close":
        term.hang_up()
        term.detach(conn)
    return None


def gone(conn):
    """conn's gone: its terminal left running, for KEEP_S, to be attached to again."""
    term = getattr(conn, "terminal", None)
    if term is not None:
        term.detach(conn)
