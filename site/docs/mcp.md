# Baude's MCP server and wire API

Made by `baude-mcp docs` from the tools' definitions (`baudelib/meshtools.py`, `baude_mcp.py`) and `schema/`. Run it again after changing either.

## baude-mcp

`baude-mcp` is an MCP server on stdin and stdout. Any agent on a machine of the mesh can run it: it needs no Baude window and no GTK. It reaches the machine's host process (`baude-headless`), and through it every member of the mesh. Being in the mesh is being trusted: no tool asks first.

Installing Baude writes it to `~/.local/bin` with the other commands (`baude-headless install`, or an app's `--install`). To give it to Claude Code:

```sh
claude mcp add baude -- ~/.local/bin/baude-mcp
```

Options: `--socket PATH` reaches a host process listening on another socket; `--name NAME` is who it is: what it sends a chat (`message_chat`) is shown as from it, not from the chat's user, and the edits it proposes are said to be its (its MCP client's name if not given, else `baude-mcp`).

Each tool is one or two of the wire's requests, below: the notebook's host does the work. An edit it proposes to a page is kept on the notebook's host (`edit_propose`), and any Record showing that notebook, on that machine or another, shows it on its page with who proposed it, for you to accept or reject. Nothing is written to the page until you accept.

## Tools

### `hosts`

The mesh's hosts (this one first): each one's id, name, whether it's reachable now, and the models it can run (their values are what start_subagent's model takes).

### `start_subagent`

Start a new chat (a subagent) on a host of the mesh, with one of its models, give it a task and, unless wait is false, wait for its answer. It runs as a chat there (listed in Baude), in cwd on that host (its home if none's given), in Auto mode unless another's asked for (Auto at most); it can't ask you for permissions, so what it does ask is refused after a minute. Its id and its host's come back, to message it again (message_chat).

| Field | Type | Required | What it is |
|---|---|---|---|
| `prompt` | string | yes | The task, said in full: it doesn't see this chat |
| `host` | string |  | A host's id or name (hosts); this one if none |
| `model` | string |  | A model's value on that host (hosts) |
| `cwd` | string |  | The folder on that host it works in |
| `title` | string |  | Its title in the list |
| `mode` | `auto` \| `acceptEdits` \| `default` \| `plan` |  | Its permission mode (default: auto; a model with no modes runs in default) |
| `wait` | boolean |  | Wait for its answer (default true) |
| `timeout_s` | integer |  | How long to wait (default 600) |

### `chats`

The chats on a host (this one if none's given), newest first: each one's id, title, state, and its Bot's name if it's a Bot.

| Field | Type | Required | What it is |
|---|---|---|---|
| `host` | string |  | A host's id or name |
| `bots` | boolean |  | Only Bots |

### `read_chat`

What a chat on a host said last: its last messages, yours and its.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The chat's id (chats, start_subagent) |
| `host` | string |  | Its host's id or name; looked for on every host if none |
| `last` | integer |  | How many messages (default 6) |

### `message_chat`

Send a message to another chat or Bot on a host, as its user would, and unless wait is false wait for its answer. Only when the user asked for this.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes |  |
| `text` | string | yes | What to say, in full: it doesn't see this chat |
| `host` | string |  | Its host's id or name; looked for if none |
| `wait` | boolean |  | Wait for its answer (default true) |
| `timeout_s` | integer |  |  |

### `list_notebooks`

Record's notebooks on a host: each one's name and how many pages it has.

| Field | Type | Required | What it is |
|---|---|---|---|
| `host` | string |  | The host it's on, by its id or name (hosts); this one if none |

### `list_pages`

A notebook's pages, Home first: each one's name (a page under another is Parent:Child) and how many characters it has.

| Field | Type | Required | What it is |
|---|---|---|---|
| `host` | string |  | The host it's on, by its id or name (hosts); this one if none |
| `notebook` | string | yes | A notebook's name (list_notebooks) |

### `read_page`

A page's text, in Markdown ([[Page]] links to another page), and the edits waiting on it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `host` | string |  | The host it's on, by its id or name (hosts); this one if none |
| `notebook` | string | yes | A notebook's name (list_notebooks) |
| `page` | string | yes | Its name (list_pages) |

### `propose_edit`

Propose a change to a page: old_text (copied exactly from the page, there once) replaced with new_text; an empty old_text adds new_text at the page's end. It waits on the notebook's host for the user, who accepts or rejects it in Record: nothing's written until they accept. A change to text one of the edits waiting puts in or replaces revises that edit (it's yours then).

| Field | Type | Required | What it is |
|---|---|---|---|
| `host` | string |  | The host it's on, by its id or name (hosts); this one if none |
| `notebook` | string | yes | A notebook's name (list_notebooks) |
| `page` | string | yes |  |
| `old_text` | string |  |  |
| `new_text` | string | yes |  |

### `list_edits`

The edits waiting for the user on a notebook's pages, each as its page has its passage now and with who proposed it (by: yours, Meta's, another agent's); with all, those accepted, rejected or withdrawn too.

| Field | Type | Required | What it is |
|---|---|---|---|
| `host` | string |  | The host it's on, by its id or name (hosts); this one if none |
| `notebook` | string | yes | A notebook's name (list_notebooks) |
| `page` | string |  |  |
| `all` | boolean |  | Decided ones too |

### `revise_edit`

Change an edit that's waiting: its new_text, and the passage it replaces if old_text is given. It stays one edit, yours now.

| Field | Type | Required | What it is |
|---|---|---|---|
| `host` | string |  | The host it's on, by its id or name (hosts); this one if none |
| `notebook` | string | yes | A notebook's name (list_notebooks) |
| `id` | string | yes | The edit's (list_edits) |
| `new_text` | string | yes |  |
| `old_text` | string |  |  |

### `withdraw_edit`

Take back an edit that's waiting.

| Field | Type | Required | What it is |
|---|---|---|---|
| `host` | string |  | The host it's on, by its id or name (hosts); this one if none |
| `notebook` | string | yes | A notebook's name (list_notebooks) |
| `id` | string | yes |  |

## The wire

Baude's apps, `baude-mcp` and the phone talk to a host process in JSON, one object a line, each with an `op`. On the same machine, connect to the Unix socket `baude-headless socket` prints; from another, run `ssh HOST ~/.local/bin/baude-headless connect` and talk to its stdin and stdout. A request carries a `req` number of the client's choosing, and its answer carries it back; anything wrong is an `error` with that `req`. `hello` comes first on every connection.

### Requests

#### `activate` (Activate)

A member taken up again.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string |  | Its id. |
| `where` | Where |  | Where a link goes. |

#### `agent` (Agent)

A model of this machine's, run here by its program (apple-claude, pi-claude) as an agent for a chat elsewhere (agentproxy), its tools there.

| Field | Type | Required | What it is |
|---|---|---|---|
| `model` | string |  | Its model here. |
| `mode` | string/null |  | Its permission mode. |
| `effort` | string/null |  | Its effort. |
| `resume` | string/null |  | The session it resumes. |
| `system_prompt` | string/null |  | Its system prompt. |
| `persist` | boolean |  | Its session's kept. |
| `append_system_prompt` | string/null |  | More for its system prompt (a Bot's: who it is, its memory). |
| `servers` | array of string |  | Its MCP servers, run on the caller's side. |
| `cwd` | string |  | The chat's folder, on the caller's side. |
| `sessions` | object |  | pi's session's files, for pi to take it up from. |

#### `always_allow` (AlwaysAllow)

Tools allowed whenever a session's agent asks for them, a head attached or not (kept in memory).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `tools` | array of string |  | These in place of any there were. |
| `add` | array of string |  | These added to them. |

#### `archive` (Archive)

A session archived: its agent stopped, and it's listed only to those subscribed with archived.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `attach` (Attach)

This connection attached to a session.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `live` | boolean |  | claude, exited and deleted as they come. |
| `answers` | boolean |  | (default true; kept for the connection, its last attach's or start's) false for a head that won't answer what the session asks: it isn't counted as there to answer. |
| `app_tools` | boolean |  | The session's app_tools (start's) carried to this connection from now. |
| `always_allow` | array of string |  | Tools added to those allowed whenever it asks. |
| `env` | object |  | As start's. |

#### `bot` (Bot)

A session made a Bot, or its name or picture changed; bot null, a chat again (its memory and picture kept). Its agent's told who it is from its next start.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `bot` | BotFields? |  | The Bot. |

#### `bot_picture` (BotPicture)

A Bot's picture.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `catalogue` (Catalogue)

What @ lists for one of its sessions' folders, made there (lookup.items: its files, branches, Kubernetes objects, what's running on that machine, its pull requests and issues): what there is, at once; waited for while it's made if there's none yet, or with wait.

| Field | Type | Required | What it is |
|---|---|---|---|
| `cwd` | string |  | The folder. |
| `wait` | boolean |  | Wait for it. |

#### `close` (Close)

A tunnel closed, from either end; or (agent) the program's stdin closed.

