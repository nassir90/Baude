"""funccount — how many times each function of Baude's own code ran in this process, and how long it took in all,
by "module:function" (counters' "function" kind). PEP 669 (sys.monitoring): a function's start and its returns are
told to two small callbacks, and a function of anything else (the standard library, vendor/, tests, the telemetry's
own code) is answered DISABLE the first time, so the interpreter never tells of it again. A thread keeps its own
numbers; drain() gives what's been counted since it was last asked, from every thread. Switched on and off with the
machine's telemetry, and off always by BAUDE_FUNCTION_COUNTS=0. No GTK."""
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
                                                  "lineprofile.py")}  # (the counting itself)
TOOL = getattr(getattr(sys, "monitoring", None), "PROFILER_ID", None)
MAX = 500  # (functions kept in a row: the most time in all)

_lock = threading.Lock()
_threads = []  # (each thread that ran one: its holder)
_local = threading.local()
_ours = {}  # (a code's file: is it Baude's)
_on = [False]


class _Held:
    """What a thread has counted (stats: code -> [times, seconds, longest]) and its calls running (stack)."""
    __slots__ = ("stats", "stack", "thread")

    def __init__(self):
        self.stats, self.stack, self.thread = {}, [], weakref.ref(threading.current_thread())


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
    held = _held()
    got = held.stats.get(code)
    if got is None:
        got = held.stats[code] = [0, 0.0, 0.0]
    got[0] += 1
    held.stack.append((code, time.perf_counter()))


def _resume(code, offset):
    if not _ours.get(code.co_filename) and not _is_ours(code.co_filename):
        return sys.monitoring.DISABLE
    _held().stack.append((code, time.perf_counter()))


def _stop(code, offset, _result=None):
    if not _ours.get(code.co_filename) and not _is_ours(code.co_filename):
        return sys.monitoring.DISABLE
    try:
        held = _local.held
    except AttributeError:
        return
    stack = held.stack
    if not stack or stack[-1][0] is not code:  # (begun before this was on)
        return
    took = time.perf_counter() - stack.pop()[1]
    got = held.stats.get(code)
    if got is None:
        return
    got[1] += took
    if took > got[2]:
        got[2] = took


def name_of(code):
    path = os.path.abspath(code.co_filename)[len(ROOT):]
    module = path[:-3].replace(os.sep, ".") if path.endswith(".py") else path
    return f"{module}:{getattr(code, 'co_qualname', code.co_name)}"


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
                              (ev.PY_YIELD, _stop), (ev.PY_UNWIND, _stop)):
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
        return _on[0]


def drain():
    """What's been counted since it was last asked: [(name, times, seconds, longest)], the most time in all first,
    at most MAX."""
    out = {}
    with _lock:
        for held in list(_threads):
            old, held.stats = held.stats, {}
            for code, (n, s, m) in list(old.items()):
                got = out.setdefault(name_of(code), [0, 0.0, 0.0])
                got[0] += n
                got[1] += s
                got[2] = max(got[2], m)
            if (t := held.thread()) is None or not t.is_alive():
                _threads.remove(held)
    rows = sorted(((k, *v) for k, v in out.items() if v[0]), key=lambda r: -r[2])[:MAX]
    return rows
