"""lookupgit — git and GitHub for lookup: running git (_git), the pull requests and issues of the GitHub
repositories a folder's checkout came from (GITHUB), and where each pull request stands (PULLS), read with the gh
CLI. Nothing here needs GTK.
"""
import itertools
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse

from baudelib import wording
from fixes import gh_path  # (the gh CLI, or None: BAUDE_GH, for tests)


def git_exe():
    """The git to run. On macOS, /usr/bin/git is a stand-in that asks xcrun where the developer tools' git is on every
    run, which doubles what a git command costs (15 ms rather than 7): that git is asked for once and run directly."""
    if not hasattr(git_exe, "path"):
        git_exe.path = shutil.which("git") or "git"
        if sys.platform == "darwin" and git_exe.path == "/usr/bin/git":
            try:
                found = subprocess.run(["xcrun", "--find", "git"], capture_output=True, text=True, timeout=10,
                                       stdin=subprocess.DEVNULL).stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                found = ""
            if found and os.access(found, os.X_OK):
                git_exe.path = found
    return git_exe.path


_GIT_WAITS = threading.local()  # (what a thread waits for before each git it runs: git_wait)


def git_wait(wait):
    """Before each git this thread runs, wait() (Signal's worker: till the chats are still)."""
    _GIT_WAITS.wait = wait


