#!/usr/bin/env python3
"""What a zoom step (Ctrl/⌘ + −) costs in Record, on a sample built up in steps so the growth shows (issue #51).

    python3 tests/bench_zoom_record.py                      # small, medium, large: each in a process of its own
    python3 tests/bench_zoom_record.py --sizes large --steps 9 --profile --sample
    python3 tests/bench_zoom_record.py --sizes large --pages 800 --messages 3000 --out result.json

The sample, in a throwaway folder (env as tests/test_record.py has it: nothing touches your Records, settings or
chats): a notebook of many pages full of [[links]] and @names, its folder a git repository with a glossary
(.baude/signal.db) and so a ⌘K catalogue with a lot of names, some of the pages open in tabs, and long chats open in
Readers, each of them read once (a tab never shown isn't laid out). --pages, --links, --names, --glossary, --files,
--messages, --notes, --chats and --asked (the conversation with the Record) change a size.

Reported, for each tab shown (a chat; a page of notes), medians of --steps steps, each from the key's handler:
  first    to the end of the first frame with the text at its new size, laid out and drawn
  done     to the last frame that did any work for it (hidden tabs' or far-off messages' work, if any is put off)
  call     in set_zoom itself
  style    the frame clock's update phase, where GTK validates the CSS (summed over the step's frames)
  layout   its layout phase (measure and allocate)
  paint    its paint phase (snapshot and render: GSK_RENDERER=cairo offscreen says little about a real one)
  stall    the longest the main loop didn't come back, keys and all (a save of records.json counts)
  moved    how far the line at the top of the view moved on screen, at worst, on any frame (0: the place is kept)
And: burst (6 key presses 33 ms apart, as holding the key does: to done, frames, the CSS's loads), switch (zoomed with
the other tab shown, then this one shown: to its first frame at the new size), solo (a step with fewer tabs open: a
chat alone; a page of notes and one chat, not shown), scale (Settings → Text Scale, a click, with the chat shown: to its
first frame laid out; a change of every widget's size, whatever the zoom does).
--profile: cProfile for a step: Python's share against the wall's (time inside GTK shows as GLib's main loop's own), and
counts of the calls that might be the cost. --sample: macOS's sample(1) while it zooms, for what's inside GTK itself.

Run it through a script that keeps windows offscreen for numbers beside a test pass, or plainly for the real renderer
on the real screen: a window comes up for as long as it runs.
"""
import argparse
import cProfile
import json
import os
import pstats
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

SIZES = {  # (pages, what's in each; the folder's names; a chat's length; tabs open)
    "small": {"pages": 20, "links": 10, "names": 5, "glossary": 200, "files": 200, "messages": 60, "notes": 2,
              "chats": 2, "asked": 10},
    "medium": {"pages": 100, "links": 25, "names": 15, "glossary": 2000, "files": 1500, "messages": 300, "notes": 4,
               "chats": 3, "asked": 40},
    "large": {"pages": 400, "links": 60, "names": 40, "glossary": 10000, "files": 6000, "messages": 1200, "notes": 8,
              "chats": 4, "asked": 100},
}
WATCHED = ("do_css_changed", "place_icons", "do_measure", "_highlight", "find", "relink", "scroll_place", "hold_place",
           "do_snapshot", "do_snapshot_layer", "char_rect", "_laid_out", "save_now", "apply_zoom", "shown_picks",
           "set_max_height", "split", "_back", "_hold")


def surface(i):
    return f"OrderRunner{i}" if i % 2 else f"invoice_store_{i}"


def file_name(i):
    return f"svc/part{i % 40}/module_{i}.py"


