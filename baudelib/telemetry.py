"""telemetry — what this machine's apps were used for, as its host keeps it (the telemetry op: an app's time on screen
in each place, and its uses of each menu item, about once a minute) and adds up (telemetry_read: Settings →
Telemetry). Kept in the host's state folder only, telemetry/YYYY-MM-DD.jsonl (the day it's told, this machine's
time), a line a telling: {"at", "app", "seconds": {place: s}, "uses": {name: n}}. Nothing here sends it anywhere: an
app tells its own machine's host, and another machine's apps (Settings → Telemetry on a host picked) read it here.
No GTK.

A place is the schema/copy key that names it (the wire's list: Telemetry.request.json's seconds); a use is the
schema/copy key of the menu item's words, else its action's name."""
import datetime
import json
import os
import threading

from baudelib import wire

PLACES = tuple(wire.DEFS["Telemetry"]["properties"]["seconds"]["propertyNames"]["enum"])
APPS = tuple(wire.DEFS["Telemetry"]["properties"]["app"]["enum"])
SECONDS_MAX = 3600  # (a place's seconds in one telling, at most: an app tells about once a minute)
USES_MAX = 10_000
NAME_MAX = 120
_LOCK = threading.Lock()


def folder(state_dir):
    return os.path.join(state_dir, "telemetry")


def _day_file(state_dir, day):
    return os.path.join(folder(state_dir), f"{day.isoformat()}.jsonl")


def keep(state_dir, msg, now=None):
    """A telemetry request's counts, added to today's file (what isn't as the wire has it is left out)."""
    app = msg.get("app")
    if app not in APPS:
        return
    seconds = {p: min(float(s), SECONDS_MAX) for p, s in (msg.get("seconds") or {}).items()
               if p in PLACES and isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0}
    uses = {n[:NAME_MAX]: min(int(c), USES_MAX) for n, c in (msg.get("uses") or {}).items()
            if isinstance(n, str) and n and isinstance(c, int) and not isinstance(c, bool) and c > 0}
    if not seconds and not uses:
        return
    now = datetime.datetime.now() if now is None else now
    line = json.dumps({"at": now.timestamp(), "app": app, "seconds": {p: round(s, 1) for p, s in seconds.items()},
                       "uses": uses}, separators=(",", ":")) + "\n"
    os.makedirs(folder(state_dir), mode=0o700, exist_ok=True)
    with _LOCK, open(_day_file(state_dir, now.date()), "a", encoding="utf-8") as f:
        f.write(line)


def read(state_dir, days, today=None):
    """The last days' (today the first) added up: {"times": [{app, place, seconds}], "uses": [{app, name, count}]},
    each the most first."""
    days = max(1, min(int(days or 1), 366))
    today = datetime.date.today() if today is None else today
    times, uses = {}, {}
    for back in range(days):
        try:
            with open(_day_file(state_dir, today - datetime.timedelta(days=back)), encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in lines:
            try:
                d = json.loads(line)
            except ValueError:
                continue  # (a line cut short as the machine went down)
            if not isinstance(d, dict) or d.get("app") not in APPS:
                continue
            for p, s in (d.get("seconds") or {}).items():
                if p in PLACES and isinstance(s, (int, float)):
                    times[(d["app"], p)] = times.get((d["app"], p), 0) + s
            for n, c in (d.get("uses") or {}).items():
                if isinstance(n, str) and isinstance(c, int):
                    uses[(d["app"], n)] = uses.get((d["app"], n), 0) + c
    return {"times": [{"app": a, "place": p, "seconds": round(s)}
                      for (a, p), s in sorted(times.items(), key=lambda kv: (-kv[1], kv[0]))],
            "uses": [{"app": a, "name": n, "count": c}
                     for (a, n), c in sorted(uses.items(), key=lambda kv: (-kv[1], kv[0]))]}
