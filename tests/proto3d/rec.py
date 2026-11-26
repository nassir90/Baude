"""Frame sequences, stepped by hand at 30 fps: an orbit (the view turning, a card's output arriving live), then a card
grabbed and thrown by a synthesized drag (NSEvents posted to the app) into the others."""
from common import *
sys.path.insert(0, os.path.join(W, "tests"))
import mouse
OUT = sys.argv[2]; N = int(os.environ.get("N", "11")); DARK = bool(os.environ.get("DARK"))
os.makedirs(OUT + "/orbit", exist_ok=True); os.makedirs(OUT + "/throw", exist_ok=True)
st = cards3d.Stage3D(cached=os.environ.get("APPROACH", "c") == "c")
cards = [make_card(i) for i in range(N)]
faces = [st.add(c) for _k, c in cards]
live = toolcards.ToolCard("Bash", "live")
live.set_input({"command": "make test"})
faces.append(st.add(live))
win = window(st, 960, 600, dark=DARK)
macos.in_unseen(win)
st.running = False
pump(0.3)
hand = mouse.Mouse(win); fake_pointer(win, hand)
DT = 1 / 30

def shot(folder, i):
    st.advance(DT)
    pump(0.012)
    frame(win, 1).save_to_png(f"{OUT}/{folder}/{i:04d}.png")

for i in range(150):  # 5 s orbiting; the live card's output comes in line by line
    if i in (40, 70, 100):
        live.add_output_text(f"test_{i // 30} ... ok\n") if hasattr(live, "add_output_text") else None
        live.output_toggle.set_visible(True)
        live.output_toggle.set_expanded(True)
    if i == 110:
        live.set_result("3 passed", False); live.set_state("done")
    shot("orbit", i)

# grab-and-throw: the subagent card (index 3) by its title, pulled right and up, let go
b = max((st.bodies[f] for f in faces[:-1]), key=lambda b: b.y)  # (the nearest card)
card = cards[faces.index(b.child)][1]
st.to_top(b); shot("throw", 0)
t = card
ok, p = t.compute_point(win, Graphene.Point().init(t.get_width() / 2, t.get_height() / 2))
x, y = p.x, p.y
hand.down(x, y); shot("throw", 1)
i = 2
for k in range(1, 15):
    hand.drag(x + k * 10, y - k * 6 - (k * k) * 0.5)
    shot("throw", i); i += 1
hand.up(x + 14 * 10, y - 14 * 6 - 14 * 14 * 0.5)
print("thrown at", round(b.vx), round(b.vy), flush=True)
for k in range(120):
    shot("throw", i); i += 1
print("frames", i, "renders", st.renders, flush=True)
