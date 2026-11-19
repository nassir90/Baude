"""hostfiles — what a head elsewhere reads of a session's folder here, read-only: its folders listed (list_dir), its
git history, a commit and its diff (git), and the names Signal's glossary has for it (signal_names). Each only inside
one of this host's sessions' folders (Server.in_sessions; git's log also of this host's own checkout, for a changelog),
and nothing written, fetched or looked up: a phone's Files, Git and Signal pages. No GTK here."""
import json
import os
import sqlite3
import subprocess
import threading

DIR_LIMIT = 2000      # the most entries a folder's listing has
LOG_LIMIT = 200       # the most commits a log has at once
DIFF_LIMIT = 400_000  # the most of a diff sent (its first part)
OUT_LIMIT = 4_000_000  # the most of any git command's output read (it's stopped past that)
FILES_LIMIT = 500     # the most of a commit's files listed
FIELDS = "%H%x1f%P%x1f%an%x1f%ae%x1f%at%x1f%D%x1f%s"


def _git(cwd, *args, timeout=20, limit=OUT_LIMIT):
    """git's output (its first limit bytes, read as it comes: it's stopped past that), as text whatever its bytes;
    (None, why) if it failed."""
    try:
        p = subprocess.Popen(["git", "-C", cwd, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except OSError as e:
        return None, str(e)
    timer = threading.Timer(timeout, p.kill)
    timer.start()
    try:
        out, cut = bytearray(), False
        while len(out) <= limit and (part := p.stdout.read(65536)):
            out += part
        if len(out) > limit:
            cut = True
            del out[limit:]
            p.kill()
        err = p.stderr.read(65536)
        code = p.wait()
    finally:
        timer.cancel()
        p.stdout.close()
        p.stderr.close()
    text = out.decode("utf-8", errors="replace")
    if code != 0 and not cut:
        return None, err.decode("utf-8", errors="replace").strip() or text.strip() or "git failed"
    return text, None


def _inside(server, path, checkout=False):
    """path (absolute) made real, if it's in one of the server's sessions' folders (or, checkout, its own code's)."""
    if not isinstance(path, str) or not os.path.isabs(os.path.expanduser(path)):
        raise ValueError("not an absolute path")
    real = os.path.realpath(os.path.expanduser(path))
    if server.in_sessions(real):
        return real
    if checkout:
        from baudelib import selfupdate
        here = os.path.realpath(selfupdate.HERE)
        if real == here:
            return real
    raise ValueError("not in one of its sessions' folders")


def list_dir(server, path):
    """list_dir {path}: a folder's entries, folders first then by name (.git left out), each {name, dir, size?,
    ignored} (ignored: git ignores it, as Baude dims it)."""
    real = _inside(server, path)
    try:
        with os.scandir(real) as it:
            entries = [e for e in it if e.name != ".git"]
    except OSError as e:
        return {"path": path, "entries": [], "error": e.strerror or str(e)}
    entries.sort(key=lambda e: (not e.is_dir(), e.name.casefold()))
    entries = entries[:DIR_LIMIT]
    ignored = set()
    try:
        r = subprocess.run(["git", "-C", real, "check-ignore", "--stdin", "-z"], capture_output=True, timeout=10,
                           input="\0".join(e.name for e in entries).encode())
        ignored = {n for n in r.stdout.decode(errors="replace").split("\0") if n}
    except (OSError, subprocess.TimeoutExpired):
        pass
    out = []
    for e in entries:
        try:
            d = e.is_dir()
            item = {"name": e.name, "dir": d, "ignored": e.name in ignored}
            if not d:
                item["size"] = e.stat().st_size
        except OSError:
            continue
        out.append(item)
    return {"path": path, "entries": out, "more": len(entries) == DIR_LIMIT}


def _commits(text):
    out = []
    for line in (text or "").splitlines():
        p = line.split("\x1f")
        if len(p) != 7:
            continue
        out.append({"sha": p[0], "parents": p[1].split(), "author": p[2], "email": p[3], "at": int(p[4] or 0),
                    "refs": [r.strip() for r in p[5].split(",") if r.strip()], "subject": p[6]})
    return out


def _ahead(cwd, a, b):
    out, _ = _git(cwd, "rev-list", "--left-right", "--count", f"{a}...{b}")
    try:
        ahead, behind = (int(x) for x in out.split())
        return {"ahead": ahead, "behind": behind}
    except (AttributeError, ValueError):
        return None


def git(server, msg):
    """git {cwd, what, ...}: log (rev?, limit?, skip?): the branch (its name, upstream, ahead and behind) and its
    commits, newest first; card (sha): a commit, its files (+ and −) and message; diff (sha, path?): its diff (its first
    DIFF_LIMIT). Read-only: nothing fetched."""
    what = msg.get("what")
    cwd = _inside(server, msg.get("cwd"), checkout=what == "log")
    top, err = _git(cwd, "rev-parse", "--show-toplevel")
    if top is None:
        return {"what": what, "error": "Not in a git repository."}
    if what == "log":
        rev = msg.get("rev") if isinstance(msg.get("rev"), str) and not msg["rev"].startswith("-") else "HEAD"
        limit = max(1, min(int(msg.get("limit") or 50), LOG_LIMIT))
        skip = max(0, int(msg.get("skip") or 0))
        text, err = _git(cwd, "log", f"--format={FIELDS}", "-n", str(limit), "--skip", str(skip), rev, "--")
        if text is None:
            return {"what": what, "error": err}
        name, _ = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
        upstream, _ = _git(cwd, "rev-parse", "--abbrev-ref", "@{upstream}")
        branch = {"name": (name or "").strip() or None, "upstream": (upstream or "").strip() or None}
        if branch["upstream"]:
            branch.update(_ahead(cwd, "HEAD", "@{upstream}") or {})
        return {"what": what, "top": top.strip(), "branch": branch, "commits": _commits(text), "skip": skip,
                "more": text.count("\n") >= limit}
    sha = msg.get("sha")
    if not isinstance(sha, str) or not sha or sha.startswith("-"):
        return {"what": what, "error": "a commit's sha, please"}
    if what == "card":
        head, err = _git(cwd, "show", "-s", "--format=%H%x1f%P%x1f%an%x1f%ae%x1f%at%x1f%cn%x1f%B", sha)
        if head is None:
            return {"what": what, "error": f"No commit {sha} here"}
        p = head.split("\x1f", 6)
        stat, _ = _git(cwd, "show", "--format=", "--numstat", sha)
        files = []
        for line in (stat or "").splitlines()[:FILES_LIMIT]:
            a, r, path = (line.split("\t", 2) + ["", "", ""])[:3]
            files.append([path, None if a == "-" else int(a or 0), None if r == "-" else int(r or 0)])
        on, _ = _git(cwd, "branch", "--contains", sha, "--format=%(refname:short)", limit=20_000)
        return {"what": what, "sha": p[0], "parents": p[1].split(), "author": p[2], "email": p[3], "at": int(p[4] or 0),
                "committer": p[5], "message": p[6].strip(), "files": files,
                "more_files": len((stat or "").splitlines()) > FILES_LIMIT,
                "branches": [b for b in (on or "").split() if b]}
    if what == "diff":
        path = msg.get("path") if isinstance(msg.get("path"), str) else None
        text, err = _git(cwd, "show", "--format=", sha, "--", *([path] if path else []), limit=DIFF_LIMIT + 1)
        if text is None:
            return {"what": what, "error": err}
        return {"what": what, "sha": sha, "path": path, "text": text[:DIFF_LIMIT], "truncated": len(text) > DIFF_LIMIT}
    return {"what": what, "error": "git takes log, card or diff"}


def signal_names(server, msg):
    """signal_names {cwd, session?}: the names Signal's glossary has for the folder (the chat's own mentions only,
    session given: its id there), read as they are: [{surface, kind, canonical, status, summary, locations}], and the
    glossary's path. Nothing's looked up or written: that's Signal's, where Baude runs it."""
    import lookup  # (no GTK in it)
    cwd = _inside(server, msg.get("cwd"))
    place = lookup.signal_place(cwd)
    db = os.path.join(place.store, "signal.db")
    if not os.path.exists(db):
        return {"glossary": None, "refs": []}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as e:
        return {"glossary": db, "refs": [], "error": str(e)}
    try:
        q = ("SELECT e.id, a.surface, e.kind, e.canonical, e.status, e.reason, e.data FROM alias a JOIN entity e "
             "ON e.id = a.entity_id WHERE a.checkout = ? AND a.scope = ?")
        args = [place.checkout, place.scope]
        sessions = [s for s in (msg.get("session"), msg.get("cli_id")) if isinstance(s, str) and s]
        if sessions:
            q += f" AND e.id IN (SELECT entity_id FROM mention WHERE session IN ({','.join('?' * len(sessions))}))"
            args += sessions
        rows = con.execute(q + " ORDER BY e.kind, a.surface LIMIT 2000", args).fetchall()
        refs = []
        for eid, surface, kind, canonical, status, reason, data in rows:
            locs = [dict(zip(("role", "path", "line", "note", "rev"), r)) for r in con.execute(
                "SELECT role, path, line, note, rev FROM location WHERE entity_id = ? LIMIT 20", (eid,))]
            ref = lookup.Ref(surface, kind, status or "pending", canonical, reason or "",
                             json.loads(data) if data else {}, locs)
            refs.append({"surface": surface, "kind": kind, "canonical": ref.canonical, "status": ref.status,
                         "summary": ref.summary(), "locations": locs})
    except (sqlite3.Error, ValueError) as e:
        return {"glossary": db, "refs": [], "error": str(e)}
    finally:
        con.close()
    return {"glossary": db, "refs": refs}
