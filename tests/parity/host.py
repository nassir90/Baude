#!/usr/bin/env python3
"""The parity rig's fixture host, for the phone's scenes: one request (a JSON line on stdin) answered as a host would, from
the fixtures, printed as the reply. kube is tests/fake_kubectl.py's k3d-dev, and databases is fixtures/databases.json's
server, read as hostkube.py and hostdb.py read a real one. No GTK; run with -I (it puts the repo's root on the path itself)."""
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))


def kube(msg):
    import kube_fixture
    from baudelib import hostkube
    tests = os.path.join(ROOT, "tests")
    os.environ.update(KUBECONFIG=kube_fixture.make_kubeconfig(os.path.join(tempfile.mkdtemp(dir="/tmp"), "kubeconfig")),
                      BAUDE_KUBECTL=os.path.join(tests, "fake_kubectl.py"),
                      BAUDE_HELM=os.path.join(tests, "fake_kubectl.py"))
    for k in ("FAKE_KUBECTL_PG", "FAKE_KUBECTL_FOLLOW", "FAKE_KUBECTL_STATE", "BAUDE_KUBE_FAULT"):
        os.environ.pop(k, None)
    return hostkube.answer(None, msg)


def databases(msg):
    import lookup
    import schemadata
    from baudelib import hostdb
    with open(os.path.join(HERE, "fixtures", "databases.json")) as f:
        fixture = json.load(f)
    server = {**fixture["server"], "engine": "postgres"}
    lookup.pg_servers_cached = lambda max_age=30: [server]
    lookup.pg_databases_cached = lambda s, max_age=30: [fixture["database"]]
    schemadata.pg_schema = lambda s, database, max_age=60: schemadata.pg_normalize(fixture["raw"])
    hostdb.time = types.SimpleNamespace(monotonic=lambda: 0.0)  # (no time taken: as the desktop scene's, drawn from the sample)
    return hostdb.answer(None, msg)


def main():
    msg = json.loads(sys.stdin.read())
    op = msg.get("op")
    reply = {"kube": kube, "databases": databases}[op](msg)
    print(json.dumps({"op": op, "req": msg.get("req"), **reply}))


if __name__ == "__main__":
    main()
