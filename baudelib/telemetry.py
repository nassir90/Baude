"""telemetry — a host's: what the apps on its machine saw done in chats (the telemetry op's events, each kept as it's
told, at its own time), samples of its machine every SAMPLE_S (load, memory, swap, memory pressure, and the CPU and memory of
its own process and of each app's on it: Baude's, Record's, Emission's, Multiplex's), and line profiles (lineprofile:
its own, and those its apps send), and the errors of every process on its machine (errorbook's: its own, and those its
apps tell it), each row with the commit and wire version of the code that made it, while it's on; all read back over a stretch by telemetry_read, from any member of the
mesh, and switched by telemetry_set (Settings → Telemetry, any head). Line profiling is switched on and off there too;
what each process profiles is lineprofile.DEFAULTS unless telemetry_set names others for it (python3 -m
baudelib.telemetry profile, from any member of the mesh). Kept in the host's state folder only,
telemetry/<events|metrics|profiles>/YYYY-MM-DD.jsonl (the day of each line's time, this machine's),
telemetry/errors.json (each error once, by its process and fingerprint), and
telemetry/state.json (on, line profiling on, and what's named in place of a process's defaults). Nothing here sends it
anywhere else.

Chat sizes aren't kept: chat_size works one out from a chat's transcript as it is when it's asked. gather() pulls every
host's into one, by their times. No GTK, no dependency but line_profiler's (lineprofile's, optional)."""
import ctypes
import ctypes.util
import datetime
import json
import os
import subprocess
import sys
import threading
import time

from baudelib import counters, deps, errorbook, funccount, lineprofile, uiindex, wire

SAMPLE_S = 5  # (how often the machine is sampled while it's on)
PROFILE_S = 30  # (how often what's profiled is saved)
KEEP_DAYS = {"events": 400, "metrics": 7, "profiles": 30, "errors": 90, "counters": 90}  # (events back a year: the tokens' grid;
# an error since it last happened)
ERRORS_FLUSH_S = 1  # (errors written this long after one comes, those meanwhile with it)
ERRORS_KEPT = 1000  # (errors kept: the latest by when they last happened)
LIMIT = 5000  # (telemetry_read's most of each, the latest kept)
EVENTS_MAX = 1000  # (one telling's)
APPS = tuple(wire.DEFS["Telemetry"]["properties"]["app"]["enum"])
PROCS = tuple(wire.DEFS["TelemetryProc"]["properties"]["app"]["enum"])
KINDS = tuple(wire.DEFS["TelemetryEvent"]["properties"]["kind"]["enum"])
PROFILED = tuple(wire.DEFS["TelemetryProfiling"]["properties"]["app"]["enum"])
WHAT = tuple(wire.DEFS["TelemetryRead"]["properties"]["what"]["items"]["enum"])
TEXT_MAX = 200
_EVENT_TEXT = ("host", "session", "turn", "model", "effort")
_EVENT_NUM = ("seconds",)
TOKENS = ("tokens_input", "tokens_cache_write", "tokens_cache_read", "tokens_out")  # (a turn's, each kind apart)
_EVENT_INT = (*TOKENS, "tokens_in")
_KINDS = {"tokens_input": "input", "tokens_cache_write": "cache_write", "tokens_cache_read": "cache_read",
          "tokens_out": "output", "tokens_in": "input_with_cache"}  # (an event's tokens: usage's names for them)
ERROR_APPS = tuple(wire.DEFS["TelemetryError"]["properties"]["app"]["enum"])
_ERROR_TEXT = ("file", "function", "commit", "first_commit", "screen", "part", "wire_op")
_ERROR_LATEST = ("message", "file", "line", "function", "stack", "commit", "wire", "screen", "part", "wire_op", "code",
                 "crumbs")  # (an error's, as it was the last time)
CRUMB_ACTIONS = tuple(wire.DEFS["TelemetryCrumb"]["properties"]["action"]["enum"])
CRUMBS_MAX = 8
COUNT_KINDS = counters.KINDS
COUNTS_MAX = 2000  # (one row's)


def folder(state_dir):
    return os.path.join(state_dir, "telemetry")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def version(commit, wire_version):
    """A row's version fields: the commit (its first 12 characters) and wire version of the code that made it."""
    return {"commit": commit[:12] if isinstance(commit, str) and commit else None,
            "wire": wire_version if isinstance(wire_version, int) and not isinstance(wire_version, bool) else None}


def tokens_of(msg):
    """A turn's result's tokens, each kind apart (TOKENS): input not read from a cache, written to it, read from it,
    and output. Claude Code's model_usage_since_last_result where it has it (every call since its previous result,
    subagents' and compaction's too, each call on one result only), else its usage (the turn's main loop's calls);
    the agents Baude runs in its place (Codex, pi, Apple, the phone's) say a turn's in usage's shape."""
    by_model = msg.get("model_usage_since_last_result")
    if isinstance(by_model, dict):
        parts = [m for m in by_model.values() if isinstance(m, dict)]
        keys = ("inputTokens", "cacheCreationInputTokens", "cacheReadInputTokens", "outputTokens")
    else:
        parts = [msg["usage"]] if isinstance(msg.get("usage"), dict) else []
        keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    out = {}
    for name, key in zip(TOKENS, keys):
        out[name] = sum(max(0, int(p[key])) for p in parts if _num(p.get(key)))
    return out


