"""Emission's profiles and its door, without GTK: what emission.py keeps, and what a host process uses to reach the
Emission that has a profile (a Bot's, started for it if none has), as a Bot's browser tools ask (emissiontools).

A profile is open in one Emission process at a time (its lock); that process listens at the profile's door, a socket
only this user can open, for requests a line of JSON each, its answer a line of JSON."""
import fcntl
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

PROFILE_SPACE = uuid.UUID("6b1f0d3e-3c4e-4f51-9b0e-7a1e5e6d2c11")  # (a profile named before ids: its name's uuid)
DEFAULT = "Default"
EMISSION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "emission.py")
START_S = 30  # (a Bot's Emission started: its door waited for this long)


def data_home():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "emission")


# ------------------------------------------------------------------ profiles --

def _read_profiles():
    try:
        with open(os.path.join(data_home(), "profiles.json"), encoding="utf-8") as f:
            raw = json.load(f).get("profiles") or []
    except (OSError, ValueError, AttributeError):
        raw = []
    out = []
    for p in raw:
        if isinstance(p, str):
            p = {"name": p, "id": str(uuid.uuid5(PROFILE_SPACE, p))}
        if not (isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"].strip() and p["name"] != DEFAULT):
            continue
        try:
            pid = str(uuid.UUID(str(p.get("id"))))
        except ValueError:
            continue  # (not an id: a store's named by one, and on Linux it's a folder's name)
        out.append({"name": p["name"].strip(), "id": pid, **({"bot": p["bot"]} if isinstance(p.get("bot"), str) else {})})
    return out


def _write_profiles(items):
    os.makedirs(data_home(), exist_ok=True)
    path = os.path.join(data_home(), "profiles.json")
    tmp = f"{path}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"profiles": items}, f, indent=1)
    os.replace(tmp, path)


class _Changing:
    """profiles.json changed by one process at a time (a read, then a write: another's not lost between)."""

    def __enter__(self):
        os.makedirs(data_home(), exist_ok=True)
        self.fd = os.open(os.path.join(data_home(), "profiles.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)

    def __exit__(self, *_):
        os.close(self.fd)


changing = _Changing


def profiles():
    """The profiles there are, "Default" first: [{name, id, bot?}], each one's cookies and logins its own (kept by its
    id, so a profile renamed keeps them; bot: the chat id of the Bot it's for)."""
    return [{"name": DEFAULT, "id": str(uuid.uuid5(PROFILE_SPACE, DEFAULT))}, *_read_profiles()]


def profile_id(name):
    return next((p["id"] for p in profiles() if p["name"] == name), None)


def add_profile(name):
    with changing():
        if profile_id(name) is None:
            _write_profiles([*_read_profiles(), {"name": name, "id": str(uuid.uuid4())}])
        return profile_id(name)


def bot_profile_id(bot_id):
    """A Bot's own profile's id on any host (its name, or whether it's been made yet, aside)."""
    return str(uuid.uuid5(PROFILE_SPACE, "bot:" + bot_id))


def bot_profile(bot_id, name):
    """The id of a Bot's own profile on this host (made if it has none; named after the Bot, again if it's renamed;
    name None: as it's named now)."""
    pid = bot_profile_id(bot_id)
    with changing():
        return _bot_profile(pid, bot_id, name)


def _bot_profile(pid, bot_id, name):
    items = _read_profiles()
    mine = next((p for p in items if p["id"] == pid), None)
    if name is None and mine is not None:
        return pid
    label = ' '.join(str(name or 'Bot').split())[:60]
    if mine is not None and (mine["name"] == label or mine["name"].startswith(label + " ") and
                             mine["name"][len(label) + 1:].isdigit()):
        return pid  # (its name, or its name and a number, as one taken made it: kept, not written again)
    taken = {p["name"] for p in items if p["id"] != pid} | {DEFAULT}
    final, n = label, 2
    while final in taken:
        final, n = f"{label} {n}", n + 1
    items = [p for p in items if p["id"] != pid] + [{"name": final, "id": pid, "bot": bot_id}]
    _write_profiles(items)
    if mine is None and pid in leftovers():  # (removed once, made again: its new data kept)
        note_leftover(pid, add=False)
    return pid


def rename_profile(old, new):
    with changing():
        _write_profiles([{**p, "name": new} if p["name"] == old else p for p in _read_profiles()])


def forget_profile(name):
    with changing():
        _write_profiles([p for p in _read_profiles() if p["name"] != name])


def leftovers():
    path = os.path.join(data_home(), "removed.json")
    try:
        with open(path, encoding="utf-8") as f:
            return [str(uuid.UUID(str(i))) for i in json.load(f)]
    except (OSError, ValueError, TypeError):
        return []


def note_leftover(pid, add=True):
    left = [i for i in leftovers() if i != pid] + ([pid] if add else [])
    os.makedirs(data_home(), exist_ok=True)
    with open(os.path.join(data_home(), "removed.json"), "w", encoding="utf-8") as f:
        json.dump(left, f)


def clear_leftovers():
    """Removed profiles' folders deleted again (none of their pages open now: each one's lock had first)."""
    for pid in leftovers():
        if pid in HELD or not lock_profile(pid):
            continue
        shutil.rmtree(os.path.join(data_home(), "profiles", pid), ignore_errors=True)
        if not os.path.exists(os.path.join(data_home(), "profiles", pid)):
            note_leftover(pid, add=False)
        try:
            os.unlink(os.path.join(data_home(), "profiles", pid + ".lock"))
        except OSError:
            pass
        unlock_profile(pid)


HELD = {}  # (this process's profile locks, by id)


def lock_profile(pid):
    """True if this process has the profile's lock (now, or already), False if another Emission has it: two processes
    on one store would share its cookie database and caches. Kept until the process ends (its store may still be
    open after its pages close: WebKit lets go of it later), or the profile's removed."""
    if pid in HELD:
        return True
    folder = os.path.join(data_home(), "profiles")
    os.makedirs(folder, exist_ok=True)
    fd = os.open(os.path.join(folder, pid + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    HELD[pid] = fd
    return True


def unlock_profile(pid):
    fd = HELD.pop(pid, None)
    if fd is not None:
        os.close(fd)


# ------------------------------------------------------------------- the door --

def door_path(pid):
    """Where the Emission that has pid's profile takes requests (a socket in a folder only this user can open; short,
    for macOS's limit on socket paths)."""
    from baudelib import wire
    name = hashlib.sha1(f"{data_home()}\0{pid}".encode()).hexdigest()[:20] + ".sock"
    base = os.path.abspath(os.path.join(wire.runtime_dir(), "emission"))  # (the host process's folder: found the
    if len(os.path.join(base, name).encode()) > 100:  # same from ssh as at the screen, unlike $TMPDIR)
        base = os.path.join(tempfile.gettempdir() if sys.platform != "darwin" else "/tmp", f"emission-{os.getuid()}")
    else:
        wire.own_dir(wire.runtime_dir())
    return os.path.join(wire.own_dir(base), name)


def call(pid, msg, timeout=60):
    """msg (a request: {op, ...}) to the Emission that has pid's profile: its answer. OSError if none's there (or it
    went), ValueError for what it said went wrong."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(door_path(pid))
        s.sendall((json.dumps(msg) + "\n").encode())
        raw = b""
        while not raw.endswith(b"\n"):
            part = s.recv(1 << 16)
            if not part:
                break
            raw += part
    if not raw:
        raise OSError("Emission went before it answered")
    got = json.loads(raw)
    if not isinstance(got, dict):
        raise ValueError("Emission's answer wasn't one")
    if got.get("error"):
        raise ValueError(got["error"])
    return got


def hand_over(pid, urls):
    """urls opened in the Emission that has pid's profile: True if it took them."""
    try:
        return bool(call(pid, {"op": "open", "urls": list(urls)}, timeout=10).get("ok"))
    except (OSError, ValueError):
        return False


def call_bot(bot_id, name, msg, timeout=60):
    """msg to the Emission that has bot_id's own profile here, started for it (out of sight: no window shown, its own
    display on Linux) if none has it."""
    pid = bot_profile(bot_id, name)
    try:
        return call(pid, msg, timeout)
    except (FileNotFoundError, ConnectionRefusedError):
        pass
    os.makedirs(data_home(), exist_ok=True)
    log_path = os.path.join(data_home(), f"agent-{pid[:8]}.log")

    def start():
        with open(log_path, "ab") as log:
            env = dict(os.environ)
            if env.get("BAUDE_RUNTIME_DIR"):  # (its folders as this process has them: it starts in another)
                env["BAUDE_RUNTIME_DIR"] = os.path.abspath(env["BAUDE_RUNTIME_DIR"])
            return subprocess.Popen([sys.executable, EMISSION, "--agent", "--bot", bot_id,
                                     *(["--name", str(name)] if name else [])],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                                    cwd=os.path.dirname(EMISSION), env=env)
    proc = start()
    end = time.monotonic() + START_S
    while True:
        try:
            return call(pid, msg, timeout)
        except (FileNotFoundError, ConnectionRefusedError):
            if proc.poll() == 0:  # (it found the profile someone's, with no door yet: tried again a moment on)
                time.sleep(0.5)
                proc = start()
                continue
            if proc.poll() not in (None, 0):  # (gone, and not because another has the profile: it said why)
                try:
                    with open(log_path, encoding="utf-8", errors="replace") as f:
                        said = [line.strip() for line in f.readlines()[-6:] if line.strip()]
                except OSError:
                    said = []
                raise OSError("Emission couldn't start for this Bot here" + (": " + said[-1] if said else ""))
            if time.monotonic() > end:
                raise OSError(f"Emission didn't start for this Bot (its log: {data_home()}/agent-{pid[:8]}.log)")
            time.sleep(0.3)
