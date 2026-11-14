"""Record's notebooks on this machine, as the host process gives them to another's Record (op "notes"): listed from
records.json, and changed through the Record running here, if one is (it keeps them, and saves over the file), else in
the file itself. No GTK: the host process reads this, and so does record.py."""
import copy
import json
import os
import socket
import sys
import threading
import time

LIMIT = 8 * 1024 * 1024  # (a listing's text, at most: past it, the pages' text isn't sent, said as too big)
_LOCK = threading.Lock()  # (one write to the file at a time, here)


def own_data_dir(app):
    """Where an app of ours keeps its data: ~/Library/Application Support/<app> on macOS, else
    $XDG_DATA_HOME/<app, lower case>."""
    if sys.platform == "darwin":
        return os.path.expanduser(f"~/Library/Application Support/{app}")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), app.lower())


def data_dir():
    d = os.environ.get("RECORD_DATA") or own_data_dir("Record")
    os.makedirs(d, exist_ok=True)
    return d


def records_path():
    return os.path.join(data_dir(), "records.json")


def socket_path():
    """The Record running here's socket (record.listen)."""
    return os.path.join(data_dir(), "record.sock")


def shared(notebook):
    """What another machine's Record is given of a notebook: its id, name and pages (its folders are this machine's,
    its conversation and Meta's marks stay here)."""
    return {"id": notebook.get("id"), "name": notebook.get("name") or "",
            "pages": {str(k): str(v) for k, v in (notebook.get("pages") or {}).items()}}


def _read():
    try:
        with open(records_path(), encoding="utf-8") as f:
            saved = json.load(f)
    except FileNotFoundError:
        return {}
    return saved if isinstance(saved, dict) else {}


def listing():
    """{notebooks: [shared(…)]} (error: why not). records.json as it is: a Record running here writes its changes
    there within a moment (record.Store.SAVE_MS)."""
    try:
        books = [shared(t) for t in _read().get("records") or [] if isinstance(t, dict) and t.get("id")]
    except (OSError, ValueError) as e:
        return {"notebooks": [], "error": f"records.json: {e}"}
    if len(json.dumps(books)) > LIMIT:
        return {"notebooks": [], "error": "its notebooks are too big to send"}
    return {"notebooks": books}


def changes_ok(changes):
    """Are changes what apply takes: [{id, name?, pages?: {name: text or None}, new?, drop?}]?"""
    return isinstance(changes, list) and all(
        isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"]
        and (c.get("name") is None or isinstance(c["name"], str))
        and isinstance(c.get("pages") or {}, dict)
        and all(isinstance(k, str) and (v is None or isinstance(v, str)) for k, v in (c.get("pages") or {}).items())
        for c in changes)


def apply(notebooks, changes):
    """changes made to notebooks (a list, changed in place): a new one put first; one dropped taken out; one's name
    and pages set (a page None: deleted). The ids changed."""
    by_id = {t.get("id"): t for t in notebooks}
    done = []
    for c in changes:
        t = by_id.get(c["id"])
        if c.get("drop"):
            if t is not None:
                notebooks.remove(t)
                del by_id[c["id"]]
                done.append(c["id"])
            continue
        if t is None:
            if not c.get("new"):
                continue  # (gone here meanwhile: not made again by an edit)
            t = {"id": c["id"], "name": c.get("name") or "", "created": time.time(), "pages": {},
                 "folders": [], "messages": []}
            notebooks.insert(0, t)
            by_id[c["id"]] = t
        if c.get("name") is not None:
            t["name"] = c["name"]
        pages = t.setdefault("pages", {})
        for name, text in (c.get("pages") or {}).items():
            if text is None:
                pages.pop(name, None)
            else:
                pages[name] = text
        done.append(c["id"])
    return done


def _hand_over(changes):
    """The changes given to the Record running here, which applies them (record.App's notes): "notes" if it did, None
    if none's running, "old" if it's one from before it took them."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.settimeout(5)
            c.connect(socket_path())
            c.sendall(json.dumps({"notes": changes}).encode() + b"\n")
            said = c.recv(16)
    except OSError:
        return None
    return "notes" if said == b"notes\n" else "old"


BACKUP_EVERY, BACKUPS = 600, 50  # (as record.Store's: a copy at most every 10 minutes, the last 50 kept)


def backup(path):
    """records.json copied to backups/records-<time>.json first, as Record's Store.backup does it."""
    if not os.path.exists(path):
        return
    folder = os.path.join(os.path.dirname(path), "backups")
    try:
        os.makedirs(folder, exist_ok=True)
        kept = sorted(n for n in os.listdir(folder) if n.startswith("records-") and n.endswith(".json"))
        if kept and time.time() - os.path.getmtime(os.path.join(folder, kept[-1])) < BACKUP_EVERY:
            return
        with open(path, "rb") as f, open(os.path.join(folder, time.strftime("records-%Y%m%d-%H%M%S.json")), "wb") as o:
            o.write(f.read())
        for old in kept[:max(0, len(kept) + 1 - BACKUPS)]:
            os.remove(os.path.join(folder, old))
    except OSError as e:
        print("notes: no backup of records.json:", e, file=sys.stderr)


def write(changes):
    """changes made to this machine's notebooks: by the Record running here, else in records.json (a copy of it kept
    first in backups/, as Record keeps one). {done: [ids]} or {error}."""
    if not changes_ok(changes):
        return {"error": "not changes to notebooks"}
    if not changes:
        return {"done": []}
    handed = _hand_over(changes)
    if handed == "notes":
        return {"done": [c["id"] for c in changes], "by": "record"}
    if handed == "old":
        return {"error": "the Record running there is older and can't take changes: restart it"}
    with _LOCK:
        path = records_path()
        try:
            saved = _read()
        except (OSError, ValueError) as e:
            return {"error": f"records.json: {e}"}
        books = saved.get("records")
        if not isinstance(books, list):
            books = saved["records"] = []
        before = copy.deepcopy(books)
        done = apply(books, changes)
        if books == before:
            return {"done": done}
        backup(path)
        try:
            saved.setdefault("notes", "markdown")
            with open(path + ".host.tmp", "w", encoding="utf-8") as f:
                json.dump(saved, f, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(path + ".host.tmp", path)
        except OSError as e:
            return {"error": f"records.json: {e}"}
    return {"done": done, "by": "file"}
