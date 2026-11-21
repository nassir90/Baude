#!/usr/bin/env python3
"""Live viewer for the Android screenshot rig.

    python3 android/tools/screens_viewer.py [port]      (default port 8765)

Serves http://127.0.0.1:<port>/ with a board of every PNG under
android/app/build/screenshots/{light,dark}/ (what `./gradlew screenshots`
writes). The page polls /api/screens about once a second and swaps in
images whose content changed.

Before/after: when a screen's PNG changes content, the version it replaced is
kept under android/app/build/screenshots-history/<theme>/<name>/<ms>-<sha>.png
(the newest few per screen), plus last.png, the last content this viewer saw,
so a change made while the viewer was not running still shows up as a
before/after on its next start. Everything lives under build/, so a gradle
clean wipes the history too.

Standard library only.
"""

import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
SHOTS = HERE.parent / "app" / "build" / "screenshots"
HISTORY = HERE.parent / "app" / "build" / "screenshots-history"
THEMES = ("light", "dark")
KEEP = 10            # earlier renders kept per screen
SCAN_EVERY = 0.25    # seconds between directory scans
GONE_AFTER = 2.0     # seconds a file must stay missing before its screen is dropped

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
HIST_RE = re.compile(r"^(\d+)-([0-9a-f]{6,40})$")
PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_END = b"IEND\xaeB`\x82"


def sha1(data):
    return hashlib.sha1(data).hexdigest()


def complete_png(data):
    # A file still being written has the signature but not yet the IEND chunk.
    return data.startswith(PNG_SIG) and data.rstrip(b"\0").endswith(PNG_END)


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class Screen:
    def __init__(self, theme, name):
        self.theme = theme
        self.name = name
        self.data = b""
        self.sha = ""
        self.mtime = 0.0
        self.sig = None
        self.present = False
        self.missing_since = None
        self.history = []  # newest first: {"id", "sha", "time"}
        self.load_history()

    @property
    def dir(self):
        return HISTORY / self.theme / self.name

    def load_history(self):
        items = []
        if self.dir.is_dir():
            for p in self.dir.glob("*.png"):
                m = HIST_RE.match(p.stem)
                if m:
                    items.append({"id": p.stem, "sha": m.group(2), "time": int(m.group(1)) / 1000})
        items.sort(key=lambda h: h["id"], reverse=True)
        self.history = items

    def archive(self, data, when):
        """Keep `data` (the content being replaced) as the newest earlier render."""
        sha = sha1(data)
        hid = "%013d-%s" % (int(when * 1000), sha[:12])
        write_atomic(self.dir / (hid + ".png"), data)
        self.history.insert(0, {"id": hid, "sha": sha[:12], "time": when})
        for old in self.history[KEEP:]:
            try:
                (self.dir / (old["id"] + ".png")).unlink()
            except OSError:
                pass
        del self.history[KEEP:]

    def listing(self):
        return {
            "theme": self.theme,
            "name": self.name,
            "mtime": self.mtime,
            "sha": self.sha[:12],
            "changed_at": self.history[0]["time"] if self.history else None,
            "history": self.history,
        }


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.screens = {}
        self.pending = {}
        self.boot = "%x" % int(time.time())
        self.counter = 0

    @property
    def version(self):
        return "%s-%d" % (self.boot, self.counter)

    def scan(self):
        seen = set()
        changed = False
        for theme in THEMES:
            try:
                entries = list(os.scandir(SHOTS / theme))
            except OSError:
                continue
            for e in entries:
                if not e.name.endswith(".png"):
                    continue
                name = e.name[:-4]
                if not NAME_RE.match(name):
                    continue
                key = (theme, name)
                try:
                    st = e.stat()
                except OSError:
                    continue
                seen.add(key)
                sig = (st.st_mtime_ns, st.st_size)
                cur = self.screens.get(key)
                if cur is not None and cur.present and cur.sig == sig:
                    self.pending.pop(key, None)
                    continue
                # Only read a file once its size and mtime held still for a scan.
                if self.pending.get(key) != sig:
                    self.pending[key] = sig
                    continue
                try:
                    data = Path(e.path).read_bytes()
                except OSError:
                    continue
                if not complete_png(data):
                    continue
                self.pending.pop(key, None)
                self.ingest(key, data, st.st_mtime, sig)
                changed = True
        with self.lock:
            t = time.monotonic()
            for key, s in self.screens.items():
                if key in seen:
                    s.missing_since = None
                elif s.present:
                    # A re-render may delete and rewrite the folder; only drop a
                    # screen once it has stayed gone for a moment.
                    if s.missing_since is None:
                        s.missing_since = t
                    elif t - s.missing_since >= GONE_AFTER:
                        s.present = False
                        changed = True
            if changed:
                self.counter += 1

    def ingest(self, key, data, mtime, sig):
        theme, name = key
        sha = sha1(data)
        with self.lock:
            s = self.screens.get(key)
            if s is None:
                s = Screen(theme, name)
                last = s.dir / "last.png"
                try:
                    old = last.read_bytes()
                except OSError:
                    old = None
                if old and sha1(old) != sha:
                    # Changed while the viewer was not watching.
                    s.archive(old, mtime)
                self.screens[key] = s
            elif s.sha and s.sha != sha:
                s.archive(s.data, time.time())
            if s.sha != sha or not (s.dir / "last.png").exists():
                write_atomic(s.dir / "last.png", data)
            s.data, s.sha, s.mtime, s.sig, s.present = data, sha, mtime, sig, True

    def snapshot(self):
        with self.lock:
            items = [s.listing() for s in self.screens.values() if s.present]
            items.sort(key=lambda s: (s["name"], THEMES.index(s["theme"])))
            return {"version": self.version, "dir": str(SHOTS), "screens": items}

    def current(self, theme, name):
        with self.lock:
            s = self.screens.get((theme, name))
            return s.data if s is not None and s.present else None


