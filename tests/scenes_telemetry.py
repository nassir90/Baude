"""scenes_telemetry — tests/screenshots.py's scenes of telemetry (baudelib.telemetry, telemetryview): Settings →
Telemetry, what this machine's host keeps: its turns' tokens by day, the models used, its machine and its processes
now, and line profiling."""
import datetime
import json
import os
import random
import time

import test_baude as T  # noqa: E402  (sets up the throwaway env: fake claude, temp config dirs)

from scenelib import H, SIDEBAR, opts, scene


def ask(msg):
    """A request to this machine's host: its answer."""
    from baudelib import wire
    ch, _info = wire.reach({"via": "local"}, timeout=10)
    try:
        ch.sock.settimeout(20)
        ch.send({**msg, "req": 1})
        return next(m for m in ch if m.get("req") == 1)
    finally:
        ch.close()


def seed(host_id):
    """A year of the host's turns (made up, the same every run), a few chats' activity today, and a profile."""
    from baudelib.serverstate import state_home
    rng, now = random.Random(7), time.time()
    folder = os.path.join(state_home(), "telemetry")
    if os.path.isdir(os.path.join(folder, "profiles")):
        return  # (seeded already, by a scene before in this run)
    rows = {}
    models = [("claude-opus-4-7", "high"), ("claude-opus-4-7", "xhigh"), ("claude-sonnet-4-6", "medium"),
              ("gpt-5.4-codex", "medium")]
    for back in range(370):
        if rng.random() < (0.35 if back > 120 else 0.75):
            day = datetime.datetime.now() - datetime.timedelta(days=back)
            for _ in range(rng.randint(1, 9)):
                model, effort = rng.choices(models, weights=(6, 2, 3, 1))[0]
                at = day.replace(hour=rng.randint(8, 22), minute=rng.randint(0, 59)).timestamp()
                if at > now:
                    continue
                rows.setdefault(("events", datetime.date.fromtimestamp(at)), []).append({
                    "at": at, "app": "host", "kind": "turn_ended", "host": host_id, "session": f"s{back}",
                    "seconds": rng.randint(8, 400), "turn": f"t{back}-{at}", "model": model, "effort": effort,
                    "tokens_out": rng.randint(500, 30_000),
                    **({"tokens_in": rng.randint(20_000, 900_000)} if back > 30 else {  # (kept before they were apart)
                        "tokens_input": rng.randint(200, 20_000), "tokens_cache_write": rng.randint(5_000, 80_000),
                        "tokens_cache_read": rng.randint(50_000, 4_000_000)})})
    chats = ["c1a7e4f0-0000-4000-8000-000000000001", "5be2d913-0000-4000-8000-000000000002"]
    for i, (kind, chat, more) in enumerate([("chat_opened", 0, {}), ("chat_front", 0, {}), ("turn_sent", 0, {}),
                                            ("turn_ended", 0, {"seconds": 131.4}), ("chat_left", 0, {}),
                                            ("chat_opened", 1, {}), ("chat_front", 1, {}), ("turn_sent", 1, {}),
                                            ("turn_ended", 1, {"seconds": 12.2})]):
        at = now - 900 + i * 60
        rows.setdefault(("events", datetime.date.fromtimestamp(at)), []).append(
            {"at": at, "app": "baude", "kind": kind, "host": host_id, "session": chats[chat], **more})
    lines = [("baudelib/telemetry.py", 271, "sample", "stats = proc_stats(sorted(pids))", 120, 0.912),
             ("baudelib/telemetry.py", 106, "proc_stats", "text = subprocess.run([\"ps\", \"-o\", \"pid=,time=,rss=\", \"-p\",",
              120, 0.774),
             ("baudelib/telemetry.py", 131, "_mac_memory", "if libc.host_statistics64(host, 4, ctypes.byref(vm), ctypes.byref(count)) == 0:",
              120, 0.402),
             ("baudelib/telemetry.py", 300, "_write", "f.write(\"\".join(json.dumps(r, separators=(\",\", \":\")) + \"\\n\" for r in these))",
              121, 0.051)]
    rows.setdefault(("profiles", datetime.date.today()), []).append({
        "at": now - 30, "from": now - 60, "app": "host",
        "lines": [{"file": f, "line": n, "function": fn, "code": c, "hits": h, "seconds": s} for f, n, fn, c, h, s in lines]})
    for app, these in (("baude", [("markdownview.py", 652, "markdown_parse", "blocks = markdown_blocks(md[start:])", 64,
                                   0.733),
                                  ("windowview.py", 761, "load_history", "items = self.history.load(self.HISTORY_CHUNK)",
                                   3, 0.288)]),
                       ("record", [("recordstore.py", 498, "take", "self.merged_page(mine, theirs, was)", 41, 0.164)])):
        rows[("profiles", datetime.date.today())].append({
            "at": now - 30, "from": now - 60, "app": app,
            "lines": [{"file": f, "line": n, "function": fn, "code": c, "hits": h, "seconds": s}
                      for f, n, fn, c, h, s in these]})
    for (kind, day), these in rows.items():
        os.makedirs(os.path.join(folder, kind), exist_ok=True)
        with open(os.path.join(folder, kind, f"{day.isoformat()}.jsonl"), "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in these))


def settings(app, profile=True, scrolled=False):
    win = app.window(opts())
    from baudelib import hosts
    assert hosts.start_here(), "this machine's host"
    host_id = ask({"op": "telemetry_read", "from": 0})["host"]
    seed(host_id)
    if profile:
        ask({"op": "telemetry_set", "on": True, "line_profiling": True})
    time.sleep(6)  # (a sample of the machine taken)
    dialog = win.open_settings()
    import telemetryview
    page = next(p for p in T.find(dialog, telemetryview.Page))
    dialog.set_visible_page(page)
    T.wait_for(lambda: {"tokens", "machine", "processes", "profiling"} <= set(page.groups), "the host's telemetry", 30)
    T.pump(0.5)
    if scrolled:  # (its foot: the machine, the processes, line profiling)
        from gi.repository import Gtk
        sw = T.find(page, Gtk.ScrolledWindow)[0]
        adj = sw.get_vadjustment()
        for _ in range(3):  # (again as what came last is laid out)
            T.pump(0.5)
            adj.set_value(adj.get_upper())
        T.pump(0.5)
    return win


@scene("39g-telemetry", "Settings → Telemetry: the host picked beside New session (this machine's here), Collect "
                        "Telemetry, its tokens by day over a year, the most used model and effort, each model's turns, "
                        "its machine and its processes now, and line profiling with its top lines",
       size=(1200 + SIDEBAR, H + 100))
def _(app):
    return settings(app)


@scene("39g-telemetry-foot", "Settings → Telemetry scrolled to its foot: its machine and its processes now, "
                             "line profiling with its top lines", size=(1200 + SIDEBAR, H + 100))
def _(app):
    return settings(app, scrolled=True)


@scene("39h-telemetry-no-profiler", "Settings → Telemetry on a host without line_profiler (BAUDE_HIDE_DEPS="
                                    "line_profiler): Line Profiling says what's missing and how it's installed",
       size=(1200 + SIDEBAR, H + 100))
def _(app):
    return settings(app, profile=False, scrolled=True)
