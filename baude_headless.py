#!/usr/bin/env python3
"""baude-headless: this machine's host process for Claude Code sessions, which Baude and other heads (baude_oneshot.py,
a client of baudelib/wire.py's) start, watch and steer, here or from another machine. One for each user on a machine:
it owns that user's sessions there and their claude processes. On baudelib (see baudelib/server.py); no GTK needed.

    python3 baude_headless.py [serve] [--group NAME] [--name NAME] [--state DIR] [--idle-minutes N]
                                      [--exit-after-hours N] [--no-menu-bar]
                                      [--auto-update] [--follow-checkout]
    python3 baude_headless.py [--auto-update] connect   # for ssh: stdin and stdout to this machine's host process
    python3 baude_headless.py socket              # where this user's host process listens (or would), to connect to
    python3 baude_headless.py quit                # this machine's host process stopped (its sessions kept)
    python3 baude_headless.py list   [--archived] [--host DEST [--host-command CMD]]   # a host's sessions
    python3 baude_headless.py models [--host DEST [--host-command CMD]]   # the models it can run
    python3 baude_headless.py stop      SESSION [--host DEST [--host-command CMD]]   # its claude stopped
    python3 baude_headless.py archive   SESSION [...]   # stopped, and listed only with --archived
    python3 baude_headless.py unarchive SESSION [...]
    python3 baude_headless.py delete    SESSION [...]   # stopped, gone, its transcript moved to the trash
    python3 baude_headless.py link   DEST [--host-command CMD]   # this host linked to DEST's, both ways: the mesh
    python3 baude_headless.py unlink DEST|ID                    # that link let go of (or one straight to a member)
    python3 baude_headless.py deactivate DEST|ID   # a member not reached, nor let reach here, till it's activated
    python3 baude_headless.py activate   DEST|ID
    python3 baude_headless.py mesh                # the mesh's members, and the links this host makes
    python3 baude_headless.py tools --cwd DIR [--only Read,...]   # an agent's tools, as an MCP server on stdio
    python3 baude_headless.py mcp-relay --socket PATH RUN NAME TOKEN   # an agent's MCP server, from its caller
A command that takes --host takes --member ID instead: a member of the mesh, reached through this machine's host.

A session's prompt, folder, model and the rest come over the wire, in start: baude_oneshot.py starts one from the
command line. The host process listens only on a Unix socket in a folder of its user's own (no port on the network):
what runs as that user on the same machine may use it. Another machine reaches it over ssh, as
`ssh DEST baude_headless.py connect`, so ssh's keys say who may: put baude_headless.py on that machine's PATH (or give
Baude its path, per host). connect, and the commands here, start the host process if it isn't running.

A client on the same machine that isn't Python asks `baude_headless.py socket` where to connect, rather than working
it out: $BAUDE_RUNTIME_DIR/host.sock, else $XDG_RUNTIME_DIR/baude/host.sock, else $XDG_STATE_HOME/baude/run/host.sock
(~/.local/state/baude/run), or /tmp/baude-<uid>/host.sock where that path's too long for a socket. XDG_RUNTIME_DIR
can differ between a session at the screen and one over ssh, or be gone once a session ends: ask from the
environment the client runs in. It prints the path whether or not the host process is running; `connect`
starts it, and is the way for a client that would rather not find it at all.

While nobody's attached it does nothing: no timers, a claude stopped once it's been idle --idle-minutes, and the
process itself gone once nothing's run for --exit-after-hours (0: never), as a head starts it again. On macOS, at the
screen, it's an item in the menu bar (no Dock icon): what it's doing, and Quit. Quitting, or SIGTERM, stops its
claudes, their transcripts kept, so their sessions are resumed next time.

Linked to another machine's host process (link, or Add Host… in Baude), it's in a mesh with it (baudelib/mesh.py):
the link's used both ways, so that machine reaches this one through it too, and every host either is linked to joins
them. A member lists, starts and steers the others' chats, and runs their models, as a head on that machine does.

It runs a model as Baude does (baudelib/models.py): Claude's in BAUDE_CLAUDE's claude, else claude on PATH; Apple's in
apple-claude, once built; one on this machine in pi-claude, once pi and the model are here.

It keeps itself up to date from git once that's asked for: --auto-update, or Settings → Auto restart on update in this
machine's Baude (and never with BAUDE_UPDATE=0). Every CHECK_S (baudelib/selfupdate.py) its checkout's looked at:
fetched and fast-forwarded, as Baude's updater does, unless --follow-checkout (Baude starts this machine's so: Baude's
own updater moves the checkout, and it follows its HEAD). A new commit that it imports from, and it restarts onto it,
its sessions' claudes stopped (a turn going on is cut short, and marked so: cut), but not while any of them is running
a command (a Bash or PowerShell call, a background shell): its info says what it's waiting for (update) meanwhile.
"""
import argparse
import json
import os
import signal
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from baudelib import hosts, selfupdate, wire  # noqa: E402
from baudelib.server import AlreadyRunning, Server  # noqa: E402

