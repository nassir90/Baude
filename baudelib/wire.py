"""What Baude and a server say to each other: one JSON object a line (UTF-8, ending in a newline), each with an "op".
The same lines could go over stdin and stdout: Channel takes any pair of binary files, a socket's or a pipe's.

A client's requests (a "req" it picks comes back on the reply):
    hello {client}                                  -> hello {server: info}
    subscribe {archived?}                           -> sessions {sessions}, then again on every change, and ping;
                                                       turn_ended {session, result, is_error, cost, turns,
                                                       duration_ms} as each of its sessions' turns ends. archived:
                                                       those archived listed too, and their turns' ends (default:
                                                       not)
    start {prompt, cwd, model, effort, mode, group, title?, attach?, answers?, system?, add_dirs?,
           allowed_tools?, disallowed_tools?, fork_from?, ask_timeout?, ask_default?}
                                                    -> started {session}, before the prompt's sent: attach, and this
                                                       connection's attached live to it first. system: more for
                                                       claude's system prompt (--append-system-prompt); add_dirs:
                                                       folders it may use besides its own (--add-dir);
                                                       allowed_tools / disallowed_tools: tool rules (--allowedTools,
                                                       --disallowedTools: ["Bash(git:*)", "Edit"]); fork_from: a
                                                       session of this server's whose conversation this one starts
                                                       as a copy of (--fork-session); those are claude's, for
                                                       Claude's models. ask_timeout: seconds a permission it asks
                                                       waits while nobody who answers is attached, then ask_default
                                                       ("deny", or "allow") answers it. answers: as attach's
    attach {session, live, answers?}                -> attached {session, asks}; live: claude {session, msg} as it
                                                       speaks, exited {session, code} when it stops; deleted
                                                       {session} when it's deleted (nothing to carry on). answers
                                                       (default true; kept for the connection, its last attach's or
                                                       start's): false for a head that won't answer what the session
                                                       asks (a permission): it isn't counted as there to answer, so
                                                       ask_timeout's wait still runs out and ask_default answers, as
                                                       if nobody were attached
    detach {session}
    input {session, msg}                            -> accepted {session}, given a req (a stream-json message for
                                                       the session's claude: started on it, with --resume, if it
                                                       isn't running)
    stop {session}                                  -> stopped {session}
    archive {session}, unarchive {session}          -> archived / unarchived {session}: its claude stopped, and it's
                                                       listed only to those subscribed with archived (unarchive: to
                                                       all again)
    delete {session}                                -> deleted {session, trashed: [paths]}: its claude stopped and
                                                       waited for, its transcript and the folder beside it (its
                                                       subagents'), and pi's own copy of it, moved to the trash,
                                                       where they can be got back, as Baude's Move to Trash does
                                                       with a chat of its own; then it's gone from the list and from
                                                       what the server keeps. One whose files can't be moved stays:
                                                       error, saying why
    quit                                            -> quitting: the host process stops, its claudes too (their
                                                       sessions kept, resumed next time)
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

A session is {id, title, cwd, model, effort, mode, group, created, said, updated, size, state, prompts, archived,
subagents, asks, cli_id}: its id a UUID the server made as it started it (claude's --session-id; what it's resumed by), state "working"
(at a turn), "waiting" (asking permission), "idle" (claude running, between turns) or "stopped" (no claude running),
archived whether it's archived, subagents the mtime of its subagents' newest file (0 for none: what a client's copy
is checked against), asks what
it's asking, not answered yet: [{request_id, tool, input}] (long strings in input cut short), cli_id the id claude
has for it where that's another (a fork's: its transcript's named so), else None. An ask is answered with
input: a control_response for its request_id, as claude's stream-json has it.

A session listed by a server on this machine says where its transcript is ("transcript": a path, or None), so what's
on the same machine reads it there rather than fetching a copy.

Where a server is: a host process (baude_headless.py) listens on a Unix socket only (socket_path(), in a folder that's
its user's alone: that folder is all the say there is in who may use it), with no port on the network. What's on the
same machine connects to the socket (connect_local); another machine reaches it over ssh (connect_ssh), as
`ssh <host> baude_headless.py connect`, which carries these lines between ssh's stdin and stdout and the socket (bridge),
starting the host process if it isn't running. So ssh says who may, and keeps what's said private: the keys in
authorized_keys and known_hosts, never a password asked for. A client that isn't Python asks `baude_headless.py
socket` where this user's socket is, rather than copying runtime_dir's rules: $BAUDE_RUNTIME_DIR, else
$XDG_RUNTIME_DIR/baude (which a session over ssh and one at the screen may have differently, or lose as a session
ends), else $XDG_STATE_HOME/baude/run (~/.local/state/baude/run), or /tmp/baude-<uid> where that's too long a path for
a socket; never $TMPDIR, which macOS gives ssh's sessions another of. Or it runs `baude_headless.py connect` and talks
to its stdin and stdout. reach(where)
connects to any of them:
    {"via": "local"}                                this machine's host process (socket_path())
    {"via": "unix", "path": P}                      one listening on another socket
    {"via": "ssh", "dest": D, "command"?, "strict"?}  ssh D (an alias in ~/.ssh/config, a host, user@host): command,
                                                    the host process on D, as it's given here (default
                                                    baude_headless.py, found on its PATH); strict: its key must be
                                                    known already
"""
import json
import os
import re
import select
import socket
import stat
import subprocess
import threading
import time

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
        if sock.family in (socket.AF_INET, socket.AF_INET6):
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

    def reply(self):
        """The next message that isn't a ping (the other end's, which may come first, whenever it's due), or None."""
        while (msg := self.read()) is not None and msg.get("op") == "ping":
            pass
        return msg

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
        if (proc := getattr(self, "proc", None)) is not None:  # (ssh's: it goes with it)
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()