| Field | Type | Required | What it is |
|---|---|---|---|
| `t` | integer |  | The tunnel, by the number of the end that opened it (this end's). |
| `r` | integer |  | The tunnel, by the number of the end that opened it (the other end's). |
| `error` | string |  | Why. |

#### `databases` (Databases)

The running databases, read-only: their servers and databases (servers), one database's schema (schema), a table's first rows (rows). Secret-looking columns are masked here.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | `servers` \| `schema` \| `rows` |  | What to read. |
| `container` | string |  | schema, rows: the server's container. |
| `database` | string |  | schema, rows: the database. |
| `schema` | string |  | rows: the table's schema. |
| `table` | string |  | rows: the table. |

#### `deactivate` (Deactivate)

A member deactivated here: its links let go of and not made, its joins and tunnels refused.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string |  | Its id. |
| `where` | Where |  | Where a link goes. |

#### `delete` (Delete)

A session gone: its agent stopped and waited for, its transcript and the folder beside it (its subagents'), pi's own copy of it, and a Bot's folder moved to the trash; then it's gone from the list. One whose files can't be moved stays: error, saying why.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `detach` (Detach)

This connection detached from a session (an ephemeral one its head's done with dropped at once).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `edit_decide` (EditDecide)

A waiting edit accepted or rejected (the user's, in Record). Accepted: the one asking has put its new text in the page in its passage's place already (its own notes write, sent before this); the host keeps what became of it. Answered with edit.

| Field | Type | Required | What it is |
|---|---|---|---|
| `notebook` | string | yes | The notebook: its id, or its name (any case). |
| `id` | string | yes | The edit's id. |
| `accept` | boolean | yes | true: accepted; false: rejected. |

#### `edit_propose` (EditPropose)

An edit proposed to a page of a notebook here, for the user to accept or reject: old_text replaced with new_text, nothing written to the page. old_text is found once (or the one nearest near) in the page as it reads with the edits waiting on it applied; an empty one adds new_text at the page's end. Text one of those edits puts in or replaces: that edit is revised to take this in (any others it reaches taken back), and is by by now. Answered with edit.

| Field | Type | Required | What it is |
|---|---|---|---|
| `notebook` | string | yes | The notebook: its id, or its name (any case). |
| `page` | string | yes | The page: its name (Parent:Child for one under another), any case, or its own name past its parents'. |
| `old_text` | string |  | The text it replaces, as the page has it; empty or left out: added at the page's end. |
| `new_text` | string | yes | What it puts in. |
| `near` | integer |  | A place in the page's text: of old_text there more than once, the one nearest it (a passage selected, say). |
| `by` | string | yes | Who's proposing it, by name (Meta, an agent's own name): kept with the edit, and shown with it. |

#### `edit_revise` (EditRevise)

A waiting edit changed: what it puts in, and what it replaces if old_text is given (found once in the page, not in another waiting edit's passage). It stays one edit, by by now; new_text the same as what it replaces takes it back. Answered with edit.

| Field | Type | Required | What it is |
|---|---|---|---|
| `notebook` | string | yes | The notebook: its id, or its name (any case). |
| `id` | string | yes | The edit's id. |
| `new_text` | string | yes | What it puts in now. |
| `old_text` | string |  | What it replaces now; left out: as it was. |
| `by` | string | yes | Who's proposing it, by name (Meta, an agent's own name): kept with the edit, and shown with it. |

#### `edit_withdraw` (EditWithdraw)

A waiting edit taken back. Answered with edit.

| Field | Type | Required | What it is |
|---|---|---|---|
| `notebook` | string | yes | The notebook: its id, or its name (any case). |
| `id` | string | yes | The edit's id. |

#### `edits` (Edits)

The edits waiting on the pages of the notebooks here (one notebook's, one page's, if given), page by page, each as its page has its passage now. Answered with edits.

| Field | Type | Required | What it is |
|---|---|---|---|
| `notebook` | string |  | Only this notebook's: its id, or its name. |
| `page` | string |  | Only this page's (with notebook). |
| `all` | boolean |  | The decided ones kept here too (decided). |

#### `emission` (Emission)

A Bot's browser tool's request (baudelib.emissiontools) to the Emission that has the Bot's own profile here, started for it if none has (or to the phone showing it).

| Field | Type | Required | What it is |
|---|---|---|---|
| `bot` | BotRef |  | The Bot. |
| `call` | object |  | The tool's request. |

#### `emission_answer` (EmissionAnswer)

A phone's answer to an emission_call.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string |  | The call's id. |
| `result` | object |  | What the browser answered. |
| `error` | string |  | What went wrong. |

#### `emission_serve` (EmissionServe)

A phone showing a Bot of this host's browser (on), or not now: while it is, the Bot's browser tools' requests here are carried to it (emission_call).

| Field | Type | Required | What it is |
|---|---|---|---|
| `bot` | string |  | The Bot's id. |
| `on` | boolean |  | Showing it. |
| `urls` | array of string |  | off: the pages it had open. |
| `note` | string |  | off: what it hadn't told the Bot yet. |

#### `emission_watch` (EmissionWatch)

A Bot's browser here watched (Baude's Browser pane): one a connection.

| Field | Type | Required | What it is |
|---|---|---|---|
| `bot` | BotRef |  | The Bot. |
| `max_w` | integer |  | The pane's width. |
| `max_h` | integer |  | The pane's height. |

#### `emission_watch_say` (EmissionWatchSay)

What the pane says back: a frame taken, its new size.

| Field | Type | Required | What it is |
|---|---|---|---|
| `line` | WatchSay |  | What it says. |

#### `feedback` (Feedback)

Feedback a head with no gh of its own wrote (a phone's), filed as an issue by this host's gh where Send Feedback files it, once for each note.

| Field | Type | Required | What it is |
|---|---|---|---|
| `note` | string |  | The note's id. |
| `text` | string |  | What it says. |
| `context` | array of string |  | Context, under it. |
| `check` | boolean |  | Only where it would go: feedback_target. |

#### `files` (Files)

One of this machine's models' files, or where they are (modelfiles.serve), read-only.

| Field | Type | Required | What it is |
|---|---|---|---|
| `method` | string |  | Its method. |
| `path` | string |  | Its path. |
| `headers` | object |  | Its headers. |

#### `fixes` (Fixes)

Ongoing Feedback, read and acted on with this host's gh, as Baude's Ongoing Feedback does: list, shot (path), merge, discard, reopen, reply (text), fix, stop; the item is as list gave it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | `list` \| `shot` \| `merge` \| `discard` \| `reopen` \| `reply` \| `fix` \| `stop` |  | What to do. |
| `item` | object |  | merge, discard, reopen, reply, fix, stop: the issue, as list gave it. |
| `text` | string |  | reply: what to ask for. |
| `path` | string |  | shot: an image's path, as a round's shot names it (<key>/<round>/<n>-<before\|after>.png). |

#### `git` (Git)

A session's folder's git: log (the host's own checkout's too, for a changelog), card (sha), diff (sha, path?), page (the Git page's whole read, fetching first and asking gh if asked), pulls (GitHub's pull requests, asked of gh).

| Field | Type | Required | What it is |
|---|---|---|---|
| `cwd` | string |  | The folder. |
| `what` | `log` \| `card` \| `diff` \| `page` \| `pulls` |  | What to read. |
| `rev` | string |  | log: from where (default HEAD). |
| `limit` | integer |  | log: how many. page: at least how many commits. |
| `skip` | integer |  | log: after how many. |
| `sha` | string |  | card, diff: the commit. |
| `path` | string |  | diff: one file's only. |
| `session` | string |  | page: the chat whose transcript says what it touched. |
| `revs` | array of string |  | page: the branches picked. log: what to read, a ^sha allowed. |
| `follow` | boolean |  | page: the branches the chat touched. |
| `every` | boolean |  | page, log: every branch, remote branch and tag. |
| `first_parent` | boolean |  | log: only the commits made on the branches themselves. |
| `fetch` | boolean |  | page: git fetch first, GitHub read afresh. |
| `then` | string |  | page: a commit to find, from a link. |
| `repo` | string |  | pulls: owner/name, else the folder's own. |
| `head` | string |  | pulls: a branch whose pull request is wanted. |
| `people` | boolean |  | pulls: GitHub's pictures of the repository's authors. |
| `fresh` | boolean |  | pulls: read afresh. |
| `github` | boolean |  | card: GitHub's part only (the author's login, its pull requests). |

#### `held` (HeldOp)

A message held for a session dropped, or sent as input would send it, from its sender.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `id` | string | yes | The held message's id. |
| `send` | boolean |  | Sent, not dropped. |

#### `hello` (Hello)

Who's connecting: the first line, always.

| Field | Type | Required | What it is |
|---|---|---|---|
| `client` | string |  | What it is (baude, record, baude-android, a host's mesh, ...). |
| `version` | integer |  | The wire version it speaks. |
| `commit` | string |  | The commit its code is (said in the host's log if it's another than the host's). |
| `here` | boolean |  | true: it's on the host's own machine, connected by its socket (an app there is listed among the host's heads; one elsewhere isn't). |

#### `in` (In)

A line for an agent's program's stdin.

| Field | Type | Required | What it is |
|---|---|---|---|
| `line` | string |  | The line. |

#### `infer` (Infer)

An HTTP request to one of this machine's model providers (an OpenAI-compatible server's: path after its base_url), its answer as it comes.

| Field | Type | Required | What it is |
|---|---|---|---|
| `provider` | string |  | The provider's id. |
| `method` | string |  | Its method. |
| `path` | string |  | Its path. |
| `headers` | object |  | Its headers. |
| `body` | string |  | Its body. |

#### `input` (Input)

A stream-json message for the session's agent (started on it, with --resume, if it isn't running). A user message without a uuid is given one.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `msg` | object |  | The message. |
| `as_agent` | string |  | From an agent outside the apps (baude-mcp), by its name, not the session's user: said as from a chat of that name, as as_meta is. |
| `as_meta` | boolean |  | From a head's Meta (its mesh tools, which run in no chat), not the session's user: said as from a chat named Meta. |
| `as` | string |  | The id of one of this host's chats whose mesh tools send it: it's from that chat, not the session's user (said_by, Senders). |

#### `join` (Join)

(another host process's) This connection, from now on, a link it's made to here.

| Field | Type | Required | What it is |
|---|---|---|---|
| `host` | HostHello |  | Who it is. |
| `straight` | boolean |  | A link straight to a member learned of through others (refused by one that's unlinked it). |

#### `kill` (Kill)

An agent's program stopped.

#### `kube` (Kube)

A cluster's objects as Freelens lists them, read through the host's kubeconfig and kubectl: its contexts (contexts), one connected to (connect), a kind's objects (list), an object's YAML (yaml), what controls an object (owner), a pod deleted (delete). Secrets' values are never read.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | `contexts` \| `connect` \| `list` \| `yaml` \| `owner` \| `delete` |  | What to do. |
| `context` | string |  | The kubeconfig context. |
| `page` | string |  | list, yaml: the kind's page, by its resource. |
| `crd` | string |  | list, yaml: a custom resource's definition (plural.group) for a kind that isn't listed. |
| `namespace` | string |  | yaml, owner, delete: the object's namespace. |
| `name` | string |  | yaml, owner, delete: the object's name. |
| `kind` | string |  | owner: the object's kind. |
| `force` | boolean |  | delete: at once, not waiting for the pod to stop. |

#### `kube_follow` (KubeFollow)

A pod's log followed (logs), or a kind's list watched (watch): one a connection, the answers kube_text and kube_rows till the connection goes (kubectl is stopped then).

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | `logs` \| `watch` |  | What to follow. |
| `context` | string |  | The kubeconfig context. |
| `page` | string |  | watch: the kind's page, by its resource. |
| `crd` | string |  | watch: a custom resource's definition. |
| `namespace` | string |  | logs: the pod's namespace. |
| `pod` | string |  | logs: the pod. |
| `container` | string |  | logs: the container. |
| `previous` | boolean |  | logs: its previous run's log, not followed. |

#### `link` (Link)

A link made from this host process to where, kept and made again whenever it drops, and used both ways: the mesh.

| Field | Type | Required | What it is |
|---|---|---|---|
| `where` | Where |  | Where (not local or mesh). |

#### `list_dir` (ListDir)

A folder on the host: any on a computer's (a mesh member may start a session in any of them); on a phone's, one in its agents' folders.

| Field | Type | Required | What it is |
|---|---|---|---|
| `path` | string |  | Its absolute path. |

#### `login` (Login)

claude auth login run on the host (a chat's claude signed in again there): one a connection, its answers login_said as it says each line, then login_end; the connection's going stops it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `args` | array of string |  | claude auth login's arguments (--console, --sso, --email <address>). |

#### `login_code` (LoginCode)

The code the sign-in page showed, passed to the connection's claude auth login (login). No answer: what it says next comes as login_said or login_end.

| Field | Type | Required | What it is |
|---|---|---|---|
| `code` | string | yes | The code, as pasted. |

#### `mcp` (Mcp)

A line for an MCP server, or from it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `conn` | integer |  | Its relay's number. |
| `line` | string |  | The line. |

#### `mcp_close` (McpClose)

An MCP server's gone, on either side.

| Field | Type | Required | What it is |
|---|---|---|---|
| `conn` | integer |  | Its relay's number. |

#### `mcp_line` (McpLine)

A line for a relayed MCP server, or from it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `line` | string |  | The line. |

#### `mcp_relay` (McpRelay)

One of an agent's MCP servers (a relay, baude_headless.py mcp-relay): carried to its caller.

| Field | Type | Required | What it is |
|---|---|---|---|
| `run` | string |  | The run's id. |
| `name` | string |  | The server's name. |
| `token` | string |  | The run's token for it. |

#### `memory` (Memory)

A Bot's memory (the file its agent keeps it in, on the host), after writing text if it's given: refused (error, with the memory as the Bot left it) if the Bot's written it since base_mtime.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `text` | string |  | What to write. |
| `seen` | string |  | What the user's accepted of it. |
| `base_mtime` | number/null |  | The mtime the text was written against, as memory gave it. |

#### `multiplex` (Multiplex)

A Bot's terminal tool's request (baudelib.multiplextools) to the terminals here (baudelib.multiplexlink): Multiplex's window's and the Kubernetes view's pod shells.

| Field | Type | Required | What it is |
|---|---|---|---|
| `bot` | BotRef |  | The Bot. |
| `call` | object |  | The tool's request. |

#### `notes` (Notes)

Record's notebooks here, for another machine's Record: listed, or changed.

| Field | Type | Required | What it is |
|---|---|---|---|
| `action` | string |  | write: changes made; else listed. |
| `have` | object |  | Those the asker has, {id: digest}: only those it hasn't as they are are sent. |
| `changes` | array of object |  | Changes to notebooks. |

#### `ping` (Ping)

Both ways, every PING_S on every connection (and link): SILENT_S of silence and the other end's taken for gone.

#### `quit` (Quit)

The host process stops, its agents too (their sessions kept, resumed next time; one at a turn or asking is marked cut).

| Field | Type | Required | What it is |
|---|---|---|---|
| `if_idle` | boolean |  | Only if no session's running a command, else busy and it goes on. |
| `restart` | boolean |  | It's to start again. |

#### `read_file` (ReadFile)

A file in one of the host's sessions' folders.

| Field | Type | Required | What it is |
|---|---|---|---|
| `path` | string |  | Its absolute path. |
| `limit` | integer |  | This much at most. |

#### `relay` (Relay)

This connection, from now on, a tunnel to member to's host process (hello first, as ever); error if there's no way to it now.

| Field | Type | Required | What it is |
|---|---|---|---|
| `to` | string |  | The member's id. |
| `as_agent` | string |  | As input's as_agent: what's sent through it is from that agent. |
| `as_meta` | boolean |  | As input's as_meta: what's sent through it is from Meta. |
| `as` | string |  | The id of one of this host's chats whose mesh tools open it: what's sent through it is from that chat. |

#### `rename` (Rename)

A session renamed (by its agent if it's running; else as it would).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `title` | string |  | Its title. |

#### `routines` (Routines)

A Bot's routines: set, one made or changed (by its id); delete, one gone (its id).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `set` | object |  | A routine (as Routine, its next worked out here). |
| `delete` | string |  | A routine's id. |

#### `run_routine` (RunRoutine)

One of a Bot's routines run now (routines answers).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `id` | string |  | The routine's id. |

#### `senders` (SendersOp)

A session's messages from others than its user.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `set_cwd` (SetCwd)

The folder a session's agent runs in, from its next message on (not at a turn).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `cwd` | string |  | The folder. |

#### `signal_names` (SignalNames)

The names Signal's glossary has for a session's folder, read as they are; nothing looked up or written.

| Field | Type | Required | What it is |
|---|---|---|---|
| `cwd` | string |  | The folder. |
| `session` | string |  | The chat's own mentions only. |
| `cli_id` | string |  | Its id there, if it's another. |

#### `start` (Start)

A new session. Its prompt is sent once it's begun.

| Field | Type | Required | What it is |
|---|---|---|---|
| `prompt` | string |  | The first message. |
| `cwd` | string |  | Its folder (default: the forked session's, else home). |
| `model` | string/null |  | Its model. |
| `effort` | string/null |  | Its effort. |
| `mode` | string/null |  | Its permission mode. |
| `group` | string/null |  | Its group. |
| `title` | string/null |  | Its title. |
| `attach` | boolean |  | This connection's attached live to it first. |
| `answers` | boolean |  | As attach's. |
| `spawned_by` | string |  | The id of the chat here whose mesh tools start it. |
| `system` | string |  | More for claude's system prompt (--append-system-prompt). |
| `add_dirs` | array of string |  | Folders it may use besides its own (--add-dir). |
| `allowed_tools` | array of string |  | Tool rules (--allowedTools: ["Bash(git:*)", "Edit"]). |
| `disallowed_tools` | array of string |  | Tool rules (--disallowedTools). |
| `fork_from` | string |  | A session of this host's whose conversation this one starts as a copy of (--fork-session). |
| `ask_timeout` | number |  | Seconds a permission it asks waits while nobody who answers is attached, then ask_default answers it. |
| `ask_default` | `deny` \| `allow` |  | What answers when ask_timeout runs out. |
| `draft` | boolean |  | A new chat, listed as draft and not kept till it's written in, and gone if nobody's attached before it is. |
| `ephemeral` | boolean |  | A side question's, forked from fork_from (or fresh), keeping nothing, never listed, gone with its last head. |
| `adopt` | string |  | A conversation claude has here already, by its id, made the host's. |
| `env` | object |  | A head on this machine's environment for the session's agent (kept in memory only). |
| `start_key` | string |  | An id the client picks, kept and listed, so it can tell whether a start whose answer it lost was taken. |
| `app_tools` | array of string |  | Names of MCP servers this head runs itself, for an agent here: each relayed back to it over this connection (mcp_open, mcp, mcp_close), allowed without asking. |
| `grace` | number |  | Seconds an ephemeral session waits for its head to come back (GRACE_MAX_S at most; else EPHEMERAL_S). |
| `as_meta` | boolean |  | As input's as_meta, for the prompt; the session is held to Auto at most, as a subagent of a chat is. |
| `as` | string |  | As input's, for the prompt. |
| `bot` | BotFields |  | A Bot made of it as it starts (a phone's host takes it; picture left out). |

#### `stop` (Stop)

A session's agent stopped: the next message resumes it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `restart` | boolean |  | Those attached aren't told it went. |

#### `subagents` (Subagents)

A session's subagents' files (in the folder beside its transcript).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `have` | object |  | Where the client's copy of each is: {name: [size, mtime]}. |
| `limit` | integer |  | All of them together, at most. |

#### `subscribe` (Subscribe)

The host's sessions, now and on every change, and ping; turn_ended as each of its sessions' turns ends.

| Field | Type | Required | What it is |
|---|---|---|---|
| `archived` | boolean |  | Those archived listed too, and their turns' ends (default: not). |

#### `task_output` (TaskOutput)

The last part of a background task's output file, one a session's transcript names. Answered as file.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session whose task it is. |
| `path` | string | yes | The output file a task's start or end named. |
| `limit` | integer |  | Its last this many bytes (4000 if absent). |

#### `terminal` (Terminal)

A terminal on this host (Multiplex's, baudelib.hostterminal): a program run in a pty here, or attach: one already running here seen again. One a connection: its answers are terminal_opened, then terminal_out as the program writes (first, on attach, what it wrote lately) and terminal_end once it ends. The connection going leaves the program running a while (TERMINAL_KEEP_S) to be attached to again.

| Field | Type | Required | What it is |
|---|---|---|---|
| `argv` | array of string |  | What to run: the user's login shell if none. |
| `cwd` | string |  | The folder it starts in: the home folder if none. |
| `title` | string |  | What it's called till its program names it. |
| `columns` | integer |  | Its width, in characters. |
| `rows` | integer |  | Its height, in lines. |
| `attach` | string |  | A terminal running here (terminals' id), seen again instead of a new one. |

#### `terminal_close` (TerminalClose)

This connection's terminal closed: its program ended (SIGHUP, as a terminal closing sends).

#### `terminal_in` (TerminalIn)

Typed into this connection's terminal: what a keyboard sends, base64.

| Field | Type | Required | What it is |
|---|---|---|---|
| `data` | string |  | The bytes, base64. |

#### `terminal_resize` (TerminalResize)

This connection's terminal is now this size.

| Field | Type | Required | What it is |
|---|---|---|---|
| `columns` | integer | yes | Its width, in characters. |
| `rows` | integer | yes | Its height, in lines. |

#### `terminals` (Terminals)

The terminals running on this host, to attach to.

#### `transcript` (Transcript)

Whole lines of a session's transcript.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `offset` | integer |  | From here. |
| `limit` | integer |  | This much at most. |

#### `unarchive` (Unarchive)

A session listed to all again.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `unlink` (Unlink)

The links made from here to where, or to member id, let go of and forgotten.

| Field | Type | Required | What it is |
|---|---|---|---|
| `where` | Where |  | Where. |
| `id` | string |  | A member's id. |

#### `waits_on` (WaitsOnOp)

A session's agent waits on another chat's answer, or (done) no longer. Kept with the session (not listed) while its turn goes on: if the host stops meanwhile, it follows the wait once it's started again, and what the wait would have given is held for the session.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `on` | WaitTarget |  | What it waits on. |
| `done` | boolean |  | No longer. |

### Answers

#### `accepted` (Accepted)

input's answer, given a req.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `activated` (Activated)

activate's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `any` | boolean |  | There was one. |

#### `agent_started` (AgentStarted)

agent's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `run` | string |  | The run's id. |

#### `allowed` (Allowed)

always_allow's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `tools` | array of string |  | All of them now, sorted. |

#### `archived` (Archived)

archive's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `attached` (Attached)

attach's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | Session |  | The session, as it's listed now. |
| `asks` | array of object |  | What it's asking, not answered yet: its agent's control requests (can_use_tool) as they came. |

#### `bot_picture` (BotPictureReply)

A Bot's picture.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `picture` | string/null |  | A PNG; null for none. |

#### `bot_set` (BotSetReply)

bot's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | Session |  | The session, as it's listed now. |

#### `busy` (Busy)

quit's if_idle: commands are running, so it goes on.

| Field | Type | Required | What it is |
|---|---|---|---|
| `sessions` | array of string |  | The sessions running them. |
| `commands` | array of Running |  | What they run. |

#### `catalogue` (CatalogueReply)

catalogue's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `cwd` | string/null |  | The folder asked for. |
| `items` | array of object |  | lookup's items. |
| `busy` | boolean |  | There's more to come (ask again, with wait). |
| `error` | string |  | What's wrong. |

#### `cwd_set` (CwdSet)

set_cwd's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `cwd` | string |  | The folder, real. |

#### `databases` (DatabasesReply)

databases' answer: servers {servers, databases}; schema {tables, links, took}; rows {rows}.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | string |  | What was read. |
| `error` | string |  | What's wrong. |
| `servers` | array of DbServer |  | servers: the servers found. |
| `databases` | object |  | servers: each server's (by container) databases. |
| `tables` | array of DbTable |  | schema: its tables. |
| `links` | array of DbLink |  | schema: their foreign keys. |
| `took` | number |  | schema: seconds it took to read, 0 when it was kept from a read before. |
| `rows` | array of object |  | rows: the first rows, each {column: value}; objects and lists as JSON text. |

#### `deactivated` (Deactivated)

deactivate's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `any` | boolean |  | There was one. |

#### `deleted` (Deleted)

delete's answer (and an event to those attached: nothing to carry on).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `trashed` | array of string |  | What was moved to the trash. |

#### `dir` (Dir)

list_dir's answer: its entries, folders first (.git left out), DIR_LIMIT at most.

| Field | Type | Required | What it is |
|---|---|---|---|
| `path` | string |  | The folder. |
| `entries` | array of DirEntry |  | Its entries. |
| `more` | boolean |  | There are more. |
| `error` | string |  | Why it can't be read. |

#### `edit` (EditReply)

edit_propose's, edit_revise's, edit_withdraw's and edit_decide's answer: the edit as it is now.

| Field | Type | Required | What it is |
|---|---|---|---|
| `edit` | Edit | yes | The edit. |
| `took_in` | array of string |  | The edits a proposal reached into, taken back as it was revised to take them in: their ids. |

#### `edits` (EditsReply)

edits's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `edits` | array of Edit | yes | The edits waiting. |
| `decided` | array of Edit |  | With all: those accepted, rejected or taken back, the last kept. |
| `stamp` | string |  | The edits here as written last (info's edits). |

#### `emission` (EmissionReply)

The tool's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `result` | object |  | What Emission answered. |

#### `emission_frame` (EmissionFrame)

emission_watch's answer, as it goes: a frame of the page in front, or its state as it changes.

| Field | Type | Required | What it is |
|---|---|---|---|
| `frame` | integer |  | Its number (ack it). |
| `w` | integer |  | Its width. |
| `h` | integer |  | Its height. |
| `jpeg` | string |  | The frame. |
| `stride` | integer |  | Its rows' length in bytes, in shared memory (shm). |
| `shm` | string |  | The shared memory it's in (a pane on Emission's own machine), not jpeg. |
| `view` | array/null of integer |  | The page's size, in CSS pixels. |
| `error` | string |  | Emission said no: why. |
| `state` | WatchState |  | The browser's state. |

#### `emission_serving` (EmissionServing)

emission_serve's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `bot` | string |  | The Bot's id. |
| `on` | boolean |  | Its requests come here. |

#### `emission_watch_end` (EmissionWatchEnd)

emission_watch's last answer: the watch is over.

#### `emission_watching` (EmissionWatching)

emission_watch's answer: it's watched: emission_frame follows, many, till emission_watch_end.

#### `error` (Error)

Anything wrong, with the request's req. A request whose op the host doesn't take is answered with code unsupported_operation (error "unsupported operation"), and one whose fields aren't as the schema has them with code invalid_data_type (error "invalid data type"): the same from every host, what was wrong in detail.

| Field | Type | Required | What it is |
|---|---|---|---|
| `error` | string | yes | What's wrong. |
| `code` | `unsupported_operation` \| `invalid_data_type` |  | One of the schema's own refusals. |
| `detail` | string |  | A refusal's: what was wrong (the op, or the field and why), for a person to read; code, not this, says which refusal it is. |
| `plain` | boolean |  | Said for the user as it is. |
| `gone` | GoneFolder |  | A session's folder gone and not made again. |
| `session` | string |  | memory's refusal: the session. |
| `text` | string |  | memory's refusal: the memory as the Bot left it. |
| `seen` | string/null |  | memory's refusal: what the user's accepted of it. |
| `mtime` | number/null |  | memory's refusal: its mtime (ns; a phone's, seconds). |
| `path` | string |  | memory's refusal: where it is. |

#### `feedback_filed` (FeedbackFiled)

feedback's answer: filed.

| Field | Type | Required | What it is |
|---|---|---|---|
| `url` | string |  | The issue. |
| `number` | integer/null |  | Its number. |
| `repo` | string |  | Where. |

#### `feedback_target` (FeedbackTarget)

feedback's answer, given check: where it would be filed.

| Field | Type | Required | What it is |
|---|---|---|---|
| `repo` | string |  | Where. |

#### `file` (File)

read_file's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `path` | string |  | The path asked for. |
| `size` | integer |  | Its size. |
| `truncated` | boolean |  | Cut short. |
| `data` | string |  | What it holds. |
| `error` | string |  | Why it can't be read. |

#### `fixes` (FixesReply)

fixes' answer: list {repo, items, can_write, locations}, or an error (gh's, or what was asked for); shot {data}; the rest {}.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | string |  | What was done. |
| `error` | string |  | What went wrong. |
| `repo` | string/null |  | list: where feedback is read from (owner/name); null: GitHub isn't connected here. |
| `items` | array of object |  | list: the issues and where each stands, in the order they're listed: {number, key, issue_location, title, body, author, url, created, updated (seconds), status (open, working, ready, merged, rejected, closed), thread [{author, at, text, mark {state, round, machine, shots [{label, before, after}]} or null, trusted}], rounds, working {machine, round, at} or null, here, error, pr {number, url, state, head, sha, cross} or null}. |
| `can_write` | boolean |  | list: whether you can merge and fix on it. |
| `locations` | array of string |  | list: the repositories the issues are on. |
| `data` | string |  | shot: the image's PNG, base64. |

#### `git` (GitReply)

git's answer: log {top, branch, commits, skip, more}; card {sha, parents, author, email, at, ct, committer, message, files, more_files, on, remotes, url, login}, or with github {sha, login, prs}; diff {sha, path, text, truncated}; page {top, folder, branches, picked, rev, mine, branch, parent, commits, own, theirs, revs, repo, every, fetched, found, more, made, heads, opened, links, prs}; pulls {repo, prs, gh, signed_in, pr, avatars}.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | string |  | What was read. |
| `error` | string |  | What's wrong. |
| `top` | string |  | log, page: the checkout's top folder. |
| `branch` | Branch |  | log, page: its branch. |
| `commits` | array of Commit |  | log, page: the commits. |
| `skip` | integer |  | log: after how many. |
| `more` | boolean |  | log, page: there are more. |
| `sha` | string |  | card, diff: the commit. |
| `parents` | array of string |  | card: its parents. |
| `author` | string |  | card: its author. |
| `email` | string |  | card: their email. |
| `at` | integer |  | card: when, epoch seconds. |
| `committer` | string |  | card: its committer. |
| `message` | string |  | card: its message. |
| `files` | array of array of any |  | card: [path, lines added, removed] (null for a binary file's). |
| `more_files` | boolean |  | card: more files than listed. |
| `on` | array of string |  | card: the local branches it's on. |
| `branches` | array of Branch |  | page: the branches to choose from. |
| `path` | string/null |  | diff: the file. |
| `text` | string |  | diff: the diff. |
| `truncated` | boolean |  | diff: cut short. |
| `folder` | string |  | page: the folder asked about. |
| `picked` | array of string |  | page: the branches shown. |
| `rev` | string |  | page: the first branch shown. |
| `mine` | string/null |  | page: the branch the chat's checkout has. |
| `parent` | GitParent? |  | page: the branch the one shown came from. |
| `own` | array of string |  | page: the shown branch's own commits. |
| `theirs` | array of string |  | page: the commits its parent has that it hasn't. |
| `revs` | array of string |  | page: what the log was read from. |
| `repo` | string/null |  | page, pulls: the GitHub repository, owner/name. |
| `every` | boolean |  | page: every branch is shown. |
| `made` | array of GitMade |  | page: the commits this chat made. |
| `heads` | array of string |  | page: the branches this chat touched. |
| `opened` | array of integer |  | page: the pull requests this chat opened. |
| `links` | array of PullLink |  | page: the pull requests its replies name. |
| `prs` | any |  | page, pulls: the repository's pull requests (null: not known). card: the commit's. |
| `fetched` | boolean/null |  | page: whether the fetch went (null: none asked). |
| `found` | GitFound? |  | page: where the commit asked for is. |
| `gh` | boolean |  | pulls: gh is there and not signed out. |
| `signed_in` | boolean |  | pulls: gh is signed in. |
| `pr` | PullInfo? |  | pulls: the pull request of the branch asked. |
| `avatars` | object |  | pulls: {email: picture URL}. |
| `remotes` | array of string |  | card: the remote branches it's on. |
| `url` | string/null |  | card: its page on GitHub. |
| `login` | string/null |  | card: its author's GitHub login. |
| `ct` | integer |  | card: when committed, epoch seconds. |

#### `held` (HeldReply)

held's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `hello` (HelloReply)

The host's answer to hello.

| Field | Type | Required | What it is |
|---|---|---|---|
| `server` | Info |  | What the host is. |

#### `infer_data` (InferData)

infer's and files's answer: some of its body.

| Field | Type | Required | What it is |
|---|---|---|---|
| `data` | string |  | The bytes. |

#### `infer_end` (InferEnd)

infer's and files's answer's end.

#### `infer_head` (InferHead)

infer's and files's answer's status and headers.

| Field | Type | Required | What it is |
|---|---|---|---|
| `status` | integer |  | Its status. |
| `headers` | object |  | Its headers. |

#### `joined` (Joined)

join's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `host` | HostHello |  | Who this is. |

#### `kube` (KubeReply)

kube's answer: contexts {contexts, where}; connect {sections, namespaces, nodes}; list {columns, rows, metrics, page}; yaml {text, file, refs}; owner {kind, name, resource}; delete {said}; fault when a cluster can't be read.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | string |  | What was done. |
| `error` | string |  | What's wrong with the request. |
| `fault` | KubeFault |  | The cluster can't be read: why. |
| `contexts` | array of KubeContext |  | contexts: the kubeconfig's. |
| `where` | string |  | contexts: the kubeconfig files, or what stands for none. |
| `sections` | array of KubeSection |  | connect: the sidebar's categories, each kind with whether the cluster serves it. |
| `namespaces` | array of string |  | connect: the cluster's namespaces. |
| `nodes` | integer |  | connect: how many nodes it has. |
| `page` | KubePage |  | list of a definition: its kind's page. |
| `columns` | array of KubeColumn |  | list: the page's columns on this cluster. |
| `rows` | array of KubeRow |  | list: the objects. |
| `metrics` | boolean/null |  | list: whether CPU and memory could be read (pods, nodes), or null for another kind. |
| `text` | string |  | yaml: the YAML (a Secret's keys only, a Helm release's JSON). |
| `file` | string |  | yaml: what it is called, for its highlighting. |
| `refs` | array of KubeRef |  | yaml: the names in it that are other objects. |
| `kind` | string |  | owner: the controlling kind, climbed up from a ReplicaSet or a Job. |
| `name` | string |  | owner: its name. |
| `resource` | string |  | owner: its kind's resource. |
| `said` | string |  | delete: what kubectl said. |

#### `kube_end` (KubeEnd)

kube_follow's last answer: kubectl ended.

| Field | Type | Required | What it is |
|---|---|---|---|
| `code` | integer |  | Its exit code. |
| `said` | string |  | What it wrote, for a failure. |
| `fault` | KubeFault |  | Why, when the cluster can't be read. |

#### `kube_rows` (KubeRows)

kube_follow watch: the page's objects as they are now.

| Field | Type | Required | What it is |
|---|---|---|---|
| `rows` | array of KubeRow |  | The objects. |
| `metrics` | boolean/null |  | Whether CPU and memory could be read, or null. |
| `note` | string |  | Why it can't be watched and is read every few seconds instead, or empty. |

#### `kube_text` (KubeText)

kube_follow logs: more of the log.

| Field | Type | Required | What it is |
|---|---|---|---|
| `text` | string |  | The lines. |

#### `linking` (Linking)

link's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `where` | Where |  | Where. |

#### `login_end` (LoginEnd)

login's last answer: claude auth login ended.

| Field | Type | Required | What it is |
|---|---|---|---|
| `code` | integer/null |  | Its exit code; null if it couldn't be started. |
| `why` | string |  | Why it couldn't be started. |
| `who` | string/null |  | Signed in (code 0): who as, as claude auth status says it; null if it didn't say. |

#### `login_said` (LoginSaid)

login: a line claude auth login wrote (the sign-in page's address among them).

| Field | Type | Required | What it is |
|---|---|---|---|
| `line` | string | yes | The line, as it wrote it. |

#### `memory` (MemoryReply)

A Bot's memory.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `text` | string |  | Its text. |
| `seen` | string/null |  | What the user's accepted of it. |
| `mtime` | number/null |  | Its mtime (ns; a phone's, seconds). |
| `path` | string |  | Where it is, on the host. |

#### `multiplex` (MultiplexReply)

The tool's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `result` | object |  | What the terminals' process answered. |

#### `notes` (NotesReply)

notes's answer: listed {notebooks, ids}, or written {done, by}.

| Field | Type | Required | What it is |
|---|---|---|---|
| `notebooks` | array of Notebook |  | Notebooks. |
| `ids` | array of string |  | Every one's id, in order. |
| `done` | array of string |  | The changes made. |
| `by` | string |  | record: the Record running there made them. |
| `error` | string |  | What's wrong. |

#### `quitting` (Quitting)

quit's answer: it's stopping.

#### `relayed` (Relayed)

relay's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `to` | string |  | The member's id. |

#### `relaying` (Relaying)

mcp_relay's answer.

#### `renamed` (Renamed)

rename's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | Session |  | The session, as it's listed now. |

#### `routines` (RoutinesReply)

routines' and run_routine's answer: a Bot's routines.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `routines` | array of Routine |  | Its routines. |
| `clock` | Clock |  | The host's clock. |

#### `senders` (SendersReply)

Its sender store.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `senders` | object |  | By message uuid. |
| `rev` | integer |  | Its revision. |

#### `signal_names` (SignalNamesReply)

signal_names's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `glossary` | string/null |  | The glossary's path; null for none. |
| `root` | string/null |  | The checkout's top folder the locations' paths are relative to. |
| `refs` | array of SignalRef |  | The names. |
| `error` | string |  | What's wrong. |

#### `started` (Started)

start's answer, before the prompt's sent (attach: this connection's attached live to it first).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | Session |  | The session, as it's listed now. |
| `forked` | boolean |  | Forked from fork_from. |

#### `stopped` (Stopped)

stop's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `subagents` (SubagentsReply)

Each one's transcript from where have says the client's copy is, in whole lines; its meta.json whole, if it's changed.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `files` | array of SubagentFile |  | The files. |
| `more` | boolean |  | There's more, past limit; those it didn't reach aren't listed. |

#### `terminal_end` (TerminalEnd)

The terminal's program has ended (or never started: said).

| Field | Type | Required | What it is |
|---|---|---|---|
| `status` | integer/null |  | Its exit status (a signal's: minus its number). |
| `said` | string |  | Why it couldn't start. |

#### `terminal_opened` (TerminalOpened)

The terminal's: what it is.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string |  | Its id here, for attach. |
| `title` | string |  | Its title. |
| `argv` | array of string |  | What it runs. |
| `cwd` | string |  | Where it started. |

#### `terminal_out` (TerminalOut)

What the terminal's program wrote, as it came (its escapes too), base64.

| Field | Type | Required | What it is |
|---|---|---|---|
| `data` | string |  | The bytes, base64. |

#### `terminals` (TerminalsReply)

The terminals running on this host.

| Field | Type | Required | What it is |
|---|---|---|---|
| `terminals` | array of TerminalInfo |  | Each one. |

#### `transcript` (TranscriptReply)

Whole lines of its transcript, as they are, byte for byte.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `offset` | integer |  | Where data starts. |
| `next` | integer |  | Where the next read starts. |
| `size` | integer |  | The transcript's size. |
| `data` | string |  | The lines. |

#### `unarchived` (Unarchived)

unarchive's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `unlinked` (Unlinked)

unlink's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `any` | boolean |  | There were any. |

#### `waits_on` (WaitsOnReply)

waits_on's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

### Events

#### `claude` (Claude)

What a session's agent says, as it says it (claude's stream-json); to those attached live. A worktree made again from its branch is told by {type: "_note", text}; a message it's sent from elsewhere, as it's given its agent and kept, by {type: "_said", uuid, message, sender?} (sender: its sender store's, shown too, if not its user).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `msg` | object |  | A stream-json message. |

#### `emission_call` (EmissionCall)

A Bot's browser tool's request, carried to the phone showing it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string |  | Its id: the answer's. |
| `bot` | BotRef |  | The Bot. |
| `call` | object |  | The request. |

#### `emission_unserved` (EmissionUnserved)

Another phone shows this Bot's browser now.

| Field | Type | Required | What it is |
|---|---|---|---|
| `bot` | string |  | The Bot's id. |
| `why` | string |  | Why. |

#### `err` (Err)

What an agent's program says, on stderr.

| Field | Type | Required | What it is |
|---|---|---|---|
| `line` | string |  | The line. |

#### `exit` (Exit)

An agent's program's gone.

| Field | Type | Required | What it is |
|---|---|---|---|
| `code` | integer/null |  | Its exit code. |

#### `exited` (Exited)

A session's agent stopped.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `code` | integer/null |  | Its exit code. |
| `stopped` | boolean |  | Told to (stop). |

#### `mcp_late` (McpLate)

A call the head answered after it was failed (its connection was gone), as it did run.

| Field | Type | Required | What it is |
|---|---|---|---|
| `conn` | integer |  | Its relay's number. |
| `line` | string |  | The line. |

#### `mcp_open` (McpOpen)

An agent's program (or a session's agent, for a head's app_tools) has started an MCP server: it's started at the other end.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string |  | The session (app_tools). |
| `conn` | integer |  | Its relay's number. |
| `name` | string |  | The server's name. |

#### `out` (Out)

What an agent's program says, on stdout.

| Field | Type | Required | What it is |
|---|---|---|---|
| `line` | string |  | The line. |

#### `sessions` (Sessions)

Its sessions (newest first; a side question's never), and what it is: subscribe's answer, then again on every change.

| Field | Type | Required | What it is |
|---|---|---|---|
| `sessions` | array of Session |  | Its sessions. |
| `server` | Info |  | What it is. |

#### `stopping` (Stopping)

How far along stopping it is: its agents left of all it's stopping.

| Field | Type | Required | What it is |
|---|---|---|---|
| `procs` | integer |  | All it's stopping. |
| `left` | integer |  | Those left. |
| `restart` | boolean |  | It's to start again. |

#### `turn_ended` (TurnEnded)

A session's turn ended.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `result` | string/null |  | Its result. |
| `is_error` | boolean |  | It ended in an error. |
| `cost` | number/null |  | Its cost, USD. |
| `turns` | integer/null |  | Its turns. |
| `duration_ms` | number/null |  | How long it took. |

### Mesh frames

#### `data` (Data)

A wire message in a tunnel, passed on as it came.

| Field | Type | Required | What it is |
|---|---|---|---|
| `t` | integer |  | The tunnel, by the number of the end that opened it (this end's). |
| `r` | integer |  | The tunnel, by the number of the end that opened it (the other end's). |
| `msg` | object |  | The message. |

#### `mesh` (MeshFrame)

Members' records, on a link.

| Field | Type | Required | What it is |
|---|---|---|---|
| `records` | array of Record |  | The records. |

#### `open` (Open)

A tunnel to member to: passed on to the next host on the way (ttl hosts at most), and at to a connection to its host process, served as if a head had connected to its socket.

| Field | Type | Required | What it is |
|---|---|---|---|
| `t` | integer |  | The tunnel, by the number of the end that opened it (this end's). |
| `to` | string |  | The member it's to. |
| `ttl` | integer |  | Hosts it may pass through. |
| `from` | string |  | The member that opened it. |
| `sender` | Sender |  | Who what's sent through it is from (relay's as). |

#### `opened` (Opened)

A tunnel's there.

| Field | Type | Required | What it is |
|---|---|---|---|
| `t` | integer |  | The tunnel, by the number of the end that opened it (this end's). |
| `r` | integer |  | The tunnel, by the number of the end that opened it (the other end's). |

### Entities

#### Ask

A permission its agent's asking for, not answered yet. Answered with input: a control_response for its request_id, as claude's stream-json has it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `request_id` | string | yes | The control request's id. |
| `tool` | string/null |  | The tool it asks to use. |
| `input` | object/null |  | The tool's input (long strings in it cut short). |

#### BotFields

What bot sets: a name and, maybe, a picture.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string | yes | Its name. |
| `picture` | string/null |  | A PNG, base64; "" to drop it; left out, kept. |

#### BotListing

A Bot, as a session lists it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string | yes | Its name. |
| `since` | number/null |  | When it was made a Bot, epoch seconds. |
| `picture` | number/null |  | Its picture's mtime; null for none. |

#### BotRef

A Bot, as its browser's ops name it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its session's id. |
| `name` | string/null |  | Its name. |

#### Branch

A checkout's branch.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string/null |  | Its name. |
| `upstream` | string/null |  | What it tracks. |
| `ahead` | integer |  | Commits ahead of it. |
| `behind` | integer |  | Commits behind it. |
| `rev` | string |  | What names it to git. |
| `label` | string |  | How the branch list names it. |
| `sha` | string/null |  | Its tip. |
| `gone` | boolean |  | Its upstream is gone from the remote. |
| `worktree` | string/null |  | The checkout it's in. |
| `remote` | boolean |  | A remote branch. |
| `touched` | boolean |  | This chat touched it (the list's first part). |

#### Clock

The host's clock.

| Field | Type | Required | What it is |
|---|---|---|---|
| `zone` | string/null |  | Its time zone's name. |
| `offset` | integer |  | Seconds from UTC. |

#### Command

What its agent is running: a Bash or PowerShell call (its subagents' too) from the call to its result (one waiting on its permission ask isn't running yet), or a background task but a subagent from its start to its notification.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | The call's tool_use_id, or the task's task_id. |
| `what` | string |  | What it runs, 80 characters at most. |
| `since` | number |  | When it started, epoch seconds. |
| `background` | boolean |  | Run in the background. |

#### Commit

A commit, as git's log has it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `sha` | string |  | Its sha. |
| `parents` | array of string |  | Its parents. |
| `author` | string |  | Its author. |
| `email` | string |  | Their email. |
| `at` | integer |  | When, epoch seconds. |
| `refs` | array of string |  | Branches and tags at it. |
| `subject` | string |  | Its first line. |
| `ct` | integer |  | When it was committed, epoch seconds. |
| `title` | string |  | What the log calls it (a merged pull request's name). |
| `files` | integer |  | How many files it changed. |
| `added` | integer |  | Lines it put in. |
| `removed` | integer |  | Lines it took out. |

#### DbColumn

A table's column.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string | yes | Its name. |
| `type` | string |  | Its type. |
| `null` | boolean |  | Whether it may be null (null: not known). |
| `pk` | boolean |  | Whether it is in the primary key. |
| `fk` | string/null |  | The column it references, as the diagram's card says it (schema omitted in its own), or null. |

#### DbLink

A foreign key: a column that references another table's.

| Field | Type | Required | What it is |
|---|---|---|---|
| `from_schema` | string |  | The referencing table's schema. |
| `from_table` | string | yes | The referencing table. |
| `from_column` | string |  | The referencing column. |
| `to_schema` | string |  | The referenced table's schema. |
| `to_table` | string | yes | The referenced table. |
| `to_column` | string |  | The referenced column. |

#### DbServer

A database server in a running container, as the database view lists it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `container` | string | yes | The container (or pod) it runs in. |
| `engine` | string |  | postgres or arango. |
| `version` | string |  | Its version, or empty. |

#### DbTable

A table (or view, or collection) and its columns.

| Field | Type | Required | What it is |
|---|---|---|---|
| `schema` | string | yes | Its schema (an ArangoDB server's: collections). |
| `name` | string | yes | Its name. |
| `kind` | string |  | r table, p partitioned table, v view, m materialized view, f foreign table, e edge collection. |
| `rows` | integer |  | About how many rows it has. |
| `columns` | array of DbColumn |  | Its columns, in order. |

#### DirEntry

An entry in a folder.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string |  | Its name. |
| `dir` | boolean |  | A folder. |
| `size` | integer |  | A file's size. |
| `ignored` | boolean |  | git ignores it. |

#### Edit

An edit proposed to a page of a notebook, kept by the notebook's host (edits.json, beside records.json) until the user accepts or rejects it in Record.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id. |
| `notebook` | string | yes | Its notebook's id. |
| `page` | string | yes | Its page's name. |
| `old` | string | yes | What it replaces (empty: it adds at the page's end). |
| `new` | string | yes | What it puts in. |
| `start` | integer |  | Where what it replaces starts in the page's text. |
| `status` | `pending` \| `accepted` \| `rejected` \| `withdrawn` | yes | pending: waiting for the user; accepted, rejected; withdrawn: taken back. |
| `t` | number |  | When it was proposed, in seconds since the epoch. |
| `by` | string |  | Who proposed it, or revised it last: Meta, an agent's own name. |
| `gone` | boolean |  | (edits) What it replaces isn't on the page as it was any more. |

#### FoundLink

A link straight to a member learned of through others (mesh's Found).

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string |  | The member's id. |
| `name` | string/null |  | Its name. |
| `state` | `waiting` \| `connecting` \| `linked` \| `dropped` |  | How it is. |
| `error` | string/null |  | What's wrong. |

#### GitFound

Where a commit a link points at is in the log.

| Field | Type | Required | What it is |
|---|---|---|---|
| `sha` | string |  | The commit. |
| `merge` | string/null |  | The merge on the branch's own line it came in by, if it's not on that line. |

#### GitMade

A commit a chat's own commands made.

| Field | Type | Required | What it is |
|---|---|---|---|
| `sha` | string |  | Its sha, as the command's output wrote it (short). |
| `subject` | string |  | Its first line. |
| `tool_use_id` | string |  | The tool call that made it. |
| `branch` | string/null |  | The branch it was made on. |

#### GitParent

The branch a branch most likely came from.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string |  | Its name. |
| `base` | string |  | The commit they share. |
| `ahead` | integer |  | Commits the branch has on it. |
| `behind` | integer |  | Commits it has on the branch. |

#### Gone

A member reached lately, not now (restarting, say).

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string |  | Its id. |
| `name` | string |  | Its name. |

#### GoneFolder

A session's folder gone and not made again.

| Field | Type | Required | What it is |
|---|---|---|---|
| `why` | string |  | Why. |
| `folder` | string/null |  | Where to carry on instead; null for nowhere to say. |

#### Head

An app on a host's own machine connected to its host process (one that's subscribed: Baude, Record; or an Emission there), by the code it runs. Those on the same code are one.

| Field | Type | Required | What it is |
|---|---|---|---|
| `client` | string | yes | What it is, as its hello says (baude, record, emission, baude-android, ...). |
| `commit` | string/null |  | The commit its code is, as its hello says (null: it didn't say). |
| `made` | integer/null |  | When that commit was made, in seconds, as the host's git has it (null: it hasn't it). |
| `behind` | integer/null |  | In a host's info only: how many commits it lacks of the mesh's newest code (MeshView's newest), as that host's git counts them (null: it can't). |

#### HeldMessage

A message held for a session: one its host had for it while nobody was there to say whether it should go (a subagent's answer after a restart, say).

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id, as held takes it. |
| `text` | string |  | What it says: what its agent's sent, as it's sent. |
| `shown` | string |  | What a person's shown of it: the answer itself, or what became of it in a line (still working, an error). Heads show text where it's missing. |
| `sender` | Sender? |  | Who it's from. |
| `at` | number/null |  | When it was held, epoch seconds. |

#### HostHello

A host process as it says it is on a link (mesh._hello_of).

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id. |
| `name` | string |  | Its name. |
| `group` | string |  | Its group. |
| `host` | string |  | Its machine's host name. |
| `version` | integer |  | Its wire version. |
| `bonjour` | string |  | Its name on the local network, if it said one. |
| `dial` | boolean |  | false: it can't be linked to, only from (a phone, with no sshd). |

#### Info

What a host is, as its hello says (and sessions carries).

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id. |
| `name` | string |  | Its name. |
| `group` | string |  | Its default group. |
| `version` | integer |  | The wire version it speaks. |
| `models` | array of object |  | Every model it can run: {value, displayName}, a Codex model's as its catalogue has it. |
| `host` | string |  | Its machine's host name. |
| `home` | string |  | Its user's home folder. |
| `pid` | integer |  | Its process's id. |
| `checkout` | string/null |  | Where its code is (a head here run from elsewhere is told so). |
| `groups` | array of string |  | Its sessions' groups. |
| `init` | object/null |  | What a session's program last said to initialize, with every model it can run in it. |
| `commit` | string/null |  | The git commit its code was as it started (null: not a checkout). |
| `made` | integer/null |  | When that commit was made, in seconds (null: not a checkout). |
| `behind` | integer/null |  | How many commits it lacks of the mesh's newest code (mesh's newest), as its git counts them (null: it can't). |
| `heads` | array of Head |  | The apps on its machine connected to it, by the code they run (each counted behind as its own commit is). |
| `update` | Update? |  | Its own updater's newer version, if it has one ready. |
| `providers` | array of Provider |  | This machine's model providers (with base_url). |
| `mesh` | MeshView |  | The members it reaches and the links it's made. |
| `notes` | string |  | Record's notebooks here, as written last (notes.stamp: "" for none). |
| `edits` | string |  | The edits proposed to the pages of Record's notebooks here, as written last (notes.edits_stamp: "" for none). |
| `phone` | boolean |  | A phone's host. |
| `ops` | array of string |  | The ops it takes, given by a host that takes only part of the wire (a phone's), with what its sessions take besides (start's ephemeral; control requests: control, set_permission_mode, set_model). |

#### KubeColumn

A table's column.

| Field | Type | Required | What it is |
|---|---|---|---|
| `title` | string | yes | Its title. |
| `kind` | string/null |  | name, namespace, owner, containers, age, status or null. |

#### KubeContainer

A pod's container, as its square shows it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string | yes | Its name. |
| `look` | string |  | ok, starting, restarted, bad or done. |
| `words` | string |  | What its state is, in words. |
| `init` | boolean |  | An init container. |

#### KubeContext

A context of the host's kubeconfig.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string | yes | Its name. |
| `current` | boolean |  | The kubeconfig's current context. |
| `local` | boolean |  | Its API server is on the host itself. |

#### KubeFault

Why a cluster can't be read, as kubectl's failure is diagnosed.

| Field | Type | Required | What it is |
|---|---|---|---|
| `title` | string | yes | What went wrong. |
| `why` | string |  | What kubectl said that says why. |
| `fix` | string/null |  | A command to run in a terminal that fixes it, or null. |
| `said` | string |  | All kubectl wrote. |

#### KubePage

A kind's page of the Kubernetes view.

| Field | Type | Required | What it is |
|---|---|---|---|
| `title` | string | yes | Its name, as the sidebar has it. |
| `resource` | string | yes | What kubectl gets it by. |
| `crd` | string/null |  | A custom resource's definition (plural.group), or null. |
| `namespaced` | boolean |  | Its objects are in namespaces. |
| `served` | boolean |  | The cluster serves it. |
| `optional` | boolean |  | Listed only on a cluster that serves it. |
| `source` | string |  | kubectl, secrets or helm. |

#### KubeRef

A name in an object's YAML that is another object.

| Field | Type | Required | What it is |
|---|---|---|---|
| `line` | integer | yes | Its line (from 0). |
| `column` | integer |  | Its column (from 0). |
| `length` | integer |  | Its length. |
| `kind` | string |  | The object's kind. |
| `name` | string |  | Its name. |
| `namespace` | string |  | Its namespace, or empty. |

#### KubeRow

An object's row.

| Field | Type | Required | What it is |
|---|---|---|---|
| `cells` | array of string |  | Each column's text. |
| `keys` | array of any |  | Each column's sort key: a number, a list of numbers or text. |
| `tones` | array of string |  | Each column's tone (ok, warn, bad), or empty. |
| `tips` | array of string |  | Each column's tooltip, or empty (an age's: its time in full). |
| `name` | string | yes | The object's name. |
| `namespace` | string |  | Its namespace, or empty. |
| `uid` | string |  | What it is known by across reads. |
| `version` | string |  | Its resource version. |
| `owner_kind` | string |  | What controls it: the kind, or empty. |
| `owner_name` | string |  | What controls it: the name, or empty. |
| `containers` | array of KubeContainer |  | A pod's containers, init ones first. |
| `default` | string |  | A pod's default container (its annotation), or empty. |

#### KubeSection

A category of the Kubernetes view's sidebar.

| Field | Type | Required | What it is |
|---|---|---|---|
| `title` | string | yes | Its name. |
| `icon` | string |  | Its icon's name, or empty. |
| `pages` | array of KubePage |  | Its kinds. |

#### Member

A member of the mesh, as info's mesh lists it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id. |
| `name` | string |  | Its name. |
| `group` | string |  | Its group. |
| `host` | string |  | Its machine's host name. |
| `version` | integer |  | Its wire version. |
| `bonjour` | string |  | Its name on the local network. |
| `dial` | boolean |  | false: it can't be linked to, only from (a phone, with no sshd). |
| `providers` | array of Provider |  | Its model providers. |
| `hops` | integer |  | Links away. |
| `through` | string/null |  | The member it's reached through, by name; null for one linked to here. |
| `commit` | string/null |  | The commit its code is (null: not a checkout, or it didn't say). |
| `made` | integer/null |  | When that commit was made, in seconds (null: not known). |
| `heads` | array of Head |  | The apps on its machine connected to it, by the code they run. |
| `behind` | integer/null |  | How many commits it lacks of the mesh's newest code (MeshView's newest), as this host's git counts them (null: it can't). |

#### MemberInfo

Who a member is, in its record.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id. |
| `name` | string |  | Its name. |
| `group` | string |  | Its group. |
| `host` | string |  | Its machine's host name. |
| `version` | integer |  | Its wire version. |
| `bonjour` | string |  | Its name on the local network. |
| `dial` | boolean |  | false: it can't be linked to, only from (a phone, with no sshd). |
| `providers` | array of Provider |  | Its model providers. |
| `commit` | string/null |  | The commit its code is (null: not a checkout, or it didn't say). |
| `made` | integer/null |  | When that commit was made, in seconds (null: not known). |
| `heads` | array of Head |  | The apps on its machine connected to it, by the code they run. |

#### MeshView

The mesh as a host sees it (mesh.Mesh.view).

| Field | Type | Required | What it is |
|---|---|---|---|
| `members` | array of Member |  | The members it reaches. |
| `links` | array of OutLink |  | The links made from here. |
| `proxy` | Proxy? |  | Its Proxy: null till a member has a provider. |
| `inactive` | array of string |  | The ids of members deactivated here. |
| `found` | array of FoundLink |  | Links straight to members learned of through others. |
| `gone` | array of Gone |  | Members reached lately, not now (not one deactivated here). |
| `newest` | string/null |  | The commit of the newest code in the mesh (the latest made of every member's and app's), what each is counted behind (null: none's known). |

#### Notebook

One of Record's notebooks, as another machine's Record is given it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string/null |  | Its id. |
| `name` | string |  | Its name. |
| `pages` | object |  | Its pages. |

#### OutLink

A link made from here.

| Field | Type | Required | What it is |
|---|---|---|---|
| `where` | Where |  | Where it goes. |
| `id` | string/null |  | The member it reaches. |
| `name` | string/null |  | Its name. |
| `state` | `linked` \| `connecting` \| `dropped` \| `inactive` |  | How it is. |
| `error` | string/null |  | What's wrong. |
| `active` | boolean |  | Not deactivated. |

#### PageNow

The tab in front, as it is now.

| Field | Type | Required | What it is |
|---|---|---|---|
| `title` | string |  | Its title. |
| `url` | string |  | Its address. |
| `progress` | number |  | How far it's loaded, 0 to 1. |
| `loading` | boolean |  | Loading. |
| `back` | boolean |  | It can go back. |
| `forward` | boolean |  | It can go forward. |

#### PointerEvent

What a Bot's tools did with the mouse, numbered (seq starts again from 1 with each Emission).

| Field | Type | Required | What it is |
|---|---|---|---|
| `seq` | integer | yes | Its number. |
| `kind` | `hover` \| `click` \| `drag` \| `scroll` |  | What it was. |
| `x` | number |  | Where, in CSS pixels. |
| `y` | number |  | Where, in CSS pixels. |
| `path` | array of array of number |  | A drag's path from there, 40 points at most. |
| `count` | integer |  | Clicks, more than one. |
| `dx` | number |  | A scroll's. |
| `dy` | number |  | A scroll's. |

#### Provider

A model provider, as the mesh is told of it (mesh.published): no address or key of its machine's.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id. |
| `kind` | string |  | What it is (openai, ollama, apple, mlx, ...). |
| `name` | string |  | Its name. |
| `http` | boolean |  | It serves HTTP, run through a member's Proxy. |
| `models` | array of ProviderModel |  | Its models. |
| `base_url` | string/null |  | Where it's served, on its own machine (info's providers only). |

#### ProviderModel

A model a provider has, as the mesh is told of it (mesh._model).

| Field | Type | Required | What it is |
|---|---|---|---|
| `value` | string | yes | What it's run as. |
| `displayName` | string |  | Its name. |
| `id` | string |  | Its provider's id for it. |
| `tools` | boolean |  | It takes tools. |
| `thinking` | boolean |  | It thinks. |
| `window` | integer |  | Its context window. |
| `version` | string |  | Its version. |
| `digest` | string |  | Its digest. |
| `input` | array of string |  | What it takes in. |

#### Proxy

This host's Proxy, which runs other members' models over HTTP.

| Field | Type | Required | What it is |
|---|---|---|---|
| `url` | string |  | Where it is. |
| `key` | string |  | What it's asked with. |

#### PullInfo

A GitHub pull request, as the Git page keeps it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `number` | integer |  | Its number. |
| `title` | string |  | Its title. |
| `state` | string |  | Where it stands: open, draft, changes, merged or closed. |
| `url` | string |  | Its page. |
| `head` | string/null |  | The branch it's from (null for a fork's). |

#### PullLink

A pull request a chat's replies name.

| Field | Type | Required | What it is |
|---|---|---|---|
| `repo` | string/null |  | Its repository, owner/name (null for a bare #12). |
| `number` | integer |  | Its number. |

#### Record

A member's record: who it is and the members it's linked to. A host sends its own when it changes (seq higher than any it sent before) and passes each one newer than it had on to its other links.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | The member's id. |
| `seq` | integer | yes | Its number: higher, newer. |
| `info` | MemberInfo |  | Who it is. |
| `links` | array of string |  | The members it's linked to. |

#### Routine

A Bot's routine (baudelib.routines): work it's given on a schedule.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id. |
| `name` | string |  | Its name. |
| `prompt` | string |  | What it's to do. |
| `paused` | boolean |  | Paused. |
| `last` | number/null |  | When it last ran. |
| `every` | integer |  | Minutes between runs. |
| `at` | string |  | The host's local time it runs at, HH:MM. |
| `days` | array of integer |  | The days it runs at at, 0 Monday to 6 Sunday. |
| `next` | number/null |  | When it runs next. |
| `error` | string/null |  | Why its last run didn't. |

#### Running

A command a session's agent is running, as quit's busy and info's update list them.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `what` | string |  | What it runs. |

#### Sender

Who a message to a session is from, if not its user, filled in by the host from what it knows itself (wire.clean_sender: words, cut short).

| Field | Type | Required | What it is |
|---|---|---|---|
| `kind` | `bot` \| `chat` \| `subagent` \| `host` | yes | What it is. |
| `name` | string |  | Its name. |
| `host` | string |  | Its host's id. |
| `host_name` | string |  | Its host's name. |
| `session` | string |  | Its session's id, on its host. |

#### Session

A session as a host lists it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | A UUID the host made as it started it (claude's --session-id: what it's resumed by). |
| `title` | string/null |  | Its title: given, its agent's, or its prompt's start. |
| `cwd` | string/null |  | The folder its agent runs in. |
| `model` | string/null |  | Its model. |
| `effort` | string/null |  | Its effort. |
| `mode` | string/null |  | Its permission mode. |
| `group` | string/null |  | Its group. |
| `created` | number/null |  | When it was made, epoch seconds. |
| `said` | number/null |  | When something was last said in it, epoch seconds. |
| `updated` | number/null |  | Its transcript's mtime. |
| `size` | integer |  | Its transcript's size. |
| `state` | `working` \| `waiting` \| `idle` \| `stopped` |  | working: at a turn; waiting: asking permission; idle: its agent running, between turns; stopped: no agent running. |
| `prompts` | integer |  | How many prompts it's had. |
| `cli_id` | string/null |  | The id claude has for it where that's another (a fork's: its transcript's named so). |
| `transcript` | string/null |  | Where its transcript is on its host's machine (what's on the same machine reads it there rather than fetching a copy). |
| `archived` | boolean |  | Archived. |
| `draft` | boolean |  | A new chat not written in yet (a head leaves it out of its lists). |
| `written` | integer/null |  | Its transcript's size as its own agent last left it (more since, with it stopped: another program's writing to it). |
| `subagents` | number |  | The mtime of its subagents' newest file (0 for none: what a client's copy is checked against). |
| `commands` | array of Command |  | What its agent is running; none once its agent's gone. |
| `cut` | number/null |  | When the host process stopped its agent at a turn or asking (shutting down, a quit, a restart), so a head can offer to carry it on; null once a user message is sent to it. |
| `bot` | BotListing? |  | A Bot's; null for a chat. |
| `acting` | boolean |  | Whether its turn is at its tools: from a tool call's start till text starts after it, the turn ends or its agent goes; only while it's working, not asking. |
| `on_phone` | boolean |  | A Bot's browser is on a phone now. |
| `fork_from` | string/null |  | The session it was forked from. |
| `spawned_by` | string/null |  | The chat whose mesh tools started it (start's spawned_by). |
| `start_key` | string/null |  | Its start's start_key, if it was given one. |
| `held` | array of HeldMessage |  | Messages held for it, oldest first. |
| `senders` | integer |  | Its sender store's revision (0 for none): what a head's copy is checked against. |
| `always_allow` | array of string |  | The tools allowed whenever it asks for them, sorted. |
| `asks` | array of Ask |  | What it's asking, not answered yet. |

#### SignalRef

A name Signal's glossary has.

| Field | Type | Required | What it is |
|---|---|---|---|
| `surface` | string |  | As it's written. |
| `kind` | string |  | What it names. |
| `canonical` | string/null |  | What it is. |
| `status` | string |  | Its status. |
| `summary` | string/null |  | What's said of it. |
| `locations` | array of object |  | Where it's mentioned: {role, path, line, note, rev, state, commit_sha, branch}. |
| `reason` | string |  | Why it wasn't found, or how its matches were told apart. |
| `data` | object |  | What was found (lookup.Ref.data): a commit's subject and branches, a pull request's state, a tool's inputs… |

#### StoredSender

Who a message to a session is from, as its host's sender store keeps it (by the message's uuid): a Sender, and what heads show of the message in place of its text, if the host says (shown).

| Field | Type | Required | What it is |
|---|---|---|---|
| `kind` | `bot` \| `chat` \| `subagent` \| `host` | yes | What it is. |
| `name` | string |  | Its name. |
| `host` | string |  | Its host's id. |
| `host_name` | string |  | Its host's name. |
| `session` | string |  | Its session's id, on its host. |
| `shown` | string |  | What heads show of the message in place of its text: set by the host only (a held answer sent: its shown, its agent having been told more). |

#### SubagentFile

One of a session's subagents' files, from where the client's copy is.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string |  | Its name. |
| `size` | integer |  | Its size. |
| `mtime` | integer/null |  | Its mtime (ns). |
| `offset` | integer |  | Where data starts. |
| `next` | integer |  | Where the next read starts. |
| `data` | string |  | What's new of it, in whole lines (a meta.json whole). |

#### Tab

A tab of a Bot's browser.

| Field | Type | Required | What it is |
|---|---|---|---|
| `tab` | string |  | Its id. |
| `title` | string |  | Its title. |
| `url` | string |  | Its address. |
| `loading` | boolean |  | Loading. |
| `selected` | boolean |  | In front. |
| `prompts` | array of object |  | What it's waiting on an answer to. |

#### TerminalInfo

A terminal running on a host.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id there, for attach. |
| `title` | string |  | Its title. |
| `argv` | array of string |  | What it runs. |
| `cwd` | string |  | Where it started. |
| `columns` | integer |  | Its width. |
| `rows` | integer |  | Its height. |
| `viewers` | integer |  | Connections attached to it now. |
| `running` | boolean |  | Whether its program is still running. |

#### Update

Its own updater has a newer version ready, and waits for those commands to end before it restarts onto it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `to` | string |  | The commit it'll restart onto. |
| `waiting` | array of Running |  | The commands it waits for. |

#### WaitTarget

Another chat's answer a session's agent waits on.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | That chat. |
| `host` | string/null |  | The member it's on; null for this host. |
| `kind` | `subagent` \| `chat` |  | What it is. |
| `text` | string/null |  | What it was asked. |
| `offset` | integer |  | Its transcript's size as it was asked. |
| `until` | number |  | When the wait ends, epoch seconds. |
| `title` | string |  | The chat's title. |

#### WatchSay

What a Browser pane says back to the Emission it watches.

| Field | Type | Required | What it is |
|---|---|---|---|
| `ack` | integer |  | A frame taken (its frame). |
| `max_w` | integer |  | The pane's width. |
| `max_h` | integer |  | The pane's height. |

#### WatchState

A watched Bot's browser as it is (emission's watch_state).

| Field | Type | Required | What it is |
|---|---|---|---|
| `profile` | string/null |  | Its profile. |
| `agent` | boolean |  | A Bot's (its tools drive it). |
| `view` | array/null of integer |  | The page's size, in CSS pixels. |
| `selected` | string/null |  | The tab in front. |
| `tabs` | array of Tab |  | Its tabs. |
| `prompts` | array of object |  | What's waiting on an answer: {id, tab, kind, ...} (a dialog's). |
| `downloads` | array of object |  | Its last downloads: {name, path, error?}. |
| `now` | PageNow? |  | The tab in front. |
| `pointer` | array of PointerEvent |  | What the Bot's tools last did with the mouse, its last 8, oldest first. |

#### Where

Somewhere wire.reach can go: this machine's host process, one listening on another socket, one over ssh, or a member of the mesh through this machine's.

| Field | Type | Required | What it is |
|---|---|---|---|
| `via` | `local` \| `unix` \| `ssh` \| `mesh` | yes | How it's reached. |
| `path` | string |  | unix: its socket; mesh: the host process it's reached through. |
| `dest` | string |  | ssh: where (an alias in ~/.ssh/config, a host, user@host); ssh runs HOST_COMMAND there. |
| `strict` | boolean |  | ssh: its key must be known already. |
| `host` | string |  | mesh: the member's id. |

### Constants

| Name | Value | What it is |
|---|---|---|
| `AGENT_CALLS_MAX` | `40` | Tool calls in one agent turn run on a phone, at most; then the model's asked to answer in words. |
| `ASK_S` | `60` | A subagent asking a permission no one's there to give (a mesh tool's start's ask_timeout): refused after this long, in seconds. |
| `BACKLOG` | `32` | A model's files' messages waiting to go out, on a link or a connection, before more are read. |
| `BATCH_CHARS` | `60000` | A browser batch's whole answer, in characters, at most, its pictures counted as their tokens' worth. |
| `BATCH_MAX` | `25` | A browser batch's actions, at most. |
| `BOT_PICTURE_MAX` | `2097152` | A Bot's picture, as bot sends it, at most, in bytes. |
| `DEFAULT_WINDOW` | `32768` | A model's context window, in tokens, where its server or settings don't say one. |
| `EPHEMERAL_S` | `1` | A side question whose head's gone (or detached): gone this soon, in seconds. |
| `FRAME_QUALITY` | `72` | A watched browser's frames' JPEG quality (emission_frame). |
| `GRACE_MAX_S` | `86400` | start's grace, at most, in seconds. |
| `HOST_COMMAND` | `~/.local/bin/baude-headless` | What ssh runs on a host to reach its host process, with connect after it: the command Baude installs on every machine (baudelib/bins.py), its ~ the host's home. |
| `IMAGE_BYTES_MAX` | `3750000` | An image attached to a message, at most, in bytes (5 MB once base64-encoded). |
| `IMAGE_EDGE_MAX` | `1568` | An image attached to a message: its longest side, in pixels, larger ones scaled down. |
| `INFER_S` | `600` | A provider has this long to answer infer, in seconds. |
| `OPEN_S` | `20` | A member of the mesh, reached through it: its tunnel has this long to open, in seconds. |
| `PING_S` | `5` | A connection's ends each say something at least this often (ping), in seconds. |
| `SHOT_MAX` | `1024` | A browser screenshot's longest side, in pixels. |
| `SHOT_QUALITY` | `82` | A browser screenshot's JPEG quality. |
| `SILENT_S` | `16` | Silent this long, in seconds, the other end's taken for gone. |
| `TERMINAL_KEEP_S` | `600` | A terminal (terminal op) no connection is attached to now: its program kept running this long to be attached to again, in seconds. |
| `TERMINAL_KEPT_BYTES` | `262144` | What a terminal (terminal op) wrote lately, kept to show again on attach: this many bytes at most. |
| `TTL` | `16` | A tunnel's passed through at most this many hosts (open's ttl). |
| `VERSION` | `19` | The wire's version, as a host's hello says it. |
| `ZOOM_MAX` | `1568` | A browser zoom's longest side, in pixels. |
| `ZOOM_QUALITY` | `90` | A browser zoom's JPEG quality. |