def event_of(e, app, ver=None):
    """A told event as it's kept (what isn't as the wire has it left out), or None."""
    if not isinstance(e, dict) or e.get("kind") not in KINDS or not _num(e.get("at")):
        return None
    out = {"at": round(float(e["at"]), 3), "app": app, "kind": e["kind"], **(ver or {})}
    for k in _EVENT_TEXT:
        if isinstance(e.get(k), str) and e[k]:
            out[k] = e[k][:TEXT_MAX]
    if "host" not in out or "session" not in out:
        return None
    for k in _EVENT_NUM:
        if _num(e.get(k)) and e[k] >= 0:
            out[k] = round(float(e[k]), 3)
    for k in _EVENT_INT:
        if isinstance(e.get(k), int) and not isinstance(e[k], bool) and e[k] >= 0:
            out[k] = e[k]
    return out


def profile_of(p, app, ver=None):
    """A told profile as it's kept, or None."""
    if not isinstance(p, dict) or not _num(p.get("at")) or not _num(p.get("from")) or not isinstance(p.get("lines"), list):
        return None
    lines = [{"file": str(x["file"])[:TEXT_MAX], "line": x["line"], "function": str(x["function"])[:TEXT_MAX],
              "code": x["code"][:TEXT_MAX] if isinstance(x.get("code"), str) else None, "hits": x["hits"],
              "seconds": float(x["seconds"])}
             for x in p["lines"][:lineprofile.LINES_MAX] if isinstance(x, dict) and isinstance(x.get("file"), str)
             and isinstance(x.get("line"), int) and isinstance(x.get("function"), str)
             and isinstance(x.get("hits"), int) and _num(x.get("seconds"))]
    return {"at": float(p["at"]), "from": float(p["from"]), "app": app, "lines": lines, **(ver or {})} if lines else None


def _text(v, n=TEXT_MAX):
    return v[:n] if isinstance(v, str) and v else None


def counters_of(row, app, ver=None):
    """A told row of counts as it's kept, or None. A part named by its screen and a copy key ("Entity.kind|copy:key",
    the phone's: it has no schema/ui of its own) named as its part, where the entity has one with that key."""
    if not isinstance(row, dict) or not _num(row.get("at")) or not _num(row.get("from")) or \
            not isinstance(row.get("counts"), list):
        return None
    counts = []
    for c in row["counts"][:COUNTS_MAX]:
        if not isinstance(c, dict) or c.get("kind") not in COUNT_KINDS or not isinstance(c.get("name"), str) or \
                not c["name"] or not isinstance(c.get("count"), int) or c["count"] < 0 or not _num(c.get("seconds")):
            continue
        name = c["name"][:counters.NAME_MAX]
        if c["kind"] in ("part", "menu") and "|copy:" in name:
            entity, _, key = name.partition("|copy:")
            part = uiindex.part_by_copy(entity, key)
            name = f"{entity}/{part}" if part else f"{entity}/({key})"
        counts.append({"kind": c["kind"], "name": name, "count": c["count"], "seconds": round(float(c["seconds"]), 6),
                       "max": round(float(c["max"]), 6) if _num(c.get("max")) else None})
    if not counts:
        return None
    return {"at": round(float(row["at"]), 3), "from": round(float(row["from"]), 3), "app": app, "counts": counts,
            **(ver or {})}


def counted(rows, legacy=()):
    """Rows of counts added up (TelemetryCount each, with its process): by process, kind and name, the most times
    first. legacy: what Baude kept before counts (screen time per place, menu uses), added in as screens and menu
    items named by their copy keys."""
    out = {}
    for r in rows:
        for c in r.get("counts") or []:
            got = out.setdefault((r.get("app"), c["kind"], c["name"]), [0, 0.0, 0.0])
            got[0] += c["count"]
            got[1] += c["seconds"]
            got[2] = max(got[2], c.get("max") or 0)
    for app, kind, name, count, seconds in legacy:
        got = out.setdefault((app, kind, name), [0, 0.0, 0.0])
        got[0] += count
        got[1] += seconds
    return [{"app": a, "kind": k, "name": n, "count": c, "seconds": round(s, 6), "max": round(m, 6) if m else None}
            for (a, k, n), (c, s, m) in sorted(out.items(), key=lambda kv: (kv[0][1] == "function", -kv[1][0], -kv[1][1], kv[0][2]))]


def crumb_of(c):
    if not isinstance(c, dict) or not _num(c.get("at")) or c.get("action") not in CRUMB_ACTIONS:
        return None
    return {"at": round(float(c["at"]), 3), "action": c["action"],
            **{k: _text(c.get(k)) for k in ("screen", "part", "widget")}}


def error_of(e, app, ver=None):
    """A told error as it's kept (what isn't as the wire has it left out), or None. ver: its telling's version,
    for one that doesn't say its own."""
    if not isinstance(e, dict) or e.get("kind") not in errorbook.KINDS or not all(
            isinstance(e.get(k), str) and e[k] for k in ("fingerprint", "type")) or not _num(e.get("first")) \
            or not _num(e.get("last")) or not isinstance(e.get("count"), int) or e["count"] < 1:
        return None
    app = e["app"] if e.get("app") in ERROR_APPS and app == "host" else app
    out = {"app": app, "kind": e["kind"], "fingerprint": e["fingerprint"][:TEXT_MAX], "type": e["type"][:TEXT_MAX],
           "message": _text(e.get("message"), errorbook.TEXT_MAX) or "", "count": e["count"],
           "first": round(float(e["first"]), 3), "last": round(float(e["last"]), 3),
           **{k: _text(e.get(k)) for k in _ERROR_TEXT},
           "line": e["line"] if isinstance(e.get("line"), int) and not isinstance(e["line"], bool) else None,
           "wire": e["wire"] if isinstance(e.get("wire"), int) and not isinstance(e["wire"], bool) else None,
           "code": e["code"] if isinstance(e.get("code"), (str, int)) and not isinstance(e["code"], bool) else None,
           "stack": [x[:TEXT_MAX] for x in e.get("stack") or [] if isinstance(x, str)][-errorbook.STACK_MAX:],
           "crumbs": [c for c in (crumb_of(x) for x in (e.get("crumbs") or [])[-CRUMBS_MAX:]) if c]}
    if ver and out["commit"] is None and out["wire"] is None:
        out.update(ver)
    out["first_commit"] = out["first_commit"] or out["commit"]
    return out


