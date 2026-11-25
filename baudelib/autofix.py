"""Auto-fix: a chat's host following the pull request of the chat's branch for it. Turned on (from a head's Git page,
or by the chat's agent: mcp__chat__autofix), the host finds the open pull request of the branch checked out in the
chat's folder (gh pr list --head), and every EVERY seconds while it's on reads where it stands (gh pr view, and the
review comments on its lines: gh api). A check newly failing, a merge conflict, or a comment, review or review comment
made since (not one made by the host's gh login during one of the chat's turns: its own agent's, through gh) is sent
the chat, from the sender Auto-fix, between its turns (the words of those only the repository's owner, members and
collaborators write: anyone else's only that they commented): one message at a time, what's found while one waits added to it. It goes off by itself
once the pull request is merged or closed. Kept in the chat's meta on its host ("autofix"), so it goes on across the
host's restarts with nobody attached; listed (Session's autofix) so every head shows it."""
import calendar
import json
import os
import subprocess
import threading
import time
import uuid

from baudelib import deps, wire, wording

EVERY = int(os.environ.get("BAUDE_AUTOFIX_EVERY") or 5 * 60)  # (seconds: how often a pull request is read;
# BAUDE_AUTOFIX_EVERY: sooner, to try it)
AGAIN = 60  # (seconds: one that couldn't be read, read again this soon, then twice as long after each that couldn't)
AGAIN_MAX = 60 * 60  # (seconds: the longest it waits after one that couldn't be read)
STOP_AFTER = 3  # (reads in a row that gh's signed out for: Auto-fix turned off, said in the chat)
PAD = 2  # (seconds: a comment this near a turn of the chat's is the chat's own, GitHub's clock and this one's apart)
TURNS_KEPT = 50  # (the chat's turns kept, to tell its own comments by)
SEEN_KEPT = 500  # (the comments' ids kept as seen)
BODY_MAX = 4000  # (characters of a comment put in the message)
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}  # (whose words are sent the chat; anyone else's, only that they commented)
FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
VIEW = "number,title,url,state,mergeable,baseRefName,headRefOid,statusCheckRollup,comments,reviews"


class SignedOut(ValueError):
    """gh isn't signed in: for its user to fix, not waited out."""


def sender(me):
    """Who its messages are from: Auto-fix, on this host (me: the host's {id, name})."""
    return wire.clean_sender({"kind": "host", "name": wording.say("autofix.title"), "host": me["id"],
                              "host_name": me["name"]})


