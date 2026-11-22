#!/usr/bin/env python3
"""Measure how smoothly baude follows a session: tailing a transcript another program is
writing, and streaming a long reply from its own claude.

    python3 tests/bench_tail.py FIXTURE.jsonl [--base N] [--replay N] [--out result.json]

Tailing: the first --base lines of FIXTURE are opened read-only; the next --replay lines are
then appended by a writer thread at the pace their own timestamps give (gaps divided by 8,
clamped to 20-400 ms), as Claude Desktop or a terminal would write them.
Streaming: the longest assistant text in FIXTURE is fed through h_stream_event in 12-character
deltas every 15 ms, the shape the CLI's partial messages have.

Reported, for each phase:
  latency   line written -> first frame painted with it on screen (tailing only)
  stalls    main loop late by > 33 ms (a 5 ms heartbeat), and the worst one
  frames    paints during the phase, and gaps between them > 50 ms while content was arriving
  jumps     frames where the view moved > 120 px at once (snaps), and the biggest
  clipped   frames whose scroll position leaves the end of the content below the fold
  hidden    frames where the end of the transcript, as painted, is under the bottom edge
  behind    frames painted with the newest content still below the fold while following
Nothing here touches real sessions or settings (see test_baude's throwaway dirs).
"""
import argparse
import json
import os
import shutil
import statistics
import sys
import threading
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_baude as T  # noqa: E402
from test_baude import GLib, baude  # noqa: E402


def stamp(line):
    try:
        ts = json.loads(line).get("timestamp")
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() if ts else None
    except (ValueError, AttributeError):
        return None


class Monitor:
    """Watches the window's frame clock, the main loop's punctuality and the scroll position."""

    def __init__(self, win):
        self.win, self.adj = win, win.scroller.get_vadjustment()
        self.paints, self.late = [], []
        self.on = False
        self.clock = win.get_frame_clock()
        self.paint_id = self.clock.connect("after-paint", self.on_paint)
        self.expect = time.perf_counter() + 0.005
        GLib.timeout_add(5, self.beat)

    def beat(self):
        now = time.perf_counter()
        if self.on:
            self.late.append(now - self.expect)
        self.expect = now + 0.005
        return True

    def on_paint(self, _clock):
        if self.on:
            a = self.adj
            self.paints.append((time.perf_counter(), a.get_value(), a.get_upper(), a.get_page_size(),
                                self.hidden_px()))

    def hidden_px(self):
        """How far the end of the transcript is below the visible area, as allocated (what was
        actually painted, not what the scroll position claims)."""
        ok, rect = self.win.transcript.compute_bounds(self.win.scroller)
        return max(0.0, rect.get_y() + rect.get_height() - self.win.scroller.get_height()) if ok else 0.0

    def start(self):
        self.paints, self.late, self.on = [], [], True

    def stop(self):
        self.on = False

    def summary(self, active_until=None):
        late = [x * 1000 for x in self.late]
        stalls = [x for x in late if x > 33]
        out = {"stalls>33ms": len(stalls), "worst_stall_ms": round(max(late or [0]), 1),
               "frames": len(self.paints)}
        gaps = [(b[0] - a[0]) * 1000 for a, b in zip(self.paints, self.paints[1:])
                if active_until is None or b[0] <= active_until]
        out["frame_gaps>50ms"] = sum(1 for g in gaps if g > 50)
        moves = [abs(b[1] - a[1]) for a, b in zip(self.paints, self.paints[1:])]
        out["jumps>120px"] = sum(1 for m in moves if m > 120)
        out["biggest_jump_px"] = round(max(moves or [0]))
        # the transcript has a 12 px bottom margin: past that, the newest text is cut off
        out["frames_clipped>12px"] = sum(1 for _t, v, u, p, _h in self.paints if u - (v + p) > 12)
        out["frames_last_line_hidden"] = sum(1 for *_x, h in self.paints if h > 2)
        out["worst_hidden_px"] = round(max([h for *_x, h in self.paints] or [0]))
        out["frames_behind>120px"] = sum(1 for _t, v, u, p, _h in self.paints if u - (v + p) > 120)
        return out


