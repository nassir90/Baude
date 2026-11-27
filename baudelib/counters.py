"""counters — how many times each thing in a process was used or run, and how long it took (the wire's TelemetryCount):
a button, row or menu item (by its schema/ui entity and part: uitrack's), a screen (opened, and its time in front),
a wire op (a host's), a main-loop callback (by its function's name), a function of Baude's own code (funccount's, by
module:function), a program started (subprocess). A count is a
number added to in memory, nothing more; take() gives what's counted since it was last taken as one row (the wire's
TelemetryCounters) and starts again, about once a minute (FLUSH_S), for the machine's host to keep. No GTK."""
import os
import subprocess
import threading
import time

from baudelib import funccount, wire

FLUSH_S = 60  # (a row a minute, a process)
NAME_MAX = 200
KINDS = tuple(wire.DEFS["TelemetryCount"]["properties"]["kind"]["enum"])


class Counters:
    def __init__(self):
        self.lock, self.counts, self.since = threading.Lock(), {}, time.time()

    def add(self, kind, name, seconds=None, count=1, longest=None):
        """kind's name used (count times), seconds its time (the longest kept too, or longest, where seconds is
        many runs' added up)."""
        key = (kind, name[:NAME_MAX])
        with self.lock:
            c = self.counts.get(key)
            if c is None:
                c = self.counts[key] = [0, 0.0, 0.0]
            c[0] += count
            if seconds:
                c[1] += seconds
                c[2] = max(c[2], seconds if longest is None else longest)

    def take(self, now=None):
        """What's counted since the last take, as a row ({at, from, counts}), or None if nothing was."""
        now = time.time() if now is None else now
        for name, n, took, longest in funccount.drain():
            self.add("function", name, took, n, longest)
        with self.lock:
            counts, since, self.counts, self.since = self.counts, self.since, {}, now
        if not counts:
            return None
        return {"at": round(now, 3), "from": round(since, 3), "counts": [
            {"kind": k, "name": n, "count": c, "seconds": round(s, 6), "max": round(m, 6)}
            for (k, n), (c, s, m) in sorted(counts.items(), key=lambda kv: (kv[0][0] == "function", -kv[1][0]))]}

    def give_back(self, row):
        """A row taken but not kept (the host wasn't there): counted again, with what's counted since."""
        with self.lock:
            self.since = min(self.since, row["from"])
            for x in row["counts"]:
                c = self.counts.setdefault((x["kind"], x["name"]), [0, 0.0, 0.0])
                c[0] += x["count"]
                c[1] += x["seconds"]
                c[2] = max(c[2], x["max"])


COUNTERS = Counters()  # (this process's: its UI, its callbacks, the programs it starts)


def callable_name(fn):
    """A function's name as a count has it: module.Class.method (a lambda's, with its line)."""
    fn = getattr(fn, "func", fn)  # (functools.partial's)
    fn = getattr(fn, "__func__", fn)
    mod = getattr(fn, "__module__", None) or "?"
    name = getattr(fn, "__qualname__", None) or type(fn).__qualname__
    code = getattr(fn, "__code__", None)
    if "<lambda>" in name and code is not None:
        name = f"{name}:{code.co_firstlineno}"
    return f"{mod}.{name}"


_PATCHED = []


def count_programs():
    """Every program this process starts (subprocess.Popen) counted, by its name; once a process."""
    if _PATCHED:
        return
    _PATCHED.append(True)
    before = subprocess.Popen.__init__

    def init(self, args, *a, **kw):
        try:
            first = args if isinstance(args, (str, bytes, os.PathLike)) else args[0]
            first = os.fsdecode(first)
            if kw.get("shell") and isinstance(args, str):
                first = first.split()[0] if first.split() else first
            COUNTERS.add("subprocess", os.path.basename(first))
        except Exception:  # noqa: BLE001 (started all the same)
            pass
        before(self, args, *a, **kw)
    subprocess.Popen.__init__ = init
