"""What a running Baude or Record holds, for its debug report (kill -USR2 <pid>, appended to
~/.cache/baude/debug-<pid>.txt): the memory the OS counts and what malloc has out, Python's objects by kind and how
each kind changed since the last report, and, started with PYTHONTRACEMALLOC=<frames>, the lines that allocated
most since then (each snapshot kept beside the report, debug-<pid>-<n>.tracemalloc, for tracemalloc's own compare).
No GTK here: tests/bench_leaks.py reads the same numbers."""
import ctypes
import ctypes.util
import gc
import os
import sys
import time
import tracemalloc
from collections import Counter


class _VmInfo(ctypes.Structure):  # (the start of macOS's task_vm_info_data_t, up to phys_footprint)
    _fields_ = [("virtual_size", ctypes.c_uint64), ("region_count", ctypes.c_int32), ("page_size", ctypes.c_int32)] + \
               [(n, ctypes.c_uint64) for n in (
                   "resident_size", "resident_size_peak", "device", "device_peak", "internal", "internal_peak",
                   "external", "external_peak", "reusable", "reusable_peak", "purgeable_volatile_pmap",
                   "purgeable_volatile_resident", "purgeable_volatile_virtual", "compressed", "compressed_peak",
                   "compressed_lifetime", "phys_footprint")]


class _MallocStats(ctypes.Structure):  # (macOS's malloc_statistics_t)
    _fields_ = [("blocks_in_use", ctypes.c_uint), ("size_in_use", ctypes.c_size_t),
                ("max_size_in_use", ctypes.c_size_t), ("size_allocated", ctypes.c_size_t)]


class _MallInfo2(ctypes.Structure):  # (glibc's struct mallinfo2)
    _fields_ = [(n, ctypes.c_size_t) for n in ("arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks", "fsmblks",
                                               "uordblks", "fordblks", "keepcost")]


def _purgeable(libc):
    """macOS: what malloc's purgeable zone has out (a _MallocStats), if there's one yet. It holds caches the system
    may take back (CoreGraphics' decoded images: each Dock tile set keeps one, the last 16 kept), outside the
    footprint while unused."""
    zones, n = ctypes.POINTER(ctypes.c_void_p)(), ctypes.c_uint()
    libc.malloc_get_all_zones.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    libc.malloc_get_zone_name.restype, libc.malloc_get_zone_name.argtypes = ctypes.c_char_p, [ctypes.c_void_p]
    stats = _MallocStats()
    if libc.malloc_get_all_zones(0, None, ctypes.byref(zones), ctypes.byref(n)) == 0:
        for zone in (zones[i] for i in range(n.value)):
            if b"Purgeable" in (libc.malloc_get_zone_name(zone) or b""):
                libc.malloc_zone_statistics(ctypes.c_void_p(zone), ctypes.byref(stats))
    return stats


def memory():
    """{"footprint", "malloc", "blocks", "purgeable"}, in MB (blocks: how many malloc has out). footprint is what
    Activity Monitor shows (Linux: RSS and swap); malloc doesn't count memory freed but kept for reuse, so it shows
    growth exactly. purgeable (macOS): malloc's caches the system may take back (_purgeable), not in malloc's."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    if sys.platform == "darwin":
        info, count = _VmInfo(), ctypes.c_uint(ctypes.sizeof(_VmInfo) // 4)
        ok = libc.task_info(ctypes.c_uint.in_dll(libc, "mach_task_self_"), 22, ctypes.byref(info),
                            ctypes.byref(count)) == 0  # (22: TASK_VM_INFO)
        stats, cache = _MallocStats(), _purgeable(libc)
        libc.malloc_zone_statistics(None, ctypes.byref(stats))
        return {"footprint": info.phys_footprint / 2**20 if ok else 0.0,
                "malloc": (stats.size_in_use - cache.size_in_use) / 2**20,
                "blocks": stats.blocks_in_use - cache.blocks_in_use, "purgeable": cache.size_in_use / 2**20}
    got = {}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith(("VmRSS:", "VmSwap:")):
                    got[line.split(":")[0]] = int(line.split()[1]) / 1024
        libc.mallinfo2.restype = _MallInfo2
        mi = libc.mallinfo2()
        used, blocks = (mi.uordblks + mi.hblkhd) / 2**20, mi.ordblks + mi.hblks
    except (OSError, AttributeError):
        used, blocks = 0.0, 0
    return {"footprint": got.get("VmRSS", 0) + got.get("VmSwap", 0), "malloc": used, "blocks": blocks,
            "purgeable": 0.0}


_LAST = {"kinds": None, "snapshot": None, "n": 0}


def write(report, path=None, top=25):
    """The memory section of a debug report, written to report (a file); path: the report's, beside which a
    tracemalloc snapshot is kept."""
    gc.collect()
    m = memory()
    kinds = Counter(type(o).__name__ for o in gc.get_objects())
    report.write(f"\n--- memory: footprint {m['footprint']:.0f} MB, malloc {m['malloc']:.1f} MB in {m['blocks']:,} "
                 f"blocks (and {m['purgeable']:.1f} MB of purgeable caches), {sum(kinds.values()):,} Python objects\n")
    if _LAST["kinds"] is not None:
        grew = (kinds - _LAST["kinds"]).most_common(top)
        report.write("Python objects, the kinds that grew since the last report:\n" +
                     "".join(f"  {n:+10,}  {k}\n" for k, n in grew))
    report.write("Python objects, the most numerous:\n" +
                 "".join(f"  {n:10,}  {k}\n" for k, n in kinds.most_common(top)))
    _LAST["kinds"] = kinds
    if not tracemalloc.is_tracing():
        report.write("(no allocation sites: start it with PYTHONTRACEMALLOC=25 for them)\n")
        return
    snap = tracemalloc.take_snapshot().filter_traces([tracemalloc.Filter(False, tracemalloc.__file__)])
    _LAST["n"] += 1
    if path:
        snap.dump(f"{os.path.splitext(path)[0]}-{_LAST['n']}.tracemalloc")
    frames = tracemalloc.get_traceback_limit()
    if _LAST["snapshot"] is not None:
        stats, what = snap.compare_to(_LAST["snapshot"], "traceback" if frames > 1 else "lineno"), "since the last report"
    else:
        stats, what = snap.statistics("traceback" if frames > 1 else "lineno"), "in all"
    report.write(f"Python allocations, the sites that allocated most {what} "
                 f"({sum(s.size for s in snap.statistics('filename')) / 2**20:.1f} MB traced):\n")
    for s in stats[:top]:
        size = getattr(s, "size_diff", s.size)
        count = getattr(s, "count_diff", s.count)
        where = "\n      ".join(line.strip() for line in s.traceback.format(limit=min(frames, 6)) if line.strip())
        report.write(f"  {size / 1024:+10.1f} KB {count:+8,}  {where}\n")
    _LAST["snapshot"] = snap


def listen(path, app_name):
    """kill -USR2 <pid>: the memory section appended to path (Record's debug report; Baude's state() writes it
    with its windows'). Returns path, or None if it can't be written."""
    import gi
    from gi.repository import GLib
    import signal
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        report = open(path, "a", buffering=1)
    except OSError:
        return None

    def state():
        report.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  {app_name} (pid {os.getpid()})\n")
        write(report, path)
        return True  # (keep listening)
    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix
        GLibUnix.signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR2, state)
    except (ImportError, ValueError):  # older GLib
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR2, state)
    return path
