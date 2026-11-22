"""gitread: what the Git page reads with git and gh: the log, its lanes, the branches, a commit's card, what a chat's
commands did, the pull requests it touches, and who made each commit. No GTK here: Baude's Git page (gitview) and a
host's git answers (baudelib.hostfiles) both use it."""
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse

import lookupgit
from baudelib import wording
from baudelib.transcripts import ref_epoch
from lookupgit import _git


def _gh_json(*args, timeout=20):
    """A `gh` call's JSON answer (GET only), or None if gh is missing, offline or says no."""
    gh = lookupgit.gh_path()
    if gh is None:
        return None
    try:
        p = subprocess.run([gh, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return json.loads(p.stdout) if p.returncode == 0 else None
    except ValueError:
        return None


AVATAR_LIST_KEEP = 600          # a repository's commits' authors, used again for this long
AVATAR_QUIET = 300              # nothing read (gh can't say): not asked again for this long
_AVATAR_LISTS, _AVATAR_LOCK = {}, threading.Lock()


def avatar_list(repo):
    """{email: picture URL} of a repository's recent commits (one GET, kept for AVATAR_LIST_KEEP), or {} if gh can't say."""
    with _AVATAR_LOCK:
        until, known = _AVATAR_LISTS.get(repo, (0, None))
        if until > time.time():
            return known or {}
        found = _gh_json("api", f"repos/{repo}/commits?per_page=100")
        known = avatar_urls(found) if isinstance(found, list) else None
        _AVATAR_LISTS[repo] = (time.time() + (AVATAR_QUIET if known is None else AVATAR_LIST_KEEP), known)
        return known or {}


def git_fetch(top):
    """Every remote fetched (the Git page's refresh: origin/… where it is now), True if it went. Never a password asked:
    no terminal and no prompt, only what git has already (its credentials, a keychain's, an agent's), and given up on
    after a minute."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "SSH_ASKPASS_REQUIRE": "never", "GCM_INTERACTIVE": "never"}
    try:
        return subprocess.run([lookupgit.git_exe(), "-C", top, "fetch", "--all", "--quiet"], env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                              start_new_session=True).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def github_repo(top):
    """owner/name of the GitHub repository `origin` points at, if it does."""
    url = (_git(top, "remote", "get-url", "origin") or "").strip()
    m = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None



GIT_LOG_LIMIT = 50       # commits a page of the log shows (Show more adds as many again)


GIT_MERGE_LIMIT = 200    # a merge's own commits shown when it's opened


GIT_FIND_LIMIT = 5000    # commits read on, at most, to show one a link points at


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


def github_login(email):
    """The GitHub user a commit's email names, when it's GitHub's noreply one (12345+naza@users.noreply.github.com,
    naza@users.noreply.github.com), else None."""
    m = re.fullmatch(r"(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com", email or "")
    return m[1] if m else None


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
                        "label": wording.say("git.detached_at", sha=(t.get('sha') or '')[:7], folder=here)})
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
    gh = lookupgit.gh_path()
    if gh is None:
        state = ("missing", None)
    else:
        try:
            ok = subprocess.run([gh, "auth", "status", "--hostname", "github.com"], stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=20).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        user = _gh_json("api", "user") if ok else None
        state = ("signed_in", (user or {}).get("login")) if ok else ("signed_out", None)
    _GH_STATE.update(state=state, at=time.time())
    return state


AVATAR_ASK = 40                 # the size asked of GitHub (twice that: sharp on a high-resolution screen)


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


_CHAT_PRS = {}  # transcript -> (bytes read, what chat_pr_links found in them)


_CHAT_PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")


# (a link to one, or a bare #12, as widgets' _BARE_PR has it)
_CHAT_PR_NAMED = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)|(?<![\w&#/.-])#(\d{1,7})(?![\w-])")


_CHAT_PR_CODE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.S)  # (code: a colour's #333 isn't one)


def chat_pr_links(path):
    """The pull requests a chat's replies name, in the order they're named: [(owner/name, number)] for a link to one,
    (None, number) for a bare #12 (the chat's repository's, if it has one by that number). Read on from where it was as
    the transcript grows."""
    try:
        size = os.path.getsize(path)
    except (OSError, TypeError):
        return []
    done, found = _CHAT_PRS.get(path, (0, []))
    if done > size:  # (written afresh)
        done, found = 0, []
    if done == size:
        return found
    found = list(found)
    try:
        with open(path, "rb") as f:
            f.seek(done)
            for raw in f:
                if not raw.endswith(b"\n"):
                    break  # (a line being written: read with the rest, next time)
                done += len(raw)
                if b'"assistant"' not in raw or not (b"/pull/" in raw or re.search(rb"#\d", raw)):
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                content = (rec.get("message") or {}).get("content")
                for b in content if isinstance(content, list) else ():
                    if isinstance(b, dict) and b.get("type") == "text":
                        found += [(m[1], int(m[2])) if m[2] else (None, int(m[3]))
                                  for m in _CHAT_PR_NAMED.finditer(_CHAT_PR_CODE.sub(" ", str(b.get("text") or "")))]
    except OSError:
        pass
    _CHAT_PRS[path] = (done, found)
    return found


def git_touched_pulls(prs, repo, heads, opened=(), links=()):
    """The pull requests a chat touches, of prs (lookupgit.PULLS: {number: pull_info}): those from the branches it
    worked on (heads: their names) and those it opened (opened: their numbers), the latest first, then those its
    replies name (links: chat_pr_links'), the last named first. [pull_info]."""
    heads = set(heads)
    own = {n for n in opened if n in prs} | {p["number"] for p in prs.values() if p.get("head") in heads}
    out, seen = sorted((prs[n] for n in own), key=lambda p: -p["number"]), set(own)
    for r, n in reversed(list(links)):
        if (r is None or r.lower() == (repo or "").lower()) and n in prs and n not in seen:
            seen.add(n)
            out.append(prs[n])
    return out


def git_pull_heads(prs):
    """{branch: the pull request from it}, of prs (lookupgit.PULLS'), the latest if there are several (a fork's left
    out: its branch isn't this repository's)."""
    heads = {}
    for p in sorted((prs or {}).values(), key=lambda p: p["number"]):
        if p.get("head"):
            heads[p["head"]] = p
    return heads


def gather(folder, transcript, picked, follow, every, then=None, gh=False, limit=GIT_LOG_LIMIT):
    """picked: the branches to show (several: their histories together), else (or follow) the ones the chat
    touched, those above the list's rule (git_branch_list's touched; none: the first checkout's). limit: the log's
    commits read."""
    top = (_git(folder, "rev-parse", "--show-toplevel") or "").strip() if folder else ""
    if not top:
        return {"top": None, "folder": folder}
    fetched = git_fetch(top) if gh else None  # (asked for, the refresh button: the remotes' branches as they are now)
    checkouts = git_checkouts(top)
    entries = checkouts + git_remote_branches(top)
    mine = next((c for c in checkouts if c["worktree"] == os.path.realpath(top)), None)
    picked = [r for r in picked or () if r]
    chat = chat_git_events(transcript) if transcript else {}
    branches = git_branch_list(top, entries, mine["rev"] if mine else None, chat) if follow or not picked else None
    touched = [e["rev"] for e in branches or () if e["touched"]]
    along = None  # (a commit's: the branch of those shown whose own line it's looked for along)
    if then and not picked:  # (a commit: the branches this chat touched if one has it, else a checkout with it)
        has = set((_git(top, "for-each-ref", "--contains", then, "--format=%(refname:short)", "refs/heads")
                   or "").split())
        along = next((r for r in ([mine["rev"]] if mine else []) + touched if r in has), None)
        pick = None if along else next((c for c in checkouts if c["name"] in has), None)
        picked = touched if along else [pick["rev"]] if pick else []
    known = {e["rev"] for e in entries}
    picked = [] if follow else [r for r in picked if r in known or _git(top, "rev-parse", "--verify", "--quiet", r)]
    if not picked:
        if branches is None:  # (the branches picked are gone)
            branches = git_branch_list(top, entries, mine["rev"] if mine else None, chat)
            touched = [e["rev"] for e in branches if e["touched"]]
        picked = touched or [checkouts[0]["rev"] if checkouts else "HEAD"]
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
    commits = git_log(top, revs, limit=limit, first_parent=not every)
    found = git_find(top, along if along in picked else rev, then, revs, commits, every) if then else None
    own = theirs = None
    if parent and not every:
        own = set((_git(top, "rev-list", "--max-count=5000", f"{parent['name']}..{rev}") or "").split())
        theirs = set((_git(top, "rev-list", "--max-count=5000", f"{rev}..{parent['name']}") or "").split())
    repo = github_repo(top)
    if branches is None:
        branches = git_branch_list(top, entries, mine["rev"] if mine else None, chat,
                                   {rev: parent["name"]} if b and parent else None)
    return {"top": top, "folder": folder, "checkouts": checkouts, "branches": branches, "rev": rev,
            "picked": picked, "branch": b, "parent": parent, "commits": commits, "own": own, "theirs": theirs,
            "repo": repo, "gh_refresh": gh, "fetched": fetched, "pr": None, "every": every, "found": found,
            "revs": revs, "chat": chat, "then": then, "mine": mine["rev"] if mine else None, "limit": limit}
