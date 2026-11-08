#!/usr/bin/env python3
"""Benchmark loading a saved session into baude (headless; run via tests/bench_history.sh).

    tests/bench_history.sh                      # the largest session on this machine
    tests/bench_history.sh PATH_OR_SESSION_ID   # a specific one

The transcript is copied first, so a session that's still being written to is never touched.
claude itself is the fake from tests/, so nothing is resumed or sent anywhere.
"""
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

REAL_PROJECTS = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")
import test_baude as T  # noqa: E402  (fake claude + throwaway config dirs from here on)
from test_baude import Gtk, GLib, baude  # noqa: E402


def rss_mb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    return 0.0


def pick_session(arg):
    if arg and os.path.exists(arg):
        return arg
    candidates = []
    for d in os.listdir(REAL_PROJECTS):
        for name in os.listdir(os.path.join(REAL_PROJECTS, d)):
            if name.endswith(".jsonl") and (not arg or name.startswith(arg)):
                p = os.path.join(REAL_PROJECTS, d, name)
                candidates.append((os.path.getsize(p), p))
    if not candidates:
        sys.exit(f"no session matching {arg!r}")
    return max(candidates)[1]


class Stopwatch:
    def __init__(self):
        self.rows = []

    def run(self, name, fn):
        t = time.perf_counter()
        out = fn()
        self.rows.append((name, time.perf_counter() - t))
        return out


def settle(adj=None, max_s=20.0, quiet_s=0.5):
    """Run the main loop until the layout is stable (the scroll height unchanged for quiet_s
    with no slow iteration). The loop never goes fully idle: the text cursor blinks.
    Returns (seconds until stable, longest single iteration)."""
    ctx = GLib.MainContext.default()
    start = last_change = time.perf_counter()
    longest, upper = 0.0, adj.get_upper() if adj else 0
    while time.perf_counter() - start < max_s:
        t = time.perf_counter()
        ctx.iteration(False)
        took = time.perf_counter() - t
        longest = max(longest, took)
        if took > 0.05 or (adj is not None and adj.get_upper() != upper):
            upper = adj.get_upper() if adj else 0
            last_change = time.perf_counter()
        if time.perf_counter() - last_change > quiet_s:
            break
        time.sleep(0.001)
    return last_change - start, longest


def main():
    src = pick_session(sys.argv[1] if len(sys.argv) > 1 else None)
    copy = os.path.join(T.TMP, os.path.basename(src))
    shutil.copyfile(src, copy)
    size = os.path.getsize(copy) / 1e6
    sw = Stopwatch()

    info = sw.run("read_session_info (title, folder, prompts)", lambda: baude.read_session_info(copy))
    items, skipped = sw.run("read_history (parse the transcript)", lambda: baude.read_history(copy, limit=None))
    kinds = {}
    image_bytes = 0
    for kind, content, *_ in items:
        kinds[kind] = kinds.get(kind, 0) + 1
        if isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                for part in [c] + (c.get("content") if isinstance(c.get("content"), list) else []):
                    if isinstance(part, dict) and part.get("type") == "image":
                        image_bytes += len(part.get("source", {}).get("data", "")) * 3 // 4

    app = T.App()
    win = app.window()
    T.pump(0.3)
    rss0 = rss_mb()
    sw.run("load_history (build every widget)", lambda: win.load_history(copy))
    adj = win.scroller.get_vadjustment()
    first, stall = settle(adj)
    sw.rows.append(("first layout + paint, until stable", first))
    rss1 = rss_mb()
    widgets = sum(1 for _ in T.walk(win.transcript))
    cards = len(T.find(win.transcript, baude.ToolCard))
    pictures = len([p for p in T.find(win.transcript, Gtk.Picture)])
    groups = len(T.find(win.transcript, baude.ToolGroup))

    def scroll_to(v):
        adj.set_value(v)
        return settle(adj, 5)
    t_top, s_top = scroll_to(0)
    sw.rows.append(("scroll to the top, until stable", t_top))
    t_bot, s_bot = scroll_to(adj.get_upper())
    sw.rows.append(("scroll to the bottom, until stable", t_bot))
    shown_after_open = len(T.find(win.transcript, baude.ToolCard))
    t = time.perf_counter()
    while win.history and win.history.unshown:
        win.show_earlier()
    sw.rows.append(("show every earlier chunk (the whole session)", time.perf_counter() - t))
    full, stall_full = settle(adj)
    sw.rows.append(("  … and its layout, until stable", full))
    widgets_all = sum(1 for _ in T.walk(win.transcript))
    rss2 = rss_mb()
    T.close(win)
    T.pump(0.2)

    print(f"\nsession   {info['title']!r}  ({os.path.basename(src)})")
    print(f"file      {size:.1f} MB, {info['prompts']} prompts, {sum(kinds.values())} history items {kinds}")
    print(f"images    {image_bytes / 1e6:.1f} MB embedded, {pictures} pictures drawn")
    print(f"opened    {widgets} widgets: {cards} tool cards, {groups} tool groups (newest "
          f"{baude.BaudeWindow.HISTORY_CHUNK} items; older ones load as you scroll up)")
    print(f"all       {widgets_all} widgets with the whole session shown")
    print(f"memory    {rss0:.0f} MB -> {rss1:.0f} MB opened (+{rss1 - rss0:.0f}) -> {rss2:.0f} MB everything "
          f"(+{rss2 - rss0:.0f})\n")
    for name, dt in sw.rows:
        print(f"  {dt * 1000:8.0f} ms  {name}")
    print(f"  {stall * 1000:8.0f} ms  longest single main-loop iteration while settling (a UI freeze)")
    print(f"  {max(s_top, s_bot) * 1000:8.0f} ms  longest iteration while scrolling")
    print(f"  {stall_full * 1000:8.0f} ms  longest iteration laying out the whole session")
    total = sum(dt for name, dt in sw.rows[:4])
    print(f"  {total * 1000:8.0f} ms  total: opening the session until it's stable")


if __name__ == "__main__":
    main()
