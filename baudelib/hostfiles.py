"""hostfiles — what a head elsewhere reads of a session's folder here: its folders listed (list_dir), its git history,
the Git page's whole read, a commit and its diff, and GitHub's pull requests (git), and the names Signal's glossary has
for it (signal_names). Each only inside one of this host's sessions' folders (Server.in_sessions; git's log also of this
host's own checkout, for a changelog). Nothing is written; only git's page and pulls fetch (when asked) and ask gh, and
a card's GitHub part: a phone's Files, Git and Signal pages. No GTK here."""
import json
import os
import re
import sqlite3
import subprocess
import threading

from baudelib import wording

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
        return None, err.decode("utf-8", errors="replace").strip() or text.strip() or \
            wording.say("host_errors.git_failed")
    return text, None


def _inside(server, path, checkout=False):
    """path (absolute) made real, if it's in one of the server's sessions' folders (or, checkout, its own code's)."""
    if not isinstance(path, str) or not os.path.isabs(os.path.expanduser(path)):
        raise ValueError(wording.say("host_errors.not_absolute"))
    real = os.path.realpath(os.path.expanduser(path))
    if server.in_sessions(real):
        return real
    if checkout:
        from baudelib import selfupdate
        here = os.path.realpath(selfupdate.HERE)
        if real == here:
            return real
    raise ValueError(wording.say("host_errors.not_in_session_folder"))


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


REPO = re.compile(r"[\w.-]+/[\w.-]+")
HEX = re.compile(r"[0-9a-fA-F]{4,64}")
PAGE_STEP = 50        # the page's commits are asked and given in steps of this many
PAGE_LIMIT = 1000     # the most commits a page has at once
CARD_PRS = 10         # the most pull requests a card has


def _valid_revs(revs):
    """The strings of revs that are revisions (a ^sha too), never an option."""
    return [r for r in revs if isinstance(r, str) and r and not r.startswith("-")] if isinstance(revs, list) else None


def _signed_in(fresh=False):
    import gitread
    return gitread.gh_state(refresh=fresh)[0] == "signed_in"


def _pulls(server, msg):
    """pulls {cwd, repo?, head?, people?, fresh?}: the repository's pull requests (repo: owner/name, else the folder's
    own) as gh last read them (read now if due, fresh: whether or not): {repo, prs, gh}; with head or people and gh
    signed in too: signed_in, pr (the branch head's pull request) and avatars (the repository's authors' pictures)."""
    import gitread
    import lookup
    cwd = _inside(server, msg.get("cwd"))
    repo = msg.get("repo") if isinstance(msg.get("repo"), str) and REPO.fullmatch(msg["repo"]) else None
    if repo is None:
        top, _ = _git(cwd, "rev-parse", "--show-toplevel")
        repo = gitread.github_repo(top.strip()) if top else None
    if repo is None:
        return {"what": "pulls", "repo": None, "prs": None, "gh": False}
    fresh = bool(msg.get("fresh"))
    prs = lookup.PULLS.read(repo, fresh=fresh)
    reply = {"what": "pulls", "repo": repo, "prs": list(prs.values()) if prs is not None else None,
             "gh": lookup.gh_path() is not None and not lookup.PULLS.signed_out()}
    head = msg.get("head") if isinstance(msg.get("head"), str) and not msg["head"].startswith("-") else None
    if head or msg.get("people"):
        reply["signed_in"] = signed = _signed_in(fresh)
        if signed and head:
            found = gitread._gh_json("pr", "list", "--repo", repo, "--head", head, "--state", "all", "--limit", "1",
                                     "--json", "number,title,state,url")
            if isinstance(found, list) and found and isinstance(found[0], dict):
                p = found[0]
                reply["pr"] = {"number": int(p["number"]), "title": str(p.get("title") or ""),
                               "state": str(p.get("state") or "").lower(), "url": str(p.get("url") or ""), "head": None}
        if signed and msg.get("people"):
            reply["avatars"] = gitread.avatar_list(repo)
    return reply


