"""The sample scene drawn offscreen, light and dark, at three moments of its orbit, each chat panel the app's own chat
page of its chat (ChatPage), and one used through the scene: a click into its box, words typed, Send clicked (NSEvents
posted to the app, tests/mouse.py; the fake claude answers), the panel's picture made again. Writes OUT/*.png and prints RESULT."""
from rig import *  # noqa: F401,F403
sys.path.insert(0, os.path.join(W, "tests"))
import mouse  # noqa: E402
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/semantic-out"
os.makedirs(OUT, exist_ok=True)
load_theme_css()
res = {}
for dark in (False, True):
    opened = []
    v = view(opened=opened)
    win = window(v, 1100, 640, dark=dark)
    macos.in_unseen(win)
    v.running = False
    pump(0.6)
    for i, t in enumerate((0.0, 10.0, 30.0)):
        v.t = t
        v.advance(0)
        pump(0.25)
        frame(win, 1).save_to_png(f"{OUT}/scene-{'dark' if dark else 'light'}-{i}.png")
    res["code_" + ("dark" if dark else "light")] = v.stats["code"]
    if not dark:
        v.t = 0.0; v.advance(0); pump(0.3)
        hand = mouse.Mouse(win); fake_pointer(win, hand)
        page = chat_page(v, "h1/c").win
        bash_page = chat_page(v, "h1/a").win
        pump(0.5)
        up0 = v.stats["uploads"]
        x, y = true_point(v, page.input)
        log = []
        g = Gtk.GestureClick(); g.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        g.connect("pressed", lambda *a: log.append(("pressed", round(a[2]), round(a[3])))); page.input.add_controller(g)
        hand.down(20, 20); hand.up(20, 20); pump(0.2)  # (a first click on the scene's sky: macOS takes the first for the window)
        res["active"] = win.is_active()
        hand.down(x, y); hand.up(x, y); pump(0.2)
        picked = win.pick(*[c - o for c, o in zip((x, y), win.get_surface_transform())], Gtk.PickFlags.DEFAULT)
        res["picked_at_box"] = type(picked).__name__
        res["box_focused"] = page.input.is_focus()
        res["window_focus"] = type(win.get_focus()).__name__
        res["entry_log"] = log
        hand.type("Leeks too?"); pump(0.2)
        buf = page.input.get_buffer()
        res["typed"] = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        x, y = true_point(v, page.send_btn)
        hand.down(x, y); hand.up(x, y); pump(1.5)
        import test_baude
        res["sent_in_transcript"] = "Leeks too?" in test_baude.labels_text(page.transcript)
        res["uploads_after_typing"] = v.stats["uploads"] - up0
        bash = test_baude.find(bash_page.transcript, toolcards.ToolCard)[-1]
        x, y = true_point(v, bash.output_toggle.button)
        hand.down(x, y); hand.up(x, y); pump(0.4)
        res["bash_output_opened"] = bash.output_toggle.get_expanded()
        frame(win, 1).save_to_png(f"{OUT}/scene-used.png")
        # a click on the floor opens nothing; on the plinth (pick none) nothing
        res["opened"] = opened
    win.destroy(); pump(0.2)
# data alone (no code): a box per chat that a click opens into its panel (pick reveal), a tag that opens the chat
scene = sample_scene()
scene["camera"]["orbit"] = None
scene["camera"].pop("orbit")
scene["nodes"] = [n for n in scene["nodes"] if n["id"] in ("floor", "lamp")] + [
    {"id": "gifts", "position": [0, 1.07, 0], "material": "panel",
     "slots": {"of": "chat", "limit": 2, "layout": "row", "spacing": 0.3, "hidden": True,
               "panel": {"show": "chat", "px": [400, 520], "size": [1.54, 2.0]},
               "with": [{"id": "box", "mesh": "box", "material": "lamp", "position": [0, -0.6, 0], "scale": [0.8, 0.8, 0.8], "pick": "reveal"},
                        {"id": "tag", "mesh": "sphere", "material": "plinth", "position": [0.9, -0.9, 0.3], "scale": [0.2, 0.2, 0.2], "pick": "open"}]}}]
opened = []
v = view(scene, opened=opened, trusted=False)
win = window(v, 1100, 640)
macos.in_unseen(win); v.running = False; pump(0.6); v.advance(0); pump(0.3)
frame(win, 1).save_to_png(f"{OUT}/reveal-before.png")
hand = mouse.Mouse(win); fake_pointer(win, hand)
def at_world(node_id):
    n = v.nodes[node_id]
    view_, proj, px, _e = v.matrices(v.get_width(), v.get_height())
    x, y, _z, w = scenehost._vec(v.world_matrix(n).multiply(view_).multiply(proj).multiply(px), 0, 0, 0)
    tx, ty = win.get_surface_transform()
    return x / w + tx, y / w + ty
box = next(k for k in v.nodes if k.endswith("/box"))
tag = next(k for k in v.nodes if k.endswith("/tag"))
panel = box.rsplit("/", 1)[0] + "/panel"
hand.down(20, 20); hand.up(20, 20); pump(0.2)
res["reveal_panel_hidden_before"] = v.nodes[panel].hidden
x, y = at_world(box); hand.down(x, y); hand.up(x, y); pump(0.3); v.advance(0); pump(0.3)
res["reveal_box_hidden_after"] = v.nodes[box].hidden
res["reveal_panel_shown_after"] = not v.nodes[panel].hidden
x, y = at_world(tag); hand.down(x, y); hand.up(x, y); pump(0.3)
res["tag_opened"] = [o["id"] for o in opened]
frame(win, 1).save_to_png(f"{OUT}/reveal-after.png")
print("RESULT " + json.dumps(res), flush=True)
