"""The Christmas scene (tests/semantic/christmas) drawn offscreen, light and dark: the room, with a real Baude window's
content as the dashboard on the desk; a host's place, a present in it clicked open (its letter rising: the app's own
chat page of its chat) and its letter used (a click in its box, words typed, Send clicked: NSEvents posted to the app,
tests/mouse.py; the fake claude answers); the dashboard clicked and gone to; and a present opened from scene.json alone
(its code not trusted). Writes OUT/*.png and prints RESULT."""
from christmas_rig import *  # noqa: F401,F403
import mouse  # noqa: E402
OUT = os.environ.get("OUT") or "/tmp/christmas-out"
os.makedirs(OUT, exist_ok=True)
res = {}
theme = install()
res["installed"] = {"kind": theme["manifest"]["kind"], "trusted": theme["trusted"], "files": len(theme["files"])}
GIFT = [(0, 0, 0.5), (0, 0.3, 0.5), (0.3, 0, 0.5), (-0.3, 0, 0.5), (0, 0.5, 0)]


def shown(dark, wd, board=None, trusted=True):
    v = view(theme, wd, board, trusted=trusted)
    win = window(v, 1100, 640, dark=dark)
    macos.in_unseen(win)
    v.running = False
    pump(0.8); v.advance(0); pump(0.4)
    return v, win


def click_at(v, win, nid, tries):
    """nid picked where it's drawn: as a click there does (SceneView.picked)."""
    x, y = where_hit(v, win, nid, tries)
    tx, ty = win.get_surface_transform()
    v.picked(x - tx, y - ty)


def node_of(v, suffix, oid):
    return next(k for k, n in v.nodes.items() if k.endswith(suffix) and (v.obj_of(n) or {}).get("id") == oid)


# clicked: a host's place, a present opened, its letter used
for dark in (False, True):
    mode = "dark" if dark else "light"
    v, win = shown(dark, world(dashboard=False))
    hand = mouse.Mouse(win); fake_pointer(win, hand)
    hand.down(20, 20); hand.up(20, 20); pump(0.2)  # (macOS takes the first click for the window)
    x, y = where_hit(v, win, node_of(v, "/rug", "h-workshop"), [(a, 0.5, b) for b in (0.3, 0.2, 0.1, 0.0) for a in (0.42, -0.42, 0.3, -0.3)])
    hand.down(x, y); hand.up(x, y); pump(0.2); settle(v, 2.5)
    frame(win, 1).save_to_png(f"{OUT}/place-{mode}.png")
    gift = node_of(v, "/box", "h-workshop/s3")
    group = gift.rsplit("/", 1)[0]
    res[f"letter_hidden_before_{mode}"] = v.nodes[group + "/panel"].hidden
    x, y = where_hit(v, win, gift, GIFT)
    hand.down(x, y); hand.up(x, y); pump(0.1); settle(v, 0.3)
    frame(win, 1).save_to_png(f"{OUT}/opening-{mode}.png")
    settle(v, 2.5)
    res[f"letter_shown_after_{mode}"] = not v.nodes[group + "/panel"].hidden and v.nodes[gift].hidden
    frame(win, 1).save_to_png(f"{OUT}/opened-{mode}.png")
    if not dark:
        page = chat_page(v, "h-workshop/s3").win
        pump(0.3)
        x, y = true_point(v, page.input)
        hand.down(x, y); hand.up(x, y); pump(0.2)
        res["box_focused"] = page.input.is_focus()
        hand.type("Leeks too?"); pump(0.2)
        x, y = true_point(v, page.send_btn)
        hand.down(x, y); hand.up(x, y); pump(1.5)
        import test_baude
        res["sent"] = "Leeks too?" in test_baude.labels_text(page.transcript)
        settle(v, 0.2)
        frame(win, 1).save_to_png(f"{OUT}/used-{mode}.png")
    res[f"code_{mode}"] = v.stats["code"]
    win.destroy(); pump(0.3)

# the room with the dashboard: a real Baude window's content on the desk's paper
board = dashboard()
for dark in (False, True):
    mode = "dark" if dark else "light"
    v, win = shown(dark, world(), board)
    res[f"nodes_{mode}"], res[f"lights_{mode}"] = len(v.nodes), len(v.scene["lights"])
    frame(win, 1).save_to_png(f"{OUT}/room-{mode}.png")
    hand = mouse.Mouse(win); fake_pointer(win, hand)
    hand.down(20, 20); hand.up(20, 20); pump(0.2)
    x, y = where_hit(v, win, "backing", [(0.48, 0.48, 0.5), (-0.48, 0.48, 0.5), (0.49, 0, 0.5)])
    hand.down(x, y); hand.up(x, y); pump(0.2)
    settle(v, 3.0)
    res[f"dashboard_clicked_{mode}"] = v.stats["code"]
    frame(win, 1).save_to_png(f"{OUT}/dashboard-{mode}.png")
    win.set_child(None); win.destroy(); pump(0.3)

# from scene.json alone: the code not trusted, a present opened all the same
v, win = shown(False, world(), board, trusted=False)
gift = node_of(v, "/box", "h-workshop/s1")
click_at(v, win, gift, GIFT); v.advance(0); pump(0.4)
res["data_only"] = {"code": v.stats["code"], "letter_shown": not v.nodes[gift.rsplit("/", 1)[0] + "/panel"].hidden}
frame(win, 1).save_to_png(f"{OUT}/data-only.png")
print("RESULT " + json.dumps(res), flush=True)
