#!/usr/bin/env python3
"""Baude's side of the parity rig: each scene of tests/parity/scenes.json drawn from tests/parity/fixtures, offscreen,
as <out>/desktop/<theme>/<scene>.png, and what's on screen in it as <out>/desktop/<scene>.json (dump.py's items: the
text, buttons, inputs, switches and images a user sees, top to bottom). A render rig, not a test: nothing's checked.

    python3 tests/parity/desktop.py [--out DIR] [-k NAME] [--theme light|dark]

Runs against tests/fake_claude.py in a throwaway HOME and config dirs (tests/test_baude.py's), never the real claude,
never the real data; on macOS its windows never reach the screen (macos.stay_offscreen)."""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
tempfile.tempdir = "/tmp"  # (every throwaway path lands under /tmp: none of it names a user's own temp folder)
with open(os.path.join(HERE, "fixtures", "sessions.json")) as f:
    HOME = json.load(f)["host"]["home"]  # (the phone's fixture host has the same one)
shutil.rmtree(HOME, ignore_errors=True)
os.makedirs(HOME)
os.environ["HOME"] = HOME  # (before anything reads it: ~ is this run's)
os.environ.setdefault("GSK_RENDERER", "cairo")
sys.path.insert(0, ROOT)
if sys.platform == "darwin":
    import macos
    macos.stay_offscreen()
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, HERE)

import argparse  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402

import screenshots as S  # noqa: E402  (tests/test_baude's throwaway env, the window machinery, capture)
from screenshots import T, Adw, Gtk, baude  # noqa: E402
import update  # noqa: E402
import baudelib.wording as wording  # noqa: E402
import dump  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
SIZE = (S.W, S.H)


def fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


def folder(name):
    """A folder of the fixture host's home: fixtures/<name>/'s files in it, if the fixtures have any."""
    path = os.path.join(HOME, name)
    src = os.path.join(FIX, name)
    if os.path.isdir(src):
        shutil.copytree(src, path, dirs_exist_ok=True)
    os.makedirs(path, exist_ok=True)
    return path


def transcript_lines(name, cwd, lines=None):
    with open(os.path.join(FIX, name)) as f:
        recs = [r.replace("@CWD@", cwd) for r in f.read().splitlines() if r.strip()]
    title = [r for r in recs if '"custom-title"' in r]
    body = [r for r in recs if r not in title]
    return (body if lines is None else body[:lines]) + title


def write_sessions(lines=None, transcript=None, subagents=None):
    """fixtures/sessions.json's sessions as this machine's Claude Code keeps them, each its age (the first's from the
    fixture named by transcript, if given); their paths, in the fixture's order."""
    paths = []
    now = time.time()
    for s in fixture("sessions.json")["sessions"]:
        cwd = folder(s["folder"])
        if s["transcript"]:
            recs = transcript_lines(transcript if not paths and transcript else s["transcript"], cwd, lines)
        else:
            recs = [json.dumps(r) for r in (
                {"type": "user", "cwd": cwd, "sessionId": s["id"], "message": {"role": "user", "content": s["title"]}},
                {"type": "assistant", "cwd": cwd, "sessionId": s["id"],
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "On it."}]}},
                {"type": "custom-title", "customTitle": s["title"], "sessionId": s["id"]})]
        d = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd))
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{s['id']}.jsonl")
        with open(path, "w") as f:
            f.write("\n".join(recs) + "\n")
        os.utime(path, (now - s["ago"], now - s["ago"]))
        if subagents and not paths:  # (the first session's subagents' transcripts, where claude keeps them)
            into = os.path.join(d, s["id"], "subagents")
            os.makedirs(into, exist_ok=True)
            for name in os.listdir(os.path.join(FIX, subagents)):
                with open(os.path.join(FIX, subagents, name)) as f:
                    text = f.read().replace("@CWD@", cwd)
                with open(os.path.join(into, name), "w") as f:
                    f.write(text)
        paths.append(path)
    return paths


def apply_settings():
    keep = {k: v for k, v in fixture("settings.json").items() if k != "description"}
    baude.save_settings(**keep)


def fixture_changelog():
    log = fixture("changelog.json")
    update.changelog = lambda commit, root=None: [tuple(c) for c in log["commits"]]
    update.version = lambda commit, root=None: (log["commit"], log["made"])
    update.STARTED = log["commit"]


