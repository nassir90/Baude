"""hostresume — a chat whose turn a usage limit stopped, carried on by its host once the limit resets. Where the
subscription it runs on has Auto Resume on Reset on (accounts.auto_resume), the turn's end keeps when (resume_at, in
the chat's meta: kept over a restart, listed for every head) and the host sends it MESSAGE then, as its user would.
Anything sent to it first, Cancel (cancel), the switch turned off, or the subscription disabled by hand, and it isn't.
A subscription that disabled itself as it was exhausted (accounts.auto_off) is enabled again as its limit resets
(due), its chats' resumes kept meanwhile. Resumes that keep failing back off (BACKOFF, doubling) and stop (MAX_RUNS)
till a turn succeeds. A task's run's chat isn't resumed: its schedule runs it again.

When a limit's what stopped a turn, and till when, is said by the agent: Claude Code's rate_limit_event refused
(status rejected, resetsAt) or its synthetic message's error with the same, else the time its message says
("resets 3:20pm (Europe/Dublin)"); Codex's turn failing as its usage limit's exceeded, with its last rate-limit
snapshot's windows' resetsAt (limit_reset on the agent's process, set as it reads them).
"""
import re
import time
import uuid

from baudelib import accounts, wording

MARGIN = 60  # (s after the limit resets: its reset is the hour's, a clock a little behind the provider's sends too soon)
MAX_RUNS = 3  # (resumes in a row, with no turn succeeding between, before a chat's let be)
BACKOFF = 300  # (s after a resume's sent before the next may be, doubled for each in a row)
MESSAGE = wording.say("limit_resume.message")


def claude_limit(msg):
    """When the limit a Claude Code message says refused its request resets (seconds since 1970), or None: a
    rate_limit_event whose status is rejected, or the synthetic message written in place of a reply that says so."""
    info = None
    if msg.get("type") == "rate_limit_event":
        info = msg.get("rate_limit_info")
    elif msg.get("type") == "assistant" and msg.get("error") == "rate_limit":
        info = (msg.get("apiErrorParams") or {}).get("rate_limit_info") or msg.get("rate_limit_info")
    if not isinstance(info, dict) or info.get("status") != "rejected":
        return None
    return accounts._reset_time(info.get("resetsAt"))


SAID_RESET = re.compile(r"resets\s+(?:(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}),?\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?"
                        r"\s*(?P<half>am|pm)?\s*\((?P<zone>[A-Za-z_]+(?:/[A-Za-z_+\-0-9]+)*)\)", re.I)


def said_reset(text, now=None):
    """When the limit Claude Code's message says it hit resets ("You've hit your 5-hour limit · resets 3:20pm
    (Europe/Dublin)", "… resets Dec 1, 9am (Europe/Dublin)"): the next such time in that zone, or None."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    m = SAID_RESET.search(text or "") if isinstance(text, str) else None
    if m is None:
        return None
    try:
        zone = ZoneInfo(m["zone"])
    except (KeyError, ValueError):
        return None
    hour = int(m["hour"]) % 12 + (12 if (m["half"] or "").lower() == "pm" else 0) if m["half"] else int(m["hour"])
    if hour > 23:
        return None
    here = datetime.fromtimestamp(now or time.time(), zone)
    try:
        if m["month"]:
            month = datetime.strptime(m["month"], "%b").month
            at = here.replace(month=month, day=int(m["day"]), hour=hour, minute=int(m["minute"] or 0), second=0,
                              microsecond=0)
            if at < here - timedelta(days=1):
                at = at.replace(year=at.year + 1)
        else:
            at = here.replace(hour=hour, minute=int(m["minute"] or 0), second=0, microsecond=0)
            if at < here - timedelta(minutes=5):
                at += timedelta(days=1)
    except ValueError:
        return None
    return at.timestamp()


def codex_reset(snapshot):
    """When a Codex rate-limit snapshot's used-up windows reset (the latest of them; where none says it's at 100%,
    the latest of all): seconds since 1970, or None."""
    if not isinstance(snapshot, dict):
        return None
    windows = [w for w in (snapshot.get("primary"), snapshot.get("secondary")) if isinstance(w, dict)]
    resets = [(w.get("usedPercent") or 0) >= 100 for w in windows]
    pick = [w for w, full in zip(windows, resets) if full] or windows
    times = [t for t in (accounts._reset_time(w.get("resetsAt")) for w in pick) if t is not None]
    return max(times) if times else None


def after(reset):
    """When a chat is resumed (and a subscription enabled again) for a limit that resets at reset: a margin after it."""
    return round(max(reset, time.time()) + MARGIN)


def keep(server, s, reset):
    """(lock held) A chat's resume kept for when its limit resets (reset: seconds since 1970), as its subscription
    resumes its chats and it isn't a task's run's; later where resumes in a row have failed (BACKOFF), none after
    MAX_RUNS. When it's to be sent, or None."""
    key = s.subscription()
    if reset is None or key is None or s.meta.get("ephemeral") or s.meta.get("task_run") or \
            not accounts.auto_resume(key) or not allowed(key):
        return None
    tried = s.meta.get("resumed") or {}
    n = tried.get("n", 0)
    if n >= MAX_RUNS:
        server.log(f"{s.id}: its resume not kept: {n} in a row didn't get a turn through")
        return None
    at = after(reset)
    if n:
        at = max(at, round(tried.get("at", 0) + BACKOFF * 2 ** (n - 1)))
    s.meta["resume_at"] = at
    server.save(s)
    server.changed()
    server.log(f"{s.id}: a usage limit stopped its turn: resumed at {time.ctime(at)}")
    return at


