#!/usr/bin/env python3
"""baude-headless: this machine's host process for Claude Code sessions, which Baude and other heads (baude_oneshot.py,
a client of baudelib/wire.py's) start, watch and steer, here or from another machine. One for each user on a machine:
it owns that user's sessions there and their claude processes. On baudelib (see baudelib/server.py); no GTK needed.

    python3 baude_headless.py [serve] [--group NAME] [--name NAME] [--state DIR] [--idle-minutes N]
                                      [--exit-after-hours N] [--advertise | --no-advertise] [--no-menu-bar]
    python3 baude_headless.py connect             # for ssh: stdin and stdout to this machine's host process
    python3 baude_headless.py socket              # where this user's host process listens (or would), to connect to
    python3 baude_headless.py quit                # this machine's host process stopped (its sessions kept)
    python3 baude_headless.py hosts               # the hosts found: this machine's, and on the network's
    python3 baude_headless.py list   [--archived] [--host DEST [--host-command CMD]]   # a host's sessions
    python3 baude_headless.py models [--host DEST [--host-command CMD]]   # the models it can run
    python3 baude_headless.py stop      SESSION [--host DEST [--host-command CMD]]   # its claude stopped
    python3 baude_headless.py archive   SESSION [...]   # stopped, and listed only with --archived
    python3 baude_headless.py unarchive SESSION [...]
    python3 baude_headless.py delete    SESSION [...]   # stopped, gone, its transcript moved to the trash

A session's prompt, folder, model and the rest come over the wire, in start: baude_oneshot.py starts one from the
command line. The host process listens only on a Unix socket in a folder of its user's own (no port on the network):
what runs as that user on the same machine may use it. Another machine reaches it over ssh, as
`ssh DEST baude_headless.py connect`, so ssh's keys say who may: put baude_headless.py on that machine's PATH (or give
Baude its path, per host). connect, and the commands here, start the host process if it isn't running.

A client on the same machine that isn't Python asks `baude_headless.py socket` where to connect, rather than working
it out: $BAUDE_RUNTIME_DIR/host.sock, else $XDG_RUNTIME_DIR/baude/host.sock, else ~/.local/state/baude/run/host.sock
(XDG_RUNTIME_DIR can differ between a session at the screen and one over ssh, or be gone once a session ends: ask
from the environment the client runs in). It prints the path whether or not the host process is running; `connect`
starts it, and is the way for a client that would rather not find it at all.

While nobody's attached it does nothing: no timers, a claude stopped once it's been idle --idle-minutes, and the
process itself gone once nothing's run for --exit-after-hours (0: never), as a head starts it again. On macOS, at the
screen, it's an item in the menu bar (no Dock icon): what it's doing, and Quit. Quitting, or SIGTERM, stops its
claudes, their transcripts kept, so their sessions are resumed next time.

It runs a model as Baude does (baudelib/models.py): Claude's in BAUDE_CLAUDE's claude, else claude on PATH; Apple's in
apple-claude, once built; one on this machine in pi-claude, once pi and the model are here.
"""
import argparse
import json
import os
import signal
import socket
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from baudelib import hosts, wire  # noqa: E402
from baudelib.server import AlreadyRunning, Server  # noqa: E402


def serve(args):
    # (SIGUSR1, a head's "listen again": ignored till it's listening, as it would end a process that isn't ready)
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    try:
        server = Server(state_dir=args.state, group=args.group, name=args.name, advertise=args.advertise,
                        idle_s=args.idle_minutes * 60, socket_path=args.socket,  # (--advertise, --no-advertise: kept)
                        exit_s=args.exit_after_hours * 3600)
        if os.environ.get("BAUDE_NETWORK", "1") == "0" and not args.advertise:
            server.advertise = False  # (nothing said this time, as Baude then looks for nothing there; not kept)
    except AlreadyRunning as e:
        sys.exit(f"baude-headless: {e}")
    print(json.dumps({"socket": server.socket_path, "id": server.me["id"], "group": server.me["group"]}), flush=True)
    item = None
    if not args.no_menu_bar:
        from baudelib import menubar
        if menubar.can_show():
            try:
                item = menubar.MenuBar(lambda: server.listing(), lambda: threading.Thread(
                    target=server.shutdown, daemon=True, name="quit").start())
            except Exception as e:  # (no window server after all, say: served without it)
                print(f"baude-headless: no menu bar item: {e}", file=sys.stderr)
    # signals, from the C handler, through a pipe read on a thread of its own: AppKit's run loop may have the main
    # thread, where Python's own handlers wait to run, and nothing's blocked that claude's children would inherit
    sigs_r, sigs_w = os.pipe()
    os.set_blocking(sigs_w, False)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
        signal.signal(sig, lambda *_: None)
    signal.set_wakeup_fd(sigs_w)

    def signals():
        while True:
            for n in os.read(sigs_r, 64):
                if n == signal.SIGUSR1:  # (a head found it running and its socket gone: hosts.start_here)
                    server.still_bound()
                elif n in (signal.SIGTERM, signal.SIGINT):
                    server.shutdown()
    threading.Thread(target=signals, daemon=True, name="signals").start()
    if item is None:
        server.serve_forever()
        server.closed.wait(10)  # (serving's stopped: shutdown, on its thread, lets go of the socket before this exits)
        return

    def ended():  # (shut down, by Quit, a signal, quit or a long idle: the process goes, AppKit's loop with it)
        server.closed.wait()
        os._exit(0)
    server.on_change = item.changed
    for fn in (server.serve_forever, ended):
        threading.Thread(target=fn, daemon=True, name=fn.__name__).start()
    item.run()


