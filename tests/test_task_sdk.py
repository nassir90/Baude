#!/usr/bin/env python3
"""The task-script SDK driven end to end: two host processes linked as a mesh (alpha and beta, on Unix sockets of the
run's own, their chats the fake claude's), a Bot on alpha, and a task of its whose script uses baudetask's SDK: it
lists the hosts and their models, opens a tab in the Bot's own Emission profile (one made up for the run, out of
sight) on a page this process serves and reads it, opens a Multiplex terminal (Multiplex started out of sight) and runs
echo in it, runs a command in a terminal of its own, lists Docker's containers and a cluster's pods (the fake docker
and kubectl: never a real daemon, cluster or kubeconfig), reads their logs, runs a command in a container and a pod,
applies a manifest, does the same on beta through the mesh, and is refused what its Bot may not do: on beta, whose
rules refuse alpha Docker's write, and on alpha once its Bot is a member's, whom alpha's rules refuse Kubernetes'
write. Its run must end done, its log saying each step; the hosts must still answer after. The generated client must
be as baudelib/tasksdk.py writes it now. No GTK in this process.

    python3 tests/test_task_sdk.py              # all of it
    python3 tests/test_task_sdk.py --no-gui     # without Emission and Multiplex
"""
import argparse
import http.server
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
TMP = os.path.realpath(tempfile.mkdtemp(prefix="baude-sdk-"))
for var, sub in (("HOME", "home"), ("XDG_DATA_HOME", "xdg-data"), ("XDG_CONFIG_HOME", "xdg-config"),
                 ("XDG_CACHE_HOME", "xdg-cache"), ("XDG_STATE_HOME", "xdg-state"), ("CLAUDE_CONFIG_DIR", "claude"),
                 ("BAUDE_LAUNCHERS", "Applications"), ("BAUDE_BINS", "bin")):
    os.environ[var] = os.path.join(TMP, sub)
    os.makedirs(os.environ[var], exist_ok=True)
FAKES = os.path.join(TMP, "fakes")  # (kubectl and docker on PATH: the fakes, for what a terminal runs too)
os.makedirs(FAKES)
for name, script in (("kubectl", "fake_kubectl.py"), ("docker", "fake_docker.py")):
    with open(os.path.join(FAKES, name), "w") as f:
        f.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(os.path.join(HERE, script))} "$@"\n')
    os.chmod(os.path.join(FAKES, name), 0o755)
KUBECONFIG = os.path.join(TMP, "kubeconfig")
with open(KUBECONFIG, "w") as f:  # (made up: the fake's clusters, never one that's real)
    f.write("apiVersion: v1\nkind: Config\ncurrent-context: k3d-dev\nclusters:\n"
            "- name: k3d-dev\n  cluster:\n    server: https://127.0.0.1:6550\n"
            "- name: prod\n  cluster:\n    server: https://prod.example.test:6443\n"
            "contexts:\n- name: k3d-dev\n  context:\n    cluster: k3d-dev\n    user: dev\n"
            "- name: prod\n  context:\n    cluster: prod\n    user: dev\nusers:\n- name: dev\n  user:\n    token: x\n")
os.environ.update(KUBECONFIG=KUBECONFIG, BAUDE_KUBECTL=os.path.join(FAKES, "kubectl"),
                  BAUDE_DOCKER=os.path.join(FAKES, "docker"), FAKE_DOCKER_STATE=os.path.join(TMP, "docker-state"),
                  DOCKER_HOST="unix://" + os.path.join(TMP, "no-docker.sock"))
os.environ["BAUDE_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="bsk-", dir="/tmp")
os.environ.update(BAUDE_SSH="false", BAUDE_MENU_BAR="0", BAUDE_UPDATE="0", BAUDE_GH="", FAKE_CLAUDE_SESSION_IDS="1")
os.environ["PATH"] = os.pathsep.join([FAKES] + [p for p in os.environ.get("PATH", "").split(os.pathsep)
                                                if not os.path.exists(os.path.join(p, "claude"))])
FAKE = os.path.join(TMP, "fake-claude")
with open(FAKE, "w") as f:
    f.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(os.path.join(HERE, "fake_claude.py"))} "$@"\n')
os.chmod(FAKE, 0o755)
os.environ["BAUDE_CLAUDE"] = FAKE

from baudelib import emissionlink, multiplexlink, tasksdk, wire  # noqa: E402
from baudelib.server import Server  # noqa: E402

FORM = [{"name": "page", "type": "text"}, {"name": "multiplex", "type": "bool"}]
BOT = "SDK Test"  # (its Emission profile's name too: made up for the run)
PAGE = b"""<!doctype html><title>SDK test page</title><main><h1>Welcome to the SDK test page</h1>
<p id="status">All systems go</p><button>Sign in</button></main>"""