def pump_until(cond, timeout):
    end = time.perf_counter() + timeout
    ctx = GLib.MainContext.default()
    while time.perf_counter() < end and not cond():
        ctx.iteration(False)
        time.sleep(0.001)


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0


def bench_tail(app, lines, base, replay, path):
    with open(path, "w") as f:
        f.writelines(lines[:base])
    win = app.window()
    win.set_default_size(1100, 820)
    win.resume_pending = True
    win.view_session(path)
    T.pump(2.0)
    mon = Monitor(win)

    # line end offsets of what the writer adds, and when each was written
    chunk = lines[base:base + replay]
    offsets, pos = [], os.path.getsize(path)
    for line in chunk:
        pos += len(line.encode())
        offsets.append(pos)
    written = [None] * len(chunk)
    stamps = [stamp(l) for l in chunk]

    # when the window had read (and drawn) up to which byte: sampled at every paint
    seen = []
    mon.clock.connect("after-paint", lambda _c: mon.on and seen.append((time.perf_counter(), win.tail.offset)))

    def writer():
        with open(path, "a") as f:
            for i, line in enumerate(chunk):
                f.write(line)
                f.flush()
                written[i] = time.perf_counter()
                a, b = stamps[i], stamps[i + 1] if i + 1 < len(chunk) else None
                gap = (b - a) / 8 if a and b else 0.1
                time.sleep(min(0.4, max(0.02, gap)))

    mon.start()
    t0 = time.perf_counter()
    th = threading.Thread(target=writer, daemon=True)
    th.start()
    pump_until(lambda: not th.is_alive(), 600)
    active_until = time.perf_counter()
    pump_until(lambda: win.tail.offset >= offsets[-1], 10)
    T.pump(1.5)
    mon.stop()

    lat = []
    for off, w in zip(offsets, written):
        hit = next((t for t, o in seen if o >= off and t >= w), None)
        if hit is not None:
            lat.append((hit - w) * 1000)
    res = {"lines": len(chunk), "seconds": round(active_until - t0, 1),
           "latency_p50_ms": round(pct(lat, .5)), "latency_p95_ms": round(pct(lat, .95)),
           "latency_max_ms": round(max(lat or [0])), **mon.summary(active_until)}
    T.close(win)
    return res


def bench_stream(app, lines):
    texts = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") == "assistant":
            for c in rec.get("message", {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "text":
                    texts.append(c["text"])
    text = "\n\n".join(sorted(texts, key=len, reverse=True)[:3])[:6000]
    win = app.window()
    win.set_default_size(1100, 820)
    T.pump(1.0)
    mon = Monitor(win)
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

    mon.start()
    t0 = time.perf_counter()
    GLib.timeout_add(15, feed)
    pump_until(lambda: state["i"] >= len(deltas), 300)
    active_until = time.perf_counter()
    T.pump(1.0)
    mon.stop()
    res = {"chars": len(text), "deltas": len(deltas), "seconds": round(active_until - t0, 1),
           "expected_s": round(len(deltas) * 0.015, 1), **mon.summary(active_until)}
    T.close(win)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture")
    ap.add_argument("--base", type=int, default=300)
    ap.add_argument("--replay", type=int, default=220)
    ap.add_argument("--out")
    ap.add_argument("--only", choices=["tail", "stream"])
    args = ap.parse_args()
    with open(args.fixture, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    copy = os.path.join(T.TMP, "tailed.jsonl")
    app = T.App()
    result = {}
    if args.only != "stream":
        result["tail"] = bench_tail(app, lines, args.base, args.replay, copy)
    if args.only != "tail":
        result["stream"] = bench_stream(app, lines[:args.base + args.replay])
    for phase, r in result.items():
        print(f"\n{phase}")
        for k, v in r.items():
            print(f"  {k:22} {v}")
    if args.out:
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)
    shutil.rmtree(T.TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