def page_text(n, size):
    """A page of notes: headings, a list, a quote, code, and its share of [[links]] and @names through them."""
    out, links, names, at = [f"# Page {n}", ""], size["links"], size["names"], n * 7
    paragraphs = max(links, names, 4)
    for p in range(paragraphs):
        words = "Some **notes** that go on for a while so that the line wraps at the page's width, *as notes do*"
        link = f" and see [[Page {(n + p * 3) % size['pages']}]]" if p < links else ""
        name = f" with @{surface((at + p) % size['glossary'])} from @{file_name(p).rpartition('/')[2]}" if p < names else ""
        out.append(f"{words}{link}{name}.")
        if p % 4 == 0:
            out += ["", f"## Part {p}", "", "- a point made", "- [ ] one to do", "  - one under it", "1. a numbered one",
                    "", "> a quote kept", ""]
        if p % 9 == 0:
            out += ["```", "make deploy", "```", ""]
    return "\n".join(out)


def reply_text(i, size):
    """A reply as Claude Code writes one: prose with names in backticks, and now and then a list, code or a table."""
    a, b = surface(i % size["glossary"]), file_name(i % size["files"]).rpartition("/")[2]
    text = (f"Message {i}: `{a}` in `{b}` does the work; the rest of this paragraph is words that wrap across the "
            "width of the column, the way a reply's paragraphs do, a good few lines of them. " * 3)
    if i % 3 == 0:
        text += f"\n\n- first, `{b}`\n- then `{a}`\n- and so on, in a line long enough to matter a little\n"
    if i % 7 == 0:
        text += "\n\n```python\ndef run(self):\n    for job in self.jobs:\n        job.start()\n```\n"
    if i % 11 == 0:
        text += f"\n\n| name | where |\n| --- | --- |\n| `{a}` | `{b}` |\n| other | here |\n"
    return text


def build(tmp, size):
    """The sample under tmp; what the window's told in the environment. Returns (the repository, the chats' paths)."""
    repo = os.path.join(tmp, "shop")
    for i in range(size["files"]):
        path = os.path.join(repo, file_name(i))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(f"class Thing{i}:\n    pass\n")
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "init.defaultBranch=main"]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "shop"]):
        subprocess.run(git + args, cwd=repo, check=True, capture_output=True)
    os.makedirs(os.path.join(repo, ".baude"))
    import lookup
    db = sqlite3.connect(os.path.join(repo, ".baude", "signal.db"))
    db.executescript(lookup.REF_SCHEMA)
    rows = range(1, size["glossary"] + 1)
    db.executemany("INSERT INTO entity (id, kind, canonical, status, data) VALUES (?, 'symbol', ?, 'resolved', '{}')",
                   ((i, surface(i)) for i in rows))
    db.executemany("INSERT INTO alias (surface, entity_id) VALUES (?, ?)", ((surface(i), i) for i in rows))
    db.executemany("INSERT INTO location (entity_id, role, path, line) VALUES (?, 'defined', ?, 1)",
                   ((i, file_name(i % size["files"])) for i in rows))
    db.commit()
    db.close()
    folder = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", "-shop")
    os.makedirs(folder)
    chats = []
    for c in range(size["chats"]):
        path = os.path.join(folder, f"chat-{c}.jsonl")
        with open(path, "w") as f:
            for i in range(size["messages"]):
                if i % 5 == 0:
                    f.write(json.dumps({"type": "user", "cwd": repo, "uuid": f"u{i}",
                                        "message": {"role": "user", "content": f"And what about part {i}?"}}) + "\n")
                f.write(json.dumps({"type": "assistant", "cwd": repo, "uuid": f"a{i}",
                                    "message": {"id": f"m{i}", "role": "assistant",
                                                "content": [{"type": "text", "text": reply_text(i, size)}]}}) + "\n")
        chats.append({"path": path, "title": f"Chat {c}", "cwd": repo})
    pages = {"Home": page_text(0, size), **{f"Page {n}": page_text(n, size) for n in range(1, size["pages"])}}
    notebook = {"id": "a", "name": "Systems", "created": 0, "folders": [repo], "pages": pages, "page": "Home",
                "open": ["Home"] + [f"Page {n}" for n in range(1, size["notes"])],
                "messages": [{"role": "record" if i % 2 else "user", "text": reply_text(i, size)}
                             for i in range(size["asked"])]}
    os.makedirs(os.environ["RECORD_DATA"], exist_ok=True)
    with open(os.path.join(os.environ["RECORD_DATA"], "records.json"), "w") as f:
        json.dump({"records": [notebook], "notes": "markdown", "chats": chats, "reading": chats[0]}, f)
    return repo, [c["path"] for c in chats]


