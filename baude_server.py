#!/usr/bin/env python3
"""baude-server: Claude Code sessions run headless on this machine, started and steered over the network from Baude
(its sidebar lists them under their host and group: ⋮ → Hosts, Groups), on baudelib (see baudelib/server.py). No GTK
needed.

    python3 baude_server.py [serve] [--group NAME] [--name NAME] [--port N] [--bind ADDR] [--state DIR]
                                    [--idle-minutes N] [--no-advertise]
    python3 baude_server.py servers                          # the servers on the network
    python3 baude_server.py list   [--server HOST:PORT]      # a server's sessions
    python3 baude_server.py models [--server HOST:PORT]      # the models it can run: Claude's, Apple's, this machine's
    python3 baude_server.py start  --prompt TEXT [--cwd DIR] [--model M] [--effort E] [--mode MODE]
                                   [--group G] [--system TEXT] [--add-dir DIR]... [--allow RULE]...
                                   [--deny RULE]... [--fork-from SESSION] [--ask-timeout S [--ask-default allow]]
                                   [--server HOST:PORT]
    python3 baude_server.py stop   SESSION [--server HOST:PORT]

Without --server, the commands use the one server found on the network or on this machine (in --group, if given).
A server bound to this machine only (--bind 127.0.0.1) isn't said on the network, and wants a token: the one in its
file in ~/.local/share/baude/local-servers, which only its owner can read; these commands, and Baude, read it there. It runs a model as
Baude does (baudelib/models.py): Claude's in BAUDE_CLAUDE's claude, else claude on PATH; Apple's in apple-claude, once
built; one on this machine in pi-claude, once pi and the model are here. There's no sign-in: anyone who can reach the port can use it.
"""
import argparse
import json
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from baudelib import wire  # noqa: E402
from baudelib.claude import MODES  # noqa: E402
from baudelib.discovery import Browser  # noqa: E402
from baudelib.server import Server  # noqa: E402


def serve(args):
    server = Server(state_dir=args.state, group=args.group, name=args.name, host=args.bind, port=args.port,
                    advertise=not args.no_advertise, idle_s=args.idle_minutes * 60)
    print(json.dumps({"port": server.port, "id": server.me["id"], "group": server.me["group"],
                      "this_machine_only": server.local}), flush=True)

    def quit_(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, quit_)
    signal.signal(signal.SIGINT, quit_)
    server.serve_forever()


def find_servers(wait=2.0):
    """The servers on this machine (their files) and on the network (multicast DNS)."""
    found = {e["id"]: {"instance": f"{e.get('name')} (this machine)", "host": "127.0.0.1", "port": e["port"],
                       "words": {"id": e["id"], "group": e.get("group")}} for e in wire.local_servers() if e.get("id")}
    browser = Browser(lambda s: found.setdefault(s["words"].get("id") or s["instance"], s)).start()
    if browser is not None:
        time.sleep(wait)
        browser.stop()
    return list(found.values())


def server_address(args):
    if args.server:
        host, _, port = args.server.rpartition(":")
        return host or "127.0.0.1", int(port)
    found = find_servers()
    found = [s for s in found if args.group and s["words"].get("group") == args.group] or found
    if not found:
        sys.exit("no server found on the network (give one with --server HOST:PORT)")
    if len(found) > 1 and not args.group:
        print(f"{len(found)} servers found, using {found[0]['instance']} (--server or --group picks)", file=sys.stderr)
    return found[0]["host"], found[0]["port"]


