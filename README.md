# Baude

A GTK4 / libadwaita desktop app for [Claude Code](https://claude.com/claude-code). It runs the `claude` CLI and shows
the chat, its tool calls and every session on the machine. [Record](#record), beside it, reads a chat with you.

![A finished turn](docs/screenshots/02-turn-complete-dark.png)

A finished turn: the reply in Markdown, a tool call, and what it cost.

## Tool calls and permissions

![Tool calls folded into one line, opened](docs/screenshots/29-tool-group-open-light.png)

Calls in a row fold into one line. Open it to see each call.

![A command to approve](docs/screenshots/10-permission-bash-light.png)

In Manual mode Baude asks before a command runs.

![An edit to approve](docs/screenshots/11-permission-edit-dark.png)

An edit to approve, as a diff.

![An MCP tool to approve](docs/screenshots/14-permission-mcp-light.png)

An MCP tool by its name, with its fields as rows.

## Replies

![Markdown](docs/screenshots/30-markdown-gallery-light.png)

Markdown: headings, lists, quotes, code with a copy button, and tables.

![Signal's links](docs/screenshots/37b-signal-link-kinds-light.png)

Signal links the names in a reply (files, commits, env vars, routes, Kubernetes objects, tables, tools), each with
its logo.

## Code, databases, Git and Kubernetes

![A file beside the chat](docs/screenshots/38b-code-view-light.png)

A file opens beside the chat, highlighted.

![A database schema](docs/screenshots/38c-schema-diagram-light.png)

A database drawn as a diagram, from Postgres or ArangoDB in Docker or a local cluster. Read-only.

![A branch's history](docs/screenshots/38f-git-light.png)

A branch's history in the side panel, and where it stands against main. Read-only.

![A cluster's pods and a pod's logs](docs/screenshots/38e-kubernetes-logs-light.png)

A cluster's pods, and a pod's logs.

## The input box

![Attachments](docs/screenshots/42-composer-attachments-light.png)

Attach images and files with +, paste or drag and drop.

![Slash commands](docs/screenshots/43-slash-completer-light.png)

/ lists the CLI's commands and skills.

## Feedback

![Send Feedback](docs/screenshots/92-feedback-with-capture-light.png)

Send Feedback (⌘.) takes a note and screen captures, and files it as a GitHub issue.

![Ongoing Feedback](docs/screenshots/96-feedback-ready-light.png)

Ongoing Feedback lists the issues. A fix that's ready shows before and after, to approve and merge.

## Record

Record reads a chat with you. It's a Haiku agent that explains only through your own notes, and remembers your last
three messages. Baude's ☰ → *Open in Record…* opens it on the chat you're in.

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
