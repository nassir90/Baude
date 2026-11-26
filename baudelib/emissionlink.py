"""Emission's profiles and their sockets, without GTK: what emission.py keeps, and what a host process uses to reach the
Emission that has a profile (a Bot's, started for it if none has), as a Bot's browser tools ask (emissiontools).

A profile is open in one Emission process at a time (its lock); that process listens on the profile's Unix socket, one
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
START_S = 30  # (a Bot's Emission started: its socket waited for this long)


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
    taken = {p["name"] for p in items if p["id"] != pid} | {DEFAULT}
    if mine is not None and (mine["name"] == label or label in taken and mine["name"].startswith(label + " ") and
                             mine["name"][len(label) + 1:].isdigit() and mine["name"] not in taken):
        return pid  # (its name, or, that taken, its name and a number: kept, not written again)
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


def open_pages(pid, urls=None, why=None, selected=0):
    """A profile's browser's tabs, written down as they change (urls, the one in front selected; why: it's closing,
    "idle" when it went unused, "moved" when Baude took it), so the next browser started on that profile opens them
    again, as a browser continues where it left off; or (no urls) what the last wrote: (urls, selected, why). urls []:
    none to open again (its last tab closed)."""
    path = os.path.join(data_home(), "profiles", pid + ".open")
    if urls is None:
        try:
            with open(path, encoding="utf-8") as f:
                had = json.load(f)
            had = had if isinstance(had, dict) else {"urls": had}
            urls = [u for u in had.get("urls") or [] if isinstance(u, str)]
            at = had.get("selected")
            return urls, at if isinstance(at, int) and 0 <= at < len(urls) else 0, had.get("why")
        except (OSError, ValueError, AttributeError):
            return [], 0, None
    try:
        if urls:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".new", "w", encoding="utf-8") as f:
                json.dump({"urls": urls, "selected": selected, "why": why}, f)
            os.replace(path + ".new", path)
        elif os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass
    return urls


def pending_note(pid, note=None, slot="note"):
    """What the Bot's to be told about its browser, kept till it is (a browser that ends first hands it on to the
    next): note "" once it's told; note None, what's waiting read. slot: which of the things it's to be told (its
    own: "reopen", that its pages were opened again, put in place of the last, not added to the others)."""
    path = os.path.join(data_home(), "profiles", pid + "." + slot)
    try:
        if note is None:
            with open(path, encoding="utf-8") as f:
                return f.read().strip() or None
        if note:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(note)
        elif os.path.exists(path):
            os.unlink(path)
    except OSError:
        return None
    return None


def phone_note(pid, note):
    """pending_note's, about a Bot's browser on a phone: put in place of the last one written (kept beside it, to be
    found again), or ("") taken out."""
    path = os.path.join(data_home(), "profiles", pid + ".phone")
    try:
        with open(path, encoding="utf-8") as f:
            last = f.read()
    except OSError:
        last = ""
    before = pending_note(pid) or ""
    now = before.replace(last, note) if last and last in before else " ".join(n for n in (before, note) if n)
    pending_note(pid, " ".join(now.split()))
    try:
        if note:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(note)
        elif os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass


# ----------------------------------------------------------------- the socket --

def profile_socket(pid):
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
        s.connect(profile_socket(pid))
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


def running(timeout=2):
    """What each Emission running here says it is (its info: the commit its code is, its process's pid, ...), one for
    each profile it has open. Baude's own socket (home: it makes Bots' browsers) is Baude's, not an Emission's."""
    folder, home = os.path.dirname(profile_socket("home")), os.path.basename(profile_socket("home"))
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".sock") and n != home)
    except OSError:
        return []
    out = []
    for name in names:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(os.path.join(folder, name))
                s.sendall(b'{"op": "info"}\n')
                raw = b""
                while not raw.endswith(b"\n") and len(raw) < 1 << 16:
                    if not (part := s.recv(1 << 12)):
                        break
                    raw += part
            got = json.loads(raw)
        except (OSError, ValueError):
            continue  # (one gone, its socket left behind)
        if isinstance(got, dict) and not got.get("error"):
            out.append(got)
    return out


def hand_over(pid, urls):
    """urls opened in the Emission that has pid's profile: True if it took them."""
    try:
        return bool(call(pid, {"op": "open", "urls": list(urls)}, timeout=10).get("ok"))
    except (OSError, ValueError):
        return False


