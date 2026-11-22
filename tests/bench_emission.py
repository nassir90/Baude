#!/usr/bin/env python3
"""Profile Emission's memory as a Bot's tools drive it, phase by phase (nothing real is touched: a Bot's Emission of
the run's own, out of sight, its profile and sockets in throwaway folders, its pages served from this process).

    python3 tests/bench_emission.py                # 30 rounds of each phase
    python3 tests/bench_emission.py --rounds 100   # a leak shows as a line that grows with the rounds
    python3 tests/bench_emission.py --hang         # + a script that never settles (its request is never answered)

Each phase does one kind of work `--rounds` times, then prints what's left: Emission's own memory (macOS: its physical
footprint; Linux: RSS + swap), WebKit's processes' (its pages'), and what Emission says it holds (its "memory"
request: Python objects, the widgets, tabs and page engines alive among them, blocks waiting to be called, threads).
What a phase leaves should be what the one before left: a tab closed, a picture taken, a click made keep nothing.

  started     one blank tab
  tabs        a tab opened on a page, then closed
  navigate    one tab taken from page to page
  look        a screenshot, a zoom, read_page, find, get_text
  input       a click, a hover, typing, a key, a scroll, a drag, form_input
  javascript  scripts run in the page
  again       all of it once more: what a second pass adds is what grows
  idle        ten seconds with nothing asked
"""
import argparse
import ctypes
import ctypes.util
import http.server
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = os.path.realpath(tempfile.mkdtemp(prefix="emission-bench-"))
for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):  # (never the real profiles, nor Baude's settings)
    os.environ[name] = os.path.join(TMP, name.lower())
