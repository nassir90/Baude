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

`baude_server.py` runs Claude Code sessions headless on a machine, for Baude to start, watch and steer from that machine
or another. It needs only Python, not GTK, and runs any model Baude can that its machine has: Claude's with `claude`,
Apple's with `apple-claude` once built, and pi's with `pi-claude` (`baude_server.py models` lists them). There's one for
each user on a machine, its host process. It listens only on a Unix socket in a folder of its user's own, with no port
on the network. Baude on the same machine uses the socket; Baude on another reaches it over ssh, as
`ssh HOST baude_server.py connect`, which carries the protocol between ssh and the socket and starts the host process if
it isn't running. So ssh decides who may: Baude never asks for a password (`BatchMode`), and uses your keys or agent as
`~/.ssh/config` has them, with the host already in `known_hosts`. Put `baude_server.py` on each host's `PATH`, or give
Baude its path there (right-click the host → Host Command…).

```bash
python3 baude_server.py --group Lab      # this machine's host process, its sessions in the group Lab
python3 baude_server.py start --prompt "Run the benchmarks" --cwd ~/src/app --model opus --effort high
python3 baude_server.py list --host lab@lab-mac.local
```

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
