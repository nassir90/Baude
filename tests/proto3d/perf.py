"""Frame time and CPU for one approach (APPROACH=a: real widgets with 3D transforms; b: textures in a GLArea) and N
cards: 3 s orbiting, then 3 s with a card thrown every 0.5 s. The window is in, unseen (macos.in_unseen), so GTK's frame
clock and its GL renderer run as they would on screen."""
from common import *
import resource, random, statistics
A, N = os.environ.get("APPROACH", "a"), int(os.environ.get("N", "10"))

def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6  # (macOS: bytes)

def rss_now():
    import subprocess
    return int(subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout) / 1024

base = rss_now()
ALLOC, SNAP = [], []
class Timed(cards3d.Stage3D):
    def do_size_allocate(self, w, h, b):
        t = time.perf_counter(); super().do_size_allocate(w, h, b); ALLOC.append((time.perf_counter() - t) * 1000)
    def do_snapshot(self, s):
        t = time.perf_counter(); super().do_snapshot(s); SNAP.append((time.perf_counter() - t) * 1000)
st = Timed() if A == "a" else Timed(cached=True) if A == "c" else cards3d.GLCards(renderer)
for i in range(N):
    st.add(make_card(i)[1])
win = window(st)
macos.in_unseen(win)
pump(1.5)
after_cards = rss_now()
clock = win.get_frame_clock()
marks = {"t0": None, "frames": []}
clock.connect("before-paint", lambda c: marks.__setitem__("t0", time.perf_counter()))
marks["ph"] = []
clock.connect("layout", lambda c: marks.__setitem__("tl", time.perf_counter()))
clock.connect("paint", lambda c: marks.__setitem__("tp", time.perf_counter()))
clock.connect_after("after-paint", lambda c: marks["t0"] is not None and "tl" in marks and "tp" in marks and marks["ph"].append(
    (marks["tl"] - marks["t0"], marks["tp"] - marks["tl"], time.perf_counter() - marks["tp"])))
clock.connect("after-paint", lambda c: marks["t0"] is not None and marks["frames"].append((marks["t0"], time.perf_counter())))

def measure(seconds, during=None):
    marks["frames"].clear(); marks["ph"].clear()
    r0, w0 = resource.getrusage(resource.RUSAGE_SELF), time.perf_counter()
    loop = GLib.MainLoop()
    if during:
        GLib.timeout_add(500, lambda: (during(), loop.is_running())[1])
    GLib.timeout_add(int(seconds * 1000), lambda: loop.quit() or False)
    loop.run()
    r1, w1 = resource.getrusage(resource.RUSAGE_SELF), time.perf_counter()
    fr = list(marks["frames"])
    work = [(b - a) * 1000 for a, b in fr]
    gaps = [(fr[i][0] - fr[i - 1][0]) * 1000 for i in range(1, len(fr))]
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(len(xs) * p))] if xs else None
    cpu = (r1.ru_utime + r1.ru_stime - r0.ru_utime - r0.ru_stime) / (w1 - w0) * 100
    return {"fps": round(len(fr) / (w1 - w0), 1), "frame_ms_mean": round(statistics.mean(work), 2) if work else None,
            "frame_ms_p95": round(q(work, 0.95), 2) if work else None, "interval_ms_p95": round(q(gaps, 0.95), 1) if gaps else None,
            "cpu_pct": round(cpu, 1),
            "phases_ms(update,layout,paint)": [round(statistics.mean(x[i] for x in marks["ph"]) * 1000, 2) for i in range(3)] if marks["ph"] else None}

rng = random.Random(3)
def throw():
    b = rng.choice(st.physics.bodies)
    a = rng.uniform(0, 2 * math.pi)
    b.vx, b.vy, b.vspin = 1600 * math.cos(a), 1600 * math.sin(a), 60
    if A in "ac":
        st.to_top(b)
    else:
        st.physics.raise_(b)
import math
st.frames.clear(); ALLOC.clear(); SNAP.clear()
orbit = measure(3.0)
orbit["py_allocate_ms"] = round(statistics.mean(ALLOC), 2) if ALLOC else None
orbit["snapshot_ms"] = round(statistics.mean(SNAP), 2) if SNAP else None
py_orbit = [ms for _t, ms in st.frames]
st.frames.clear()
thrown = measure(3.0, throw)
py_thrown = [ms for _t, ms in st.frames]
out = {"approach": A, "n": N, "renderer": type(win.get_renderer()).__name__, "orbit": orbit, "thrown": thrown,
       "python_step_ms_mean": [round(statistics.mean(py_orbit), 3), round(statistics.mean(py_thrown), 3)],
       "rss_mb_cards": round(after_cards - base, 1)}
if A == "c":
    out.update(renders=st.renders)
if A == "b":
    out.update(texture_bytes_per_card=round(st.bytes / N), uploads=st.uploads, upload_ms_per_card=round(st.upload_ms / max(st.uploads, 1), 2))
print("RESULT " + json.dumps(out), flush=True)