def connect(_args):
    """ssh's end: its stdin and stdout carried to this machine's host process and back, started if it isn't running."""
    if not hosts.start_here():
        sys.exit(f"baude-headless: no host process here (see {hosts.log_path()})")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(wire.socket_path())
    wire.bridge(sock)


def ask(args, msg):
    try:
        ch, _info = hosts.reach(args.host, args.host_command)
    except OSError as e:
        sys.exit(f"baude-headless: {e}")
    ch.send({**msg, "req": 1})
    reply = ch.reply()
    ch.close()
    if not reply or reply.get("op") == "error":
        sys.exit(f"baude-headless: {(reply or {}).get('error') or 'no answer'}")
    return reply


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command")

    def client(name, help_):
        c = sub.add_parser(name, help=help_)
        c.add_argument("--host", help="where ssh reaches another machine's host process: an alias in ~/.ssh/config, a "
                                      "host, or user@host (default: this machine's)")
        c.add_argument("--host-command", help="the host process there, if baude_headless.py isn't on its PATH")
        return c

    def serving(c):
        c.add_argument("--group", help="the group its sessions are in unless started in another (default: its name)")
        c.add_argument("--name", help="what it's called on the network (default: this machine's name)")
        c.add_argument("--state", help="where it keeps its sessions' settings (default: ~/.local/share/baude/server)")
        c.add_argument("--socket", help=argparse.SUPPRESS)  # (another socket than this user's one: a test's)
        c.add_argument("--idle-minutes", type=float, default=30, help="a session's claude stops after this long unused")
        c.add_argument("--exit-after-hours", type=float, default=4,
                       help="with nothing running and nobody attached this long, it goes (0: never)")
        said = c.add_mutually_exclusive_group()
        said.add_argument("--advertise", action="store_const", const=True, dest="advertise",
                          help="say on the local network that Baude is on this host, and which user ssh would log in "
                               "as (multicast DNS); remembered (default: not said)")
        said.add_argument("--no-advertise", action="store_const", const=False, dest="advertise",
                          help="don't say it on the network any more")
        c.add_argument("--no-menu-bar", action="store_true", help="on macOS, no item in the menu bar")
    serving(sub.add_parser("serve", help="the host process (the default)"))
    serving(p)
    sub.add_parser("connect", help="this machine's host process on stdin and stdout, for ssh (started if need be)")
    sub.add_parser("socket", help="the path of this user's host process's socket, to connect to (running or not)")
    sub.add_parser("quit", help="this machine's host process stopped, its claudes too (their sessions kept)")
    sub.add_parser("hosts", help="the hosts found: this machine's, and those on the network")
    client("list", "a host's sessions").add_argument("--archived", action="store_true", help="archived ones too")
    client("models", "the models a host can run")
    for name, help_ in (("stop", "a session's claude stopped (it's resumed when it's next written to)"),
                        ("archive", "a session's claude stopped, and the session listed only when archived ones are"),
                        ("unarchive", "a session listed again"),
                        ("delete", "a session's claude stopped, the session gone, its transcript moved to the trash")):
        client(name, help_).add_argument("session")
    args = p.parse_args()

    if args.command in (None, "serve"):
        return serve(args)
    if args.command == "connect":
        return connect(args)
    if args.command == "socket":
        return print(wire.socket_path())
    if args.command == "quit":
        try:
            ch, _info = wire.connect_local(timeout=3)
        except OSError:
            return print("no host process is running here")
        ch.send({"op": "quit", "req": 1})
        ch.reply()
        ch.close()
        return
    if args.command == "hosts":
        for s in hosts.find():
            print(f"{s['name']}  {s['dest'] or '-'}  group={s['words'].get('group')}  id={s['words'].get('id')}")
        return
    if args.command == "list":
        for s in ask(args, {"op": "subscribe", "archived": args.archived})["sessions"]:
            print(f"{s['id']}  {s['state']:8}  {s.get('group') or '':12}  {s.get('model') or 'default':14}  "
                  f"{s.get('title') or ''}  ({s.get('cwd')})" + ("  [archived]" if s.get("archived") else ""))
        return
    if args.command == "models":
        for m in ask(args, {"op": "subscribe"})["server"].get("models") or []:
            print(f"{m['value']:24}  {m['displayName']}")
        return
    if args.command in ("stop", "archive", "unarchive"):
        ask(args, {"op": args.command, "session": args.session})
    if args.command == "delete":
        for path in ask(args, {"op": "delete", "session": args.session}).get("trashed") or []:
            print(f"moved to the trash: {path}")


if __name__ == "__main__":
    main()