READY_S = 2  # (a new version found: how often it's seen whether the commands it waits for are done)


def serve(args):
    # (SIGUSR1, a head's "listen again": ignored till it's listening, as it would end a process that isn't ready)
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    try:
        server = Server(state_dir=args.state, group=args.group, name=args.name, idle_s=args.idle_minutes * 60,
                        socket_path=args.socket, exit_s=args.exit_after_hours * 3600)
    except AlreadyRunning as e:
        sys.exit(f"baude-headless: {e}")
    print(json.dumps({"socket": server.socket_path, "id": server.me["id"], "group": server.me["group"]}), flush=True)
    if server.commit:  # (a git checkout: kept up to date, once that's asked for)
        threading.Thread(target=updater, args=(server, args), daemon=True, name="updater").start()
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
        if server.restarting:
            restart(server)
        return

    def ended():  # (shut down, by Quit, a signal, quit or a long idle: the process goes, AppKit's loop with it; by
        # an update, it starts again)
        server.closed.wait()
        if server.restarting:
            restart(server)
        os._exit(0)
    server.on_change = item.changed
    for fn in (server.serve_forever, ended):
        threading.Thread(target=fn, daemon=True, name=fn.__name__).start()
    item.run()


def updater(server, args):
    """The host process kept up to date (once it's asked for: selfupdate.host_enabled): every CHECK_S, a look at its
    checkout (selfupdate.look: fetched and fast-forwarded; --follow-checkout, only its HEAD as Baude leaves it), and a
    new commit that imports is restarted onto, its turns cut short, but not while a command runs: info's update says
    what it's waiting for, and it's tried again every READY_S. Said in its log as it goes."""
    root, started, ready, waited, looked = selfupdate.HERE, server.commit, None, None, 0.0
    while not server.stopped.wait(READY_S if ready else selfupdate.CHECK_S):
        if not selfupdate.host_enabled(args.auto_update):
            if ready:
                server.log(f"update to {ready[:12]} not waited for: updates turned off")
            ready = waited = None
            server.set_update(None)
            continue
        if ready and time.monotonic() - looked >= selfupdate.CHECK_S and \
                selfupdate.git(root, "rev-parse", "HEAD", timeout=5) != ready:
            ready = waited = None  # (moved on since: looked at again)
            server.set_update(None)
        if ready is None:
            ready, looked = selfupdate.look(started, "baude_headless", root, follow=args.follow_checkout), \
                time.monotonic()
            if ready is None:
                continue
            n = selfupdate.new_commits(started, ready, root)
            server.log(f"update: {ready[:12]}, {n} commit{'s' if n != 1 else ''} after {started[:12]}")
        waiting = server.shutdown(if_idle=True, restart=ready)
        if waiting is None:  # (shut down, to start again on it: serve's restart)
            return
        if waiting != waited:
            server.log(f"update to {ready[:12]} waiting for {len(waiting)} command{'s' if len(waiting) != 1 else ''}"
                       f": {'; '.join(w['what'] for w in waiting)}")
            waited = waiting
        server.set_update({"to": ready, "waiting": waiting})