def gh(*args, timeout=30):
    """gh's JSON answer; deps.Missing if gh isn't here, ValueError with the last line it said if it says no."""
    import lookupgit  # (here: the Git page's, read as it's first wanted)
    path = lookupgit.gh_path()
    if path is None:
        raise deps.Missing(deps.info("gh", here=False), "autofix.title")
    try:
        p = subprocess.run([path, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValueError(f"gh {args[0]} {args[1]} took over {timeout} s") from None
    if p.returncode != 0:
        said = [line.strip() for line in (p.stderr or p.stdout or "").splitlines() if line.strip()]
        if p.returncode == 4 or any("gh auth login" in line for line in said):
            raise SignedOut(wording.say("autofix.signed_out"))
        raise ValueError(said[-1] if said else f"gh exited with {p.returncode}")
    try:
        return json.loads(p.stdout)
    except ValueError:
        raise ValueError("gh's answer isn't JSON") from None


_login = []  # (the host's gh login, read once: login)


def login():
    """Who the host's gh is signed in as (gh api user), read once."""
    if not _login:
        got = gh("api", "user")
        if not isinstance(got, dict) or not got.get("login"):
            raise ValueError("gh didn't say who it's signed in as")
        _login.append(str(got["login"]))
    return _login[0]


def find(cwd):
    """The open pull request of the branch checked out in cwd: {repo, number, url, title, branch}; ValueError (or
    deps.Missing) saying why there's none."""
    import gitread
    import lookupgit
    top = (lookupgit._git(cwd, "rev-parse", "--show-toplevel") or "").strip()
    if not top:
        raise ValueError(wording.say("host_errors.not_a_repo"))
    repo = gitread.github_repo(top)
    if not repo:
        raise ValueError(wording.say("autofix.not_github"))
    branch = (lookupgit._git(top, "symbolic-ref", "--short", "-q", "HEAD") or "").strip()
    if not branch:
        raise ValueError(wording.say("autofix.no_branch"))
    got = gh("pr", "list", "--repo", repo, "--head", branch, "--state", "open", "--limit", "30",
             "--json", "number,title,url,headRepositoryOwner")
    owner = repo.split("/")[0].lower()
    p = next((p for p in got if isinstance(p, dict) and
              str((p.get("headRepositoryOwner") or {}).get("login") or "").lower() == owner), None) \
        if isinstance(got, list) else None  # (not a fork's branch of the same name)
    if p is None:
        raise ValueError(wording.say("autofix.no_pr", branch=branch))
    return {"repo": repo, "number": int(p["number"]), "url": str(p.get("url") or ""),
            "title": str(p.get("title") or "")[:300], "branch": branch}


def started(pr, now):
    """Auto-fix as it's kept, just turned on for pr (find's): read at once, every check failing then is new."""
    return {**pr, "since": now, "next": now, "failing": [], "conflict": False, "seen": [], "turns": [],
            "waiting": [], "error": None}


def listing(af):
    """What heads are told of it (the wire's AutoFix), or None while it's off."""
    if not af:
        return None
    return {"repo": af["repo"], "number": af["number"], "url": af.get("url") or "", "title": af.get("title") or "",
            "error": af.get("error")}


def read(af):
    """Where af's pull request stands, as gh says: (its view, the review comments on its lines, newest first)."""
    view = gh("pr", "view", str(af["number"]), "--repo", af["repo"], "--json", VIEW)
    lines = gh("api", f"repos/{af['repo']}/pulls/{af['number']}/comments?per_page=100&sort=created&direction=desc")
    return view, lines if isinstance(lines, list) else []


def _when(stamp):
    """GitHub's time (2026-11-25T10:00:00Z) in seconds since the epoch, or None."""
    try:
        return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


def _check(c):
    """A check's (name, failed, url), GitHub's check run or status alike."""
    name = c.get("name") or c.get("context") or "?"
    failed = (c.get("conclusion") or c.get("state") or "").upper() in FAILED
    return name, failed, c.get("detailsUrl") or c.get("targetUrl") or ""


def _mine(at, turns):
    return at is not None and any(a - PAD <= at <= b + PAD for a, b in turns)


def look(af, view, lines, now, me=None, began=None):
    """af after a read of its pull request (view, lines: read's; me: the host's gh login; began: when the chat's turn
    going on began, None while it's between turns): (af as it's kept now, or None once the pull request is merged or
    closed; what's new on it, each {kind: check, name, url} or {kind: conflict, base} or {kind: comment, id, who, url,
    body?, path?, state?, outside?}). A comment's id is kept as seen once it's sent (send) or it's the chat's agent's:
    me's, made during one of the chat's turns."""
    if str(view.get("state") or "").upper() in ("MERGED", "CLOSED"):
        return None, []
    found = []
    failing = []
    for c in view.get("statusCheckRollup") or []:
        if isinstance(c, dict):
            name, failed, url = _check(c)
            key = f"{name} {view.get('headRefOid') or url}"  # (failing again after a push: new)
            if failed and key not in failing:
                failing.append(key)
                if key not in af["failing"]:
                    found.append({"kind": "check", "name": name, "url": url})
    conflict = str(view.get("mergeable") or "").upper() == "CONFLICTING"
    if conflict and not af["conflict"]:
        found.append({"kind": "conflict", "base": view.get("baseRefName") or ""})
    seen, said = set(af["seen"]), []
    waiting = list(af.get("waiting") or [])
    held = {f.get("id") for f in waiting}
    turns = list(af["turns"]) + ([[began, now + 60]] if began is not None else [])
    comments = [("comment", c, c.get("createdAt"), c.get("url")) for c in view.get("comments") or []] + \
        [("review", c, c.get("submittedAt"), c.get("url")) for c in view.get("reviews") or []] + \
        [("line", c, c.get("created_at"), c.get("html_url")) for c in lines]
    for kind, c, stamp, url in comments:
        if not isinstance(c, dict) or c.get("id") is None:
            continue
        cid = f"{kind}:{c['id']}"
        at = _when(stamp)
        if cid in seen or cid in held or at is None or at < af["since"] - PAD:
            continue
        body = (c.get("body") or "").strip()
        state = str(c.get("state") or "").upper()
        if kind == "review" and not body and state != "CHANGES_REQUESTED":
            continue  # (a review that's only its line comments: those are said each)
        who = ((c.get("author") or c.get("user") or {}).get("login")) or "?"
        if me and who == me and _mine(at, turns):
            seen.add(cid)
            said.append(cid)
            continue
        item = {"kind": "comment", "id": cid, "who": who, "url": url or ""}
        if str(c.get("authorAssociation") or c.get("author_association") or "").upper() in TRUSTED:
            item["body"] = body[:BODY_MAX]
            if kind == "line" and c.get("path"):
                item["path"] = c["path"]
            if kind == "review":
                item["state"] = state.lower().replace("_", " ")
        else:
            item["outside"] = True
        found.append(item)
    kept = {**af, "failing": failing, "conflict": conflict, "seen": (af["seen"] + said)[-SEEN_KEPT:],
            "title": str(view.get("title") or af.get("title") or "")[:300], "url": view.get("url") or af.get("url"),
            "error": None, "fails": 0, "signed_out": 0, "next": now + EVERY}
    if found:
        kept["waiting"] = waiting + found
    return kept, found


def told(af, found):
    """The message the chat's sent: what needs fixing on its pull request (and who else commented, only that)."""
    asks = [f for f in found if not f.get("outside")]
    lines = [wording.say("autofix.intro" if asks else "autofix.about", number=af["number"], repo=af["repo"]), ""]
    for f in found:
        if f["kind"] == "check":
            lines.append("- " + (wording.say("autofix.failing_at", name=f["name"], url=f["url"]) if f["url"] else
                                 wording.say("autofix.failing", name=f["name"])))
        elif f["kind"] == "conflict":
            lines.append("- " + wording.say("autofix.conflict", base=f["base"] or "main"))
    for f in asks:
        if f["kind"] != "comment":
            continue
        head = wording.say("autofix.review", who=f["who"], state=f["state"]) if f.get("state") else \
            wording.say("autofix.comment_on", who=f["who"], path=f["path"]) if f.get("path") else \
            wording.say("autofix.comment", who=f["who"])
        lines += ["", head + (f" {f['url']}" if f.get("url") else "")]
        lines += ["> " + line for line in (f.get("body") or "").splitlines()] or []
    outside = [f for f in found if f.get("outside")]
    if outside:
        lines += [""] * bool(lines[-1]) + ["- " + wording.say("autofix.outside", who=f["who"]) for f in outside]
    if asks:
        lines += ["", wording.say("autofix.ask")]
    return "\n".join(lines)


def turn_over(s, began, now):
    """(lock held) A turn of s's is over (began then): kept, to tell its own comments on the pull request by."""
    af = s.meta.get("autofix")
    if af and began is not None:
        af["turns"] = (af.get("turns") or [])[-(TURNS_KEPT - 1):] + [[began, now]]


# ---- the host's side (server: baudelib.server.Server)
def request(server, msg):
    """autofix {session, on?}: a chat's Auto-fix turned on (on true: its branch's open pull request found, read at
    once) or off (false), or as it is (on left out); answered with it (AutofixReply), or an error saying why it can't
    be on."""
    sid = msg.get("session")
    with server.lock:
        s = server.sessions.get(sid)
        if s is None:
            raise ValueError(f"no session {sid}")
        on = msg.get("on")
        if on is not False and (s.meta.get("desktop") or sid in server.desktop.chats):
            raise ValueError(wording.say("autofix.not_desktop"))
        cwd = s.meta.get("cwd")
    pr = None
    if on is True:
        pr = find(cwd)  # (not under the lock: git and gh)
    with server.lock:
        if server.sessions.get(sid) is not s or s.deleting:
            raise ValueError(f"no session {sid}")
        af = s.meta.get("autofix")
        if on is True and not (af and af["repo"] == pr["repo"] and af["number"] == pr["number"]):
            s.meta["autofix"] = started(pr, time.time())
        elif on is False:
            s.meta.pop("autofix", None)
        if on is not None:
            server.save(s)
            server.changed()
            server.woken.set()  # (read at once: the watch told)
        return listing(s.meta.get("autofix"))


def due(server):
    """Each chat's Auto-fix whose pull request's due to be read, read (each on a thread of its own: gh takes its time),
    and what's waiting sent to each that's between turns; when the next's due (seconds from now), or None if none's
    on."""
    now, soonest, reads = time.time(), None, []
    with server.lock:
        if server.stopped.is_set():
            return None
        for s in list(server.sessions.values()):
            af = s.meta.get("autofix")
            if not af or s.deleting or s.meta.get("archived") or s.meta.get("desktop"):
                continue
            if af.get("waiting") and not s.busy and af.get("retry", 0) <= now:
                send(server, s)
            if af.get("waiting"):  # (it's at a turn: sent as it's over)
                soonest = 2 if soonest is None else min(soonest, 2)
            if s.id in server.autofix_reading:
                continue
            if af["next"] <= now:
                server.autofix_reading.add(s.id)
                reads.append((s, dict(af)))
            else:
                soonest = af["next"] - now if soonest is None else min(soonest, af["next"] - now)
    for s, af in reads:
        threading.Thread(target=_read, args=(server, s, af), daemon=True, name="autofix").start()
    return 1 if reads else soonest


def _read(server, s, af):
    """A chat's pull request read, and what's new on it kept to send (sent at once, if the chat's between turns)."""
    try:
        me = login()
        view, lines = read(af)
        error = None
    except Exception as e:  # noqa: BLE001 (said on the Git page's toggle, and read again later each time)
        me, view, lines, error = None, None, None, e
    with server.lock:
        server.autofix_reading.discard(s.id)
        now = time.time()
        have = s.meta.get("autofix")
        if server.sessions.get(s.id) is not s or s.deleting or not have or \
                (have["repo"], have["number"]) != (af["repo"], af["number"]):
            return  # (gone, or turned off or onto another meanwhile)
        if error is not None:
            fails = (have.get("fails") or 0) + 1  # (rate limited, offline: tried less and less often)
            signed_out = (have.get("signed_out") or 0) + 1 if isinstance(error, SignedOut) else 0
            server.log(f"auto-fix of {s.id}: {error}")
            if signed_out >= STOP_AFTER:  # (for its user to fix: off, and the chat told)
                s.meta.pop("autofix", None)
                note(server, s, wording.say("autofix.stopped", why=str(error)))
            else:
                s.meta["autofix"] = {**have, "error": str(error), "fails": fails, "signed_out": signed_out,
                                     "next": now + min(AGAIN * 2 ** (fails - 1), AGAIN_MAX)}
        else:
            kept, found = look(have, view, lines, now, me, s.turn_began if s.busy else None)
            if kept is None:
                s.meta.pop("autofix", None)
            else:
                s.meta["autofix"] = kept
            if kept is None:
                server.log(f"auto-fix of {s.id}: #{af['number']} is {str(view.get('state')).lower()}, off")
            elif found:
                server.log(f"auto-fix of {s.id}: new on #{af['number']}: {', '.join(f['kind'] for f in found)}")
                if not s.busy:
                    send(server, s)
        server.save(s)
        if listing(s.meta.get("autofix")) != listing(have):  # (heads told only of a change they show)
            server.changed()
        server.woken.set()


def note(server, s, text):
    """(lock held) A note in s's chat (a system message its apps show as one): to those viewing it now, else held for
    the next to attach (held)."""
    if s.viewers:
        for conn in s.viewers:
            conn.send({"op": "claude", "session": s.id, "msg": {"type": "system", "subtype": "informational",
                                                                "content": text}})
    else:
        s.meta["autofix_note"] = text


def held(server, s, conn):
    """(lock held) A note held for s's chat (note) sent conn, just attached, once its attach is answered."""
    text = s.meta.pop("autofix_note", None)
    if not text:
        return
    server.save(s)

    def later():
        with server.lock:
            conn.send({"op": "claude", "session": s.id, "msg": {"type": "system", "subtype": "informational",
                                                                "content": text}})
    threading.Timer(0.5, later).start()


def send(server, s):
    """(lock held) What's waiting for s sent it, as a message from Auto-fix (one its Desktop writes in now, or that
    can't start its agent, kept for later)."""
    af = s.meta["autofix"]
    found = af.get("waiting") or []
    if not found:
        return
    try:
        server.take_input(None, s, {"type": "user", "uuid": str(uuid.uuid4()),
                                    "message": {"role": "user", "content": told(af, found)}},
                          sender=sender(server.me))
    except Exception as e:  # noqa: BLE001 (tried again at the next read)
        af["error"], af["retry"] = str(e), time.time() + AGAIN
        server.log(f"auto-fix of {s.id} couldn't be sent: {type(e).__name__}: {e}")
        return
    af["seen"] = ((af.get("seen") or []) + [f["id"] for f in found if f.get("id")])[-SEEN_KEPT:]  # (sent: seen)
    af["waiting"], af["retry"] = [], 0
    server.save(s)

