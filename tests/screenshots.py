#!/usr/bin/env python3
"""Screenshot matrix: every screen and render baude has, in light and dark.

Run through tests/screenshots.sh (headless, inside Xvfb):

    tests/screenshots.sh                 # all scenes -> screenshots/
    tests/screenshots.sh -k permission   # scenes whose name contains "permission"
    tests/screenshots.sh --out DIR

Writes <scene>-light.png / <scene>-dark.png, an index.html gallery (one theme at a time,
with a Light / Dark switch) and matrix-light.png / matrix-dark.png contact sheets. Scenes drive a real BaudeWindow against tests/fake_claude.py, the same
way the UI tests do, so the pictures always match the current code.

The scenes are in tests/scenes_chat.py, tests/scenes_panels.py, tests/scenes_deps.py and tests/scenes_telemetry.py, registered (tests/scenelib.py's SCENES) as this
file imports them.
"""
import argparse
import html
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_baude as T  # noqa: E402  (sets up the throwaway env: fake claude, temp config dirs)
from test_baude import Adw, Gtk, baude  # noqa: E402

from PIL import Image, ImageDraw, ImageGrab  # noqa: E402
from scenelib import HERE, SCENES  # noqa: E402
import scenes_chat  # noqa: E402,F401  (their scenes, registered in SCENES as they're imported: these first)
import scenes_panels  # noqa: E402,F401
import scenes_deps  # noqa: E402,F401
import scenes_telemetry  # noqa: E402,F401


# --------------------------------------------------------------- capture --

def capture(win, size, path):
    try:
        # the widget size excludes the client-side shadow (a few px per side) that the capture includes
        T.wait_for(lambda: all(0 <= want - got <= 24 for want, got in zip(size, (win.get_width(), win.get_height()))),
                   f"window at {size}", 5)
    except AssertionError:
        raise AssertionError(f"window is {win.get_width()}x{win.get_height()}, wanted {size[0]}x{size[1]}")
    T.pump(0.6)  # let layout, the 120 ms reveal animations and popovers settle
    if os.environ.get("DISPLAY"):
        img = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])  # the whole screen, so popovers are included
        img.crop((0, 0, *size)).save(path)
        return
    if sys.platform == "darwin":
        try:
            img = capture_macos(win)
            img.crop((0, 0, min(img.width, size[0]), min(img.height, size[1]))).save(path)
            return
        except subprocess.CalledProcessError:  # (no Screen Recording for this process: GTK draws it, as elsewhere)
            pass
    # elsewhere without X: GTK draws the window itself, and the popovers open in it (surfaces of their own) where they are
    img = render_widget(win, path)
    for pop in T.find(win, Gtk.Popover, lambda p: p.get_visible() and p.get_mapped()):
        # (each placed in the surface of what it's in: the window's, or another popover's, which is placed in its own)
        x = y = 0
        outer = pop
        while isinstance(outer, Gtk.Popover) and outer.get_surface() is not None:
            x, y = x + outer.get_surface().get_position_x(), y + outer.get_surface().get_position_y()
            outer = outer.get_parent().get_native() if outer.get_parent() is not None else None
        if pop.get_width() and pop.get_height():
            shot = render_widget(pop, path + ".popover.png")
            os.remove(path + ".popover.png")
            img.paste(shot, (x, y), shot)
    img.crop((0, 0, min(img.width, size[0]), min(img.height, size[1]))).save(path)


def render_widget(widget, path):
    """A widget as GTK draws it (its own surface's renderer), as an RGBA image (saved at path on the way)."""
    paintable = Gtk.WidgetPaintable.new(widget)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, widget.get_width(), widget.get_height())
    widget.get_native().get_renderer().render_texture(snap.to_node(), None).save_to_png(path)
    return Image.open(path).convert("RGBA")


def capture_macos(win):
    """macOS: the window as the screen shows it, its traffic lights too (macOS draws those, not GTK). screencapture -l
    takes one window by its number, with the windows it owns (its popovers), so a window of another app in front of
    it never gets in. Scaled back to points."""
    import ctypes
    import subprocess
    import tempfile
    import macos

    main = macos.native_window(win)
    number = macos.objc_send(main, b"windowNumber", ret=ctypes.c_long)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        subprocess.run(["screencapture", "-x", "-o", "-l", str(number), tmp], check=True)
        img = Image.open(tmp).convert("RGB")
    finally:
        os.unlink(tmp)
    scale = img.width / macos.objc_send(main, b"frame", ret=macos._rect()).w
    return img.resize((round(img.width / scale), round(img.height / scale)), Image.LANCZOS)


def reset_state():
    baude.save_settings(zoom=1.0, full_width=False, sidebar=True, collapsed_folders=["archived"], archived=[],
                        signal_reader=False, sidebar_network=None, new_session_host=None, new_session_host_name=None,
                        sidebar_groups=None,
                        hidden_remote=None)
    os.environ["BAUDE_GH"] = ""  # (a Feedback tab's fake GitHub is that scene's, its notes too)
    shutil.rmtree(baude.feedback_dir(), ignore_errors=True)
    shutil.rmtree(os.path.join(os.environ["XDG_CONFIG_HOME"], "Claude", "claude-code-sessions"),  # (a scene's Claude
                  ignore_errors=True)                                                             # Desktop chats)
    T.feedback._gh_seen.update(at=None, repo=None)


THEMES = ("light", "dark")


