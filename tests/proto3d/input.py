"""Synthesized clicks and a drag (NSEvents posted to the app, tests/mouse.py) through the 3D transforms."""
from common import *
sys.path.insert(0, os.path.join(W, "tests"))
import mouse
st = cards3d.Stage3D()
cards = [make_card(i) for i in range(10)]
faces = [st.add(c) for _k, c in cards]
win = window(st)
st.camera.orbit = False
pump(0.3)
print("unseen", macos.in_unseen(win), flush=True); pump(0.3)
hand = mouse.Mouse(win); fake_pointer(win, hand)

def find(w, cls, pred=lambda x: True):
    out = []
    def walk(x):
        if isinstance(x, cls) and pred(x): out.append(x)
        c = x.get_first_child()
        while c: walk(c); c = c.get_next_sibling()
    walk(w); return out

def centre_in_win(w):
    ok, p = w.compute_point(win, Graphene.Point().init(w.get_width() / 2, w.get_height() / 2))
    tx, ty = win.get_surface_transform()
    return p.x + tx, p.y + ty

results = {}; print("ready", flush=True)
def click(name, w, card_index, check):
    st.to_top(st.bodies[faces[card_index]]); pump(0.15)
    x, y = centre_in_win(w)
    picked = win.pick(x, y, Gtk.PickFlags.DEFAULT)
    anc = picked
    while anc is not None and anc is not w: anc = anc.get_parent()
    before = len(META.clicked)
    log = []
    g = Gtk.GestureClick(); g.connect("pressed", lambda *a: log.append("pressed")); g.connect("cancel", lambda *a: log.append("cancel"))
    w.add_controller(g)
    m = Gtk.EventControllerMotion(); m.connect("enter", lambda *a: log.append("enter")); m.connect("leave", lambda *a: log.append("leave")); w.add_controller(m)
    sm = Gtk.EventControllerMotion(); sm.connect("motion", lambda c, x, y: log.append(("stage", round(x), round(y)))); st.add_controller(sm)
    hand.down(x, y); hand.up(x, y); pump(0.15)
    results[name] = {"at": [round(x), round(y)], "pick_hits_it": anc is w, "result": check(before), "log": log[:12], "picked": type(picked).__name__, "picked_card": faces.index(st.body_of(picked).child) if st.body_of(picked) else None, "top": faces.index(st.get_last_child())}
    print(name, results[name], flush=True)
st.camera.orbit = bool(os.environ.get("ORBIT"))  # (clicked while the view keeps turning)
click("meta_tool_keep", find(cards[0][1], Gtk.Button, lambda b: b.has_css_class("meta-batch"))[0], 0, lambda n: META.clicked[n:])
click("meta_change_undo", find(cards[1][1], Gtk.Button, lambda b: b.get_icon_name() == "edit-undo-symbolic")[0], 1, lambda n: META.clicked[n:])
click("meta_change_item", find(cards[1][1], Gtk.Button, lambda b: b.has_css_class("meta-item"))[0], 1, lambda n: META.clicked[n:])
bash = cards[2][1]
click("chat_bash_output", bash.output_toggle.button, 2, lambda n: bash.output_toggle.get_expanded())
form = cards[4][1]
rel = find(form, Gtk.CheckButton)[1]
click("form_release", rel, 4, lambda n: rel.get_active())
ans = find(form, Gtk.Button, lambda b: b.has_css_class("suggested-action"))
if ans:
    click("form_answer", ans[0], 4, lambda n: META.clicked[n:])
frame(st).save_to_png("input-clicked.png")
st.camera.orbit = False
# 2. a drag: grab card 3 (subagent) by its title, throw it right
b = st.bodies[faces[3]]
x0, y0 = b.x, b.y
x, y = centre_in_win(cards[3][1].title)
frame(st).save_to_png("input-before.png")
hand.down(x, y)
for i in range(1, 13):
    hand.drag(x + i * 25, y + i * 4)
after_drag = (b.x, b.y)
_rel = st.release
def dbg(b):
    print("release samples", len(b.samples), b.samples[:1], b.samples[-1:], flush=True); _rel(b)
st.release = dbg
hand.up(x + 12 * 25, y + 12 * 4)
results["drag"] = {"moved": [round(after_drag[0] - x0), round(after_drag[1] - y0)], "v": [round(b.vx), round(b.vy)],
                   "held_after": b.held}
st.camera.orbit = True
pump(1.5)
results["after_throw"] = [round(b.x - after_drag[0]), round(b.y - after_drag[1]), round(b.vx), round(b.vy)]
frame(st).save_to_png("input-after.png")
print(json.dumps(results, indent=1))