SCRIPT = r'''import json
import baudetask as task

def step(name, *what):
    task.say(f"[{name}]", *what)

hosts = task.hosts()
step("hosts", json.dumps([[h.name, h.here, h.reachable] for h in hosts]))
beta = next(h for h in hosts if h.name == "beta")
assert hosts[0].here and beta.reachable, hosts
step("models", len(task.models()), len(task.models(host=beta)))

if task.inputs.get("page"):
    profile = task.emission.profile()
    step("emission profile", profile.name)
    tab = task.emission.open(url=task.inputs["page"])
    text = task.emission.text(tab=tab.tab).text or ""
    found = task.emission.find(query="Sign in", tab=tab.tab)
    step("emission", repr(tab.title), "Welcome" in text, len(found.matches or found.found or []) > 0)
    assert "Welcome to the SDK test page" in text, text
    if "All systems go" not in text:
        task.ask("The status line is missing. Is that expected?")
    tabs = task.emission.tabs()
    task.emission.close(tab=tab.tab)
    step("emission tabs", len(tabs.tabs or []))

if task.inputs.get("multiplex"):
    term = task.terminal.open(command="sh", title="sdk proof")
    tid = term.terminal or term.id
    task.terminal.type(terminal=tid, text="echo multiplex-says-$((6*7))", enter=True)
    seen = task.terminal.wait(terminal=tid, pattern="multiplex-says-42", timeout=20)
    step("multiplex", tid, seen.matched if "matched" in seen else seen.get("why"), "multiplex-says-42" in (seen.screen or ""))
    assert "multiplex-says-42" in (seen.screen or ""), seen
    task.terminal.close(terminal=tid)

ran = task.terminal.run(["echo", "hello from a terminal"])
step("terminal run", repr(ran.output.strip()), ran.status)
assert ran.output.strip() == "hello from a terminal" and ran.status == 0, ran

ctx = next(c for c in task.docker.contexts() if c.current)
containers = task.docker.containers(context=ctx.name)
running = [c for c in containers if c.running]
step("docker", ctx.name, len(containers), [c.name for c in running])
api = next(c for c in running if c.name.startswith("shop-api"))
step("docker logs", len(task.docker.logs(api, tail=3).splitlines()))
out = task.docker.exec(api, ["echo", "in the container"])
step("docker exec", repr(out.text.strip()), out.status)
assert out.text.strip() == "in the container" and out.status == 0, out

pods = task.kube.pods(namespace="shop", selector="app=web")
pod = next(p for p in pods if p.status == "Running")
step("kube pods", pod.context, [p.name for p in pods], pod.status)
assert all(p.namespace == "shop" for p in pods) and pod.name.startswith("web-"), pods
step("kube logs", len(task.kube.logs(pod, tail=5).splitlines()))
out = task.kube.exec(pod, ["echo", "in the pod"])
step("kube exec", repr(out.text.strip()), out.status)
step("kube apply", task.kube.apply("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: sdk-proof\n"))

there = task.docker.containers(host="beta")
step("docker on beta", len(there), there[0].host == beta.id)
step("kube on beta", [c.name for c in task.kube.contexts(host=beta)])
try:
    task.docker.stop(next(c for c in there if c.running))
    raise AssertionError("beta let alpha's Bot stop a container")
except task.Refused as e:
    step("refused on beta", e.rule, str(e))
    assert e.rule == "docker_write", e
'''

LOCAL = r'''import baudetask as task
pod = task.kube.pods(namespace="shop")[0]
task.say("[read as a member's]", pod.name)
try:
    task.kube.delete(pod)
    raise AssertionError("alpha let its member's Bot delete a pod")
except task.Refused as e:
    task.say("[refused on alpha]", e.rule, str(e))
    assert e.rule == "kube_write", e
'''


class Head:
    """A head on a host's socket: one request at a time, events set aside."""

    def __init__(self, path):
        self.ch, _ = wire.connect_local(path)
        self.req = 0

    def ask(self, msg, timeout=30):
        self.req += 1
        self.ch.sock.settimeout(timeout)
        self.ch.send({**msg, "req": self.req})
        while (r := self.ch.reply()) is not None:
            if r.get("req") == self.req:
                return r
        raise AssertionError(f"no answer to {msg['op']}")


def wait(cond, what, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if (got := cond()):
            return got
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def host(name):
    s = Server(state_dir=os.path.join(TMP, "servers", name), name=name, log=lambda _t: None,
               socket_path=os.path.join(os.environ["BAUDE_RUNTIME_DIR"], f"{name}.sock"))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def page_server():
    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *_):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/"


