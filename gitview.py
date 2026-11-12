"""The Git page (GitPage: the folder's commits, lanes and a commit's changes; the chat's own commits when its host has a chat), with what only it needs. Baude and Record both use it."""
import argparse
import array
import base64
import glob
import hashlib
import html
import itertools
import json
import math
import os
import re
import shutil
import socket
import struct
import weakref
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import queue
import urllib.parse

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402,F401

import lookup  # noqa: E402,F401
from lookup import (  # noqa: E402,F401  (⌘K's engine: what a folder has)
    _git,
)
from widgets import (  # noqa: E402,F401
    RefreshButton, WorkerPool, discard, full_time, keep_times, on_label_link, pretty_path, ref_epoch, ref_pr_info,
    say_time, sel_label, themed_icon, toast,
)
from widgets import empty_label  # noqa: E402
import pointers  # noqa: E402


def _gh_json(*args, timeout=20):
    """A `gh` call's JSON answer (GET only), or None if gh is missing, offline or says no."""
    if not shutil.which("gh"):
        return None
    try:
        p = subprocess.run(["gh", *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return json.loads(p.stdout) if p.returncode == 0 else None
    except ValueError:
        return None


def git_fetch(top):
    """Every remote fetched (the Git page's refresh: origin/… where it is now), True if it went. Never a password asked:
    no terminal and no prompt, only what git has already (its credentials, a keychain's, an agent's), and given up on
    after a minute."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "SSH_ASKPASS_REQUIRE": "never", "GCM_INTERACTIVE": "never"}
    try:
        return subprocess.run([lookup.git_exe(), "-C", top, "fetch", "--all", "--quiet"], env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                              start_new_session=True).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def github_repo(top):
    """owner/name of the GitHub repository `origin` points at, if it does."""
    url = (_git(top, "remote", "get-url", "origin") or "").strip()
    m = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


# ----------------------------------------------------------------- git view --
# The side panel's Git page: a branch's history as a commit log with lanes (the checkout the chat works in by
# default, or any branch, local or remote; or several, their histories together), the branch it tracks upstream and
# the branch it came from, when each commit was made (the clock time) and how much it changed (lines added and
# removed, in all and per file), the commits this chat's own commands made, and the hours commits are made at.
# GitHub adds pull requests when gh is signed in; otherwise there are links only. Everything is read with git, in a
# worker thread.
# (Who made each commit, with their picture: "authors' pictures", below.)

GIT_POOL = WorkerPool(2)


GH_POOL = WorkerPool(3)  # (GitHub's answers: half a second or more each, never in git's way)


GIT_CARDS = {}  # (top, sha) -> a commit's card (GitPage.read_card); None while it's read, False if there's none


GIT_CARD_FRESH = 120  # seconds a card's branches and pull requests are shown before they're read again


GIT_LOG_LIMIT = 50       # commits a page of the log shows (Show more adds as many again)


GIT_MERGE_LIMIT = 200    # a merge's own commits shown when it's opened


GIT_FIND_LIMIT = 5000    # commits read on, at most, to show one a link points at


GIT_LANES = ("#3584e4", "#2ec27e", "#e66100", "#9141ac", "#c88800", "#e01b24", "#2190a4", "#d56199")  # Adwaita's


GIT_CHAT = "#7f77dd"     # a commit this chat made: its dot


GIT_OFF_PEAK = (8, 20)   # commits outside these hours (local time) are off-peak


# (%b has lines of its own, and --shortstat's come after it: a last %x1f ends it)
_GIT_FMT = "%x1e" + "%x1f".join(("%H", "%P", "%an", "%ae", "%at", "%ct", "%D", "%s", "%b", ""))


_GIT_PR_MERGE = re.compile(r"Merge pull request #(\d+) from (?:[^\s/]+/(\S+)|.+)")  # (.+: "unknown repository")


# (Co-authored-by: and its kind, not a title's "fix: ..." or "Stand-by: ...")
_GIT_TRAILER = re.compile(r"(co-authored|signed-off|reviewed|tested|acked|reported|suggested|helped)-by: ", re.I)


def git_title(subject, body=""):
    """What the log and a commit's card call a commit: its subject, but a pull request GitHub merged (Merge pull
    request #78 from owner/branch) by #78 and the pull request's name, which GitHub puts first in the merge's body. No
    name there (a blank body, or only a Co-authored-by: line): #78 and its branch, if the subject says."""
    m = _GIT_PR_MERGE.fullmatch(subject)
    if not m:
        return subject
    name = next((line.strip() for line in body.splitlines() if line.strip()), "")
    if name and not _GIT_TRAILER.match(name):
        return f"#{m[1]} {name}"
    return f"#{m[1]} {m[2]}" if m[2] else subject


def git_log(top, revs, limit=GIT_LOG_LIMIT, skip=0, first_parent=False):
    """Commits reachable from revs, newest first (date order): [{sha, parents, author, email, at, ct, refs, subject,
    title (git_title), files, added, removed}], a merge's change counted against its first parent. first_parent: only
    the commits made on the branches themselves (a merged branch's own commits left out: its merge stands for them)."""
    args = ["log", "--date-order", f"--max-count={limit}", f"--skip={skip}", f"--format={_GIT_FMT}", "--shortstat"] + \
        (["--first-parent"] if first_parent else [])
    out = _git(top, *args, "--diff-merges=first-parent", *revs, "--", timeout=60)
    if out is None:  # (a git older than 2.31 doesn't know --diff-merges: merges show no change then)
        out = _git(top, *args, *revs, "--", timeout=60) or ""
    commits = []
    # (a body may hold anything: a record starts with its hash)
    for rec in re.split(r"\x1e(?=[0-9a-f]{40,64}\x1f)", out)[1:]:
        f = rec.split("\x1f")
        if len(f) < 10:
            continue
        stat = f[-1]
        num = lambda pat: int(m.group(1)) if (m := re.search(pat, stat)) else 0  # noqa: E731
        commits.append({"sha": f[0], "parents": f[1].split(), "author": f[2], "email": f[3], "at": int(f[4] or 0),
                        "ct": int(f[5] or 0), "refs": [r.strip() for r in f[6].split(",") if r.strip()],
                        "subject": f[7], "title": git_title(f[7], f[8]), "files": num(r"(\d+) files? changed"),
                        "added": num(r"(\d+) insertions?"), "removed": num(r"(\d+) deletions?")})
    return commits


def git_find(top, rev, sha, revs, commits, every):
    """Where a commit is in a branch's history as the Git page shows it: {sha, merge} (merge: the merge on the branch's
    own line it came in by, None if it's on that line, or every commit is shown), with commits read on (appended)
    until the row to show is in. None if the branch's history doesn't have it."""
    full = (_git(top, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}") or "").strip()
    if not full:
        return None
    target, merge = full, None
    if not every and full not in {c["sha"] for c in commits}:
        # (the commits after it on the branch's own line, newest first: the oldest came after it; if it isn't its
        # first parent, that's the merge that brought it in)
        line = (_git(top, "rev-list", "--first-parent", "--ancestry-path", f"{full}..{rev}") or "").split()
        if not line:
            return None
        oldest = line[-1]
        first = (_git(top, "rev-parse", f"{oldest}^1") or "").strip()
        if first != full:
            target = merge = oldest
    while target not in {c["sha"] for c in commits} and len(commits) < GIT_FIND_LIMIT:
        more = git_log(top, revs, limit=max(GIT_LOG_LIMIT, len(commits)), skip=len(commits), first_parent=not every)
        if not more:
            return None
        commits += more
    return {"sha": full, "merge": merge} if target in {c["sha"] for c in commits} else None


def git_lanes(commits):
    """Where a log's commits (newest first) are drawn, one row each: {col, id, into, through, out}. col is the dot's
    column; into the lanes that end at the dot (its children's); through the lanes that pass it; out the lanes its
    parents go on in, its first parent in its own column, so a branch is a straight line. Lines are (column, lane id),
    the id staying with a lane from its first commit to its last (its colour)."""
    lanes, ids, rows, next_id = [], [], [], 0

    def free():
        if None in lanes:
            return lanes.index(None)
        lanes.append(None)
        ids.append(None)
        return len(lanes) - 1
    for c in commits:
        hits = [i for i, s in enumerate(lanes) if s == c["sha"]]
        if hits:
            col = hits[0]
        else:  # (a branch's tip: a new lane)
            col = free()
            ids[col], next_id = next_id, next_id + 1
        into = [(i, ids[i]) for i in hits]
        through = [(i, ids[i]) for i, s in enumerate(lanes) if s is not None and s != c["sha"]]
        mine = ids[col]
        for i in hits[1:]:  # (children in other lanes: those lanes end here)
            lanes[i] = ids[i] = None
        out = []
        if c["parents"]:
            lanes[col] = c["parents"][0]
            out.append((col, mine))
            for p in c["parents"][1:]:
                if p in lanes:
                    j = lanes.index(p)
                else:
                    j = free()
                    lanes[j], ids[j], next_id = p, next_id, next_id + 1
                out.append((j, ids[j]))
        else:
            lanes[col] = ids[col] = None
        while lanes and lanes[-1] is None:
            lanes.pop()
            ids.pop()
        rows.append({"col": col, "id": mine, "into": into, "through": through, "out": out})
    return rows


git_full_when = full_time  # (a commit's card says when in full)


def github_login(email):
    """The GitHub user a commit's email names, when it's GitHub's noreply one (12345+naza@users.noreply.github.com,
    naza@users.noreply.github.com), else None."""
    m = re.fullmatch(r"(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com", email or "")
    return m[1] if m else None


def git_hours(commits):
    """How many of the commits were made at each hour of the day (local time), and the share made off-peak (outside
    GIT_OFF_PEAK) and at weekends: ([24 counts], off-peak share, weekend share)."""
    hours, weekend = [0] * 24, 0
    for c in commits:
        t = time.localtime(c["at"])
        hours[t.tm_hour] += 1
        weekend += t.tm_wday >= 5
    n = len(commits) or 1
    off = sum(v for h, v in enumerate(hours) if not GIT_OFF_PEAK[0] <= h < GIT_OFF_PEAK[1])
    return hours, off / n, weekend / n


def git_checkouts(top):
    """The branches to choose from: every checkout of the repository (its worktrees, a detached one too), then the
    other local branches, most recent first: [{rev, name (None if detached), label, worktree, upstream, ahead,
    behind, sha}]."""
    trees, cur = [], {}
    for line in (_git(top, "worktree", "list", "--porcelain") or "").splitlines() + [""]:
        if line.startswith("worktree "):
            cur = {"worktree": os.path.realpath(line[9:])}
        elif line.startswith("HEAD "):
            cur["sha"] = line[5:]
        elif line.startswith("branch "):
            cur["name"] = line[7:].removeprefix("refs/heads/")
        elif not line and cur:
            trees.append(cur)
            cur = {}
    fmt = "%(refname:short)%00%(objectname)%00%(upstream:short)%00%(upstream:track,nobracket)%00%(worktreepath)"
    branches = []
    for line in (_git(top, "for-each-ref", "--sort=-committerdate", f"--format={fmt}", "refs/heads") or "").splitlines():
        name, sha, up, track, tree = (line.split("\0") + [""] * 5)[:5]
        ahead = int(m.group(1)) if (m := re.search(r"ahead (\d+)", track)) else 0
        behind = int(m.group(1)) if (m := re.search(r"behind (\d+)", track)) else 0
        branches.append({"rev": name, "name": name, "sha": sha, "upstream": up or None, "ahead": ahead,
                         "behind": behind, "gone": "gone" in track, "worktree": os.path.realpath(tree) if tree else None})
    by_name = {b["name"]: b for b in branches}
    out = []
    for t in trees:
        b = by_name.get(t.get("name"))
        here = os.path.basename(t["worktree"])
        if b:
            out.append({**b, "worktree": t["worktree"], "label": f"{b['name']} — {here}"})
        elif not t.get("name"):  # (a detached checkout: its commit)
            out.append({"rev": t.get("sha", "HEAD"), "name": None, "sha": t.get("sha"), "upstream": None, "ahead": 0,
                        "behind": 0, "gone": False, "worktree": t["worktree"],
                        "label": f"detached at {(t.get('sha') or '')[:7]} — {here}"})
    shown = {o["name"] for o in out}
    out += [{**b, "label": b["name"]} for b in branches if b["name"] not in shown]
    return out


def git_parent_branch(top, branch, upstream=None):
    """The branch a branch most likely came from: of the other branches (local, then remote; its own upstream and its
    remote copies left out), the one whose merge base with it is the fewest commits below its tip, the remote's
    default branch winning a tie. A branch that contains it whole (a branch made from it, or one it was merged into)
    counts only when no other does. {name, base, ahead, behind} or None (for the default branch itself)."""
    tip = (_git(top, "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}") or "").strip()
    if not tip:
        return None
    default = (_git(top, "symbolic-ref", "--short", "refs/remotes/origin/HEAD") or "").strip()
    short = branch.split("/", 1)[1] if branch.startswith("origin/") else branch
    skip = {branch, upstream, f"origin/{short}", default if default.endswith("/" + short) else None, "origin/HEAD"}
    if default.endswith("/" + short) or short in ("main", "master") and not default:
        return None  # (the default branch: nothing it came from)
    refs = [r for r in (_git(top, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)",
                             "refs/heads", "refs/remotes") or "").splitlines() if r and r not in skip and r != "origin"]
    cands = ([default] if default in refs else []) + [r for r in refs if r != default][:40]
    best = whole = None
    for r in cands:
        base = (_git(top, "merge-base", tip, r) or "").strip()
        if not base:
            continue
        ahead = int((_git(top, "rev-list", "--count", f"{base}..{tip}") or "0").strip() or 0)
        key = (ahead, r != default, "/" in r)  # (fewest commits of its own; the default branch; a local one)
        if base == tip:
            whole = whole or (key, r, base)
        elif best is None or key < best[0]:
            best = (key, r, base)
    best = best or whole
    if best is None:
        return None
    behind = int((_git(top, "rev-list", "--count", f"{tip}..{best[1]}") or "0").strip() or 0)
    return {"name": best[1], "base": best[2], "ahead": best[0][0], "behind": behind}


GIT_REMOTES_LISTED = 300  # remote branches the Git page's branch list has at most (the most recently changed)
GIT_BASES_READ = 3        # branches this chat touched whose base is worked out (git_parent_branch) for the list's order
_GIT_BASES = {}           # (top, branch, its tip) -> the name of the branch it came from, or None (git_branch_list)


def git_remote_branches(top):
    """The remote branches, most recently changed first (origin/HEAD left out), as git_checkouts' entries are."""
    out = []
    for line in (_git(top, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)%00%(objectname)%00"
                      "%(symref)", "refs/remotes") or "").splitlines():
        name, sha, symref = (line.split("\0") + ["", ""])[:3]
        if name and not symref and "/" in name:
            out.append({"rev": name, "name": name, "sha": sha, "upstream": None, "ahead": 0, "behind": 0,
                        "gone": False, "worktree": None, "label": name, "remote": True})
    return out[:GIT_REMOTES_LISTED]


def git_branch_list(top, entries, mine, chat, parents=None):
    """The Git page's branch list, in order: [entry + {touched}] (entries: git_checkouts' and git_remote_branches',
    in that order). First the branches this chat touched: the branch its checkout has (mine), then those its own
    commands committed on or pushed to (chat: chat_git_events), the latest first; and with each the branch it came
    from: the one it tracks if that's another (made with -b from origin/main, it tracks that), else git_parent_branch's
    guess (parents: {rev: name} already worked out). Each base before the branches made from it, those made from one
    base together, a branch's own remote copy right after it. Then every other branch, in entries' order."""
    by_rev = {e["rev"]: e for e in entries}

    def known(name):  # (a branch a command named: the local one, else a remote's copy of it, origin's first)
        if name in by_rev and not by_rev[name].get("remote"):
            return name
        copies = [e["rev"] for e in entries if e.get("remote") and e["rev"].split("/", 1)[1] == name]
        return next((r for r in copies if r.startswith("origin/")), copies[0] if copies else None)
    touched = [mine] if mine in by_rev else []
    for ev in reversed((chat or {}).get("events", [])):
        if ev["kind"] in ("commit", "push") and ev.get("branch") and \
                (rev := known(ev["branch"])) is not None and rev not in touched:
            touched.append(rev)
    bases, copies, kids = {}, {}, {}
    for i, rev in enumerate(touched):
        e = by_rev[rev]
        base = up = e.get("upstream")
        if up and up.split("/", 1)[-1] == e["name"]:
            copies[rev], base = up, None  # (its own remote copy: after it)
        if base is None:  # (one made with -b from another tracks that: else, the likeliest)
            base = (parents or {}).get(rev)
            if base is None and i < GIT_BASES_READ:
                key = (top, rev, e.get("sha"))
                if key not in _GIT_BASES:
                    found = git_parent_branch(top, rev, up)
                    _GIT_BASES[key] = found["name"] if found else None
                base = _GIT_BASES[key]
        if base and base in by_rev and base != rev:
            bases[rev] = base
            kids.setdefault(base, []).append(rev)
    out, seen = [], set()

    def add(rev, touched=True):
        if rev and rev in by_rev and rev not in seen:
            seen.add(rev)
            out.append({**by_rev[rev], "touched": touched})

    def place(rev):  # (it, its remote copy, then what was made from it, the same way)
        if rev not in seen:
            add(rev)
            add(copies.get(rev))
            for kid in kids.get(rev, ()):
                place(kid)
    for rev in touched:
        below = set()
        while rev in bases and bases[rev] not in seen and rev not in below:  # (from the base it all came from)
            below.add(rev)
            rev = bases[rev]
        place(rev)
    for e in entries:
        add(e["rev"], touched=False)
    return out


def git_commit_card(top, sha):
    """git's part of a commit's card: {sha, parents, author, email, at, committer, ct, message, files, login (from a
    noreply email), read}, or None if there's no such commit. The branches and GitHub's part come later
    (GitPage.read_card_more)."""
    info = _git(top, "show", "-s", "--format=%H%x00%P%x00%an%x00%ae%x00%at%x00%cn%x00%ct%x00%B", sha) or ""
    f = info.split("\0")
    if len(f) < 8:
        return None
    return {"sha": f[0], "parents": f[1].split(), "author": f[2], "email": f[3], "at": int(f[4] or 0),
            "committer": f[5], "ct": int(f[6] or 0), "message": f[7].strip(), "files": git_numstat(top, f[0]),
            "branches": None, "remotes": [], "login": github_login(f[3]), "read": time.time()}


def git_numstat(top, sha):
    """A commit's change per file, against its first parent: [(path, added, removed)], None for a binary file's."""
    out = _git(top, "show", "--numstat", "--format=", "-M", "--diff-merges=first-parent", sha) or \
        _git(top, "show", "--numstat", "--format=", "-M", sha) or ""
    files = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            a, d, path = parts
            files.append((path, int(a) if a.isdigit() else None, int(d) if d.isdigit() else None))
    return files


_GH_STATE = {}


def gh_state(refresh=False):
    """Whether gh can ask GitHub as you: ("signed_in", login), ("signed_out", None) or ("missing", None). Asked again
    after five minutes."""
    if not refresh and _GH_STATE.get("at", 0) > time.time() - 300:
        return _GH_STATE["state"]
    if not shutil.which("gh"):
        state = ("missing", None)
    else:
        try:
            ok = subprocess.run(["gh", "auth", "status", "--hostname", "github.com"], stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=20).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        user = _gh_json("api", "user") if ok else None
        state = ("signed_in", (user or {}).get("login")) if ok else ("signed_out", None)
    _GH_STATE.update(state=state, at=time.time())
    return state


# ---------------------------------------------------------- authors' pictures --
# A log row's small round picture of who made the commit. Adw.Avatar draws their initials on a colour until a picture
# comes, and for good if none does. A GitHub noreply email names the account (no sign-in needed); for any other, one
# read of the repository's recent commits (gh signed in) says whose it is. A picture is read once, off the main loop,
# a few at a time, from GitHub only, and kept in the cache folder, so a restart doesn't read it again.

AVATAR_SIZE = 20                # a row's picture, in pixels (less than the row's two lines: the row's no taller)


AVATAR_ASK = 40                 # the size asked of GitHub (twice that: sharp on a high-resolution screen)


AVATAR_MAX_BYTES = 1_000_000    # a picture bigger than this isn't taken (at the size asked, they're a few KB)


AVATAR_MAX_SIDE = 1024          # nor one wider or taller than this, said by its header before anything decodes it


AVATAR_CACHE_BYTES = 30_000_000  # the cache folder in all (past this, or AVATAR_CACHE_FILES, the oldest go)


AVATAR_CACHE_FILES = 2000


AVATAR_KEEP = 30 * 86400        # a picture's read again after this, the old one showing till then (people change them)


AVATAR_MISS_KEEP = 86400        # GitHub had nothing for someone: not asked again for this long


AVATAR_LIST_KEEP = 600          # what a repository's commits said of who's who, used again for this long


AVATAR_QUIET = 300              # offline, GitHub not answering, or signed out: nothing more tried for this long


AVATAR_ASK_MOST = 100           # new authors looked for at a time, the list's first (a deep link can draw thousands of rows)


AVATAR_POOL = WorkerPool(2)     # (their own: a page of new authors doesn't hold up a commit's card, on GH_POOL)


def avatar_folder():
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "baude", "avatars")