class Frames:
    """Each frame's phases, as the window's frame clock goes through them (a frame has only those it was asked for)."""

    def __init__(self, win):
        self.clock, self.list, self.at, self.on_end = win.get_frame_clock(), [], None, None
        self.clock.connect("before-paint", self._begin)
        self.clock.connect_after("update", self._mark, "update")
        self.clock.connect_after("layout", self._mark, "layout")
        self.clock.connect_after("paint", self._mark, "paint")
        self.clock.connect("after-paint", self._end)

    def _begin(self, _clock):
        self.at = {"start": time.perf_counter()}

    def _mark(self, _clock, phase):
        if self.at is not None:
            self.at[phase] = time.perf_counter()

    def _end(self, _clock):
        f, self.at = self.at, None
        if f is None:
            return
        end = time.perf_counter()
        update = f.get("update", f["start"])
        layout = f.get("layout", update)
        f["style"], f["layout"], f["paint"] = update - f["start"], layout - update, f.get("paint", layout) - layout
        f["end"], f["work"] = end, end - f["start"]
        self.list.append(f)
        if self.on_end is not None:
            self.on_end(f)


class Bench:
    def __init__(self, win, frames):
        self.win, self.frames, self.worst = win, frames, 0.0
        self.saves = 0
        save_now = win.store.save_now

        def saved():
            self.saves += 1
            return save_now()
        win.store.save_now = saved
        self.applied = 0
        apply_zoom = win.apply_zoom

        def applied():
            self.applied += 1
            return apply_zoom()
        win.apply_zoom = applied

    def pump(self, until, timeout=60):
        """The main loop, till until() (or for a while), noting its longest turn."""
        end, ctx = time.perf_counter() + timeout, GLib.MainContext.default()
        while time.perf_counter() < end and not until():
            t = time.perf_counter()
            busy = ctx.iteration(False)
            self.worst = max(self.worst, time.perf_counter() - t)
            if not busy:
                time.sleep(0.0005)

    def wait(self, seconds):
        end = time.perf_counter() + seconds
        self.pump(lambda: time.perf_counter() > end)

    def settle(self, since, quiet=0.15, at_least=0.33):
        """Till nothing's been done for a while: any save the step asked for included."""
        def calm():
            now = time.perf_counter()
            done = [f["end"] for f in self.frames.list if f["end"] > since and f["work"] > 0.002]
            return now - since > at_least and now - (done[-1] if done else since) > quiet
        self.pump(calm)
        return next((f["end"] for f in reversed(self.frames.list) if f["end"] > since and f["work"] > 0.002), since)

    def font(self, widget):
        return widget.get_pango_context().get_font_description().get_size()

    def at_size(self, probe, was, zoom_was, zoom):
        return abs(self.font(probe) / was - zoom / zoom_was) < 0.02

    def press(self, zoom):
        t = time.perf_counter()
        self.win.set_zoom(zoom)
        return t, time.perf_counter() - t


def probe_of(tab):
    """A widget in what's shown whose font says its text's size, near what's in view."""
    if isinstance(tab, record.Reader):
        top = tab.scroll.get_vadjustment().get_value()
        for row in tab.rows:
            ok, b = row.compute_bounds(tab.box)
            if ok and b.get_y() + b.get_height() > top and row.get_visible():
                return next(w for w in widgets.walk(row) if isinstance(w, Gtk.Label) and w.get_selectable())
        return next(w for w in widgets.walk(tab.rows[-1]) if isinstance(w, Gtk.Label))
    return tab.view


