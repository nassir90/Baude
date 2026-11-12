#!/usr/bin/env python3
"""baude-server: Claude Code sessions run headless on this machine, started and steered from Baude, here or on another
machine (its sidebar lists them under their host and group: ⋮ → Hosts, Groups), on baudelib (see baudelib/server.py).
No GTK needed.

    python3 baude_server.py [serve] [--group NAME] [--name NAME] [--state DIR] [--idle-minutes N]
                                    [--advertise | --no-advertise]
    python3 baude_server.py connect                         # for ssh: stdin and stdout to this machine's host process
    python3 baude_server.py servers                         # the hosts found: this machine's, and on the network's
    python3 baude_server.py list   [--host DEST [--host-command CMD]]   # a host's sessions
    python3 baude_server.py models [--host DEST]            # the models it can run: Claude's, Apple's, its own
    python3 baude_server.py start  --prompt TEXT [--cwd DIR] [--model M] [--effort E] [--mode MODE]
                                   [--group G] [--system TEXT] [--add-dir DIR]... [--allow RULE]...
                                   [--deny RULE]... [--fork-from SESSION] [--ask-timeout S [--ask-default allow]]
                                   [--host DEST]
    python3 baude_server.py stop   SESSION [--host DEST]

There's one host process for each user on a machine. It listens only on a Unix socket in a folder of its user's own
(no port on the network): what runs as that user on the same machine may use it. Another machine reaches it over ssh,
as `ssh DEST baude_server.py connect`, so ssh's keys say who may: put baude_server.py on that machine's PATH (or give
Baude its path, per host). connect, and these commands, start the host process if it isn't running. Without --host
they use this machine's. It runs a model as Baude does (baudelib/models.py): Claude's in BAUDE_CLAUDE's claude, else
claude on PATH; Apple's in apple-claude, once built; one on this machine in pi-claude, once pi and the model are here.
"""
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from baudelib import wire  # noqa: E402
from baudelib.claude import MODES  # noqa: E402
from baudelib.discovery import Browser  # noqa: E402
from baudelib.server import AlreadyRunning, Server, state_home  # noqa: E402


def serve(args):
    try:
        server = Server(state_dir=args.state, group=args.group, name=args.name, advertise=args.advertise,
                        idle_s=args.idle_minutes * 60, socket_path=args.socket)  # (--advertise, --no-advertise: kept)
        if os.environ.get("BAUDE_NETWORK", "1") == "0" and not args.advertise:
            server.advertise = False  # (nothing said this time, as Baude then looks for nothing there; not kept)
    except AlreadyRunning as e:
        sys.exit(f"baude-server: {e}")
    print(json.dumps({"socket": server.socket_path, "id": server.me["id"], "group": server.me["group"]}), flush=True)

    def quit_(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, quit_)
    signal.signal(signal.SIGINT, quit_)
    server.serve_forever()
    server.closed.wait(10)  # (serving's stopped: shutdown, on its thread, lets go of the socket before this exits)


def start_here(path=None, wait=20.0):
    """This machine's host process, started if it isn't running (in a process of its own, which goes on after this
    one: its output in its state folder's host.log). False if it isn't there after wait seconds."""
    path = path or wire.socket_path()
    if wire.wait_for_socket(path, 0.2):
        return True
    os.makedirs(state_home(), exist_ok=True)
    with open(os.path.join(state_home(), "host.log"), "ab") as log:
        subprocess.Popen([sys.executable, os.path.realpath(__file__), "serve"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
    return wire.wait_for_socket(path, wait)


def connect(_args):
    """ssh's end: its stdin and stdout carried to this machine's host process and back, started if it isn't running."""
    if not start_here():
        sys.exit(f"baude-server: no host process here (see {os.path.join(state_home(), 'host.log')})")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(wire.socket_path())
    wire.bridge(sock)


def find_servers(wait=2.0):
    """The hosts found: this machine's (its socket), and those on the network that say Baude is on them (multicast
    DNS), each {name, dest, words}."""
    found = {}
    try:
        ch, info = wire.connect_local(timeout=2)
        ch.close()
        found[info.get("id")] = {"name": f"{info.get('name')} (this machine)", "dest": None,
                                 "words": {"id": info.get("id"), "group": info.get("group")}}
    except OSError:
        pass

    def seen(s):
        user, host = s["words"].get("user"), s.get("target") or s["host"]
        found.setdefault(s["words"].get("id") or s["instance"], {
            "name": s["instance"].split(".")[0], "dest": f"{user}@{host}" if user else host, "words": s["words"]})
    browser = Browser(seen).start()
    time.sleep(wait)
    browser.stop()
    return list(found.values())


def channel(args):
    if args.host:
        return wire.connect_ssh(args.host, args.host_command)
    if not start_here():
        sys.exit("baude-server: no host process here, and it couldn't be started")
    return wire.connect_local()


def ask(args, msg):
    try:
        ch, _info = channel(args)
    except OSError as e:
        sys.exit(f"baude-server: {e}")
    ch.send({**msg, "req": 1})
    reply = ch.reply()
    ch.close()
    if not reply or reply.get("op") == "error":
        sys.exit(f"baude-server: {(reply or {}).get('error') or 'no answer'}")
    return reply


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command")

    def client(name, help_):
        c = sub.add_parser(name, help=help_)
        c.add_argument("--host", help="where ssh reaches another machine's host process: an alias in ~/.ssh/config, a "
                                      "host, or user@host (default: this machine's)")
        c.add_argument("--host-command", help="the host process there, if baude_server.py isn't on its PATH")
        if name == "start":
            c.add_argument("--group", help="the session's group")
        return c

    def serving(c):
        c.add_argument("--group", help="the group its sessions are in unless started in another (default: its name)")
        c.add_argument("--name", help="what it's called on the network (default: this machine's name)")
        c.add_argument("--state", help="where it keeps its sessions' settings (default: ~/.local/share/baude/server)")
        c.add_argument("--socket", help=argparse.SUPPRESS)  # (another socket than this user's one: a test's)
        c.add_argument("--idle-minutes", type=float, default=30, help="a session's claude stops after this long unused")
        said = c.add_mutually_exclusive_group()
        said.add_argument("--advertise", action="store_const", const=True, dest="advertise",
                          help="say on the local network that Baude is on this host, and which user ssh would log in "
                               "as (multicast DNS); remembered (default: not said)")
        said.add_argument("--no-advertise", action="store_const", const=False, dest="advertise",
                          help="don't say it on the network any more")
    serving(sub.add_parser("serve", help="serve sessions (the default)"))
    serving(p)
    sub.add_parser("connect", help="this machine's host process on stdin and stdout, for ssh (started if need be)")
    sub.add_parser("servers", help="the hosts found: this machine's, and those on the network")
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
    if args.command == "connect":
        return connect(args)
    if args.command == "servers":
        for s in find_servers():
            print(f"{s['name']}  {s['dest'] or '-'}  group={s['words'].get('group')}  id={s['words'].get('id')}")
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