def restart(server):
    """Shut down for an update (restarting): this process started again, as it was (its Python's flags, the same
    arguments), on the code checked out now. Its lock and socket are let go of already (shutdown's), and whatever
    else it has open is closed as it's started (Python opens nothing inheritable)."""
    argv = [sys.executable, *(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])[1:]]
    server.log(f"update: restarting onto {server.restarting[:12]}")
    try:  # (a head's "listen again" till the new one's listening: ignored, as serve does; kept so across exec)
        signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    except ValueError:  # (not the main thread: AppKit's, with the menu bar)
        pass
    for f in (sys.stdout, sys.stderr):
        try:
            f.flush()
        except (OSError, ValueError):
            pass
    try:
        os.execv(sys.executable, argv)
    except OSError as e:  # (gone, its Python moved: the next head starts one)
        server.log(f"couldn't start again ({e}): going")
        os._exit(1)


def connect(args):
    """ssh's end: its stdin and stdout carried to this machine's host process and back, started if it isn't running
    (keeping its checkout up to date itself, as there may be no Baude here to: --auto-update, or Baude's setting)."""
    if not hosts.start_here(follow_checkout=False, auto_update=args.auto_update):
        sys.exit(f"baude-headless: no host process here (see {hosts.log_path()})")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(wire.socket_path())
    wire.bridge(sock)


