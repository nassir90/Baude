"""aim — where line profiling is aimed, from where each process's time actually went (baudelib.counters' function
counts, funccount's): each function's own time (the processor time it took, less its counted callees'), what ran within a slow click or
op weighted up by how slow that click or op is on average (WEIGHT_S: its average over this, up to WEIGHT_MAX times).
The most first, never telemetry's own code (nor what runs within its ops and its page's clicks: funccount counts
none of that); of the top CANDIDATES, in a stable order, PER at a time in turn, ROTATE_S each, so each gets a window.
What telemetry_set names for a process is profiled in place of them (Baude's developers' call). No GTK."""
import time

from baudelib import lineprofile

CANDIDATES = 8  # (the most own time, each process)
PER = 2  # (profiled at once)
ROTATE_S = 600  # (each pair's window)
SINCE_S = 86400  # (the counts picked from: the last day's)
WEIGHT_S = 0.1  # (a click or op this slow on average: what ran within it counts twice)
WEIGHT_MAX = 10
OWN = ("telemetryview", "uitrack", "baudelib.telemetry", "baudelib.counters", "baudelib.funccount",
       "baudelib.errorbook", "baudelib.lineprofile", "baudelib.uiindex", "baudelib.aim")  # (telemetry's own code:
# never aimed at)
WITHIN_KINDS = ("part", "menu", "op")


def ours(name):
    """Is a count's function ("module:Class.method") telemetry's own?"""
    module, _, qual = name.partition(":")
    return module in OWN or qual.rsplit(".", 1)[-1].startswith("telemetry")


def candidates(app, counted):
    """app's process's candidates, from counted (telemetry.counted's rows): [TelemetryPick], the most weighted own
    time first."""
    each = {(c["kind"], c["name"]): c["seconds"] / c["count"] for c in counted
            if c.get("app") == app and c.get("kind") in WITHIN_KINDS and c.get("count")}
    scored = []
    for c in counted:
        name = c["name"]
        if c.get("app") != app or c.get("kind") != "function" or ":" not in name or "<" in name or ours(name):
            continue  # (a lambda's, a nested function's, a comprehension's: not reached by its name)
        own = c.get("own") or 0.0
        if own <= 0:
            continue
        score, most, most_w = own, None, 0.0
        for w in c.get("within") or []:
            mean = each.get((w["kind"], w["name"]), 0.0)
            weighted = w["seconds"] * min(mean / WEIGHT_S, WEIGHT_MAX)
            score += weighted
            if most is None or weighted > most_w:
                most, most_w = {**w, "each": round(mean, 6)}, weighted
        scored.append((score, {"function": name, "count": c["count"], "seconds": round(own, 6),
                               **({"within": most} if most is not None else {})}))
    scored.sort(key=lambda s: (-s[0], s[1]["function"]))
    return [p for _, p in scored[:CANDIDATES]]


def picks(app, counted, now=None):
    """What app's process profiles now: PER of its candidates (by name, so the order holds as their times change),
    in turn by the ROTATE_S window now is in."""
    found = sorted(candidates(app, counted), key=lambda p: p["function"])
    if not found:
        return []
    turns = (len(found) + PER - 1) // PER
    turn = int((time.time() if now is None else now) // ROTATE_S) % turns
    return found[turn * PER:(turn + 1) * PER]


def pick_of(x):
    """A told TelemetryPick as it's kept, or None."""
    num = (int, float)
    if not isinstance(x, dict) or not isinstance(x.get("function"), str) or not isinstance(x.get("count"), int) \
            or not isinstance(x.get("seconds"), num) or isinstance(x.get("seconds"), bool):
        return None
    out = {"function": x["function"][:400], "count": x["count"], "seconds": float(x["seconds"])}
    w = x.get("within")
    if isinstance(w, dict) and w.get("kind") in WITHIN_KINDS and isinstance(w.get("name"), str) \
            and all(isinstance(w.get(k), num) and not isinstance(w.get(k), bool) for k in ("seconds", "each")):
        out["within"] = {"kind": w["kind"], "name": w["name"][:400], "seconds": float(w["seconds"]),
                         "each": float(w["each"])}
    return out


def defaults(app):
    """The developers' pick (lineprofile.DEFAULTS), before there's anything counted to pick from."""
    return [f for f in lineprofile.DEFAULTS.get(app, []) if not f.startswith(OWN)]
