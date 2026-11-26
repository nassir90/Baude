"""Frame time and CPU of a scene with N live panels (proto3d's cards: Meta's tool cards and change boxes, the chat's
tool cards, a question form) in a grid on the sample's floor, lit, with bloom, the view orbiting: 3 s as it is, then 3 s
with one panel's content changing ten times a second (a tool card's output growing). The window is in, unseen, so GTK's
frame clock and GL run as on screen. Prints RESULT."""
from rig import *  # noqa: F401,F403
import math, resource, statistics
N = int(os.environ.get("N", "3"))
cards = {f"c{i}": make_card(i)[1] for i in range(N)}
wd = scenehost.World([{"kind": "meta_card", "id": f"c{i}", "name": f"card {i}", "used": NOW - i} for i in range(N)])
scene = sample_scene()
cols = max(1, round(math.sqrt(N * 1.6)))
scene["camera"] = {"position": [0, 1.2 + cols * 0.35, 3.2 + cols * 1.05], "target": [0, 1.0, 0], "fov": 45, "orbit": {"yaw": 12, "period": 12}}
scene["nodes"] = [n for n in scene["nodes"] if n["id"] in ("floor", "lamp")] + [
    {"id": "cards", "position": [0, 0.4 + 0.75 * math.ceil(N / cols) / 2 + 0.4, 0], "material": "panel",
     "slots": {"of": "meta_card", "layout": "grid", "columns": cols, "spacing": 0.08, "sort": "recent",
               "panel": {"show": "meta_card", "px": [340, 150], "size": [1.13, 0.5]}}}]
if os.environ.get("NOBLOOM"):
    scene.pop("bloom", None)
if os.environ.get("SCENE") == "station":  # (the Space Station theme as it is, its 14 panels: station.py's world)
    import station
    station.load_station_css()
    v, wd = station.station_view()
    scene = v.scene
    if os.environ.get("NOBLOOM"):
        scene.pop("bloom", None)
    N = None
else:
    v = scenehost.SceneView(scene, wd, {"meta_card": lambda o: cards[o["id"]]}, theme=SAMPLE, trusted=True)
if os.environ.get("RENDER_ONLY"):  # (the view still: only GL drawing each frame, no layout)
    v.advance = lambda dt: (v.stats["frames"].append(0.0), v.area.queue_render())
if os.environ.get("LAYOUT_ONLY"):  # (layout each frame, GL not asked to draw again)
    def lay(dt):
        t0 = time.perf_counter(); v.t += dt; v.do_size_allocate(v.get_width(), v.get_height(), -1); v.queue_draw()
        v.stats["frames"].append((time.perf_counter() - t0) * 1000)
    v.advance = lay
win = window(v, 1250, 760)
macos.in_unseen(win)
pump(1.5)
clock = win.get_frame_clock()
marks = {"frames": []}
clock.connect("before-paint", lambda c: marks.__setitem__("t0", time.perf_counter()))
clock.connect("after-paint", lambda c: "t0" in marks and marks["frames"].append((marks["t0"], time.perf_counter())))

def measure(seconds, during=None):
    marks["frames"].clear(); v.stats["frames"].clear(); v.stats["render_ms"].clear(); v.stats["layout_ms"].clear()
    up0, ums0 = v.stats["uploads"], v.stats["upload_ms"]
    r0, w0 = resource.getrusage(resource.RUSAGE_SELF), time.perf_counter()
    loop = GLib.MainLoop()
    if during:
        GLib.timeout_add(100, lambda: (during(), loop.is_running())[1])
    GLib.timeout_add(int(seconds * 1000), lambda: loop.quit() or False)
    loop.run()
    r1, w1 = resource.getrusage(resource.RUSAGE_SELF), time.perf_counter()
    fr = marks["frames"]
    work = [(b - a) * 1000 for a, b in fr]
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(len(xs) * p))] if xs else None
    ups = v.stats["uploads"] - up0
    return {"fps": round(len(fr) / (w1 - w0), 1), "frame_ms_mean": round(statistics.mean(work), 2) if work else None,
            "frame_ms_p95": round(q(work, 0.95), 2) if work else None,
            "py_layout_ms": round(statistics.mean(v.stats["layout_ms"]), 2) if v.stats["layout_ms"] else None,
            "gl_render_ms": round(statistics.mean(v.stats["render_ms"]), 2) if v.stats["render_ms"] else None,
            "cpu_pct": round((r1.ru_utime + r1.ru_stime - r0.ru_utime - r0.ru_stime) / (w1 - w0) * 100, 1),
            "uploads": ups, "upload_ms_each": round((v.stats["upload_ms"] - ums0) / ups, 2) if ups else None}
if os.environ.get("SAMPLE"):  # (macOS's sample: where the main thread's time goes, C and all)
    import subprocess
    subprocess.Popen(["sample", str(os.getpid()), "2", "-file", os.environ["SAMPLE"]], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
still = measure(3.0)
if N is None:  # (the station: its working chat's Bash card)
    N = sum(1 for n in v.nodes.values() if n.panel is not None and n.widget is not None)
    import test_baude
    target = test_baude.find(chat_page(v, "h1/a").win.transcript, toolcards.ToolCard)[-1]
else:
    target = cards["c2"] if N > 2 else cards["c0"]
k = [0]
def grow():  # (a tool card's result changing: its panel's picture made again)
    k[0] += 1
    if hasattr(target, "set_result"):
        target.set_result(f"{k[0]} passed", False)
    else:
        target.queue_draw()
changing = measure(3.0, grow)
print("RESULT " + json.dumps({"n": N, "scale": win.get_scale_factor(), "bloom": "bloom" in scene, "renderer": type(win.get_renderer()).__name__, "still": still, "one_changing_10hz": changing}), flush=True)