def ask(args, msg):
    try:
        ch, _info = hosts.reach(args.host, args.host_command, getattr(args, "member", None))
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
        c.add_argument("--member", help="a member of the mesh (its id, as `mesh` lists it), reached through this "
                                        "machine's host process")
        return c

    def serving(c):
        c.add_argument("--group", help="the group its sessions are in unless started in another (default: its name)")
        c.add_argument("--name", help="what heads call it (default: this machine's name)")
        c.add_argument("--state", help="where it keeps its sessions' settings (default: ~/.local/share/baude/server)")
        c.add_argument("--socket", help=argparse.SUPPRESS)  # (another socket than this user's one: a test's)
        c.add_argument("--idle-minutes", type=float, default=30, help="a session's claude stops after this long unused")
        c.add_argument("--exit-after-hours", type=float, default=4,
                       help="with nothing running and nobody attached this long, it goes (0: never)")
        # (said Baude was on this host by multicast DNS, once: taken and ignored, as one started with them restarts
        # with them onto an update)
        c.add_argument("--advertise", "--no-advertise", action="store_true", dest="advertise", help=argparse.SUPPRESS)
        c.add_argument("--no-menu-bar", action="store_true", help="on macOS, no item in the menu bar")
        c.add_argument("--auto-update", action="store_true",
                       help="keep itself up to date from git, as Baude's Auto restart on update does (default: as "
                            "this machine's Baude settings say; never with BAUDE_UPDATE=0)")
        c.add_argument("--follow-checkout", action="store_true",
                       help="updating, only follow what's checked out (which this machine's Baude keeps up to date), "
                            "never fetching or merging")
    serving(sub.add_parser("serve", help="the host process (the default)"))
    serving(p)
    sub.add_parser("connect", help="this machine's host process on stdin and stdout, for ssh (started if need be)")
    sub.add_parser("socket", help="the path of this user's host process's socket, to connect to (running or not)")
    sub.add_parser("quit", help="this machine's host process stopped, its claudes too (their sessions kept)")
    client("list", "a host's sessions").add_argument("--archived", action="store_true", help="archived ones too")
    client("models", "the models a host can run")
    link = sub.add_parser("link", help="this machine's host process linked to another's, over ssh, both ways: the mesh")
    link.add_argument("dest", help="where ssh goes: an alias in ~/.ssh/config, a host, or user@host")
    link.add_argument("--host-command", help="the host process there, if baude_headless.py isn't on its PATH")
    sub.add_parser("unlink", help="a link this machine's host process makes let go of").add_argument("dest")
    sub.add_parser("mesh", help="the mesh's members, and the links this machine's host process makes")
    tools = sub.add_parser("tools", help="the tools of an agent run on another host (Read, Write, Edit, Bash, Grep, "
                                         "Glob), here, as an MCP server on stdin and stdout")
    tools.add_argument("--cwd", required=True, help="the folder they work in")
    tools.add_argument("--only", help="those of them it has, by name, with commas (default: all)")
    relay = sub.add_parser("mcp-relay", help=argparse.SUPPRESS)  # (an agent run here's MCP server: its caller's)
    relay.add_argument("--socket", required=True)
    for name in ("run", "name", "token"):
        relay.add_argument(name)
    for name, help_ in (("deactivate", "a member (where a link goes, or its id) not reached from here, nor let reach "
                                       "here, till it's activated again"),
                        ("activate", "a member deactivated here taken up again")):
        sub.add_parser(name, help=help_).add_argument("target", help="where ssh goes (as linked), or its id")
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
    if args.command == "tools":
        from baudelib import hosttools
        return hosttools.serve(args.cwd, args.only.split(",") if args.only else None)
    if args.command == "mcp-relay":
        from baudelib import agentproxy
        return agentproxy.relay(args.socket, args.run, args.name, args.token)
    if args.command == "quit":
        try:
            ch, _info = wire.connect_local(timeout=3)
        except OSError:
            return print("no host process is running here")
        ch.send({"op": "quit", "req": 1})
        ch.reply()
        ch.close()
        return
    if args.command == "list":
        for s in [x for x in ask(args, {"op": "subscribe", "archived": args.archived})["sessions"] if not x.get("draft")]:
            print(f"{s['id']}  {s['state']:8}  {s.get('group') or '':12}  {s.get('model') or 'default':14}  "
                  f"{s.get('title') or ''}  ({s.get('cwd')})" + ("  [archived]" if s.get("archived") else ""))
        return
    if args.command == "models":
        for m in ask(args, {"op": "subscribe"})["server"].get("models") or []:
            print(f"{m['value']:24}  {m['displayName']}")
        return
    if args.command == "unlink":  # (a link made from here, or straight to a member found: by where, or its id)
        args.host, args.host_command = None, None
        said = ask(args, {"op": "unlink", **({"id": args.dest} if wire.is_id(args.dest) else
                                             {"where": {"via": "ssh", "dest": args.dest}})})
        return print(f"no longer linked to {args.dest}" if said.get("any") else f"no link to {args.dest} here")
    if args.command == "link":
        where = {"via": "ssh", "dest": args.dest, **({"command": args.host_command}
                                                      if getattr(args, "host_command", None) else {})}
        args.host, args.host_command = None, None
        ask(args, {"op": args.command, "where": where})
        return print(f"linking to {args.dest}: `{os.path.basename(sys.argv[0])} mesh` says how it goes")
    if args.command in ("deactivate", "activate"):
        args.host, args.host_command = None, None
        said = ask(args, {"op": args.command, **({"id": args.target} if wire.is_id(args.target) else {}),
                          **({"where": {"via": "ssh", "dest": args.target}} if wire.is_dest(args.target) else {})})
        return print(f"{args.target} {args.command}d" if said.get("any") else f"no member or link {args.target} here")
    if args.command == "mesh":
        args.host, args.host_command = None, None
        mesh = ask(args, {"op": "subscribe"})["server"].get("mesh") or {}
        for m in mesh.get("members") or []:
            print(f"{m['id']}  {m['name']:16}  " + (f"through {m['through']}" if m.get("through") else "linked") +
                  "".join(f"  [{p['name']}: {len(p.get('models') or [])} models]" for p in m.get("providers") or []))
        for x in mesh.get("links") or []:
            print(f"link to {wire.said_where(x['where'])}: {'deactivated' if x.get('active') is False else x['state']}"
                  + (f" ({x['error']})" if x.get("error") else ""))
        for x in mesh.get("found") or []:
            print(f"{x['id']}  link straight to {x['name']} (found through the mesh): {x['state']}"
                  + (f" ({x['error']})" if x.get("error") else ""))
        for i in mesh.get("inactive") or []:
            print(f"{i}  deactivated")
        if not mesh.get("members") and not mesh.get("links") and not mesh.get("inactive") and not mesh.get("found"):
            print("in no mesh: link to another host with `link DEST`")
        return
    if args.command in ("stop", "archive", "unarchive"):
        ask(args, {"op": args.command, "session": args.session})
    if args.command == "delete":
        for path in ask(args, {"op": "delete", "session": args.session}).get("trashed") or []:
            print(f"moved to the trash: {path}")


if __name__ == "__main__":
    main()
