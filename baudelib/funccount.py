"""funccount — how many times each function of Baude's own code ran in this process, and how long it took in all,
by "module:function" (counters' "function" kind), with its own time (the processor time its calls took on their thread,
less what the counted functions it called took: what it ran itself, not what it waited for) and how much of that ran within each click and op (within(): what a use was timed by). PEP 669 (sys.monitoring): a function's start and its returns are
told to two small callbacks, and a function of anything else (the standard library, vendor/, tests, the telemetry's
own code) is answered DISABLE the first time, so the interpreter never tells of it again. A thread keeps its own
numbers; drain() gives what's been counted since it was last asked, from every thread. Switched on and off with the
machine's line profiling (counted to aim it), and off always by BAUDE_FUNCTION_COUNTS=0. No GTK."""
import os
import sys
import threading
import time
import weakref

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep
LEFT_OUT = ("vendor", "tests", "android", "BaudeRs", "site", "docs", "fonts", "schema", "tools", "apple-claude",
            "pi-claude")  # (folders of the repo that aren't its Python)
OWN = {os.path.join(ROOT, f) for f in ("uitrack.py", "telemetryview.py")} | {
    os.path.join(ROOT, "baudelib", f) for f in ("counters.py", "funccount.py", "telemetry.py", "errorbook.py",
                                                  "lineprofile.py", "aim.py", "uiindex.py")}  # (the counting itself)
TOOL = 4 if hasattr(sys, "monitoring") else None  # (a tool id of its own, none of Python's named ones: line_profiler
# takes PROFILER_ID's, and its callbacks there take the place of another's)
MAX = 500  # (functions kept in a row: the most time in all)
WITHIN_MAX = 3  # (a function's clicks and ops kept, its own time in each: the most first)

_lock = threading.Lock()
_threads = []  # (each thread that ran one: its holder)
_local = threading.local()
_ours = {}  # (a code's file: is it Baude's)
_on = [False]
_wall, _cpu = time.perf_counter, time.thread_time


class _Held:
    """What a thread has counted (stats: code -> [times, seconds, longest, own seconds]; inside: (kind, name) -> code
    -> own seconds within that click or op; cur: the one it's in now's), its calls running (stack: [code, began, its
    thread's processor time then, its callees' processor time]), the click or
    op it's in (within), and whether it's in one of telemetry's own ops (quiet: nothing counted)."""
    __slots__ = ("stats", "inside", "cur", "stack", "within", "quiet", "thread")

    def __init__(self):
        self.stats, self.inside, self.cur, self.stack = {}, {}, None, []
        self.within, self.quiet, self.thread = None, False, weakref.ref(threading.current_thread())


def _is_ours(filename):
    got = _ours.get(filename)
    if got is None:
        path = os.path.abspath(filename) if filename and not filename.startswith("<") else ""
        got = _ours[filename] = (path.startswith(ROOT) and path not in OWN and
                                 path[len(ROOT):].split(os.sep, 1)[0] not in LEFT_OUT)
    return got


def _held():
    try:
        return _local.held
    except AttributeError:
        held = _local.held = _Held()
        with _lock:
            _threads.append(held)
        return held


def _start(code, offset):
    if not _ours.get(code.co_filename) and not _is_ours(code.co_filename):
        return sys.monitoring.DISABLE
    try:
        held = _local.held
    except AttributeError:
        held = _held()
    if held.quiet:
        return
    got = held.stats.get(code)
    if got is None:
        got = held.stats[code] = [0, 0.0, 0.0, 0.0]
    got[0] += 1
    held.stack.append([code, _wall(), _cpu(), 0.0])


def _resume(code, offset):
    if not _ours.get(code.co_filename) and not _is_ours(code.co_filename):
        return sys.monitoring.DISABLE
    held = _held()
    if not held.quiet:
        held.stack.append([code, _wall(), _cpu(), 0.0])


