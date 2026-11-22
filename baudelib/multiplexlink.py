"""Multiplex's terminals on this machine, without GTK: where each process that has some (Multiplex's window, Baude's
and Record's Kubernetes view) takes requests, and a Bot's terminal tools' requests (multiplextools, through the host
process: wire's multiplex op) taken to the one that has the terminal asked about, or to Multiplex's window (started if
it isn't running) for a new one.

Each such process listens on a Unix socket of its own (socket_path: its pid's, in a folder only this user can open),
for requests a line of JSON each, its answer a line of JSON. A terminal's id is its process's pid and its number
there ("4711.2"), so a request finds its process by its id alone."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

MULTIPLEX = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "multiplex.py")
START_S = 20  # (Multiplex's window started for a Bot's open: this long to be listening)
OPS = ("list", "open", "read", "type", "key", "wait", "close")  # (what a Bot's tools ask: nothing else is passed on)
WAIT_MOST_S = 120  # (a wait's longest)


def folder():
    """Where the processes' sockets are: beside the host process's own (the same from ssh as at the screen), or one
    in /tmp of this user's own where that's too long a path for a socket."""
    from baudelib import wire
    base = os.path.abspath(os.path.join(wire.runtime_dir(), "multiplex"))
    if len(os.path.join(base, "4194304.sock").encode()) > 100:
        base = os.path.join(tempfile.gettempdir() if sys.platform != "darwin" else "/tmp", f"multiplex-{os.getuid()}")
    else:
        wire.own_dir(wire.runtime_dir())
    return wire.own_dir(base)


def socket_path(pid=None):
    return os.path.join(folder(), f"{pid or os.getpid()}.sock")


def ask(path, msg, timeout=30):
    """msg to the process listening at path: its answer. OSError if none's there, ValueError for what it said went
    wrong."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall((json.dumps(msg) + "\n").encode())
        raw = b""
        while not raw.endswith(b"\n"):
            part = s.recv(1 << 16)
            if not part:
                break
            raw += part
    if not raw:
        raise OSError("the terminal's process went before it answered")
    got = json.loads(raw)
    if not isinstance(got, dict):
        raise ValueError("its answer wasn't one")
    if got.get("error"):
        raise ValueError(got["error"])
    return got


def processes(timeout=3):
    """[(path, its info: {pid, app, opens})] for each process listening here; a socket whose process is gone is
    removed."""
    out = []
    try:
        names = sorted(n for n in os.listdir(folder()) if n.endswith(".sock"))
    except OSError:
        return out
    for name in names:
        path = os.path.join(folder(), name)
        try:
            out.append((path, ask(path, {"op": "info"}, timeout)))
        except (ConnectionRefusedError, FileNotFoundError):
            try:
                os.unlink(path)
            except OSError:
                pass
        except (OSError, ValueError):
            continue
    return out


def terminal_path(tid):
    """The socket of the process that has terminal tid ("pid.n")."""
    pid = str(tid).split(".")[0] if isinstance(tid, str) else ""
    if not pid.isdigit():
        raise ValueError(f"no terminal {tid!r}: an id is list_terminals'")
    return socket_path(pid)


def call(msg, bot=None):
    """A Bot's tool's request (msg: {op, ...}) done here: list from every process, open in Multiplex's window, the
    rest by the process with the terminal. bot ({id, name}): who's asking, said to the terminal (its glow)."""
    op = msg.get("op")
    if op not in OPS:
        raise ValueError(f"multiplex takes {', '.join(OPS)}, not {op!r}")
    msg = {**msg, **({"bot": bot} if bot else {})}
    if op == "list":
        terminals = []
        for path, _info in processes():
            try:
                terminals += ask(path, msg).get("terminals", [])
            except (OSError, ValueError):
                continue
        return {"terminals": terminals}
    if op == "open":
        return opened(msg)
    if not msg.get("terminal"):
        raise ValueError(f"{op} needs a terminal (list_terminals' id)")
    timeout = 15 + (min(float(msg.get("timeout") or 30), WAIT_MOST_S) if op in ("wait", "type", "key") else 0)
    try:
        return ask(terminal_path(msg["terminal"]), msg, timeout)
    except (FileNotFoundError, ConnectionRefusedError):
        raise ValueError(f"no terminal {msg['terminal']}: its process has gone (list_terminals: the ones there are)")


def opened(msg):
    """A new terminal in Multiplex's window here, started (and shown) if none's running: what it said."""
    for path, info in processes():
        if info.get("opens"):
            return ask(path, msg, 30)
    log_path = os.path.join(folder(), "start.log")
    env = dict(os.environ)
    if env.get("BAUDE_RUNTIME_DIR"):  # (its folders as this process has them: it starts in another)
        env["BAUDE_RUNTIME_DIR"] = os.path.abspath(env["BAUDE_RUNTIME_DIR"])
    with open(log_path, "ab") as log:
        proc = subprocess.Popen([sys.executable, MULTIPLEX, "--empty"], stdin=subprocess.DEVNULL, stdout=log,
                                stderr=log, start_new_session=True, cwd=os.path.dirname(MULTIPLEX), env=env)
    end = time.monotonic() + START_S
    while time.monotonic() < end:
        try:
            return ask(socket_path(proc.pid), msg, 30)
        except (FileNotFoundError, ConnectionRefusedError):
            if proc.poll() is not None:
                try:
                    with open(log_path, encoding="utf-8", errors="replace") as f:
                        said = [line.strip() for line in f.readlines()[-6:] if line.strip()]
                except OSError:
                    said = []
                raise OSError("Multiplex couldn't start here" + (": " + said[-1] if said else ""))
            time.sleep(0.2)
    raise OSError(f"Multiplex didn't start (its log: {log_path})")


def hand_over(argv, cwd, title):
    """A new tab for argv in cwd in Multiplex's window already running here: True if one took it."""
    for path, info in processes():
        if info.get("opens"):
            try:
                ask(path, {"op": "open", "argv": argv, "cwd": cwd, "title": title, "show": True}, 10)
                return True
            except (OSError, ValueError):
                continue
    return False
