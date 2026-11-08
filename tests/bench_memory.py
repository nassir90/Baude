#!/usr/bin/env python3
"""Profile baude's memory, phase by phase, against copies of your own sessions (nothing real is
written: the sessions are cloned into a throwaway config, chats open in throwaway folders, and
claude is the fake from tests/).

    python3 tests/bench_memory.py                  # macOS: as is; Linux: under xvfb-run, like run.sh
    python3 tests/bench_memory.py --trace 15       # + the 15 lines that allocated the most per phase
    python3 tests/bench_memory.py --types 10       # + the 10 kinds of Python object that grew most
    python3 tests/bench_memory.py --sessions 4     # how many of the largest sessions to open

Each phase prints the process's memory as the OS counts it (macOS: the physical footprint Activity
Monitor shows; Linux: RSS), what malloc has out (exact, where the others keep freed memory a while),
what Python's own allocations add up to (with --trace), how many Python objects there are and how
many widgets the window has. Phases:

  started     the window, with the fake claude ready
  sidebar     every session listed (the copies of yours)
  turns       the sidebar refreshed as often as that many turns ending refresh it
  open N      the largest session opened (as the sidebar opens one: the newest part drawn)
  all of it   every earlier part of it drawn too
  switch      the next largest sessions opened, one after another
  small       a small session opened after them: what the big ones left behind
  signal      Signal on, for a chat in a copy of this repository
  pages       the Files, Git and Code pages opened (baude.py in the Code page)
  closed      the window closed and a new one opened: what a closed window leaves behind
"""
import argparse
import ctypes
import ctypes.util
import gc
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tracemalloc
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
REAL_PROJECTS = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")


# ---- the process's memory, as the OS counts it

class _VmInfo(ctypes.Structure):  # (the start of task_vm_info_data_t, up to phys_footprint)
    _fields_ = [(n, ctypes.c_uint64) for n in ("virtual_size",)] + [("region_count", ctypes.c_int32),
                                                                     ("page_size", ctypes.c_int32)] + \
               [(n, ctypes.c_uint64) for n in (
                   "resident_size", "resident_size_peak", "device", "device_peak", "internal", "internal_peak",
                   "external", "external_peak", "reusable", "reusable_peak", "purgeable_volatile_pmap",
                   "purgeable_volatile_resident", "purgeable_volatile_virtual", "compressed", "compressed_peak",
                   "compressed_lifetime", "phys_footprint")]


class _MallocStats(ctypes.Structure):  # (macOS: malloc_statistics_t)
    _fields_ = [("blocks_in_use", ctypes.c_uint), ("size_in_use", ctypes.c_size_t),
                ("max_size_in_use", ctypes.c_size_t), ("size_allocated", ctypes.c_size_t)]


class _MallInfo2(ctypes.Structure):  # (glibc: struct mallinfo2)
    _fields_ = [(n, ctypes.c_size_t) for n in ("arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks", "fsmblks",
                                               "uordblks", "fordblks", "keepcost")]