# ---- the machine, sampled
_CLK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") and sys.platform != "darwin" else 100
_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def _ps_time(text):
    """macOS ps's cputime ([dd-][hh:]mm:ss.ss) in seconds."""
    days, _, rest = text.rpartition("-")
    total = 0.0
    for part in rest.split(":"):
        total = total * 60 + float(part)
    return total + (int(days) * 86400 if days else 0)


def commands(pids):
    """{pid: its command line} of those still running."""
    out = {}
    if not pids:
        return out
    if sys.platform == "darwin":
        for pid in pids:
            try:
                text = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                                      timeout=5).stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                continue
            if text:
                out[pid] = text
        return out
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                out[pid] = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
    return out


def proc_stats(pids):
    """{pid: (CPU seconds so far, resident bytes)} of those still running."""
    out = {}
    if not pids:
        return out
    if sys.platform == "darwin":
        try:
            text = subprocess.run(["ps", "-o", "pid=,time=,rss=", "-p", ",".join(str(p) for p in pids)],
                                  capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return out
        for line in text.splitlines():
            f = line.split()
            try:
                out[int(f[0])] = (_ps_time(f[1]), int(f[2]) * 1024)
            except (IndexError, ValueError):
                continue
        return out
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat") as f:
                stat = f.read().rsplit(")", 1)[1].split()
            with open(f"/proc/{pid}/statm") as f:
                rss = int(f.read().split()[1]) * _PAGE
            out[pid] = ((int(stat[11]) + int(stat[12])) / _CLK, rss)
        except (OSError, IndexError, ValueError):
            continue
    return out


_PRESSURES = {1: "normal", 2: "warning", 4: "critical"}  # (macOS's kern.memorystatus_vm_pressure_level, dispatch's)


class _VMStats(ctypes.Structure):
    """The head of macOS's struct vm_statistics64 (mach/vm_statistics.h), as host_statistics64 fills it."""
    _fields_ = [(n, ctypes.c_uint32) for n in ("free", "active", "inactive", "wire")] + \
        [(n, ctypes.c_uint64) for n in ("zero_fill", "reactivations", "pageins", "pageouts", "faults", "cow_faults",
                                        "lookups", "hits", "purges")] + \
        [(n, ctypes.c_uint32) for n in ("purgeable", "speculative")] + \
        [(n, ctypes.c_uint64) for n in ("decompressions", "compressions", "swapins", "swapouts")] + \
        [(n, ctypes.c_uint32) for n in ("compressor", "throttled", "external", "internal")] + \
        [("uncompressed_in_compressor", ctypes.c_uint64)]


class _SwapUsage(ctypes.Structure):
    """macOS's struct xsw_usage (sys/sysctl.h), vm.swapusage's."""
    _fields_ = [("total", ctypes.c_uint64), ("avail", ctypes.c_uint64), ("used", ctypes.c_uint64),
                ("pagesize", ctypes.c_uint32), ("encrypted", ctypes.c_int32)]


_MAC = []  # (libSystem and this host's port, found once)


def _mac():
    if not _MAC:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.mach_host_self.restype = ctypes.c_uint32
        _MAC.append((libc, libc.mach_host_self()))
    return _MAC[0]


def _sysctl(name, value):
    """value filled from macOS's sysctl name; False if it can't be read."""
    libc = _mac()[0]
    size = ctypes.c_size_t(ctypes.sizeof(value))
    return libc.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, ctypes.c_size_t(0)) == 0