def multiplex_unseen():
    """Multiplex's window started out of sight (macOS: never on screen, no Dock icon; Linux: its own display), for
    the Bot's open to find it: its process."""
    code = ("import runpy, sys; sys.path.insert(0, %r); import macos; macos.stay_offscreen(); "
            "sys.argv = ['multiplex.py', '--empty', '-ApplePersistenceIgnoreState', 'YES']; "
            "runpy.run_path(%r, run_name='__main__')") % (ROOT, os.path.join(ROOT, "multiplex.py"))
    log = open(os.path.join(TMP, "multiplex.log"), "ab")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                            start_new_session=True, env={**os.environ, "BAUDE_NO_THEME": "1"})
    wait(lambda: any(info.get("opens") for _p, info in multiplexlink.processes()) or proc.poll() is not None,
         "Multiplex listening", 40)
    assert proc.poll() is None, open(os.path.join(TMP, "multiplex.log")).read()[-2000:]
    return proc


def run(head, sid, script, inputs, timeout=240):
    tid = head.ask({"op": "tasks", "session": sid, "set": {"name": "SDK proof", "runtime": "python",
                                                          "notify_failure": False, "timeout_s": timeout,
                                                          "script": script, "form": FORM}})["saved"]
    rid = head.ask({"op": "run_task", "session": sid, "id": tid, "inputs": inputs})["run"]["id"]

    def done():
        r = head.ask({"op": "task_run", "session": sid, "run": rid})
        return r if r["run"]["status"] not in ("running", "asking") else None
    got = wait(done, "the run", timeout + 30)
    for line in got.get("log") or []:
        print(f"  {line['kind']}: {line['text']}", flush=True)
    assert got["run"]["status"] == "done", got["run"]
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-gui", action="store_true", help="no Emission and no Multiplex")
    a = ap.parse_args()
    assert tasksdk.generated() == open(tasksdk.GENERATED, encoding="utf-8").read(), \
        "baudetask_sdk/generated.py isn't as baudelib/tasksdk.py writes it: run python3 -m baudelib.tasksdk"
    alpha, beta = host("alpha"), host("beta")
    beta.mesh.link({"via": "unix", "path": alpha.socket_path})
    wait(lambda: beta.me["id"] in alpha.mesh.hops and alpha.me["id"] in beta.mesh.hops, "the two linked")
    wait(lambda: alpha.mesh.pins.get(beta.me["id"]) == beta.mesh.key and
         beta.mesh.pins.get(alpha.me["id"]) == alpha.mesh.key, "their keys pinned")
    head, bhead = Head(alpha.socket_path), Head(beta.socket_path)
    assert bhead.ask({"op": "access_set", "capability": "docker_write", "rule": {"who": "nobody"}})["op"] == \
        "access_set"
    folder = os.path.join(TMP, "bot")
    os.makedirs(folder)
    sid = head.ask({"op": "start", "prompt": "", "cwd": folder, "draft": True})["session"]["id"]
    assert head.ask({"op": "bot", "session": sid, "bot": {"name": BOT}})["op"] != "error"
    real_store = os.path.join(os.path.expanduser("~" + os.environ.get("USER", "")), "Library", "WebKit",
                              "org.python.python", "WebsiteDataStore", emissionlink.bot_profile_id(sid))
    had_store = os.path.exists(real_store)
    procs = []
    try:
        inputs = {}
        if not a.no_gui:
            procs.append(multiplex_unseen())
            inputs = {"page": page_server(), "multiplex": True}
        run(head, sid, SCRIPT, inputs)
        # (the Bot a member's: judged as that member, whom alpha's rules let read the clusters but not change them)
        assert head.ask({"op": "access_set", "capability": "kube_write", "rule": {"who": "nobody"}})["op"] == \
            "access_set"
        with alpha.lock:
            alpha.sessions[sid].meta["started_by"] = {"id": beta.me["id"], "verified": True}
        run(head, sid, LOCAL, {})
        bad = Head(alpha.socket_path)  # (a token no run has: refused at its hello)
        got = bad.ask({"op": "hello", "client": "task", "task": {"run": "nope", "token": "0" * 32}})
        assert got["op"] == "error" and got.get("code") == wire.DENIED, got
        print(f"  a made-up token: {wire.said(got)}", flush=True)
        for h in (Head(alpha.socket_path), Head(beta.socket_path)):  # (both still answer)
            assert h.ask({"op": "subscribe"})["op"] == "sessions"
        print("ok", flush=True)
    finally:
        for p in procs:
            p.terminate()
        for info in emissionlink.running():  # (the Bot's Emission this run started, and only it: its sockets are the run's)
            if info.get("pid"):
                try:
                    os.kill(info["pid"], signal.SIGTERM)
                except OSError:
                    pass
        if sys.platform == "darwin" and not had_store:
            time.sleep(1)
            shutil.rmtree(real_store, ignore_errors=True)


if __name__ == "__main__":
    main()
