#!/usr/bin/env python3
"""Deciders over the wire, fuzzed: two host processes linked as a mesh (on Unix sockets of the run's own), one with a
decider (tests/fake_decider.py, a stand-in for `strands-decider serve`: no model), the other a Bot (the fake claude)
whose task's script decides with it through its own host and the mesh (baudetask.decide). Each round asks random
questions (choice, noul, score; one, or several by name), some the decider refuses; every answer must be as the
schema has it. A decider that isn't running, and the stand-in stopped, are DecisionUnavailable to the script, which
asks its Bot instead, on its own: nothing else is asked for it. Settings' ops (deciders: add, test, remove, list) are
checked as they go: each listing as the schema has it, with whether each decider answers, the other host's as its
record says. No GTK.

    python3 tests/test_deciders.py [--rounds N] [--seed S]
"""
import argparse
import json
import os
import random
import shlex
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
TMP = os.path.realpath(tempfile.mkdtemp(prefix="baude-deciders-"))
for var, sub in (("HOME", "home"), ("XDG_DATA_HOME", "xdg-data"), ("XDG_CONFIG_HOME", "xdg-config"),
                 ("XDG_CACHE_HOME", "xdg-cache"), ("XDG_STATE_HOME", "xdg-state"), ("CLAUDE_CONFIG_DIR", "claude"),
                 ("BAUDE_LAUNCHERS", "Applications"), ("BAUDE_BINS", "bin")):
    os.environ[var] = os.path.join(TMP, sub)
    os.makedirs(os.environ[var], exist_ok=True)
os.environ["KUBECONFIG"] = os.path.join(TMP, "no-kubeconfig")
os.environ["DOCKER_HOST"] = "unix://" + os.path.join(TMP, "no-docker.sock")
os.environ["BAUDE_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="bdd-", dir="/tmp")
os.environ.update(BAUDE_SSH="false", BAUDE_MENU_BAR="0", BAUDE_UPDATE="0", BAUDE_GH="", FAKE_CLAUDE_SESSION_IDS="1")
os.environ["PATH"] = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                                     if not os.path.exists(os.path.join(p, "claude")))
FAKE = os.path.join(TMP, "fake-claude")
with open(FAKE, "w") as f:
    f.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(os.path.join(HERE, "fake_claude.py"))} "$@"\n')
os.chmod(FAKE, 0o755)
os.environ["BAUDE_CLAUDE"] = FAKE

import fake_decider  # noqa: E402
from baudelib import wire  # noqa: E402
from baudelib.server import Server  # noqa: E402

SCRIPT = '''import json
import baudetask as task
asks = json.loads(%(asks)r)
for model, questions, one in asks:
    made = {n: getattr(task, q[0])(*q[1:]) for n, q in questions.items()}
    try:
        got = task.decide("Help! My payouts have been failing for 3 days!", next(iter(made.values())) if one else made,
                          model=model)
    except task.DecisionUnavailable as e:
        task.say("unavailable:", e)
        task.say("asked instead:", task.ask("Is this urgent? Help! My payouts have been failing for 3 days!"))
        continue
    except task.Refused as e:
        task.say("refused:", e)
        continue
    for name, a in ({"answer": got} if one else got).items():
        task.say("decided", name, json.dumps({"type": a.type, "choice": a.choice, "noul": a.noul, "score": a.score,
                                               "scores": a.scores, "confidence": a.confidence}))
'''


class Head:
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


