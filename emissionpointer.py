"""emissionpointer — the Bot's pointer (BotPointer): what its tools do with the mouse, played over its page, and
its shapes and the glow round the page's edges (arrow_at, hint_at, chevrons, glow_frame), as Emission's window
(emissionmac.MacOverlay), Baude's Browser page (browserpane) and the phone draw them.
"""
import math
import sys
import time

from baudelib import tokens  # noqa: E402  (its pointer's sizes and times: schema/tokens, the Android app's too)


# ------------------------------------------------------------- the Bot's pointer --

class BotPointer:
    """The Bot's pointer as it's drawn over its page, never in it (not in the page, nor in what the Bot's shown of
    it): what its tools do with the mouse (hover, click, drag, scroll; where, the page's CSS pixels) played in turn.
    It glides there; a click presses it (a ring spreads from it, one each for a double click's presses), a drag
    takes it pressed along its path, a scroll shows the wheel's way (a chevron going that way). It fades once the Bot's
    let it be a while. The glow (the page's edges in the accent) fades in while the Bot's at Emission's tools (acting) or
    its pointer's moving (gliding, pressing, dragging, scrolling), and out once neither is, GLOW_HOLD_S after.
    look(now) says what's to be drawn then (now: time.monotonic())."""
    # (a glide's time: the shortest, the longest; about a second for 1500 px between)
    GLIDE_S = (tokens.seconds("pointer.glide-shortest"), tokens.seconds("pointer.glide-longest"))
    PRESS_S, RING_S, HINT_S = (tokens.seconds(f"pointer.{k}") for k in ("press", "ring", "hint"))
    # (nothing done this long: the pointer faded out, in this long; in, this)
    IDLE_S, FADE_S, APPEAR_S = (tokens.seconds(f"pointer.{k}") for k in ("idle", "fade", "appear"))
    GLOW_IN_S, GLOW_OUT_S, GLOW_HOLD_S = (tokens.seconds(f"pointer.{k}") for k in ("glow-in", "glow-out", "glow-hold"))
    BEHIND = tokens.number("pointer.behind")  # (more than this many steps waiting: glides quicker, so it isn't left
    # behind)
    MOST = 256  # (steps kept waiting while nothing draws them: play; a drag is some 45)

    def __init__(self):
        self.at, self.steps, self.step, self.step_t = None, [], None, 0.0
        self.pressed, self.marks, self.seen = False, [], -1e9  # (marks: rings and chevrons, as they fade)
        self.alpha, self.glow, self.acting, self.looked = 0.0, 0.0, False, None
        self.moved = -1e9  # (when its pointer last moved: the glow's held on till GLOW_HOLD_S after)
        self.tab = None  # (the tab it was last in: hosted, drawn over that tab's page only)
        self.log, self.seq = [], 0  # (what was done, numbered: watchers' to play, in the watch's state)
        self.changed = None  # (told as something's to be moved or drawn anew: a hosted page's frames start then)

    def add(self, kind, x, y, tab=None, path=None, count=1, dx=0, dy=0, played=True):
        """What the Bot did: kind hover, click, drag or scroll, at x, y (a drag's path from there); played False:
        only kept for watchers (one elsewhere plays it, not this one)."""
        self.seq += 1
        ev = {"seq": self.seq, "kind": kind, "x": round(x, 1), "y": round(y, 1)}
        if path:
            ev["path"] = [[round(p[0], 1), round(p[1], 1)] for p in path[:40]]
        if count > 1:
            ev["count"] = count
        if dx or dy:
            ev["dx"], ev["dy"] = dx, dy
        self.log = (self.log + [ev])[-8:]
        if played:
            self.play(ev, tab)

    def play(self, ev, tab=None):
        """ev (add's) played here, after what's waiting."""
        self.tab = tab if tab is not None else self.tab
        x, y = float(ev["x"]), float(ev["y"])
        kind = ev.get("kind")
        if kind == "drag" and len(ev.get("path") or []) >= 2:
            path = [(float(p[0]), float(p[1])) for p in ev["path"]]
            self.steps += [("glide", path[0], 1.0), ("press", True, 0)]
            whole = sum(math.dist(a, b) for a, b in zip(path, path[1:])) or 1
            total = min(1.2, max(0.35, whole / 900))  # (the whole path in about its length's time)
            self.steps += [("glide", p, total * math.dist(a, p) / whole) for a, p in zip(path, path[1:])]
            self.steps.append(("press", False, 0))
        else:
            self.steps.append(("glide", (x, y), 1.0))
            if kind == "click":
                self.steps += [("tap", None, 1.0)] * max(1, min(3, int(ev.get("count") or 1)))
            elif kind == "scroll":
                self.steps.append(("hint", (ev.get("dx") or 0, ev.get("dy") or 0), 1.0))
        # (not being drawn, a hosted page out of sight: steps are only taken as it's looked at, so a Bot at work
        # behind a hidden Browser page only adds to them. The oldest are skipped then, the pointer where they'd have
        # left it; never while it's drawn, where a drag's steps are all played)
        if len(self.steps) > self.MOST and (self.looked is None or time.monotonic() - self.looked > 1.0):
            skipped, self.steps = self.steps[:-self.MOST], self.steps[-self.MOST:]
            for kind, what, _t in skipped:
                if kind == "glide":
                    self.at = what
                elif kind == "press":
                    self.pressed = bool(what)
            self.step = None
        self.seen = self.moved = time.monotonic()
        if self.changed is not None:
            self.changed()

    def set_acting(self, on):
        if bool(on) != self.acting:
            self.acting = bool(on)
            if self.changed is not None:
                self.changed()

    def busy(self):
        """Whether anything's moving, or about to (the glow's hold after the pointer's last move running out): else
        what's drawn stays as it is, till something's done or the Bot's turn changes."""
        return bool(self.steps or self.step or self.marks) or self.alpha not in (0.0, 1.0) or \
            self.glow not in (0.0, 1.0) or (self.glow > 0) != self.glowing() or (self.alpha > 0) != self.shown() or \
            time.monotonic() - self.moved < self.GLOW_HOLD_S

    def moving(self):
        return bool(self.steps or self.step or self.marks)

    def glowing(self, now=None):
        """Whether the glow's wanted: the Bot's at Emission's tools, or its pointer's moving or just has."""
        return self.acting or self.moving() or (now or time.monotonic()) - self.moved < self.GLOW_HOLD_S

    def shown(self, now=None):
        """Whether the pointer's to be seen (it's somewhere, or about to be, and the Bot's been at it lately)."""
        return (self.at is not None or bool(self.steps)) and (
            self.acting or bool(self.steps or self.step) or (now or time.monotonic()) - self.seen < self.IDLE_S)

    def look(self, now=None):
        """What's drawn at now: {x, y (None: nowhere yet), alpha, scale (pressed: smaller), rings [(x, y, radius,
        alpha)], hints [(x, y, dx, dy, how far on, alpha)], glow}."""
        now = time.monotonic() if now is None else now
        dt = 0.0 if self.looked is None else max(0.0, min(0.1, now - self.looked))
        self.looked = now
        self.advance(now)
        want = 1.0 if self.shown(now) else 0.0
        self.alpha = min(want, self.alpha + dt / self.APPEAR_S) if want > self.alpha else \
            max(want, self.alpha - dt / self.FADE_S)
        if self.moving():
            self.moved = now
        g = 1.0 if self.glowing(now) else 0.0
        self.glow = min(g, self.glow + dt / self.GLOW_IN_S) if g > self.glow else \
            max(g, self.glow - dt / self.GLOW_OUT_S)
        x, y = self.at if self.at is not None else (None, None)
        scale = 0.82 if self.pressed else 1.0
        if self.step is not None and self.step[0] == "glide" and self.at is not None:
            (sx, sy), (tx, ty), dur = self.step[1], self.step[2], self.step[3]
            k = min(1.0, (now - self.step_t) / dur) if dur > 0 else 1.0
            e = 4 * k ** 3 if k < 0.5 else 1 - (-2 * k + 2) ** 3 / 2  # (eased in and out, as a hand moves one)
            x, y = sx + (tx - sx) * e, sy + (ty - sy) * e
        elif self.step is not None and self.step[0] == "tap":
            k = (now - self.step_t) / self.PRESS_S
            scale = 1.0 - 0.18 * math.sin(math.pi * min(1.0, k))
        rings, hints = [], []
        for kind, mx, my, t0, extra in self.marks:
            k = (now - t0) / (self.RING_S if kind == "ring" else self.HINT_S)
            if kind == "ring":
                rings.append((mx, my, 5 + 22 * (1 - (1 - k) ** 2), 0.9 * (1 - k)))
            else:
                hints.append((mx, my, extra[0], extra[1], k, 1 - k ** 2))
        self.marks = [m for m in self.marks if now - m[3] < (self.RING_S if m[0] == "ring" else self.HINT_S)]
        return {"x": x, "y": y, "alpha": self.alpha, "scale": scale, "rings": rings, "hints": hints,
                "glow": self.glow, "tab": self.tab}

    def advance(self, now):
        while True:
            if self.step is not None:
                kind = self.step[0]
                dur = self.step[3] if kind == "glide" else self.PRESS_S if kind == "tap" else 0
                if now - self.step_t < dur:
                    return
                if kind == "glide":
                    self.at = self.step[2]
                self.step = None
            if not self.steps:
                return
            kind, arg, share = self.steps.pop(0)
            quick = 0.35 if len(self.steps) > self.BEHIND else 1.0
            if kind == "glide":
                if self.at is None:  # (its first: shown there, not glided from nowhere)
                    self.at = arg
                    continue
                d = math.dist(self.at, arg)
                lo, hi = self.GLIDE_S
                dur = (min(hi, lo + d / 1500) if share == 1.0 else max(0.03, share)) * quick
                self.step = ("glide", self.at, arg, dur)
            elif kind == "press":
                self.pressed = arg
                continue
            elif kind == "tap":
                self.step = ("tap",)
                self.marks.append(("ring", *self.at, now, None))
            elif kind == "hint":
                if arg[0] or arg[1]:
                    self.marks.append(("hint", *self.at, now, arg))
                continue
            self.step_t = now


