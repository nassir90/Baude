#!/usr/bin/env python3
"""The parity rig's fixture host, for the ops the phone asks of a host that Baude answers with its own functions
(baudelib/hostfixes.py): a request (JSON) on stdin, the reply (JSON) on stdout. It runs in a throwaway home, against
tests/fake_gh.py's fake GitHub, never the real gh. No GTK here. Run with -I: the repo's root is put on the path here."""
import json
import os
import shutil
import sys
import tempfile
import types

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


def kube(msg):
    """kube: tests/fake_kubectl.py's clusters (k3d-dev, prod, crowded), read as hostkube.py reads a real one."""
    import kube_fixture
    from baudelib import hostkube
    os.environ.update(KUBECONFIG=kube_fixture.make_kubeconfig(os.path.join(tempfile.mkdtemp(dir="/tmp"), "kubeconfig"),
                                                              crowded=True),
                      BAUDE_KUBECTL=os.path.join(TESTS, "fake_kubectl.py"), BAUDE_HELM=os.path.join(TESTS, "fake_kubectl.py"))
    for k in ("FAKE_KUBECTL_PG", "FAKE_KUBECTL_FOLLOW", "FAKE_KUBECTL_STATE", "BAUDE_KUBE_FAULT"):
        os.environ.pop(k, None)
    if msg.get("what") == "summary":  # (a process a request: Home's history as the desktop scene's)
        kube_fixture.seed_home_history(msg.get("context") or "k3d-dev")
    return hostkube.answer(None, msg)


def databases(msg):
    """databases: fixtures/databases.json's server, read as hostdb.py reads a real one."""
    import lookupkube
    import schemadata
    from baudelib import hostdb
    with open(os.path.join(HERE, "fixtures", "databases.json")) as f:
        fixture = json.load(f)
    server = {**fixture["server"], "engine": "postgres"}
    lookupkube.pg_servers_cached = lambda max_age=30: [server]
    lookupkube.pg_databases_cached = lambda s, max_age=30: [fixture["database"]]
    schemadata.pg_schema = lambda s, database, max_age=60: schemadata.pg_normalize(fixture["raw"])
    hostdb.time = types.SimpleNamespace(monotonic=lambda: 0.0)  # (no time taken: as the desktop scene's)
    return hostdb.answer(None, msg)


def main():
    msg = json.load(sys.stdin)
    tmp = tempfile.mkdtemp(prefix="parity-host-", dir="/tmp")
    try:
        if msg.get("op") == "fixes":
            fixes_env(tmp)
            from baudelib import hostfixes
            reply = hostfixes.answer(None, msg)
        elif msg.get("op") == "kube":
            reply = kube(msg)
        elif msg.get("op") == "databases":
            reply = databases(msg)
        else:
            reply = {"error": f"the fixture host doesn't take {msg.get('op')!r}"}
        print(json.dumps({"op": msg["op"], "req": msg.get("req"), **reply}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