def contact_sheet(rows, out_dir, theme, per_row=4, thumb_w=360):
    """matrix-<theme>.png: every scene in one theme, in a grid, each with its name above."""
    pad, label_h = 18, 20
    cells = []
    for name, _, _ in rows:
        im = Image.open(os.path.join(out_dir, f"{name}-{theme}.png")).convert("RGB")
        cells.append((name, im.resize((thumb_w, round(im.height * thumb_w / im.width)))))
    grid = [cells[i:i + per_row] for i in range(0, len(cells), per_row)]
    heights = [label_h + max(im.height for _, im in line) for line in grid]
    bg, fg = ("white", "black") if theme == "light" else ("#1e1e1e", "#e8e8e8")
    sheet = Image.new("RGB", (pad + per_row * (thumb_w + pad), pad + sum(h + pad for h in heights)), bg)
    draw = ImageDraw.Draw(sheet)
    y = pad
    for line, h in zip(grid, heights):
        for col, (name, im) in enumerate(line):
            x = pad + col * (thumb_w + pad)
            draw.text((x, y + 4), name, fill=fg)
            sheet.paste(im, (x, y + label_h))
        y += h + pad
    sheet.save(os.path.join(out_dir, f"matrix-{theme}.png"))


INDEX_HEAD = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>baude screenshots</title>
<style>
 :root { --bg: #f6f5f4; --fg: #222; --dim: #666; --line: #d0d0d0; --btn: #fff; }
 :root[data-theme="dark"] { --bg: #1e1e1e; --fg: #eee; --dim: #aaa; --line: #444; --btn: #2d2d2d; }
 body { font: 15px system-ui, sans-serif; margin: 0; background: var(--bg); color: var(--fg); }
 header { position: sticky; top: 0; z-index: 1; display: flex; gap: 16px; align-items: center; padding: 12px 24px;
          background: var(--bg); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
 header h1 { font-size: 18px; margin: 0; } header .meta { color: var(--dim); flex: 1; font-size: 13px; }
 .switch { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
 .switch button { font: inherit; padding: 6px 14px; border: 0; background: var(--btn); color: var(--fg); cursor: pointer; }
 .switch button[aria-pressed="true"] { background: #3584e4; color: white; }
 main { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 520px), 1fr)); gap: 28px; padding: 24px; }
 h2 { margin: 0 0 4px; font-size: 16px; } p { margin: 0 0 10px; color: var(--dim); font-size: 14px; }
 img { width: 100%; border: 1px solid var(--line); border-radius: 6px; display: block; }
</style>
"""

INDEX_SCRIPT = """<script>
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  for (const img of document.querySelectorAll("img[data-scene]")) {
    img.src = img.dataset.scene + "-" + theme + ".png";
    img.parentElement.href = img.src;
  }
  for (const b of document.querySelectorAll(".switch button")) b.setAttribute("aria-pressed", b.dataset.theme === theme);
  try { localStorage.setItem("baude-shots-theme", theme); } catch (e) {}
}
let start = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
try { start = localStorage.getItem("baude-shots-theme") || start; } catch (e) {}
for (const b of document.querySelectorAll(".switch button")) b.addEventListener("click", () => setTheme(b.dataset.theme));
setTheme(start);
</script>
"""


def write_index(rows, out_dir):
    """index.html: one image per scene and a single Light / Dark switch for the whole page."""
    cards = "\n".join(
        f'<section><h2>{html.escape(n)}</h2><p>{html.escape(d)}</p>'
        f'<a href="{n}-light.png"><img data-scene="{n}" src="{n}-light.png" alt="{html.escape(n)}" loading="lazy"></a>'
        f'</section>' for n, d, _ in rows)
    header = (f'<header><h1>baude screenshots</h1><span class="meta">{len(rows)} scenes · generated '
              f'{time.strftime("%Y-%m-%d %H:%M")} by tests/screenshots.sh</span>'
              '<span class="switch" role="group" aria-label="Theme"><button data-theme="light">Light</button>'
              '<button data-theme="dark">Dark</button></span></header>')
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(INDEX_HEAD + header + "\n<main>\n" + cards + "\n</main>\n" + INDEX_SCRIPT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="only scenes whose name contains this")
    ap.add_argument("--only", help="only the scene of this name, and no gallery: what a fix's shots are (fixes.shoot)")
    ap.add_argument("--theme", choices=THEMES, help="only this theme, not both")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(HERE), "screenshots"))
    args = ap.parse_args()
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    app = T.App()
    style = Adw.StyleManager.get_default()
    rows, failed = [], 0
    for name, description, size, build in sorted(SCENES, key=lambda sc: sc[0]):
        if args.k not in name or args.only not in (None, name):
            continue
        made = 0
        for theme, scheme in (("light", Adw.ColorScheme.FORCE_LIGHT), ("dark", Adw.ColorScheme.FORCE_DARK)):
            if args.theme not in (None, theme):
                continue
            style.set_color_scheme(scheme)
            reset_state()
            win = None
            app.next_size = size
            try:
                win = build(app)
                capture(win, size, os.path.join(out_dir, f"{name}-{theme}.png"))
                made += 1
            except Exception as e:  # noqa: BLE001 — keep going, report at the end
                failed += 1
                print(f"FAIL  {name} ({theme}): {e!r}")
            finally:
                for w in Gtk.Window.list_toplevels():  # dialogs and popovers too
                    if isinstance(w, baude.BaudeWindow):
                        T.close(w)
                reset_state()
        if made == (1 if args.theme else 2):  # only count scenes rendered in this run, not leftovers from an earlier one
            rows.append((name, description, size))
            print(f"ok    {name}")
    if rows and not args.only and not args.theme:
        write_index(rows, out_dir)
        for theme in THEMES:
            contact_sheet(rows, out_dir, theme)
        stale = os.path.join(out_dir, "matrix.png")  # the old side-by-side sheet
        if os.path.exists(stale):
            os.remove(stale)
    print(f"\n{len(rows)} scenes, {failed} failures -> {out_dir}/index.html")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
