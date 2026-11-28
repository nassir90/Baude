"""Where a frame of #454's stage goes at N cards: Python allocate, recording the stage's render nodes, GSK rendering
them (GL, finished), for (a) live cards under perspective, (a2) the same cards under 2D transforms only, (c) cached
textures under perspective. Each step run REPS times by hand on an unseen window, the GPU finished each time."""
from rig import *  # noqa: F401,F403
import statistics
import cards3d
N, MODE, REPS = int(os.environ.get("N", "200")), os.environ.get("MODE", "a"), 30

if MODE == "a2":  # (no perspective, no 3D rotation: a 2D scale+translate+rotate per card)
    def room(self, w, h):
        return Gsk.Transform().translate(Graphene.Point().init(w / 2 + self.pan, h / 2)).scale(0.8, 0.7)
    cards3d.Camera.room = room
st = cards3d.Stage3D(cached=(MODE == "c"))
for i in range(N):
    st.add(make_card(i)[1])
win = window(st)
macos.in_unseen(win)
st.running = False
pump(1.0)
rend = win.get_renderer()
w, h = st.get_width(), st.get_height()

def finish(tex):  # (GPU work done: read back one row's worth via a tiny downloader on the whole texture)
    d = Gdk.TextureDownloader.new(tex); d.set_format(Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED); d.download_bytes()

# baseline: downloading a texture of this size alone
def plain():
    sn = Gtk.Snapshot()
    sn.append_color(Gdk.RGBA(red=1, green=1, blue=1, alpha=1), Graphene.Rect().init(0, 0, w, h))
    return sn.to_node()
base = []
for _ in range(10):
    t0 = time.perf_counter(); finish(rend.render_texture(plain(), Graphene.Rect().init(0, 0, w, h))); base.append((time.perf_counter() - t0) * 1000)
alloc, rec, rend_ms, rec_dirty = [], [], [], []
paint = Gtk.WidgetPaintable.new(st)
for i in range(REPS):
    st.advance(1 / 60)
    t0 = time.perf_counter(); st.do_size_allocate(w, h, -1); alloc.append((time.perf_counter() - t0) * 1000)
    pump(0.0)
    t0 = time.perf_counter(); sn = Gtk.Snapshot(); paint.snapshot(sn, w, h); node = sn.to_node(); rec.append((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter(); finish(rend.render_texture(node, Graphene.Rect().init(0, 0, w, h))); rend_ms.append((time.perf_counter() - t0) * 1000)
# the stage's own snapshot (its Python do_snapshot, each card's kept node put in), then with every widget in every card
# drawn again first (queue_draw on each): what re-recording every card each frame would cost
def record():
    t0 = time.perf_counter(); sn = Gtk.Snapshot(); st.do_snapshot(sn); sn.to_node(); return (time.perf_counter() - t0) * 1000
def walk(wd):
    yield wd
    c = wd.get_first_child()
    while c is not None:
        yield from walk(c); c = c.get_next_sibling()
everything = [x for f in st.bodies for x in walk(f)]
kept = [record() for _ in range(20)]
for _ in range(10):
    for x in everything:
        x.queue_draw()
    rec_dirty.append(record())

def kinds(n, out):
    out[n.get_node_type().value_nick] = out.get(n.get_node_type().value_nick, 0) + 1
    t = n.get_node_type()
    for getter in ("get_child",):
        if hasattr(n, getter):
            try: kinds(getattr(n, getter)(), out)
            except Exception: pass
    if t == Gsk.RenderNodeType.CONTAINER_NODE:
        for j in range(n.get_n_children()): kinds(n.get_child(j), out)
    return out
k = kinds(node, {})
cat = {}
def cats(n):
    t = n.get_node_type()
    if t == Gsk.RenderNodeType.TRANSFORM_NODE:
        c = n.get_transform().get_category().value_nick; cat[c] = cat.get(c, 0) + 1
    if hasattr(n, "get_child") and t != Gsk.RenderNodeType.CONTAINER_NODE:
        try: cats(n.get_child())
        except Exception: pass
    if t == Gsk.RenderNodeType.CONTAINER_NODE:
        for j in range(n.get_n_children()): cats(n.get_child(j))
cats(node)
m = lambda xs: round(statistics.median(xs), 2)
print("RESULT " + json.dumps({"mode": MODE, "n": N, "renderer": type(rend).__name__, "py_allocate_ms": m(alloc),
      "record_nodes_ms": m(kept), "record_after_every_widget_redrawn_ms": m(rec_dirty), "widgets": len(everything), "gsk_render_ms": round(m(rend_ms) - m(base), 2),
      "download_baseline_ms": m(base), "node_kinds": k, "transform_categories": cat, "renders": st.renders}), flush=True)
