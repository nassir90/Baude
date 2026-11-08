#!/usr/bin/env python3
"""Replay a real session through Baude with Signal on, and write what it linked as JSON (for ref_score.py).

    tests/ref_replay.py TRANSCRIPT.jsonl FOLDER OUT.json [--lsp] [--sonnet BUDGET] [--model sonnet|haiku]
                        [--context full|light] [--no-evidence] [--timeout SECONDS]

Runs Baude headless (wrap it in xvfb-run, as tests/run.sh does), opens the transcript as if you'd picked it in
the sidebar, waits until Signal is idle, and writes every name it looked up: its kind, status and locations,
plus the Activity log, the names it skipped and Sonnet's cost. Settings, the glossary and Baude's data live in a
throwaway folder (--work), so your own settings and the repository's .baude stay untouched; --lsp uses language
servers already fetched into ~/.local/share/baude/lsp (Signal's question isn't asked). The claude Baude
starts is tests/fake_claude.py unless --sonnet asks for the real one.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("transcript")
ap.add_argument("folder")
ap.add_argument("out")
ap.add_argument("--lsp", action="store_true", help="use the language servers Baude fetched")
ap.add_argument("--sonnet", type=float, metavar="BUDGET", help="turn the reader on, with this cap in dollars")
ap.add_argument("--model", default="sonnet", help="the reader's model (sonnet, haiku)")
ap.add_argument("--context", default="full", choices=("full", "light"),
                help="what the reader is given: full (your messages, Claude's tool calls and results, its subagents' "
                     "work, the replies) or light (your messages and the replies)")
ap.add_argument("--effort", default="medium", help="the reader's effort (low, medium, high)")
ap.add_argument("--history", default="session", choices=("session", "glossary"),
                help="session: one session reads the whole chat; glossary: a session per part, which knows the "
                     "replies before only through the glossary")
ap.add_argument("--part-chars", type=int, help="about how many characters a part has (default: 18000 full, 9000 light)")
ap.add_argument("--no-evidence", action="store_true", help="don't place names from what Claude read and grepped")
ap.add_argument("--claude", metavar="PATH", help="the claude to run for the reader (default: the one on PATH); a wrapper "
                "that records its stream, say")
ap.add_argument("--timeout", type=float, default=1800)
ap.add_argument("--work", help="folder for settings and the glossary (default: a new temporary one)")
ap.add_argument("--open", action="append", default=[], metavar="NAME",
                help="afterwards, click NAME's link and record what opens (a file and line, or the database schema "
                     "with its table or column selected); a screenshot of each goes in the work folder")
args = ap.parse_args()
work = args.work or tempfile.mkdtemp(prefix="baude-replay-")
real_data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
os.environ["XDG_CONFIG_HOME"] = os.path.join(work, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(work, "data")
if args.lsp and os.path.isdir(os.path.join(real_data, "baude", "lsp")):  # the servers you fetched, not a new copy
    os.makedirs(os.path.join(work, "data", "baude"), exist_ok=True)
    if not os.path.exists(os.path.join(work, "data", "baude", "lsp")):
        os.symlink(os.path.join(real_data, "baude", "lsp"), os.path.join(work, "data", "baude", "lsp"))
os.environ["BAUDE_CLAUDE"] = (args.claude or shutil.which("claude")) if args.sonnet else os.path.join(HERE, "fake_claude.py")
os.environ["FAKE_CLAUDE_LOG"] = os.path.join(work, "fake.log")
sys.path.insert(0, os.path.dirname(HERE))
import baude  # noqa: E402
from baude import Gdk, Gio, GLib, Gtk  # noqa: E402

orig_open = baude.Signal._open


def _open(self):
    if not getattr(self, "replay_store", None):  # one store per glossary (a worktree the chat moved into has its own)
        self.replay_store = os.path.join(work, "stores", hashlib.sha1(self.dir.encode()).hexdigest()[:10])
    self.dir = self.replay_store  # (never <folder>/.baude)
    return orig_open(self)


baude.Signal._open = _open


def pump(cond, timeout):
    end = time.monotonic() + timeout
    ctx = GLib.MainContext.default()
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.02)
    return False


opts = argparse.Namespace(cwd=args.folder, model=None, resume=None)
app = baude.BaudeApp(opts)
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
app.register(None)
css = Gtk.CssProvider()
css.load_from_string(baude.CSS)
Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
app.hold()
baude.save_settings(signal=True, sidebar=False, signal_reader=bool(args.sonnet), signal_lsp=args.lsp,
                    signal_budget=args.sonnet or 0, signal_model=args.model,
                    signal_context=args.context, signal_evidence=not args.no_evidence,
                    signal_effort=args.effort, signal_history=args.history, signal_part_chars=args.part_chars)
win = baude.BaudeWindow(app, opts)
win.set_default_size(1400, 1000)
win.present()
pump(lambda: win.proc is not None, 5)
win.resume_session({"id": "replay", "path": args.transcript, "cwd": args.folder,
                    "title": os.path.basename(args.transcript)})
t0 = time.monotonic()
ref = win.signal


def idle():
    ag = win.signal.agent if win.signal is not None else None
    return win.signal is not None and win.signal.read and win.signal.activity is None and \
        not win.signal.waiting and (ag is None or not ag.busy())


done = pump(idle, args.timeout)
elapsed = time.monotonic() - t0
ref = win.signal  # (it may have moved to the checkout the chat works in)
pump(lambda: False, 1.0)


def row(s, r, strong):
    return {"surface": s, "kind": r.kind, "status": r.status, "strong": strong, "summary": r.summary(),
            "by": r.data.get("by", "rules"), "lsp": r.data.get("lsp"),
            "locations": [{"role": loc["role"], "path": loc["path"], "line": loc.get("line"),
                           "resolver": loc.get("resolver")} for loc in r.locations]}


rows = [row(s, ref.refs[s], s in ref.strong) for s in win.ref_surfaces if s in ref.refs]
rows += [row(s, r, False) for s, r in ref.refs.items()  # Sonnet's names in prose, not in the rules' list
         if s not in win.ref_surfaces and r.data.get("by") == "sonnet"]
opened = {}
for name in args.open:  # click the link, as you would: what comes up?
    if name not in ref.refs:
        opened[name] = {"error": "no link (not in the glossary)"}
        continue
    shown = []
    orig_show = win.show_code
    win.show_code = lambda path, line=None, text=None, note=None: (shown.append((path, line)), True)[1]
    try:
        win.schema.set_visible(False)
        win.open_ref(name, win.transcript)
        sv = win.schema
        if sv.get_visible():
            pump(lambda: sv.canvas.selected is not None or "Not found" in sv.status.get_label()
                 or "no " in sv.status.get_label().lower(), 60)
            pump(lambda: False, 0.5)
            card = sv.canvas.cards[sv.canvas.selected]["table"] if sv.canvas.selected is not None else None
            opened[name] = {"schema": True, "server": (sv.server or {}).get("container"), "database": sv.database,
                            "table": card and card.get("name"), "column": sv.focus_column,
                            "status": sv.status.get_label(), "code": sv.code_link}
        elif shown:
            opened[name] = {"code": [(os.path.relpath(p, ref.root), l) for p, l in shown]}
        else:
            opened[name] = {"popover": True, "summary": ref.refs[name].summary()}
        pump(lambda: False, 0.3)
        shot = os.path.join(work, f"open-{''.join(c if c.isalnum() else '_' for c in name)}.png")
        paintable = Gtk.WidgetPaintable.new(win)
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, win.get_width(), win.get_height())
        node = snap.to_node()
        if node is not None:  # (nothing drawn yet: no picture)
            win.get_native().get_renderer().render_texture(node, None).save_to_png(shot)
            opened[name]["shot"] = shot
    finally:
        win.show_code = orig_show
ag = ref.agent
with open(args.out, "w") as f:
    json.dump({"done": done, "seconds": round(elapsed, 1), "rows": rows, "skipped": ref.skipped,
               "cost": ag.cost if ag else 0, "parts": ag.parts if ag else 0, "model": args.model if ag else None,
               "history": args.history if ag else None, "part_chars": args.part_chars,
               "context": args.context if ag else None, "effort": args.effort if ag else None, "reading": bool(ag), "evidence": not args.no_evidence,
               "servers": ref.code.running() if ref.code else [], "log": [t for _, t in ref.log],
               "searched": ref.folder, "store": getattr(ref, "replay_store", None), "opened": opened}, f, indent=1)
if ref.code is not None:
    ref.code.stop()
print("done" if done else "TIMED OUT", f"{elapsed:.1f}s", len(rows), "names")