SSH_COMMAND = "baude_headless.py"  # (the host process on another machine, found on its PATH)
_DEST = re.compile(r"[A-Za-z0-9_.@%:\[\]+-]+")
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
SUN_PATH = 104  # (macOS's room for a socket's path, and its end: Linux has 108)


def is_id(value):
    """Is it an id a host may give (its own, a session's): a plain word, no path in it, as files here are named by
    them?"""
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9_.-]", "-", text or "")[:64] or "host"


def runtime_dir():
    """Where this machine's host process listens, and ssh keeps its connections to other machines' to share: its
    user's own folder ($BAUDE_RUNTIME_DIR; else $XDG_RUNTIME_DIR/baude; else ~/.local/state/baude/run, or baude-<uid>
    in /tmp if that's too long a path for a socket). Not $TMPDIR: macOS sets it for a session at the screen but not
    for one ssh starts, and both must find the same host process."""
    if os.environ.get("BAUDE_RUNTIME_DIR"):
        return os.environ["BAUDE_RUNTIME_DIR"]
    if os.environ.get("XDG_RUNTIME_DIR"):
        return os.path.join(os.environ["XDG_RUNTIME_DIR"], "baude")
    path = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "baude", "run")
    if len(os.path.join(path, "host.sock.lock").encode()) <= 100:
        return path
    return f"/tmp/baude-{os.getuid()}"


def socket_path():
    return os.path.join(runtime_dir(), "host.sock")