def _mac_memory():
    total, page, level = ctypes.c_uint64(), ctypes.c_uint64(), ctypes.c_uint32()
    if not _sysctl("hw.memsize", total) or not _sysctl("hw.pagesize", page):
        return {}
    out = {"memory_total": total.value}
    libc, host = _mac()
    vm = _VMStats()
    count = ctypes.c_uint32(ctypes.sizeof(vm) // 4)
    if libc.host_statistics64(host, 4, ctypes.byref(vm), ctypes.byref(count)) == 0:  # (HOST_VM_INFO64)
        # Activity Monitor's Memory Used: app memory (anonymous, less purgeable), wired and compressed
        used = (max(0, vm.internal - vm.purgeable) + vm.wire + vm.compressor) * page.value
        out.update(memory_used=used)
    swap = _SwapUsage()
    if _sysctl("vm.swapusage", swap):
        out.update(swap_total=swap.total, swap_used=swap.used)
    if _sysctl("kern.memorystatus_vm_pressure_level", level):
        out["memory_pressure"] = _PRESSURES.get(level.value)
    return out


def _linux_memory():
    out = {}
    try:
        with open("/proc/meminfo") as f:
            info = {k: int(v.split()[0]) * 1024 for k, v in (line.split(":", 1) for line in f if ":" in line)}
        out.update(memory_total=info["MemTotal"],
                   memory_used=info["MemTotal"] - info["MemAvailable"])
        if "SwapTotal" in info:
            out.update(swap_total=info["SwapTotal"], swap_used=info["SwapTotal"] - info.get("SwapFree", 0))
    except (OSError, KeyError, ValueError):
        pass
    try:  # (PSI: missing before 4.20, off with psi=0 or without CONFIG_PSI)
        with open("/proc/pressure/memory") as f:
            some = dict(kv.split("=") for kv in f.readline().split()[1:])
        out["memory_stall"] = float(some["avg10"])
    except (OSError, KeyError, ValueError):
        pass
    return out


def memory():
    """This machine's memory, swap and memory pressure, as a TelemetrySample's fields (each None if it can't be
    told): bytes in use, swap in use of its total, macOS's
    pressure level and Linux's share of the last 10 seconds some task stalled waiting on memory (PSI's some avg10)."""
    out = dict.fromkeys(("memory_total", "memory_used", "swap_total", "swap_used",
                         "memory_pressure", "memory_stall"))
    try:
        out.update(_mac_memory() if sys.platform == "darwin" else _linux_memory())
    except (OSError, AttributeError, TypeError, ValueError, ctypes.ArgumentError):
        pass
    return out


class Telemetry:
    """A host's telemetry (its server's: server.telemetry). changed() is told when its state changes (Info's
    telemetry, which its machine's apps follow); found() is {pid: app} of the processes on its machine that tell it
    nothing but are sampled too (Emission's, Multiplex's), asked each sample."""

    def __init__(self, state_dir, host_id, changed=lambda: None, log=lambda _line: None, found=dict, commit=None):
        self.dir, self.host_id, self.changed, self.log, self.found = folder(state_dir), host_id, changed, log, found
        self.ver = version(commit, wire.VERSION)  # (on each row the host makes itself)
        self.lock, self.wake = threading.Lock(), threading.Event()
        self.pids = {os.getpid(): "host"}  # (the processes sampled: the host's, and each app's that's told it)
        self.fronts = {}  # (an app's process: the chat in front in it, (host, session), as its last chat_front said)
        self.cpu = {}  # (pid: (when, CPU seconds), the sample before's)
        self.errors = {}  # (an app's profiling error, as it last said)
        self.pruned = None  # (the day files were last pruned)
        self.profiler = lineprofile.Profiler("host")
        self.has_lp = deps.present("line_profiler")  # (looked for again as it's switched or read)
        try:
            with open(os.path.join(self.dir, "state.json"), encoding="utf-8") as f:
                kept = json.load(f)
        except (OSError, ValueError):
            kept = {}
        self.on = kept.get("on") is not False  # (on unless switched off)
        self.profiling = kept.get("line_profiling") is True  # (off unless switched on: it slows what it times)
        named = kept.get("named") if isinstance(kept.get("named"), dict) else {}
        self.named = {a: [f for f in named[a] if isinstance(f, str)] for a in PROFILED
                      if isinstance(named.get(a), list)}  # (what's named in place of a process's defaults)
        self.thread = None
        self.errors_lock, self.errs, self.errors_due = threading.Lock(), None, None  # (errors.json's, as read;
        # its write waited for)
        self.counters = counters.Counters()  # (its wire ops'; this process's own, COUNTERS, too where it has the book)
        self.book = errorbook.BOOK.claim("host", self._book_woke)  # (this process's own errors: the host's, unless
        # an app in it took them first)

    def start(self):
        """Sampling (and the host's own profiling) started, if it's on."""
        errorbook.install()
        self._profile_host()
        self._run()

    def functions(self, app):
        """What app's process profiles while line profiling's on: what telemetry_set named for it, else the defaults."""
        return list(self.named[app] if app in self.named else lineprofile.DEFAULTS[app])

    def _profile_host(self):
        """The host's own profiler following the state: its functions while telemetry and line profiling are on (and
        the functions counted, to aim it, in the same time)."""
        funccount.switch(self.on and self.profiling)
        if (last := self.profiler.set(self.functions("host") if self.on and self.profiling else [])) is not None:
            self._write("profiles", [last])

    def _run(self):
        with self.lock:
            if self.on and (self.thread is None or not self.thread.is_alive()):
                self.wake.clear()
                self.thread = threading.Thread(target=self._sampling, daemon=True, name="telemetry")
                self.thread.start()

    def stop(self):
        self.wake.set()
        if (last := self.profiler.set([])) is not None:
            self._write("profiles", [last])
        self._flush_errors()
        self.flush_counts()

    def flush_counts(self):
        """What the host process counted since it last kept it, kept as a row (its ops'; and the process's own
        callbacks and programs, where it's the host's)."""
        rows = [r for r in (self.counters.take(), counters.COUNTERS.take() if self.book else None) if r]
        if not rows or not self.on:
            return
        row = rows[0] if len(rows) == 1 else {"at": max(r["at"] for r in rows), "from": min(r["from"] for r in rows),
                                              "counts": [c for r in rows for c in r["counts"]]}
        self._write("counters", [{**row, "app": "host"}])

    # ---- its state
    def state(self):
        """As the wire's TelemetryState has it."""
        profiling = [{"app": a, "functions": self.functions(a), "default": a not in self.named,
                      "error": self.profiler.error if a == "host" else self.errors.get(a)} for a in PROFILED]
        return {"on": self.on, "line_profiler": self.has_lp, "line_profiling": self.profiling, "profiling": profiling}

    def set(self, msg):
        """telemetry_set's: on or off, line profiling on or off, and what a process profiles (named, or its
        defaults again); kept, and the state returned."""
        self.has_lp = deps.present("line_profiler")
        if isinstance(msg.get("on"), bool) and msg["on"] != self.on:
            self.on = msg["on"]
            if self.on:
                self._run()
            else:
                self.wake.set()
                self.cpu.clear()
                with self.lock:
                    self.fronts.clear()
        if isinstance(msg.get("line_profiling"), bool):
            self.profiling = msg["line_profiling"]
        app = msg.get("app")
        if app in PROFILED and msg.get("default") is True:
            self.named.pop(app, None)
            self.errors.pop(app, None)
        elif app in PROFILED and isinstance(msg.get("functions"), list):
            self.named[app] = list(dict.fromkeys(f.strip() for f in msg["functions"]
                                                 if isinstance(f, str) and f.strip()))
            self.errors.pop(app, None)
        self._profile_host()
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        tmp = os.path.join(self.dir, "state.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"on": self.on, "line_profiling": self.profiling, "named": self.named}, f)
        os.replace(tmp, os.path.join(self.dir, "state.json"))
        self.changed()
        return self.state()

    # ---- what's told
    def keep(self, msg):
        """An app's telling: its events and its profile kept (while it's on), its process sampled from now."""
        app = msg.get("app")
        if app not in APPS:
            return
        if isinstance(msg.get("pid"), int) and msg["pid"] > 0 and self.on:
            with self.lock:
                self.pids[msg["pid"]] = app
        if app in PROFILED:
            error = msg.get("profile_error") if isinstance(msg.get("profile_error"), str) else None
            if error != self.errors.get(app):
                self.errors[app] = error
                self.changed()
        if not self.on:
            return
        ver = version(msg.get("commit"), msg.get("wire"))
        if msg.get("errors"):
            self.keep_errors(msg["errors"], app, ver)
        if app not in PROFILED and app not in ("phone",):  # (Emission's: its errors only)
            return
        events = [e for e in (event_of(x, app, ver) for x in (msg.get("events") or [])[:EVENTS_MAX]) if e]
        if events:
            self._write("events", events)
            if isinstance(msg.get("pid"), int) and self._front(msg["pid"], events):
                self.changed()
        if (c := counters_of(msg.get("counters"), app, ver)) is not None:
            self._write("counters", [c])
        if app in PROFILED and (p := profile_of(msg.get("profile"), app, ver)) is not None:
            self._write("profiles", [p])

    def turn(self, session_id, msg, model, effort, began):
        """A turn of a session of this host's ended (its result, msg): kept as the host's own turn_ended, with its
        model, effort and tokens, whichever app (or none) sent it. What tokens are added up from (usage)."""
        if not self.on:
            return
        now = time.time()
        took = now - began if isinstance(began, (int, float)) and began else (
            msg["duration_ms"] / 1000 if _num(msg.get("duration_ms")) else None)
        e = event_of({"at": now, "kind": "turn_ended", "host": self.host_id, "session": session_id,
                      "seconds": took, "turn": msg.get("uuid"), "model": model, "effort": effort,
                      **tokens_of(msg)}, "host", self.ver)
        if e is not None:
            self._write("events", [e])

    def _front(self, pid, events):
        """An app's chat in front followed through its events: whether what's in front here changed."""
        with self.lock:
            before = self.fronts.get(pid)
            for e in events:
                if e["kind"] == "chat_front":
                    self.fronts[pid] = (e["host"], e["session"])
                elif e["kind"] == "chat_left" and self.fronts.get(pid) == (e["host"], e["session"]):
                    self.fronts.pop(pid)
            return self.fronts.get(pid) != before

    def front(self):
        """The chats in front on this machine now (Info's front, a TelemetryFront each): what no head notifies of."""
        with self.lock:
            return [{"host": h, "session": s} for h, s in sorted(set(self.fronts.values()))]

    # ---- errors
    def note(self, err):
        """An error of the host's own (errorbook.of_note's or of_exception's), kept as one happening now."""
        if self.on:
            now = time.time()
            self.keep_errors([{**err, "count": 1, "first": now, "last": now}], "host", self.ver)

    def _book_woke(self):
        """This process's errorbook has one: taken soon, with those meanwhile."""
        self._errors_soon()

    def keep_errors(self, errors, app, ver):
        """Errors told (an app's, or the host's own): each added to what's kept of it, by its process and
        fingerprint; written soon."""
        rows = [r for r in (error_of(e, app, ver) for e in errors[:errorbook.PENDING_MAX]) if r]
        if not rows:
            return
        with self.errors_lock:
            errs = self._errors()
            for r in rows:
                key = f"{r['app']}:{r['fingerprint']}"
                got = errs.get(key)
                if got is None:
                    errs[key] = r
                    continue
                if r["last"] >= got["last"]:
                    got.update({k: r[k] for k in _ERROR_LATEST})
                got.update(count=got["count"] + r["count"], first=min(got["first"], r["first"]),
                           last=max(got["last"], r["last"]))
            self._cap_errors()
        self._errors_soon()

    def _cap_errors(self):
        """(errors_lock held) Only the latest ERRORS_KEPT by when they last happened kept."""
        if len(self.errs) > ERRORS_KEPT:
            keep = sorted(self.errs.items(), key=lambda kv: -kv[1]["last"])[:ERRORS_KEPT]
            self.errs = dict(keep)

    def _errors(self):
        """(errors_lock held) What's kept, read the first time."""
        if self.errs is None:
            try:
                with open(os.path.join(self.dir, "errors.json"), encoding="utf-8") as f:
                    kept = json.load(f).get("errors")
            except (OSError, ValueError, AttributeError):
                kept = None
            self.errs = {f"{e['app']}:{e['fingerprint']}": e for e in kept or []
                         if isinstance(e, dict) and isinstance(e.get("fingerprint"), str) and e.get("app")}
        return self.errs

    def _errors_soon(self):
        with self.errors_lock:
            if self.errors_due is None:
                self.errors_due = threading.Timer(ERRORS_FLUSH_S, self._flush_errors)
                self.errors_due.daemon = True
                self.errors_due.start()

    def _flush_errors(self):
        """This process's errors taken (the host's), and what's kept written, those not seen in KEEP_DAYS gone."""
        if self.book and (own := errorbook.BOOK.take()) and self.on:
            self.keep_errors(own, "host", self.ver)
        with self.errors_lock:
            if self.errors_due is not None:
                self.errors_due.cancel()
            self.errors_due = None
            if self.errs is None:
                return
            old = time.time() - KEEP_DAYS["errors"] * 86400
            self.errs = {k: e for k, e in self.errs.items() if e["last"] >= old}
            self._cap_errors()
            try:
                os.makedirs(self.dir, mode=0o700, exist_ok=True)
                tmp = os.path.join(self.dir, "errors.json.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"errors": list(self.errs.values())}, f, separators=(",", ":"))
                os.replace(tmp, os.path.join(self.dir, "errors.json"))
            except OSError as e:
                self.log(f"telemetry: errors not written: {e}")

    def error_rows(self, start, end, limit=LIMIT):
        """The errors that happened from start to end, the last to happen first, the latest limit of them."""
        with self.errors_lock:
            rows = [dict(e) for e in self._errors().values() if e["last"] >= start and e["first"] <= end]
        rows.sort(key=lambda e: -e["last"])
        return rows[:limit] if limit else rows

    def _write(self, kind, rows):
        for r in rows:
            if "wire" not in r:  # (the host's own: its code's)
                r.update(self.ver)
        by_day = {}
        for r in rows:
            by_day.setdefault(datetime.date.fromtimestamp(r["at"]), []).append(r)
        where = os.path.join(self.dir, kind)
        os.makedirs(where, mode=0o700, exist_ok=True)
        with self.lock:
            for day, these in by_day.items():
                with open(os.path.join(where, f"{day.isoformat()}.jsonl"), "a", encoding="utf-8") as f:
                    f.write("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in these))
            today = datetime.date.today()
            if self.pruned != today:
                self.pruned = today
                self._prune(today)

    def _prune(self, today):
        """(lock held) Day files older than they're kept, gone."""
        for kind, days in KEEP_DAYS.items():
            try:
                names = os.listdir(os.path.join(self.dir, kind))
            except OSError:
                continue
            for name in names:
                try:
                    day = datetime.date.fromisoformat(name.removesuffix(".jsonl"))
                except ValueError:
                    continue
                if (today - day).days > days:
                    try:
                        os.unlink(os.path.join(self.dir, kind, name))
                    except OSError:
                        pass

    # ---- sampling
    def sample(self, now=None):
        """One sample of the machine and its processes (a TelemetrySample)."""
        try:
            found = {pid: app for pid, app in self.found().items() if app in PROCS}
        except Exception as e:  # noqa: BLE001 (what couldn't be found: sampled without it)
            self.log(f"telemetry: {type(e).__name__}: {e}")
            found = {}
        cmds = commands(sorted(found))
        found = {pid: app for pid, app in found.items() if app in cmds.get(pid, "").lower()}  # (a pid a
        # process left, taken by another: not sampled as it)
        now = time.time() if now is None else now
        with self.lock:
            pids = {**found, **self.pids}
        stats = proc_stats(sorted(pids))
        procs = []
        for pid, app in pids.items():
            if pid not in stats:
                if app != "host":
                    with self.lock:
                        self.pids.pop(pid, None)
                        gone = self.fronts.pop(pid, None)  # (an app gone without saying: its chat no longer in front)
                    self.cpu.pop(pid, None)
                    if gone is not None:
                        self.changed()
                continue
            cpu_s, rss = stats[pid]
            before = self.cpu.get(pid)
            self.cpu[pid] = (now, cpu_s)
            cpu = round(max(0.0, cpu_s - before[1]) / (now - before[0]) * 100, 1) \
                if before and now > before[0] else None
            procs.append({"app": app, "pid": pid, "cpu": cpu, "rss": rss})
        try:
            load = [round(x, 2) for x in os.getloadavg()]
        except OSError:
            load = None
        return {"at": round(now, 3), "load": load, **memory(), "procs": procs}

    def _sampling(self):
        profiled = counted_at = time.monotonic()
        while not self.wake.wait(SAMPLE_S):
            try:
                if time.monotonic() - counted_at >= counters.FLUSH_S:
                    counted_at = time.monotonic()
                    self.flush_counts()
                self._write("metrics", [self.sample()])
                if time.monotonic() - profiled >= PROFILE_S:
                    profiled = time.monotonic()
                    if (p := self.profiler.snapshot()) is not None:
                        self._write("profiles", [p])
            except Exception as e:  # noqa: BLE001 (a sample gone wrong: said, and the next one taken)
                self.log(f"telemetry: {type(e).__name__}: {e}")

    # ---- read back
    def rows(self, kind, start, end, limit=LIMIT):
        """kind's rows from start to end (seconds since the epoch), the earliest first, the latest limit of them."""
        out = []
        day, last = datetime.date.fromtimestamp(max(0, start)), datetime.date.fromtimestamp(end)
        if (last - day).days > KEEP_DAYS[kind]:
            day = last - datetime.timedelta(days=KEEP_DAYS[kind])
        while day <= last:
            try:
                path = os.path.join(self.dir, kind, f"{day.isoformat()}.jsonl")
                if kind == "metrics":  # (written in time order: only its end since start read, as the page's tick is)
                    lines = tail_since(path, start)
                else:
                    with open(path, encoding="utf-8") as f:
                        lines = f.readlines()
            except OSError:
                lines = []
            for line in lines:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue  # (a line cut short as the machine went down)
                if isinstance(r, dict) and _num(r.get("at")) and start <= r["at"] <= end:
                    out.append(r)
            day += datetime.timedelta(days=1)
        out.sort(key=lambda r: r["at"])
        return out[-limit:] if limit else out

    def read(self, msg, host_id):
        """telemetry_read's answer (but op and req)."""
        start = float(msg["from"]) if _num(msg.get("from")) else time.time() - 86400
        end = float(msg["to"]) if _num(msg.get("to")) else time.time()
        limit = max(1, min(int(msg["limit"]), 100_000)) if isinstance(msg.get("limit"), int) else LIMIT
        what = [w for w in msg.get("what") or [] if w in WHAT]
        self.has_lp = deps.present("line_profiler")
        out = {"host": host_id, "state": self.state()}
        for kind in ("events", "metrics", "profiles"):
            if kind in what:
                out[kind] = self.rows(kind, start, end, limit)
        if "usage" in what:
            out["usage"] = usage(self.rows("events", start, end, 0))
        if "errors" in what:
            out["errors"] = self.error_rows(start, end, limit)
        if "counters" in what:
            out["counters"] = self.rows("counters", start, end, limit)
        if "counted" in what:
            out["counted"] = counted(self.rows("counters", start, end, 0), self.legacy(start, end))[:limit]
        return out

    def legacy(self, start, end):
        """What Baude kept before counts, in telemetry/YYYY-MM-DD.jsonl (a line a telling: {at, app, seconds: {place:
        s}, uses: {name: n}}): (app, kind, name, count, seconds) each, from start to end."""
        out = []
        try:
            names = [n for n in os.listdir(self.dir) if n.endswith(".jsonl")]
        except OSError:
            return out
        for n in names:
            try:
                with open(os.path.join(self.dir, n), encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                continue
            for line in lines:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict) or not _num(d.get("at")) or not start <= d["at"] <= end or \
                        d.get("app") not in APPS:
                    continue
                out += [(d["app"], "screen", str(p)[:counters.NAME_MAX], 0, float(v))
                        for p, v in (d.get("seconds") or {}).items() if _num(v)]
                out += [(d["app"], "menu", str(u)[:counters.NAME_MAX], v, 0.0)
                        for u, v in (d.get("uses") or {}).items() if isinstance(v, int)]
        return out


def tail_since(path, start, block=1 << 16):
    """A file of rows in time order, its lines from the first at start or later (and a few before), read from its
    end back only as far as that."""
    with open(path, "rb") as f:
        end = f.seek(0, os.SEEK_END)
        at, buf = end, b""
        while at > 0:
            at, block = max(0, end - block), block * 2
            f.seek(at)
            buf = f.read(end - at)
            parts = buf.split(b"\n", 2)
            first = parts[1] if at and len(parts) > 1 else parts[0]  # (a whole line: the block's first may be cut)
            try:
                if json.loads(first).get("at", start) < start:
                    break
            except (ValueError, AttributeError):
                continue
    lines = buf.decode("utf-8", "replace").splitlines(keepends=True)
    return lines[1:] if at else lines


def usage(events):
    """The hosts' own turn_ended events added up (TelemetryUsage): tokens by day, by model, by model and effort, each
    turn once (by its host, session and result's id), each kind of token apart (input, cache writes, cache reads,
    output; an event kept before they were told apart, its tokens_in as input_with_cache), tokens all of them. An
    app's turn_ended is the same turn seen there: not counted again."""
    seen, days, models, combos = set(), {}, {}, {}

    def add(table, k, e):
        t = table.setdefault(k, {"turns": 0, "tokens": 0, **{v: 0 for v in _KINDS.values()}})
        t["turns"] += 1
        for field, name in _KINDS.items():
            n = e.get(field)
            if isinstance(n, int) and not isinstance(n, bool) and n > 0:
                t[name] += n
                t["tokens"] += n
    for e in events:
        if e.get("kind") != "turn_ended" or e.get("app") != "host":
            continue
        key = (e.get("host"), e.get("session"), e.get("turn") or round(e["at"]))
        if key in seen:
            continue
        seen.add(key)
        add(days, datetime.date.fromtimestamp(e["at"]).isoformat(), e)
        if e.get("model"):
            add(models, (e["model"], None), e)
            add(combos, (e["model"], e.get("effort")), e)

    def ranked(table):
        return [{"model": m, "effort": eff, **t}
                for (m, eff), t in sorted(table.items(), key=lambda kv: (-kv[1]["turns"], -kv[1]["tokens"], str(kv[0])))]
    return {"days": [{"day": d, **t} for d, t in sorted(days.items())], "models": ranked(models),
            "combos": ranked(combos)}


def chat_size(path):
    """A chat's size, from its transcript as it is now (chat_size's): {bytes, messages, images}. A message is a
    prompt sent or a reply (an assistant message once, however many lines it's written in), a tool's result not
    counted; images are every image in it, those in tools' results too."""
    size, messages, images, replies = 0, 0, 0, set()
    if not path or not os.path.exists(path):
        return {"bytes": 0, "messages": 0, "images": 0}
    size = os.path.getsize(path)

    def count_images(content):
        n = 0
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict):
                n += b.get("type") == "image"
                n += count_images(b.get("content"))
        return n
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("type") not in ("user", "assistant") or d.get("isMeta"):
                continue
            m = d.get("message") if isinstance(d.get("message"), dict) else {}
            content = m.get("content")
            images += count_images(content)
            if d["type"] == "assistant":
                mid = m.get("id") or d.get("uuid")
                if mid not in replies:
                    replies.add(mid)
                    messages += 1
            elif isinstance(content, str) or any(isinstance(b, dict) and b.get("type") in ("text", "image")
                                                 for b in content or []):
                messages += 1
    return {"bytes": size, "messages": messages, "images": images}


def ask_every(ask, timeout=20, host=None):
    """ask sent to every host in the mesh (this machine's host's members, and it; host: that one's id only), all at
    once: {id: its answer, or {"error"}}, what hasn't answered within timeout (all told) an error."""
    deadline = time.monotonic() + timeout
    ch, info = wire.reach({"via": "local"}, timeout=timeout)
    ch.close()
    wheres = [({"via": "local"}, info.get("id"))] + [
        ({"via": "mesh", "host": m["id"]}, m["id"]) for m in (info.get("mesh") or {}).get("members") or []
        if isinstance(m, dict) and wire.is_id(m.get("id")) and m["id"] != info.get("id")]
    if host is not None:
        wheres = [w for w in wheres if w[1] == host] or [({"via": "mesh", "host": host}, host)]
    replies, ask = {}, {**ask, "req": 1}
    lock, done = threading.Lock(), [False]

    def one(where, hid):
        try:
            c, _ = wire.reach(where, timeout=max(1, deadline - time.monotonic()))
            try:
                c.sock.settimeout(max(1, deadline - time.monotonic()))
                c.send(ask)
                got = next((m for m in c if m.get("req") == 1), {"error": "no answer"})
            finally:
                c.close()
        except (OSError, ValueError) as e:
            got = {"error": str(e)}
        with lock:
            if not done[0]:  # (an answer after ask_every's returned: dropped)
                replies[hid] = got
    threads = [threading.Thread(target=one, args=w, daemon=True) for w in wheres]
    for t in threads:
        t.start()
    for t in threads:
        t.join(max(0, deadline - time.monotonic()))
    with lock:
        done[0] = True
        return {hid: replies.get(hid) or {"error": "no answer"} for _, hid in wheres}


def gather(start, end=None, what=("events", "metrics", "profiles", "usage", "errors"), timeout=20):
    """Every host's telemetry in the mesh (ask_every's), pulled by telemetry_read from here and put together:
    {"hosts": {id: reply or {"error"}}, "events": [...], "metrics": [...], "profiles": [...], "usage": ...,
    "errors": [...]}, each row with its host's id, the earliest first (errors the latest first); usage added up over
    every host's events, each turn once."""
    hosts = ask_every({"op": "telemetry_read", "from": start, "to": end, "what": [w for w in what if w != "usage"]
                       + (["events"] if "usage" in what and "events" not in what else [])}, timeout)
    out = {"hosts": hosts}
    for kind in ("events", "metrics", "profiles"):
        out[kind] = sorted(({**r, "from_host": hid} for hid, rep in hosts.items() for r in rep.get(kind) or []),
                           key=lambda r: r["at"])
    if "errors" in what:
        out["errors"] = sorted(({**r, "from_host": hid} for hid, rep in hosts.items() for r in rep.get("errors") or []),
                               key=lambda r: -r["last"])
    if "counters" in what:
        out["counters"] = sorted(({**r, "from_host": hid} for hid, rep in hosts.items()
                                  for r in rep.get("counters") or []), key=lambda r: r["at"])
    if "counted" in what:
        out["counted"] = sorted(({**r, "from_host": hid} for hid, rep in hosts.items()
                                 for r in rep.get("counted") or []), key=lambda r: (r["kind"] == "function", -r["count"], -r["seconds"]))
    if "usage" in what:
        out["usage"] = usage(out["events"])
    if "events" not in what:
        del out["events"]
    return out


def main(argv):
    """python3 -m baudelib.telemetry [hours]: every host's last hours, as JSON. python3 -m baudelib.telemetry errors
    [hours]: every host's errors in its last hours (default a week), the latest first. python3 -m baudelib.telemetry
    counted [hours]: every host's counts added up over its last hours (default a day), the most times first. python3 -m baudelib.telemetry profile
    [--host ID] [--on | --off] [APP [NAME ...] | APP --default]: line profiling switched, or what APP's process
    profiles named (NAME: a function or module's dotted name; none: nothing) or its defaults again, on every host in
    the mesh (or ID's only); each host's telemetry state after it, as JSON."""
    import argparse
    if argv[:1] == ["counted"]:
        hours = float(argv[1]) if len(argv) > 1 else 24
        return json.dump(gather(time.time() - hours * 3600, what=("counted",))["counted"], sys.stdout, indent=1)
    if argv[:1] == ["errors"]:
        hours = float(argv[1]) if len(argv) > 1 else 24 * 7
        return json.dump(gather(time.time() - hours * 3600, what=("errors",))["errors"], sys.stdout, indent=1)
    if argv[:1] != ["profile"]:
        hours = float(argv[0]) if argv else 1
        return json.dump(gather(time.time() - hours * 3600), sys.stdout, indent=1)
    p = argparse.ArgumentParser(prog="python3 -m baudelib.telemetry profile")
    p.add_argument("--host")
    p.add_argument("--on", dest="line_profiling", action="store_const", const=True)
    p.add_argument("--off", dest="line_profiling", action="store_const", const=False)
    p.add_argument("--default", action="store_true")
    p.add_argument("app", nargs="?", choices=PROFILED)
    p.add_argument("names", nargs="*")
    a = p.parse_args(argv[1:])
    if a.default and (a.app is None or a.names):
        p.error("--default takes an app and no names")
    ask = {"op": "telemetry_set", "line_profiling": a.line_profiling, "app": a.app,
           **({"default": True} if a.default else {"functions": a.names} if a.app else {})}
    answers = ask_every(ask, host=a.host)
    json.dump({hid: r.get("state") or {"error": r.get("error") or r.get("said") or "no answer"}
               for hid, r in answers.items()}, sys.stdout, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