def _page(server, msg, cwd):
    """page {cwd, session?, follow?, revs?, every?, fetch?, then?, limit?}: the Git page's whole read (gitread.gather,
    as Baude's own page reads it): the branches to choose from and shown, the log with what each commit changed, the
    branch's parent, what this chat's commands did, and the pull requests as last read."""
    import gitread
    import lookup
    session = None
    if isinstance(msg.get("session"), str):
        with server.lock:
            session = server.sessions.get(msg["session"])
    transcript = session.transcript() if session else None
    then = msg.get("then") if isinstance(msg.get("then"), str) and HEX.fullmatch(msg["then"]) else None
    asked = msg.get("limit") if isinstance(msg.get("limit"), int) else PAGE_STEP
    limit = -(-max(1, min(asked, PAGE_LIMIT)) // PAGE_STEP) * PAGE_STEP
    res = gitread.gather(cwd, transcript, _valid_revs(msg.get("revs")) or [], bool(msg.get("follow", True)),
                         bool(msg.get("every")), then, bool(msg.get("fetch")), limit)
    if not res["top"]:
        return {"what": "page", "folder": cwd, "error": wording.say("host_errors.not_a_repo")}
    repo = res["repo"]
    events = (res.get("chat") or {}).get("events", [])
    mine = res["mine"]
    heads = [mine] if mine else []
    for ev in events:
        if ev["kind"] in ("commit", "push") and ev.get("branch") and ev["branch"] not in heads:
            heads.append(ev["branch"])
    opened = []
    for ev in events:
        m = gitread._CHAT_PR_URL.search(ev.get("url") or "") if ev["kind"] == "pr" else None
        if m and repo and m[1].lower() == repo.lower() and int(m[2]) not in opened:
            opened.append(int(m[2]))
    prs = lookup.PULLS.states(repo, want=False) if repo else None
    return {"what": "page", "top": res["top"], "folder": cwd,
            "branches": [{**b, "remote": bool(b.get("remote")), "touched": bool(b.get("touched"))}
                         for b in res["branches"]],
            "picked": res["picked"], "rev": res["rev"], "mine": mine, "branch": res["branch"],
            "parent": res["parent"], "commits": res["commits"], "own": sorted(res["own"] or ()),
            "theirs": sorted(res["theirs"] or ()), "revs": [r for r in res["revs"] if not r.startswith("--")],
            "repo": repo, "every": res["every"], "fetched": res["fetched"], "found": res["found"],
            "more": len(res["commits"]) >= limit,
            "made": [{"sha": e["sha"], "subject": e["subject"], "tool_use_id": e["tool_use_id"],
                      "branch": e.get("branch")} for e in events if e["kind"] == "commit"],
            "heads": heads, "opened": opened,
            "links": [{"repo": r, "number": n} for r, n in
                      (gitread.chat_pr_links(transcript) if transcript else [])],
            "prs": list(prs.values()) if prs is not None else None}


def _card_github(cwd, top, sha):
    """card {github}: a commit's GitHub part: its author's login and its pull requests, with gh signed in."""
    import gitread
    reply = {"what": "card", "sha": sha}
    repo = gitread.github_repo(top)
    if not repo or not HEX.fullmatch(sha) or not _signed_in():
        return reply
    on_github = gitread._gh_json("api", f"repos/{repo}/commits/{sha}")
    if isinstance(on_github, dict) and (on_github.get("author") or {}).get("login"):
        reply["login"] = on_github["author"]["login"]
    found = gitread._gh_json("api", f"repos/{repo}/commits/{sha}/pulls")
    reply["prs"] = [{"number": p.get("number"), "title": p.get("title", ""),
                     "state": "merged" if p.get("merged_at") else p.get("state", ""), "url": p.get("html_url") or "",
                     "head": None} for p in found[:CARD_PRS] if isinstance(p, dict)] if isinstance(found, list) else []
    return reply


def _card(cwd, top, sha):
    """card {sha}: a commit (gitread.git_commit_card), the branches that have it and its page on GitHub."""
    import gitread
    d = gitread.git_commit_card(top, sha)
    if d is None:
        return {"what": "card", "error": wording.say("git.no_commit", sha=sha)}
    refs, _ = _git(top, "for-each-ref", "--contains", d["sha"], "--format=%(refname)", "refs/heads", "refs/remotes",
                   limit=20_000)
    refs = (refs or "").split()
    remotes = [r.removeprefix("refs/remotes/") for r in refs if r.startswith("refs/remotes/") and not r.endswith("/HEAD")]
    repo = gitread.github_repo(top)
    reply = {"what": "card", "sha": d["sha"], "parents": d["parents"], "author": d["author"], "email": d["email"],
             "at": d["at"], "ct": d["ct"], "committer": d["committer"], "message": d["message"],
             "files": [list(f) for f in d["files"][:FILES_LIMIT]], "more_files": len(d["files"]) > FILES_LIMIT,
             "on": [r.removeprefix("refs/heads/") for r in refs if r.startswith("refs/heads/")],
             "remotes": remotes, "login": d["login"]}
    if repo and remotes:
        reply["url"] = f"https://github.com/{repo}/commit/{d['sha']}"
    return reply


def git(server, msg):
    """git {cwd, what, ...}: log (rev?, limit?, skip?; or revs / every, first_parent): the branch (its name, upstream,
    ahead and behind) and its commits, newest first; card (sha): a commit, its files (+ and −), message, branches and
    GitHub page, or with github, GitHub's part of it (gh signed in); diff (sha, path?): its diff (its first DIFF_LIMIT);
    page: the Git page's whole read (fetch: git fetch first and GitHub read afresh); pulls: GitHub's pull requests, asked
    of gh. Only page (fetch) and pulls fetch or ask gh; the rest read only."""
    what = msg.get("what")
    if what == "pulls":
        return _pulls(server, msg)
    cwd = _inside(server, msg.get("cwd"), checkout=what == "log")
    if what == "page":
        return _page(server, msg, cwd)
    top, err = _git(cwd, "rev-parse", "--show-toplevel")
    if top is None:
        return {"what": what, "error": wording.say("host_errors.not_a_repo")}
    if what == "log":
        limit = max(1, min(int(msg.get("limit") or 50), LOG_LIMIT))
        skip = max(0, int(msg.get("skip") or 0))
        revs = _valid_revs(msg.get("revs"))
        if revs is not None or msg.get("every"):
            import gitread
            revs = ["--branches", "--remotes", "--tags"] if msg.get("every") else revs
            commits = gitread.git_log(top.strip(), revs, limit, skip, first_parent=bool(msg.get("first_parent")))
            return {"what": what, "top": top.strip(), "commits": commits, "skip": skip, "more": len(commits) >= limit}
        rev = msg.get("rev") if isinstance(msg.get("rev"), str) and not msg["rev"].startswith("-") else "HEAD"
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
        return _card_github(cwd, top.strip(), sha) if msg.get("github") else _card(cwd, top.strip(), sha)
    if what == "diff":
        path = msg.get("path") if isinstance(msg.get("path"), str) else None
        text, err = _git(cwd, "show", "--format=", sha, "--", *([path] if path else []), limit=DIFF_LIMIT + 1)
        if text is None:
            return {"what": what, "error": err}
        return {"what": what, "sha": sha, "path": path, "text": text[:DIFF_LIMIT], "truncated": len(text) > DIFF_LIMIT}
    return {"what": what, "error": "git takes log, card, diff, page or pulls"}


def signal_names(server, msg):
    """signal_names {cwd, session?}: the names Signal's glossary has for the folder (the chat's own mentions only,
    session given: its id there), read as they are: [{surface, kind, canonical, status, summary, reason, data, locations}],
    the glossary's path and the checkout's top (root, what the locations' paths are from). Nothing's looked up or written: that's Signal's, where Baude runs it."""
    import lookup  # (no GTK in it)
    cwd = _inside(server, msg.get("cwd"))
    place = lookup.signal_place(cwd)
    db = os.path.join(place.store, "signal.db")
    if not os.path.exists(db):
        return {"glossary": None, "root": place.top or cwd, "refs": []}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as e:
        return {"glossary": db, "root": place.top or cwd, "refs": [], "error": str(e)}
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
            locs = [dict(zip(("role", "path", "line", "note", "rev", "state", "commit_sha", "branch"), r))
                    for r in con.execute("SELECT role, path, line, note, rev, state, commit_sha, branch FROM location "
                                         "WHERE entity_id = ? LIMIT 20", (eid,))]
            ref = lookup.Ref(surface, kind, status or "pending", canonical, reason or "",
                             json.loads(data) if data else {}, locs)
            refs.append({"surface": surface, "kind": kind, "canonical": ref.canonical, "status": ref.status,
                         "summary": ref.summary(), "reason": ref.reason, "data": ref.data, "locations": locs})
    except (sqlite3.Error, ValueError) as e:
        return {"glossary": db, "root": place.top or cwd, "refs": [], "error": str(e)}
    finally:
        con.close()
    return {"glossary": db, "root": place.top or cwd, "refs": refs}