# (what's drawn over a page for the Bot's pointer, as shapes in the page's points: GTK draws them with its own paths,
# macOS's overlay with Core Animation's)

ARROW = tokens.points("pointer.arrow")  # (its tip at 0, 0)
ARROW_SHADOW = tokens.point("pointer.arrow-shadow")  # (its shadow: this far right and down)
RING_EDGE, ARROW_EDGE = tokens.px("pointer.ring-edge"), tokens.px("pointer.arrow-edge")  # (lines' widths)
HINT_EDGE = tokens.px("pointer.hint-edge")  # (a chevron's)
HINT_HALO = tokens.px("pointer.hint-halo")  # (the white round it)


def arrow_at(x, y, scale=1.0):
    """The pointer's outline, its tip at (x, y)."""
    return [(x + px * scale, y + py * scale) for px, py in ARROW]


def hint_at(x, y, dx, dy, k):
    """A scroll's chevrons for the pointer's tip at (x, y), the wheel turned (dx, dy), k of the way through: their
    way (a unit vector), and their middle: beside the pointer, off it, going that way."""
    n = math.hypot(dx, dy) or 1
    ux, uy = dx / n, dy / n
    d = 30 + 12 * k
    return (ux, uy), (x + 9 + ux * d, y + 13 + uy * d)


