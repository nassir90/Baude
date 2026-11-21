#!/usr/bin/env python3
"""The parity rig's fixture host, for the ops the phone asks of a host that Baude answers with its own functions
(baudelib/hostfixes.py): a request (JSON) on stdin, the reply (JSON) on stdout. It runs in a throwaway home, against
tests/fake_gh.py's fake GitHub, never the real gh. No GTK here. Run with -I: the repo's root is put on the path here."""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS = os.path.join(ROOT, "tests")
sys.path[:0] = [ROOT, TESTS]


def fixes_env(tmp):
    """The fake gh, its state (tests/fixes_fixture.py's) and a home for what's kept, with the fix's images in place."""
    import fixes_fixture
    shim = os.path.join(tmp, "fake-gh")
    with open(shim, "w") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{os.path.join(TESTS, "fake_gh.py")}" "$@"\n')
    os.chmod(shim, 0o755)
    state = os.path.join(tmp, "fake-gh.json")
    with open(state, "w") as f:
        json.dump(fixes_fixture.state(), f)
    home = os.path.join(tmp, "data")
    os.environ.update(BAUDE_GH=shim, FAKE_GH_STATE=state, FAKE_GH_SIGNED_IN="1", XDG_DATA_HOME=home, HOME=tmp)
    png = os.path.join(HERE, "fixtures", "gallery", "docs", "flow.png")
    for name in ("1-before.png", "1-after.png"):
        kept = os.path.join(home, "baude", "fixes", "shots", "12", "1", name)
        os.makedirs(os.path.dirname(kept), exist_ok=True)
        shutil.copy(png, kept)


def main():
    msg = json.load(sys.stdin)
    tmp = tempfile.mkdtemp(prefix="parity-host-", dir="/tmp")
    try:
        if msg.get("op") == "fixes":
            fixes_env(tmp)
            from baudelib import hostfixes
            reply = hostfixes.answer(None, msg)
        else:
            reply = {"error": f"the fixture host doesn't take {msg.get('op')!r}"}
        print(json.dumps({"op": msg["op"], "req": msg.get("req"), **reply}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
