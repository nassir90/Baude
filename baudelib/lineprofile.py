"""lineprofile — line_profiler (schema/deps/line_profiler.json, optional) switched on and off at run time for the
functions and modules named, in whichever process this runs in: the host process (baudelib.telemetry's), Baude's and
Record's (telemetryview's). While nothing's named nothing of line_profiler is loaded or running, so off costs nothing.
What it timed is taken every so often (snapshot) as the wire's TelemetryProfile, the time since the last one's.
No GTK."""
import atexit
import importlib
import linecache
import os
import sys
import threading
import time

from baudelib import deps, wording

LINES_MAX = 200  # (a snapshot's lines, the most time first)
CHECKOUT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def resolve(name):
    """What a dotted name is: a module (every function in it profiled) or a function or method (module.function,
    module.Class.method; module:Class.method too). The longest part of it that's a module is the module (imported, if
    it isn't yet). ValueError if it isn't one of them."""
    name = name.strip().replace(":", ".")
    parts = [p for p in name.split(".") if p]
    for cut in range(len(parts), 0, -1):
        mod_name = ".".join(parts[:cut])
        mod = sys.modules.get(mod_name)
        if mod is None:
            try:
                mod = importlib.import_module(mod_name)
            except Exception:  # noqa: BLE001 (not a module, or one that won't import: a shorter part may be)
                continue
        thing = mod
        try:
            for attr in parts[cut:]:
                thing = getattr(thing, attr)
        except AttributeError:
            break
        return thing
    raise ValueError(wording.say("telemetry.not_found", name=name))


class Profiler:
    """One process's: what it's told to profile (functions), and line_profiler's LineProfiler timing them since the
    last snapshot. error: why it isn't profiling what it's told to (line_profiler isn't here; a name isn't found)."""

    def __init__(self, app):
        self.app, self.functions, self.error = app, [], None
        self.lp, self.since, self.lock = None, None, threading.Lock()

    def set(self, functions):
        """Profile these (none: off). What was timed so far is returned (snapshot's), not lost."""
        functions = [f for f in dict.fromkeys(f.strip() for f in functions or [] if isinstance(f, str)) if f]
        with self.lock:
            if functions == self.functions and (self.lp is not None or not functions):
                return None
            last = self._take(restart=False)
            self.functions, self.error = functions, None
            if functions:
                self._start()
            return last

    def _start(self):
        if deps.present("line_profiler"):
            try:
                import line_profiler
            except ImportError:
                line_profiler = None
        else:
            line_profiler = None
        if line_profiler is None:
            self.error = deps.say(deps.info("line_profiler", False), "telemetry.line_profiling")
            return
        lp, bad = line_profiler.LineProfiler(), []
        for name in self.functions:
            try:
                thing = resolve(name)
                if isinstance(thing, type(sys)):
                    lp.add_module(thing)
                elif isinstance(thing, type):
                    lp.add_class(thing)
                else:
                    lp.add_function(getattr(thing, "__func__", thing))
            except (ValueError, TypeError) as e:
                bad.append(str(e) if isinstance(e, ValueError) else wording.say("telemetry.not_found", name=name))
        self.error = "; ".join(bad) or None
        lp.enable_by_count()
        self.lp, self.since = lp, time.time()
        if not getattr(self, "exiting", False):
            self.exiting = True
            atexit.register(lambda: self.lp is not None and self.lp.disable_by_count())

    def snapshot(self):
        """What's been timed since the last snapshot (a TelemetryProfile), or None if nothing's being profiled or
        nothing ran; profiling goes on."""
        with self.lock:
            return self._take(restart=True)

    def _take(self, restart):
        lp, since = self.lp, self.since
        if lp is None:
            return None
        lp.disable_by_count()
        self.lp = None
        if restart:
            self._start()
        stats = lp.get_stats()
        lines = []
        for (path, _first, func), timings in stats.timings.items():
            for line, hits, t in timings:
                if hits:
                    lines.append({"file": shown_path(path), "line": line, "function": func,
                                  "code": linecache.getline(path, line).strip()[:200] or None, "hits": hits,
                                  "seconds": round(t * stats.unit, 6)})
        if not lines:
            return None
        lines.sort(key=lambda x: -x["seconds"])
        return {"at": time.time(), "from": since, "app": self.app, "lines": lines[:LINES_MAX]}

    def profiling(self):
        """As the wire's TelemetryProfiling has it."""
        return {"app": self.app, "functions": list(self.functions), "error": self.error}


def shown_path(path):
    """A file's path as a profile names it: under the checkout, relative to it."""
    real = os.path.realpath(path)
    return os.path.relpath(real, CHECKOUT) if real.startswith(CHECKOUT + os.sep) else path
