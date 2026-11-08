#!/usr/bin/env python3
"""Time baude's start-up, headless in Xvfb, against a *copy* of your sessions and Claude Desktop's
index (nothing real is read for writing, or written), with the fake claude unless BAUDE_CLAUDE is set.

    python3 tests/bench_startup.py [runs]

Each way of starting it (as a script, and as the launcher does: an imported module) runs `runs`
times after one cold run (no saved session index); the medians are printed per milestone: wall time
since the process started, and the CPU time it had used by then (see trace() in baude.py). On a busy
machine the wall times swing a lot; the CPU times barely move."""
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MILESTONES = ["imported", "activate", "window built", "window mapped", "first frame", "claude ready",
              "sidebar shows the newest", "sidebar filled"]


def run(cmd, env):
    out = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1600x1000x24", *cmd], env=env, cwd=ROOT,
                         capture_output=True, text=True, timeout=90)
    return {m.group(3): (int(m.group(1)), int(m.group(2)))
            for m in re.finditer(r"baude-trace\s+(\d+) ms\s+(\d+) cpu  (.+)", out.stderr)}


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    tmp = tempfile.mkdtemp(prefix="baude-startup-")
    try:
        shutil.copytree(os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"),
                                     "projects"), os.path.join(tmp, "claude", "projects"))
        desktop = os.path.expanduser("~/.config/Claude/claude-code-sessions")
        if os.path.isdir(desktop):
            shutil.copytree(desktop, os.path.join(tmp, "config", "Claude", "claude-code-sessions"))
        env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}
        env.update(GDK_BACKEND="x11", GSK_RENDERER="cairo", BAUDE_TRACE="exit",
                   XDG_CONFIG_HOME=os.path.join(tmp, "config"), XDG_CACHE_HOME=os.path.join(tmp, "cache"),
                   XDG_DATA_HOME=os.path.join(tmp, "data"), CLAUDE_CONFIG_DIR=os.path.join(tmp, "claude"),
                   BAUDE_CLAUDE=os.environ.get("BAUDE_CLAUDE") or os.path.join(ROOT, "tests", "fake_claude.py"))
        script = [sys.executable, "baude.py", "--cwd", ROOT]
        sys.path.insert(0, ROOT)
        import baude  # noqa: E402  (also leaves the cached bytecode the module start uses)
        module = baude.launch_command("--cwd", ROOT)
        cold = run(module, env)
        print("cold start (no saved session index), as a module:")
        for m in MILESTONES:
            wall, cpu = cold.get(m, ("-", "-"))
            print(f"  {wall:>6} ms  {cpu:>6} ms cpu  {m}")
        results = {}
        for name, cmd in (("script", script), ("module", module)):
            results[name] = [run(cmd, env) for _ in range(runs)]
        print(f"\nwarm starts, median of {runs} (wall / cpu ms)   python3 baude.py     launcher (module)")
        for m in MILESTONES:
            cells = []
            for n in ("script", "module"):
                got = [r[m] for r in results[n] if m in r]
                cells.append(f"{statistics.median(w for w, _ in got):5.0f} / {statistics.median(c for _, c in got):4.0f}"
                             if got else "    -")
            print(f"  {m:20}                    {cells[0]:>13}       {cells[1]:>13}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