def extent(tab, what):
    """Where what is (a message, a line of notes) in what's scrolled: (y, height)."""
    if isinstance(tab, record.Reader):
        ok, b = what.compute_bounds(tab.clamp)
        return (b.get_y(), b.get_height()) if ok else None
    ok, it = tab.buf.get_iter_at_line(what)
    y, height = tab.view.get_line_yrange(it)
    return tab.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, 0, y)[1], height


def place(tab):
    """What's at the top of the view, and how far through it the top of the view is: for on_screen."""
    value = tab.scroll.get_vadjustment().get_value()
    if isinstance(tab, record.Reader):
        what = next((r for r in tab.rows if r.get_visible() and sum(extent(tab, r) or (0, 0)) > value), tab.rows[-1])
    else:
        y = tab.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, 0, int(value))[1]
        what = tab.view.get_line_at_y(y)[0].get_line()
    y, height = extent(tab, what)
    return what, (value - y) / height if height else 0.0


def on_screen(tab, at):
    """How far down the view that place is, now."""
    what, through = at
    found = extent(tab, what)
    return None if found is None else found[0] + through * found[1] - tab.scroll.get_vadjustment().get_value()


def busy_frames(frames):
    return sum(1 for f in frames if f["work"] > 0.002)


def step(bench, tab, zoom):
    """One step: what it took (see the module's docstring)."""
    win, frames = bench.win, bench.frames
    probe = probe_of(tab)
    was, zoom_was, at = bench.font(probe), win.store.zoom, place(tab)
    y0 = on_screen(tab, at)
    bench.worst, saves, first, moved = 0.0, bench.saves, [], [0.0]

    def frame(f):
        if not first and bench.at_size(probe, was, zoom_was, zoom):
            first.append(f["end"])
        if first and (y := on_screen(tab, at)) is not None and y0 is not None:
            moved[0] = max(moved[0], abs(y - y0))
    frames.on_end = frame
    t, call = bench.press(zoom)
    n = len(frames.list)
    bench.pump(lambda: first, 60)
    done = bench.settle(t)
    frames.on_end = None
    mine = frames.list[n:]
    return {"first": (first[0] - t if first else float("nan")) * 1000, "done": (done - t) * 1000, "call": call * 1000,
            "style": sum(f["style"] for f in mine) * 1000, "layout": sum(f["layout"] for f in mine) * 1000,
            "paint": sum(f["paint"] for f in mine) * 1000, "frames": busy_frames(mine), "stall": bench.worst * 1000,
            "moved": moved[0], "saves": bench.saves - saves}


def steps(bench, tab, count):
    """count steps, in and out in turn, from where the zoom is: their medians."""
    base, results = bench.win.store.zoom, []
    for i in range(count):
        results.append(step(bench, tab, round(base + (0.1 if i % 2 == 0 else 0.0), 2)))
    return {k: statistics.median(r[k] for r in results) for k in results[0]} | {
        "moved": max(r["moved"] for r in results), "first_each": [round(r["first"]) for r in results]}


def burst(bench, tab, presses=6, gap=0.033):
    """Key presses as a held key sends them: to everything done, frames in it, how many times the CSS was loaded."""
    win, frames = bench.win, bench.frames
    base, n, applied = win.store.zoom, len(frames.list), bench.applied
    bench.worst = 0.0
    t = time.perf_counter()
    for i in range(presses):
        win.set_zoom(round(base + 0.1 * (i + 1), 2))
        next_at = t + gap * (i + 1)
        bench.pump(lambda: time.perf_counter() >= next_at)
    last = time.perf_counter()
    done = bench.settle(last)
    mine = frames.list[n:]
    result = {"done": (done - t) * 1000, "frames": busy_frames(mine), "applied": bench.applied - applied,
              "stall": bench.worst * 1000}
    bench.wait(0.1)
    win.set_zoom(base)
    bench.settle(time.perf_counter())
    return result


