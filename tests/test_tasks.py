#!/usr/bin/env python3
"""A Bot's tasks over the wire, fuzzed: a host process (on a Unix socket of the run's own, its Bot the fake claude),
a head on its socket. Each round makes a task (Python, shell, or the Bot's own prompt) with a random form and runs it:
by hand, its form filled at random; dry (its asks answered by the dummy agent); failing (handed to its Bot); as a git
push to a repo of the run's own fires it (the payload filling its form); as its schedule comes due; stopped midway.
Each run must end, its record and log must be as the schema has them, and the host must still answer after. Then a
failure is healed end to end (handed to the Bot, which sets the task's script and runs it again with its own tools,
the new run asking it), two pushes during one run are both deployed (by the run after it), a Bot's routines, kept as
they were, are moved to tasks (twice: once only), a deploy's failure falls back to one-shot agents (the library's,
and baude-oneshot's) that nest under the Bot, are kept with the run, and are told the Bot as they finish, and the
script library is run outside a task. No GTK.

    python3 tests/test_tasks.py [--rounds N] [--seed S]
"""
import argparse
import json
import os
import random
import shlex
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = os.path.realpath(tempfile.mkdtemp(prefix="baude-tasks-"))
for var, sub in (("HOME", "home"), ("XDG_DATA_HOME", "xdg-data"), ("XDG_CONFIG_HOME", "xdg-config"),
                 ("XDG_CACHE_HOME", "xdg-cache"), ("XDG_STATE_HOME", "xdg-state"), ("CLAUDE_CONFIG_DIR", "claude"),
                 ("BAUDE_LAUNCHERS", "Applications"), ("BAUDE_BINS", "bin")):
    os.environ[var] = os.path.join(TMP, sub)
    os.makedirs(os.environ[var], exist_ok=True)
os.environ["KUBECONFIG"] = os.path.join(TMP, "no-kubeconfig")
os.environ["DOCKER_HOST"] = "unix://" + os.path.join(TMP, "no-docker.sock")
os.environ["BAUDE_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="bdt-", dir="/tmp")
os.environ.update(BAUDE_SSH="false", BAUDE_MENU_BAR="0", BAUDE_UPDATE="0", BAUDE_GH="", FAKE_CLAUDE_SESSION_IDS="1",
                  GIT_AUTHOR_NAME="Ada", GIT_AUTHOR_EMAIL="ada@example.com", GIT_COMMITTER_NAME="Ada",
                  GIT_COMMITTER_EMAIL="ada@example.com")
os.environ["PATH"] = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                                     if not os.path.exists(os.path.join(p, "claude")))
FAKE = os.path.join(TMP, "fake-claude")
with open(FAKE, "w") as f:
    f.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(os.path.join(HERE, "fake_claude.py"))} "$@"\n')
os.chmod(FAKE, 0o755)
os.environ["BAUDE_CLAUDE"] = FAKE

from baudelib import tasks, wire  # noqa: E402
from baudelib.server import Server  # noqa: E402

PY = '''import json
import baudetask as task
task.say("inputs", task.inputs)
task.say("payload", task.payload)
form = json.loads(%(form)r)
if form:
    got = task.ask("Fill this in.", form=form)
    assert set(got) == {f["name"] for f in form}, got
    task.say("got", got)
print("plain output")
if %(fail)r:
    raise RuntimeError("it went wrong on purpose")
if %(slow)r:
    import time
    time.sleep(30)
'''
SH = '''echo "branch=$BAUDE_IN_BRANCH"
echo "to stderr" >&2
%(fail)s
%(slow)s
'''


class Head:
    """A head on the host's socket: one request at a time, events set aside."""

    def __init__(self, path):
        self.ch, _ = wire.connect_local(path)
        self.req = 0
        self.lock = threading.Lock()

    def ask(self, msg, timeout=30):
        with self.lock:
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


def field(rng, i):
    kind = rng.choice(("text", "number", "bool", "choice"))
    f = {"name": f"f{i}", "type": kind}
    if kind == "choice":
        f["choices"] = rng.sample(["red", "green", "blue", "plain \"quoted\""], rng.randint(1, 3))
    if rng.random() < 0.4:
        f["default"] = {"text": "x", "number": 3, "bool": True, "choice": (f.get("choices") or [""])[0]}[kind]
    return f


def given(rng, form):
    return {f["name"]: {"text": rng.choice(["", "hello", "ü 💡"]), "number": rng.randint(-5, 5),
                        "bool": rng.random() < 0.5, "choice": rng.choice(f.get("choices") or [""])}[f["type"]]
            for f in form if rng.random() < 0.7}


