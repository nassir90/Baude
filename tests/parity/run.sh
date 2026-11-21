#!/usr/bin/env bash
# The parity rig, start to end: every scene of tests/parity/scenes.json drawn by Baude (desktop.py) and by the phone
# (android's Parity.kt) from tests/parity/fixtures, then laid side by side and compared (compare.py all).
# Output: tests/parity/out/{desktop,phone,pairs,diffs}. Args pass to desktop.py (-k NAME, --theme).
set -euo pipefail
cd "$(dirname "$0")/../.."
rm -rf tests/parity/out/desktop tests/parity/out/phone
python3 tests/parity/desktop.py "$@" &
desk=$!
(cd android && JAVA_HOME="${JAVA_HOME:-$HOME/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home}" \
    ./gradlew --offline -q testDebugUnitTest --tests '*Parity*' > ../tests/parity/out-gradle.log 2>&1) || \
    echo "the phone's rig failed: tests/parity/out-gradle.log" >&2
wait "$desk" || true
python3 tests/parity/compare.py all > /dev/null
python3 tests/parity/compare.py list
