# Baude

A GTK4 / libadwaita desktop app for [Claude Code](https://claude.com/claude-code). It runs one `claude -p` process
and shows the chat, its tool calls and prompts, every session on the machine, and the subagents and background tasks.
[Record](#record), beside it, reads a chat with you.

![baude: a finished turn](docs/screenshots/02-turn-complete-dark.png)

## Running it

You need Python 3.11+, GTK 4 and libadwaita 1.5+ with PyGObject (on Debian/Ubuntu:
`python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`), and the `claude` CLI, signed in (`claude`, then `/login`).

```bash
python3 baude.py                       # in the current folder
python3 baude.py --cwd ~/projects/app  # somewhere else
python3 baude.py --install             # add Baude to your application menu
python3 record.py                      # Record
python3 record.py --install
```

## Features

- **Sessions:** every Claude Code session on the machine (Baude's, the terminal's, Claude Desktop's), by folder or by day.
- **Status dots:** a session is working, waiting for you, or done.
- **Forks** sit under the session they came from.
- **Live tail:** a session running elsewhere is followed as it's written.
- **Tool cards:** each call with its command and output. A run of calls folds into one line.
- **Permission prompts** in Manual mode: a command, an edit as a diff, an MCP tool by its name.
- **Images** inline, opened full-window with zoom, ←/→ through all of them.
- **Markdown:** headings, lists, code with a copy button, tables that scroll sideways.
- **Paths** that exist (`src/app.py:42`) open in your editor at the line.
- **Selection** runs across the whole chat, and copies as it reads.
- **Signal** links the names in Claude's replies: files, commits, env vars, routes, Kubernetes objects, tables, tools.
- **Code tabs:** a file opens beside the chat, highlighted, with find.
- **Files:** the chat's folder as a tree.
- **Database schemas:** Postgres and ArangoDB in Docker or a local cluster, drawn as a diagram. Read-only.
- **Git:** a branch's history, each commit's card, its pull requests. Read-only.
- **Kubernetes:** a cluster view like Freelens, with pod logs. It changes nothing but a pod you delete.
- **⌘K** (Ctrl+K) searches every name the folder has and opens it.
- **Input box:** a live markdown preview, attachments, `/` for commands and skills.
- **Model, effort and permission mode** change live. The status bar shows cost, tokens and context.
- **Side panel:** subagents (nested too), processes, artifacts, files, git and Signal.
- **`/btw <question>`** asks on the side, mid-turn, and saves nothing.
- **`/login`** and **`/logout`** from the chat.
- **Artifacts** read as their title and are listed in the side panel.
- **Send Feedback** (⌘.) takes a note and marked screen captures, and files it as a GitHub issue.
- **Ongoing Feedback:** the issues and where each stands. *Fix here* has claude fix one and open a PR.
- **Text size:** Ctrl +/−/0 zooms the chat. Full-width text drops the reading width.
- **Updates:** Baude pulls its checkout and restarts onto the new version once you're idle.
- **Restart Baude** brings every window back as it was, drafts too.
- **Read-only session** hides the input box for a chat you only watch.
- **Settings** (Ctrl+, or ⌘,) are kept in `~/.config/baude/settings.json`.
- **Keys:** Enter sends, Shift+Enter is a new line, Esc stops, Ctrl+N is a new session, Ctrl+B shows the sidebar, Ctrl+Alt+B the side panel.

## Sessions

The dot on a row: working, waiting for your answer, done. Search filters every folder. Forks sit under the session
they came from, a session running elsewhere is followed live, and folders fold. A resumed session comes back with its
images.

## Tool calls and permissions

Calls in a row fold into one line. Long output scrolls in a pane of its own:

| | |
|---|---|
| ![collapsed group](docs/screenshots/28-tool-group-light.png) | ![opened group](docs/screenshots/29-tool-group-open-light.png) |
| ![command and output](docs/screenshots/21-output-text-light.png) | ![long output](docs/screenshots/21b-output-long-light.png) |

A background task that ends is one line.

In Manual mode each call asks first:

![Bash permission](docs/screenshots/10-permission-bash-light.png)

| | |
|---|---|
| ![Edit diff](docs/screenshots/11-permission-edit-dark.png) | ![MCP tool](docs/screenshots/14-permission-mcp-light.png) |

## Images

![an image Read](docs/screenshots/25-image-read-light.png)

Click one for the full-window view. Right-click to copy, open or show in folder:

| | |
|---|---|
| ![lightbox](docs/screenshots/26-image-viewer-light.png) | ![right-click menu](docs/screenshots/27-image-menu-light.png) |

## Markdown, tables and links

![markdown gallery](docs/screenshots/30-markdown-gallery-light.png)

| | |
|---|---|
| ![small table](docs/screenshots/33-table-small-dark.png) | ![wide table](docs/screenshots/35-table-wide-light.png) |

Web addresses, artifact links and paths that exist are links:

![auto links](docs/screenshots/36-auto-links-light.png)

## Signal

Each kind of link has its logo, and a Claude Code tool's link opens its card:

![every kind of link](docs/screenshots/37b-signal-link-kinds-light.png)

The Signal page, and a name's popover:

| | |
|---|---|
| ![signal](docs/screenshots/37-signal-light.png) | ![signal popover](docs/screenshots/38-signal-popover-dark.png) |

## Code and database schemas

![code view](docs/screenshots/38b-code-view-light.png)

![schema diagram](docs/screenshots/38c-schema-diagram-light.png)

## Git

The history, a commit's card, and GitHub signed in and out:

| | |
|---|---|
| ![git](docs/screenshots/38f-git-light.png) | ![a commit's card](docs/screenshots/38g-git-commit-dark.png) |
| ![signed in: the branch's pull request](docs/screenshots/38h-git-signed-in-light.png) | ![signed out](docs/screenshots/38i-git-signed-out-light.png) |

## Kubernetes

| | |
|---|---|
| ![kubernetes](docs/screenshots/38e-kubernetes-light.png) | ![a pod's logs](docs/screenshots/38e-kubernetes-logs-light.png) |

## The input box

| | |
|---|---|
| ![composer markdown](docs/screenshots/40-composer-markdown-light.png) | ![code fence](docs/screenshots/41-composer-code-fence-dark.png) |

Attach with **+**, paste or drag and drop:

![attachments](docs/screenshots/42-composer-attachments-light.png)

**/** lists the CLI's commands and skills:

| | |
|---|---|
| ![slash commands](docs/screenshots/43-slash-completer-light.png) | ![filtered](docs/screenshots/44-completer-filter-light.png) |

## Models, effort and context

The model, effort and permission mode change live. A model without effort says so, and the status bar warns as the
context fills.

## Background tasks, subagents and the side panel

A background command shows while it runs and when it finishes. The side panel lists processes, subagents with their
transcripts (subagents of a subagent too), side questions (`/btw`), and a tailed session's background tasks.

## Artifacts

A published artifact reads as its title, links to artifacts in a reply open them, and the side panel lists them all.

## Feedback

A capture, and regions marked on it:

| | |
|---|---|
| ![with a capture](docs/screenshots/92-feedback-with-capture-light.png) | ![marking regions](docs/screenshots/93-feedback-regions-dark.png) |

Ongoing Feedback: a fix ready for review, and feedback nobody's started on:

| | |
|---|---|
| ![a fix ready for review](docs/screenshots/96-feedback-ready-light.png) | ![feedback nobody's started on](docs/screenshots/96b-feedback-open-light.png) |

## Text size and width

Ctrl +/−/0 zooms the chat, and full-width text drops the reading width.

## Every screen

`tests/screenshots.sh` draws every screen, light and dark.

## Record

Record reads a chat with you. It's a Haiku agent with two limits, on purpose:

- **It explains only through your notes.** You keep the things you already understand as notes, in your own words.
  When a passage needs something you haven't written down, it lists it under *To learn*.
- **It remembers three messages.** Each reply sees your newest message and the two before it.

Baude's ☰ → *Open in Record…* opens Record on the chat you're in. Its window is laid out like Baude's: notebooks and
pages on the left, tabs of notes, chats and code in the middle, and the Record, Files and Git on the right.

- **Notebooks**, one per subject, each with its pages and its folders.
- **Notes** in Markdown, drawn as they read and saved as you type: `[[Page]]` links, sub-pages, Org mode's heading keys.
- **Chats** open in tabs, followed live, with their names linked as Signal links them.
- **Passages to ask about:** select one, or Ctrl-drag to pick several.
- **Citation links** (Ctrl+Shift+C) to a passage, to paste into your notes.
- **Unexplained nouns** in red: "the platform" when nothing in the chat or your notes introduced one.
- **`@`** in the notes finds files, chats, branches and the rest. **`#`** finds GitHub issues and pull requests.
- **Baude's viewers:** Files, Git, Kubernetes and the database diagram, without Baude running.
- **Export** a notebook, or all of them, to a zip of Markdown. A backup zip is made every 10 minutes.
- **Send Feedback**, **Ongoing Feedback**, updates and Restart Record, as in Baude.

## License

[MIT](LICENSE). The Kubernetes sidebar icons in `widgets.py` are Freelens' (MIT) and Material Icons (Apache-2.0).
