#!/usr/bin/env python3
"""baude-oneshot: one Claude Code session in a host process (baude_headless.py), this machine's or another's over ssh,
started (or carried on) with a prompt, attached to, and what its claude says streamed to stdout as JSON lines, as
`claude -p --output-format stream-json` writes them. A head, as Baude is: the session goes on in the host process, and
Baude lists it under its host and group. No GTK needed.

    python3 baude_oneshot.py [--host DEST [--host-command CMD] | --member ID] [--group G] [--cwd DIR] [--model M]
                             [--effort E] [--mode MODE] [--title T] [--system TEXT] [--add-dir DIR]... [--allow RULE]...
                             [--deny RULE]... [--fork-from SESSION] [--resume SESSION] [--follow] [--input]
                             [--partial] [--ask-timeout S [--ask-default allow]] PROMPT

Its first line is {"type": "baude", "event": "started", "session": {...}}, the session as the host lists it: its "id"
is what --resume takes. Then claude's own lines, till the result of its turn (--resume of a session at a turn: the
turn after it, its own); with --input, the result of the last turn stdin's started (--follow, with --input: till
stdin closes, or claude exits). With --input, each line on stdin is a stream-json message for claude: another user
message, or a control_response answering what it asks (a permission, as claude's control_request has it); without it,
what it asks is answered with a deny, unless --ask-timeout leaves it to the host process to answer after that long.
Once nobody reads its stdout (| head -1), it says no more, and still sees the turn to its end. Exit: 0; 1 if the
turn's result was an error, or claude ended before it; 2 if the host couldn't be reached, or the session started, or
it refused what was sent; 3 if the host process went before the turn's end.
"""
import argparse
import itertools
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from baudelib import hosts  # noqa: E402
from baudelib.claude import MODES  # noqa: E402


def out(obj):
    """A line out; once nobody reads them (| head -1, say), none, but the turn's still seen to its end."""
    if out.closed:
        return
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, ValueError):
        out.closed = True
        try:  # (and nothing more to flush as it exits)
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass


out.closed = False


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("prompt", nargs="?", help="what to say first (from stdin's first line with --input, if it's left out)")
    p.add_argument("--host", help="another machine's host process, where ssh reaches it: an alias, a host, user@host "
                                  "(default: this machine's, started if it isn't running)")
    p.add_argument("--host-command", help="the host process there, if baude_headless.py isn't on its PATH")
    p.add_argument("--member", help="a member of the mesh (its id, as `baude_headless.py mesh` lists it), reached "
                                    "through this machine's host process")
    p.add_argument("--group", help="the session's group (default: the host's)")
    p.add_argument("--cwd", help="the folder it runs in, on the host (default: its home; a relative one is this folder's)")
    p.add_argument("--model")
    p.add_argument("--effort", help="Claude's effort (low … max), or a local model's thinking level (off … high)")
    p.add_argument("--mode", choices=[m for m, _ in MODES], default="default")
    p.add_argument("--title")
    p.add_argument("--system", help="more for claude's system prompt (--append-system-prompt)")
    p.add_argument("--add-dir", action="append", default=[], help="a folder it may use besides its own (again for more)")
    p.add_argument("--allow", action="append", default=[], help="a tool rule it needn't ask about: Bash(git:*)")
    p.add_argument("--deny", action="append", default=[], help="a tool rule it may not use")
    p.add_argument("--fork-from", help="a session whose conversation this one starts as a copy of")
    p.add_argument("--resume", metavar="SESSION", help="carry on this session (its id) instead of starting one")
    p.add_argument("--follow", action="store_true", help="keep streaming past the turn's end, till claude exits")
    p.add_argument("--partial", action="store_true", help="its replies as they're written too (stream_event lines, as "
                                                          "claude's --include-partial-messages)")
    p.add_argument("--input", action="store_true", help="stdin's lines are stream-json messages for claude")
    p.add_argument("--ask-timeout", type=float, help="seconds a permission waits, then the host answers it")
    p.add_argument("--ask-default", choices=["deny", "allow"], help="what answers it then (default deny)")
    args = p.parse_args()
    if not args.prompt and not args.input:
        p.error("a prompt, or --input")
    if args.follow and not args.input:
        p.error("--follow needs --input: claude goes on while it's followed, and only stdin's end can end it")
    if args.resume and (args.ask_timeout is not None or args.ask_default):
        p.error("--ask-timeout and --ask-default are a new session's: a resumed one keeps what it was started with")
    try:
        ch, info = hosts.reach(args.host, args.host_command, args.member)
    except OSError as e:
        print(f"baude-oneshot: {e}", file=sys.stderr)
        return 2
    ch.sock.settimeout(None)
    ch.keep_alive()
    answers = args.input  # (asks left to the host's timeout, or to stdin; else denied here)
    left_to_host = args.ask_timeout is not None and not args.input
    prompt, reqs, owed = args.prompt, itertools.count(2), [0]
    if args.resume:
        ch.send({"op": "attach", "session": args.resume, "live": True, "answers": answers, "req": 1})
    else:
        away = args.host or args.member
        cwd = args.cwd if not args.cwd or args.cwd.startswith(("/", "~")) or away else os.path.abspath(args.cwd)
        ch.send({"op": "start", "prompt": prompt or "", "cwd": cwd, "model": args.model, "effort": args.effort,
                 "mode": args.mode, "group": args.group, "title": args.title, "system": args.system,
                 "add_dirs": [os.path.abspath(os.path.expanduser(d)) if not away else d for d in args.add_dir],
                 "allowed_tools": args.allow, "disallowed_tools": args.deny, "fork_from": args.fork_from,
                 "ask_timeout": args.ask_timeout, "ask_default": args.ask_default, "attach": True,
                 "answers": answers, "req": 1})
        owed[0] += bool((prompt or "").strip())
        prompt = None  # (sent with it)
    sid, code = args.resume, None

    def to_claude(msg):
        if msg.get("type") == "user":
            owed[0] += 1  # (a turn of its own, whose result's waited for)
        ch.send({"op": "input", "session": sid, "msg": msg, "req": next(reqs)})

    def deny(request_id):
        to_claude({"type": "control_response", "response": {"subtype": "success", "request_id": request_id, "response": {
            "behavior": "deny", "message": "Nobody was there to ask (baude-oneshot, without --input)."}}})

    def read_stdin():
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                print(f"baude-oneshot: not JSON: {line[:80]}", file=sys.stderr)
                continue
            to_claude(msg)
        if args.follow:
            ch.close()  # (stdin's done: so's following it)
    for msg in ch:
        op = msg.get("op")
        if op == "error" and msg.get("req") is not None:  # (the start, the attach, or something sent: it went nowhere)
            print(f"baude-oneshot: {msg.get('error')}", file=sys.stderr)
            code = 2
            break
        if op in ("started", "attached") and msg.get("req") == 1:
            listed = msg.get("session") or {}
            sid = sid or listed.get("id")
            if op == "attached" and listed.get("state") in ("working", "waiting"):
                owed[0] += 1  # (a turn going on as it's attached: its result's not this one's)
            out({"type": "baude", "event": op, "session": {**listed, "id": sid},
                 "host": info.get("name"), "host_id": info.get("id")})
            for ask in msg.get("asks") or []:  # (asked before this attached: answered as below)
                if not answers and not left_to_host:
                    deny(ask.get("request_id"))
            if prompt:  # (--resume: what to say, now it's attached)
                to_claude({"type": "user", "message": {"role": "user", "content": prompt}, "parent_tool_use_id": None,
                           "session_id": ""})
            if args.input:
                threading.Thread(target=read_stdin, daemon=True, name="stdin").start()
            continue
        if op == "exited" and msg.get("session") == sid:
            out({"type": "baude", "event": "exited", "code": msg.get("code")})
            if code is None:  # (claude went before its turn's end: stopped, or it crashed)
                code = 1
            break
        if op != "claude" or msg.get("session") != sid:
            continue
        m = msg.get("msg") or {}
        if m.get("type") != "stream_event" or args.partial:
            out(m)
        if m.get("type") == "control_request" and (m.get("request") or {}).get("subtype") == "can_use_tool" \
                and not answers and not left_to_host:
            deny(m.get("request_id"))
        if m.get("type") == "result":
            owed[0] = max(0, owed[0] - 1)
            if owed[0] == 0:
                code = 1 if m.get("is_error") else 0
                if not args.follow:
                    break
    else:
        if code is None and not (args.follow and args.input):  # (the host went before the turn's end)
            print("baude-oneshot: the host process went before the turn ended", file=sys.stderr)
            code = 3
    ch.send({"op": "detach", "session": sid})
    ch.close()
    return code or 0

if __name__ == "__main__":
    sys.exit(main())