def _git(cwd, *args, ok=(0,), raw=False, timeout=20):
    """git's output (text, or bytes if raw), or None if it failed or git isn't there. It's started by posix_spawn, not
    forked: in cwd by -C, and no fds closed (Python's own are close-on-exec), as Python needs for that. A fork holds
    Python's lock 8 ms or more in a process Baude's size, and the window waited for each git a thread started."""
    if cwd is not None and not os.path.isdir(cwd):
        return None  # (as cwd= had it: no such folder)
    if (wait := getattr(_GIT_WAITS, "wait", None)) is not None:
        wait()
    try:
        p = subprocess.run([git_exe(), *(("-C", cwd) if cwd is not None else ()), *args], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=timeout, close_fds=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode not in ok:
        return None
    return p.stdout if raw else p.stdout.decode("utf-8", "replace")


def github_icon(issue, state):
    """A pull request's logo (the branch's, green while it's open, purple once merged, grey if it was closed without
    being merged) or an issue's (a #, grey once closed): REF_ICON_SVG."""
    if issue:
        return "issue_closed" if state == "closed" else "issue"
    return {"merged": "pr_merged", "closed": "pr_closed"}.get(state, "pr_open")


# ---------------------------------------------------------------- GitHub's pull requests and issues
# With the gh CLI signed in, the pull requests and issues of the GitHub repositories a folder's checkout came from are
# names too (its origin's and its upstream's): the latest few and the open ones, in ⌘K and @, and # in Record's notes
# (items, SearchSpace.issues). GITHUB reads them on a thread of its own, keeps a copy of each on disk for the next start,
# and only ever asks with GET. Nothing at all happens without gh, signed out (gh api says so before it asks GitHub
# anything), or in a folder with no remote on GitHub.
#
# A look costs nothing while nothing's changed: asked again with the ETag of its last answer, GitHub answers 304, which
# doesn't count against the rate limit. So a repository in use is looked at about once a minute, while a window's shown
# (meshui.watch_github), with one request: the latest of everything, sorted by when it changed, which is what any
# change changes. Only when that's changed is the list of open ones asked for too: two requests a change. An answer with
# no ETag (no free lunch on offer) is asked again every 5 minutes.
#
# A folder has the repository it's in, not those of checkouts under it: a vendored checkout isn't what a chat's about.

GITHUB_LOOK_S = 60  # a repository in use is looked at this often
GITHUB_PLAIN_S = 300  # (or, with no ETag to ask with, this often)
GITHUB_LOOKS_A_MINUTE = 6  # (each a gh run: many repositories in use are each looked at less often, this many in all)
GITHUB_REPOS = 24  # the most looked at, shared by the folders in use (GitHub._in_use)
GITHUB_RECENT, GITHUB_OPEN = 50, 100  # what's fetched of a repository: the latest of everything, the latest open
GITHUB_KEEP = 100  # (under this many requests left this hour, it waits for the next: they're for your other work too)
GITHUB_OUT_S, GITHUB_GONE_S = 600, 3600  # how long till it asks again: signed out; a repository GitHub won't show
GITHUB_TIMEOUT_S = 30
GITHUB_BACKOFF_S = 1800  # (offline, or GitHub in trouble: 2, 4, 8… minutes between tries, up to this)
GITHUB_SCAN_S = 300  # how often the checkout a folder's in is found again
_GITHUB_REMOTE = re.compile(r"(?:^|[/@])(?:www\.)?github\.com[:/]([\w-][\w.-]*/[\w-][\w.-]*?)(?:\.git)?/?$")
GITHUB_NAME = re.compile(r"(?<![\w./-])([\w.-]+/[\w.-]+)#(\d+)\b")  # (a pull request's or an issue's: owner/name#12)


def github_remotes(checkout):
    """The GitHub repositories (owner/name) a checkout came from, as configured (not as an insteadOf rewrites them):
    its origin's and its upstream's (a fork's issues are mostly its parent's), else the first it has."""
    named = {}
    for line in (_git(checkout, "config", "--get-regexp", r"^remote\..*\.url$", timeout=5) or "").splitlines():
        key, _, url = line.partition(" ")
        if (m := _GITHUB_REMOTE.search(url.strip())) and ".." not in m[1]:
            named.setdefault(key[len("remote."):-len(".url")], m[1])
    wanted = [named[n] for n in ("origin", "upstream") if n in named] or list(named.values())[:1]
    return list(dict.fromkeys(wanted))


def github_checkout(folder):
    """The git checkout a folder's in (its top), or None."""
    return (_git(folder, "rev-parse", "--show-toplevel", timeout=5) or "").strip() or None


def _gh_get(path, etag=None):
    """GitHub's answer to a GET of path: (its status, or None if none came; {its headers, lower case}, its body, gh's
    exit code, or None if it didn't finish). gh api -i prints what GitHub answered whatever it was, but exits 1 on all
    but a 2xx, a 304 too; and 4, having asked no one, if it's signed out."""
    exe = gh_path()
    if not exe:
        return None, {}, b"", None
    args = [exe, "api", "--hostname", "github.com", "--method", "GET", "-i", "-H", "Accept: application/vnd.github+json"]
    args += ["-H", f"If-None-Match: {etag}"] * bool(etag)
    try:
        p = subprocess.run([*args, path], stdin=subprocess.DEVNULL, capture_output=True, timeout=GITHUB_TIMEOUT_S,
                           env={**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"})
    except (OSError, subprocess.TimeoutExpired):
        return None, {}, b"", None
    m = re.match(rb"HTTP/[\d.]+ (\d+)", p.stdout)
    if not m:
        return None, {}, b"", p.returncode
    head, *body = re.split(rb"\r?\n\r?\n", p.stdout, maxsplit=1)
    headers = {}
    for line in head.decode("utf-8", "replace").splitlines()[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return int(m[1]), headers, body[0] if body else b"", p.returncode


def github_kept(p):
    """What's kept of an issue or a pull request as GitHub's REST API says it: /issues lists both, a pull request saying
    so, and whether it's been merged, under pull_request; /pulls says that of it directly."""
    pr = "pull_request" in p or "merged_at" in p
    return {"number": p["number"], "title": re.sub(r"\s+", " ", p.get("title") or "").strip()[:300], "pr": pr,
            "state": "merged" if (p.get("pull_request") or p).get("merged_at") else
            "closed" if p.get("state") == "closed" else "open",
            "draft": bool(p.get("draft")), "updated": p.get("updated_at") or ""}


def github_data(listed):
    """What a link (a Ref's data) says of a pull request or an issue GITHUB lists."""
    return {"number": listed["number"], "repo": listed["repo"], "url": listed["url"], "title": listed["title"],
            "state": listed["state"], "issue": not listed["pr"]}


def github_url(surface):
    """The page of owner/name#12, a pull request or an issue (GitHub takes either to the right one), if owner/name is a
    repository that's been read: anything else might be spelt so (a branch called feature/login#12); else None."""
    m = GITHUB_NAME.fullmatch(surface)
    return f"https://github.com/{m[1]}/issues/{m[2]}" if m and GITHUB.has_read(m[1]) else None


def github_listing(repo, kept):
    """An item of lookup's (items) for a pull request or an issue kept (github_kept). Its name is owner/name#12: the
    same number in two repositories is two names."""
    number, pr, state = kept["number"], kept["pr"], kept["state"]
    what = {"merged": wording.say("lookup.merged_pull_request"),
            "closed": wording.say("lookup.closed_pull_request")}.get(state) or \
        (wording.say("lookup.draft_pull_request") if kept["draft"] else wording.say("lookup.pull_request")) if pr else \
        wording.say("lookup.closed_issue") if state == "closed" else wording.say("lookup.issue")
    url = f"https://github.com/{repo}/{'pull' if pr else 'issues'}/{number}"
    surface = f"{repo}#{number}"
    entry = {"kind": "pr" if pr else "issue", "hint": None, "sources": [(what, repo)],
             "github": {**kept, "repo": repo, "url": url}}
    return {"surface": surface, "canonical": surface, "faint": False, "icon": github_icon(not pr, state), "what": what,
            "summary": f"{kept['title']} · {repo}", "kind": entry["kind"], "path": None, "line": None, "entry": entry,
            "label": str(number)}


class GitHub:
    """The pull requests and issues of the GitHub repositories in the folders in use, read on a thread of its own (one
    for the app: GITHUB). look(folders), from the main loop, asks; what's read is swapped in whole, listed(folder) the
    same list till it changes (and generation goes up)."""

    NOTHING = ()

    def __init__(self):
        self.generation = 0
        self._todo, self._thread, self._one = queue.SimpleQueue(), None, threading.Lock()
        self.forget()

    def forget(self):
        """Nothing known, nothing in the way of the next look (in memory: the copies on disk stay)."""
        with self._one:
            self._lists, self._repos, self._folders = {}, {}, {}
            self._paused, self._fails, self._out = 0.0, 0, False
            self.generation += 1

    # ---- main loop (it never waits for any of this)
    def listed(self, folder):
        """The items (github_listing) of the repositories in a folder, as they were last read."""
        return self._lists.get(folder, self.NOTHING)

    def known(self, repo, number):
        """What's been read of repo's number (a listing's entry's "github"), or None."""
        for it in (self._repos.get(repo) or {}).get("listing", ()):
            if it["entry"]["github"]["number"] == number:
                return it["entry"]["github"]
        return None

    def own_repository(self, folder):
        """The repository a folder's checkout came from, as far as it's been looked at, or None: what a bare #12 in a
        chat in that folder can mean."""
        return next((k["own"] for k in list(self._folders.values()) if folder in k["keys"] and k["own"]), None)

    def has_read(self, repo):
        """Whether a repository's been read: here, or on another day (what's kept of it)."""
        return bool((self._repos.get(repo) or {}).get("etags")) or os.path.exists(self._path(repo))

    def look(self, folders=()):
        """Look at the repositories in use, if it's time to: at once, not waiting for it. Nothing without gh. folders:
        those in use (a chat's: the repository each is in)."""
        folders = [f for f in folders if f]
        if not folders or gh_path() is None:
            return
        self._todo.put(folders)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="lookup-github")
            self._thread.start()

    # ---- its thread
    def _run(self):
        while True:
            folders = self._todo.get()
            try:
                while True:
                    folders = folders + self._todo.get_nowait()
            except queue.Empty:
                pass
            try:
                self.round(folders)
            except Exception as e:  # noqa: BLE001 (whatever it was: asked again at the next look)
                print(f"lookup: reading GitHub failed: {e!r}", file=sys.stderr)

    def round(self, folders=()):
        """A look at the repositories in use (look): each once it's due, while GitHub can be asked. What's kept on disk
        of one not seen before is listed at once."""
        if gh_path() is None:
            return
        with self._one:
            repos = self._in_use(folders)
            for repo in repos:
                if repo not in self._repos:
                    self._repos[repo] = self._load(repo)
            self._publish()
            every = max(GITHUB_LOOK_S, 60 * len(repos) / GITHUB_LOOKS_A_MINUTE)
            for repo in repos:
                r, now = self._repos[repo], time.time()
                if now < self._paused:
                    break
                if now >= r["paused"] and now - r["looked"] >= (every if r["etags"].get("recent") else GITHUB_PLAIN_S):
                    self._read(repo, r)
                    self._publish()

    def _in_use(self, folders):
        """The repositories in use, as many as are looked at, shared out: each folder's own first, then the rest of
        each's (an upstream) in turn."""
        each = [self._in_folder(f)["repos"] for f in dict.fromkeys(folders)]
        order = [repos[0] for repos in each if repos]
        for turn in itertools.zip_longest(*(repos[1:] for repos in each)):
            order += [r for r in turn if r]
        return list(dict.fromkeys(order))[:GITHUB_REPOS]

    def _in_folder(self, folder):
        """The repositories of the checkout a folder's in, found again every so often (what's published of it, kept:
        nothing's changed unless they have)."""
        known = self._folders.get(folder)
        if known is None or time.time() - known["at"] > GITHUB_SCAN_S:
            top = github_checkout(folder)
            repos = github_remotes(top) if top else []
            known = self._folders[folder] = {**(known or {}), "repos": repos[:GITHUB_REPOS],
                                             "own": repos[0] if repos else None, "at": time.time(),
                                             "keys": {folder, top} - {None}}  # (top: a Names' root)
        return known

    def _read(self, repo, r):
        """One look: the latest of everything, and if that's changed (or wasn't read as far as that last time) the open
        ones too."""
        r["looked"] = time.time()
        recent = self._get(repo, r, "recent", f"state=all&sort=updated&direction=desc&per_page={GITHUB_RECENT}")
        if recent == "no issues":  # (a 410: issues switched off, its pull requests still there)
            r["pulls"], r["etags"] = True, {}
            recent = self._get(repo, r, "recent", f"state=all&sort=updated&direction=desc&per_page={GITHUB_RECENT}")
        if recent == "changed":
            r["open_due"] = True
        if recent in ("changed", "same") and r["open_due"] and \
                self._get(repo, r, "open", f"state=open&sort=updated&direction=desc&per_page={GITHUB_OPEN}") in \
                ("changed", "same"):
            r["open_due"] = False

    def _get(self, repo, r, which, query):
        """Ask for one list: "changed" (and it's taken in), "same", "gone" (GitHub won't show this repository), "no
        issues" (it says they're switched off) or "failed". What the answer says of GitHub as a whole goes for every
        repository: signed out, offline, the rate limit."""
        path = f"repos/{repo}/{'pulls' if r['pulls'] else 'issues'}?{query}"
        status, head, body, code = _gh_get(path, r["etags"].get(which))
        now = time.time()
        if code == 4 or status == 401:
            self._out, self._paused = True, now + GITHUB_OUT_S
            return "failed"
        if status is None or status >= 500:  # (offline, or GitHub in trouble)
            self._fails += 1
            self._paused = now + min(GITHUB_BACKOFF_S, GITHUB_LOOK_S * 2 ** self._fails)
            return "failed"
        self._fails, self._out = 0, False
        left = head.get("x-ratelimit-remaining", "")
        try:
            reset = float(head.get("x-ratelimit-reset") or 0)
            wait = float(head.get("retry-after") or 0)
        except ValueError:
            reset = wait = 0.0
        if status in (403, 429) and (wait or left == "0"):
            self._paused = max(now + wait, reset, now + GITHUB_LOOK_S)
            return "failed"
        if left.isdigit() and int(left) < GITHUB_KEEP:
            self._paused = max(self._paused, reset)
        if status == 304:
            return "same"
        if status == 410 and not r["pulls"]:
            return "no issues"
        if status != 200:  # (404: not there, or not yours to see; 403: not with this sign-in)
            r["paused"] = now + GITHUB_GONE_S
            if status == 404:
                r.update(listing=[], recent=[], open=[], etags={})
                try:
                    os.remove(self._path(repo))
                except OSError:
                    pass
            return "gone"
        try:
            kept = [github_kept(p) for p in json.loads(body)]
        except (ValueError, TypeError, KeyError, AttributeError):
            return "failed"
        same = kept == r[which]  # (a new ETag, as a reaction makes, and nothing here to show for it)
        r[which], r["etags"][which] = kept, head.get("etag") or ""
        if not same:
            r["listing"] = self._listing(repo, r)
        self._save(repo, r)
        return "same" if same else "changed"

    @staticmethod
    def _listing(repo, r):
        """What's listed of a repository: its two lists as one, the latest first."""
        latest = {}
        for it in sorted(r["recent"] + r["open"], key=lambda k: k["updated"]):
            latest[it["number"]] = it  # (in both: the one that says it changed last)
        return [github_listing(repo, it) for it in sorted(latest.values(), key=lambda k: k["updated"], reverse=True)]

    def _publish(self):
        """What's listed for each folder as things are: nothing while signed out. A folder's list is a new one only if
        it's changed."""
        changed = False
        for folder in self._folders.values():
            parts = [] if self._out else [self._repos[r]["listing"] for r in folder["repos"] if r in self._repos]
            if folder.get("parts") is None or len(parts) != len(folder["parts"]) or \
                    any(a is not b for a, b in zip(parts, folder["parts"])):
                folder["parts"] = parts
                made = tuple(it for part in parts for it in part)
                for key in folder["keys"]:
                    if made or key in self._lists:
                        self._lists[key] = made
                        changed = True
        if changed:
            self.generation += 1

    # ---- the copy on disk
    @staticmethod
    def _path(repo):
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        return os.path.join(base, "baude", "github", urllib.parse.quote(repo, safe="") + ".json")

    def _load(self, repo):
        r = {"listing": [], "recent": [], "open": [], "etags": {}, "looked": 0.0, "paused": 0.0, "open_due": False,
             "pulls": False}
        try:
            with open(self._path(repo), encoding="utf-8") as f:
                kept = json.load(f)
            listing = self._listing(repo, kept)
            r.update(recent=kept["recent"], open=kept["open"], etags=dict(kept["etags"]), listing=listing,
                     pulls=bool(kept.get("pulls")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass  # (none, or not what's written here)
        return r

    def _save(self, repo, r):
        path = self._path(repo)
        new = f"{path}.{os.getpid()}.new"  # (Baude and Record may both be at it)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(new, "w", encoding="utf-8") as f:
                json.dump({"recent": r["recent"], "open": r["open"], "etags": r["etags"], "pulls": r["pulls"]}, f)
            os.replace(new, path)
        except OSError:
            pass  # (only the next start's wait)


GITHUB = GitHub()


# ---------------------------------------------------------------- where each pull request stands
# The Git page's list of the pull requests a chat touches and its pills on the branches that have one, and the pills a
# reply's #12s and pull request links are drawn as, are each in the colour of where the pull request stands: a draft,
# open, changes asked for (a review's verdict, which holds it up), merged, or closed without being merged. GITHUB's
# lists don't say a review's verdict, nor the branch a pull request is from, so this asks gh pr list for them: the
# latest PULLS_LIMIT of a repository in one call, on a thread of its own, at most every PULLS_FRESH_S while something
# shows them (never one call a pull request), and keeps the answer on disk for the next start. Without gh, or signed
# out, it has nothing: no list, no pills.

PULLS_FRESH_S = 120  # a repository's pull requests are read again after this, if they're still shown
PULLS_LIMIT = 100  # the latest of them read (a number older than these: what GITHUB's lists say of it, if anything)
PULLS_FIELDS = "number,title,state,isDraft,reviewDecision,headRefName,isCrossRepository,url"


def pull_state(p):
    """Where a pull request stands, from gh pr list --json: "merged", "closed" (without being merged), "changes" (a
    review asked for changes: it can't go in till they're made, draft or not), "draft" or "open"."""
    state = (p.get("state") or "").upper()
    if state in ("MERGED", "CLOSED"):
        return state.lower()
    if (p.get("reviewDecision") or "").upper() == "CHANGES_REQUESTED":
        return "changes"
    return "draft" if p.get("isDraft") else "open"


def pull_info(p):
    """What's kept of a pull request gh pr list says: {number, title, state (pull_state), url, head}. head: the branch
    it's from, None if that's a fork's (another repository's branch of that name isn't this one's)."""
    return {"number": int(p["number"]), "title": re.sub(r"\s+", " ", p.get("title") or "").strip()[:300],
            "state": pull_state(p), "url": p.get("url") or "",
            "head": None if p.get("isCrossRepository") else (p.get("headRefName") or None)}


class PullStates:
    """Where the pull requests of each GitHub repository asked about stand (one for the app: PULLS). states(repo), from
    the main loop, is what was read last (or kept from the last start), asking for a read if one's due and never
    waiting for it; read(repo), on a worker thread, waits. generation goes up when what's known changes."""

    def __init__(self):
        self._cond, self._todo, self._thread = threading.Condition(), queue.SimpleQueue(), None
        self.forget()

    def forget(self):
        """Nothing known (in memory: the copies on disk stay)."""
        with self._cond:
            self._repos, self.generation = {}, getattr(self, "generation", 0) + 1

    def _entry(self, repo):  # (with _cond held)
        r = self._repos.get(repo)
        if r is None:
            r = self._repos[repo] = {"prs": None, "at": 0.0, "paused": 0.0, "out": False, "busy": False,
                                     "queued": False}
            try:
                with open(self._path(repo), encoding="utf-8") as f:
                    kept = json.load(f)
                r.update(prs={int(p["number"]): p for p in kept["prs"]}, at=float(kept["at"]),
                         out=bool(kept.get("out")))  # (signed out last time: nothing, till gh says otherwise)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass  # (none kept, or not what's written here)
        return r

    @staticmethod
    def _due(r, now):
        return now >= r["paused"] and now - r["at"] >= PULLS_FRESH_S

    # ---- main loop (it never waits for gh)
    def states(self, repo, want=True):
        """{number: pull_info} of a repository's pull requests as last read, or None: none read yet, no gh, or gh
        signed out. want: and a read asked for if one's due."""
        if not repo or gh_path() is None:
            return None
        with self._cond:
            r = self._entry(repo)
            prs = None if r["out"] else r["prs"]
            want = want and not (r["queued"] or r["busy"]) and self._due(r, time.time())
        if want:
            self.want(repo)
        return prs

    def pull(self, repo, number):
        """What's known of repo's pull request number: its pull_info; else, for one older than those read, what
        GITHUB's lists say (where it stands, not a review's verdict, nor its branch); else None. None too for an issue,
        and with nothing read of repo (states' None)."""
        prs = self.states(repo)
        if prs is None:
            return None
        if (p := prs.get(number)) is not None:
            return p
        k = GITHUB.known(repo, number)
        if k is None or not k.get("pr"):
            return None
        state = "draft" if k["state"] == "open" and k.get("draft") else k["state"]
        return {"number": number, "title": k.get("title", ""), "state": state,
                "url": f"https://github.com/{repo}/pull/{number}", "head": None}

    def signed_out(self):
        """Has gh said it's signed out (a read of any repository's)?"""
        with self._cond:
            return any(r["out"] for r in self._repos.values())

    def want(self, repo, fresh=False):
        """A read of repo's pull requests asked for, on this one's thread, if one's due (fresh: whether or not)."""
        if not repo or gh_path() is None:
            return
        with self._cond:
            r = self._entry(repo)
            if r["queued"] or r["busy"] or not (fresh or self._due(r, time.time())):
                return
            r["queued"] = True
        self._todo.put((repo, fresh))
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="lookup-pulls")
            self._thread.start()

    def look(self, repos):
        """The repositories shown, of those asked about before: each read again if it's due (a window's tick)."""
        for repo in repos:
            if repo and repo in self._repos:
                self.want(repo)

    # ---- worker threads
    def _run(self):
        while True:
            repo, fresh = self._todo.get()
            with self._cond:
                self._repos.get(repo, {})["queued"] = False
            try:
                self.read(repo, fresh)
            except Exception as e:  # noqa: BLE001 (whatever it was: asked again when it's next due)
                print(f"lookup: reading pull requests failed: {e!r}", file=sys.stderr)

    def read(self, repo, fresh=False):
        """(Not the main loop.) repo's pull requests, read now if it's due (fresh: whether or not), else as last read:
        states' answer. A read already under way is waited for, not made twice."""
        if not repo or gh_path() is None:
            return None
        with self._cond:
            r = self._entry(repo)
            while r["busy"]:
                self._cond.wait(GITHUB_TIMEOUT_S + 5)
            go = r["busy"] = fresh or self._due(r, time.time())  # (fresh: asked for, signed in again perhaps)
        if go:
            try:
                self._fetch(repo, r)
            finally:
                with self._cond:
                    r["busy"] = False
                    self._cond.notify_all()
        with self._cond:
            return None if r["out"] else r["prs"]

    def _fetch(self, repo, r):
        exe, code, out, err = gh_path(), None, "", ""
        try:
            p = subprocess.run([exe, "pr", "list", "--repo", repo, "--state", "all", "--limit", str(PULLS_LIMIT),
                                "--json", PULLS_FIELDS], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=GITHUB_TIMEOUT_S, errors="replace",
                               env={**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"})
            code, out, err = p.returncode, p.stdout, p.stderr
        except (OSError, subprocess.TimeoutExpired, TypeError, ValueError):
            pass  # (no answer: as offline)
        prs = None
        if code == 0:
            try:
                prs = {(i := pull_info(p))["number"]: i for p in json.loads(out)}
            except (ValueError, TypeError, KeyError, AttributeError):
                prs = None
        now = time.time()
        with self._cond:
            if prs is not None:
                changed = r["out"] or prs != r["prs"]
                r.update(prs=prs, at=now, paused=0.0, out=False)
            elif code == 4 or "gh auth login" in err:  # (signed out: gh asked no one)
                changed = not r["out"]
                r.update(out=True, at=now, paused=now + GITHUB_OUT_S)
                self._save(repo, now, {}, out=True)  # (what was read goes: the next start shows nothing either)
            else:  # (offline, GitHub in trouble, or a repository it won't show: what was read stays)
                changed = False
                r["paused"] = now + (GITHUB_GONE_S if "Could not resolve" in err else GITHUB_LOOK_S * 5)
            if changed:
                self.generation += 1
        if prs is not None:
            self._save(repo, now, prs)

    # ---- the copy on disk
    @staticmethod
    def _path(repo):
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        return os.path.join(base, "baude", "github", urllib.parse.quote(repo, safe="") + ".pulls.json")

    def _save(self, repo, at, prs, out=False):
        path = self._path(repo)
        new = f"{path}.{os.getpid()}.new"  # (Baude and Record may both be at it)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(new, "w", encoding="utf-8") as f:
                json.dump({"at": at, "prs": list(prs.values()), "out": out}, f)
            os.replace(new, path)
        except OSError:
            pass  # (only the next start's wait)


PULLS = PullStates()
