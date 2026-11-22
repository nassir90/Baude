#!/usr/bin/env bash
# Run baude's UI tests, then Record's (record.py and lookup.py), headless in a throwaway Xvfb display.
# Args pass through to test_baude.py (-k NAME, --shots DIR, --real).
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v xvfb-run >/dev/null; then
    echo "xvfb-run not found (Debian/Ubuntu: sudo apt install xvfb)" >&2
    exit 1
fi

# Force X11 so GTK uses the virtual display rather than the real Wayland session,
# and the cairo renderer since Xvfb has no GPU.
exec env -u WAYLAND_DISPLAY GDK_BACKEND=x11 GSK_RENDERER=cairo \
    xvfb-run --auto-servernum --server-args="-screen 0 1280x900x24" \
    sh -c 'python3 tests/test_baude.py "$@"; baude=$?; python3 tests/test_record.py; exit $((baude || $?))' sh "$@"
