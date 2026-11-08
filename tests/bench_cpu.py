#!/usr/bin/env python3
"""Load-test baude's CPU, scenario by scenario, against copies of your own sessions (nothing real is
written: see bench_memory.py, whose set-up this shares).

    python3 tests/bench_cpu.py                     # macOS: as is; Linux: under xvfb-run, like run.sh
    python3 tests/bench_cpu.py --profile 12        # + the 12 functions the process spent most in
    python3 tests/bench_cpu.py --only tail,stream  # some scenarios only

Each scenario prints the wall time it took, the CPU the whole process used meanwhile (every thread:
Signal's worker, the session scan, image decoding) and the main thread's own share, and the longest
the main loop went without running (a freeze you'd see). Scenarios:

  idle        a window doing nothing for a while, the sessions listed (what its timers and animations cost)
  behind      the same with another window in front of it (where it is while Claude works, mostly)
  viewing     the same, showing the largest session (read-only, followed as it grows)
  start       a second window opening, its sidebar filled from the saved index
  turns       the sidebar refreshed as each turn's end refreshes it
  open        the largest session opened
  scroll      all of it drawn, a chunk at a time, as scrolling up does
  stream      a long reply streamed in, 12 characters every 15 ms (the CLI's partial messages)
  tail        another program appending to a big session you're viewing (Claude Desktop, a terminal)
  signal      the same with Signal on, for a chat in a copy of this repository
"""
import argparse
import cProfile
import json
import os
import pstats
import re
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import bench_memory as M  # noqa: E402  (clone_projects, os_memory, REAL_PROJECTS)

SCENARIOS = ["idle", "behind", "viewing", "start", "turns", "open", "scroll", "stream", "tail", "signal"]


class Meter:
    """Runs the main loop the way the app's own does (blocking until something's due), with a 10 ms heartbeat:
    how late a beat comes is how long the UI froze. (Polling it instead would cost more CPU than an idle app.)"""

    BEAT = 0.01

    def __init__(self):
        self.ctx = M_GLib().MainContext.default()
        self.worst, self.expect = 0.0, None
        M_GLib().timeout_add(int(self.BEAT * 1000), self.beat)

    def beat(self):
        now = time.monotonic()
        if self.expect is not None:
            self.worst = max(self.worst, now - self.expect)
        self.expect = now + self.BEAT
        return True

    def pump(self, seconds=0.0, until=None, quiet=None, limit=600):
        """Run for `seconds`, or until until() holds; with quiet, also until no beat came late for that long."""
        start = calm = time.monotonic()
        while True:
            now = time.monotonic()
            done = (until() if until else now - start >= seconds) and (quiet is None or now - calm >= quiet)
            if done or now - start > limit:
                return
            before = self.worst
            self.ctx.iteration(True)  # (the heartbeat wakes it at least every 10 ms)
            if self.worst > before or (self.expect is not None and time.monotonic() - self.expect > 0.02):
                calm = time.monotonic()

    def __enter__(self):
        self.worst, self.expect = 0.0, time.monotonic() + self.BEAT  # (work done before the first beat counts too)
        self.wall, self.cpu, self.main = time.perf_counter(), time.process_time(), time.thread_time()
        return self

    def __exit__(self, *exc):
        self.wall = time.perf_counter() - self.wall
        self.cpu = time.process_time() - self.cpu
        self.main = time.thread_time() - self.main