def switch(bench, away, to):
    """Zoomed with another tab shown, then this one shown: to its first frame at the new size, and to all done."""
    win, frames = bench.win, bench.frames
    tabs = win.tabs
    tabs.show(to.tab_key)
    bench.settle(time.perf_counter(), at_least=0.1)
    probe = probe_of(to)
    was, zoom_was = bench.font(probe), win.store.zoom
    at = place(to)
    y0 = on_screen(to, at)
    tabs.show(away.tab_key)
    bench.settle(time.perf_counter(), at_least=0.1)
    zoom = round(zoom_was + 0.1, 2)
    win.set_zoom(zoom)
    bench.settle(time.perf_counter())
    first, moved, n = [], [0.0], len(frames.list)

    def frame(f):
        if not first and bench.at_size(probe, was, zoom_was, zoom):
            first.append(f["end"])
        if first and (y := on_screen(to, at)) is not None:
            moved[0] = max(moved[0], abs(y - y0))
    frames.on_end = frame
    t = time.perf_counter()
    tabs.show(to.tab_key)
    bench.pump(lambda: first, 60)
    done = bench.settle(t, at_least=0.1)
    frames.on_end = None
    mine = frames.list[n:]
    result = {"first": (first[0] - t if first else float("nan")) * 1000, "done": (done - t) * 1000,
              "style": sum(f["style"] for f in mine) * 1000, "layout": sum(f["layout"] for f in mine) * 1000,
              "stall": max((f["work"] for f in mine), default=0) * 1000, "moved": moved[0]}
    win.set_zoom(zoom_was)
    bench.settle(time.perf_counter())
    return result


def text_scale(bench, tab, count=4):
    """A click on Settings → Text Scale, up and down: to the first frame that lays anything out, and to all done."""
    win, frames, results = bench.win, bench.frames, []
    for i in range(count):
        at = place(tab)
        y0, first, moved, n = on_screen(tab, at), [], [0.0], len(frames.list)

        def frame(f):
            if not first and f["layout"] > 0.002:
                first.append(f["end"])
            if first and (y := on_screen(tab, at)) is not None:
                moved[0] = max(moved[0], abs(y - y0))
        frames.on_end = frame
        t = time.perf_counter()
        win.set_scale("text_scale", 1.1 if i % 2 == 0 else 1.0)
        bench.pump(lambda: first, 120)
        done = bench.settle(t)
        frames.on_end = None
        results.append({"first": (first[0] - t if first else float("nan")) * 1000, "done": (done - t) * 1000,
                        "moved": moved[0]})
    return {k: statistics.median(r[k] for r in results) for k in results[0]}


