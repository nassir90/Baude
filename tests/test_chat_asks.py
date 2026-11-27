#!/usr/bin/env python3
"""A chat's own asks driven end to end on a host process of the run's own (its chat the fake claude's), no GTK: the
chat's model raises an ask with its chat's tools (mcp__chat__ask_user: a picture it made, given by its path, a form,
coloured buttons), a head subscribed hears it, fetches its picture, writes to the asking chat and presses a button;
the answer comes back into the chat as a message from the Inbox, its next turn. The model revises an ask, reads it,
withdraws one, waits on one it's answered meanwhile (told it there, not as a message too); the host started again on
the same state keeps a waiting ask waiting and answers it back into the chat; one whose chat is archived is stopped.
Nothing must blow up and the host must still answer.

    python3 tests/test_chat_asks.py
"""
import base64
import json
import os
import shlex
import sys
import tempfile
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
TMP = os.path.realpath(tempfile.mkdtemp(prefix="baude-chat-asks-"))
for var, sub in (("HOME", "home"), ("XDG_DATA_HOME", "xdg-data"), ("XDG_CONFIG_HOME", "xdg-config"),
                 ("XDG_CACHE_HOME", "xdg-cache"), ("XDG_STATE_HOME", "xdg-state"), ("CLAUDE_CONFIG_DIR", "claude"),
                 ("BAUDE_LAUNCHERS", "Applications"), ("BAUDE_BINS", "bin")):
    os.environ[var] = os.path.join(TMP, sub)
    os.makedirs(os.environ[var], exist_ok=True)