def os_memory():
    """{"footprint", "rss", "malloc"} in MB. Linux: footprint is RSS + swap. malloc is what malloc has handed out and
    not had back (GTK's widgets, Pango's layouts, textures' pixels, Python's bigger objects): unlike the others it
    doesn't count memory freed but kept for reuse, so it shows a phase's own cost exactly."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    if sys.platform == "darwin":
        info, count = _VmInfo(), ctypes.c_uint(ctypes.sizeof(_VmInfo) // 4)
        task = ctypes.c_uint.in_dll(libc, "mach_task_self_")
        ok = libc.task_info(task, 22, ctypes.byref(info), ctypes.byref(count)) == 0  # (22: TASK_VM_INFO)
        stats = _MallocStats()
        libc.malloc_zone_statistics(None, ctypes.byref(stats))
        return {"footprint": info.phys_footprint / 2**20 if ok else 0.0, "rss": info.resident_size / 2**20 if ok else 0.0,
                "malloc": stats.size_in_use / 2**20}
    got = {}
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(("VmRSS:", "VmSwap:")):
                got[line.split(":")[0]] = int(line.split()[1]) / 1024
    libc.mallinfo2.restype = _MallInfo2
    mi = libc.mallinfo2()
    return {"footprint": got.get("VmRSS", 0) + got.get("VmSwap", 0), "rss": got.get("VmRSS", 0),
            "malloc": (mi.uordblks + mi.hblkhd) / 2**20}


# ---- the sessions: copies of yours

def clone_projects(dest):
    """Copy the real projects folder (on APFS, clones: no space used, nearly instant)."""
    if sys.platform == "darwin":
        subprocess.run(["cp", "-cR", REAL_PROJECTS, dest], check=True)
    else:
        subprocess.run(["cp", "-R", "--reflink=auto", REAL_PROJECTS, dest], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", type=int, default=0, help="top allocation sites per phase (tracemalloc)")
    ap.add_argument("--frames", type=int, default=1, help="traceback depth for --trace")
    ap.add_argument("--types", type=int, default=0, help="Python object kinds that grew most per phase")
    ap.add_argument("--sessions", type=int, default=4, help="largest sessions to open")
    ap.add_argument("--turns", type=int, default=20, help="sidebar refreshes, as that many turns ending make")
    ap.add_argument("--skip", default="", help="phases to skip, comma-separated (signal, pages, closed)")
    ap.add_argument("--json", help="also write the phases' numbers here")
    ap.add_argument("--footprint", help="(macOS) save `footprint -p` of each phase in this folder")
    args = ap.parse_args()
    skip = set(filter(None, args.skip.split(",")))
    if args.trace:
        tracemalloc.start(args.frames)

    rows, last = [], {"snap": None, "types": None}
    import test_baude as T  # noqa: E402  (throwaway config dirs and the fake claude from here on)
    from test_baude import baude  # noqa: E402

    def settle(seconds=1.5, quiet=0.4):
        """Run the main loop until nothing much happens for `quiet` (layout done, pictures decoded)."""
        ctx = T.GLib.MainContext.default()
        end, calm = time.monotonic() + seconds, time.monotonic()
        while time.monotonic() < end or time.monotonic() - calm < quiet:
            t = time.monotonic()
            busy = ctx.iteration(False)
            if busy and time.monotonic() - t > 0.005:
                calm = time.monotonic()
            if time.monotonic() > end + 20:
                break
            time.sleep(0.002)

    def measure(name, win=None):
        settle()
        gc.collect()
        m = os_memory()
        row = {"phase": name, **m, "objects": len(gc.get_objects()),
               "widgets": sum(1 for _ in T.walk(win)) if win is not None else 0}
        if args.trace:
            snap = tracemalloc.take_snapshot().filter_traces([tracemalloc.Filter(False, tracemalloc.__file__)])
            row["python"] = sum(s.size for s in snap.statistics("filename")) / 2**20
            if last["snap"] is not None:
                key = "traceback" if args.frames > 1 else "lineno"
                top = snap.compare_to(last["snap"], key)[:args.trace]
                row["top"] = [(s.size_diff / 2**20, s.count_diff, s.traceback.format(limit=args.frames))
                              for s in top]
            last["snap"] = snap
        if args.types:
            counts = Counter(type(o).__name__ for o in gc.get_objects())
            if last["types"] is not None:
                row["grew"] = (counts - last["types"]).most_common(args.types)
            last["types"] = counts
        rows.append(row)
        if args.footprint:
            os.makedirs(args.footprint, exist_ok=True)
            out = os.path.join(args.footprint, f"{len(rows):02d} {re.sub(r'\W+', '-', name)}.txt")
            with open(out, "w") as f:
                subprocess.run(["footprint", "-p", str(os.getpid())], stdout=f, stderr=subprocess.STDOUT)
        prev = rows[-2] if len(rows) > 1 else None
        delta = f"{m['footprint'] - prev['footprint']:+7.0f}" if prev else "       "
        py = f"{row['python']:7.0f} MB py" if "python" in row else ""
        print(f"{name:28} {m['footprint']:7.0f} MB {delta}   rss {m['rss']:6.0f}   malloc {m['malloc']:6.0f}   {py}  "
              f"{row['objects']:>9,} objects  {row['widgets']:>7,} widgets", flush=True)
        for size, count, tb in row.get("top", []):
            where = "\n                ".join(l.strip() for l in tb if l.strip())
            print(f"      {size:+8.1f} MB {count:+9,}  {where}")
        for kind, n in row.get("grew", []):
            print(f"      {n:+9,}  {kind}")
        return row

    print(f"{'phase':28} {'memory':>10} {'change':>7}", flush=True)
    measure("imported")
    projects = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects")
    os.makedirs(os.path.dirname(projects), exist_ok=True)
    clone_projects(projects)
    files = sorted((os.path.getsize(os.path.join(d, n)), os.path.join(d, n))
                   for d, _, names in os.walk(projects) for n in names if n.endswith(".jsonl"))
    print(f"(sessions: {len(files)}, {sum(s for s, _ in files) / 2**30:.1f} GB; largest "
          f"{', '.join(f'{s / 2**20:.0f}' for s, _ in files[::-1][:args.sessions])} MB)", flush=True)

    folder = os.path.join(T.TMP, "work")  # (chats open here, not in their real folders)
    os.makedirs(folder)
    app = T.App()
    win = app.window(T.argparse.Namespace(cwd=folder, model=None, resume=None))
    measure("started", win)
    T.wait_for(lambda: not win.sidebar.loading and len(win.sidebar.sessions) > 1, "the sidebar", 120)
    measure(f"sidebar ({len(win.sidebar.sessions)} sessions)", win)
    for _ in range(args.turns):  # (each turn's end re-reads the session list, and the sidebar is filled again)
        win.sidebar.refresh()
        T.wait_for(lambda: not win.sidebar.loading, "the sidebar again", 120)
    measure(f"{args.turns} turns ended", win)

    def open_chat(path):
        info = baude.cached_session_info(path) or {}
        info = {**info, "id": os.path.basename(path)[:-6], "path": path, "cwd": folder}
        win.resume_session(info)
        return info

    big = [p for _, p in files[::-1][:args.sessions]]
    open_chat(big[0])
    measure(f"open largest ({os.path.getsize(big[0]) / 2**20:.0f} MB)", win)
    while win.history and win.history.unshown:
        win.show_earlier()
        settle(0.05, 0.05)
    measure("all of it drawn", win)
    for p in big[1:]:
        open_chat(p)
        measure(f"switch ({os.path.getsize(p) / 2**20:.0f} MB)", win)
    small = next(p for s, p in files if s > 20_000)
    open_chat(small)
    measure("small chat after them", win)

    if "signal" not in skip:
        repo = os.path.join(T.TMP, "repo")
        subprocess.run(["git", "clone", "-q", "--local", ROOT, repo], check=True)
        here = os.path.join(ROOT, "")  # this repository's own chats, moved to the copy
        mine = os.path.join(REAL_PROJECTS, re.sub(r"[^A-Za-z0-9]", "-", ROOT))
        chats = sorted((os.path.getsize(os.path.join(mine, n)), os.path.join(mine, n))
                       for n in os.listdir(mine) if n.endswith(".jsonl")) if os.path.isdir(mine) else []
        if chats:
            d = os.path.join(projects, re.sub(r"[^A-Za-z0-9]", "-", repo))
            os.makedirs(d, exist_ok=True)
            src = chats[-1][1]
            dst = os.path.join(d, os.path.basename(src))
            with open(src, encoding="utf-8", errors="replace") as f, open(dst, "w") as out:
                for line in f:
                    out.write(line.replace(here, repo + "/").replace(f'"{ROOT}"', json.dumps(repo)))
            info = {**(baude.cached_session_info(dst) or {}), "id": os.path.basename(dst)[:-6], "path": dst,
                    "cwd": repo}
            win.resume_session(info)
            settle()
            win.signal_run.set_active(True)
            sig = win.signal
            settle(3)
            T.wait_for(lambda: sig.activity is None and not sig.waiting and sig.jobs.empty(), "Signal to finish", 600)
            measure(f"signal ({os.path.getsize(dst) / 2**20:.0f} MB chat)", win)

    if "pages" not in skip:
        win.show_panel(True, "files")
        settle()
        win.show_panel(True, "git")
        T.wait_for(lambda: win.panel.git_page.commits, "the git log", 60)
        win.show_code(os.path.join(ROOT, "baude.py"), 9000)
        measure("pages (files, git, code)", win)

    if "closed" not in skip:
        T.close(win)
        win = app.window(T.argparse.Namespace(cwd=folder, model=None, resume=None))
        T.wait_for(lambda: not win.sidebar.loading and len(win.sidebar.sessions) > 1, "the sidebar", 120)
        measure("closed, a new window", win)
    T.close(win)
    if args.json:
        with open(args.json, "w") as f:
            json.dump([{k: v for k, v in r.items() if k not in ("top", "grew")} for r in rows], f, indent=1)
    shutil.rmtree(T.TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