os.environ["BAUDE_LAUNCHERS"] = os.path.join(TMP, "applications")  # (nor ~/Applications)
os.environ["BAUDE_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="eb-", dir="/tmp")  # (nor the real sockets; short: a socket's)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from baudelib import emissionlink  # noqa: E402

BOT = "bench-emission"


class _RUsage(ctypes.Structure):  # (macOS: the start of rusage_info_v2, up to ri_phys_footprint; the rest left room)
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + \
               [(n, ctypes.c_uint64) for n in ("user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups", "pageins",
                                               "wired_size", "resident_size", "phys_footprint")] + \
               [("rest", ctypes.c_uint64 * 64)]


def process_memory(pid):
    """{"footprint", "rss"} in MB of a process: macOS, its physical footprint (what Activity Monitor shows); Linux,
    RSS + swap."""
    if sys.platform == "darwin":
        info = _RUsage()
        libproc = ctypes.CDLL(ctypes.util.find_library("proc"))
        ok = libproc.proc_pid_rusage(pid, 2, ctypes.byref(info)) == 0  # (2: RUSAGE_INFO_V2)
        return {"footprint": info.phys_footprint / 2**20 if ok else 0.0, "rss": info.resident_size / 2**20 if ok else 0.0}
    got = {}
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith(("VmRSS:", "VmSwap:")):
                    got[line.split(":")[0]] = int(line.split()[1]) / 1024
    except OSError:  # (gone meanwhile)
        pass
    return {"footprint": got.get("VmRSS", 0) + got.get("VmSwap", 0), "rss": got.get("VmRSS", 0)}


def png(w, h, seed):
    """A w x h picture of noise (it doesn't compress: its pixels are held as they'd be for a photograph)."""
    rows = b"".join(b"\0" + bytes((seed * 31 + y * 7 + x * 13 + (x * y) % 251) % 256 for x in range(w * 3))
                    for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b"")


def page(n):
    paras = "".join(f"<p id='p{i}'>Paragraph {i} of page {n}: the harbour's berths, sounded in spring, and "
                    f"<a href='/page/{(n + i) % 7}'>another page</a>.</p>" for i in range(300))
    return (f"<!doctype html><title>Page {n}</title><body style='font: 15px sans-serif'><h1>Page {n}</h1>"
            f"<form onsubmit='return false'><input id='name' placeholder='Name'> <select id='pick'><option>one"
            f"<option>two</select> <button id='go' onclick=\"document.title='Pressed {n}'\">Press</button></form>"
            f"<img src='/picture/{n % 3}.png' width=640 height=400>{paras}"
            "<script>console.log('loaded', document.title)</script></body>").encode()


class Pages(http.server.BaseHTTPRequestHandler):
    pictures = {}

    def do_GET(self):
        if self.path.startswith("/picture/"):
            n = int(self.path.split("/")[2].split(".")[0])
            body = self.pictures.setdefault(n, png(640, 400, n))
            kind = "image/png"
        else:
            body, kind = page(int(self.path.rsplit("/", 1)[1] or 0) if self.path.startswith("/page/") else 0), "text/html"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def forget_store(pid):
    """(macOS) The bench's Bot's profile's store taken away: WebKit keeps a profile's cookies and cache under
    ~/Library/WebKit/<the program>/WebsiteDataStore/<its id>, whatever folders this run was given. Its folder's
    removed once its Emission's gone (WebKit's own removal needs a WebKit run loop, which this process hasn't)."""
    import glob
    for path in glob.glob(os.path.expanduser(f"~/Library/WebKit/*/WebsiteDataStore/{pid.lower()}")):
        shutil.rmtree(path, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=30, help="how many times each phase does its work")
    ap.add_argument("--hang", action="store_true", help="also a script that never settles, before the last phases")
    ap.add_argument("--json", help="also write the phases' numbers here")
    args = ap.parse_args()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    rows = []

    def ask(op, timeout=60, **more):
        return emissionlink.call_bot(BOT, "Bench", {"op": op, **more}, timeout)

    def measure(name):
        time.sleep(1.5)  # (what it's just done let go of: idles run, pages settled)
        m = ask("memory")
        own = process_memory(m["pid"])
        web = sum(process_memory(p)["footprint"] for p in m["web_pids"])
        row = {"phase": name, "footprint": own["footprint"], "web": web, "web_processes": len(m["web_pids"]),
               **{k: m[k] for k in ("objects", "widgets", "tab_objects", "engines", "tabs", "views", "blocks",
                                    "queue", "pointer_steps", "threads")}}
        prev = rows[-1] if rows else None
        rows.append(row)
        delta = f"{row['footprint'] - prev['footprint']:+6.0f}" if prev else "      "
        print(f"{name:22} {row['footprint']:6.0f} MB {delta}   pages {web:6.0f} MB in {row['web_processes']}   "
              f"{row['objects']:>8,} objects  {row['widgets']:>5,} widgets  tabs {row['tabs']} "
              f"(alive {row['tab_objects']}, engines {row['engines']}, views {row['views']})  blocks {row['blocks']}  "
              f"queue {row['queue']}  steps {row['pointer_steps']}  threads {row['threads']}", flush=True)

    def tabs(n):
        for i in range(n):
            t = ask("new_tab", url=f"{base}/page/{i % 7}")["tab"]
            ask("close_tab", tab=t)

    def navigate(n):
        for i in range(n):
            ask("navigate", url=f"{base}/page/{i % 7}")

    def look(n):
        for i in range(n):
            ask("screenshot")
            ask("zoom", region=[0, 0, 400, 300])
            ask("read_page")
            ask("find", query="Press")
            ask("get_text")

    def put(n):
        for i in range(n):
            ref = ask("find", query="Name")["found"][0].split("[")[1].split("]")[0]  # (the page's first field)
            ask("click", x=400, y=300, css=True)
            ask("hover", x=300 + i % 50, y=200, css=True)
            ask("type", ref=ref, text="berth")
            ask("key", keys="Tab")
            ask("scroll", dy=400)
            ask("scroll", dy=-400)
            ask("drag", path=[[100, 300], [200, 320], [300, 340]], css=True)
            ask("form_input", ref=ref, value=f"name {i}")

    def script(n):
        for i in range(n):
            ask("javascript", text=f"document.querySelectorAll('p').length + {i}")
            ask("javascript", text="await new Promise(r => setTimeout(r, 5)); [...document.images].map(i => i.src)")

    try:
        ask("tabs")
        ask("navigate", url=f"{base}/page/0")
        measure("started")
        for name, work in (("tabs", tabs), ("navigate", navigate), ("look", look), ("input", put),
                           ("javascript", script)):
            work(args.rounds)
            measure(f"{name} x{args.rounds}")
        if args.hang:
            try:
                ask("javascript", timeout=5, text="await new Promise(() => {})")
            except (OSError, ValueError) as e:  # (no answer in 5 s; or WebKitGTK's own word that none will come)
                print(f"   (a script that never settles: {type(e).__name__}: {e})", flush=True)
            measure("a script hung")
        for work in (tabs, navigate, look, put, script):
            work(args.rounds)
        measure(f"again x{args.rounds}")
        time.sleep(10)
        measure("idle 10 s")
    finally:
        try:
            emissionlink.call(emissionlink.bot_profile(BOT, None), {"op": "quit"}, 10)
        except (OSError, ValueError):
            pass
        time.sleep(1)
        srv.shutdown()
        if sys.platform == "darwin":
            forget_store(emissionlink.bot_profile_id(BOT))
        if args.json:
            with open(args.json, "w") as f:
                json.dump(rows, f, indent=1)
        shutil.rmtree(TMP, ignore_errors=True)
        shutil.rmtree(os.environ["BAUDE_RUNTIME_DIR"], ignore_errors=True)


if __name__ == "__main__":
    main()