def wait(cond, what, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if (got := cond()):
            return got
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def fits(name, value):
    out = []
    wire._validate(wire.DEFS[name], value, name, out)
    assert not out, (name, out, value)


def host(name):
    s = Server(state_dir=os.path.join(TMP, "servers", name), name=name, log=lambda _t: None,
               socket_path=os.path.join(os.environ["BAUDE_RUNTIME_DIR"], f"{name}.sock"))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def question(rng):
    kind = rng.choice(("choice", "noul", "score"))
    if kind == "choice":
        return ["choice", "Which team should handle this?",
                rng.sample(["billing", "sales", "retail", "payouts"], rng.choice((1, 2, 3, 4)))]
    if kind == "score":
        return ["score", "How frustrated is the customer?", ["calm", "frustrated", "depressed", "furious"][
            :rng.choice((1, 2, 3, 4))]]
    return ["noul", "Is this urgent?"]


def ran(head, sid, tid):
    rid = head.ask({"op": "run_task", "session": sid, "id": tid})["run"]["id"]

    def done():
        r = head.ask({"op": "task_run", "session": sid, "run": rid})
        return r if r["run"]["status"] not in ("running", "asking") else None
    got = wait(done, f"run {rid}", 60)
    for line in got.get("log") or []:
        fits("TaskLogLine", line)
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    print(f"seed {seed}", flush=True)
    rng = random.Random(seed)
    stub = fake_decider.serve()
    studio, laptop = host("studio"), host("laptop")
    laptop.mesh.link({"via": "unix", "path": studio.socket_path})
    wait(lambda: laptop.me["id"] in studio.mesh.hops and studio.me["id"] in laptop.mesh.hops, "the two linked")
    there, here = Head(studio.socket_path), Head(laptop.socket_path)
    added = there.ask({"op": "deciders", "what": "add", "name": "Triage", "url": f"http://127.0.0.1:{stub.server_port}/v1"})
    fits("DecidersReply", added)
    assert added["tested"]["reachable"] and added["tested"]["model"] == fake_decider.MODEL, added
    assert there.ask({"op": "deciders", "what": "add", "name": "triage", "url": "http://x"})["error"]
    assert there.ask({"op": "deciders", "what": "add", "name": "Bad", "url": "ftp://x"})["error"]
    gone = here.ask({"op": "deciders", "what": "add", "name": "Local", "url": "http://127.0.0.1:9"})
    assert not gone["tested"]["reachable"] and gone["tested"]["error"], gone
    listed = wait(lambda: (r := here.ask({"op": "deciders"})) and any(
        d["value"] == "studio/triage" and d["reachable"] for d in r["deciders"]) and r, "studio's decider listed")
    fits("DecidersReply", listed)
    print("listed:", [(d["value"], d["reachable"]) for d in listed["deciders"]], flush=True)

    folder = os.path.join(TMP, "bot")
    os.makedirs(folder)
    sid = here.ask({"op": "start", "prompt": "", "cwd": folder, "draft": True})["session"]["id"]
    assert here.ask({"op": "bot", "session": sid, "bot": {"name": "Ada"}})["op"] != "error"
    decided = unavailable = 0
    for n in range(args.rounds):
        asks = []
        for _ in range(rng.randint(1, 3)):
            one = rng.random() < 0.5
            qs = {"answer": question(rng)} if one else {f"q{i}": question(rng) for i in range(rng.randint(1, 3))}
            asks.append([rng.choice(("studio/triage", "studio/Triage", "studio/triage", "local", "studio/nope",
                                     "elsewhere/triage")), qs, one])
        if n == args.rounds - 1:  # (the decider stopped: the script finds out as it asks, and falls back itself)
            stub.shutdown()
            stub.server_close()
            asks = [["studio/triage", {"answer": ["noul", "Is this urgent?"]}, True]]
        tid = here.ask({"op": "tasks", "session": sid, "set": {"name": f"Decide {n}", "runtime": "python",
                                                                "script": SCRIPT % {"asks": json.dumps(asks)},
                                                                "timeout_s": 40, "notify_failure": False}})["saved"]
        got = ran(here, sid, tid)
        assert got["run"]["status"] == "done", (got["run"], got.get("log"))
        log = [x["text"] for x in got["log"] if x["kind"] in ("say", "answer")]
        for text in log:
            if text.startswith("decided "):
                a = json.loads(text.split(" ", 2)[2])
                fits("Decision", {k: v for k, v in a.items() if v is not None and k != "scores"})
                assert 0 <= a["confidence"] <= 1, a
                decided += 1
            elif text.startswith("unavailable:"):
                unavailable += 1
        for model, qs, _one in asks:
            ok = model.lower() == "studio/triage" and n != args.rounds - 1
            valid = all(q[0] == "noul" or len(q[2]) >= 2 for q in qs.values())
            if not ok:
                assert any(t.startswith("unavailable:") for t in log), (model, log)
            elif not valid:
                assert any(t.startswith("refused:") for t in log), (qs, log)
        print(f"round {n}:", *log, sep="\n  ", flush=True)
    last = here.ask({"op": "deciders"})
    assert any(d["value"] == "local" and not d["reachable"] for d in last["deciders"]), last
    assert here.ask({"op": "deciders", "what": "remove", "id": "local"})["deciders"][0]["value"] != "local"
    for h in (here, there):
        assert h.ask({"op": "deciders"})["op"] == "deciders"
    print(f"decided {decided}, unavailable {unavailable}: ok", flush=True)


if __name__ == "__main__":
    main()