def usable_display():
    """Whether a window started here is seen: macOS in a session at its screen (not a process ssh started, which has
    none), or a display of someone's on Linux (not one of Emission's own, virtual)."""
    if os.environ.get("EMISSION_VIRTUAL_DISPLAY"):
        return False
    if sys.platform != "darwin":
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    import ctypes
    try:
        cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        session = cg.CGSessionCopyCurrentDictionary()
        if not session:
            return False
        cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease(session)
        return True
    except (OSError, AttributeError):
        return False


STARTED = {"at": -1e9}  # (when open_window last started an Emission: another not started while that one opens)
START_S = 20


def open_window(show=True):
    """Emission's window here on its Default profile (☰ → Browser): the Emission that has it brought to the front
    (show), else one started, on this machine's screen; OSError (why, to be said) where there's no screen to show it
    on, or the profile is a Bot's browser's here (hosted in Baude, or out of sight), which isn't opened again. Its
    answer's fields: started, whether it was."""
    from baudelib import wording
    pid = profile_id(DEFAULT)
    try:
        info = call(pid, {"op": "info"}, timeout=5)
    except (OSError, ValueError):
        info = None
    if info is not None:
        if info.get("hosted") or info.get("agent"):
            raise OSError(wording.say("host_errors.browser_default_bots"))
        if show:
            try:
                call(pid, {"op": "show"}, timeout=5)
            except ValueError as e:
                raise OSError(str(e))
        return {"started": False}
    if not usable_display():
        raise OSError(wording.say("host_errors.browser_no_display"))
    if time.monotonic() - STARTED["at"] < START_S:  # (started a moment ago, not open yet: that one's coming)
        return {"started": True}
    STARTED["at"] = time.monotonic()
    os.makedirs(data_home(), exist_ok=True)
    env = dict(os.environ)
    if env.get("BAUDE_RUNTIME_DIR"):  # (its folders as this process has them: it starts in another)
        env["BAUDE_RUNTIME_DIR"] = os.path.abspath(env["BAUDE_RUNTIME_DIR"])
    with open(os.path.join(data_home(), "window.log"), "ab") as log:
        subprocess.Popen([sys.executable, EMISSION], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, cwd=os.path.dirname(EMISSION), env=env)
    return {"started": True}


def default_window(wait_s):
    """The socket of Emission's window here on its Default profile, for it to be watched from elsewhere (☰ → Browser
    on another host): the one that has it, else one started (open_window's, not brought to the front), waited for
    till wait_s has passed."""
    pid = profile_id(DEFAULT)
    open_window(show=False)
    end = time.monotonic() + wait_s
    while True:
        try:
            info = call(pid, {"op": "info"}, timeout=5)
            break
        except (OSError, ValueError) as e:
            if time.monotonic() >= end:
                raise OSError(str(e) or "Emission didn't open")
            time.sleep(0.5)
    if info.get("hosted") or info.get("agent"):
        from baudelib import wording
        raise OSError(wording.say("host_errors.browser_default_bots"))
    return profile_socket(pid)


def call_bot(bot_id, name, msg, timeout=60):
    """msg to the Emission that has bot_id's own profile here, started for it (out of sight: no window shown, its own
    display on Linux) if none has it."""
    pid = bot_profile(bot_id, name)
    try:
        return call(pid, msg, timeout)
    except (FileNotFoundError, ConnectionRefusedError):
        pass
    try:  # (Baude running here: the browser made in it, its pages shown in its Browser page as they are)
        call("home", {"op": "host", "bot": bot_id, "name": name}, timeout=START_S)
    except (OSError, ValueError):
        pass
    else:
        return call(pid, msg, timeout)  # (its own errors its own: not a reason to start another)
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
            if proc.poll() == 0:  # (it found the profile someone's, with no socket yet: tried again a moment on)
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