def avatar_file(*key):
    return os.path.join(avatar_folder(), hashlib.sha1("\0".join(key).encode()).hexdigest())


def avatar_url_of(email):
    """Where a noreply email's picture is, without asking anyone: by the account's number where it says it (a name can pass
    to someone else), else by its name. None for any other email."""
    login = github_login(email)
    if not login:
        return None
    number = email.split("+")[0]
    return f"https://avatars.githubusercontent.com/u/{number}?s={AVATAR_ASK}" if number.isdigit() else \
        f"https://github.com/{login}.png?size={AVATAR_ASK}"


def avatar_url_ok(url):
    """Whether a URL is one a picture's read from: https, GitHub's own hosts, nothing else (this comes from a server)."""
    try:
        u = urllib.parse.urlsplit(url)
        host = re.fullmatch(r"github\.com|avatars\d*\.githubusercontent\.com", u.hostname or "")
        return bool(host) and u.scheme == "https" and u.port in (None, 443) and "@" not in u.netloc and \
            url.isprintable() and " " not in url
    except (ValueError, TypeError, AttributeError):
        return False


def avatar_urls(commits):
    """{email: picture URL} from GitHub's list of a repository's commits (repos/{repo}/commits): each commit's author,
    where GitHub knows who that is. What isn't as it should be is left out."""
    urls = {}

    def dig(o, *path):
        for p in path:
            o = o.get(p) if isinstance(o, dict) else None
        return o
    for c in commits if isinstance(commits, list) else ():
        email, url = dig(c, "commit", "author", "email"), dig(c, "author", "avatar_url")
        if email and isinstance(email, str) and avatar_url_ok(url):
            urls[email.lower()] = f"{url}{'&' if '?' in url else '?'}s={AVATAR_ASK}"
    return urls


