#!/usr/bin/env bash
# Regenerate the screenshot matrix (every screen, light and dark) headless in Xvfb.
# Args pass through to screenshots.py (-k NAME, --out DIR). Output: screenshots/index.html
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v xvfb-run >/dev/null; then
    echo "xvfb-run not found (Debian/Ubuntu: sudo apt install xvfb)" >&2
    exit 1
fi

# X11 + cairo for the virtual display (no GPU); a screen wide enough for the widest scene.
exec env -u WAYLAND_DISPLAY GDK_BACKEND=x11 GSK_RENDERER=cairo \
    xvfb-run --auto-servernum --server-args="-screen 0 1920x1000x24" \
    python3 tests/screenshots.py "$@"
