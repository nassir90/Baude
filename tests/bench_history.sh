#!/usr/bin/env bash
# Benchmark loading a saved session into baude, headless (see bench_history.py).
set -euo pipefail
cd "$(dirname "$0")/.."
exec env -u WAYLAND_DISPLAY GDK_BACKEND=x11 GSK_RENDERER=cairo \
    xvfb-run --auto-servernum --server-args="-screen 0 1280x900x24" \
    python3 tests/bench_history.py "$@" 2>&1 | grep -v "DRI3"