def pick_header(win, s):
    """The chat's model, effort and mode as the session has them, picked in the window's pickers as a user would."""
    T.wait_for(lambda: s["model"] in win.model_dd.values, "its models")
    row = next(r for r in win.model_dd._rows() if r.value == s["model"])
    win.model_dd._activate(row)
    T.pump(0.5)
    for dd, value in ((win.effort_dd, s["effort"]), (win.mode_dd, s["mode"])):
        T.wait_for(lambda dd=dd, value=value: value in dd.values, f"the {value} pick")
        dd.set_selected(dd.values.index(value))
    T.pump(0.5)


def open_chat(app, sc):
    shutil.rmtree(os.path.join(HOME, "garden"), ignore_errors=True)  # (what an earlier scene put in the folder)
    shown = fixture("sessions.json")["sessions"][sc.get("session", 0)]
    if sc.get("files"):  # (the scene's files join the chat's folder)
        shutil.copytree(os.path.join(FIX, sc["files"]), folder("garden"), dirs_exist_ok=True)
    path = write_sessions(sc.get("lines"), sc.get("transcript"), sc.get("subagents"))[sc.get("session", 0)]
    win = app.window(S.opts(cwd=folder("garden")))
    info = baude.read_session_info(path)
    win.resume_session(info)
    T.wait_for(lambda: win.session_id == info["id"] and win.transcript.get_first_child() is not None, "its history")
    T.pump(0.8)
    pick_header(win, shown)
    if shown["state"] == "working":  # (at a turn, as tests/screenshots.py's 03-working)
        win.set_busy(True)
        T.pump(0.4)
    if sc.get("open"):
        for g in T.find(win, baude.ToolGroup):
            g.toggle.set_expanded(True)
        for card in list(win.tool_cards.values()):
            for t in ("input_toggle", "output_toggle"):
                if getattr(card, t, None) is not None:
                    getattr(card, t).set_expanded(True)
        T.pump(0.4)
    if sc.get("ask"):
        card = win.tool_cards[sc["ask"]]
        if card.state == "interrupted":  # (a finished chat's call with no result: asking now)
            card.set_state("running")
        win.permission_card(f"perm-{sc['ask']}", card.name, card.input, sc["ask"])
        T.pump(0.4)
    if sc.get("page"):
        win.show_panel(True, {"signal": "refs"}.get(sc["page"], sc["page"]))
        T.pump(0.4)
    for t in sc.get("running", []):  # (background tasks heard as started)
        win.on_task_event("task_started", dict(t))
    if sc.get("process"):
        row = win.panel.proc_rows[sc["process"]]
        win.panel.open_process(row) if sc.get("full") else row.set_expanded(True)
    if sc.get("view"):
        win.tool_cards[sc["view"]].view_btn.set_active(True)
    if sc.get("reveal"):
        win.show_folder(os.path.join(HOME, "garden", sc["reveal"]))
    if sc.get("gallery"):
        win.panel.files_page.show_gallery(True, os.path.join(HOME, "garden", sc["gallery"]), save=False)
        T.pump(1.5)  # (the pictures' shapes and thumbnails load off the main thread)
    if sc.get("hold"):
        exp = next(e for e in T.find(win.panel.files_page.view, Gtk.TreeExpander)
                   if getattr(e, "node", None) is not None and os.path.basename(e.node.path) == sc["hold"])
        win.panel.files_page.menu(exp, 40, 12)
    if sc.get("running") or sc.get("process") or sc.get("view") or sc.get("reveal") or sc.get("gallery") or sc.get("hold"):
        T.pump(0.6)
    if sc.get("state") == "working":
        win.set_busy(True)
    if sc.get("queued"):
        win.input.get_buffer().set_text(sc["queued"])
        win.queue_message()
        win.queued[-1].show_buttons(True)
        T.pump(0.4)
    if sc.get("down"):  # (the rig's chats are this machine's: stand in for a network host gone, as update_banner shows it)
        win.banner.set_title(wording.say("hosts.down", host=fixture("sessions.json")["host"]["name"]))
        win.banner.set_revealed(True)
        win.composer_box.gate("host", True)
    if sc.get("draft"):
        win.input.get_buffer().set_text(sc["draft"])
    vadj = win.scroller.get_vadjustment()
    vadj.set_value(vadj.get_upper())  # (at its end, as a chat opens)
    T.pump(0.4)
    return win


