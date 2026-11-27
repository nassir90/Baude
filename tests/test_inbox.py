#!/usr/bin/env python3
"""The Inbox driven end to end on a host process of the run's own (its chats the fake claude's), no GTK: a Bot's
task raises asks of a person (baudetask.inbox: a picture, coloured buttons, a form), a head subscribed hears each as it
changes, fetches its picture, has a model answer one (the fake's answer, whatever it makes of it), sends one to a Bot,
writes to the asking Bot, and presses a button; the script gets the answer and goes on. A run that revises and then
withdraws its ask, and one that ends with its ask unanswered (stopped), and the host started again on the same state:
the stopped ask still there, then removed. Nothing must blow up and the hosts must still answer.

    python3 tests/test_inbox.py
"""
import base64
import os
import shlex
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
TMP = os.path.realpath(tempfile.mkdtemp(prefix="baude-inbox-"))
for var, sub in (("HOME", "home"), ("XDG_DATA_HOME", "xdg-data"), ("XDG_CONFIG_HOME", "xdg-config"),
                 ("XDG_CACHE_HOME", "xdg-cache"), ("XDG_STATE_HOME", "xdg-state"), ("CLAUDE_CONFIG_DIR", "claude"),
                 ("BAUDE_LAUNCHERS", "Applications"), ("BAUDE_BINS", "bin")):
    os.environ[var] = os.path.join(TMP, sub)
    os.makedirs(os.environ[var], exist_ok=True)
os.environ["BAUDE_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="bin-", dir="/tmp")
os.environ.update(BAUDE_SSH="false", BAUDE_MENU_BAR="0", BAUDE_UPDATE="0", BAUDE_GH="", FAKE_CLAUDE_SESSION_IDS="1")
os.environ["PATH"] = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                                     if not os.path.exists(os.path.join(p, "claude")))
FAKE = os.path.join(TMP, "fake-claude")
with open(FAKE, "w") as f:
    f.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(os.path.join(HERE, "fake_claude.py"))} "$@"\n')
os.chmod(FAKE, 0o755)
os.environ["BAUDE_CLAUDE"] = FAKE

from baudelib import wire  # noqa: E402
from baudelib.server import Server  # noqa: E402