STORE = Store()


def scanner():
    while True:
        try:
            STORE.scan()
        except Exception:
            traceback.print_exc()
        time.sleep(SCAN_EVERY)


class Handler(BaseHTTPRequestHandler):
    server_version = "screens-viewer"

    def log_message(self, fmt, *args):
        pass  # the page polls every second; keep the terminal quiet

    def send(self, code, body, ctype, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self.route()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def route(self):
        url = urlsplit(self.path)
        parts = [unquote(p) for p in url.path.split("/") if p]
        if not parts:
            return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
        if parts == ["api", "screens"]:
            body = json.dumps(STORE.snapshot()).encode()
            return self.send(200, body, "application/json")
        if len(parts) == 3 and parts[0] == "img" and parts[1] in THEMES and parts[2].endswith(".png"):
            name = parts[2][:-4]
            data = STORE.current(parts[1], name) if NAME_RE.match(name) else None
            if data is None:
                return self.send(404, b"not found", "text/plain")
            cache = "max-age=86400" if url.query else "no-store"
            return self.send(200, data, "image/png", cache)
        if (len(parts) == 4 and parts[0] == "hist" and parts[1] in THEMES
                and NAME_RE.match(parts[2]) and parts[3].endswith(".png")
                and HIST_RE.match(parts[3][:-4])):
            try:
                data = (HISTORY / parts[1] / parts[2] / parts[3]).read_bytes()
            except OSError:
                return self.send(404, b"not found", "text/plain")
            return self.send(200, data, "image/png", "max-age=86400, immutable")
        return self.send(404, b"not found", "text/plain")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Android screens</title>
<style>
:root{color-scheme:light dark;--bg:#f5f5f3;--panel:#fff;--fg:#1d1d1f;--muted:#6b6b72;--line:#d8d8dd;
  --board:#e7e7e3;--accent:#e8590c;--accent-fg:#fff;--shadow:0 1px 4px rgba(0,0,0,.18)}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--panel:#222226;--fg:#ececf0;--muted:#9b9ba4;
  --line:#37373e;--board:#0f0f11;--accent:#ff8a3d;--accent-fg:#1b1b1b;--shadow:0 1px 4px rgba(0,0,0,.7)}}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
  font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
body{display:flex;flex-direction:column;overflow:hidden}
header{display:flex;gap:10px;align-items:center;padding:8px 12px;background:var(--panel);
  border-bottom:1px solid var(--line);flex-wrap:wrap}
h1{font-size:15px;margin:0 6px 0 0;font-weight:600}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:7px;overflow:hidden}
.seg button{border:0;background:transparent;color:var(--fg);padding:4px 11px;font:inherit;cursor:pointer}
.seg button+button{border-left:1px solid var(--line)}
.seg button[aria-pressed=true]{background:var(--fg);color:var(--bg)}
.btn{border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:7px;padding:4px 11px;
  font:inherit;cursor:pointer}
.btn:hover,.seg button:hover{outline:1px solid var(--muted);outline-offset:-1px}
select{font:inherit;color:var(--fg);background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:3px 6px}
.muted{color:var(--muted)}
.status{margin-left:auto;color:var(--muted);font-size:12px;white-space:nowrap}
.status.bad{color:#d43c3c;font-weight:600}
main{position:relative;flex:1;min-height:0}
#viewport{position:absolute;inset:0;overflow:hidden;background:var(--board);touch-action:none;cursor:grab;
  user-select:none;-webkit-user-select:none}
#viewport.dragging{cursor:grabbing}
#world{position:absolute;left:0;top:0;transform-origin:0 0;--k:1}
.label{position:absolute;display:flex;align-items:flex-end;font-weight:600;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;font-size:max(26px,calc(13px / var(--k)));line-height:1.15;padding-bottom:10px}
.label span{overflow:hidden;text-overflow:ellipsis}
.card{position:absolute;background:var(--panel);border-radius:12px;box-shadow:var(--shadow);cursor:pointer}
.card img{display:block;width:100%;height:100%;object-fit:contain;border-radius:12px;-webkit-user-drag:none;pointer-events:none}
.card .ring{position:absolute;inset:-8px;border:6px solid var(--accent);border-radius:18px;opacity:0;pointer-events:none}
.card .tag,.card .badge{position:absolute;top:10px;border-radius:999px;padding:1px 9px;
  font-size:max(15px,min(calc(10px / var(--k)),40px));font-weight:600;pointer-events:none;white-space:nowrap}
#world.small .tag{display:none}
.card .tag{left:10px;background:rgba(127,127,127,.25);color:var(--fg);backdrop-filter:blur(4px)}
.card .badge{right:10px;background:var(--accent);color:var(--accent-fg)}
.card .badge:empty{display:none}
.card.missing{display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:24px;
  border:2px dashed var(--line);background:transparent;box-shadow:none}
.empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;
  color:var(--muted);padding:24px}