def build(app, sc):
    name = sc["name"]
    if name.startswith("home"):
        write_sessions()
        win = app.window(S.opts(cwd=folder("garden")))
        if name == "home":
            return win
        title = fixture("sessions.json")["sessions"][0]["title"]
        T.wait_for(lambda: S.sidebar_row(win, title) is not None, "rows")
        T.pump(1)  # (the list settled: the row kept)
        if name == "home-row-menu":
            win.sidebar.show_menu(S.sidebar_row(win, title), 120, 14)
        elif name == "home-host-menu":
            S.host_right_click(win)
        elif name == "home-add-host":
            win.ask_add_host()
        return win
    if name.startswith("chat") or name.startswith("panel"):
        return open_chat(app, sc)
    if name == "menu":
        win = open_chat(app, sc)
        button = next(b for b in T.find(win, Gtk.MenuButton) if b.get_icon_name() == "open-menu-symbolic")
        button.popup()
        return win
    if name == "feedback":
        win = open_chat(app, sc)
        win.open_feedback()
        return win
    if name == "settings":
        win = open_chat(app, sc)
        win.open_settings()
        return win
    if name == "changelog":
        win = open_chat(app, sc)
        update.changelog_dialog().present(win)
        return win
    return None


def capture(win, path):
    """The window as GTK draws it, with its open popovers where they are: the picture grown to hold one that reaches
    past the window's edge (☰'s does), not cut at it."""
    T.wait_for(lambda: (win.get_width(), win.get_height()) == SIZE, f"window at {SIZE}", 5)
    T.pump(0.6)  # (layout, the reveal animations and popovers settled)
    img = S.render_widget(win, path)
    pops = []
    for pop in dump._popovers(win):
        if pop.get_width() and pop.get_height():
            pops.append((S.render_widget(pop, path + ".popover.png"), dump._origin(pop)))
            os.remove(path + ".popover.png")
    w = max([img.width] + [x + shot.width for shot, (x, _) in pops])
    h = max([img.height] + [y + shot.height for shot, (_, y) in pops])
    canvas = S.Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(img, (0, 0))
    for shot, at in pops:
        canvas.paste(shot, at, shot)
    canvas.save(path)


def areas(win):
    """The window's parts, outermost first, as the dump names them."""
    header = next(iter(T.find(win, Adw.HeaderBar)), None)
    return [(header, "header"), (win.sidebar, "sidebar"), (win.panel, "panel"), (win.scroller, "chat"),
            (win.composer, "composer"), (win.status.get_parent(), "status")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("-k", default="")
    ap.add_argument("--theme", choices=("light", "dark"))
    args = ap.parse_args()
    out = os.path.join(os.path.abspath(args.out), "desktop")
    os.makedirs(out, exist_ok=True)
    fixture_changelog()
    shutil.copytree(os.path.join(FIX, "tasks"), "/tmp/parity/tasks", dirs_exist_ok=True)  # (the fixture transcript's path)
    app = T.App()
    style = Adw.StyleManager.get_default()
    failed = 0
    for sc in fixture_scenes():
        if args.k not in sc["name"]:
            continue
        for theme, scheme in (("light", Adw.ColorScheme.FORCE_LIGHT), ("dark", Adw.ColorScheme.FORCE_DARK)):
            if args.theme not in (None, theme):
                continue
            style.set_color_scheme(scheme)
            S.reset_state()
            apply_settings()
            app.next_size = SIZE
            win = None
            try:
                win = build(app, sc)
                if win is None:
                    with open(os.path.join(out, f"{sc['name']}.json"), "w") as f:
                        json.dump({"scene": sc["name"], "app": "desktop", "missing": "no desktop scene"}, f, indent=1)
                    break
                os.makedirs(os.path.join(out, theme), exist_ok=True)
                capture(win, os.path.join(out, theme, f"{sc['name']}.png"))
                if theme == "light" or args.theme == "dark":
                    with open(os.path.join(out, f"{sc['name']}.json"), "w") as f:
                        json.dump({"scene": sc["name"], "app": "desktop", "size": list(SIZE),
                                   "items": dump.items(win, areas(win))}, f, indent=1, ensure_ascii=False)
                print(f"ok    {sc['name']} ({theme})")
            except Exception as e:  # noqa: BLE001 — keep going, report at the end
                failed += 1
                print(f"FAIL  {sc['name']} ({theme}): {e!r}")
            finally:
                for w in Gtk.Window.list_toplevels():
                    if isinstance(w, baude.BaudeWindow):
                        T.close(w)
                S.reset_state()
                shutil.rmtree(os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects"), ignore_errors=True)
    print(f"{failed} failures -> {out}")
    return 1 if failed else 0


def fixture_scenes():
    with open(os.path.join(HERE, "scenes.json")) as f:
        return json.load(f)["scenes"]


if __name__ == "__main__":
    sys.exit(main())