def _stop(code, offset, _result=None):
    if not _ours.get(code.co_filename) and not _is_ours(code.co_filename):
        return sys.monitoring.DISABLE
    try:
        held = _local.held
    except AttributeError:
        return
    stack = held.stack
    if held.quiet or not stack or stack[-1][0] is not code:  # (begun before this was on)
        return
    _code, began, cpu_began, callees = stack.pop()
    took, cpu = _wall() - began, _cpu() - cpu_began
    if stack:
        stack[-1][3] += cpu
    got = held.stats.get(code)
    if got is None:
        return
    got[1] += took
    if took > got[2]:
        got[2] = took
    own = cpu - callees
    got[3] += own
    cur = held.cur
    if cur is not None:
        cur[code] = cur.get(code, 0.0) + own


def _unwind(code, offset, _error=None):
    if _ours.get(code.co_filename) or _is_ours(code.co_filename):  # (an unwind can't be disabled)
        _stop(code, offset)


def name_of(code):
    path = os.path.abspath(code.co_filename)[len(ROOT):]
    module = path[:-3].replace(os.sep, ".") if path.endswith(".py") else path
    return f"{module}:{getattr(code, 'co_qualname', code.co_name)}"


def within(kind=None, name=None):
    """This thread's functions' own time counted as within kind's name (a click: "part" or "menu"; an op) from now,
    till within() again (None: within nothing). Within one of telemetry's own ops (telemetry*) or a click on its page
(Telemetry.dialog's), nothing's counted.
    Returns what it was within, to put back."""
    if not _on[0]:
        return None
    held = _held()
    was = held.within
    held.within = (kind, name) if kind else None
    held.cur = held.inside.setdefault(held.within, {}) if kind else None
    held.quiet = str(name).startswith("telemetry" if kind == "op" else "Telemetry.")
    return was


def switch(on):
    """Counting on or off (False where Python has no sys.monitoring, or BAUDE_FUNCTION_COUNTS=0). Returns whether
    it's on."""
    if TOOL is None or os.environ.get("BAUDE_FUNCTION_COUNTS") == "0":
        return False
    mon = sys.monitoring
    with _lock:
        if on and not _on[0]:
            if mon.get_tool(TOOL) is not None:
                return False
            mon.use_tool_id(TOOL, "baude-functions")
            ev = mon.events
            for event, fn in ((ev.PY_START, _start), (ev.PY_RESUME, _resume), (ev.PY_RETURN, _stop),
                              (ev.PY_YIELD, _stop), (ev.PY_UNWIND, _unwind)):
                mon.register_callback(TOOL, event, fn)
            mon.set_events(TOOL, ev.PY_START | ev.PY_RESUME | ev.PY_RETURN | ev.PY_YIELD | ev.PY_UNWIND)
            mon.restart_events()
            _on[0] = True
        elif not on and _on[0]:
            mon.set_events(TOOL, 0)
            mon.free_tool_id(TOOL)
            _on[0] = False
            for held in _threads:
                held.stack.clear()
                held.within, held.cur, held.quiet = None, None, False
        return _on[0]


def drain():
    """What's been counted since it was last asked: [(name, times, seconds, longest, own seconds, within)], the most
    time in all first, at most MAX; within: [(kind, name, own seconds)], its clicks and ops, the most first (at most
    WITHIN_MAX)."""
    out, inside = {}, {}
    with _lock:
        for held in list(_threads):
            old, held.stats = held.stats, {}
            old_in, held.inside = held.inside, {}
            for code, (n, s, m, own) in list(old.items()):
                got = out.setdefault(name_of(code), [0, 0.0, 0.0, 0.0])
                got[0] += n
                got[1] += s
                got[2] = max(got[2], m)
                got[3] += own
            held.cur = held.inside.setdefault(held.within, {}) if held.within else None
            for (kind, name), codes in list(old_in.items()):
                for code, own in list(codes.items()):
                    key = (name_of(code), kind, name)
                    inside[key] = inside.get(key, 0.0) + own
            if (t := held.thread()) is None or not t.is_alive():
                _threads.remove(held)
    within = {}
    for (fn, kind, name), own in inside.items():
        within.setdefault(fn, []).append((kind, name, max(own, 0.0)))
    rows = sorted(((k, *v[:3], max(v[3], 0.0), sorted(within.get(k, ()), key=lambda w: -w[2])[:WITHIN_MAX])
                   for k, v in out.items() if v[0]), key=lambda r: -r[2])[:MAX]
    return rows