def ended(head, sid, rid):
    def done():
        r = head.ask({"op": "task_run", "session": sid, "run": rid})
        assert r["op"] == "task_run", r
        return r if r["run"]["status"] not in ("running", "asking") else None
    got = wait(done, f"run {rid}")
    fits("TaskRun", got["run"])
    for line in got.get("log") or []:
        fits("TaskLogLine", line)
    return got


def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)


def runs_of(head, sid, tid):
    return head.ask({"op": "task", "session": sid, "id": tid})["runs"]


def heal(head, sid):
    """A failure handed to the Bot (the fake claude) as the run's over: it sets the task's script and runs it again
    in that turn, not waiting on it; the new run's ask waits for that turn's end, answered by a turn of its own."""
    tid = head.ask({"op": "tasks", "session": sid, "set": {
        "name": "Heal", "runtime": "python", "fallback": True, "timeout_s": 20,
        "script": 'print("HEAL ME")\nraise SystemExit(3)\n'}})["saved"]
    first = head.ask({"op": "run_task", "session": sid, "id": tid})["run"]
    failed = ended(head, sid, first["id"])
    assert failed["run"]["status"] == "failed" and failed["run"]["handed"], failed["run"]
    healed = wait(lambda: next((r for r in runs_of(head, sid, tid) if r["id"] != first["id"]
                                and r["status"] == "done"), None), "the healed run", 60)
    assert healed["version"] != first["version"] and healed["kind"] == "manual", healed
    log = ended(head, sid, healed["id"])["log"]
    assert any(x["kind"] == "answer" and "Looked into it" in x["text"] for x in log), log
    handed = wait(lambda: [x for x in ended(head, sid, first["id"])["log"] if x["kind"] == "answer"],
                  "the hand-off's answer")
    assert "Healed" in handed[0]["text"], handed
    assert not any(r["status"] == "skipped" for r in runs_of(head, sid, tid))
    print("healed: failed, handed over, set, run again, done", flush=True)


def pushes(head, srv, sid, repo):
    """Two pushes while a push's run is going: both deployed, by one run after it (from the head it ran to the
    latest)."""
    tid = head.ask({"op": "tasks", "session": sid, "set": {
        "name": "Deploy", "runtime": "python", "fallback": False, "timeout_s": 20,
        "script": 'import time\nimport baudetask as task\ntask.say("deploying", task.payload["after"])\n'
                  'time.sleep(3)\n',
        "triggers": [{"kind": "git_push", "repo": repo, "branch": "main", "poll_s": 10}]}})["saved"]

    def head_of():
        return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    srv.tasks.polled.clear()
    srv.tasks.due(block=True)  # (its first look: the head seen)
    git(repo, "commit", "-q", "--allow-empty", "-m", "deploy a")
    a = head_of()
    srv.tasks.polled.clear()
    srv.tasks.due(block=True)
    first = wait(lambda: next((r for r in runs_of(head, sid, tid) if r["kind"] == "git_push"), None), "a's run")
    assert first["payload"]["after"] == a and first["status"] == "running", first
    for m in ("deploy b", "deploy c"):
        git(repo, "commit", "-q", "--allow-empty", "-m", m)
        srv.tasks.polled.clear()
        srv.tasks.due(block=True)
    c = head_of()
    assert [r["id"] for r in runs_of(head, sid, tid)] == [first["id"]]
    ended(head, sid, first["id"])
    srv.tasks.due(block=True)
    second = wait(lambda: next((r for r in runs_of(head, sid, tid) if r["id"] != first["id"]), None), "c's run")
    assert second["payload"]["after"] == c and second["payload"]["before"] == a, second
    ended(head, sid, second["id"])
    assert len(runs_of(head, sid, tid)) == 2 and not any(r["status"] == "skipped" for r in runs_of(head, sid, tid))
    head.ask({"op": "tasks", "session": sid, "delete": tid})
    print("pushes: two during a run, deployed by the next", flush=True)


AGENTS = '''import os, subprocess, sys
import baudetask as task
try:
    raise RuntimeError("the deploy went wrong")
except RuntimeError as e:
    got = task.agent(f"The deploy failed: {e}. Find out why.", title="Why the deploy failed")
    task.say("agent", got["session"], got["failed"])
    out = subprocess.run([sys.executable, %(oneshot)r, "--title", "Look again", "Look again."], capture_output=True,
                         text=True)
    task.say("oneshot", out.returncode, out.stderr.strip())
    assert out.returncode == 0
'''


