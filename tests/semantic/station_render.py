"""The Space Station scene theme, installed from its file (theme_install's themepacks.install of the folder zipped as
space-station.baudetheme) and drawn offscreen from the installed copy, light and dark, at three moments of its orbit;
then used through the 3D view (NSEvents posted to the app, tests/mouse.py): a click into the Garden planner's box
(its chat's own page), words typed, Send clicked (the fake claude answers); the Bash card's output opened; a click on a
sessions row. Writes OUT/*.png, prints RESULT."""
from station import *  # noqa: F401,F403
import io, zipfile  # noqa: E401,E402
from baudelib import themepacks, themes  # noqa: E402
sys.path.insert(0, os.path.join(W, "tests"))
import mouse  # noqa: E402
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/station-out"
SIZE = (int(os.environ.get("WIDTH", "1280")), int(os.environ.get("HEIGHT", "760")))
os.makedirs(OUT, exist_ok=True)
res = {}
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _d, files in os.walk(STATION):
        for f in files:
            z.write(os.path.join(root, f), os.path.relpath(os.path.join(root, f), STATION))
with open(f"{OUT}/space-station.baudetheme", "wb") as fh:
    fh.write(buf.getvalue())
name, replaced = themepacks.install(buf.getvalue())
got = themes.theme(name)
res["installed"] = {"name": name, "replaced": replaced, "kind": got["manifest"]["kind"], "nodes": len(got["scene"]["nodes"]),
                    "files": got["files"], "code": got.get("code"), "error": got.get("error")}
res["pack_size"] = len(buf.getvalue())
installed = themepacks.folder(name)
load_station_css()
for dark in (False, True):
    opened = []
    v, wd = station_view(opened, theme=installed)
    win = window(v, *SIZE, dark=dark)
    macos.in_unseen(win)
    v.running = False
    pump(0.8)
    for i, t in enumerate((0.0, 12.0, 36.0)):
        v.t = t
        v.advance(0)
        pump(0.3)
        frame(win, 1).save_to_png(f"{OUT}/station-{'dark' if dark else 'light'}-{i}.png")
    panels = [n for n in v.nodes.values() if n.panel is not None and n.widget is not None]
    res["panels_" + ("dark" if dark else "light")] = sorted({n.panel["show"] for n in panels})
    res["panel_count"] = len(panels)
    res["nodes"] = len(v.nodes)
    res["code"] = v.stats["code"]
    res["view_size"] = [v.get_width(), v.get_height(), win.get_width(), win.get_height()]
    if not dark:
        v.t = 0.0; v.advance(0); pump(0.3)
        hand = mouse.Mouse(win); fake_pointer(win, hand)
        page = chat_page(v, "h1/c").win
        bash_page = chat_page(v, "h1/a").win
        pump(0.5)
        hand.down(20, 20); hand.up(20, 20); pump(0.2)  # (a first click on the sky: macOS takes the first for the window)
        x, y = true_point(v, page.input)
        hand.down(x, y); hand.up(x, y); pump(0.2)
        res["box_focused"] = page.input.is_focus()
        hand.type("Leeks too?"); pump(0.2)
        buf_ = page.input.get_buffer()
        res["typed"] = buf_.get_text(buf_.get_start_iter(), buf_.get_end_iter(), False)
        x, y = true_point(v, page.send_btn)
        hand.down(x, y); hand.up(x, y); pump(1.5)
        import test_baude
        res["sent"] = "Leeks too?" in test_baude.labels_text(page.transcript)
        bash = test_baude.find(bash_page.transcript, toolcards.ToolCard)[-1]
        x, y = true_point(v, bash.output_toggle.button)
        hand.down(x, y); hand.up(x, y); pump(0.4)
        res["bash_output_opened"] = bash.output_toggle.get_expanded()
        row = next(n.widget for n in panels if n.panel["show"] == "chat_row").get_row_at_index(0)
        acts = []
        row.get_parent().connect("row-activated", lambda _l, r: acts.append(r.info["id"]))
        x, y = true_point(v, row)
        hand.down(x, y); hand.up(x, y); pump(0.3)
        res["row_activated"] = acts
        pump(0.3)
        frame(win, 1).save_to_png(f"{OUT}/station-used.png")
        res["usage_window_given_back"] = None
    win.destroy(); pump(0.3)
    res["usage_window_given_back" if not dark else "usage_window_given_back_dark"] = v.windows["usage"].get_child() is not None
print("RESULT " + json.dumps(res), flush=True)
