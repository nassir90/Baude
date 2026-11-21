#!/usr/bin/env bash
# The parity rig, start to end: every scene of tests/parity/scenes.json drawn by Baude (desktop.py) and by the phone
# (android's Parity.kt) from tests/parity/fixtures, then laid side by side and compared (compare.py all).
# Output: tests/parity/out/{desktop,phone,pairs,diffs}. Args pass to desktop.py (-k NAME, --theme).
set -euo pipefail
cd "$(dirname "$0")/../.."
rm -rf tests/parity/out/desktop tests/parity/out/phone
if [ "$(uname)" = Linux ] && command -v xvfb-run >/dev/null; then  # (a virtual display, as tests/screenshots.sh)
    env -u WAYLAND_DISPLAY GDK_BACKEND=x11 GSK_RENDERER=cairo \
        xvfb-run --auto-servernum --server-args="-screen 0 1920x1000x24" python3 tests/parity/desktop.py "$@" &
else
    python3 tests/parity/desktop.py "$@" &
fi
desk=$!
mac_java="$HOME/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home"
[ -z "${JAVA_HOME:-}" ] && [ -d "$mac_java" ] && export JAVA_HOME="$mac_java"
(cd android && ./gradlew --offline -q testDebugUnitTest --tests '*Parity*' > ../tests/parity/out-gradle.log 2>&1) || \
    echo "the phone's rig failed: tests/parity/out-gradle.log" >&2
wait "$desk" || true
python3 tests/parity/compare.py all > /dev/null
python3 tests/parity/compare.py list