os.environ["BAUDE_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="bca-", dir="/tmp")
os.environ.update(BAUDE_SSH="false", BAUDE_MENU_BAR="0", BAUDE_UPDATE="0", BAUDE_GH="", FAKE_CLAUDE_SESSION_IDS="1",
                  FAKE_CLAUDE_LOG=os.path.join(TMP, "fake.log"),
                  FAKE_ANSWERS=json.dumps({"hello": "Hi.", "Inbox": "Noted."}))  # (turns that run no tools)
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
FOLDER = os.path.join(TMP, "project")
os.makedirs(FOLDER)
with open(os.path.join(FOLDER, "chart.png"), "wb") as f:
    f.write(PNG)

ASK = {"title": "Which chart goes in the report?",
       "body": "### Orders per day\n\nI drew the week's orders two ways.\n\n![bars](chart.png)\n\n"
               "| Option | Looks |\n|---|---|\n| Bars | ![bars](chart.png) |\n| Line | (none yet) |",
       "buttons": [{"label": "Bars", "style": "primary", "picture": "chart.png"}, {"label": "Line", "style": "teal"},
                   "Neither"],
       "form": [{"name": "note", "type": "text", "label": "Note"},
                {"name": "weeks", "type": "number", "label": "Weeks shown", "default": 1}],
       "pictures": [{"name": "chart.png", "path": "chart.png"}]}


class Head:
    """A head on a host's socket: requests answered in turn, its events (inbox_changed) kept."""

    def __init__(self, path):
        self.ch, _ = wire.connect_local(path)
        self.req, self.events = 0, []

    def ask(self, msg, timeout=60):
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
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


def host(name):
    s = Server(state_dir=os.path.join(TMP, "servers", name), name=name, log=lambda t: print(f"  [host] {t}"),
               socket_path=os.path.join(os.environ["BAUDE_RUNTIME_DIR"], f"{name}.sock"))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def say(head, sid, text):
    got = head.ask({"op": "input", "session": sid, "msg": {"type": "user", "uuid": str(uuid.uuid4()),
                                                            "message": {"role": "user", "content": text}}})
    assert got.get("op") != "error", got


def idle(srv, sid):
    wait(lambda: not srv.sessions[sid].busy, "the chat idle", 60)


def tool(head, srv, sid, name, args):
    """The chat's model calls one of its chat's tools (the fake's CHAT TOOL): what it gave."""
    mark = len(log())
    say(head, sid, f"CHAT TOOL {name} {json.dumps(args)}")
    line = wait(lambda: next((ln for ln in log()[mark:] if ln.startswith(f"CHAT TOOL {name} ")), None),
                f"{name} called")
    idle(srv, sid)
    said = line.split(" ", 3)[3]
    try:
        return json.loads(said)
    except ValueError:
        return {"error": said}


def log():
    try:
        with open(os.environ["FAKE_CLAUDE_LOG"]) as f:
            return f.read().splitlines()
    except OSError:
        return []


def told(mark, words):
    """A message the fake was sent since log line mark, holding words."""
    return next((ln for ln in log()[mark:] if words in ln and '"type": "user"' in ln), None)


def asks(head):
    return head.ask({"op": "inbox"})["asks"]


def main():
    alpha = host("alpha")
    head = Head(alpha.socket_path)
    assert head.ask({"op": "subscribe"})["op"] == "sessions"
    sid = head.ask({"op": "start", "prompt": "", "cwd": FOLDER, "draft": True})["session"]["id"]
    assert head.ask({"op": "rename", "session": sid, "title": "Weekly report"})["op"] != "error"
    say(head, sid, "hello")
    idle(alpha, sid)

    got = tool(head, alpha, sid, "ask_user", ASK)
    print(f"  ask_user: {got}", flush=True)
    aid = got["ask"]
    ask = next(a for a in asks(head) if a["id"] == aid)
    assert ask["run"] is None and ask["session"] == sid, ask
    print(f"  listed: from {ask['chat']} · {ask['versions'][-1]['title']} · "
          f"{[b['label'] for b in ask['versions'][-1]['buttons']]} · form {[f['name'] for f in ask['versions'][-1]['form']]}",
          flush=True)
    pic = head.ask({"op": "inbox_picture", "ask": aid, "version": 1, "name": "chart.png"})
    assert base64.b64decode(pic["data"]) == PNG, pic
    print(f"  picture: {pic['mime']}", flush=True)

    mark = len(log())
    assert head.ask({"op": "inbox_say", "ask": aid, "text": "Can the line one have dots?"})["op"] == "inbox_done"
    wait(lambda: told(mark, "Can the line one have dots?"), "the person's words in the chat")
    idle(alpha, sid)
    print("  written in the box: sent the asking chat", flush=True)

    got = tool(head, alpha, sid, "revise_ask", {"ask": aid, "body": ASK["body"].replace("(none yet)", "dots")})
    print(f"  revise_ask: {got}", flush=True)
    assert got.get("version") == 2, got
    bad = head.ask({"op": "inbox_press", "ask": aid, "button": 0, "version": 1})
    print(f"  a press on version 1: {wire.said(bad)}", flush=True)

    mark = len(log())
    got = head.ask({"op": "inbox_press", "ask": aid, "button": 1, "version": 2, "values": {"note": "dots, please"}})
    assert got["op"] == "inbox_done", got
    line = wait(lambda: told(mark, aid), "the answer sent into the chat")
    print(f"  answer in the chat: {json.loads(line)['message']['content'][:160]!r}", flush=True)
    idle(alpha, sid)
    assert not any(a["id"] == aid for a in asks(head))
    got = tool(head, alpha, sid, "read_ask", {"ask": aid})
    print(f"  read_ask: {got}", flush=True)
    assert got["status"] == "answered" and got["answer"]["label"] == "Line", got

    bot = head.ask({"op": "start", "prompt": "", "cwd": FOLDER, "draft": True})["session"]["id"]
    assert head.ask({"op": "bot", "session": bot, "bot": {"name": "Fern"}})["op"] != "error"
    got = tool(head, alpha, sid, "ask_user", {"body": "Which day?", "buttons": ["Monday", "Friday"]})
    routed = got["ask"]
    assert head.ask({"op": "inbox_route", "ask": routed, "bot": bot})["op"] == "inbox_done"
    print(f"  routed: sent to {next(a for a in asks(head) if a['id'] == routed)['sent_to_name']}", flush=True)
    mark = len(log())
    assert head.ask({"op": "inbox_press", "ask": routed, "button": 1, "as": bot})["op"] == "inbox_done"
    line = wait(lambda: next((ln for ln in log()[mark:] if routed in ln and 'baude-sender name=\\"Inbox' in ln), None),
                "the Bot's answer sent into the chat")
    print(f"  the Bot's answer in the chat: {json.loads(line)['message']['content'].split(chr(10), 2)[2]!r}", flush=True)
    idle(alpha, sid)

    got = tool(head, alpha, sid, "ask_user", {"body": "Send the report now?", "buttons": ["Send", "Not yet"]})
    gone = got["ask"]
    got = tool(head, alpha, sid, "withdraw_ask", {"ask": gone})
    print(f"  withdraw_ask: {got}", flush=True)
    assert not any(a["id"] == gone for a in asks(head))

    mark = len(log())
    other = Head(alpha.socket_path)

    def press_soon():
        aid2 = wait(lambda: next((a["id"] for a in asks(other) if a["versions"][-1]["body"] == "Quick one?"), None),
                    "the quick ask")
        time.sleep(0.5)
        other.ask({"op": "inbox_press", "ask": aid2, "button": 0})
    threading.Thread(target=press_soon, daemon=True).start()
    got = tool(head, alpha, sid, "ask_user", {"body": "Quick one?", "buttons": ["Yes", "No"], "wait_s": 30})
    print(f"  ask_user, waited: {got['status']} {got.get('answer', {}).get('label')}", flush=True)
    assert got["status"] == "answered", got
    time.sleep(2)
    assert not told(mark, got["ask"]), "a waited answer was sent as a message too"

    got = tool(head, alpha, sid, "ask_user", {"title": "Ship it on Monday?", "body": "### Monday?\n\nOr Tuesday.",
                                              "buttons": [{"label": "Monday", "style": "green"}, "Tuesday"]})
    kept = got["ask"]
    alpha.shutdown()
    time.sleep(1)
    alpha = host("alpha")
    head = Head(alpha.socket_path)
    assert head.ask({"op": "subscribe"})["op"] == "sessions"
    now = next((a for a in asks(head) if a["id"] == kept), None)
    print(f"  after a restart: {now and now['status']}", flush=True)
    assert now and now["status"] == "waiting", now
    mark = len(log())
    assert head.ask({"op": "inbox_press", "ask": kept, "button": 0})["op"] == "inbox_done"
    wait(lambda: told(mark, kept), "the answer sent into the chat after the restart")
    idle(alpha, sid)
    print("  answered after the restart: sent the chat", flush=True)

    got = tool(head, alpha, sid, "ask_user", {"body": "Left open", "buttons": ["OK"]})
    left = got["ask"]
    assert head.ask({"op": "archive", "session": sid})["op"] != "error"
    stopped = wait(lambda: next((a for a in asks(head) if a["id"] == left and a["status"] == "stopped"), None),
                   "the ask stopped")
    print(f"  chat archived: its ask {stopped['status']}", flush=True)
    assert head.ask({"op": "inbox_remove", "ask": left})["op"] == "inbox_done"
    print(f"  events heard: {len(head.events)}", flush=True)
    assert head.ask({"op": "subscribe"})["op"] == "sessions"
    alpha.shutdown()
    print("ok", flush=True)


if __name__ == "__main__":
    main()