def M_GLib():
    from test_baude import GLib
    return GLib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", type=int, default=0,
                    help="top functions per scenario (every thread's: cProfile sees them all from Python 3.12)")
    ap.add_argument("--only", default="", help="scenarios to run, comma-separated (default: all)")
    ap.add_argument("--seconds", type=float, default=15, help="how long the idle scenarios last")
    ap.add_argument("--json", help="also write the results here")
    args = ap.parse_args()
    only = [s for s in args.only.split(",") if s] or SCENARIOS
    rows = []

    import test_baude as T  # noqa: E402  (throwaway config dirs and the fake claude from here on)
    from test_baude import baude, GLib  # noqa: E402

    projects = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects")
    os.makedirs(os.path.dirname(projects), exist_ok=True)
    M.clone_projects(projects)
    files = sorted((os.path.getsize(os.path.join(d, n)), os.path.join(d, n))
                   for d, _, names in os.walk(projects) for n in names if n.endswith(".jsonl"))
    largest = files[-1][1]
    print(f"(sessions: {len(files)}, {sum(s for s, _ in files) / 2**30:.1f} GB; largest {files[-1][0] / 2**20:.0f} MB)")
    folder = os.path.join(T.TMP, "work")
    os.makedirs(folder)
    meter = Meter()

    def run(name, fn, note=""):
        prof = cProfile.Profile() if args.profile else None
        with meter:
            if prof:
                prof.enable()
            extra = fn() or ""
            if prof:
                prof.disable()
        row = {"scenario": name, "wall_s": round(meter.wall, 2), "cpu_s": round(meter.cpu, 2),
               "main_s": round(meter.main, 2), "worst_ms": round(meter.worst * 1000), "note": f"{note}{extra}"}
        rows.append(row)
        print(f"{name:10} {meter.wall:7.2f} s wall {meter.cpu:7.2f} s cpu ({100 * meter.cpu / max(meter.wall, 1e-9):4.0f}%)"
              f" {meter.main:7.2f} s main   longest freeze {meter.worst * 1000:5.0f} ms   {row['note']}", flush=True)
        if prof:
            stats = pstats.Stats(prof)
            stats.sort_stats("tottime")
            width = len(ROOT) + 1
            for (path, line, func), (cc, nc, tt, ct, _callers) in sorted(
                    stats.stats.items(), key=lambda kv: -kv[1][2])[:args.profile]:
                where = f"{path[width:] if path.startswith(ROOT) else os.path.basename(path)}:{line} {func}"
                print(f"      {tt:7.3f} s self {ct:7.3f} s total {nc:>9,} calls  {where}")
        return row

    app = T.App()
    win = app.window(T.argparse.Namespace(cwd=folder, model=None, resume=None))
    meter.__enter__()
    meter.pump(until=lambda: not win.sidebar.loading and len(win.sidebar.sessions) > 1, quiet=1.0, limit=180)
    baude.save_info_cache(force=True)  # (what a second start reads)

    def open_chat(path, cwd=folder, w=None):
        w = w or win
        info = {**(baude.cached_session_info(path) or {}), "id": os.path.basename(path)[:-6], "path": path, "cwd": cwd}
        w.resume_session(info)

    if "idle" in only:
        run("idle", lambda: meter.pump(args.seconds), f"{args.seconds:.0f} s, {len(win.sidebar.sessions)} sessions listed")
    if "behind" in only:
        front = T.Gtk.Window(application=app.app, title="in front")
        front.present()
        meter.pump(1, quiet=0.5)
        run("behind", lambda: meter.pump(args.seconds), f"{args.seconds:.0f} s, another window in front")
        front.destroy()
        win.present()
        meter.pump(1, quiet=0.5)
    if "viewing" in only:
        open_chat(largest)
        meter.pump(1, quiet=1.0)
        run("viewing", lambda: meter.pump(args.seconds), f"{args.seconds:.0f} s, the largest session shown")
    if "start" in only:
        def start():
            other = app.window(T.argparse.Namespace(cwd=folder, model=None, resume=None))
            meter.pump(until=lambda: not other.sidebar.loading and len(other.sidebar.sessions) > 1, quiet=0.5)
            T.close(other)
        run("start", start, "a second window, its sidebar from the saved index")
    if "turns" in only:
        def turns(n=10):
            for _ in range(n):
                win.sidebar.refresh()
                meter.pump(until=lambda: not win.sidebar.loading)
            meter.pump(0.2)
            return f"10 refreshes, {len(win.sidebar.sessions)} sessions"
        run("turns", turns)
    if "open" in only:
        def open_largest():
            open_chat(largest)
            meter.pump(0.5, until=lambda: not (win.history and win.history.todo), quiet=0.5)
        run("open", open_largest, f"{os.path.getsize(largest) / 2**20:.0f} MB (its start indexed between frames too)")
    if "scroll" in only:
        def scroll():
            n = 0
            while win.history and win.history.unshown:
                win.show_earlier()
                meter.pump(0.02)
                n += 1
            meter.pump(0.5, quiet=0.5)
            return f"{n} chunks"
        run("scroll", scroll)

    if "stream" in only:
        texts = []
        with open(largest, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"type":"assistant"' in line and '"text"' in line:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    texts += [c["text"] for c in rec.get("message", {}).get("content") or []
                              if isinstance(c, dict) and c.get("type") == "text"]
        text = "\n\n".join(sorted(texts, key=len, reverse=True)[:4])[:12000]
        win.new_session()
        meter.pump(1, quiet=0.5)

        def stream():
            deltas = [text[i:i + 12] for i in range(0, len(text), 12)]
            win.h_stream_event({"event": {"type": "message_start", "message": {"id": "bench"}}})
            win.h_stream_event({"event": {"type": "content_block_start", "index": 0,
                                          "content_block": {"type": "text", "text": ""}}})
            state = {"i": 0}

            def feed():
                if state["i"] >= len(deltas):
                    return False
                win.h_stream_event({"event": {"type": "content_block_delta", "index": 0,
                                              "delta": {"type": "text_delta", "text": deltas[state["i"]]}}})
                state["i"] += 1
                return True
            GLib.timeout_add(15, feed)
            meter.pump(until=lambda: state["i"] >= len(deltas))
            meter.pump(0.5)
            return f"{len(text):,} characters in {len(deltas)} deltas ({len(deltas) * 0.015:.0f} s of feed)"
        run("stream", stream)

    def tail(path, lines, w):
        """Another program appends `lines` to path, ~6 a second, while w shows it."""
        def writer():
            with open(path, "a") as f:
                for line in lines:
                    f.write(line)
                    f.flush()
                    time.sleep(0.15)
        th = threading.Thread(target=writer, daemon=True)
        th.start()
        meter.pump(until=lambda: not th.is_alive())
        meter.pump(1.0, quiet=0.5)
        return f"{len(lines)} lines appended"

    if "tail" in only:
        big = [p for s, p in files if s < files[-1][0]][-1]  # (the second largest: a copy that grows)
        copy = os.path.join(os.path.dirname(big), "tailed-" + os.path.basename(big))
        with open(big, encoding="utf-8", errors="replace") as f:
            src = f.readlines()
        with open(copy, "w") as f:
            f.writelines(src[:-120])
        open_chat(copy)
        meter.pump(1, quiet=1.0)
        run("tail", lambda: tail(copy, src[-120:], win), f"{os.path.getsize(copy) / 2**20:.0f} MB session, ")

    if "signal" in only:
        repo = os.path.join(T.TMP, "repo")
        subprocess.run(["git", "clone", "-q", "--local", ROOT, repo], check=True)
        mine = os.path.join(M.REAL_PROJECTS, re.sub(r"[^A-Za-z0-9]", "-", ROOT))
        chats = sorted((os.path.getsize(os.path.join(mine, n)), os.path.join(mine, n))
                       for n in os.listdir(mine) if n.endswith(".jsonl")) if os.path.isdir(mine) else []
        if chats:
            d = os.path.join(projects, re.sub(r"[^A-Za-z0-9]", "-", repo))
            os.makedirs(d, exist_ok=True)
            with open(chats[-1][1], encoding="utf-8", errors="replace") as f:
                src = [l.replace(os.path.join(ROOT, ""), repo + "/").replace(f'"{ROOT}"', json.dumps(repo)) for l in f]
            dst = os.path.join(d, os.path.basename(chats[-1][1]))
            with open(dst, "w") as f:
                f.writelines(src[:-120])
            open_chat(dst, repo)
            meter.pump(1, quiet=0.5)
            win.signal_run.set_active(True)
            sig = win.signal
            meter.pump(until=lambda: sig.activity is None and not sig.waiting and sig.jobs.empty(), quiet=2.0,
                       limit=900)
            run("signal", lambda: tail(dst, src[-120:], win), f"{os.path.getsize(dst) / 2**20:.0f} MB chat, ")

    T.close(win)
    mem = M.os_memory()
    print(f"(memory at the end: {mem['footprint']:.0f} MB footprint, malloc {mem['malloc']:.0f} MB)")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(rows, f, indent=1)
    shutil.rmtree(T.TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