PNG = base64.b64decode(  # (a 2×1 picture)
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAAEUlEQVR4nGP4z8DwnwEIGAAa5gP9P6KpqQAAAABJRU5ErkJggg==")
SHOT = os.path.join(TMP, "home.png")
with open(SHOT, "wb") as f:
    f.write(PNG)

ANSWERED = r'''import baudetask as task
asked = task.inbox("### Deploy 2.5?\n\nStaging has run **2.5**.\n\n![home](home.png)\n\n| Page | Now |\n|---|---|\n"
                   "| Home | ![home](home.png) |",
                   ["Approve", {"label": "Deny", "style": "red"}, {"label": "Later", "style": "teal"}],
                   pictures=[%r], title="Deploy site: 2.5 to production?",
                   form=[{"name": "note", "type": "text", "label": "Note"},
                         {"name": "cap", "type": "number", "default": 20}, {"name": "notify", "type": "bool"}])
task.say("[raised]", asked.id)
assert asked.poll() is None
got = asked.wait()
task.say("[answered]", got["label"], got["values"]["note"], got["values"]["cap"])
''' % SHOT

WITHDRAWN = r'''import baudetask as task
asked = task.inbox("Which logo?", [{"label": "A", "picture": "a.png"}, "None"], pictures={"a.png": %r})
task.say("[v]", asked.revise(body="Which logo, now?"))
assert asked.wait(timeout=0.5) is None
asked.withdraw()
try:
    asked.wait()
except task.Unanswered as e:
    task.say("[withdrawn]", e.status)
''' % SHOT

LEFT = r'''import baudetask as task
asked = task.inbox("Morning digest", ["Dismiss"])
task.say("[left]", asked.id)
'''


class Head:
    """A head on a host's socket: requests answered in turn, its events (inbox_changed) kept."""

    def __init__(self, path):
        self.ch, _ = wire.connect_local(path)
        self.req, self.events, self.lock = 0, [], threading.Lock()

    def ask(self, msg, timeout=30):
        self.req += 1
        self.ch.sock.settimeout(timeout)
        self.ch.send({**msg, "req": self.req})
        while (r := self.ch.reply()) is not None:
            if r.get("req") == self.req:
                return r
            if r.get("op") == "inbox_changed":
                self.events.append(r)
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


def start(head, sid, name, script):
    tid = head.ask({"op": "tasks", "session": sid, "set": {"name": name, "runtime": "python", "notify_failure": False,
                                                          "timeout_s": 120, "script": script}})["saved"]
    return head.ask({"op": "run_task", "session": sid, "id": tid})["run"]["id"]


def ended(head, sid, rid):
    def done():
        r = head.ask({"op": "task_run", "session": sid, "run": rid})
        return r if r["run"]["status"] not in ("running", "asking") else None
    got = wait(done, "the run")
    for line in got.get("log") or []:
        print(f"  {line['kind']}: {line['text']}", flush=True)
    return got


def asks(head):
    return head.ask({"op": "inbox"})["asks"]


def main():
    alpha = host("alpha")
    head = Head(alpha.socket_path)
    assert head.ask({"op": "subscribe"})["op"] == "sessions"
    folder = os.path.join(TMP, "bot")
    os.makedirs(folder)
    sids = []
    for name in ("Juniper", "Fern"):
        sid = head.ask({"op": "start", "prompt": "", "cwd": folder, "draft": True})["session"]["id"]
        assert head.ask({"op": "bot", "session": sid, "bot": {"name": name}})["op"] != "error"
        sids.append(sid)
    sid, fern = sids

    rid = start(head, sid, "Deploy site", ANSWERED)
    ask = wait(lambda: next((a for a in asks(head) if a["run"] == rid), None), "the ask listed")
    print(f"  listed: {ask['task_name']} · {ask['versions'][-1]['title']} · {len(ask['versions'][-1]['buttons'])} "
          f"buttons", flush=True)
    pic = head.ask({"op": "inbox_picture", "ask": ask["id"], "version": 1, "name": "home.png"})
    assert base64.b64decode(pic["data"]) == PNG, pic
    print(f"  picture: {pic['mime']}", flush=True)
    got = head.ask({"op": "inbox_route", "ask": ask["id"], "model": "opus"})
    print(f"  model asked: {got['op']}", flush=True)
    wait(lambda: (next(a for a in asks(head) if a["id"] == ask["id"]).get("proposal") or {}).get("status")
         in ("ready", "failed"), "the model's answer", 60)
    print(f"  model's answer: {next(a for a in asks(head) if a['id'] == ask['id'])['proposal']['status']}", flush=True)
    print(f"  sent to Fern: {head.ask({'op': 'inbox_route', 'ask': ask['id'], 'bot': fern})['op']}", flush=True)
    print(f"  written to the Bot: {head.ask({'op': 'inbox_say', 'ask': ask['id'], 'text': 'Is staging up?'})['op']}",
          flush=True)
    bad = head.ask({"op": "inbox_press", "ask": ask["id"], "button": 9})
    print(f"  a button it hasn't: {wire.said(bad)}", flush=True)
    got = head.ask({"op": "inbox_press", "ask": ask["id"], "button": 0, "values": {"note": "ship it", "cap": "25"}})
    assert got["op"] == "inbox_done", got
    run = ended(head, sid, rid)
    assert run["run"]["status"] == "done", run["run"]
    assert not any(a["id"] == ask["id"] for a in asks(head))

    rid = start(head, sid, "Kestrel logo", WITHDRAWN)
    assert ended(head, sid, rid)["run"]["status"] == "done"
    rid = start(head, sid, "Morning digest", LEFT)
    assert ended(head, sid, rid)["run"]["status"] == "done"
    left = wait(lambda: next((a for a in asks(head) if a["run"] == rid and a["status"] == "stopped"), None),
                "the ask stopped")
    print(f"  stopped: {left['task_name']}", flush=True)
    print(f"  events heard: {len(head.events)}", flush=True)

    alpha.shutdown()
    time.sleep(1)
    again = host("alpha")  # (the same state: the stopped ask kept)
    h2 = Head(again.socket_path)
    kept = [a for a in asks(h2) if a["id"] == left["id"]]
    assert kept and kept[0]["status"] == "stopped", kept
    assert h2.ask({"op": "inbox_remove", "ask": left["id"]})["op"] == "inbox_done"
    assert not any(a["id"] == left["id"] for a in asks(h2))
    assert h2.ask({"op": "subscribe"})["op"] == "sessions"
    print("ok", flush=True)


if __name__ == "__main__":
    main()
