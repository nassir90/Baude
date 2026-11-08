# Baude

Baude is a GTK4 / libadwaita desktop app for [Claude Code](https://claude.com/claude-code). It runs the `claude` CLI
and shows the chat, its tool calls and every session on the machine. [Record](#record) reads a chat with you.

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

## Code, databases, Git and Kubernetes

| | |
|---|---|
| ![Code view](docs/screenshots/38b-code-view-light.png)<br>Code view | ![Schema](docs/screenshots/38c-schema-diagram-light.png)<br>Database schema |
| ![Git](docs/screenshots/38f-git-light.png)<br>Git history | ![Kubernetes](docs/screenshots/38e-kubernetes-logs-light.png)<br>Kubernetes pods and logs |

## Input and feedback

| | |
|---|---|
| ![Attachments](docs/screenshots/42-composer-attachments-light.png)<br>Attachments | ![Slash commands](docs/screenshots/43-slash-completer-light.png)<br>Slash commands |
| ![Send Feedback](docs/screenshots/92-feedback-with-capture-light.png)<br>Send Feedback | ![Ongoing Feedback](docs/screenshots/96-feedback-ready-light.png)<br>Ongoing Feedback |

## Record

Record reads a chat with you. It's a Haiku agent that explains only through your own notes and remembers your last
three messages. Open it from Baude's ☰ → *Open in Record…*.

## Running it

You need Python 3.11+, GTK 4 and libadwaita 1.5+ with PyGObject (on Debian/Ubuntu:
`python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`), and the `claude` CLI, signed in.

```bash
python3 baude.py             # in the current folder
python3 baude.py --install   # add Baude to your application menu
python3 record.py            # Record
```

## License

[MIT](LICENSE). The Kubernetes sidebar icons in `widgets.py` are Freelens' (MIT) and Material Icons (Apache-2.0).