def profile(bench, tab, watch=WATCHED):
    """One step in, one out, under cProfile. What the wall took, what's in Python's own functions, and call counts."""
    base = bench.win.store.zoom
    prof = cProfile.Profile()
    t = time.perf_counter()
    prof.enable()
    step(bench, tab, round(base + 0.1, 2))
    step(bench, tab, base)
    prof.disable()
    wall = time.perf_counter() - t
    stats = pstats.Stats(prof).stats
    pumped = sleep = 0.0
    python, rows, counts = 0.0, [], {}
    for (path, _line, name), (_cc, nc, tt, ct, _callers) in stats.items():
        if name == "<built-in method time.sleep>":
            sleep += tt
        elif name == "iteration" or name in ("pump", "calm"):  # (GLib's main loop: its own time is all GTK's)
            pumped += tt
        else:
            python += tt
            rows.append((tt, nc, ct, f"{os.path.basename(path)}:{name}"))
        if name in watch and "bench_zoom" not in path:
            key = f"{os.path.basename(path)}:{name}"
            counts[key] = counts.get(key, 0) + nc
    rows.sort(reverse=True)
    return {"wall_ms": wall * 1000 / 2, "python_ms": python * 1000 / 2, "in_gtk_ms": pumped * 1000 / 2,
            "idle_ms": sleep * 1000 / 2, "counts_per_step": {k: v / 2 for k, v in sorted(counts.items())},
            "top": [(round(tt * 500, 1), nc // 2, round(ct * 500, 1), name) for tt, nc, ct, name in rows[:18]]}


def sample(bench, seconds):
    """macOS's sample(1) of this process while it zooms without a pause: where the main thread's samples are, by what
    it's running (the most any line of the call graph has of that symbol) and at the top of the stack."""
    if sys.platform != "darwin" or not shutil.which("sample"):
        return None
    out = os.path.join(tempfile.gettempdir(), f"zoom-sample-{os.getpid()}.txt")
    proc = subprocess.Popen(["sample", str(os.getpid()), str(seconds), "-file", out], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    base, i, frames = bench.win.store.zoom, 0, bench.frames
    while proc.poll() is None:
        i += 1
        bench.win.set_zoom(round(base + (0.1 if i % 2 else 0.0), 2))
        n = len(frames.list)
        bench.pump(lambda: len(frames.list) > n, 60)
    bench.win.set_zoom(base)
    bench.settle(time.perf_counter())
    try:
        with open(out) as f:
            text = f.read()
    except OSError:
        return None
    os.remove(out)
    return summarize_sample(text)


SAMPLE_SYMBOLS = ("gdk_frame_clock_frame", "gtk_css_node_validate", "gdk_surface_layout_on_clock",
                  "toplevel_compute_size", "gtk_label_measure", "pango_layout_check_lines", "pango_shape_internal",
                  "_gtk_css_selector_tree_match_all", "surface_render_cb", "gtk_widget_do_snapshot",
                  "gsk_cairo_renderer_render", "gsk_renderer_render")


def summarize_sample(text):
    calls, main = {}, 0
    graph, _, tops = text.partition("Sort by top of stack")
    for line in graph.splitlines():
        if m := re.match(r"[\s+!:|]*(\d+) (\S+)", line):
            count, name = int(m.group(1)), m.group(2)
            main = count if "main-thread" in line else main
            if name in SAMPLE_SYMBOLS:  # (one call to it may be several lines: a frame's phases are)
                calls[name] = max(calls.get(name, 0), count)
    top = [(int(m.group(3)), m.group(1), m.group(2)) for line in tops.splitlines()[1:40]
           if (m := re.match(r"\s*(\S+)\s+\(in ([^)]+)\)\s+(\d+)", line))]
    return {"main_samples": main, "calls": dict(sorted(calls.items(), key=lambda kv: -kv[1])), "top_of_stack": top[:22]}


def open_sample(tmp, size):
    """The sample built under tmp and open in a Record: (its window, frames, a Bench, seconds till ⌘K had its names)."""
    global record, widgets, Gdk, Gtk, GLib, Graphene
    os.environ["RECORD_DATA"] = os.path.join(tmp, "record-data")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "config")
    os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(tmp, "claude-config")
    os.environ["KUBECONFIG"] = os.path.join(tmp, "no-kubeconfig")
    os.environ["DOCKER_HOST"] = "unix://" + os.path.join(tmp, "no-docker.sock")
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "xdg-data")
    os.environ["RECORD_NOUNS"] = "off"
    os.environ["BAUDE_GH"] = ""
    os.environ["BAUDE_UPDATE"] = "0"
    os.environ["RECORD_CLAUDE"] = "/usr/bin/false"
    build(tmp, size)
    import record
    import widgets
    from gi.repository import Gdk, GLib, Graphene, Gtk
    widgets.SLIDE_MS = 0
    app = record.App()
    app.register(None)
    app.activate()
    ctx = GLib.MainContext.default()
    end = time.monotonic() + 30
    while (app.win is None or app.win.get_frame_clock() is None) and time.monotonic() < end:
        ctx.iteration(False)
    win = app.win
    win.present()
    frames = Frames(win)
    bench = Bench(win, frames)
    space = win.space()
    t = time.perf_counter()
    bench.pump(lambda: not space.busy and space.find(surface(1)) is not None, 120)
    took = time.perf_counter() - t
    bench.wait(1.5)  # (the readers' links follow on a timer)
    for child in win.tabs.children():  # (a page's logos, as it's drawn once ⌘K has its names: the caret moved, say)
        if isinstance(child, record.DiaryEditor):
            child._highlight()
    shown = win.tabs.current()
    for child in win.tabs.children():  # (each read once: a tab never shown isn't laid out, as you've read what's open)
        win.tabs.show(child.tab_key)
        bench.wait(0.3)
    win.tabs.show(shown.tab_key)
    bench.wait(0.3)
    return win, frames, bench, took


def run_one(name, size, args):
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="record-zoom-bench-"))
    try:
        win, _frames, bench, took = open_sample(tmp, size)
        space = win.space()
        chats = [c.chat["path"] for c in win.tabs.children() if isinstance(c, record.Reader)]
        result = {"size": name, **size, "catalogue_s": round(took, 1)}
        result["items"] = len(space.items())
        note, chat = win.tabs.get(("note", "Home")), win.tabs.get(("chat", os.path.realpath(chats[0])))
        assert note is not None and chat is not None, list(win.tabs.pages)
        tree = sum(1 for _ in widgets.walk(win))
        shown_chat = sum(1 for _ in widgets.walk(chat))
        result["widgets"], result["widgets_chat"] = tree, shown_chat
        result["logos"], result["marks"] = len(note.view.logos), len(note.view.marks)
        for tab, label in ((chat, "chat"), (note, "note")):
            win.tabs.show(tab.tab_key)
            bench.wait(0.6)
            adj = tab.scroll.get_vadjustment()
            adj.set_value((adj.get_upper() - adj.get_page_size()) * 0.4)  # (somewhere in it: not at its end)
            bench.wait(0.4)
            result[label] = steps(bench, tab, args.steps)
            result[label]["burst"] = burst(bench, tab)
        result["switch_to_chat"] = switch(bench, note, chat)
        result["switch_to_note"] = switch(bench, chat, note)
        win.tabs.show(chat.tab_key)
        bench.settle(time.perf_counter())
        result["scale"] = text_scale(bench, chat)
        if args.profile:
            for tab, label in ((chat, "chat"), (note, "note")):
                win.tabs.show(tab.tab_key)
                bench.settle(time.perf_counter())
                result[label]["profile"] = profile(bench, tab)
        if args.sample:
            for tab, label in ((chat, "chat"), (note, "note")):
                win.tabs.show(tab.tab_key)
                bench.settle(time.perf_counter())
                # (long enough for a couple of steps, however long they take)
                result[label]["sample"] = sample(bench, max(3, min(30, round(result[label]["first"] * 2.5 / 1000))))
        # what the tabs not shown add: each again, with nothing else open
        win.tabs.show(note.tab_key)
        bench.settle(time.perf_counter())
        win.switching = True  # (not kept as closed: nothing's saved of it)
        for key in list(win.tabs.pages):
            if key not in (chat.tab_key, note.tab_key):
                win.tabs.close(key)
        win.switching = False
        bench.wait(0.6)
        result["two_tabs_note"] = steps(bench, note, max(3, args.steps // 2))
        win.tabs.show(chat.tab_key)
        bench.settle(time.perf_counter())
        win.switching = True  # (not kept as closed: nothing's saved of it)
        for key in list(win.tabs.pages):
            if key != chat.tab_key:
                win.tabs.close(key)
        win.switching = False
        bench.wait(0.6)
        result["solo_chat"] = steps(bench, chat, max(3, args.steps // 2))
        win.store.save_now()
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def table(results):
    print(f"{'size':8}{'tab':6}{'first':>8}{'done':>8}{'call':>7}{'style':>8}{'layout':>8}{'paint':>8}{'stall':>8}"
          f"{'moved':>7}{'burst':>8}{'frames':>7}{'loads':>6}{'switch':>8}{'solo':>8}{'scale':>8}")
    for r in results:
        for tab in ("chat", "note"):
            s, b = r[tab], r[tab]["burst"]
            to = r[f"switch_to_{tab}"]
            solo = r["solo_chat"]["first"] if tab == "chat" else r["two_tabs_note"]["first"]
            print(f"{r['size']:8}{tab:6}{s['first']:8.0f}{s['done']:8.0f}{s['call']:7.1f}{s['style']:8.0f}"
                  f"{s['layout']:8.0f}{s['paint']:8.0f}{s['stall']:8.0f}{s['moved']:7.0f}{b['done']:8.0f}"
                  f"{b['frames']:7d}{b['applied']:6d}{to['first']:8.0f}{solo:8.0f}{r['scale']['first']:8.0f}")
    print("(ms, medians; style, layout and paint are summed over the step's frames. GSK_RENDERER=cairo: paint isn't "
          "a real renderer's)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sizes", default="small,medium,large", help="of " + ", ".join(SIZES))
    parser.add_argument("--steps", type=int, default=7, help="zoom steps to take the median of")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--out", help="the results, as JSON")
    parser.add_argument("--one", help=argparse.SUPPRESS)  # (this process runs that size, and writes to --out)
    for key in SIZES["small"]:
        parser.add_argument(f"--{key}", type=int)
    args = parser.parse_args()
    overrides = {k: v for k in SIZES["small"] if (v := getattr(args, k)) is not None}
    if args.one:
        result = run_one(args.one, {**SIZES[args.one], **overrides}, args)
        with open(args.out, "w") as f:
            json.dump(result, f)
        os._exit(0)  # (not the app's shutdown: nothing here to keep)
    results = []
    for name in args.sizes.split(","):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            part = f.name
        rest = sys.orig_argv[1:]  # (as it was run: through whatever keeps its windows offscreen, if anything does)
        for flag in ("--out", "--sizes"):  # (its own, for the process that runs it)
            if flag in rest:
                at = rest.index(flag)
                del rest[at:at + 2]
        subprocess.run([sys.executable, *rest, "--one", name, "--out", part], check=True)
        with open(part) as f:
            results.append(json.load(f))
        os.remove(part)
        table(results[-1:])
        sys.stdout.flush()
    print()
    table(results)
    for r in results:
        print(f"\n{r['size']}: {r['pages']} pages ({r['links']} links, {r['names']} @names each), {r['items']} names in "
              f"⌘K, {r['messages']} messages a chat, {r['notes']} notes and {r['chats']} chats open; {r['widgets']} "
              f"widgets, {r['widgets_chat']} in a chat; a page has {r['logos']} logos and {r['marks']} marks")
        for tab in ("chat", "note"):
            print(f"  {tab}: each step's first frame {r[tab]['first_each']}, frames {r[tab]['frames']}, saves "
                  f"{r[tab]['saves']}; to it after a zoom elsewhere {r[f'switch_to_{tab}']}")
            if (p := r[tab].get("profile")) is not None:
                print(f"    profile of a step: wall {p['wall_ms']:.0f} ms (idle {p['idle_ms']:.0f}), Python "
                      f"{p['python_ms']:.0f} ms, inside GTK {p['in_gtk_ms']:.0f} ms")
                print(f"    calls a step: {p['counts_per_step']}")
                for tt, nc, ct, fn in p["top"]:
                    print(f"      {tt:8.1f} ms own {ct:8.1f} ms all {nc:7d} calls  {fn}")
            if (s := r[tab].get("sample")) is not None:
                print(f"    sample: {s['main_samples']} on the main thread: {s['calls']}")
                for count, fn, lib in s["top_of_stack"]:
                    print(f"      {count:6d}  {fn}  ({lib})")
    if args.out:
        with open(args.out, "w") as f:
            json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