def chevrons(cx, cy, ux, uy):
    """Two chevrons, one after the other, at (cx, cy) pointing along (ux, uy): each a line of three points."""
    px, py = -uy, ux
    out = []
    for back in (-4.5, 3.5):
        bx, by = cx + ux * back, cy + uy * back
        out.append([(bx - ux * 4 + px * 7, by - uy * 4 + py * 7), (bx + ux * 3, by + uy * 3),
                    (bx - ux * 4 - px * 7, by - uy * 4 - py * 7)])
    return out


GLOW_STRENGTH = tokens.number("pointer.glow-strength")  # (the glow: how strong it is at the page's edge itself,
GLOW_REACH = tokens.px("pointer.glow-reach")             # 0 to 1, and how far in it reaches, in points)
GLOWS = {}  # ((size, rgb, scale): its picture's pixels, made once)


def glow_at(d, depth=GLOW_REACH):
    """The glow's strength d in from an edge: strong at it, nothing by depth in."""
    return GLOW_STRENGTH * (1 - d / depth) ** 2.4 if d < depth else 0.0


def glow_pixels(size, rgb, scale=2):
    """The glow for a page size by size points, as size * scale pixels each way (RGBA, premultiplied: a texture's,
    or a CGImage's), each pixel as strong as its two nearest edges make it together, so its corners are as soft as
    its sides. MacOverlay stretches its middle to the page's size. Made once for each accent and scale."""
    key = (size, tuple(round(c, 4) for c in rgb), scale)
    if key in GLOWS:
        return GLOWS[key]
    import array
    n = size * scale
    half = (n + 1) // 2
    edge = [glow_at(i / scale) for i in range(half)]
    r, g, b = (c * 255 for c in rgb)
    rows = []
    for y in range(half):
        ey = edge[y]
        left = array.array("I", [0]) * half
        for x in range(half):
            a = 1 - (1 - edge[x]) * (1 - ey)
            left[x] = round(r * a) | round(g * a) << 8 | round(b * a) << 16 | round(255 * a) << 24
        row = left + left[::-1][n % 2:]  # (the right half the left's, mirrored)
        if sys.byteorder != "little":
            row.byteswap()
        rows.append(row.tobytes())
    GLOWS[key] = b"".join(rows + rows[::-1][n % 2:])  # (the bottom half the top's)
    return GLOWS[key]


def glow_frame(w, h, rgb, scale=1):
    """The glow for a page w by h pixels, scale pixels a point, whole (as glow_pixels has it: what GTK draws over a
    streamed page as one texture, not stretched from pieces, which leave seams where they meet): each row its
    corners' pixels worked out, its middle's put in as one."""
    import array
    reach = min(GLOW_REACH * scale, w // 2)
    edge = [glow_at(i / scale) for i in range(reach)]
    r, g, b = (c * 255 for c in rgb)

    def pixel(a):
        return round(r * a) | round(g * a) << 8 | round(b * a) << 16 | round(255 * a) << 24
    rows = []
    for y in range((h + 1) // 2):
        ey = glow_at(y / scale)
        left = array.array("I", [pixel(1 - (1 - e) * (1 - ey)) for e in edge])
        row = left + array.array("I", [pixel(ey)]) * (w - 2 * reach) + left[::-1]
        if sys.byteorder != "little":
            row.byteswap()
        rows.append(row.tobytes())
    return b"".join(rows + rows[::-1][h % 2:])
