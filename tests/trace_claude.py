#!/usr/bin/env python3
"""Stands in for claude: runs the real one and records every stream line, both ways, with its time.

    tests/ref_replay.py ... --sonnet 4 --claude tests/trace_claude.py     (TRACE_DIR=folder for the records)

Each claude process gets TRACE_DIR/claude-<pid>.jsonl: {"t": time, "d": "argv"|"in"|"out", "l": the line}.
TRACE_REAL_CLAUDE names the real claude (default: the one on PATH). tests/trace_report.py reads the records.
"""
import json, os, shutil, subprocess, sys, threading, time

real = os.environ.get("TRACE_REAL_CLAUDE") or next(
    (p for p in (os.path.join(d, "claude") for d in os.environ.get("PATH", "").split(os.pathsep))
     if os.access(p, os.X_OK) and os.path.realpath(p) != os.path.realpath(__file__)), None) or shutil.which("claude")
trace_dir = os.environ.get("TRACE_DIR") or os.path.join(os.getcwd(), "claude-trace")
os.makedirs(trace_dir, exist_ok=True)
out = open(os.path.join(trace_dir, f"claude-{os.getpid()}.jsonl"), "a")
lock = threading.Lock()


def log(d, line):
    with lock:
        out.write(json.dumps({"t": time.time(), "d": d, "l": line.rstrip("\n")}) + "\n")
        out.flush()


log("argv", json.dumps(sys.argv[1:]))
p = subprocess.Popen([real] + sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)


def pump_in():
    for line in sys.stdin:
        log("in", line)
        try:
            p.stdin.write(line)
            p.stdin.flush()
        except (BrokenPipeError, ValueError):
            break
    try:
        p.stdin.close()
    except OSError:
        pass


threading.Thread(target=pump_in, daemon=True).start()
for line in p.stdout:
    log("out", line)
    sys.stdout.write(line)
    sys.stdout.flush()
sys.exit(p.wait())
