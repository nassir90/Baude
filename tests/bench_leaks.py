#!/usr/bin/env python3
"""What repeating ordinary actions leaves behind, in Baude or Record, round by round: a fuzzing-style bench (nothing
asserted), run offscreen against the tests' throwaway data and fake claude.

    python3 tests/bench_leaks.py                        # Baude, every action, 4 rounds
    python3 tests/bench_leaks.py --app record           # Record's actions
    python3 tests/bench_leaks.py -a window,code -r 6    # some actions, more rounds
    python3 tests/bench_leaks.py --trace 1              # + the Python lines that allocated most each round
    python3 tests/bench_leaks.py --heap                 # (macOS) + the GPU's compiled programs (`heap -s`), and blocks
    GSK_RENDERER=gl python3 tests/bench_leaks.py --heap # (drawn by GTK's GL renderer: cairo's is the default here)

Each round does every action once; then the round's growth: malloc (what's handed out and not freed: exact, where the
footprint Activity Monitor shows keeps freed memory a while), the footprint, Python's objects, and GObjects by kind
(GOBJECT_DEBUG=instance-count: widgets, windows, popovers, surfaces, renderers, GL contexts). A round that grows the
same each time is a leak; the first round warms caches up.

Baude's actions: window (one opened on a long chat, its code and Git pages shown, closed with its ×), switch (chats
switched, every part of the long one drawn), code (three files opened in tabs and the tabs closed, one 600 KB),
git (the Git page), theme (dark, light), accent (three accents, as Settings → Accent Colour), popover (the window's
menus opened and closed), settings (Settings opened and closed), turn (three turns of the fake claude).
Record's: notebooks (switched back and forth), chat (a chat opened in a tab and closed), code, theme, accent,
settings."""
import argparse
import ctypes
import ctypes.util
import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
tempfile.tempdir = "/tmp"
HOME = tempfile.mkdtemp(prefix="bench-leaks-home-")
os.environ["HOME"] = HOME  # (before anything reads it: ~ is this run's)
os.environ["GOBJECT_DEBUG"] = "instance-count"  # (before GObject starts: g_type_get_instance_count's counts)
os.environ.setdefault("GSK_RENDERER", "cairo")
sys.path[:0] = [ROOT, HERE]
import macos  # noqa: E402

macos.stay_offscreen()
ap = argparse.ArgumentParser()
ap.add_argument("--app", choices=("baude", "record"), default="baude")
ap.add_argument("-a", "--actions", help="comma-separated (see above); default: all of the app's")
ap.add_argument("-r", "--rounds", type=int, default=4)
ap.add_argument("--trace", type=int, default=0, help="tracemalloc's frames: the top allocation sites each round")
ap.add_argument("--heap", action="store_true", help="(macOS) the GPU's programs and malloc's untyped blocks")
ap.add_argument("--json", help="also write the rounds' numbers here")
args = ap.parse_known_args()[0]  # (and AppKit's own, -ApplePersistenceIgnoreState YES)
if args.trace:
    tracemalloc.start(args.trace)

if args.app == "baude":
    import test_baude as T  # noqa: E402  (its throwaway config, data and fake claude)
    from test_baude import baude  # noqa: E402
else:
    import test_record as T  # noqa: E402
from gi.repository import Adw, GObject, Gtk  # noqa: E402
import memreport  # noqa: E402
import theming  # noqa: E402

_gobject = ctypes.CDLL(ctypes.util.find_library("gobject-2.0") or "/opt/homebrew/lib/libgobject-2.0.0.dylib")
_gobject.g_type_get_instance_count.restype, _gobject.g_type_get_instance_count.argtypes = ctypes.c_int, [ctypes.c_size_t]
_gobject.g_type_from_name.restype, _gobject.g_type_from_name.argtypes = ctypes.c_size_t, [ctypes.c_char_p]
KINDS = ["GtkWidget", "GtkWindow", "GtkPopover", "GtkTextView", "GdkSurface", "GskRenderer", "GdkGLContext",
         "GtkEventController", "PangoLayout"]
GPU = {"GLRRenderPipelineState": "pipelines", "FragmentProgramVariant": "fragment programs",
       "_MTLFunctionInternal": "metal functions", "non-object": "untyped blocks"}


