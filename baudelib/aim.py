"""aim — where line profiling is aimed, from what the counts show took the user's time (baudelib.counters): in Baude
and Record, the handlers of the buttons, rows and menu items they used and the main-loop callbacks they ran; in the
host process, the wire ops it answered (each its own method where it has one, else ServerRunning.handle). The most
total time first, never telemetry's own code; PER at a time, in turn, ROTATE_S each, so each of the top CANDIDATES
gets a window. What telemetry_set names for a process is profiled in place of them (Baude's developers' call). No
GTK."""
import time

from baudelib import lineprofile

CANDIDATES = 8  # (the most total time, each process)
PER = 2  # (profiled at once)
ROTATE_S = 600  # (each pair's window)
SINCE_S = 86400  # (the counts picked from: the last day's)
OWN = ("telemetryview.", "uitrack.", "baudelib.telemetry.", "baudelib.counters.", "baudelib.errorbook.",
       "baudelib.lineprofile.", "baudelib.uiindex.", "baudelib.aim.")  # (telemetry's own code: never aimed at)
OWN_OPS = ("telemetry", "telemetry_read", "telemetry_set", "ping")
KINDS = {"host": ("op",), "baude": ("handler", "callback"), "record": ("handler", "callback")}


def ours(name):
    return name.startswith(OWN)


def op_function(op):
    """The function that answers op in the host process: its own method on Server where it has one (op, op_op),
    else ServerRunning.handle."""
    try:
        from baudelib import server
    except ImportError:
        return "baudelib.serverrun.ServerRunning.handle"
    for name in (op, f"{op}_op"):
        for cls in server.Server.__mro__:
            if name in vars(cls) and callable(vars(cls)[name]):
                return f"{cls.__module__}.{cls.__qualname__}.{name}"
    return "baudelib.serverrun.ServerRunning.handle"


def candidates(app, counted):
    """app's process's candidates, from counted (telemetry.counted's rows): [TelemetryPick], the most time first."""
    out, seen = [], set()
    for c in sorted((c for c in counted if c.get("app") == app and c.get("kind") in KINDS.get(app, ())),
                    key=lambda c: -c["seconds"]):
        name = c["name"]
        if c["kind"] == "op":
            if name in OWN_OPS:
                continue
            function = op_function(name)
        else:
            if "<" in name or ours(name):  # (a lambda's or a nested function's: not reached by its name)
                continue
            function = name
        if function in seen or not c["seconds"]:
            continue
        seen.add(function)
        out.append({"function": function, "kind": c["kind"], "name": name, "count": c["count"],
                    "seconds": round(c["seconds"], 6)})
        if len(out) >= CANDIDATES:
            break
    return out


def picks(app, counted, now=None):
    """What app's process profiles now: PER of its candidates, in turn by the ROTATE_S window now is in."""
    found = candidates(app, counted)
    if not found:
        return []
    turns = (len(found) + PER - 1) // PER
    turn = int((time.time() if now is None else now) // ROTATE_S) % turns
    return found[turn * PER:(turn + 1) * PER]


def defaults(app):
    """The developers' pick (lineprofile.DEFAULTS), before there's anything counted to pick from."""
    return [f for f in lineprofile.DEFAULTS.get(app, []) if not f.startswith(OWN)]
