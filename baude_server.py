#!/usr/bin/env python3
"""baude-server: Claude Code sessions run headless on this machine, started and steered over the network from Baude
(its sidebar's ⋮ → Group lists the group they're in), on baudelib (see baudelib/server.py). No GTK needed.

    python3 baude_server.py [serve] [--group NAME] [--name NAME] [--port N] [--bind ADDR] [--state DIR]
                                    [--idle-minutes N] [--no-advertise]
    python3 baude_server.py servers                          # the servers on the network
    python3 baude_server.py list   [--server HOST:PORT]      # a server's sessions
    python3 baude_server.py models [--server HOST:PORT]      # the models it can run: Claude's, Apple's, this machine's
    python3 baude_server.py start  --prompt TEXT [--cwd DIR] [--model M] [--effort E] [--mode MODE]
                                   [--tag T]... [--group G] [--server HOST:PORT]
    python3 baude_server.py stop   SESSION [--server HOST:PORT]

Without --server, the commands use the one server found on the network (in --group, if given). It runs a model as
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
    print(json.dumps({"port": server.port, "id": server.me["id"], "group": server.me["group"]}), flush=True)

    def quit_(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, quit_)
    signal.signal(signal.SIGINT, quit_)
    server.serve_forever()


def find_servers(wait=2.0):
    found = {}
    browser = Browser(lambda s: found.__setitem__(s["words"].get("id") or s["instance"], s)).start()
    if browser is None:
        return []
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
    s.add_argument("--tag", action="append", default=[], help="a tag (again for more)")
    s.add_argument("--title")
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
                           "effort": args.effort, "mode": args.mode, "tags": args.tag, "group": args.group,
                           "title": args.title})
        print(reply["session"]["id"])
        return
    if args.command == "stop":
        ask(args, {"op": "stop", "session": args.session})


if __name__ == "__main__":
    main()