def ask(args, msg):
    ch, _info = wire.connect(*server_address(args))
    ch.send({**msg, "req": 1})
    reply = ch.read()
    ch.close()
    if not reply or reply.get("op") == "error":
        sys.exit(f"baude-server: {(reply or {}).get('error') or 'no answer'}")
    return reply


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command")

    def client(name, help_):
        c = sub.add_parser(name, help=help_)
        c.add_argument("--server", help="HOST:PORT (else the one found on the network)")
        c.add_argument("--group", help="a server whose group this is, of those found; start: the session's group")
        return c

    def serving(c):
        c.add_argument("--group", help="the group its sessions are in unless started in another (default: its name)")
        c.add_argument("--name", help="what it's called on the network (default: this machine's name)")
        c.add_argument("--port", type=int, default=0, help="the port to serve on (default: any free one)")
        c.add_argument("--bind", default="0.0.0.0", help="the address to serve on (default: all)")
        c.add_argument("--state", help="where it keeps its sessions' settings (default: ~/.local/share/baude/server)")
        c.add_argument("--idle-minutes", type=float, default=30, help="a session's claude stops after this long unused")
        c.add_argument("--no-advertise", action="store_true", help="don't say it's here on the network (mDNS)")
    serving(sub.add_parser("serve", help="serve sessions (the default)"))
    serving(p)
    sub.add_parser("servers", help="the servers on the network")
    client("list", "a server's sessions")
    client("models", "the models a server can run")
    s = client("start", "start a session")
    s.add_argument("--prompt", required=True)
    s.add_argument("--cwd", help="the folder it runs in, on the server's machine (default: its home; a relative one is "
                                 "this folder's)")
    s.add_argument("--model")
    s.add_argument("--effort", help="Claude's effort (low … max), or a local model's thinking level (off … high)")
    s.add_argument("--mode", choices=[m for m, _ in MODES], default="default")
    s.add_argument("--title")
    s.add_argument("--system", help="more for claude's system prompt (--append-system-prompt)")
    s.add_argument("--add-dir", action="append", default=[], help="a folder it may use besides its own (again for more)")
    s.add_argument("--allow", action="append", default=[], help="a tool rule it needn't ask about: Bash(git:*) (again "
                                                                 "for more)")
    s.add_argument("--deny", action="append", default=[], help="a tool rule it may not use (again for more)")
    s.add_argument("--fork-from", help="a session of the server's whose conversation this one starts as a copy of")
    s.add_argument("--ask-timeout", type=float, help="seconds a permission waits while nobody's attached")
    s.add_argument("--ask-default", choices=["deny", "allow"], help="what answers it then (default deny)")
    st = client("stop", "stop a session's claude")
    st.add_argument("session")
    args = p.parse_args()

    if args.command in (None, "serve"):
        return serve(args)
    if args.command == "servers":
        for s in find_servers():
            print(f"{s['instance']}  {s['host']}:{s['port']}  group={s['words'].get('group')}  id={s['words'].get('id')}")
        return
    if args.command == "list":
        for s in ask(args, {"op": "subscribe"})["sessions"]:
            print(f"{s['id']}  {s['state']:8}  {s.get('group') or '':12}  {s.get('model') or 'default':14}  "
                  f"{s.get('title') or ''}  ({s.get('cwd')})")
        return
    if args.command == "models":
        for m in ask(args, {"op": "subscribe"})["server"].get("models") or []:
            print(f"{m['value']:24}  {m['displayName']}")
        return
    if args.command == "start":
        cwd = args.cwd if not args.cwd or args.cwd.startswith(("/", "~")) else os.path.abspath(args.cwd)
        reply = ask(args, {"op": "start", "prompt": args.prompt, "cwd": cwd, "model": args.model,
                           "effort": args.effort, "mode": args.mode, "group": args.group,
                           "title": args.title, "system": args.system,
                           "add_dirs": [os.path.abspath(os.path.expanduser(d)) for d in args.add_dir],
                           "allowed_tools": args.allow, "disallowed_tools": args.deny, "fork_from": args.fork_from,
                           "ask_timeout": args.ask_timeout, "ask_default": args.ask_default})
        print(reply["session"]["id"])
        return
    if args.command == "stop":
        ask(args, {"op": "stop", "session": args.session})


if __name__ == "__main__":
    main()