def turn_over(server, s, proc, msg):
    """(lock held, at a turn's result) A turn's end: one that went through clears the resumes in a row; one a usage
    limit stopped has its chat's resume kept for when the limit resets (keep)."""
    reset, proc.limit_reset = getattr(proc, "limit_reset", None), None
    if not msg.get("is_error"):
        if s.meta.pop("resumed", None) is not None:
            server.save(s)
        return
    if reset is None:
        reset = said_reset(msg.get("result"))
    key = s.subscription()
    if reset is None or key is None:
        return
    off = accounts.auto_off(key)
    if off is not None and off["until"] is None and accounts.auto_resume(key):
        accounts.mark_auto_off(key, after(reset))  # (disabled by itself before its limit's reset was known)
    keep(server, s, reset)


def drop(server, s):
    """(lock held) A chat's resume let go of (Cancel, the switch off, its subscription disabled): listed so."""
    if s.meta.pop("resume_at", None) is not None:
        server.save(s)
        server.changed()


def switched(server):
    """(lock held) The subscriptions' switches changed: the resumes of chats whose subscription no longer resumes
    them, or is disabled by hand, let go of."""
    for s in list(server.sessions.values()):
        if s.meta.get("resume_at") is not None and not wanted(s):
            drop(server, s)


def allowed(key):
    """Whether a subscription takes resumes: not disabled, or disabled by itself as it was exhausted (it's enabled
    again as its limit resets)."""
    return not accounts.disabled(key) or accounts.auto_off(key) is not None


def wanted(s):
    """Whether a chat's resume is still to go: its subscription resumes its chats and takes them (allowed)."""
    key = s.subscription()
    return key is not None and accounts.auto_resume(key) and allowed(key)


def due(server):
    """(lock not held: the watch's) Each subscription that disabled itself enabled again as its limit's reset, then
    each chat whose resume's due sent MESSAGE; the seconds till the next of either, or None."""
    now, waits = time.time(), []
    with server.lock:
        if server.stopped.is_set():
            return None
        for key, off in accounts.auto_off().items():
            if off["until"] is None or not accounts.auto_resume(key):
                continue
            if off["until"] > now:
                waits.append(off["until"] - now)
                continue
            accounts.set_switch(key, disabled=False)
            server.log("a subscription's usage limit has reset: enabled again")
            server.subscription_switched()
        for s in list(server.sessions.values()):
            at = s.meta.get("resume_at")
            if not isinstance(at, (int, float)) or isinstance(at, bool):
                continue
            if at > now:
                waits.append(at - now)
                continue
            send(server, s)
    return min(waits) if waits else None


def send(server, s):
    """(lock held) A chat's resume due: MESSAGE sent it as its user's would be, unless something's going in it, or it
    isn't wanted now (wanted), or it's archived or going."""
    drop(server, s)
    if s.busy or s.deleting or s.meta.get("archived") or not wanted(s):
        return server.log(f"{s.id}: its resume not sent: {'busy' if s.busy else 'not wanted now'}")
    try:
        server.take_input(None, s, {"type": "user", "uuid": str(uuid.uuid4()),
                                    "message": {"role": "user", "content": MESSAGE}})
        s.meta["resumed"] = {"n": (s.meta.get("resumed") or {}).get("n", 0) + 1, "at": time.time()}
        server.save(s)
        server.log(f"{s.id}: resumed after its usage limit reset")
    except Exception as e:  # (its agent couldn't be started, its folder gone: said, not sent)
        server.log(f"{s.id}: its resume not sent: {type(e).__name__}: {e}")
