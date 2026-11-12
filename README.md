# Baude

Baude is a GTK4 / libadwaita desktop app for [Claude Code](https://claude.com/claude-code). It runs the `claude` CLI
and shows the chat, its tool calls and every session on the machine.

![Baude after a finished turn](docs/screenshots/02-turn-complete-dark.png)

## Chat

| | |
|---|---|
| ![Markdown](docs/screenshots/30-markdown-gallery-light.png)<br>Markdown | ![Signal links](docs/screenshots/37b-signal-link-kinds-light.png)<br>Signal links |

## Tools and permissions

| | |
|---|---|
| ![Tool calls](docs/screenshots/29-tool-group-open-light.png)<br>Tool calls | ![Command prompt](docs/screenshots/10-permission-bash-light.png)<br>Command approval |
| ![Edit prompt](docs/screenshots/11-permission-edit-dark.png)<br>Edit approval | ![MCP prompt](docs/screenshots/14-permission-mcp-light.png)<br>MCP tool approval |

## Git and Kubernetes

| | |
|---|---|
| ![Git](docs/screenshots/38f-git-light.png)<br>Git history | ![Kubernetes](docs/screenshots/38e-kubernetes-logs-light.png)<br>Kubernetes pods and logs |

## Input and feedback

| | |
|---|---|
| ![Attachments](docs/screenshots/42-composer-attachments-light.png)<br>Attachments | ![Slash commands](docs/screenshots/43-slash-completer-light.png)<br>Slash commands |
| ![Send Feedback](docs/screenshots/92-feedback-with-capture-light.png)<br>Send Feedback | ![Ongoing Feedback](docs/screenshots/96-feedback-ready-light.png)<br>Ongoing Feedback |

## Running it

You need Python 3.11+, GTK 4 and libadwaita 1.5+ with PyGObject (on Debian/Ubuntu:
`python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`), and the `claude` CLI, signed in.

```bash
python3 baude.py             # in the current folder
python3 baude.py --install   # add Baude to your application menu
python3 record.py            # Record
```

## Sessions on the network

`baude_headless.py` is a machine's host process for Claude Code sessions: one for each user, which owns that user's
sessions there and their claude processes, for Baude to start, watch and steer from that machine or another. It needs
only Python, not GTK, and runs any model Baude can that its machine has: Claude's with `claude`, Apple's with
`apple-claude` once built, and pi's with `pi-claude` (`baude_headless.py models` lists them). It listens only on a Unix
socket in a folder of its user's own, with no port on the network. Baude on the same machine uses the socket, and reads
its sessions' transcripts where they are; Baude on another reaches it over ssh, as `ssh HOST baude_headless.py
connect`, which carries the protocol between ssh and the socket and starts the host process if it isn't running. So
ssh decides who may: Baude never asks for a password (`BatchMode`), and uses your keys or agent as `~/.ssh/config` has
them, with the host already in `known_hosts`. Put `baude_headless.py` on each host's `PATH`, or give Baude its path
there (right-click the host → Host Command…).

Heads start the host process when they need it, so there's nothing to run by hand. With no head attached it does
nothing: no timers, a session's claude stopped once it's been idle half an hour, and the process itself gone after four
hours with nothing running (`--idle-minutes`, `--exit-after-hours`). On macOS, at the screen, it's an item in the menu
bar, with no Dock icon: how many sessions it has and which are running, and Quit. Quitting it (or `baude_headless.py
quit`, or SIGTERM) stops its claudes, keeping their transcripts, so their sessions are resumed next time.

`baude_oneshot.py` starts one session in a host process, attaches, and streams what its claude says to stdout as JSON
lines, as `claude -p --output-format stream-json` does. The session goes on in the host process, so Baude lists it,
under its host and group, and can be opened there.

```bash
python3 baude_oneshot.py --cwd ~/src/app --model opus --effort high "Run the benchmarks"
python3 baude_oneshot.py --host lab@lab-mac.local --group Lab "Bisect the flaky login test"
python3 baude_oneshot.py --resume SESSION "Now fix it"   # its id: the first line's session.id
python3 baude_headless.py list --host lab@lab-mac.local
python3 baude_headless.py archive SESSION   # or unarchive, or delete (its transcript to the trash)
```

Its first line is `{"type": "baude", "event": "started", "session": {...}}`, with the session's `id`; then claude's
lines, till its turn's `result`. With `--input`, each line on stdin is a stream-json message for claude, a user message
or a `control_response` answering a permission it asks for, and `--follow` keeps it going till stdin closes; without
`--input`, what it asks is denied, or, with `--ask-timeout`, left to the host process to answer then. It takes
claude's per-session options too: `--system`, `--add-dir`, `--allow`, `--deny`, `--fork-from`. It exits 0, 1 if the
turn ended in an error or claude ended before it did, 2 if the host or the session couldn't be had, and 3 if the host
process went mid-turn.

Moving to them, from `baude_server.py`:

- `baude_server.py serve` is `baude_headless.py` (or nothing: a head starts it). It no longer takes `--port` or
  `--bind`, and wants no token.
- `baude_server.py start --prompt TEXT …` is `baude_oneshot.py … TEXT`, with the same options (`--ask-timeout` and
  `--ask-default` for a new session only). It waits for the turn and prints it, rather than printing the session's id
  and leaving: `baude_oneshot.py … TEXT | head -1` gives the id, and the turn's still seen to its end.
- `baude_server.py servers` is `baude_headless.py hosts`; `list`, `models` and `stop` are `baude_headless.py`'s, with
  `--host DEST` (an ssh destination) instead of `--server HOST:PORT`.
- A client of `baudelib/wire.py` connects with `wire.connect_local()` on the same machine, or `wire.connect_ssh(DEST)`
  from another, instead of `wire.connect(host, port)`: the protocol's the same. `baudelib.hosts.reach(DEST)` starts
  this machine's host process first if it isn't running. Or it runs `baude_oneshot.py` and reads its stdout.
- A client that isn't Python asks `baude_headless.py socket` where the socket is, rather than copying its rules
  (`$BAUDE_RUNTIME_DIR`, `$XDG_RUNTIME_DIR`, `$XDG_STATE_HOME` or `~/.local/state`, or `/tmp/baude-<uid>` for a path
  too long for a socket; `XDG_RUNTIME_DIR`, which comes first, can differ between a session at the screen and one over
  ssh: ask from where the client runs), or talks to `baude_headless.py connect`'s stdin and stdout.
- A client that attaches to a session but won't answer what it asks says so (`answers: false` on `attach` or
  `start`): the host's `ask_timeout` then runs out and `ask_default` answers, as if nobody were attached.
- `archive`, `unarchive` and `delete` clean up a client's sessions: archived ones are listed only to a subscriber
  that asks for them (`subscribe {archived: true}`); a deleted one's transcript goes to the trash once its claude has
  ended, as Baude's Move to Trash does with its own, and heads attached to it hear `deleted`.
- On another machine, put `baude_headless.py` on `PATH` where `baude_server.py` was, for ssh to find.

The sidebar lists every host's sessions: This Mac's own chats (Claude Code's and Claude Desktop's, and its host
process's) with other hosts'. ⋮ beside its search → Hosts and Groups pick which are listed: a click lists only that one,
and Shift or Ctrl (⌘) and a click adds or takes away one, as the Git page's branch list does. A group is the label a
session was started with. Right-click a host → New Session Here starts a session there with the top bar's model, effort
and mode, and a prompt and folder; on This Mac, its host process is started first if it isn't running.

Baude finds hosts three ways:
- **On the local network,** a host process started with `--advertise` (remembered from then on) says by multicast DNS
  (`_baude._tcp`) that Baude is on its host, and which user ssh would log in as. Baude lists it in ⋮ → Hosts as found,
  but doesn't reach it till you right-click it → Connect, since anyone on a network can say anything: then it's
  `ssh user@name.local`, with the host's key already in `known_hosts`. Nothing's said on the network unless asked.
- **Tailscale's peers** are tried too (`tailscale status --json`, where Tailscale's installed), one at a time, since
  multicast DNS doesn't cross a tailnet; one is listed once Baude answers there, and its key must be known already.
- **By hand,** ⋮ → Hosts → Add Host… (or `BAUDE_HOSTS=dest,…`) takes an ssh alias, a host or `user@host`.

`BAUDE_NETWORK=0` stops Baude looking.

Each host shares one ssh connection among Baude's sessions there (`ControlMaster`), so sshd's `MaxSessions` (10 by
default) is how many can be open at once. A host process started over ssh, by `connect`, has that ssh session's
environment (its `PATH`, and on macOS no keychain, so `claude` there may need signing in another way); one started at
the screen, by Baude there, has the screen's. A home folder shared between machines (NFS) shares their host processes'
state, which isn't supported.

## License

[MIT](LICENSE). The Kubernetes sidebar icons in `widgets.py` are Freelens' (MIT) and Material Icons (Apache-2.0).