def instances(name):
    """How many GObjects of the type name and its subclasses there are."""
    try:
        todo = [GObject.type_from_name(name)]
    except RuntimeError:
        return 0
    n = 0
    while todo:
        t = todo.pop()
        n += _gobject.g_type_get_instance_count(_gobject.g_type_from_name(t.name.encode()))
        todo.extend(GObject.type_children(t))
    return n


def gpu():
    out = subprocess.run(["heap", "-s", str(os.getpid())], capture_output=True, text=True).stdout
    got = Counter()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].isdigit():
            for key, name in GPU.items():
                if key in line:
                    got[name] += int(parts[0])
    return dict(got)


def settle(seconds=0.6):
    T.pump(seconds)


last = {}


def measure(name):
    settle()
    for _ in range(3):
        gc.collect()
        T.pump(0.05)
    row = {"round": name, **memreport.memory(), "objects": len(gc.get_objects()),
           **{k: instances(k) for k in KINDS}, **(gpu() if args.heap else {})}
    prev = last.get("row")
    grew = "" if prev is None else "  grew: " + ", ".join(
        f"{k} {row[k] - prev[k]:+,.1f}" if isinstance(row[k], float) else f"{k} {row[k] - prev[k]:+,}"
        for k in row if k != "round" and k in prev and row[k] != prev[k])
    print(f"{name:10} malloc {row['malloc']:7.1f} MB  footprint {row['footprint']:6.0f} MB  "
          f"{row['objects']:,} objects  {row['GtkWidget']:,} widgets{grew}", flush=True)
    if args.trace:
        snap = tracemalloc.take_snapshot().filter_traces([tracemalloc.Filter(False, tracemalloc.__file__)])
        if "snap" in last:
            for s in snap.compare_to(last["snap"], "traceback" if args.trace > 1 else "lineno")[:8]:
                where = " < ".join(f"{os.path.basename(f.filename)}:{f.lineno}" for f in s.traceback)
                print(f"    {s.size_diff / 1024:+9.1f} KB {s.count_diff:+7,}  {where}", flush=True)
        last["snap"] = snap
    last["row"] = row
    rows.append(row)


rows = []


def accent():
    for name in ("purple", "green", None):
        theming.save_settings(accent=name)
        theming.apply_accent(Gtk.Window.list_toplevels()[0].get_display())
        settle(0.4)


def theme():
    manager = Adw.StyleManager.get_default()
    for scheme in (Adw.ColorScheme.FORCE_DARK, Adw.ColorScheme.FORCE_LIGHT):
        manager.set_color_scheme(scheme)
        settle(0.3)


def big_file(folder):
    path = os.path.join(folder, "big.py")
    if not os.path.exists(path):
        shutil.copy(os.path.join(HERE, "test_baude.py"), path)
    return path


def close_code_tabs(win):
    for key in [k for k in list(win.tabs.pages) if k and k[0] == "code"]:
        win.tabs.close(key)
    settle(0.3)


def long_chat(folder, sid, turns=150):
    """A transcript of turns questions, each answered with prose, code and a tool call."""
    d = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", "".join(c if c.isalnum() else "-" for c in folder))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{sid}.jsonl")
    with open(path, "w") as f:
        for i in range(turns):
            for rec in ({"type": "user", "message": {"role": "user", "content": f"Question {i} about `f{i % 5}.py`"}},
                        {"type": "assistant", "message": {"role": "assistant", "content": [
                            {"type": "text", "text": f"Answer {i}\n\n```python\ndef f{i}():\n    return {i}\n```\n- a\n- b"},
                            {"type": "tool_use", "id": f"toolu_{i}", "name": "Read",
                             "input": {"file_path": f"f{i % 5}.py"}}]}},
                        {"type": "user", "message": {"role": "user", "content": [
                            {"type": "tool_result", "tool_use_id": f"toolu_{i}", "content": "def f(): ...\n" * 5}]}}):
                f.write(json.dumps({**rec, "cwd": folder, "sessionId": sid}) + "\n")
    return path