#focus{position:absolute;inset:0;background:var(--bg);display:flex;flex-direction:column}
#focus[hidden]{display:none}
.fbar{display:flex;gap:10px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.fbar strong{font-size:15px}
.hint{font-size:12px;margin-left:auto}
#stage{flex:1;min-height:0;display:flex;gap:18px;padding:12px 16px 16px;justify-content:center;touch-action:pan-y}
figure{margin:0;flex:1 1 0;min-width:0;max-width:640px;display:flex;flex-direction:column;gap:6px}
figcaption{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:center}
figcaption b{color:var(--fg)}
figcaption .chg{color:var(--accent);font-weight:600}
.shot{position:relative;flex:1;min-height:0;display:flex;justify-content:center}
.shot img{height:100%;max-width:100%;object-fit:contain;border-radius:12px;box-shadow:var(--shadow);background:var(--panel);
  -webkit-user-drag:none;user-select:none}
.shot.prev img{outline:3px dashed var(--muted);outline-offset:3px}
.shot .none{align-self:center;color:var(--muted)}
</style>
</head>
<body>
<header>
  <h1>Android screens</h1>
  <div class="seg" id="modes" role="group" aria-label="Themes shown">
    <button data-mode="light">Light</button><button data-mode="dark">Dark</button><button data-mode="both">Both</button>
  </div>
  <button class="btn" id="fit" title="Fit all (F)">Fit all</button>
  <span class="status" id="status">connecting…</span>
</header>
<main>
  <div id="viewport"><div id="world"></div></div>
  <section id="focus" hidden>
    <div class="fbar">
      <button class="btn" id="back" title="Back to the board (Esc)">← Board</button>
      <button class="btn" id="prevScene" title="Previous scene (←)">‹</button>
      <button class="btn" id="nextScene" title="Next scene (→)">›</button>
      <strong id="fname"></strong><span class="muted" id="fpos"></span>
      <div class="seg" id="cmp" role="group" aria-label="Before and after">
        <button data-cmp="current">Current</button><button data-cmp="previous">Previous</button><button data-cmp="split">Side by side</button>
      </div>
      <label class="muted">against <select id="backN"></select></label>
      <span class="hint muted">← → scenes · hold Space to flip · C compare · Esc board</span>
    </div>
    <div id="stage"></div>
  </section>
</main>
<script>
"use strict";
const $ = s => document.querySelector(s);
const vp = $("#viewport"), world = $("#world"), focusEl = $("#focus"), stage = $("#stage");
const prefs = {
  get(k, d) { try { const v = localStorage.getItem("screens." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("screens." + k, JSON.stringify(v)); } catch (e) {} },
};
const W = 360, H = Math.round(W * 2673 / 1233), GX = 48, GY = 30, LABEL = 70, BANDGAP = 90, PAD = 28;
const FRESH = 120;  // seconds a changed screen stays marked

let mode = prefs.get("mode", "both");
let compare = prefs.get("compare", "split");
let backN = 1;
let screens = new Map();     // "theme/name" -> listing entry
let version = null, loaded = false, dir = "";
const newAt = new Map();     // keys first seen after the first load
const everSeen = new Set();
let scenes = [];
let layoutKey = "", worldW = 0, worldH = 0;
const cards = new Map();
let view = { x: 0, y: 0, k: 1 }, userMoved = false;
let focusName = null, flip = false;

const themesShown = () => mode === "both" ? ["light", "dark"] : [mode];
const now = () => Date.now() / 1000;
const imgURL = s => `/img/${s.theme}/${encodeURIComponent(s.name)}.png?v=${s.sha}`;
const histURL = (s, h) => `/hist/${s.theme}/${encodeURIComponent(s.name)}/${h.id}.png`;
const earlier = s => s.history.filter(h => h.sha !== s.sha);
const clock = t => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
function ago(t) {
  const a = Math.max(0, Math.round(now() - t));
  return a < 60 ? `${a}s ago` : a < 3600 ? `${Math.floor(a / 60)}m ago` : `${Math.floor(a / 3600)}h ago`;
}
function freshness(key, s) {
  const t = Math.max(s && s.changed_at || 0, newAt.get(key) || 0);
  const a = now() - t;
  return a < FRESH ? { level: 1 - a / FRESH, text: newAt.get(key) === t ? "new" : ago(t) } : null;
}
function setSrc(img, url) {
  if (img.dataset.url === url) return;
  img.dataset.url = url;
  if (!img.getAttribute("src")) { img.src = url; return; }
  const pre = new Image();  // swap once loaded, so there is no blank flash
  pre.onload = () => { if (img.dataset.url === url) img.src = url; };
  pre.src = url;
}

// ---- polling ----
let lastChange = null;
async function poll() {
  const st = $("#status");
  try {
    const r = await fetch("/api/screens", { cache: "no-store" });
    const j = await r.json();
    if (j.version !== version) { version = j.version; lastChange = now(); apply(j); }
    st.className = "status";
    st.textContent = `${screens.size} shots · updated ${lastChange ? clock(lastChange) : "–"}`;
  } catch (e) {
    st.className = "status bad";
    st.textContent = "viewer server not reachable";
  }
  setTimeout(poll, 1000);
}
function apply(j) {
  dir = j.dir;
  const next = new Map();
  for (const s of j.screens) {
    const key = s.theme + "/" + s.name;
    next.set(key, s);
    if (loaded && !everSeen.has(key)) newAt.set(key, now());
    everSeen.add(key);
  }
  screens = next;
  loaded = true;
  render();
  if (focusName == null && location.hash.length > 1) openFocus(decodeURIComponent(location.hash.slice(1)));
  if (focusName != null) renderFocus();
}

// ---- board ----
function chooseCols(n, rows) {
  const vw = vp.clientWidth || 1200, vh = vp.clientHeight || 800;
  const bandH = LABEL + rows * H + (rows - 1) * GY;
  let best = 1, bestK = 0;
  for (let c = 1; c <= n; c++) {
    const b = Math.ceil(n / c);
    const k = Math.min(vw / (c * W + (c - 1) * GX + 2 * PAD), vh / (b * bandH + (b - 1) * BANDGAP + 2 * PAD));
    if (k > bestK * 1.001) { bestK = k; best = c; }
  }
  return best;
}
function render() {
  const themes = themesShown();
  scenes = [...new Set([...screens.values()].filter(s => themes.includes(s.theme)).map(s => s.name))].sort();
  for (const b of document.querySelectorAll("#modes button")) b.setAttribute("aria-pressed", b.dataset.mode === mode);
  const cols = chooseCols(Math.max(1, scenes.length), themes.length);
  const key = [cols, themes.join(","), scenes.join("|")].join(";");
  if (key !== layoutKey) { layoutKey = key; build(cols, themes); }
  for (const [k, c] of cards) updateCard(k, c);
}
function build(cols, themes) {
  world.textContent = "";
  cards.clear();
  const old = vp.querySelector(".empty");
  if (old) old.remove();
  if (!scenes.length) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = `No screenshots yet in ${dir}. They appear here as soon as the screenshots task writes them.`;
    vp.appendChild(e);
    worldW = worldH = 0;
    return;
  }
  const bandH = LABEL + themes.length * H + (themes.length - 1) * GY;
  scenes.forEach((name, i) => {
    const x = (i % cols) * (W + GX), y0 = Math.floor(i / cols) * (bandH + BANDGAP);
    const lab = document.createElement("div");
    lab.className = "label";
    Object.assign(lab.style, { left: x + "px", top: y0 + "px", width: W + "px", height: LABEL + "px" });
    lab.innerHTML = "<span></span>";
    lab.firstChild.textContent = name;
    lab.title = name;
    world.appendChild(lab);
    themes.forEach((t, r) => {
      const el = document.createElement("div");
      el.className = "card";
      el.dataset.scene = name;
      Object.assign(el.style, { left: x + "px", top: (y0 + LABEL + r * (H + GY)) + "px", width: W + "px", height: H + "px" });
      el.innerHTML = '<img alt=""><div class="ring"></div><span class="tag"></span><span class="badge"></span>';
      el.querySelector(".tag").textContent = t;
      world.appendChild(el);
      cards.set(t + "/" + name, el);
    });
  });
  const n = Math.min(cols, scenes.length), bands = Math.ceil(scenes.length / cols);
  worldW = n * W + (n - 1) * GX;
  worldH = bands * bandH + (bands - 1) * BANDGAP;
  if (!userMoved) fit();
}
function updateCard(key, el) {
  const s = screens.get(key);
  el.classList.toggle("missing", !s);
  const img = el.querySelector("img");
  if (!s) { img.hidden = true; el.title = key + " (not rendered)"; return; }
  img.hidden = false;
  el.title = `${key} · rendered ${clock(s.mtime)}`;
  setSrc(img, imgURL(s));
  markFresh(el, key, s);
}
function markFresh(el, key, s) {
  const f = freshness(key, s);
  el.querySelector(".ring").style.opacity = f ? (0.25 + 0.75 * f.level).toFixed(3) : 0;
  el.querySelector(".badge").textContent = f ? f.text : "";
}
setInterval(() => {
  for (const [k, el] of cards) if (screens.has(k)) markFresh(el, k, screens.get(k));
  if (focusName != null) captions();
}, 1000);

// ---- pan & zoom ----
function applyView() {
  world.style.transform = `translate(${view.x}px,${view.y}px) scale(${view.k})`;
  world.style.setProperty("--k", view.k);
  world.classList.toggle("small", view.k < 0.45);
}
function fit() {
  if (!worldW) return;
  const vw = vp.clientWidth, vh = vp.clientHeight;
  const k = Math.min((vw - 2 * PAD) / worldW, (vh - 2 * PAD) / worldH);
  view = { k, x: (vw - worldW * k) / 2, y: (vh - worldH * k) / 2 };
  applyView();
}
function zoomAt(px, py, f) {
  const k = Math.min(8, Math.max(0.03, view.k * f));
  view.x = px - (px - view.x) * (k / view.k);
  view.y = py - (py - view.y) * (k / view.k);
  view.k = k;
  userMoved = true;
  applyView();
}
const local = e => { const r = vp.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
let lastGesture = 0;
vp.addEventListener("wheel", e => {
  e.preventDefault();
  if (e.ctrlKey && Date.now() - lastGesture < 200) return;
  const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? vp.clientHeight : 1;
  if (e.shiftKey) {  // shift+wheel pans sideways
    view.x -= (e.deltaX || e.deltaY) * unit; userMoved = true; applyView(); return;
  }
  const [x, y] = local(e);
  zoomAt(x, y, Math.exp(-e.deltaY * unit * (e.ctrlKey ? 0.01 : 0.002)));
}, { passive: false });
let gScale = 1;  // Safari trackpad pinch
vp.addEventListener("gesturestart", e => { e.preventDefault(); gScale = 1; });
vp.addEventListener("gesturechange", e => {
  e.preventDefault(); lastGesture = Date.now();
  const [x, y] = local(e); zoomAt(x, y, e.scale / gScale); gScale = e.scale;
});
document.addEventListener("gesturestart", e => e.preventDefault());

const ptrs = new Map();
let press = null;
function pair() {
  const [a, b] = [...ptrs.values()];
  return { mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2, d: Math.hypot(a.x - b.x, a.y - b.y) || 1 };
}
let pinch = null;
vp.addEventListener("pointerdown", e => {
  if (e.pointerType === "mouse" && e.button !== 0) return;
  vp.setPointerCapture(e.pointerId);
  const [x, y] = local(e);
  ptrs.set(e.pointerId, { x, y });
  if (ptrs.size === 1) {
    const card = e.target.closest(".card");
    press = { x, y, moved: false, scene: card ? card.dataset.scene : null };
  } else {
    if (press) press.moved = true;
    pinch = pair();
  }
});
vp.addEventListener("pointermove", e => {
  const p = ptrs.get(e.pointerId);
  if (!p) return;
  const [x, y] = local(e);
  if (ptrs.size === 1) {
    view.x += x - p.x; view.y += y - p.y;
    p.x = x; p.y = y;
    if (press && Math.hypot(x - press.x, y - press.y) > 5) { press.moved = true; vp.classList.add("dragging"); }
    if (press && press.moved) { userMoved = true; applyView(); }
  } else {
    p.x = x; p.y = y;
    const q = pair();
    view.x += q.mx - pinch.mx; view.y += q.my - pinch.my;
    zoomAt(q.mx, q.my, q.d / pinch.d);
    pinch = q;
  }
});
function release(e) {
  if (!ptrs.delete(e.pointerId)) return;
  if (ptrs.size === 1) pinch = null;
  if (ptrs.size === 0) {
    vp.classList.remove("dragging");
    if (e.type === "pointerup" && press && !press.moved && press.scene) openFocus(press.scene);
    press = null;
  }
}
vp.addEventListener("pointerup", release);
vp.addEventListener("pointercancel", release);
let resizeT;
window.addEventListener("resize", () => {
  clearTimeout(resizeT);
  resizeT = setTimeout(() => { if (!userMoved) { layoutKey = ""; render(); } }, 150);
});

// ---- focus ----
function openFocus(name) {
  focusName = name;
  flip = false;
  focusEl.hidden = false;
  $("#fit").hidden = true;
  history.replaceState(null, "", "#" + encodeURIComponent(name));
  renderFocus();
}
function closeFocus() {
  focusName = null;
  focusEl.hidden = true;
  $("#fit").hidden = false;
  stage.textContent = "";
  history.replaceState(null, "", location.pathname);
}
function step(d) {
  if (!scenes.length) return;
  let i = scenes.indexOf(focusName);
  if (i < 0) i = d > 0 ? scenes.findIndex(n => n > focusName) - 1 : scenes.findIndex(n => n > focusName);
  i = ((i + d) % scenes.length + scenes.length) % scenes.length;
  openFocus(scenes[i]);
}
function panels() {
  const out = [];
  let shown = compare;
  if (flip && compare !== "split") shown = compare === "current" ? "previous" : "current";
  for (const t of themesShown()) {
    const key = t + "/" + focusName, s = screens.get(key);
    const kinds = shown === "split" ? ["previous", "current"] : [shown];
    for (const kind of kinds) out.push({ key, theme: t, kind, s });
  }
  return out;
}
function renderFocus() {
  const i = scenes.indexOf(focusName);
  $("#fname").textContent = focusName;
  $("#fpos").textContent = i < 0 ? "(gone)" : `${i + 1} / ${scenes.length}`;
  for (const b of document.querySelectorAll("#cmp button")) b.setAttribute("aria-pressed", b.dataset.cmp === compare);
  let depth = 0;
  for (const t of themesShown()) { const s = screens.get(t + "/" + focusName); if (s) depth = Math.max(depth, earlier(s).length); }
  const sel = $("#backN");
  const opts = depth ? Array.from({ length: depth }, (_, n) => n + 1) : [1];
  if (sel.dataset.opts !== opts.join()) {
    sel.dataset.opts = opts.join();
    sel.innerHTML = opts.map(n => `<option value="${n}">${n === 1 ? "previous render" : n + " renders back"}</option>`).join("");
  }
  if (backN > opts.length) backN = opts.length;
  sel.value = backN;
  sel.disabled = !depth;

  const want = panels();
  const sig = want.map(p => p.key + ":" + p.kind).join("|");
  if (stage.dataset.sig !== sig) {
    stage.dataset.sig = sig;
    stage.textContent = "";
    for (const p of want) {
      const fig = document.createElement("figure");
      fig.innerHTML = '<figcaption></figcaption><div class="shot"><img alt=""><span class="none"></span></div>';
      stage.appendChild(fig);
    }
  }
  want.forEach((p, n) => {
    const fig = stage.children[n], img = fig.querySelector("img"), none = fig.querySelector(".none");
    fig.querySelector(".shot").classList.toggle("prev", p.kind === "previous");
    let url = null, msg = "";
    if (!p.s) msg = "not rendered";
    else if (p.kind === "current") url = imgURL(p.s);
    else { const h = earlier(p.s)[backN - 1]; if (h) url = histURL(p.s, h); else msg = "no earlier render kept"; }
    img.hidden = !url; none.textContent = msg;
    if (url) setSrc(img, url);
  });
  captions();
}
function captions() {
  panels().forEach((p, n) => {
    const fig = stage.children[n];
    if (!fig) return;
    const cap = fig.querySelector("figcaption"), s = p.s;
    let html = `<b>${p.theme}</b> · ${p.kind}`;
    if (s && p.kind === "current") {
      html += ` · rendered ${clock(s.mtime)}`;
      const f = freshness(p.key, s);
      if (f) html += ` · <span class="chg">${f.text === "new" ? "new" : "changed " + f.text}</span>`;
    } else if (s) {
      const list = earlier(s), h = list[backN - 1];
      if (h) {
        const replaced = backN === 1 ? s.changed_at : list[backN - 2].time;
        html += ` · replaced ${clock(replaced)}` + (backN > 1 ? ` (${backN} back)` : "");
      }
    }
    cap.innerHTML = html;
  });
}
let swipe = null;
stage.addEventListener("pointerdown", e => { swipe = { x: e.clientX, y: e.clientY }; });
stage.addEventListener("pointerup", e => {
  if (!swipe) return;
  const dx = e.clientX - swipe.x, dy = e.clientY - swipe.y;
  swipe = null;
  if (Math.abs(dx) > 60 && Math.abs(dx) > 1.5 * Math.abs(dy)) step(dx < 0 ? 1 : -1);
});

// ---- controls ----
$("#modes").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  mode = b.dataset.mode; prefs.set("mode", mode); userMoved = false; render();
  if (focusName != null) renderFocus();
});
$("#cmp").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  compare = b.dataset.cmp; prefs.set("compare", compare); renderFocus();
});
$("#backN").addEventListener("change", e => { backN = +e.target.value; renderFocus(); });
$("#fit").addEventListener("click", () => { userMoved = false; layoutKey = ""; render(); fit(); });
$("#back").addEventListener("click", closeFocus);
$("#prevScene").addEventListener("click", () => step(-1));
$("#nextScene").addEventListener("click", () => step(1));
document.addEventListener("keydown", e => {
  if (e.metaKey || e.ctrlKey || e.altKey || e.target.tagName === "SELECT") return;
  if (focusName != null) {
    if (e.key === "Escape") closeFocus();
    else if (e.key === "ArrowRight" || e.key === "ArrowDown") step(1);
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") step(-1);
    else if (e.key === " ") { if (!e.repeat) { flip = true; renderFocus(); } }
    else if (e.key === "c") { const o = ["current", "previous", "split"]; compare = o[(o.indexOf(compare) + 1) % 3]; prefs.set("compare", compare); renderFocus(); }
    else return;
    e.preventDefault();
  } else {
    if (e.key === "f" || e.key === "0") { userMoved = false; layoutKey = ""; render(); fit(); }
    else if (e.key === "=" || e.key === "+") zoomAt(vp.clientWidth / 2, vp.clientHeight / 2, 1.25);
    else if (e.key === "-") zoomAt(vp.clientWidth / 2, vp.clientHeight / 2, 0.8);
    else return;
    e.preventDefault();
  }
});
document.addEventListener("keyup", e => { if (e.key === " " && flip) { flip = false; renderFocus(); } });
window.addEventListener("blur", () => { if (flip) { flip = false; renderFocus(); } });
for (const b of document.querySelectorAll("#modes button")) b.setAttribute("aria-pressed", b.dataset.mode === mode);
poll();
</script>
</body>
</html>
"""


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    STORE.scan()
    time.sleep(SCAN_EVERY)
    STORE.scan()
    threading.Thread(target=scanner, daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    print("Screens viewer on http://127.0.0.1:%d/ watching %s" % (port, SHOTS), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