def own_dir(path):
    """A folder that's this user's alone (made, if it isn't there): not a link, theirs, no one else let in. OSError
    if it can't be (another user's, or something else there)."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    st = os.lstat(path)
    if not os.path.isdir(path) or os.path.islink(path) or st.st_uid != os.getuid():
        raise OSError(f"{path} isn't a folder of this user's own")
    if st.st_mode & 0o077:
        os.chmod(path, 0o700)
    return path


def _hello(ch, timeout, what):
    """(channel, the server's info) past hello, or OSError: what didn't answer."""
    ch.sock.settimeout(timeout)
    ch.send({"op": "hello", "client": "baude", "version": VERSION})
    reply = ch.reply()
    if not reply or reply.get("op") != "hello":
        ch.close()
        raise OSError((reply or {}).get("error") or f"{what} isn't a Baude host")
    ch.sock.settimeout(None)
    return ch, reply.get("server") or {}


def own_socket(path):
    """OSError unless a socket's this user's own, in a folder no one else may write in: one another user left where
    this user's should be (the /tmp folder, say) is no host process of theirs, and isn't connected to."""
    folder, me = os.lstat(os.path.dirname(path) or "."), os.getuid()
    st = os.lstat(path)
    if not stat.S_ISDIR(folder.st_mode) or folder.st_uid != me or folder.st_mode & 0o022 or \
            not stat.S_ISSOCK(st.st_mode) or st.st_uid != me:
        raise OSError(f"{path} isn't this user's own: not connected to")


def connect_local(path=None, timeout=5.0):
    """A Channel to the host process listening on a Unix socket (this machine's, socket_path(), if none's given)."""
    path = path or socket_path()
    own_socket(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(path)
    except OSError:
        sock.close()
        raise
    return _hello(Channel.over(sock), timeout, path)


def is_dest(dest):
    """Can this be given to ssh as where to go: a name, user@host or an address, not an option?"""
    return isinstance(dest, str) and bool(_DEST.fullmatch(dest)) and not dest.startswith("-")


def ssh_args(dest, command=None, strict=False):
    """ssh's command line for a host's process: no password or passphrase ever asked (BatchMode: its keys and agent,
    as ~/.ssh/config has them), a connection to each host kept for a minute and shared (ControlMaster), so a
    session's own connection doesn't wait for another handshake. strict: the host's key must be in known_hosts
    already (a host found on the network, which anyone can say they are). command: the host's process, a command line
    (default SSH_COMMAND, on its PATH)."""
    if not is_dest(dest):
        raise OSError(f"not somewhere ssh can go: {dest!r}")
    control = control_path(dest)
    share = ["-o", "ControlMaster=auto", "-o", f"ControlPath={control}", "-o", "ControlPersist=60"] if control else \
        ["-o", "ControlMaster=no", "-o", "ControlPath=none"]
    return [os.environ.get("BAUDE_SSH") or "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", *share,
            *(["-o", "StrictHostKeyChecking=yes"] if strict else []), "--", dest,
            f"{command or SSH_COMMAND} connect"]


def control_path(dest):
    """Where ssh keeps its shared connection to dest: short, as the socket ssh makes there first is its path and 17
    more (".", 16 random letters); in the runtime folder, else this user's own folder in /tmp; None if neither has
    room (each connection its own, then)."""
    import hashlib
    name = "c-" + hashlib.sha1(dest.encode()).hexdigest()[:12]
    for folder in (runtime_dir(), f"/tmp/baude-{os.getuid()}"):
        path = os.path.join(folder, name)
        if len(path.encode()) + 17 < SUN_PATH:
            try:
                own_dir(folder)
                return path
            except OSError:
                continue
    return None


def connect_ssh(dest, command=None, strict=False, timeout=25.0):
    """A Channel to another machine's host process, over ssh (ssh_args): its stdin and stdout one end of a socket
    pair, this the other, so a Channel takes it as it takes a socket. OSError, with what ssh said, if it can't."""
    mine, theirs = socket.socketpair()
    try:
        proc = subprocess.Popen(ssh_args(dest, command, strict), stdin=theirs, stdout=theirs, stderr=subprocess.PIPE,
                                start_new_session=True)
    except OSError:
        mine.close()
        raise
    finally:
        theirs.close()
    said = []

    def errors():  # (what ssh says goes wrong: the last of it, for the error)
        for line in proc.stderr:
            said[:] = (said + [line.decode("utf-8", "replace").strip()])[-3:]
    threading.Thread(target=errors, daemon=True, name="ssh-stderr").start()
    ch = Channel.over(mine)
    ch.proc = proc
    try:
        return _hello(ch, timeout, dest)
    except OSError as e:
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        raise OSError("; ".join(s for s in said if s) or str(e)) from None


def reach(where, timeout=5.0):
    """(channel, info) for a host process wherever it is (where: see this module's docstring)."""
    via = (where or {}).get("via")
    if via == "local":
        return connect_local(None, timeout)
    if via == "unix":
        return connect_local(where["path"], timeout)
    if via == "ssh":
        return connect_ssh(where["dest"], where.get("command"), bool(where.get("strict")), max(timeout, 3.0))
    raise OSError(f"nowhere to reach: {where!r}")


def is_local(where):
    """Is it on this machine (reached by its socket, not over ssh)?"""
    return (where or {}).get("via") in ("local", "unix")


def said_where(where):
    """Where a host process is, as people say it: user@host, or this machine."""
    return where.get("dest") if (where or {}).get("via") == "ssh" else "this machine"


def bridge(sock, rfd=0, wfd=1):
    """What comes in on rfd to the socket, and what the socket says out on wfd, as it comes, till either end's done
    (`baude_headless.py connect`: ssh's stdin and stdout, and the host process's socket)."""
    sock.setblocking(True)
    open_in = True
    while True:
        ready, _, _ = select.select([sock] + ([rfd] if open_in else []), [], [])
        if sock in ready:
            data = sock.recv(65536)
            if not data:
                return
            while data:
                data = data[os.write(wfd, data):]
        if open_in and rfd in ready:
            data = os.read(rfd, 65536)
            if not data:  # (the other end's said all it will: the socket's told, and what's still to come comes)
                open_in = False
                try:
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    return
                continue
            sock.sendall(data)


def wait_for_socket(path, seconds):
    """Has a host process started listening at path within seconds?"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            ch, _info = connect_local(path, timeout=2)
            ch.close()
            return True
        except OSError:
            time.sleep(0.1)
    return False
