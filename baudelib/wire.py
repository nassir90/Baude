"""What Baude and a server say to each other: one JSON object a line (UTF-8, ending in a newline), each with an "op".
The same lines could go over stdin and stdout: Channel takes any pair of binary files, a socket's or a pipe's.

A client's requests (a "req" it picks comes back on the reply):
    hello {client}                                  -> hello {server: info}
    subscribe                                       -> sessions {sessions}, then again on every change, and ping
    start {prompt, cwd, model, effort, mode, group, title?}
                                                    -> started {session}
    attach {session, live}                          -> attached {session, asks}; live: claude {session, msg} as it
                                                       speaks, exited {session, code} when it stops
    detach {session}
    input {session, msg}                            (a stream-json message for the session's claude: started on it,
                                                     with --resume, if it isn't running)
    stop {session}                                  -> stopped {session}
    transcript {session, offset, limit?}            -> transcript {session, offset, next, size, data (base64)}
    subagents {session, have?, limit?}              -> subagents {session, files: [{name, size, mtime, offset, next,
                                                       data (base64)}], more} (its subagents' files, in the folder
                                                       beside its transcript: each one's transcript, from where have
                                                       {name: [size, mtime]} says the client's copy is, in whole lines
                                                       like transcript's; its meta.json whole, if it's changed. more:
                                                       there's more, past limit; those it didn't reach aren't listed)
    rename {session, title}                         -> renamed {session} (claude renames it, if it's running; else
                                                       the server does as claude would)
    read_file {path, limit?}                        -> file {path, size, data (base64), error?} (in a session's folder)
    ping                                            (both ways, every PING_S on every connection: SILENT_S of silence
                                                     and the other end's taken for gone)
Anything wrong: error {error}, with the request's "req".

A session is {id, title, cwd, model, effort, mode, group, created, said, updated, size, state, prompts, subagents}:
its id a UUID the server made as it started it (claude's --session-id; what it's resumed by), state "working" (at a
turn), "waiting" (asking permission), "idle" (claude running, between turns) or "stopped" (no claude running),
subagents the mtime of its subagents' newest file (0 for none: what a client's copy is checked against).
"""
import json
import socket
import threading

VERSION = 1
SERVICE = "_baude._tcp.local."  # (DNS-SD: discovery.py)
STATES = ("working", "waiting", "idle", "stopped")
PING_S = 5      # a subscriber hears from the server at least this often
SILENT_S = 16   # and takes it for gone after this long without a word


def encode(msg):
    return (json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


class Channel:
    """Messages over a pair of binary files (rfile, wfile): send from any thread, read in one."""

    def __init__(self, rfile, wfile, sock=None):
        self.rfile, self.wfile, self.sock = rfile, wfile, sock
        self.lock = threading.Lock()
        self.closed = False

    @classmethod
    def over(cls, sock):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return cls(sock.makefile("rb"), sock.makefile("wb"), sock)

    def send(self, msg):
        """False once the other end's gone."""
        data = encode(msg)
        with self.lock:
            if self.closed:
                return False
            try:
                self.wfile.write(data)
                self.wfile.flush()
                return True
            except (OSError, ValueError):
                self.closed = True
                return False

    def read(self):
        """The next message, or None at the end (closed, or a line that isn't one)."""
        while True:
            try:
                line = self.rfile.readline()
            except (OSError, ValueError):
                return None
            if not line:
                return None
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                return None
            return msg if isinstance(msg, dict) else None

    def keep_alive(self):
        """A ping every PING_S till it's closed, so the other end, which takes SILENT_S of silence for gone, doesn't."""
        def run():
            while not self.closed and self.send({"op": "ping"}):
                self.pinged.wait(PING_S)
        self.pinged = threading.Event()
        threading.Thread(target=run, daemon=True, name="ping").start()

    def __iter__(self):
        while (msg := self.read()) is not None:
            yield msg

    def close(self):
        with self.lock:
            self.closed = True
        if hasattr(self, "pinged"):
            self.pinged.set()
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for f in (self.wfile, self.rfile, self.sock):
            try:
                f is not None and f.close()
            except OSError:
                pass


def connect(host, port, timeout=5.0):
    """A Channel to a server (OSError if it can't be reached), past hello: (channel, the server's info)."""
    sock = socket.create_connection((host, port), timeout=timeout)
    ch = Channel.over(sock)
    ch.send({"op": "hello", "client": "baude", "version": VERSION})
    reply = ch.read()
    if not reply or reply.get("op") != "hello":
        ch.close()
        raise OSError(f"{host}:{port} isn't a Baude server")
    sock.settimeout(None)
    return ch, reply.get("server") or {}
