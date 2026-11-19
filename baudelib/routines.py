"""A Bot's routines: work it's given on a schedule, its host process giving it a turn with what to do when one's due
(as if you'd said it), whether or not anyone has Baude open. Each is kept with its Bot (its session's meta), on its
host: {id, name, prompt, every (minutes) or at ("HH:MM", the host's local time) on days (0 Monday … 6 Sunday), paused,
last (when it last ran), next (when it runs next)}. One missed while the host was off runs once as it's back."""
import datetime
import re
import time
import uuid

MIN_EVERY = 5  # (minutes: a routine no more often than this)
MAX_EVERY = 60 * 24 * 31
DAYS = (0, 1, 2, 3, 4, 5, 6)


def clean(r, old=None):
    """A routine as it's given (by a head) made one to keep, or ValueError saying what's wrong with it. old: the same
    routine as it was (its id, when it last ran)."""
    if not isinstance(r, dict):
        raise ValueError("a routine is {name, prompt, every or at}")
    name = " ".join(str(r.get("name") or "").split())[:80]
    prompt = str(r.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("a routine needs what the Bot's to do (prompt)")
    out = {"id": (old or {}).get("id") or str(r.get("id") or "") or uuid.uuid4().hex[:12],
           "name": name or prompt.splitlines()[0][:40], "prompt": prompt[:20000], "paused": bool(r.get("paused")),
           "last": (old or {}).get("last")}
    if r.get("every") not in (None, ""):
        every = int(r["every"])
        if not MIN_EVERY <= every <= MAX_EVERY:
            raise ValueError(f"every is in minutes, {MIN_EVERY} at least")
        out["every"] = every
    elif r.get("at"):
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(r["at"])):
            raise ValueError("at is a time of day, HH:MM")
        days = sorted({int(d) for d in (r.get("days") if r.get("days") is not None else DAYS) if int(d) in DAYS})
        if not days:
            raise ValueError("at needs a day to be on")
        out["at"], out["days"] = "%02d:%s" % (int(str(r["at"]).split(":")[0]), str(r["at"]).split(":")[1]), days
    else:
        raise ValueError("a routine runs every so many minutes (every), or at a time of day (at)")
    out["next"] = next_run(out, time.time())
    return out


def next_run(r, after):
    """When r runs next after after (seconds since the epoch): every's from after; at's the next time of day it's
    on (local)."""
    if r.get("every"):
        return after + r["every"] * 60
    hour, minute = (int(x) for x in r["at"].split(":"))
    day = datetime.datetime.fromtimestamp(after)
    for ahead in range(8):
        when = (day + datetime.timedelta(days=ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when.weekday() in r["days"] and when.timestamp() > after:
            return when.timestamp()
    return after + 86400


def due(r, now):
    return not r.get("paused") and r.get("next") is not None and r["next"] <= now


def said(r, now):
    """What the Bot's told as it runs: its routine's name and when, then what to do."""
    when = datetime.datetime.fromtimestamp(now).strftime("%a %d %b %H:%M")
    return f"(Your routine “{r['name']}”, {when}.)\n\n{r['prompt']}"


def ran(r, now):
    """r after a run at now: its next one from now (one missed while the host was off isn't run again)."""
    return {**r, "last": now, "next": next_run(r, now)}
