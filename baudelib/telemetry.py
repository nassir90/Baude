"""telemetry — a host's: what the apps on its machine saw done in chats (the telemetry op's events, each kept as it's
told, at its own time), samples of its machine every SAMPLE_S (load, memory, swap, memory pressure, its own and each
app's CPU and memory), and line profiles (lineprofile: its own, and those its apps send), while it's on; all read
back over a stretch by telemetry_read, from any member of the mesh, and switched by telemetry_set (Settings →
Telemetry, any head). Kept in the host's state folder only, telemetry/<events|metrics|profiles>/YYYY-MM-DD.jsonl (the
day of each line's time, this machine's), and telemetry/state.json (on, and what each process profiles). Nothing here
sends it anywhere else.

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

from baudelib import deps, lineprofile, wire

SAMPLE_S = 5  # (how often the machine is sampled while it's on)
PROFILE_S = 30  # (how often what's profiled is saved)
KEEP_DAYS = {"events": 400, "metrics": 7, "profiles": 30}  # (events back a year: the tokens' grid)
LIMIT = 5000  # (telemetry_read's most of each, the latest kept)
EVENTS_MAX = 1000  # (one telling's)
APPS = tuple(wire.DEFS["Telemetry"]["properties"]["app"]["enum"])
KINDS = tuple(wire.DEFS["TelemetryEvent"]["properties"]["kind"]["enum"])
PROFILED = tuple(wire.DEFS["TelemetryProfiling"]["properties"]["app"]["enum"])
WHAT = tuple(wire.DEFS["TelemetryRead"]["properties"]["what"]["items"]["enum"])
TEXT_MAX = 200
_EVENT_TEXT = ("host", "session", "turn", "model", "effort")
_EVENT_NUM = ("seconds",)
_EVENT_INT = ("tokens_in", "tokens_out")


def folder(state_dir):
    return os.path.join(state_dir, "telemetry")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def event_of(e, app):
    """A told event as it's kept (what isn't as the wire has it left out), or None."""
    if not isinstance(e, dict) or e.get("kind") not in KINDS or not _num(e.get("at")):
        return None
    out = {"at": round(float(e["at"]), 3), "app": app, "kind": e["kind"]}
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


def profile_of(p, app):
    """A told profile as it's kept, or None."""
    if not isinstance(p, dict) or not _num(p.get("at")) or not _num(p.get("from")) or not isinstance(p.get("lines"), list):
        return None
    lines = [{"file": str(x["file"])[:TEXT_MAX], "line": x["line"], "function": str(x["function"])[:TEXT_MAX],
              "code": x["code"][:TEXT_MAX] if isinstance(x.get("code"), str) else None, "hits": x["hits"],
              "seconds": float(x["seconds"])}
             for x in p["lines"][:lineprofile.LINES_MAX] if isinstance(x, dict) and isinstance(x.get("file"), str)
             and isinstance(x.get("line"), int) and isinstance(x.get("function"), str)
             and isinstance(x.get("hits"), int) and _num(x.get("seconds"))]
    return {"at": float(p["at"]), "from": float(p["from"]), "app": app, "lines": lines} if lines else None


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
        out.update(memory_used=used, memory_available=max(0, total.value - used))
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
        out.update(memory_total=info["MemTotal"], memory_available=info["MemAvailable"],
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
    told): bytes in use and available (used and available make the total), swap in use of its total, macOS's
    pressure level and Linux's share of the last 10 seconds some task stalled waiting on memory (PSI's some avg10)."""
    out = dict.fromkeys(("memory_total", "memory_used", "memory_available", "swap_total", "swap_used",
                         "memory_pressure", "memory_stall"))
    try:
        out.update(_mac_memory() if sys.platform == "darwin" else _linux_memory())
    except (OSError, AttributeError, TypeError, ValueError):
        pass
    return out


class Telemetry:
    """A host's telemetry (its server's: server.telemetry). changed() is told when its state changes (Info's
    telemetry, which its machine's apps follow)."""

    def __init__(self, state_dir, host_id, changed=lambda: None, log=lambda _line: None):
        self.dir, self.host_id, self.changed, self.log = folder(state_dir), host_id, changed, log
        self.lock, self.wake = threading.Lock(), threading.Event()
        self.pids = {os.getpid(): "host"}  # (the processes sampled: the host's, and each app's that's told it)
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
        self.profile = {a: [f for f in (kept.get("profile") or {}).get(a) or [] if isinstance(f, str)] for a in PROFILED}
        self.thread = None

    def start(self):
        """Sampling (and the host's own profiling) started, if it's on."""
        if self.profile["host"]:
            self.profiler.set(self.profile["host"])
        self._run()

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

    # ---- its state
    def state(self):
        """As the wire's TelemetryState has it."""
        profiling = [self.profiler.profiling()] + [
            {"app": a, "functions": list(self.profile[a]), "error": self.errors.get(a)} for a in PROFILED if a != "host"]
        return {"on": self.on, "line_profiler": self.has_lp, "profiling": profiling}

    def set(self, msg):
        """telemetry_set's: on or off, and what a process profiles; kept, and the state returned."""
        self.has_lp = deps.present("line_profiler")
        if isinstance(msg.get("on"), bool) and msg["on"] != self.on:
            self.on = msg["on"]
            if self.on:
                self._run()
            else:
                self.wake.set()
                self.cpu.clear()
        app = msg.get("app")
        if app in PROFILED and isinstance(msg.get("functions"), list):
            self.profile[app] = [f.strip() for f in msg["functions"] if isinstance(f, str) and f.strip()]
            self.errors.pop(app, None)
            if app == "host" and (last := self.profiler.set(self.profile["host"])) is not None:
                self._write("profiles", [last])
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        tmp = os.path.join(self.dir, "state.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"on": self.on, "profile": self.profile}, f)
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
        events = [e for e in (event_of(x, app) for x in (msg.get("events") or [])[:EVENTS_MAX]) if e]
        if events:
            self._write("events", events)
        if app in PROFILED and (p := profile_of(msg.get("profile"), app)) is not None:
            self._write("profiles", [p])

    def turn(self, session_id, msg, model, effort, began):
        """A turn of a session of this host's ended (its result, msg): kept as the host's own turn_ended, with its
        model, effort and tokens, whichever app (or none) sent it. What tokens are added up from (usage)."""
        if not self.on:
            return
        u = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
        now = time.time()
        took = now - began if isinstance(began, (int, float)) and began else (
            msg["duration_ms"] / 1000 if _num(msg.get("duration_ms")) else None)
        e = event_of({"at": now, "kind": "turn_ended", "host": self.host_id, "session": session_id,
                      "seconds": took, "turn": msg.get("uuid"), "model": model, "effort": effort,
                      "tokens_in": sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                                    "cache_creation_input_tokens")),
                      "tokens_out": int(u.get("output_tokens") or 0)}, "host")
        if e is not None:
            self._write("events", [e])

    def _write(self, kind, rows):
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
        now = time.time() if now is None else now
        with self.lock:
            pids = dict(self.pids)
        stats = proc_stats(sorted(pids))
        procs = []
        for pid, app in pids.items():
            if pid not in stats:
                if app != "host":
                    with self.lock:
                        self.pids.pop(pid, None)
                    self.cpu.pop(pid, None)
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
        profiled = time.monotonic()
        while not self.wake.wait(SAMPLE_S):
            try:
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
    turn once (by its host, session and result's id). An app's turn_ended is the same turn seen there: not counted
    again."""
    seen, days, models, combos = set(), {}, {}, {}
    for e in events:
        if e.get("kind") != "turn_ended" or e.get("app") != "host":
            continue
        key = (e.get("host"), e.get("session"), e.get("turn") or round(e["at"]))
        if key in seen:
            continue
        seen.add(key)
        tokens = (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0)
        day = datetime.date.fromtimestamp(e["at"]).isoformat()
        d = days.setdefault(day, [0, 0])
        d[0], d[1] = d[0] + tokens, d[1] + 1
        if e.get("model"):
            for table, k in ((models, (e["model"], None)), (combos, (e["model"], e.get("effort")))):
                m = table.setdefault(k, [0, 0])
                m[0], m[1] = m[0] + 1, m[1] + tokens

    def ranked(table):
        return [{"model": m, "effort": eff, "turns": t, "tokens": tok}
                for (m, eff), (t, tok) in sorted(table.items(), key=lambda kv: (-kv[1][0], -kv[1][1], str(kv[0])))]
    return {"days": [{"day": d, "tokens": t, "turns": n} for d, (t, n) in sorted(days.items())],
            "models": ranked(models), "combos": ranked(combos)}


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


def gather(start, end=None, what=("events", "metrics", "profiles", "usage"), timeout=20):
    """Every host's telemetry in the mesh (this machine's host's members, and it), pulled by telemetry_read from here
    and put together: {"hosts": {id: reply or {"error"}}, "events": [...], "metrics": [...], "profiles": [...],
    "usage": ...}, each row with its host's id, the earliest first; usage added up over every host's events, each turn
    once. Every host's asked at once, and what hasn't answered within timeout (all told) is an error."""
    deadline = time.monotonic() + timeout
    ch, info = wire.reach({"via": "local"}, timeout=timeout)
    ch.close()
    wheres = [({"via": "local"}, info.get("id"))] + [
        ({"via": "mesh", "host": m["id"]}, m["id"]) for m in (info.get("mesh") or {}).get("members") or []
        if isinstance(m, dict) and wire.is_id(m.get("id")) and m["id"] != info.get("id")]
    replies, ask = {}, {"op": "telemetry_read", "from": start, "to": end, "what": [w for w in what if w != "usage"]
                        + (["events"] if "usage" in what and "events" not in what else []), "req": 1}

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
            if not done[0]:  # (an answer after gather's returned: dropped)
                replies[hid] = got
    threads = [threading.Thread(target=one, args=w, daemon=True) for w in wheres]
    for t in threads:
        t.start()
    for t in threads:
        t.join(max(0, deadline - time.monotonic()))
    with lock:
        done[0] = True
        hosts = {hid: replies.get(hid) or {"error": "no answer"} for _, hid in wheres}
    out = {"hosts": hosts}
    for kind in ("events", "metrics", "profiles"):
        out[kind] = sorted(({**r, "from_host": hid} for hid, rep in hosts.items() for r in rep.get(kind) or []),
                           key=lambda r: r["at"])
    if "usage" in what:
        out["usage"] = usage(out["events"])
    if "events" not in what:
        del out["events"]
    return out


if __name__ == "__main__":  # (python3 -m baudelib.telemetry [hours]: every host's last hours, as JSON)
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 1
    json.dump(gather(time.time() - hours * 3600), sys.stdout, indent=1)