def avatar_size(data):
    """(width, height) of a PNG, JPEG or GIF, from its header alone, or None for anything else (an SVG, a web page)."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data[:3] == b"\xff\xd8\xff":
            i = 2
            while data[i] == 0xFF:
                marker = data[i + 1]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # (a frame's start says its size)
                    height, width = struct.unpack(">HH", data[i + 5:i + 9])
                    return width, height
                i += 1 if marker == 0xFF else 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except (IndexError, struct.error):
        pass
    return None


def avatar_picture(data):
    """A texture of what was read, if it's a real picture of a sane size (asked before GDK sees it, which would take
    an SVG or a picture too big to decode), and whole. Else None."""
    size = avatar_size(data) if data and len(data) <= AVATAR_MAX_BYTES else None
    if not size or not all(0 < side <= AVATAR_MAX_SIDE for side in size):
        return None
    try:
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
    except GLib.Error:
        return None


def _avatar_opener():
    """urllib's opener, kept to GitHub: a redirect anywhere else is no picture (github.com/x.png is always a redirect,
    to its avatars host)."""
    import urllib.request  # (only now: http/ssl/email cost ~25 ms at start-up)

    class Stay(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not avatar_url_ok(newurl):
                raise ValueError(f"redirected off GitHub: {newurl}")
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    return urllib.request.build_opener(Stay)


def _avatar_download(url):
    """A picture's bytes, or None if GitHub has none. Raises OSError if that can't be known: offline, or something other
    than GitHub answering (a network's sign-in page), or only part of it coming."""
    import http.client
    import urllib.error
    import urllib.request
    if not avatar_url_ok(url):
        return None
    try:
        with _avatar_opener().open(urllib.request.Request(url, headers={"User-Agent": "baude"}), timeout=10) as r:
            if not r.headers.get_content_type().startswith("image/"):
                raise OSError(f"not a picture: {r.headers.get_content_type()}")
            data = r.read(AVATAR_MAX_BYTES + 1)
            length = r.headers.get("Content-Length") or ""
        if length.isdigit() and len(data) < min(int(length), AVATAR_MAX_BYTES + 1):
            raise OSError(f"{len(data)} bytes of {length}")  # (a cut-off JPEG still decodes, and would be kept)
        return data if len(data) <= AVATAR_MAX_BYTES else None
    except urllib.error.HTTPError as e:
        e.close()
        if e.code in (404, 410, 451):
            return None
        raise
    except (ValueError, http.client.HTTPException) as e:  # (ValueError: sent off GitHub)
        raise OSError(e) from e


def _avatar_keep(path, data):
    """A file in the cache folder, whole or not at all, and the folder kept small: pictures stay longest, then notes that
    there's none, the oldest of each going first. (If it can't be written, the picture's read again next run.)"""
    try:
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        files, total = [], 0
        for e in os.scandir(folder):
            st = e.stat()
            rank = 0 if e.name.endswith(".img") else 1 if e.name.endswith(".miss") else 2  # (2: a write that never finished)
            files.append((rank, -st.st_mtime, st.st_size, e.path))
        for i, (_rank, _at, size, name) in enumerate(sorted(files)):
            total += size
            if i >= AVATAR_CACHE_FILES or total > AVATAR_CACHE_BYTES:
                os.remove(name)
    except OSError:
        pass


class GitAvatars:
    """Who has what picture, for every Git page there is. On the main loop: ask, again, came; in workers: the rest."""

    def __init__(self):
        self.pictures = {}  # email -> its texture
        # email -> till when its picture's new enough; (repo, email) -> when it may be looked for again (inf: it's being
        # looked for). repo None: a noreply email, whose picture doesn't depend on the repository
        self.asked = {}
        self.lists = {}     # repo -> (till when it's used, what its commits said: avatar_urls)
        self.quiet = 0      # no network: till when nothing's tried
        self.sign_in_read = True  # (False: the refresh button was pressed, and whether gh's signed in is read again)
        self.pages = weakref.WeakSet()
        self.list_lock = threading.Lock()

    @staticmethod
    def key(email, repo):
        return None if github_login(email) else repo, email

    @staticmethod
    def miss_file(key):
        return avatar_file(key[0] or "", key[1]) + ".miss"

    def recall(self, commits, repo):
        """What the cache folder has on these commits' authors (a worker, before their rows are drawn: they come with
        their pictures, and don't change as they're shown)."""
        for email in {c["email"].lower() for c in commits} - {""}:
            if email not in self.pictures:
                path = avatar_file(email) + ".img"
                try:
                    with open(path, "rb") as f:
                        picture = avatar_picture(f.read())
                    if picture is not None:
                        self.pictures[email], self.asked[email] = picture, os.stat(path).st_mtime + AVATAR_KEEP
                except OSError:
                    pass
            key = self.key(email, repo)
            if key not in self.asked:
                try:
                    self.asked[key] = os.stat(self.miss_file(key)).st_mtime + AVATAR_MISS_KEEP
                except OSError:
                    pass

    def ask(self, page, commits, repo):
        """Rows were just drawn for these commits: whoever's picture isn't known is looked for, in the background, once.
        (On every redraw: it does nothing when no one's new.) BAUDE_GH empty, as tests and the fixer's runs of them
        have it: nothing's asked of GitHub at all."""
        self.pages.add(page)
        now = time.time()
        if os.environ.get("BAUDE_GH") == "" or now < self.quiet:
            return
        new = []
        for email in dict.fromkeys(c["email"].lower() for c in commits):
            key = self.key(email, repo)
            if email and (repo or github_login(email)) and self.asked.get(email, 0) <= now and self.asked.get(key, 0) <= now:
                self.asked[key] = math.inf
                new.append(key)
                if len(new) == AVATAR_ASK_MOST:
                    break
        if new:
            AVATAR_POOL.submit(lambda: self.find(new, repo))

    def again(self):
        """The refresh button: whoever has no picture is looked for again, now."""
        self.quiet, self.sign_in_read = 0, False
        self.lists.clear()
        for key, until in list(self.asked.items()):
            if isinstance(key, tuple) and until != math.inf:
                del self.asked[key]

    def find(self, keys, repo):
        """Where each one's picture is, and its download queued (one for those who share a picture)."""
        known = self.listed(repo) if any(key[0] for key in keys) else None
        urls = {}
        for key in keys:
            url = avatar_url_of(key[1]) or (known or {}).get(key[1])
            if url:
                urls.setdefault(url, []).append(key)
            elif known is None:  # (signed out or offline: that says nothing about them)
                self.asked[key] = time.time() + AVATAR_QUIET
            else:
                self.nothing(key)
        for url, who in urls.items():
            AVATAR_POOL.submit(lambda url=url, who=who: self.fetch(url, who))

    def listed(self, repo):
        """{email: picture URL} of a repository's recent commits (one GET, used again for a while), or None if gh can't
        say (not asked again for a while either). Who asks while it's being read waits for its answer."""
        with self.list_lock:
            until, known = self.lists.get(repo, (0, None))
            if until > time.time():
                return known
            read, self.sign_in_read = self.sign_in_read, True
            found = _gh_json("api", f"repos/{repo}/commits?per_page=100") if gh_state(refresh=not read)[0] == "signed_in" else None
            known = avatar_urls(found) if isinstance(found, list) else None
            self.lists[repo] = (time.time() + (AVATAR_QUIET if known is None else AVATAR_LIST_KEEP), known)
            return known

    def fetch(self, url, keys):
        """One picture, for everyone it's the picture of. No network: the ones queued behind it don't try either."""
        data, offline = None, time.time() < self.quiet
        if not offline:
            try:
                data = _avatar_download(url)
            except OSError:
                offline, self.quiet = True, time.time() + AVATAR_QUIET
        if offline:
            for key in keys:
                self.asked[key] = self.quiet
            return
        picture = avatar_picture(data)
        if picture is None:
            for key in keys:
                self.nothing(key)
            return
        for email in {key[1] for key in keys}:
            _avatar_keep(avatar_file(email) + ".img", data)
        GLib.idle_add(self.came, keys, picture)

    def nothing(self, key):
        """GitHub has no picture for them: not asked again for a while, this run or the next."""
        self.asked[key] = time.time() + AVATAR_MISS_KEEP
        _avatar_keep(self.miss_file(key), b"")

    def came(self, keys, picture):
        emails = {key[1] for key in keys}
        for key in keys:
            self.asked.pop(key, None)
        for email in emails:
            self.pictures[email], self.asked[email] = picture, time.time() + AVATAR_KEEP
        for page in list(self.pages):
            page.avatar_came(emails, picture)
        return False


AVATARS = GitAvatars()


_CHAT_GIT = {}


_GIT_COMMIT_OUT = re.compile(r"^\[([^\]\s]+)(?: \(root-commit\))? ([0-9a-f]{7,40})\] (.*)$", re.M)


_GIT_PUSH_OUT = re.compile(r"^\s*[+ ]?(?:([0-9a-f]{7,40})\.\.\.?([0-9a-f]{7,40})|\* \[new branch\])\s+(\S+)\s+->\s+(\S+)",
                           re.M)


def chat_git_events(path):
    """What a chat's own commands did to git, from its transcript: {"commits": {sha: event}, "events": [event]}. An
    event is {kind (commit, push, merge, pr), sha, branch, subject, tool_use_id, at}: a commit from git commit's
    "[branch sha] subject", a push from "a..b branch -> branch", a fast-forward merge from "Updating a..b", a pull
    request from gh pr create's URL. Read again when the transcript grows."""
    try:
        size = os.path.getsize(path)
    except (OSError, TypeError):
        return {"commits": {}, "events": []}
    if (hit := _CHAT_GIT.get(path)) and hit[0] == size:
        return hit[1]
    calls, events = {}, []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"tool_use"' not in line and '"tool_result"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                content = (rec.get("message") or {}).get("content")
                for b in content if isinstance(content, list) else ():
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use" and b.get("name") == "Bash":
                        cmd = str((b.get("input") or {}).get("command") or "")
                        if re.search(r"\b(git|gh)\s", cmd):
                            calls[b.get("id")] = cmd
                    elif b.get("type") == "tool_result" and b.get("tool_use_id") in calls:
                        out = b.get("content")
                        out = out if isinstance(out, str) else "\n".join(
                            x.get("text", "") for x in out or () if isinstance(x, dict))
                        tid, at = b["tool_use_id"], ref_epoch(rec.get("timestamp"))
                        ev = lambda kind, **kw: events.append({"kind": kind, "tool_use_id": tid, "at": at, **kw})  # noqa: E731
                        for m in _GIT_COMMIT_OUT.finditer(out):
                            ev("commit", sha=m.group(2), branch=m.group(1), subject=m.group(3))
                        for m in _GIT_PUSH_OUT.finditer(out):
                            ev("push", sha=m.group(2), branch=m.group(4), subject=f"pushed {m.group(3)} to {m.group(4)}")
                        if m := re.search(r"^Updating ([0-9a-f]{7,40})\.\.([0-9a-f]{7,40})$", out, re.M):
                            ev("merge", sha=m.group(2), branch=None, subject=f"fast-forwarded to {m.group(2)}")
                        for m in re.finditer(r"https://github\.com/[\w.-]+/[\w.-]+/pull/(\d+)", out):
                            if "gh pr create" in calls[tid]:
                                ev("pr", sha=None, branch=None, subject=f"opened #{m.group(1)}", url=m.group(0))
    except OSError:
        pass
    found = {"commits": {e["sha"]: e for e in events if e["kind"] == "commit"}, "events": events}
    _CHAT_GIT[path] = (size, found)
    return found


def git_chat_commit(chat, sha):
    """The event of this chat's that made the commit sha (its hash written short), or None."""
    return next((e for short, e in chat.get("commits", {}).items() if sha.startswith(short)), None)


class GitLane(Gtk.Widget):
    """A row's slice of the commit graph (git_lanes): the lanes passing it, those ending at its dot and those its
    parents go on in; every commit a plain dot, a violet one if this chat made it."""

    STEP, PAD = 12, 9

    def __init__(self, row, cols, chat=False, dim=False):
        super().__init__(vexpand=True)
        self.row, self.chat, self.dim = row, chat, dim
        self.set_size_request(self.PAD * 2 + max(0, min(cols, 10) - 1) * self.STEP, -1)

    def x(self, col):
        return self.PAD + min(col, 10) * self.STEP

    def color(self, lane, chat=False):
        c = Gdk.RGBA()
        c.parse(GIT_CHAT if chat else GIT_LANES[(lane or 0) % len(GIT_LANES)])
        c.alpha = 0.45 if self.dim else 1.0
        return c

    def do_snapshot(self, snap):
        h, row = self.get_height(), self.row
        mid, xc = h / 2, self.x(row["col"])
        stroke = Gsk.Stroke.new(1.6)

        def line(x1, y1, x2, y2, lane):
            p = Gsk.PathBuilder.new()
            p.move_to(x1, y1)
            if x1 == x2:
                p.line_to(x2, y2)
            else:  # (a curve into or out of the dot's column)
                p.cubic_to(x1, (y1 + y2) / 2, x2, (y1 + y2) / 2, x2, y2)
            snap.append_stroke(p.to_path(), stroke, self.color(lane))
        for col, lane in row["through"]:
            line(self.x(col), 0, self.x(col), h, lane)
        for col, lane in row["into"]:
            line(self.x(col), 0, xc, mid, lane)
        for col, lane in row["out"]:
            line(xc, mid, self.x(col), h, lane)
        dot = Gsk.PathBuilder.new()
        dot.add_circle(Graphene.Point().init(xc, mid), 4.0)
        snap.append_fill(dot.to_path(), Gsk.FillRule.WINDING, self.color(row["id"], self.chat))


class GitHours(Gtk.Widget):
    """Commits per hour of the day (local time) as 24 bars, the off-peak hours' in amber; the hours along the bottom
    (00, 06, 12, 18) and the busiest hour's count up the side, with a faint line across."""

    AXIS = 13  # the height of the hours row under the bars

    def __init__(self):
        super().__init__(hexpand=True)
        self.hours = [0] * 24
        self.set_size_request(-1, 30 + self.AXIS)

    def set_hours(self, hours):
        self.hours = hours
        self.set_tooltip_text("Commits by hour of the day, local time\n" +
                              "\n".join(f"{h:02d}:00  {n}" for h, n in enumerate(hours) if n))
        self.queue_draw()

    def labels(self):
        """The count up the side: the peak's."""
        top = max(self.hours)
        return [f"{top}"] if top else []

    def do_snapshot(self, snap):
        w, h, top = self.get_width(), self.get_height(), max(self.hours) or 1
        fg = self.get_color()
        dim = self.get_color()
        dim.alpha = 0.55
        layouts = [self.create_pango_layout(t) for t in self.labels()]
        for lay in layouts:
            lay.set_font_description(Pango.FontDescription.from_string("Sans 7"))
        gutter = max([0] + [lay.get_pixel_size()[0] for lay in layouts]) + 5 if layouts else 0
        chart = h - self.AXIS
        gap = 2
        bw = max(1.0, (w - gutter - gap * 23) / 24)
        x0 = lambda i: gutter + i * (bw + gap)  # noqa: E731
        line = self.get_color()
        line.alpha = 0.12
        for lay in layouts:  # (the peak at the top, a faint line across)
            y = 1
            snap.append_color(line, Graphene.Rect().init(gutter, round(y), w - gutter, 1))
            tw, th = lay.get_pixel_size()
            snap.save()
            snap.translate(Graphene.Point().init(gutter - 5 - tw, max(0, y - th / 2)))
            snap.append_layout(lay, dim)
            snap.restore()
        day, night, empty = Gdk.RGBA(), Gdk.RGBA(), self.get_color()
        day.parse(GIT_LANES[0])
        night.parse("#c88800")
        empty.alpha = 0.12
        for i, n in enumerate(self.hours):
            bh = max(2.0, (chart - 2) * n / top) if n else 2.0
            col = empty if not n else day if GIT_OFF_PEAK[0] <= i < GIT_OFF_PEAK[1] else night
            snap.append_color(col, Graphene.Rect().init(x0(i), chart - bh, bw, bh))
        for hour in (0, 6, 12, 18):
            lay = self.create_pango_layout(f"{hour:02d}")
            lay.set_font_description(Pango.FontDescription.from_string("Sans 7"))
            snap.save()
            snap.translate(Graphene.Point().init(x0(hour), chart + 1))
            snap.append_layout(lay, dim if hour else fg)
            snap.restore()


class GitPage(Gtk.Stack):
    """The side panel's Git page (see "git view" above). The list: the branches shown, picked from a list (the ones
    this chat touched first, the chat's own checkout's chosen at first; then the rest: git_branch_list), what the
    branch tracks and what it came from, GitHub's state, commits per hour, and the log, each row with its author's
    picture (GitAvatars). A commit opens its card: when it was made, its message, the lines it changed in all and
    per file (each opens its diff), the branches that have it, its pull requests, and the turn of this chat that made
    it."""

    def __init__(self, win):
        super().__init__(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.win, self.top, self.checkouts, self.commits, self.chat = win, None, [], [], {}
        self.revs, self.follow, self.token, self.stale, self.state = None, True, 0, True, {}
        self.opened = {}  # merge sha -> its own commits, shown under it (None while they're read)
        self.loaded_for, self.current, self.chat_key, self.soon = None, None, None, None
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head = Gtk.Box(spacing=6, margin_start=10, margin_end=8, margin_top=8)
        self.where = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.START, hexpand=True)  # (the repository)
        self.where.add_css_class("dim-label")
        head.append(self.where)
        self.all_btn = Gtk.ToggleButton(icon_name=themed_icon("view-list-bullet-symbolic", "view-list-symbolic"),
                                        tooltip_text="Show every branch")
        self.all_btn.add_css_class("flat")
        self.all_btn.connect("toggled", lambda *_: self.load())
        head.append(self.all_btn)
        self.refresh_btn = RefreshButton(tooltip_text="Fetch, and read the history again (and ask GitHub)")
        self.refresh_btn.connect("clicked", lambda *_: self.load(gh=True))
        head.append(self.refresh_btn)
        top.append(head)
        # the branches shown: a list chosen from as files are in a file manager (a click picks one, Shift and a click
        # the ones from the last picked to it, Ctrl or ⌘ and a click adds one or takes it out; the arrows, Shift and
        # the arrows, Ctrl+Space too), shown once it's closed (Escape, a click outside it, Enter or a double click):
        # several, their histories together. The branches this chat touched first, then a rule, then the rest
        # (git_branch_list). The button says what's shown, cut short with … to fit: a long name never widens the
        # panel (a list's whole names are in its popover, a window of its own)
        self.picker = Gtk.MenuButton(margin_start=10, margin_end=10, always_show_arrow=True,
                                     css_classes=["git-branches"],
                                     tooltip_text="The branches shown: this chat's first, then the rest. "
                                                  "Shift or Ctrl (⌘) and a click picks more")
        said = Gtk.Box(spacing=6)
        self.picked_name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.picked_more = Gtk.Label(xalign=0, hexpand=True, visible=False)
        self.picked_more.add_css_class("dim-label")
        said.append(self.picked_name)
        said.append(self.picked_more)
        self.picker.set_child(said)
        self.branches = Gtk.ListBox(selection_mode=Gtk.SelectionMode.MULTIPLE, activate_on_single_click=False)
        self.branches.set_header_func(GitPage.branch_rule)
        self.branches.connect("selected-rows-changed", lambda *_: self.tick_branches())
        self.branches.connect("row-activated", lambda *_: self.branch_list.popdown())  # (Enter, a double click)
        self.branch_list = Gtk.Popover(css_classes=["git-branches"], child=Gtk.ScrolledWindow(
            child=self.branches, hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_width=True,
            propagate_natural_height=True, max_content_height=420))
        self.branch_list.connect("show", lambda *_: self.branches_shown())
        self.branch_list.connect("closed", lambda *_: self.branches_closed())
        self.picker.set_popover(self.branch_list)
        self.picked = []  # (the branches shown, as the list was when it last closed or was read)
        top.append(self.picker)
        self.info = Gtk.Label(xalign=0, wrap=True, margin_start=12, margin_end=10, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.info.connect("activate-link", on_label_link)
        top.append(self.info)
        hours = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=12, margin_end=12, margin_top=2)
        self.hours = GitHours()
        hours.append(self.hours)
        top.append(hours)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("git-log")
        self.list.connect("row-activated", lambda _l, row: self.show_commit(row.commit["sha"]))
        self.list.set_placeholder(empty_label("No commits"))
        keep_times(self.list)  # (the history's only read again when something changes)
        self.more = Gtk.Button(label="Show more", halign=Gtk.Align.CENTER, visible=False, margin_top=6, margin_bottom=10)
        self.more.add_css_class("flat")
        self.more.connect("clicked", lambda *_: self.load_more())
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.list)
        body.append(self.more)
        self.list_scroll = Gtk.ScrolledWindow(child=body, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        top.append(self.list_scroll)
        self.add_named(top, "list")
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        dhead = Gtk.Box(spacing=6, margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="The log")
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self.show_list())
        self.detail_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        self.detail_title.add_css_class("heading")
        dhead.append(back)
        dhead.append(self.detail_title)
        detail.append(dhead)
        self.detail_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12, margin_end=12,
                                   margin_bottom=12)
        detail.append(Gtk.ScrolledWindow(child=self.detail_body, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.add_named(detail, "detail")

    # ---- what to show
    def chat_folder(self):
        return getattr(self, "pinned", None) or self.win.ref_checkout or self.win.cwd

    def chat_changed(self):
        """The chat changed or went on: read the history again (soon, if this page shows). Another chat, or the chat
        moving to another checkout, shows its branch again; a branch you picked stays while the chat only goes on."""
        key = (self.win.ref_checkout or self.win.cwd, self.win.ref_transcript())
        if key != self.chat_key:
            self.follow, self.chat_key, self.pinned = True, key, None
        self.stale = True
        if self.soon is None and self.win.panel.get_visible() and self.win.panel.stack.get_visible_child_name() == "git":
            self.soon = GLib.timeout_add(800, self._changed_soon)

    def _changed_soon(self):
        self.soon = None
        self.ensure()
        return False

    def ensure(self):
        """Shown: load, if something changed since."""
        if self.stale or self.loaded_for != (self.chat_folder(), self.win.ref_transcript()):
            self.load()

    def focus(self, rev=None, sha=None, folder=None):
        """Show a branch (rev), or a commit (a link) in the history of a branch that has it, highlighted, the merge
        it came in by opened. folder: another repository's (a commit the chat made there), shown until the chat
        changes or one of its own is asked for."""
        self.follow, self.pinned = False, folder
        if rev or sha:
            self.revs = [rev] if rev else None  # (a commit: the chat's branch if it has it, else one that does)
        self.load(then=sha)

    # ---- the branch list
    @staticmethod
    def branch_rule(row, before):
        """The rule between the branches this chat touched and the rest."""
        rule = before is not None and before.touched and not row.touched
        if rule != isinstance(row.get_header(), Gtk.Separator):
            row.set_header(Gtk.Separator(margin_top=4, margin_bottom=4) if rule else None)

    def fill_branches(self, branches, picked):
        """The list read again (branches: git_branch_list's), the ones shown (picked) chosen in it."""
        while (row := self.branches.get_first_child()) is not None:
            self.branches.remove(row)
        for b in branches:
            row = Gtk.ListBoxRow()
            row.rev, row.touched, row.name = b["rev"], b["touched"], b["name"] or b["label"]
            box = Gtk.Box(spacing=10)
            box.append(Gtk.Label(label=b["label"] + ("  (this chat)" if b["rev"] == self.state.get("mine") else ""),
                                 xalign=0, hexpand=True))
            row.tick = Gtk.Image(icon_name="object-select-symbolic", opacity=0)
            box.append(row.tick)
            row.set_child(box)
            self.branches.append(row)
        self.choose(picked)
        self.picked = [r.rev for r in self.branch_rows() if r.is_selected()]
        self.say_picked()

    def choose(self, revs):
        """These branches chosen in the list, and no others; Shift goes on from the first of them (the one GTK's
        list chose last)."""
        rows = list(self.branch_rows())
        for row in rows:
            if row.rev not in revs:
                self.branches.unselect_row(row)
        for row in reversed([r for r in rows if r.rev in revs]):
            self.branches.select_row(row)

    def branch_rows(self):
        row = self.branches.get_first_child()
        while row is not None:
            if isinstance(row, Gtk.ListBoxRow):
                yield row
            row = row.get_next_sibling()

    def tick_branches(self):
        for row in self.branch_rows():
            row.tick.set_opacity(1 if row.is_selected() else 0)

    def say_picked(self):
        """The button: the branch shown, or the first of them and how many more."""
        names = [r.name for r in self.branch_rows() if r.rev in self.picked]
        self.picked_name.set_label(names[0] if names else "")
        self.picked_more.set_label(f"+ {len(names) - 1} more")
        self.picked_more.set_visible(len(names) > 1)
        self.picker.set_tooltip_text("\n".join(names) if len(names) > 1 else
                                     "The branches shown: this chat's first, then the rest. "
                                     "Shift or Ctrl (⌘) and a click picks more")

    def branches_shown(self):
        """The list open: the keys at the first branch shown, so the arrows go on from there. (GTK's popover, opening,
        has put them on the list's last chosen, and a list that's given them so chooses that one alone: the ones
        shown are chosen again, before it's drawn)"""
        self.choose(self.picked)
        if (first := next((r for r in self.branch_rows() if r.is_selected()), None)) is not None:
            first.grab_focus()

    def branches_closed(self):
        """The list closed: the branches chosen in it shown, if they're others (none chosen: those shown stay)."""
        chosen = [r.rev for r in self.branch_rows() if r.is_selected()]
        if chosen and chosen != self.picked:
            self.pick(chosen)
        elif getattr(self, "branches_later", None):  # (as last read: read while it was open, or none chosen)
            self.fill_branches(*self.branches_later)

    def pick(self, revs):
        """Show these branches (several: their histories together), as the list does once it's closed."""
        self.choose(revs)
        self.picked, self.revs, self.follow = list(revs), list(revs), False
        self.say_picked()
        self.load()

    # ---- reading (worker thread)
    def load(self, then=None, gh=False):
        folder, transcript = self.chat_folder(), self.win.ref_transcript()
        self.token += 1
        token, rev, follow, every = self.token, self.revs, self.follow, self.all_btn.get_active()
        self.stale, self.loaded_for = False, (folder, transcript)
        self.where.set_label("Reading the history…")
        if gh:  # (asked for: its button turns till it's read. The reads a chat going on brings aren't worth the motion)
            self.refresh_btn.set_loading(True)

        def work():
            res = self.gather(folder, transcript, rev, follow, every, then, gh)
            GLib.idle_add(self.loaded, token, res)
        GIT_POOL.submit(work)

    @staticmethod
    def gather(folder, transcript, picked, follow, every, then=None, gh=False):
        """picked: the branches to show (several: their histories together), else (or follow) the chat's."""
        top = (_git(folder, "rev-parse", "--show-toplevel") or "").strip() if folder else ""
        if not top:
            return {"top": None, "folder": folder}
        fetched = git_fetch(top) if gh else None  # (asked for, the refresh button: the remotes' branches as they are now)
        checkouts = git_checkouts(top)
        entries = checkouts + git_remote_branches(top)
        mine = next((c for c in checkouts if c["worktree"] == os.path.realpath(top)), None)
        picked = [r for r in picked or () if r]
        if then and not picked:  # (a commit: the chat's branch if it has it, else the first checkout or one with it)
            has = set((_git(top, "for-each-ref", "--contains", then, "--format=%(refname:short)", "refs/heads")
                       or "").split())
            pick = next((c for c in ([mine] if mine else []) + checkouts if c and c["name"] in has), None)
            picked = [pick["rev"]] if pick else []
        known = {e["rev"] for e in entries}
        picked = [] if follow else [r for r in picked if r in known or _git(top, "rev-parse", "--verify", "--quiet", r)]
        if not picked:
            picked = [(mine or (checkouts[0] if checkouts else {"rev": "HEAD"}))["rev"]]
        for r in picked:  # (a branch from a link, a tag: listed too while it's shown)
            if r not in known and r != "HEAD":
                entries.append({"rev": r, "name": r, "sha": None, "upstream": None, "ahead": 0, "behind": 0,
                                "gone": False, "worktree": None, "label": r})
        rev = picked[0]
        b = next((e for e in entries if e["rev"] == rev), None) if len(picked) == 1 else None
        upstream = b["upstream"] if b else None
        parent = git_parent_branch(top, rev, upstream) if b and b["name"] else None
        revs = ["--branches", "--remotes", "--tags"] if every else \
            picked if len(picked) > 1 else [rev] + ([parent["name"]] if parent else [])
        commits = git_log(top, revs, first_parent=not every)
        found = git_find(top, rev, then, revs, commits, every) if then else None
        own = theirs = None
        if parent and not every:
            own = set((_git(top, "rev-list", "--max-count=5000", f"{parent['name']}..{rev}") or "").split())
            theirs = set((_git(top, "rev-list", "--max-count=5000", f"{rev}..{parent['name']}") or "").split())
        repo = github_repo(top)
        AVATARS.recall(commits, repo)
        chat = chat_git_events(transcript) if transcript else {}
        branches = git_branch_list(top, entries, mine["rev"] if mine else None, chat,
                                   {rev: parent["name"]} if b and parent else None)
        return {"top": top, "folder": folder, "checkouts": checkouts, "branches": branches, "rev": rev,
                "picked": picked, "branch": b, "parent": parent, "commits": commits, "own": own, "theirs": theirs,
                "repo": repo, "gh_refresh": gh, "fetched": fetched, "pr": None, "every": every, "found": found,
                "revs": revs, "chat": chat, "then": then, "mine": mine["rev"] if mine else None}

    # ---- drawing (main loop)
    def loaded(self, token, res):
        if token != self.token:
            return False  # (a newer load is coming)
        self.refresh_btn.set_loading(False)
        self.state = res
        self.top = res["top"]
        while (row := self.list.get_first_child()) is not None:
            self.list.remove(row)
            discard(row)
        if not self.top:
            self.where.set_label(pretty_path(res["folder"] or ""))
            self.info.set_label("Not in a git repository.")
            self.hours.set_visible(False)
            self.picker.set_visible(False)
            self.more.set_visible(False)
            return False
        self.picker.set_visible(True)
        self.hours.set_visible(True)
        self.where.set_label(pretty_path(self.top))
        self.checkouts, self.revs, self.chat = res["checkouts"], res["picked"], res["chat"]
        self.branches_later = (res["branches"], res["picked"])
        if not self.branch_list.get_visible():  # (open, it's what you're choosing: read again once it's closed)
            self.fill_branches(*self.branches_later)
        markup = self.info_markup(res)
        self.info.set_markup(markup)
        self.info.set_visible(bool(markup))
        self.commits = res["commits"]
        shown = (tuple(res["picked"]), res["every"])
        if shown != getattr(self, "opened_for", None):  # (other branches: their merges closed)
            self.opened, self.opened_for = {}, shown
        if res["gh_refresh"]:
            AVATARS.again()
        if res.get("fetched") is False:
            toast(self.win, "Couldn't fetch: the remotes' branches are as last fetched")
        self.fill(self.commits)
        self.more.set_visible(len(self.commits) >= GIT_LOG_LIMIT)
        if (found := res.get("found")) and found["merge"]:  # (in a merge: opened, then highlighted)
            self.highlight_after = found["sha"]
            if found["merge"] not in self.opened:
                self.open_merge(next(c for c in self.commits if c["sha"] == found["merge"]))
            else:
                self.highlight(found["sha"])
        elif found:
            self.highlight(found["sha"])
        elif res.get("then"):  # (not in this branch's history: its card)
            self.show_commit(res["then"])
        self.read_github(token, res)
        return False

    def hover(self, ref):
        """A commit or branch the pointer is on in the chat (a resolved Signal ref), or None: its row (a branch's tip)
        lit while it's there, if this history has it. Nothing scrolls: only what's already in view shows it."""
        if (row := getattr(self, "hover_row", None)) is not None:
            row.remove_css_class("git-hover")
            self.hover_row = None
        if ref is None or not self.top or (ref.data.get("repo") and
                                           os.path.realpath(ref.data["repo"]) != os.path.realpath(self.top)):
            return
        sha = ref.data.get("sha") if ref.kind == "commit" else None
        branch = (ref.data.get("ref") or "").removeprefix("refs/heads/").removeprefix("refs/remotes/") \
            if ref.kind == "branch" else None
        if not (sha or branch):
            return
        row = self.list.get_first_child()
        while row is not None:
            c = getattr(row, "commit", None)
            if c and (sha and c["sha"].startswith(sha) or branch and
                      branch in (r.removeprefix("HEAD -> ").removeprefix("tag: ") for r in c.get("refs") or ())):
                row.add_css_class("git-hover")
                self.hover_row = row
                return
            row = row.get_next_sibling()

    def highlight(self, sha):
        """A commit's row picked out and scrolled into the middle of the view (a link to it was clicked)."""
        self.highlight_after = None
        self.show_list()
        found = None
        row = self.list.get_first_child()
        while row is not None:
            row.remove_css_class("git-found")
            if getattr(row, "commit", {}).get("sha") == sha:
                found = row
            row = row.get_next_sibling()
        if found is None:
            return
        found.add_css_class("git-found")

        def scroll(tries=[0]):
            tries[0] += 1
            ok, point = found.compute_point(self.list, Graphene.Point().init(0, 0))
            if not ok or found.get_height() == 0:
                return tries[0] < 30  # (not laid out yet)
            adj = self.list_scroll.get_vadjustment()
            adj.set_value(max(0, point.y - (adj.get_page_size() - found.get_height()) / 2))
            return False
        GLib.timeout_add(16, scroll)

    def read_github(self, token, res):
        """GitHub's part of the branch, after its history is shown: its pull request (gh signed in only)."""
        b, repo = res["branch"], res["repo"]
        if not (repo and b and b["name"]):
            return

        def work():
            if gh_state(refresh=res["gh_refresh"])[0] != "signed_in":
                return
            prs = _gh_json("pr", "list", "--repo", repo, "--head", b["name"], "--state", "all", "--limit", "1",
                           "--json", "number,title,state,url")
            if isinstance(prs, list) and prs:
                GLib.idle_add(self.github_read, token, prs[0])
        GH_POOL.submit(work)

    def github_read(self, token, pr):
        if token == self.token:
            self.state["pr"] = pr
            markup = self.info_markup(self.state)
            self.info.set_markup(markup)
            self.info.set_visible(bool(markup))
        return False

    def info_markup(self, res):
        """What the branch tracks and came from, and what GitHub can add."""
        e, b, p, lines = html.escape, res["branch"], res["parent"], []
        if res["every"]:
            lines.append("Every branch, remote branch and tag")
        if b and b["upstream"]:
            track = "gone from the remote" if b["gone"] else git_ahead_behind(b["ahead"], b["behind"]) or "up to date"
            lines.append(f"Tracks <b>{e(b['upstream'])}</b>: {e(track)}")
        if p:
            lines.append(f"From <b>{e(p['name'])}</b> at <tt>{e(p['base'][:7])}</tt>: "
                         f"{e(git_ahead_behind(p['ahead'], p['behind']) or 'up to date')}")
        if pr := res["pr"]:  # (only with gh signed in; signed out, there are links and nothing more)
            lines.append(f'Pull request <a href="{e(pr["url"])}">#{pr["number"]}</a> {e(pr["title"][:60])} '
                         f'({e(pr["state"].lower())})')
        return "\n".join(lines)

    def fill(self, commits):
        """The log: in a branch's history (first parent only), a merge's own commits under it once it's opened."""
        shown, opened = [], self.opened
        for c in commits:
            if self.state.get("every") or len(c["parents"]) < 2:
                shown.append((c, c, False))
            elif c["sha"] in opened and opened[c["sha"]] is not None:
                shown.append((c, c, True))
                shown += [(m, m, False) for m in opened[c["sha"]]]
            else:  # (its lanes: as if it had only its first parent, the merged commits not being shown)
                shown.append(({**c, "parents": c["parents"][:1]}, c, True))
        rows = git_lanes([drawn for drawn, _c, _m in shown])
        cols = max([1] + [max([r["col"]] + [c for c, _ in r["through"] + r["out"]]) + 1 for r in rows])
        own, theirs = self.state.get("own"), self.state.get("theirs")
        for (_drawn, c, merge), lane in zip(shown, rows):
            self.list.append(self.row(c, lane, cols, dim=bool(theirs and c["sha"] in theirs),
                                      own=bool(own and c["sha"] in own), merge=merge))
        self.hours.set_hours(git_hours(commits)[0])
        self.hours.set_visible(bool(commits))
        AVATARS.ask(self, [c for _drawn, c, _merge in shown], self.state.get("repo"))

    def avatar_came(self, emails, picture):
        """An author's picture has been read: it goes on their rows, which stay as they are otherwise."""
        row = self.list.get_first_child()
        while row is not None:
            if (c := getattr(row, "commit", None)) and c["email"].lower() in emails:
                row.avatar.set_custom_image(picture)
            row = row.get_next_sibling()

    def refill(self):
        while (row := self.list.get_first_child()) is not None:
            self.list.remove(row)
            discard(row)
        self.fill(self.commits)

    def open_merge(self, c):
        """Show a merge's own commits under it (the branch it merged, from where it left), or hide them again."""
        if c["sha"] in self.opened:
            del self.opened[c["sha"]]
            self.refill()
            return
        self.opened[c["sha"]] = None
        token, top, repo = self.token, self.top, self.state.get("repo")

        def work():
            merged = git_log(top, [c["parents"][1], f"^{c['parents'][0]}"], limit=GIT_MERGE_LIMIT)
            AVATARS.recall(merged, repo)
            GLib.idle_add(self.merge_read, token, c["sha"], merged)
        GIT_POOL.submit(work)

    def merge_read(self, token, sha, merged):
        if token == self.token and sha in self.opened:
            self.opened[sha] = merged
            self.refill()
            if getattr(self, "highlight_after", None):
                self.highlight(self.highlight_after)
        return False

    def row(self, c, lane, cols, dim=False, own=False, merge=False):
        row = Gtk.ListBoxRow(activatable=True)
        row.commit = c
        made = git_chat_commit(self.chat, c["sha"])
        box = Gtk.Box(spacing=6)
        box.append(GitLane(lane, cols, chat=bool(made), dim=dim))
        # (their initials on a colour; their picture, already if it's known, so a redrawn row doesn't flicker)
        row.avatar = Adw.Avatar(size=AVATAR_SIZE, text=c["author"], show_initials=True, valign=Gtk.Align.CENTER,
                                custom_image=AVATARS.pictures.get(c["email"].lower()))
        box.append(row.avatar)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, margin_top=4, margin_bottom=4)
        first = Gtk.Box(spacing=4)
        for ref in c["refs"]:
            first.append(self.chip(ref))
        if made:
            chip = Gtk.Label(label="this chat")
            chip.add_css_class("git-ref")
            chip.add_css_class("chat")
            first.append(chip)
        subject = Gtk.Label(label=c["title"], xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        if own:
            subject.add_css_class("git-own")
        first.append(subject)
        text.append(first)
        meta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        meta.add_css_class("caption")
        say_time(meta, c["at"], after=f" · {html.escape(c['author'])}" + git_delta_markup(c), markup=True)
        meta.set_opacity(0.75)
        text.append(meta)
        box.append(text)
        if merge:  # (a merge in a branch's history: its own commits, on asking)
            opened = c["sha"] in self.opened
            btn = Gtk.Button(icon_name="pan-down-symbolic" if opened else "pan-end-symbolic", valign=Gtk.Align.CENTER,
                             tooltip_text="Hide the commits it merged" if opened else "Show the commits it merged")
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_: self.open_merge(c))
            box.append(btn)
        if dim:
            text.set_opacity(0.55)
            row.avatar.set_opacity(0.55)
        row.set_child(box)
        row.set_tooltip_text(f"{c['sha'][:9]} · {c['title']}" +
                             (f"\n{c['subject']}" if c["title"] != c["subject"] else "") +  # (a pull request's name)
                             (f"\nMade by this chat: {made['subject']}" if made else ""))
        return row

    @staticmethod
    def chip(ref):
        label = ref.removeprefix("tag: ").replace("HEAD -> ", "")
        chip = Gtk.Label(label=label, ellipsize=Pango.EllipsizeMode.END, max_width_chars=22)
        chip.add_css_class("git-ref")
        chip.add_css_class("tag" if ref.startswith("tag: ") else "head" if ref.startswith("HEAD") else
                           "remote" if "/" in ref and not ref.startswith("HEAD") and ref.split("/")[0] in ("origin", "upstream")
                           else "local")
        return chip

    def load_more(self):
        res, skip = self.state, len(self.commits)
        token = self.token

        def work():
            more = git_log(res["top"], res["revs"], skip=skip, first_parent=not res["every"])
            AVATARS.recall(more, res["repo"])
            GLib.idle_add(self.more_loaded, token, more)
        GIT_POOL.submit(work)

    def more_loaded(self, token, more):
        if token == self.token:
            self.commits = self.commits + more
            self.refill()  # (the lanes are worked out from the top again)
            self.more.set_visible(len(more) >= GIT_LOG_LIMIT)
        return False

    # ---- a commit's card
    def show_list(self):
        self.current = None
        self.set_visible_child_name("list")

    def show_commit(self, sha):
        """A commit's card: git's part at once (its message and files), then the branches that have it and GitHub's
        part (its author there, its pull requests) as they come, each read in the background and kept (GIT_CARDS)."""
        top = self.top
        if not top:
            return
        self.current = sha
        self.detail_title.set_text(sha[:9])
        body = self.detail_body
        while (c := body.get_first_child()) is not None:
            body.remove(c)
            discard(c)
        self.set_visible_child_name("detail")
        d = self.read_card(top, sha)
        if d is not None:
            self.commit_loaded(sha, d)
        else:
            body.append(empty_label("Reading the commit…"))

    def read_card(self, top, sha):
        """A commit's card, if it's been read (None: it's being read; card_read draws it when it is)."""
        key = (top, sha)
        if key in GIT_CARDS:
            d = GIT_CARDS[key]
            if d and (time.time() - d["read"] > GIT_CARD_FRESH or d.get("gh") != _GH_STATE.get("state")):
                d["read"] = time.time()  # (the branches and pull requests, read again: later, or signed in or out)
                self.read_card_more(top, sha, d)
            return d
        GIT_CARDS[key] = None
        repo = self.state.get("repo")

        def work():
            d = git_commit_card(top, sha)
            GLib.idle_add(self.card_read, top, sha, d)
            if d:
                self.read_card_more(top, sha, d, repo)
        GIT_POOL.submit(work)
        return None

    def read_card_more(self, top, sha, d, repo=None):
        """The branches that have a commit (git: a third of a second in a big repository), then GitHub's part: its
        author's login and its pull requests, asked at once."""
        repo = repo or self.state.get("repo")

        def branches():
            refs = (_git(top, "for-each-ref", "--contains", d["sha"], "--format=%(refname)", "refs/heads",
                         "refs/remotes") or "").split()
            d["branches"] = [r.removeprefix("refs/heads/") for r in refs if r.startswith("refs/heads/")]
            d["remotes"] = [r.removeprefix("refs/remotes/") for r in refs if r.startswith("refs/remotes/")
                            and not r.endswith("/HEAD")]
            if repo and d["remotes"]:
                d["url"] = f"https://github.com/{repo}/commit/{d['sha']}"
            GLib.idle_add(self.card_more, sha, d)
            d["gh"] = gh_state() if repo and d["remotes"] else _GH_STATE.get("state")
            if repo and d["remotes"] and d["gh"][0] == "signed_in":
                GH_POOL.submit(login)
                GH_POOL.submit(prs)

        def login():
            on_github = _gh_json("api", f"repos/{repo}/commits/{d['sha']}")
            if isinstance(on_github, dict) and (on_github.get("author") or {}).get("login"):
                d["login"] = on_github["author"]["login"]
                GLib.idle_add(self.card_more, sha, d)

        def prs():
            found = _gh_json("api", f"repos/{repo}/commits/{d['sha']}/pulls")
            d["prs"] = [ref_pr_info(p) for p in found[:10]] if isinstance(found, list) else []
            GLib.idle_add(self.card_more, sha, d)
        GIT_POOL.submit(branches)

    def card_read(self, top, sha, d):
        GIT_CARDS[(top, sha)] = d or False
        if self.current == sha and self.top == top:
            self.commit_loaded(sha, d)
        return False

    def commit_loaded(self, sha, d):
        if self.current != sha:
            return False
        body, e = self.detail_body, html.escape
        while (c := body.get_first_child()) is not None:
            body.remove(c)
            discard(c)
        if not d:
            body.append(empty_label(f"No commit {sha} here"))
            return False
        self.card = d
        subject, _, rest = d["message"].partition("\n")
        named = git_title(subject, rest)
        if named != subject:  # (a pull request's name above: its message, as it is, below)
            rest = d["message"]
        title = Gtk.Label(label=named, xalign=0, wrap=True, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        title.add_css_class("heading")
        body.append(title)
        self.card_when = sel_label(self.when_markup(d), css="caption")
        body.append(self.card_when)
        made = git_chat_commit(self.chat, d["sha"])
        if made:
            jump = Gtk.Button(label="Made by this chat: show the command", halign=Gtk.Align.START,
                              tooltip_text=made["subject"])
            jump.add_css_class("git-chat-btn")
            jump.connect("clicked", lambda *_: self.jump(made))
            body.append(jump)
        files = d["files"]
        added = sum(a or 0 for _p, a, _r in files)
        removed = sum(r or 0 for _p, _a, r in files)
        total = sel_label(f"<b>{len(files)} file{'s' * (len(files) != 1)}</b>  "
                          f'<span foreground="#26a269">+{added:,}</span> <span foreground="#c01c28">−{removed:,}</span>'
                          + (f'  <span alpha="70%">against first parent {e(d["parents"][0][:7])}</span>'
                             if len(d["parents"]) > 1 else ""))
        body.append(total)
        if files:
            lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            lst.add_css_class("boxed-list")
            biggest = max((a or 0) + (r or 0) for _p, a, r in files) or 1
            for path, a, r in files:
                rowbox = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4, margin_start=8, margin_end=8)
                name = Gtk.Label(label=path, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.START)
                name.add_css_class("mono")
                rowbox.append(name)
                n = Gtk.Label()
                n.add_css_class("caption")
                n.set_markup("binary" if a is None else
                             f'<span foreground="#26a269">+{a}</span> <span foreground="#c01c28">−{r}</span>')
                rowbox.append(n)
                rowbox.append(GitDelta(a or 0, r or 0, biggest))
                row = Gtk.ListBoxRow(activatable=True, child=rowbox)
                row.path = path
                row.set_tooltip_text(f"{path}: the diff in this commit")
                lst.append(row)
            lst.connect("row-activated", lambda _l, row: self.diff(d["sha"], row.path))
            body.append(lst)
        if rest.strip():
            msg = Gtk.Label(label=rest.strip(), xalign=0, wrap=True, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            msg.add_css_class("caption")
            body.append(msg)
        self.card_refs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.append(self.card_refs)
        self.card_actions = Gtk.Box(spacing=6)
        diff = Gtk.Button(label="Open diff", tooltip_text="The whole commit on the Code page")
        diff.connect("clicked", lambda *_: self.diff(d["sha"]))
        self.card_actions.append(diff)
        body.append(self.card_actions)
        self.card_more(sha, d)
        return False

    @staticmethod
    def when_markup(d):
        """The author (their GitHub profile, if they're known there) · when, in full · the commit (on GitHub, if
        pushed)."""
        e = html.escape
        who = f'<a href="{e("https://github.com/" + d["login"])}">{e(d["author"])}</a>' if d.get("login") else \
            e(d["author"])
        short = f"<tt>{e(d['sha'][:9])}</tt>"
        when = f"{who} · {e(git_full_when(d['at']))} · " + (f'<a href="{e(d["url"])}">{short}</a>' if d.get("url")
                                                             else short)
        if d["committer"] != d["author"] or abs(d["ct"] - d["at"]) > 60:  # (rebased, amended or applied by another)
            when += f'\n<span alpha="70%">Committed by {e(d["committer"])} · {e(git_full_when(d["ct"]))}</span>'
        return when

    def card_more(self, sha, d):
        """The card's parts read after it was drawn: its branches, GitHub's link and login, its pull requests."""
        if self.current != sha or getattr(self, "card", None) is not d:
            return False
        e, box = html.escape, self.card_refs
        self.card_when.set_markup(self.when_markup(d))
        while (c := box.get_first_child()) is not None:
            box.remove(c)
            discard(c)
        if d.get("branches") is None:
            box.append(sel_label('<span alpha="70%">On …</span>', css="caption"))
        else:
            on = d["branches"] + d["remotes"]
            box.append(sel_label(f'<span alpha="70%">On {e(", ".join(on[:8]))}' +
                                 (f" and {len(on) - 8} more" if len(on) > 8 else "") + "</span>" if on else
                                 '<span alpha="70%">On no branch (rewritten, or only in the reflog)</span>',
                                 css="caption"))
        for p in d.get("prs") or []:  # (gh signed in only)
            box.append(sel_label(f'<a href="{e(p["url"])}">#{p["number"]}</a> {e(p["title"])} ({e(p["state"])})'))
        if d.get("url") and not getattr(self.card_actions, "github", None):
            gh = self.card_actions.github = pointers.hand(Gtk.Button(label="GitHub ↗", tooltip_text=d["url"]))
            gh.connect("clicked", lambda b: Gtk.UriLauncher(uri=d["url"]).launch(b.get_root(), None, None))
            self.card_actions.append(gh)
        return False

    def diff(self, sha, path=None):
        """A commit's diff (or one file's) on the Code page."""
        top = self.top
        args = ["show", "--format=fuller", "--stat", "--patch", "-M", "--diff-merges=first-parent", sha] + \
            (["--", path] if path else [])
        text = _git(top, *args) or _git(top, *[a for a in args if not a.startswith("--diff-merges")]) or ""
        name = f"{sha[:9]}{'-' + os.path.basename(path) if path else ''}.diff"
        self.win.show_code(os.path.join(top, name), text=text,
                           note=f"{path} in {sha[:9]}" if path else f"Commit {sha[:9]}")

    def jump(self, event):
        """The tool call of this chat's that made a commit: the chat scrolls to it."""
        card = self.win.tool_cards.get(event.get("tool_use_id"))
        if card is not None:
            self.win.jump_to_card(card)
        else:
            toast(self.win, "That command is further up the chat than what's shown: scroll up to load it")


class GitDelta(Gtk.Widget):
    """A file's lines added (green) and removed (red), as a bar scaled to the commit's biggest file."""

    def __init__(self, added, removed, biggest):
        super().__init__(valign=Gtk.Align.CENTER)
        self.added, self.removed, self.biggest = added, removed, biggest
        self.set_size_request(48, 6)

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        green, red, rest = Gdk.RGBA(), Gdk.RGBA(), self.get_color()
        green.parse("#26a269")
        red.parse("#c01c28")
        rest.alpha = 0.12
        snap.append_color(rest, Graphene.Rect().init(0, 0, w, h))
        a = w * self.added / self.biggest
        r = w * self.removed / self.biggest
        snap.append_color(green, Graphene.Rect().init(0, 0, a, h))
        snap.append_color(red, Graphene.Rect().init(a, 0, r, h))


def git_ahead_behind(ahead, behind):
    """2 commits ahead, 1 commit behind (either left out at 0; nothing at all if both are)."""
    commits = lambda n: f"{n} commit{'s' * (n != 1)}"  # noqa: E731
    return ", ".join(x for x in (f"{commits(ahead)} ahead" if ahead else "", f"{commits(behind)} behind" if behind else "")
                     if x)


def git_delta_markup(c):
    """A log row's change: · +12 −3 in 2 files (nothing for a commit that changed nothing)."""
    if not c["files"]:
        return ""
    return (f' · <span foreground="#26a269">+{c["added"]:,}</span> <span foreground="#c01c28">−{c["removed"]:,}</span>'
            f' in {c["files"]} file{"s" * (c["files"] != 1)}')
