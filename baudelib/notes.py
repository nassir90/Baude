"""Record's notebooks on this machine, as the host process gives them to another's Record (op "notes"): listed from
records.json, and changed through the Record running here, if one is (it keeps them, and saves over the file), else in
the file itself; and the edits proposed to their pages, which the host process keeps (edits.json: edit_op). No GTK: the
host process reads this, and so does record.py."""
import copy
import difflib
import hashlib
import json
import os
import socket
import sys
import threading
import time

from baudelib import notebooktools, wording

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


def _records_file():
    """records.json's path, its folder not made (the host process looks at it on every machine: stamp)."""
    return os.path.join(os.environ.get("RECORD_DATA") or own_data_dir("Record"), "records.json")


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


def digest(book):
    """A notebook as shared (shared's), in a few characters: the same while it is."""
    return hashlib.sha1(json.dumps(book, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def file_key():
    """records.json as written last (when, how big): another once it's written again; "" with none."""
    try:
        st = os.stat(_records_file())
    except OSError:
        return ""
    return f"{st.st_mtime_ns}:{st.st_size}"


def stamp():
    """The notebooks here as they are, in a word: another once one's changed (a save of Record's that changed only
    what it shows, its zoom, say, isn't one). The host process's info says it, so a Record elsewhere showing them
    lists them again: live."""
    try:
        with open(_records_file(), encoding="utf-8") as f:
            books = [shared(t) for t in (json.load(f) or {}).get("records") or [] if isinstance(t, dict) and t.get("id")]
    except (OSError, ValueError, AttributeError):
        return ""
    return digest({"books": books})


def listing(have=None):
    """{notebooks: [shared(…)], ids: every one's id, in order} (error: why not); have ({id: digest}: those the asker
    has), only those it hasn't as they are. records.json as it is: a Record running here writes its changes there
    within a moment (recordstore.Store.SAVE_MS)."""
    try:
        books = [shared(t) for t in _read().get("records") or [] if isinstance(t, dict) and t.get("id")]
    except (OSError, ValueError) as e:
        return {"notebooks": [], "error": f"records.json: {e}"}
    have = have if isinstance(have, dict) else {}
    sent = [b for b in books if have.get(b["id"]) != digest(b)]
    if len(json.dumps(sent)) > LIMIT:
        return {"notebooks": [], "error": wording.say("host_errors.notebooks_too_big")}
    return {"notebooks": sent, "ids": [b["id"] for b in books]}


def merge3(base, mine, theirs):
    """A page changed two ways from base: both changes kept where they don't touch (by line, then within the lines
    both changed, by character). Where both changed the same text, nothing either wrote is lost: the one that has the
    other in it (one side wrote on), else both, mine first."""
    if mine == theirs or theirs == base:
        return mine
    if mine == base:
        return theirs
    return _merge(base, mine, theirs, True)


def _hunks(a, b, side):
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return [(i1, i2, b[j1:j2], side) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def _both(mine, theirs, lines):
    """One stretch both sides changed, both kept: the one that has the other at its start or end, else mine then
    theirs (by line: each on lines of its own); by character, None: not merged so."""
    if theirs.startswith(mine) or theirs.endswith(mine):
        return theirs
    if mine.startswith(theirs) or mine.endswith(theirs):
        return mine
    if not lines:
        return None
    return mine + ("" if not mine or mine.endswith("\n") else "\n") + theirs


def _merge(base, mine, theirs, lines):
    """merge3 by line (lines), or by character: None where, by character, both changed the same text."""
    split = (lambda t: t.splitlines(keepends=True)) if lines else list
    b, m, t = split(base), split(mine), split(theirs)
    hunks = sorted(_hunks(b, m, 0) + _hunks(b, t, 1), key=lambda h: (h[0], h[1]))
    clusters = []
    for h in hunks:  # (those whose stretches of base meet, or are put in at one place: together)
        c = clusters[-1] if clusters else None
        if c is not None and (h[0] < c["end"] or h[0] == c["end"] and (h[0] == h[1] or c["start"] == c["end"])):
            c["hunks"].append(h)
            c["end"] = max(c["end"], h[1])
        else:
            clusters.append({"start": h[0], "end": h[1], "hunks": [h]})

    def side(c, which):
        out, at = [], c["start"]
        for s, e, rep, w in c["hunks"]:
            if w == which:
                out += b[at:s] + list(rep)
                at = e
        return "".join(out + b[at:c["end"]])
    out, at = [], 0
    for c in clusters:
        out.append("".join(b[at:c["start"]]))
        sides = {h[3] for h in c["hunks"]}
        if sides == {0}:
            out.append(side(c, 0))
        elif sides == {1}:
            out.append(side(c, 1))
        else:
            mine_c, theirs_c = side(c, 0), side(c, 1)
            if mine_c == theirs_c:
                out.append(mine_c)
            elif not lines:
                if (kept := _both(mine_c, theirs_c, False)) is None:
                    return None
                out.append(kept)
            elif c["start"] == c["end"]:  # (both put lines in at one place)
                out.append(_both(mine_c, theirs_c, True))
            else:
                by_char = _merge("".join(b[c["start"]:c["end"]]), mine_c, theirs_c, False)
                out.append(by_char if by_char is not None else _both(mine_c, theirs_c, True))
        at = c["end"]
    out.append("".join(b[at:]))
    return "".join(out)


def changes_ok(changes):
    """Are changes what apply takes: [{id, name?, pages?: {name: text or None}, new?, drop?}]?"""
    return isinstance(changes, list) and all(
        isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"]
        and (c.get("name") is None or isinstance(c["name"], str))
        and isinstance(c.get("pages") or {}, dict)
        and all(isinstance(k, str) and (v is None or isinstance(v, str)) for k, v in (c.get("pages") or {}).items())
        and isinstance(c.get("was") or {}, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in (c.get("was") or {}).items())
        for c in changes)


def apply(notebooks, changes):
    """changes made to notebooks (a list, changed in place): a new one put first; one dropped taken out; one's name
    and pages set (a page None: deleted). A page's was (what the sender had it as): one changed here too since,
    both changes kept (merge3); one deleted there and changed here, kept. A page the sender had none of (said by its
    was, without this page) and one here has: both kept. The ids changed."""
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
        was = c.get("was") or {}  # (what the sender had of each page)
        for name, text in (c.get("pages") or {}).items():
            here = pages.get(name)
            if text is None:
                if not (name in was and here is not None and here != was[name]):  # (changed here: kept)
                    pages.pop(name, None)
            elif here is not None and here != was.get(name, here if name in was else ""):
                pages[name] = merge3(was.get(name, ""), here, text)  # (what's here kept where it was changed)
            else:
                pages[name] = text
        done.append(c["id"])
    return done


def _hand_over(changes):
    """The changes given to the Record running here, which applies them (record.App's notes): True if it did, False if
    it said otherwise, None if none's running."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.settimeout(5)
            c.connect(socket_path())
            c.sendall(json.dumps({"notes": changes}).encode() + b"\n")
            said = c.recv(16)
    except OSError:
        return None
    return said == b"notes\n"


BACKUP_EVERY, BACKUPS = 600, 50  # (as recordstore.Store's: a copy at most every 10 minutes, the last 50 kept)


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


def _save(saved, path):
    """saved written to records.json (path), a copy of it kept first in backups/: an error, or None."""
    backup(path)
    try:
        saved.setdefault("notes", "markdown")
        with open(path + ".host.tmp", "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(path + ".host.tmp", path)
    except OSError as e:
        return f"records.json: {e}"
    return None


def moved_ok(moved):
    """Is moved what write takes: {notebook id: {a page's old name: its new}}?"""
    return moved is None or isinstance(moved, dict) and all(
        isinstance(i, str) and isinstance(m, dict) and all(isinstance(k, str) and isinstance(v, str)
                                                           for k, v in m.items()) for i, m in moved.items())


def write(changes, moved=None):
    """changes made to this machine's notebooks: by the Record running here, else in records.json (a copy of it kept
    first in backups/, as Record keeps one); moved ({notebook id: {old page name: new}}): the edits waiting on those
    pages moved with them (edits.json). {done: [ids]} or {error}."""
    if not changes_ok(changes) or not moved_ok(moved):
        return {"error": "not changes to notebooks"}
    out = _write(changes)
    if moved and "error" not in out and (error := _edits_moved(moved)) is not None:
        return {"error": error}
    return out


def _write(changes):
    if not changes:
        return {"done": []}
    handed = _hand_over(changes)
    if handed is not None:
        return {"done": [c["id"] for c in changes], "by": "record"} if handed else \
            {"error": wording.say("host_errors.changes_not_taken")}
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
        if (error := _save(saved, path)) is not None:
            return {"error": error}
    return {"done": done, "by": "file"}


def edits_path():
    """The edits proposed to the notebooks here (notebooktools.Edits): the host process's to write, and no one else's."""
    return os.path.join(data_dir(), "edits.json")


def edits_stamp():
    """edits.json as written last, in a word (info's edits: a Record showing the notebooks here asks for them again
    once it's another)."""
    try:
        st = os.stat(os.path.join(os.environ.get("RECORD_DATA") or own_data_dir("Record"), "edits.json"))
    except OSError:
        return ""
    return f"{st.st_mtime_ns}:{st.st_size}"


def _edits_read(saved):
    """edits.json's edits; the first time, those waiting in records.json (saved) from before, written there."""
    try:
        with open(edits_path(), encoding="utf-8") as f:
            got = json.load(f)
        return [e for e in (got.get("edits") if isinstance(got, dict) else None) or () if isinstance(e, dict)]
    except FileNotFoundError:
        edits = notebooktools.imported(saved)
        _edits_write(edits)
        return edits


def _edits_write(edits):
    path = edits_path()
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"edits": edits}, f, indent=1, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(path + ".tmp", path)


def _edits_moved(moved):
    """The edits waiting on pages moved ({notebook id: {old name: new}}) moved with them: an error, or None."""
    with _LOCK:
        try:
            edits = _edits_read(_read())
        except (OSError, ValueError) as e:
            return f"Record's store here: {e}"
        changed = False
        for e in edits:
            to = (moved.get(e.get("notebook")) or {}).get(e.get("page"))
            if e.get("status") == "pending" and to is not None and to != e["page"]:
                e["page"], changed = to, True
        if changed:
            try:
                _edits_write(edits)
            except OSError as e:
                return f"edits.json: {e}"
    return None


EDIT_OPS = {"edit_propose": "propose", "edit_revise": "revise", "edit_withdraw": "withdraw", "edit_decide": "decide",
            "edit_adopt": "adopt", "edits": "listed"}


def edit_op(msg):
    """One of the edit ops (EDIT_OPS) on the notebooks here, as records.json has them: (its answer's op, its fields).
    Run with the notes ops, in the order asked (Server._notes): a page written just before is read as written."""
    with _LOCK:
        try:
            saved = _read()
            edits = _edits_read(saved)
        except (OSError, ValueError) as e:
            return "error", {"error": f"Record's store here: {e}"}
        books = [t for t in saved.get("records") or () if isinstance(t, dict) and t.get("id")]
        tools = notebooktools.Edits(edits, books)
        try:
            out = getattr(tools, EDIT_OPS[msg["op"]])(msg)
        except ValueError as e:
            return "error", {"error": str(e)}
        if tools.changed:
            try:
                _edits_write(edits)
            except OSError as e:
                return "error", {"error": f"edits.json: {e}"}
    return ("edits", {**out, "stamp": edits_stamp()}) if msg["op"] in ("edits", "edit_adopt") else ("edit", out)