def agents(head, srv, sid):
    """A deploy that fails and falls back to one-shot agents (task.agent, and baude-oneshot run by the script): each
    nests under the Bot, is kept with the run, records the run, and is told the Bot (from it) as its turn ends, the
    run's log saying so too."""
    oneshot = os.path.join(os.path.dirname(HERE), "baude_oneshot.py")
    tid = head.ask({"op": "tasks", "session": sid, "set": {
        "name": "Deploy with fallback", "runtime": "python", "fallback": False, "timeout_s": 30,
        "script": AGENTS % {"oneshot": oneshot}}})["saved"]
    rid = head.ask({"op": "run_task", "session": sid, "id": tid})["run"]["id"]
    got = ended(head, sid, rid)
    assert got["run"]["status"] == "done", got
    chats = got["run"].get("chats") or []
    assert len(chats) == 2, got["run"]
    with srv.lock:
        listed = {c: srv.sessions[c].listing() for c in chats}
    for c in chats:
        fits("Session", listed[c])
        assert listed[c]["spawned_by"] == sid and listed[c]["task_run"]["run"] == rid, listed[c]

    def told():  # (the Bot's own asks of its user, as it's told: refused, so it goes on to the next)
        with srv.lock:
            bot = srv.sessions[sid]
            for ask in list(bot.asks):
                srv.take_input(None, bot, {"type": "control_response", "response": {
                    "subtype": "success", "request_id": ask, "response": {"behavior": "deny", "message": "no"}}})
            senders = srv.senders(bot) or {}
        return {v.get("session") for v in senders.values() if v.get("kind") == "subagent"} >= set(chats)
    wait(told, "the Bot told what its task's agents said")
    log = wait(lambda: [x for x in ended(head, sid, rid)["log"] if x["kind"] == "agent"] if
               len([x for x in ended(head, sid, rid)["log"] if x["kind"] == "agent"]) == 2 else None, "the agents' log")
    assert {x["session"] for x in log} == set(chats), log
    # run by a member of the mesh: the chat it starts is judged as that member (its started_by), as its tools are
    head.ask({"op": "tasks", "session": sid, "set": {
        "id": tid, "name": "Deploy with fallback", "runtime": "python", "fallback": False, "timeout_s": 30,
        "script": 'import baudetask as task\ntask.agent("Look into it.", wait=False)\n'}})
    member = {"id": "member-a", "verified": True}
    with srv.lock:
        st = srv.tasks.store(srv.sessions[sid])
        rid = srv.tasks.start(srv.sessions[sid], st.find(tid), None, "manual", {}, {}, by=member)["id"]
    chat = ended(head, sid, rid)["run"]["chats"][0]
    with srv.lock:
        assert srv.sessions[chat].meta.get("started_by") == member, srv.sessions[chat].meta
    assert srv.started_by(chat) == ("member-a", True)
    head.ask({"op": "tasks", "session": sid, "delete": tid})
    print("agents: a failure's one-shot agents nested under the Bot, kept with the run, told the Bot", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    print(f"seed {seed}", flush=True)
    rng = random.Random(seed)
    srv = Server(state_dir=os.path.join(TMP, "state"), name="tasks-host", log=lambda _t: None,
                 socket_path=os.path.join(os.environ["BAUDE_RUNTIME_DIR"], "h.sock"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    head = Head(srv.socket_path)
    folder = os.path.join(TMP, "bot")
    os.makedirs(folder)
    started = head.ask({"op": "start", "prompt": "", "cwd": folder, "draft": True})
    sid = started["session"]["id"]
    assert head.ask({"op": "bot", "session": sid, "bot": {"name": "Ada"}})["op"] != "error"
    repo = os.path.join(TMP, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "first")
    runs = 0
    for n in range(args.rounds):
        runtime = rng.choice(("python", "python", "shell", "bot"))
        form = [field(rng, i) for i in range(rng.randint(0, 3))]
        if runtime == "shell" or rng.random() < 0.5:
            form.append({"name": "branch", "type": "text", "default": "main"})
        fail, slow = rng.random() < 0.3, rng.random() < 0.15
        script = PY % {"form": json.dumps(form), "fail": fail, "slow": slow} if runtime == "python" else \
            SH % {"fail": "exit 4" if fail else "", "slow": "sleep 30" if slow else ""} if runtime == "shell" else \
            f"Round {n}: say hello."
        trigger = rng.choice((None, "git_push", "schedule"))
        triggers = [{"kind": "git_push", "repo": repo, "branch": "main", "poll_s": 10}] if trigger == "git_push" else \
            [{"kind": "schedule", "every": rng.choice((5, 60, 1440))}] if trigger == "schedule" else []
        set_ = head.ask({"op": "tasks", "session": sid, "set": {"name": f"Round {n}", "runtime": runtime,
                                                                 "script": script, "form": form,
                                                                 "triggers": triggers, "timeout_s": 20,
                                                                 "fallback": rng.random() < 0.7}})
        assert set_["op"] == "tasks", set_
        fits("TasksReply", set_)
        tid = set_["saved"]
        got = head.ask({"op": "task", "session": sid, "id": tid})
        assert got["op"] == "task", got
        assert got["task"]["version"] == tasks.version_of(script)
        fits("TaskReply", got)
        how = rng.choice(("hand", "dry", "trigger", "stop"))
        if how == "trigger" and trigger == "git_push":
            srv.tasks.polled.clear()
            srv.tasks.due(block=True)  # (its first look: the head seen, nothing fired)
            git(repo, "commit", "-q", "--allow-empty", "-m", f"push {n}")
            srv.tasks.polled.clear()
            srv.tasks.due(block=True)
            run = wait(lambda: next((r for r in head.ask({"op": "task", "session": sid, "id": tid})["runs"]
                                     if r["kind"] == "git_push"), None), "a push's run")
            assert run["payload"]["message"] == f"push {n}" and run["inputs"].get("branch", "main") == "main", run
        elif how == "trigger" and trigger == "schedule":
            with srv.lock:
                st = srv.tasks.store(srv.sessions[sid])
                st.find(tid)["triggers"][0]["next"] = time.time() - 1
            srv.tasks.due(block=True)
            run = wait(lambda: next((r for r in head.ask({"op": "task", "session": sid, "id": tid})["runs"]
                                     if r["kind"] == "schedule"), None), "a schedule's run")
        else:
            r = head.ask({"op": "run_task", "session": sid, "id": tid, "inputs": given(rng, form),
                          "dry": how == "dry"})
            assert r["op"] == "task_started", r
            fits("TaskStarted", r)
            run = r["run"]
            if how == "stop":
                time.sleep(rng.random())
                head.ask({"op": "task_stop", "session": sid, "run": run["id"]})
        got = ended(head, sid, run["id"])
        runs += 1
        status = got["run"]["status"]
        print(f"round {n}: {runtime} {how} {trigger or ''} -> {status}"
              f"{' (handed)' if got['run'].get('handed') else ''}", flush=True)
        kinds = {x["kind"] for x in got["log"]}
        if runtime == "python" and form and how != "stop" and not slow:
            assert {"ask", "answer"} <= kinds, got["log"]
        assert status in ("done", "failed", "stopped"), got["run"]
        if not slow and how != "stop":
            assert (status == "failed") == (fail and runtime != "bot"), got["run"]
        assert head.ask({"op": "tasks", "session": sid})["op"] == "tasks"  # (the host still answering)
        if rng.random() < 0.3:
            assert head.ask({"op": "tasks", "session": sid, "delete": tid})["op"] == "tasks"
    heal(head, sid)
    pushes(head, srv, sid, repo)
    agents(head, srv, sid)
    # a script's ops, refused without the run's token
    bad = head.ask({"op": "task_report", "run": "nope", "token": "nope", "say": "hi"})
    assert bad["op"] == "error", bad
    # a Bot's routines (as they were kept) moved to tasks
    with srv.lock:
        srv.sessions[sid].meta["routines"] = [{"id": "r1", "name": "Old", "prompt": "Do the old thing", "every": 60,
                                               "paused": False, "last": None, "next": time.time() + 600}]
        srv.tasks.stores.pop(sid, None)
    listed = head.ask({"op": "tasks", "session": sid})
    old = next(t for t in listed["tasks"] if t["name"] == "Old")
    assert old["runtime"] == "bot" and old["triggers"][0]["every"] == 60, old
    assert "routines" not in srv.sessions[sid].meta
    with srv.lock:  # (moved again, as if the host was killed before the routines were dropped: not twice)
        srv.sessions[sid].meta["routines"] = [{"id": "r1", "name": "Old", "prompt": "Do the old thing", "every": 60,
                                               "paused": False, "last": None, "next": time.time() + 600}]
        srv.tasks.stores.pop(sid, None)
    listed = head.ask({"op": "tasks", "session": sid})
    assert sum(t["name"] == "Old" for t in listed["tasks"]) == 1, listed
    # the library outside a task: a dummy agent's answers
    out = subprocess.run([sys.executable, "-c", "import baudetask as t; print(t.ask('x', form=[{'name': 'a', "
                          "'type': 'choice', 'choices': ['p', 'q']}]))"], capture_output=True, text=True,
                         env={k: v for k, v in os.environ.items() if not k.startswith("BAUDE_TASK")} |
                         {"PYTHONPATH": tasks.SCRIPT_DIR})
    assert "{'a': 'p'}" in out.stdout, out
    srv.shutdown()
    print(f"ok: {runs} runs", flush=True)


if __name__ == "__main__":
    main()