def baude_actions():
    folder = os.path.join(T.TMP, "repo")
    os.makedirs(folder)
    git = ["git", "-C", folder, "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q", folder], check=True)
    for i in range(30):
        with open(os.path.join(folder, f"f{i % 5}.py"), "a") as f:
            f.write(f"def f{i}():\n    return {i}\n")
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-qm", f"Change {i}"], check=True)
    short = T.write_session(folder, "aaaaaaaa-0000-4000-8000-000000000001", title="Short one")
    long = long_chat(folder, "aaaaaaaa-0000-4000-8000-000000000002")
    app = T.App()
    opts = T.argparse.Namespace(cwd=folder, model=None, resume=None)
    win = app.window(opts)
    T.wait_for(lambda: not win.sidebar.loading, "the sidebar", 60)

    def open_chat(w, path):
        info = baude.cached_session_info(path) or {}
        w.resume_session({**info, "id": os.path.basename(path)[:-6], "path": path, "cwd": folder})
        settle(0.8)
        while w.history and w.history.unshown:
            w.show_earlier()
            settle(0.05)

    def window():
        other = app.window(opts)
        open_chat(other, long)
        other.show_code(os.path.join(folder, "f1.py"), 2)
        other.show_git(folder=folder)
        settle(1)
        other.close()  # (as its × does)
        settle(0.3)

    def switch():
        for path in (short, long, short):
            open_chat(win, path)

    def code():
        for name in ("f0.py", "f1.py"):
            win.show_code(os.path.join(folder, name), 2)
            settle(0.3)
        win.show_code(big_file(folder), 2)
        settle(0.5)
        close_code_tabs(win)

    def git_page():
        win.show_git(folder=folder)
        page = win.panel.git_page
        T.wait_for(lambda: page.top and page.commits, "the git log", 30)
        win.show_panel(False)

    def popover():
        for button in [b for b in T.find(win, Gtk.MenuButton) if b.get_mapped()][:6]:
            button.popup()
            settle(0.1)
            button.popdown()
            settle(0.1)

    def settings():
        dialog = win.open_settings()
        settle(0.5)
        dialog.close()
        settle(0.3)

    def turn():
        for _ in range(3):
            T.type_and_send(win, "list the files")
            T.wait_for(lambda: T.button(win, "Allow") or not win.busy, "the turn", 30)
            if T.button(win, "Allow"):
                T.button(win, "Allow").emit("clicked")
            T.wait_for(lambda: not win.busy, "the turn's end", 30)
            settle(0.2)

    return {"window": window, "switch": switch, "code": code, "git": git_page, "theme": theme, "accent": accent,
            "popover": popover, "settings": settings, "turn": turn}


def record_actions():
    app = T.App()
    win = app.win

    def notebooks():
        for tid in ("b", "a"):
            T.select(win, tid)
            settle(0.4)

    def chat():
        win.open_chat(T.OTHER)
        settle(0.6)
        win.tabs.close(("chat", os.path.realpath(T.OTHER)))
        settle(0.3)

    def code():
        win.show_code(os.path.join(T.REPO, "src", "relay_jobs.py"), 1)
        settle(0.3)
        win.show_code(big_file(T.TMP), 2)
        settle(0.5)
        close_code_tabs(win)

    def settings():
        import recordwindow
        d = recordwindow.Settings(win)
        d.present(win)
        settle(0.5)
        d.close()
        settle(0.3)

    return {"notebooks": notebooks, "chat": chat, "code": code, "theme": theme, "accent": accent,
            "settings": settings}


def main():
    acts = baude_actions() if args.app == "baude" else record_actions()
    picked = args.actions.split(",") if args.actions else list(acts)
    print(f"{args.app}, {os.environ['GSK_RENDERER']} renderer: {', '.join(picked)}", flush=True)
    settle(2)
    measure("started")
    for r in range(args.rounds):
        for name in picked:
            acts[name]()
        measure(f"round {r + 1}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(rows, f, indent=1)


try:
    main()
finally:
    for folder in (T.TMP, HOME, os.environ.get("BAUDE_RUNTIME_DIR")):
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
    os._exit(0)  # (no windows' teardown at exit: what's measured is done)
