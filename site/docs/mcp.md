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

### `tasks`

Your tasks (a Bot's; another Bot's, by its session): scripts your host runs, by hand or as a trigger fires (a schedule; a git push to a branch of a repo on the host), each one's triggers, form, last run and version. Their scripts call you mid-run (import baudetask: ask, fallback) when they can't decide or fail: see task for one's script.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string |  | A Bot's session id (yours if none's given) |

### `task`

One of your tasks: its script, triggers, form and variables (what each's bound to, and a warning for one bound to what isn't there now), and its latest runs (when, what started each, how long, how it ended, the version it ran).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string |  | A Bot's session id (yours if none's given) |
| `id` | string | yes |  |

### `set_task`

Make one of your tasks, or change one (by its id; a field left out kept as it was, but triggers and form, given, are given whole; variables and bindings too). A Python script imports baudetask: baudetask.inputs (its form, filled), baudetask.payload (its trigger's event), baudetask.vars (its variables: vars["Translation Model"] or vars.translation_model, typed, None if unbound, .warning if what it's bound to isn't there now; a model one passed straight to agent(model=)), say(text) (a line in its log), ask(text, form=None) (asks you: your answer, or the form's values), fallback(error), and @baudetask.main (an exception handed to you). A shell script gets its inputs as $BAUDE_IN_<FIELD>. A trigger's payload fills a field of the same name, or the one fills maps it to. Saving a new script makes a new version.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string |  | A Bot's session id (yours if none's given) |
| `id` | string |  | A task's id, to change it |
| `name` | string |  |  |
| `runtime` | `python` \| `shell` \| `bot` |  | bot: the script is a prompt you're given as a turn |
| `script` | string |  |  |
| `form` | array of object |  | Fields: {name, label?, type: text\|number\|bool\|choice, choices?, default?, required?} |
| `triggers` | array of object |  | {kind: schedule, every (minutes) or at (HH:MM) and days (0 Monday … 6 Sunday)} or {kind: git_push, repo (a folder on the host), branch, poll_s}; each may have fills: {field: payload key}. A git push's payload: repo, branch, before, after, commit, message, author |
| `variables` | array of object |  | Variables, each bound once for the task (not per run, as the form is): {name, type: model\|decider\|host\|browser_host\|terminal_host\|kube_context\|pod\|container\|database\|text, description?} |
| `bindings` | object |  | A variable's name: what it's bound to, {value, effort? (a model's), host? (the host serving a model), model? (a decider's), context?, namespace? (a pod's), container? (a database's)}, or just its value as text; given, given whole. A model's value is as hosts lists it; anything else is taken too. One bound to what isn't there is warned of, and the run starts anyway |
| `notify_failure` | boolean |  | A failed run is sent to you (default true) |
| `notify_exit` | boolean |  | A run that finishes with exit 0 is told to you, for your information (default false) |
| `timeout_s` | integer |  |  |
| `paused` | boolean |  |  |
| `delete` | boolean |  | Delete the task (id) |

### `run_task`

Run one of your tasks now, its form filled with inputs (the rest by default). Your own task's run is started and given at once (wait true: waited for until it ends or asks you, as you can only answer once this turn's over); another Bot's is waited for until it ends (wait false: at once). Gives its record and log; task_run reads it again.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string |  | A Bot's session id (yours if none's given) |
| `id` | string | yes |  |
| `inputs` | object |  |  |
| `wait` | boolean |  |  |

### `task_run`

One run of one of your tasks, and its log.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string |  | A Bot's session id (yours if none's given) |
| `run` | string | yes |  |

### `answer_task`

Answer an ask of one of your tasks' runs whose message says to answer with this tool (mcp__mesh__answer_task); the run's id is in its table. With a form, answer is an object of its fields (the ask lists them); with none, text. Its run goes on as soon as you've answered: what you say besides is only commentary. A wrong answer is refused with what's wrong: call it again. A message from a task that doesn't say to use this tool is answered in your reply, not with it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string |  | A Bot's session id (yours if none's given) |
| `run` | string | yes | The asking run's id (its table's run) |
| `answer` | object |  | The form's fields, by name: each a text, number, true or false, or one of a choice's choices |
| `text` | string |  | The answer to an ask with no form |

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

#### `access` (Access)

A host's access rules, and a chat's own if session's given (refused from a member access_read doesn't allow).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string/null |  | A chat of the host's: its own rule for managing it too. |

#### `access_set` (AccessSet)

One of a host's access rules set (capability: the rule's name), or a chat's own rule for managing it (session; capability manage_read or manage_write; rule null: the host's again). Refused (access_denied) from a member the access_write rule doesn't allow.

| Field | Type | Required | What it is |
|---|---|---|---|
| `capability` | any |  | The rule's name (AccessRules'): a chat's, manage_read or manage_write. |
| `session` | string/null |  | A chat of the host's: its own rule. |
| `rule` | AccessRule? |  | The rule (null, for a chat: the host's). |

#### `accounts` (Accounts)

The host's Claude and Codex subscription accounts (hostaccounts): list them; add one (Claude: name and an existing signed-in config folder, or name and token; Codex: name and an existing signed-in home, or name and token); rename one (id, name); replace a token account's token (id, token); remove one (id); use another for a chat (session, id; "" for the CLI's own sign-in: its agent started again under it, resuming the chat, once its turn's over; kept as the one the host's new chats on its provider's models take, defaults); read a subscription's usage (id; "" for the CLI's own sign-in of provider, Claude's where none is said); sign in (sign_in: provider and name for a new account, or id to sign in to one again; Claude's runs claude auth login with the account's own folder, its lines coming as they're written, the page to open among them; Codex's codex login (method: the browser's, by default, or the device code's), its state coming as it goes (login: the page to open, and the device code's code to enter there); then its end; the connection's going stops it); pass a code the sign-in page showed to the sign-in this connection runs (code: no answer of its own); pass the address a browser ended on after a Codex browser sign-in's page to the one this connection runs (callback: url; answered with the page codex's server gave, or error). Each other is answered with accounts.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | `list` \| `add` \| `rename` \| `token` \| `remove` \| `use` \| `usage` \| `sign_in` \| `code` \| `callback` | yes | What to do. |
| `provider` | `claude` \| `codex` |  | add, sign_in (a new account): whose. usage with id "": whose CLI's own sign-in (Claude's where left out). |
| `id` | string |  | rename, token, remove, use, usage, sign_in: the account ("" for the CLI's own sign-in, use and usage only). |
| `session` | string |  | use: the chat whose account it is, one of the host's sessions whose model is the account's provider's. sign_in (a Claude account's) and token: the chat it's for, whose sign-in (ChatLogin) its host moves as it goes, as a login request's, if the asker may read the chat (Agent Management's read). |
| `name` | string |  | add, rename, sign_in (a new account): its name. |
| `token` | string |  | add (Claude, Codex), token: what signs the account in, pasted: Claude's a long-lived token from claude setup-token (sk-ant-oat…) or an Anthropic API key (sk-ant-api…); Codex's an OpenAI API key (sk-…), written to a new CODEX_HOME's auth.json as codex login --with-api-key writes it. Kept on the host only, owner-only; never said back. |
| `home` | string |  | add (Codex): an existing CODEX_HOME on the host, signed in with ChatGPT and keeping its credentials in a file. |
| `folder` | string |  | add (Claude): an existing Claude config folder on the host, signed in (claude auth status says so, run with it). |
| `code` | string |  | code: what the sign-in page showed, pasted. |
| `method` | `browser` \| `device` |  | sign_in, Codex's: browser (the default: codex login, its page sending the browser back to the host's 127.0.0.1) or device (codex login --device-auth). |
| `url` | string |  | callback: the address the browser ended on (its path and query are what's passed on). |
| `cb` | integer |  | callback: a number of the asker's pick, carried back on its answer (one sign-in can have several callbacks waiting). |

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

#### `answer_task` (AnswerTask)

An ask of one of a Bot's tasks' runs (task_ask) answered: the Bot's answer_task tool's, or anyone's who may manage the Bot. Taken only for an ask of that run that's waiting on that Bot; the run goes on at once. Refused, with what's wrong, if it doesn't fit the ask's form.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The Bot's session id: the one the ask's waiting on. |
| `run` | string | yes | The asking run's id. |
| `answer` | object |  | An ask with a form: its fields' values, by name. |
| `text` | string |  | An ask with no form: the answer. |

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

#### `autofix` (Autofix)

A chat's Auto-fix: on, its branch's open pull request found (gh) and followed; off; or as it is, on left out. Refused, saying why, if the branch has no open pull request or gh can't ask GitHub.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `on` | boolean |  | On or off. |

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

#### `browser_open` (BrowserOpen)

Emission's window on the host's own screen, on its Default profile (☰ → Browser): the one that has it brought to the front, else one started.

#### `catalogue` (Catalogue)

What @ lists for one of its sessions' folders, made there (lookup.items: its files, branches, Kubernetes objects, what's running on that machine, its pull requests and issues): what there is, at once; waited for while it's made if there's none yet, or with wait.

| Field | Type | Required | What it is |
|---|---|---|---|
| `cwd` | string |  | The folder. |
| `wait` | boolean |  | Wait for it. |

#### `chat_size` (ChatSize)

How big a chat is, worked out by its host from its transcript as it is now (never kept with telemetry).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `close` (Close)

A tunnel closed, from either end; or (agent) the program's stdin closed.

| Field | Type | Required | What it is |
|---|---|---|---|
| `t` | integer |  | The tunnel, by the number of the end that opened it (this end's). |
| `r` | integer |  | The tunnel, by the number of the end that opened it (the other end's). |
| `error` | string |  | Why. |

#### `databases` (Databases)

The running databases, read-only: their servers and databases (servers), one database's schema (schema), a table's first rows (rows), a query's rows (query: Postgres's, in a read-only transaction). Secret-looking columns are masked here.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | `servers` \| `schema` \| `rows` \| `query` |  | What to read. |
| `container` | string |  | schema, rows, query: the server's container. |
| `database` | string |  | schema, rows, query: the database. |
| `schema` | string |  | rows: the table's schema. |
| `table` | string |  | rows: the table. |
| `sql` | string |  | query: one statement that reads (SELECT, WITH, VALUES, TABLE), run read-only; its rows come back as rows' do, secret-looking columns masked, QUERY_ROWS_MAX of them at most. |

#### `deactivate` (Deactivate)

A member deactivated here: its links let go of and not made, its joins and tunnels refused.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string |  | Its id. |
| `where` | Where |  | Where a link goes. |

#### `decide` (Decide)

A decision asked of one of the host's deciders, or a member's through it (baudetask.decide): the state, and each question about it by name. Answered with decided once it's asked. A task's script asks on its host's own socket with its run's token; a member, as its access rules let it use the host's providers.

| Field | Type | Required | What it is |
|---|---|---|---|
| `decider` | string/null |  | Which (a Decider's value: one of this host's by its id or name, another member's as <member>/<its id or name>); none: this host's first. |
| `state` | string/object/array |  | Text, or structured data the decider renders as it does text. |
| `questions` | object | yes | The questions, by name. |
| `images` | array of string |  | Base64 images, part of the state. |
| `run` | string |  | A task's script's run (BAUDE_TASK_RUN). |
| `token` | string |  | Its run's token (BAUDE_TASK_TOKEN). |

#### `deciders` (Deciders)

The host's deciders (Settings → Dependencies, a task's decider variable): list them, each with whether it answers, and the mesh's other members' as their records say; add one (name, url: a Strands Decider's server's base, http://127.0.0.1:8000), looked at as it's added; remove one (id); test one (id): looked at now. Each is answered with deciders.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | `list` \| `add` \| `remove` \| `test` |  | What to do (list where left out). |
| `name` | string |  | add: its name. |
| `url` | string |  | add: where it's served. |
| `id` | string |  | remove, test: which. |

#### `delete` (Delete)

A session gone: its agent stopped and waited for, its transcript and the folder beside it (its subagents'), pi's own copy of it, and a Bot's folder moved to the trash; then it's gone from the list. One whose files can't be moved stays: error, saying why.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `deps` (Deps)

The programs and libraries this host's features and its machine's apps need (schema/deps), each there or not, with the checks of what's installed (a kubeconfig's contexts, gh signed in, Ollama running).

#### `detach` (Detach)

This connection detached from a session (an ephemeral one its head's done with dropped at once).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |

#### `docker` (Docker)

Docker's containers, images, volumes, networks and Compose projects on the host, read and acted on through its docker CLI: its contexts and the sidebar's categories (contexts), a category's objects (list), an object inspected (inspect), a container's or Compose project's log as it is now (logs), a command run in a running container (exec: no terminal, its output when it ends), and a container or Compose project started, stopped, restarted or removed (start, stop, restart, remove; an image, a volume or a network removed).

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | `contexts` \| `list` \| `inspect` \| `start` \| `stop` \| `restart` \| `remove` \| `logs` \| `exec` |  | What to do. |
| `context` | string |  | The context, by its name, as contexts lists it. |
| `page` | string |  | list, inspect, logs, exec and the actions: the category's page, by its key. |
| `id` | string |  | inspect, logs, exec and the actions: the object, by its id (a Compose project's, its name). |
| `format` | `json` \| `yaml` |  | inspect: as JSON (the default) or YAML. |
| `tail` | integer |  | logs: its last this many lines (the Docker view's number if none). |
| `command` | array of string |  | exec: what's run, its argv (docker exec, with no terminal and nothing on its stdin). |
| `timeout` | number |  | exec: seconds it may run (60 if none; 600 at most): then it's stopped. |

#### `docker_follow` (DockerFollow)

A container's or a Compose project's log, followed (logs): one a connection, the answers docker_text till docker_end or the connection goes (docker is stopped then).

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | `logs` |  | What to follow. |
| `context` | string |  | The context, by its name. |
| `page` | string |  | The category's page, by its key: a container's or a Compose project's. |
| `id` | string |  | The container's id, or the Compose project's name. |

#### `draft_save` (DraftSave)

A chat's draft written (a change to the chat: Agent Management's write), kept by its host as the newest whatever was there (the last write wins), and told every other head attached to it (draft_changed). Sent a moment after typing stops, as the chat's left, and at once as the box is emptied by sending.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `text` | string | yes | What's written in its box now (empty: nothing, as once it's sent). |
| `head` | string/null |  | The box writing it, an id of the head's own pick (one each window or chat page): carried in the draft as it's told, so the box knows its own save. |

#### `edit_adopt` (EditAdopt)

Edits waiting on this host's notebooks that another machine's Record kept (Meta's, from before edits were the notebook host's), handed to the host once, each with its own id, by and t. One whose id the host has (waiting or decided), or whose notebook it hasn't, is left out. Answered with edits: those taken in.

| Field | Type | Required | What it is |
|---|---|---|---|
| `edits` | array of Edit | yes | The edits, each pending. |

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
| `default` | boolean |  | The call is to Emission's own window here on its Default profile instead of a Bot's (☰ → Browser from another host, watched): what a Browser pane does (tabs, navigate, new_tab, close_tab, select_tab, click, hover, drag, type, key, scroll, answer_prompt), nothing else. |
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
| `default` | boolean |  | Emission's own window here watched instead of a Bot's, on its Default profile (☰ → Browser from another host): started (on this host's screen) if none has the profile; checked for browser_write. |
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
| `as` | string |  | A chat on the host it's from (its own tools, on its host's socket): judged by the access rules as the member that started it, if one did. |
| `task` | object |  | A task's script on its host's socket: its run's id and token, as its environment names them (BAUDE_TASK_RUN, BAUDE_TASK_TOKEN). From then on the connection is the run's Bot's: the access rules judge it as they judge the Bot's own tools (as the member that started the Bot, if one did), its emission and multiplex calls drive the Bot's own browser and terminals, and what it relays through the mesh is from the Bot. Refused (access_denied) if no run going here has that token. |
| `task.run` | string | yes | The run's id. |
| `task.token` | string | yes | The run's token. |

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

A cluster's objects as Freelens lists them, read through the host's kubeconfig and kubectl: its contexts (contexts), whether a context is still there as the head knows it (check: answered removed, or with nothing else; kubectl isn't run), one connected to (connect), a kind's objects (list), an object's YAML (yaml), what controls an object (owner), a pod deleted (delete), objects applied from YAML (apply), a pod's log as it is now (logs), a command run in a pod's container (exec: no terminal, its output when it ends), the whole stack's CPU and memory (summary, a read as list is). Secrets' values are never read.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | `contexts` \| `check` \| `connect` \| `list` \| `yaml` \| `owner` \| `summary` \| `delete` \| `apply` \| `logs` \| `exec` |  | What to do. |
| `context` | string |  | The kubeconfig context. |
| `server` | string |  | The context's server as the head knows it (KubeContext's): a context of that name with another server is another cluster, and the request is answered removed. |
| `page` | string |  | list, yaml: the kind's page, by its resource. |
| `crd` | string |  | list, yaml: a custom resource's definition (plural.group) for a kind that isn't listed. |
| `selector` | string |  | list: only the objects whose labels match this selector (kubectl's -l: app=web,tier!=cache); a Secret's or a Helm release's page lists every one. |
| `namespaces` | array of string |  | summary: the namespaces it's the totals of (every one when it's left out; none when it's empty). |
| `since` | number |  | summary: the newest history sample the head has (its at): only later ones are sent (every one when it's left out). |
| `fresh` | boolean |  | summary: read the cluster now (Refresh), not a read the host kept or the fault it holds while it waits to try a failing cluster again. |
| `namespace` | string |  | yaml, owner, delete, logs, exec: the object's namespace; apply: where objects that name none go. |
| `name` | string |  | yaml, owner, delete, logs, exec: the object's name (logs, exec: the pod's). |
| `kind` | string |  | owner: the object's kind. |
| `force` | boolean |  | delete: at once, not waiting for the pod to stop. |
| `manifest` | string |  | apply: the objects, as YAML or JSON (kubectl apply -f -). |
| `container` | string |  | logs, exec: the container (the pod's default if none). |
| `previous` | boolean |  | logs: its previous run's. |
| `tail` | integer |  | logs: its last this many lines (the Kubernetes view's number if none). |
| `command` | array of string |  | exec: what's run, its argv (kubectl exec --, with no terminal and nothing on its stdin). |
| `timeout` | number |  | exec: seconds it may run (60 if none; 600 at most): then it's stopped. |

#### `kube_follow` (KubeFollow)

The kubeconfig's contexts followed (contexts: kube_contexts, sent again whenever tools add or delete clusters), a pod's log followed (logs), or a kind's list watched (watch): one a connection, the answers kube_contexts, kube_text and kube_rows till the connection goes (kubectl is stopped then), or till the context goes from the kubeconfig (kube_end, removed).

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | `contexts` \| `logs` \| `watch` |  | What to follow: the kubeconfig's contexts (kube_contexts, as they change), a pod's log, or a kind's list. |
| `context` | string |  | The kubeconfig context. |
| `server` | string |  | The context's server as the head knows it (KubeContext's): a context of that name with another server is another cluster, and the request is answered removed. |
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

claude auth login run on the host (a chat's claude signed in again there): one a connection, its answers login_said as it says each line, then login_end; the connection's going stops it. session: the chat it's for, whose sign-in (ChatLogin) its host moves as it goes and tells every head attached to it (login_state).

| Field | Type | Required | What it is |
|---|---|---|---|
| `args` | array of string |  | claude auth login's arguments (--console, --sso, --email <address>). |
| `session` | string/null |  | The chat it signs in for, if any: ignored (the sign-in runs, for no chat) if the asker may not read it (Agent Management's read). |

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
| `moved` | object |  | With write: pages moved or renamed, {notebook id: {old name: new name}}; the edits waiting on them are moved with them, in the same op. |

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

#### `restart` (Restart)

The host process's restart, as a head asks for it: once nothing's running (its pending restart, info's restart, kept by the host), now, or held while this head shows what it waits for.

| Field | Type | Required | What it is |
|---|---|---|---|
| `do` | `free` \| `now` \| `pause` | yes | free: restart once no chat's running a command and no task's running (and this head's pause let go of); now: restart at once, what's running stopped and turns cut short; pause: no restart while this head holds it (till it says free, or its connection goes). |

#### `rotate` (Rotate)

A Bot's conversation archived as a past thread of its own ("<Bot> / <its title>", read-only), and the Bot carried on in a new, empty one with a new agent: its id, name, picture, memory, tasks, browser and rules as they were. Refused while it's at a turn or one of its tasks is asking it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The Bot's session id. |

#### `run_task` (RunTask)

One of a Bot's tasks run now, by hand.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `id` | string | yes | The task's id. |
| `inputs` | object |  | Its form, filled. |
| `waited` | boolean/null |  | Its Bot waits for the run's end in a tool call (its own run_task, wait true): it isn't told the run finished. |

#### `seen` (Seen)

A session seen: a head shows it on screen (its window the one in front, its chat shown; the phone's app in front, on its chat), up to upto. Its last turn's end is seen once upto reaches it, and every head is told so in its listing (seen): each takes back its notification of it, and its dot.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `upto` | integer | yes | How much of its transcript the head shows (its size, as the head has read it). |

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
| `task_run` | object |  | A task's run going here that starts it, as the run's environment names it (BAUDE_TASK_RUN, BAUDE_TASK_TOKEN): nested under its Bot (spawned_by), kept in the run's chats, judged as the member that ran it, and its first turn's end told its Bot. Refused if no run's going with that token. |
| `task_run.run` | string | yes | The run's id. |
| `task_run.token` | string | yes | The run's token. |
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

#### `task` (TaskGet)

One of a Bot's tasks, its script and latest runs.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `id` | string | yes | The task's id. |
| `script` | boolean |  | Whether the task's script comes back with it (its version always does); left out, it doesn't. |

#### `task_ask` (TaskAsk)

A running script asks its Bot (on the host's own socket, with its run's token): something it can't decide, or (error) a failure handed over. Sent the Bot as a turn of its own once it's idle, answered as the Bot calls its answer_task tool (answer_task); a turn that ends without it reminded once, then failed (refused, as a failure, for a task whose failures aren't handed over too).

| Field | Type | Required | What it is |
|---|---|---|---|
| `run` | string | yes | Its run's id. |
| `token` | string | yes | Its run's token. |
| `text` | string | yes | What's asked. |
| `form` | array of TaskField |  | Fields the Bot's to fill: its answer's values. |
| `error` | boolean |  | It's an error handed over. |

#### `task_bind` (TaskBind)

One of a Bot's task's variables bound (binding), or let go of (none): answered as task is, with the variable's warning as it's looked at now.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `id` | string | yes | The task's id. |
| `name` | string | yes | The variable's name. |
| `binding` | TaskBinding? |  | What it's bound to; none: unbound. |

#### `task_choices` (TaskChoices)

What a task's variable of a type could be bound to, as the Bot's host has them.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `type` | `model` \| `decider` \| `host` \| `browser_host` \| `terminal_host` \| `kube_context` \| `pod` \| `container` \| `database` \| `text` | yes | The variable's type (TaskVariable's). |

#### `task_output` (TaskOutput)

The last part of a background task's output file, one a session's transcript names. Answered as file.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session whose task it is. |
| `path` | string | yes | The output file a task's start or end named. |
| `limit` | integer |  | Its last this many bytes (4000 if absent). |

#### `task_report` (TaskReport)

A running script's line in its run's log (on the host's own socket, with its run's token).

| Field | Type | Required | What it is |
|---|---|---|---|
| `run` | string | yes | Its run's id (BAUDE_TASK_RUN). |
| `token` | string | yes | Its run's token (BAUDE_TASK_TOKEN). |
| `say` | string | yes | The line. |

#### `task_run` (TaskRunGet)

One of a Bot's tasks' runs and its log.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `run` | string | yes | The run's id. |

#### `task_stop` (TaskStop)

A run of one of a Bot's tasks stopped.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `run` | string | yes | The run's id. |

#### `tasks` (Tasks)

A Bot's tasks: set, one made or changed (by its id, with its script); delete, one gone (its id).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `set` | object |  | A task (as Task). |
| `delete` | string |  | A task's id. |

#### `telemetry` (Telemetry)

An app's telemetry, told to its own machine's host as it happens (the phone's app to its own host), never on to another: what was done in chats, and what it profiled. Kept on that machine only (its state folder's telemetry/), while the host's telemetry is on. Its process id is how the host samples it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `app` | `baude` \| `record` \| `phone` | yes | The app it's of: Baude, Record, or the phone's app. |
| `pid` | integer | yes | The app's process id. |
| `events` | array of TelemetryEvent |  | What happened since it last told (each at its own time). |
| `profile` | TelemetryProfile? |  | What it profiled since it last told, if it's profiling. |
| `profile_error` | string/null |  | Why it isn't profiling what it's told to, if it isn't. |

#### `telemetry_read` (TelemetryRead)

A host's telemetry over a stretch: its chat events, its samples, its line profiles, and its turns added up; its telemetry state with it. Any member of the mesh may ask any other: one machine pulls every host's and puts them together by their times.

| Field | Type | Required | What it is |
|---|---|---|---|
| `from` | number | yes | From, in seconds since the epoch. |
| `to` | number/null |  | To (null: now). |
| `what` | array of `events` \| `metrics` \| `profiles` \| `usage` |  | What's wanted (none: only the state). |
| `limit` | integer/null |  | The most events, samples and profiles each (the latest kept; default 5000). |

#### `telemetry_set` (TelemetrySet)

A host's telemetry switched on or off, its line profiling switched on or off, or what a process on its machine profiles named (or its defaults again), at run time: kept by the host, and told to its machine's apps (Info's telemetry).

| Field | Type | Required | What it is |
|---|---|---|---|
| `on` | boolean/null |  | Telemetry on or off (null: as it is). |
| `line_profiling` | boolean/null |  | Line profiling on or off: the host's process, Baude's and Record's each profiling what it's told to while telemetry's on too (null: as it is). |
| `app` | any |  | The process whose profiled functions are named or put back to its defaults: the host's, Baude's or Record's (null: none). |
| `functions` | array/null of string |  | What app's process profiles in place of its defaults: functions and modules by their dotted names; empty: nothing (null: as it is). |
| `default` | boolean/null |  | True: what app's process profiles is its defaults again (Baude's own pick, lineprofile.DEFAULTS), what was named in their place dropped. |

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

#### `theme_remove` (ThemeRemove)

A theme taken away (the undo of one made).

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string | yes | Its name. |

#### `theme_save` (ThemeSave)

A theme made, or its files replaced, once its tokens are checked (Meta's create_theme and install_theme, and their undo).

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string | yes | Its name: 1 to 40 letters, digits, spaces, - or _. |
| `css` | string/null |  | Its theme.css; null or left out: none. |
| `tokens` | object/null |  | Its tokens.json (ThemeFiles' tokens); null or left out: none. |

#### `themes` (Themes)

The themes this host keeps, each worked out (Settings → Theme, on every app).

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

#### `access` (AccessReply)

access's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `state` | AccessState | yes | The host's rules now. |
| `session` | string/null |  | The chat asked about. |
| `chat` | AccessChat? |  | That chat's own rules for managing it (null, or a side not there: the host's). |

#### `access_set` (AccessSetReply)

access_set's answer: the rules as they are now.

| Field | Type | Required | What it is |
|---|---|---|---|
| `state` | AccessState | yes | The host's rules now. |
| `session` | string/null |  | The chat asked about. |
| `chat` | AccessChat? |  | That chat's own rules for managing it (null, or a side not there: the host's). |

#### `accounts` (AccountsReply)

accounts' answer: the accounts and defaults as they are after it, and whether the host has claude and what it lacks of each provider's program and each subscription's last read usage (with error, if it couldn't be done); usage's, the limits; sign_in's, each line Claude's sign-in writes (line alone), or Codex's state as it goes (login alone), then its end; callback's, the page codex's server answered (page alone), or error.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | string |  | What was asked. |
| `error` | string |  | Why it couldn't be done, for the user as it is. |
| `accounts` | array of Account |  | The host's accounts, by provider then name. |
| `defaults` | object |  | The account each provider's new chats take: the one a chat's provider picker (accounts' use) last picked there, else the CLI's own sign-in. |
| `defaults.claude` | string |  | Claude's account's id, "" for the CLI's own sign-in. |
| `defaults.codex` | string |  | Codex's. |
| `usage` | array/null of UsageLimit |  | usage: the subscription's limits; for a Claude sign-in that can't be asked (a setup token's), as its chats' turns last said them (as_of); null where none can be said (an API key's, a Codex sign-in that isn't a ChatGPT plan's, a setup token's before its first turn: after_turn). |
| `as_of` | number |  | usage: where its limits are as the subscription's chats' turns last said them (its sign-in can't be asked: a setup token's), when they last said them, in seconds since 1970; shown as "Last update <how long ago>" under them. |
| `after_turn` | boolean |  | usage: true where its limits can only be said by its chats' turns (a setup token's) and none has said them yet (usage null). |
| `api_key` | boolean |  | usage: true where the account is signed in with an API key (paid per use), so it has no subscription limits (usage null). |
| `line` | string |  | sign_in (Claude's): a line the sign-in wrote (the page to open among them). |
| `login` | ChatLogin |  | sign_in (Codex's): its state as it goes (login alone: signing_in, then with the page, and the device code's code), then on its end, signed_in or failed. |
| `page` | CallbackPage |  | callback: what the Codex sign-in's server answered the address. |
| `cb` | integer |  | callback: the request's cb, carried back. |
| `signed_in` | boolean |  | sign_in: its end, signed in. |
| `claude_cli` | boolean |  | Whether the host has claude: a Claude account there is a folder signed in to with claude auth login; without it, a token from claude setup-token. |
| `codex_usage` | boolean |  | Whether the host reads a Codex subscription's usage (usage with provider codex, and last_usage's "codex"): a client shows Codex's CLI default card and Codex usage only where it does; an older host's usage read of "" is Claude's. |
| `missing` | object |  | What the host lacks of a provider's program, by provider (codex: its Codex CLI, as deps says it); a provider that's there isn't in it. Shown in place of Add account. |
| `last_usage` | object |  | Each Claude and Codex subscription's usage as the host last read it, by id ("" for Claude's CLI's own sign-in, "codex" for Codex's), where that read said its limits: a client shows it greyed till its own usage read answers. |
| `who` | string/null |  | sign_in, signed in: who as, as claude auth status says (Codex's: the email its sign-in names). |

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
| `login` | ChatLogin? |  | Its sign-in, if a turn failed for want of it and that's not been put right yet (ChatLogin): the sign-in card every head draws. |
| `writes` | boolean |  | Whether the head may change the chat (Agent Management's write, the chat's own first): false, it only reads it, and answers none of its asks. |
| `draft` | ChatDraft? |  | Its draft, as its host keeps it (draft_save); null if none's been written, or the head may only read the chat. |

#### `autofix` (AutofixReply)

autofix's answer: the chat's Auto-fix as it is now.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `autofix` | AutofixPull? |  | The pull request it follows; null while it's off. |

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

#### `browser_opened` (BrowserOpened)

browser_open's answer: Emission's window is in front there.

| Field | Type | Required | What it is |
|---|---|---|---|
| `started` | boolean |  | Whether an Emission was started for it (none had the profile). |

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

#### `chat_size` (ChatSizeReply)

chat_size's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `bytes` | integer | yes | Its transcript's size. |
| `messages` | integer | yes | Its messages: each prompt sent and each reply, a tool's result not counted. |
| `images` | integer | yes | The images in it. |

#### `cwd_set` (CwdSet)

set_cwd's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `cwd` | string |  | The folder, real. |

#### `databases` (DatabasesReply)

databases' answer: servers {servers, databases}; schema {tables, links, took}; rows and query {rows}.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | string |  | What was read. |
| `error` | string |  | What's wrong. |
| `servers` | array of DbServer |  | servers: the servers found. |
| `databases` | object |  | servers: each server's (by container) databases. |
| `tables` | array of DbTable |  | schema: its tables. |
| `links` | array of DbLink |  | schema: their foreign keys. |
| `took` | number |  | schema: seconds it took to read, 0 when it was kept from a read before. |
| `rows` | array of object |  | rows: the first rows; query: its rows (QUERY_ROWS_MAX at most); each {column: value}, objects and lists as JSON text. |
| `missing` | Dep |  | servers: no server was found and Docker isn't on the host: Databases shows this in its place. |

#### `deactivated` (Deactivated)

deactivate's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `any` | boolean |  | There was one. |

#### `decided` (Decided)

decide's answer: each question's, by name; or, unavailable, why the decider couldn't be asked (not running, not answering, not there, its host out of reach), the script's to deal with: nothing else is asked instead.

| Field | Type | Required | What it is |
|---|---|---|---|
| `decider` | string |  | The decider asked, by its name. |
| `host` | string |  | Its host's name. |
| `model` | string |  | The model that answered. |
| `answers` | object |  | Each question's answer, by name. |
| `latency_ms` | number |  | How long it took its server. |
| `unavailable` | string |  | Why it couldn't be asked. |

#### `deciders` (DecidersReply)

deciders' answer: the host's own deciders (with their URLs), then the other members'.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | `list` \| `add` \| `remove` \| `test` |  | What was asked. |
| `deciders` | array of Decider |  | The deciders. |
| `tested` | Decider |  | add, test: the one looked at. |
| `error` | string |  | Why it couldn't be done (a name taken, a URL that isn't one). |

#### `deleted` (Deleted)

delete's answer (and an event to those attached: nothing to carry on).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `trashed` | array of string |  | What was moved to the trash. |

#### `deps` (DepsReply)

deps' answer: each of the host's dependencies on its platform.

| Field | Type | Required | What it is |
|---|---|---|---|
| `deps` | array of Dep |  | Each one. |

#### `dir` (Dir)

list_dir's answer: its entries, folders first (.git left out), DIR_LIMIT at most.

| Field | Type | Required | What it is |
|---|---|---|---|
| `path` | string |  | The folder. |
| `entries` | array of DirEntry |  | Its entries. |
| `more` | boolean |  | There are more. |
| `error` | string |  | Why it can't be read. |

#### `docker` (DockerReply)

docker's answer: contexts {contexts, sections}; list {columns, rows}; inspect {text, file}; logs {text}; exec {text, status}; start, stop, restart and remove {said}; fault when Docker can't be reached; missing when there's no docker.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | string |  | What was done. |
| `error` | string |  | What's wrong with the request. |
| `fault` | DockerFault |  | Docker can't be reached: why. |
| `contexts` | array of DockerContext |  | contexts: docker's contexts, and Colima's and Podman's found besides. |
| `sections` | array of DockerSection |  | contexts: the sidebar's categories. |
| `columns` | array of DockerColumn |  | list: the page's columns. |
| `rows` | array of DockerRow |  | list: the objects. |
| `text` | string |  | inspect: what docker inspect says of it; logs: its last lines; exec: what the command wrote, its stdout and stderr together. |
| `file` | string |  | inspect: what it is called, for its highlighting. |
| `said` | string |  | start, stop, restart, remove: what docker said. |
| `status` | integer |  | exec: the command's exit status (docker's, if it couldn't run it). |
| `missing` | Dep |  | docker isn't on the host: the Docker view shows this in its place. |

#### `docker_end` (DockerEnd)

docker_follow's last answer: docker ended.

| Field | Type | Required | What it is |
|---|---|---|---|
| `code` | integer |  | Its exit code. |
| `said` | string |  | What it wrote, for a failure. |

#### `docker_text` (DockerText)

docker_follow logs: more of the log.

| Field | Type | Required | What it is |
|---|---|---|---|
| `text` | string |  | The lines. |

#### `draft_saved` (DraftSaved)

draft_save's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `draft` | ChatDraft | yes | Its draft as kept now. |

#### `edit` (EditReply)

edit_propose's, edit_revise's, edit_withdraw's and edit_decide's answer: the edit as it is now.

| Field | Type | Required | What it is |
|---|---|---|---|
| `edit` | Edit | yes | The edit. |
| `took_in` | array of string |  | The edits a proposal reached into, taken back as it was revised to take them in: their ids. |

#### `edits` (EditsReply)

edits's answer, and edit_adopt's (its edits: those taken in).

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

Anything wrong, with the request's req. A request whose op the host doesn't take is answered with code unsupported_operation (error "unsupported operation"), and one whose fields aren't as the schema has them with code invalid_data_type (error "invalid data type"): the same from every host, what was wrong in detail. One a host's access rules refuse is answered with code access_denied (error "access denied"), denied naming the rule.

| Field | Type | Required | What it is |
|---|---|---|---|
| `error` | string | yes | What's wrong. |
| `code` | `unsupported_operation` \| `invalid_data_type` \| `access_denied` |  | One of the schema's own refusals. |
| `detail` | string |  | A refusal's: what was wrong (the op, or the field and why), for a person to read; code, not this, says which refusal it is. |
| `denied` | `shell` \| `browser_read` \| `browser_write` \| `providers` \| `model_files` \| `spawn` \| `manage_read` \| `manage_write` \| `kube_read` \| `kube_write` \| `docker_read` \| `docker_write` \| `databases` \| `subscriptions_read` \| `subscriptions_write` \| `access_read` \| `access_write` |  | access_denied's: the rule refused (AccessRules'): a capability, or one side of one split into read and write. |
| `plain` | boolean |  | Said for the user as it is. |
| `gone` | GoneFolder |  | A session's folder gone and not made again. |
| `missing` | Dep |  | What a chat's program needs that the host hasn't (claude, codex): error says what to install. |
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
| `missing` | Dep |  | git isn't on the host: the Git page shows this in its place. |

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

kube's answer: contexts {contexts, where}; connect {sections, namespaces, nodes}; list {columns, rows, metrics, page}; yaml {text, file, refs}; owner {kind, name, resource}; summary {summary}; delete and apply {said}; logs {text}; exec {text, status}; fault when a cluster can't be read; removed when its context has gone from the kubeconfig.

| Field | Type | Required | What it is |
|---|---|---|---|
| `what` | string |  | What was done. |
| `error` | string |  | What's wrong with the request. |
| `fault` | KubeFault |  | The cluster can't be read: why. |
| `removed` | boolean |  | The context isn't in the kubeconfig (any more): nothing was read. |
| `contexts` | array of KubeContext |  | contexts: the kubeconfig's. |
| `where` | string |  | contexts: the kubeconfig files, or what stands for none. |
| `sections` | array of KubeSection |  | connect: the sidebar's categories, each kind with whether the cluster serves it. |
| `namespaces` | array of string |  | connect: the cluster's namespaces. |
| `nodes` | integer |  | connect: how many nodes it has. |
| `page` | KubePage |  | list of a definition: its kind's page. |
| `columns` | array of KubeColumn |  | list: the page's columns on this cluster. |
| `rows` | array of KubeRow |  | list: the objects. |
| `metrics` | boolean/null |  | list: whether CPU and memory could be read (pods, nodes), or null for another kind. |
| `text` | string |  | yaml: the YAML (a Secret's keys only, a Helm release's JSON); logs: the log; exec: what the command wrote, its stdout and stderr together. |
| `file` | string |  | yaml: what it is called, for its highlighting. |
| `refs` | array of KubeRef |  | yaml: the names in it that are other objects. |
| `kind` | string |  | owner: the controlling kind, climbed up from a ReplicaSet or a Job. |
| `name` | string |  | owner: its name. |
| `resource` | string |  | owner: its kind's resource. |
| `summary` | KubeSummary |  | summary: the stack's CPU and memory. |
| `said` | string |  | delete, apply: what kubectl said. |
| `status` | integer |  | exec: the command's exit status (kubectl's, if it couldn't run it). |
| `missing` | Dep |  | kubectl isn't on the host: the Kubernetes view shows this in its place. |

#### `kube_contexts` (KubeContexts)

kube_follow contexts' answer: the kubeconfig's contexts, as the follow starts and each time they change.

| Field | Type | Required | What it is |
|---|---|---|---|
| `contexts` | array of KubeContext |  | The kubeconfig's contexts. |
| `where` | string |  | The kubeconfig files, or what stands for none. |

#### `kube_end` (KubeEnd)

kube_follow's last answer: kubectl ended.

| Field | Type | Required | What it is |
|---|---|---|---|
| `code` | integer |  | Its exit code. |
| `said` | string |  | What it wrote, for a failure. |
| `fault` | KubeFault |  | Why, when the cluster can't be read. |
| `removed` | boolean |  | Its context went from the kubeconfig (its cluster was deleted, or made again under the same name). |

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

#### `restart_pending` (RestartPending)

restart's answer: the restart it keeps, as info's restart says it (it goes once nothing blocks it and nobody's holding it).

| Field | Type | Required | What it is |
|---|---|---|---|
| `restart` | RestartState? |  | Its restart, or null: none pending. |

#### `rotated` (Rotated)

rotate's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | Session | yes | The Bot, as it's listed now: in its new conversation. |
| `thread` | Session | yes | Its past thread, as it's listed now: archived. |

#### `seen_set` (SeenSet)

seen's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `seen` | TurnEnd? |  | The last turn's end seen now (null: none yet). |

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

#### `task` (TaskReply)

task's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `task` | Task |  | The task, with its script. |
| `runs` | array of TaskRun |  | Its latest runs, newest first. |
| `clock` | Clock |  | The host's clock. |

#### `task_answer` (TaskAnswer)

task_ask's answer: the Bot's.

| Field | Type | Required | What it is |
|---|---|---|---|
| `text` | string | yes | What it said. |
| `values` | object/null |  | A form's values, as the Bot filled them. |

#### `task_answered` (TaskAnswered)

answer_task's answer: the ask answered, its run going on.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The Bot's session id. |
| `run` | string | yes | The run's id. |
| `task` | string | yes | The run's task, by its name. |
| `values` | object |  | A form's values, as they were taken (checked against its fields). |
| `text` | string |  | The answer to an ask with no form. |

#### `task_choices` (TaskChoicesReply)

task_choices' answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `type` | string |  | The type asked for. |
| `choices` | array of TaskChoice |  | What it could be bound to, best first. |

#### `task_reported` (TaskReported)

task_report's answer.

#### `task_run` (TaskRunReply)

task_run's and task_stop's answer.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `run` | TaskRun | yes | The run. |
| `log` | array of TaskLogLine |  | Its log (its last 2000 lines). |
| `bot` | string/null |  | Its Bot's name. |

#### `task_started` (TaskStarted)

run_task's answer: the run started.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `run` | TaskRun | yes | The run. |

#### `tasks` (TasksReply)

tasks' answer: a Bot's tasks (their scripts left out).

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `tasks` | array of Task |  | Its tasks. |
| `clock` | Clock |  | The host's clock. |
| `saved` | string/null |  | The id of the task set, if one was. |

#### `telemetry_kept` (TelemetryKept)

telemetry's answer: kept.

#### `telemetry_read` (TelemetryReadReply)

telemetry_read's answer, each list the earliest first.

| Field | Type | Required | What it is |
|---|---|---|---|
| `host` | string | yes | The host's id. |
| `state` | TelemetryState | yes | Its telemetry state. |
| `events` | array of TelemetryEvent |  | Its chat events. |
| `metrics` | array of TelemetrySample |  | Its samples. |
| `profiles` | array of TelemetryProfile |  | Its line profiles. |
| `usage` | TelemetryUsage? |  | Its turns added up. |

#### `telemetry_set` (TelemetrySetReply)

telemetry_set's answer: the host's telemetry state now.

| Field | Type | Required | What it is |
|---|---|---|---|
| `state` | TelemetryState | yes | Its telemetry state. |

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

#### `theme_remove` (ThemeRemoved)

theme_remove's answer: what it had.

| Field | Type | Required | What it is |
|---|---|---|---|
| `before` | ThemeFiles? |  | Its files, or null if it wasn't there. |

#### `theme_save` (ThemeSaved)

theme_save's answer: the theme as it is now, and what it had before.

| Field | Type | Required | What it is |
|---|---|---|---|
| `theme` | Theme |  | It, worked out. |
| `before` | ThemeFiles? |  | Its files before, or null if it's new. |

#### `themes` (ThemesReply)

themes' answer: each theme, by name.

| Field | Type | Required | What it is |
|---|---|---|---|
| `themes` | array of Theme |  | Each one, in order. |

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

#### `draft_changed` (DraftChanged)

A chat's draft written by a head (draft_save): told every other head attached to it that may write it, as attach's answer tells one attaching.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `draft` | ChatDraft | yes | Its draft now. |

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

#### `login_state` (LoginState)

A chat's sign-in changed on its host (a turn failed for want of it, a sign-in for it started, finished, failed or was cancelled): told every head attached to it, as attach's answer tells one attaching.

| Field | Type | Required | What it is |
|---|---|---|---|
| `session` | string | yes | The session's id. |
| `login` | ChatLogin? |  | Its sign-in now; null once a turn's gone through, or a sign-in for it with nothing to sign in for before was cancelled. |

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
| `uuid` | string/null |  | The uuid of the message it ended with (its listing's ended's: what a notification of it is known by); null for a chat that keeps no turn's end (ended). |
| `size` | integer/null |  | Its transcript's size as it ended (its listing's ended's). |
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

A tunnel to member to: passed on to the next host on the way (ttl hosts at most), as it came, and at to a connection to its host process, served as if a head had connected to its socket, from the member that signed it; closed there if it has no from, or isn't signed as from's key, now and once.

| Field | Type | Required | What it is |
|---|---|---|---|
| `t` | integer |  | The tunnel, by the number of the end that opened it (this end's). |
| `to` | string |  | The member it's to. |
| `ttl` | integer |  | Hosts it may pass through. |
| `from` | string |  | The member that opened it. |
| `sender` | Sender |  | Who what's sent through it is from (relay's as). |
| `signed` | string |  | JSON of {to, from, time (in seconds), nonce, sender}, as the member that opened it signed it: what's taken of who it's from where it's opened to. |
| `sig` | string |  | signed's Ed25519 signature, by from's key, in base64. |

#### `opened` (Opened)

A tunnel's there.

| Field | Type | Required | What it is |
|---|---|---|---|
| `t` | integer |  | The tunnel, by the number of the end that opened it (this end's). |
| `r` | integer |  | The tunnel, by the number of the end that opened it (the other end's). |

### Entities

#### AccessChat

A chat's own rules for managing it, each going before its host's of the same name; one not there is the host's. Checked as a pair: write lets in only those read does, whatever the host's rules are.

| Field | Type | Required | What it is |
|---|---|---|---|
| `manage_read` | AccessRule |  | Who may read it. |
| `manage_write` | AccessRule |  | Who may change it (and read it). |

#### AccessMember

A member a rule can name: one the host reaches, has lately, or a rule names.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id. |
| `name` | string | yes | Its name. |

#### AccessRule

Who in the mesh may use one of a host's capabilities: every member, none, or those chosen. A head on the host's own machine, or one over ssh, is never refused.

| Field | Type | Required | What it is |
|---|---|---|---|
| `who` | `everybody` \| `nobody` \| `chosen` | yes | Every member, none, or those in members. |
| `members` | array of string |  | chosen's: the members' ids. |

#### AccessRules

A host's rule for each of its capabilities, by name: a capability split into what reads and what changes has two, <capability>_read and <capability>_write (write lets a member read too); one with nothing to split has one. Everybody's where none's set, but access_write's, nobody's.

| Field | Type | Required | What it is |
|---|---|---|---|
| `shell` | AccessRule | yes | A shell on its machine: its terminals (terminal ops) and Multiplex's, through a Bot's terminal tools (multiplex), and a command run in a pod's or a container's (kube's and docker's exec). |
| `browser_read` | AccessRule | yes | Its Bots' Emission browsers watched: the watch stream (emission_watch) and what the pane says back on it (emission_watch_say). |
| `browser_write` | AccessRule | yes | Its Bots' Emission browsers driven: their tools' calls (emission), a phone showing one and answering its calls (emission_serve, emission_answer), Emission's own window brought up on its screen (browser_open), or watched and driven from elsewhere (emission_watch and emission, default), and a Bot made or driven (with Shell). Lets a member watch too. |
| `providers` | AccessRule | yes | Its inference providers' models, run through the mesh (infer). |
| `model_files` | AccessRule | yes | Its models' files, read through the mesh (files). |
| `spawn` | AccessRule | yes | New chats and agents started on it (start, agent). |
| `manage_read` | AccessRule | yes | Its chats as they are, read: listed (and told of their turns' ends), attached to and watched, their transcripts, sizes, subagents, senders, a Bot's picture, memory and tasks read, their Auto-fix as it is, their folders read (read_file, list_dir, git, signal_names, catalogue), forked from; Record's notebooks here listed, and the edits waiting on their pages (notes, edits). A chat's own rule (AccessChat's manage_read) goes before this. |
| `manage_write` | AccessRule | yes | Its chats as they are, changed: written in, held messages sent or dropped, asks answered, stopped, renamed, archived, deleted, their folders moved, tools allowed always (attach's only where it adds some, its env only where it changes it), made Bots, their Auto-fix turned on or off, a Bot's memory written and tasks set, run and stopped, a chat adopted, moved to another subscription (with Subscriptions' write); Record's notebooks here written, and edits to their pages proposed, revised, withdrawn, decided and adopted (notes' write, edit_*). Lets a member read them too. A chat's own rule (AccessChat's manage_write) goes before this. |
| `kube_read` | AccessRule | yes | Its machine's Kubernetes clusters read: contexts, lists, YAML, owners, a pod's log (kube) and logs and watches (kube_follow), and the clusters' parts of @. |
| `kube_write` | AccessRule | yes | Its machine's Kubernetes clusters changed: a pod deleted, objects applied (kube's delete, apply). Lets a member read them too. |
| `docker_read` | AccessRule | yes | Its machine's Docker read: contexts, lists, an object inspected, a log as it is now (docker) and logs (docker_follow). A shell in a container is a terminal: Shell's. |
| `docker_write` | AccessRule | yes | Its machine's Docker changed: a container or Compose project started, stopped, restarted or removed, an image, volume or network removed (docker's start, stop, restart, remove). Lets a member read it too. |
| `databases` | AccessRule | yes | Its machine's databases, read (databases): their schemas, a table's rows, a query run read-only. |
| `subscriptions_read` | AccessRule | yes | Its subscription accounts read: listed, and their usage (accounts' list and usage). |
| `subscriptions_write` | AccessRule | yes | Its subscription accounts changed: added, renamed, a token replaced, removed (signed out), a provider's default picked, a chat moved to one (with the chat's Agent Management write too), signed in to (accounts' sign_in and code, login, login_code). Lets a member read them too. |
| `access_read` | AccessRule | yes | These rules read (access), and the host's own: its telemetry (telemetry_read) and its Ongoing Feedback listed (fixes' list and shot). |
| `access_write` | AccessRule | yes | These rules changed (access_set), and the host itself: stopped, linked and unlinked, deactivated, its telemetry, themes and feedback (quit, link, unlink, deactivate, activate, telemetry_set, theme_save, theme_remove, feedback), its Ongoing Feedback acted on (fixes' merge, discard, reopen, reply, fix, stop). Lets a member read them too. A head on its own machine, or one over ssh, may always. |

#### AccessState

A host's access rules, as access and access_set answer them.

| Field | Type | Required | What it is |
|---|---|---|---|
| `host` | string | yes | The host's name. |
| `rules` | AccessRules | yes | Each capability's. |
| `members` | array of AccessMember | yes | The members its rules can name, by name. |
| `editable` | boolean | yes | The connection asking may change them (access_set): a head on its machine or over ssh, else a member the access_write rule allows. |

#### Account

A Claude or Codex subscription account kept on a host (Settings → Subscriptions): what a head sees of it; its credential never leaves the host.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id on its host (what a session's kept with). |
| `provider` | `claude` \| `codex` | yes | Whose subscription it is. |
| `name` | string | yes | Its name, the user's, unique for its provider on its host. |
| `kind` | `folder` \| `token` \| `profile` | yes | folder: a Claude account that is a config folder of its own on its host (CLAUDE_CONFIG_DIR), signed in to with claude auth login run there with it (signed in again the same way); token: a Claude account signed in with a token from claude setup-token or an Anthropic API key (signed in again by replacing it); profile: a Codex account, a CODEX_HOME of its own, signed in with ChatGPT or an OpenAI API key (signed in again with codex login). |

#### Ask

A permission its agent's asking for, not answered yet. Answered with input: a control_response for its request_id, as claude's stream-json has it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `request_id` | string | yes | The control request's id. |
| `tool` | string/null |  | The tool it asks to use. |
| `input` | object/null |  | The tool's input (long strings in it cut short). |

#### AutofixPull

A chat's Auto-fix while it's on: the pull request of the chat's branch its host follows, every five minutes, sending the chat a failing check, a merge conflict or a new comment.

| Field | Type | Required | What it is |
|---|---|---|---|
| `repo` | string | yes | Its repository, owner/name. |
| `number` | integer | yes | The pull request's number. |
| `url` | string |  | Its page. |
| `title` | string |  | Its title. |
| `error` | string/null |  | Why its last read or message didn't work (gh signed out, offline, ...), till one does; null if it did. |

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

#### CallbackPage

What a Codex browser sign-in's server on the host answered the address a browser ended on (accounts callback), for the head to show that browser: its own redirects back to itself followed.

| Field | Type | Required | What it is |
|---|---|---|---|
| `status` | integer | yes | Its HTTP status. |
| `type` | string |  | Its Content-Type. |
| `body` | string | yes | The page. |
| `location` | string |  | Where it sends the browser on to, if it does (a page of OpenAI's). |

#### ChatDraft

A chat's draft, as its host keeps it: the text written in its box and not sent, the same on every head that opens the chat, on any machine. Only the text: what's attached is a file on the head's own machine, kept there.

| Field | Type | Required | What it is |
|---|---|---|---|
| `text` | string | yes | What's written (empty once it's sent). |
| `rev` | integer | yes | Its revision: one more with every save, never less (a head drops one it's seen a later revision than). |
| `head` | string/null |  | The box that wrote it (draft_save's head): what that box knows as its own save, coming back. |
| `at` | number/null |  | When it was written, epoch seconds. |

#### ChatLogin

A sign-in, as its host has it: a chat's to Claude, from which every head draws the chat's sign-in card alone (LoginCard), whichever head started what's going on; or a Codex account's sign-in in Subscriptions (accounts sign_in's login), from which the head that asked draws its card.

| Field | Type | Required | What it is |
|---|---|---|---|
| `state` | `needed` \| `signing_in` \| `signed_in` \| `failed` | yes | needed: a turn failed for want of signing in (and every sign-in started since was cancelled); signing_in: claude auth login (codex login) runs on the host for it; signed_in: it finished; failed: it ended without signing in. |
| `method` | `browser` \| `device` |  | A Codex sign-in's: browser, codex login's own (its page sends the browser back to codex's server on the host's 127.0.0.1, at port); device, codex login --device-auth's (a code entered on its page). |
| `url` | string |  | signing_in, a Codex sign-in's: the page to open, once codex has said it. |
| `code` | string |  | signing_in, a Codex device-code sign-in's: the one-time code to enter on that page. |
| `port` | integer |  | signing_in, a Codex browser sign-in's: the port of the host's 127.0.0.1 its page sends the browser back to (1455, else 1457). |
| `opened` | boolean |  | signing_in, a Codex browser sign-in's: codex opened the page in the host's own browser itself (on macOS, where it doesn't go by BROWSER). |
| `expired` | boolean |  | needed because the sign-in there was ran out (expired, reached its time limit, can't be renewed), not because there's none. |
| `reason` | string/null |  | needed: the first sentence of Claude's own error; failed: why the sign-in didn't finish. |
| `who` | string/null |  | signed_in: who as, as claude auth status says it (Codex's: the email its sign-in names); null if it didn't say, or the head may not read Subscriptions. |
| `turn` | integer |  | needed: which of the chat's turns that failed for want of signing in it's for, counted on its host since it started: a new one each time one fails, so every head moves the card to that turn. |

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

#### ContextCategory

One kind of what a session's context holds (ContextUsage's categories): the system prompt, tools, messages, free space.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string | yes | What it is, as its agent names it. |
| `tokens` | integer | yes | The tokens it takes up. |
| `kind` | string/null |  | Its agent's kind for it (deferred: not in the context yet, left out of the count shown). |

#### ContextUsage

How much of its context window a session's agent last said it's used (Claude Code's get_context_usage answer, Codex's token count, pi-claude's count of what it sends), kept by its host: asked of its agent as it starts, through a turn while a chat has it open (at most once a second), as the turn ends, after a compaction and after its model's changed, and kept while its agent's stopped; given another model's window (or none) if one's picked while it's stopped. Every app's context meter shows it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `totalTokens` | integer/null |  | The tokens its conversation takes up now; null where its agent hasn't said. |
| `maxTokens` | integer/null |  | Its model's window, in tokens; null or 0 where its agent doesn't know it. |
| `showWithoutLimit` | boolean |  | Shown though there's no window (Codex's before it says one): the tokens used alone. |
| `categories` | array of ContextCategory |  | What the tokens are, as its agent splits them (its meter's tooltip). Left out of a session's listing: the chat that's open asks for them (get_context_usage). |

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

#### DecideQuestion

One of a decision's questions, as System One has them: noul (yes or no: the probability it's true), choice (one of criteria's options, by name) or score (an ordered rubric, criteria's levels from low to high).

| Field | Type | Required | What it is |
|---|---|---|---|
| `type` | `choice` \| `noul` \| `score` | yes | What kind of question it is. |
| `instructions` | string/object/array | yes | Text, or structured data the decider renders as it does text. |
| `criteria` | object/array/null |  | choice: each option's name and what it means (null for a bare name); score: its levels, lowest first; noul: what true and false mean, or nothing. |

#### Decider

A decider a host knows (baudelib/deciders.py): a server that answers Strands Decider's System One API (POST /v1/systemone, GET /health), as the deciders op lists it, and as a member's record says it (no url). What a task's decider variable binds, and what decide's decider names, is its value.

| Field | Type | Required | What it is |
|---|---|---|---|
| `value` | string |  | What decide's decider names it as, on the host that listed it: its id for one of that host's own, <its host's name>/<its id> for another member's. |
| `id` | string | yes | Its id on its host. |
| `name` | string |  | Its name. |
| `host` | string/null |  | The member that has it, by its id (the listing host's own: null). |
| `host_name` | string/null |  | That member's name. |
| `url` | string/null |  | Where it's served (the listing host's own only). |
| `reachable` | boolean |  | It answered as a decider when it was last looked at: a variable bound to one that didn't is warned of before a run. |
| `model` | string/null |  | The model it serves, as its /health says. |
| `error` | string/null |  | Why it didn't answer. |

#### Decision

A decider's answer to one question, as System One gives it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `type` | `choice` \| `noul` \| `score` | yes | Its question's kind. |
| `choice` | string |  | choice: the option picked. |
| `noul` | number |  | noul: the probability it's true. |
| `score` | number |  | score: the expected level, over the levels' indices (0 the lowest). |
| `probabilities` | object |  | choice: each option's probability; score: each level's, by its index. |
| `legend` | object |  | score: each level's index and what it is. |
| `confidence` | number |  | How sure it is, calibrated (choice and score; a noul's probability says it). |

#### Dep

A program or library a feature needs (schema/deps), as a host or an app has it: whether it's there, and how it's installed on that machine.

| Field | Type | Required | What it is |
|---|---|---|---|
| `dep` | string | yes | Its key in schema/deps (vte, kubectl, docker, …). |
| `name` | string | yes | Its name, as it's shown. |
| `where` | `app` \| `host` |  | What needs it: an app's own process, or the host process. |
| `present` | boolean | yes | It's there. |
| `fix` | string/null |  | The command that installs it on that machine (a check's: readies it; Ollama installed and not running: starts it), or null (another Linux: link). |
| `link` | string/null |  | Its install page (none for a check). |
| `group` | string/null |  | The schema/copy key of what Settings → Dependencies lists it under (deps.providers, window.kubernetes, …). |
| `check` | boolean |  | It's a check of an installed program (kubeconfig, GitHub Account): it has no page of its own. |
| `ready` | string/null |  | What's said of it while it's there (Installed, Signed in, Running, 3 models), as its host says it; "Couldn't check" when looking raised; null when it isn't there. |

#### DirEntry

An entry in a folder.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string |  | Its name. |
| `dir` | boolean |  | A folder. |
| `size` | integer |  | A file's size. |
| `ignored` | boolean |  | git ignores it. |

#### DockerColumn

A table's column.

| Field | Type | Required | What it is |
|---|---|---|---|
| `title` | string | yes | Its title. |
| `kind` | string/null |  | name, status, age, or null. |

#### DockerContext

A Docker context the host can reach: one of docker context ls, or Colima's or Podman's socket found besides.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string | yes | Its name. |
| `current` | boolean |  | docker's current context. |

#### DockerFault

Why Docker can't be reached, as docker's failure says.

| Field | Type | Required | What it is |
|---|---|---|---|
| `title` | string | yes | What went wrong. |
| `why` | string |  | What docker said that says why. |
| `fix` | string/null |  | A command to run in a terminal that fixes it (Docker started), or null. |
| `said` | string |  | All docker wrote. |

#### DockerPage

A page of the Docker view: what one of its sidebar's rows lists.

| Field | Type | Required | What it is |
|---|---|---|---|
| `title` | string | yes | Its name, as the sidebar has it. |
| `key` | string | yes | What it's asked for by: running, containers, images, volumes, networks or compose. |
| `what` | string |  | What it lists, in a word for a count: containers, images… |

#### DockerRow

An object's row.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | What it's asked for by: a container's, image's, volume's or network's id, a Compose project's name. |
| `name` | string | yes | Its name. |
| `cells` | array of string |  | Each column's text. |
| `keys` | array of any |  | Each column's sort key: a number or text. |
| `tones` | array of string |  | Each column's tone (ok, warn, bad), or empty. |
| `tips` | array of string |  | Each column's tooltip, or empty (an age's: its time in full). |
| `filters` | array of string |  | What each column's filter goes by, where it isn't the column's text, else empty (a container's Status: its state, Running or Exited, where the text says for how long). |
| `actions` | array of string |  | What can be done to it as it is: start, stop, restart, remove. |
| `logs` | boolean |  | It has a log to follow (docker_follow). |
| `shell` | array of string |  | A running container's: the command that opens a shell in it (bash if it has it, else sh), for a terminal on the host; else empty. |
| `running` | boolean |  | A container that's running, or a Compose project with one running. |

#### DockerSection

A category of the Docker view's sidebar.

| Field | Type | Required | What it is |
|---|---|---|---|
| `title` | string | yes | Its name. |
| `icon` | string |  | Its icon's name, or empty. |
| `pages` | array of DockerPage |  | Its pages (one with the category's own name: the category is that page). |

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
| `key` | string |  | Its key (Ed25519's public half, in base64): what signs the tunnels it opens. |

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
| `restart` | RestartState? |  | Its restart, kept till nothing blocks it (restart), or null: none pending. |
| `providers` | array of Provider |  | This machine's model providers (with base_url). |
| `mesh` | MeshView |  | The members it reaches and the links it's made. |
| `notes` | string |  | Record's notebooks here, as written last (notes.stamp: "" for none). |
| `edits` | string |  | The edits proposed to the pages of Record's notebooks here, as written last (notes.edits_stamp: "" for none). |
| `phone` | boolean |  | A phone's host. |
| `ops` | array of string |  | The ops it takes, given by a host that takes only part of the wire (a phone's), with what its sessions take besides (start's ephemeral; control requests: control, set_permission_mode, set_model). |
| `telemetry` | TelemetryState |  | Its telemetry state: the apps on its machine tell it telemetry only while it's on, and profile what it says. |
| `front` | array of TelemetryFront |  | The chats in front on its machine now, as its apps' telemetry says (each one's last chat_front, till its chat_left or the app's gone): none while its telemetry's off. No head notifies of a chat in front on any machine of the mesh. |
| `tool_names` | ToolNames |  | How its MCP tools are named: what every head shows for one ("Mesh / Tasks"). |

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
| `server` | string |  | Its cluster's API server (no credentials): with its name, which cluster it is. A context deleted and made again under the same name is another cluster. |
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

#### KubeSample

The stack's totals at one time, in the namespaces asked for.

| Field | Type | Required | What it is |
|---|---|---|---|
| `at` | number |  | When (seconds since 1970). |
| `cpu_requests` | number |  | Cores asked for. |
| `cpu_used` | number/null |  | Cores used, or null without metrics. |
| `memory_requests` | number |  | Bytes asked for. |
| `memory_used` | number/null |  | Bytes used, or null without metrics. |

#### KubeSection

A category of the Kubernetes view's sidebar.

| Field | Type | Required | What it is |
|---|---|---|---|
| `title` | string | yes | Its name. |
| `icon` | string |  | Its icon's name, or empty. |
| `pages` | array of KubePage |  | Its kinds. |

#### KubeShare

A namespace's or a node's part of the stack's totals, a row of its table.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string |  | The namespace or node. |
| `cells` | array of string |  | Each column's text. |
| `keys` | array of any |  | Each column's number (the name's, its text in lower case), or null where there's none (no limit set, nothing requested, no metrics API): what it's sorted by, and what an "Other (n)" row adds up. |

#### KubeShareColumn

A column of Home's namespaces' or nodes' table: its title, and what its numbers are, so a head can sort by it and add rows up (the "Other (n)" row).

| Field | Type | Required | What it is |
|---|---|---|---|
| `title` | string | yes | Its title. |
| `kind` | `name` \| `count` \| `cores` \| `bytes` \| `share` | yes | What it holds: the namespace's or node's name, a count, cores (0.00), bytes (1.2GiB), or a share (82%): column of[0] as a share of column of[1]. |
| `of` | array of integer |  | A share's columns: what's used, and what it's a share of (its requests or limits). |

#### KubeSummary

Home: the whole stack's CPU and memory, in the namespaces asked for: the pods that hold on to a node (not those that finished), added up, by namespace and by node, and how the totals went.

| Field | Type | Required | What it is |
|---|---|---|---|
| `cpu` | KubeUse |  | CPU, in cores. |
| `memory` | KubeUse |  | Memory, in bytes. |
| `metrics` | boolean |  | Whether the cluster has a metrics API (else nothing is used). |
| `pods` | integer |  | How many pods are added up. |
| `namespace_columns` | array of KubeShareColumn |  | The namespaces table's columns. |
| `namespaces` | array of KubeShare |  | Each namespace's part, most CPU used first (then most requested). |
| `node_columns` | array of KubeShareColumn |  | The nodes table's columns. |
| `nodes` | array of KubeShare |  | Each node's part (against its own allocatable), most CPU used first (then most requested). |
| `history` | array of KubeSample |  | The totals every `every` seconds from the first time Home was read for the context, oldest first, kept in the host's memory: those after the request's since (every one without). |
| `every` | integer |  | How often the host samples them (and a head reads them), in seconds. |
| `minutes` | integer |  | How far back the history goes, in minutes. |
| `sorted_by` | integer |  | The column both tables come sorted by, most first: CPU used. |
| `shown` | integer |  | How many rows a table shows, most first by the column it's sorted by; the rest are added up in one "Other (n)" row. |
| `began` | number |  | When the host began this history (seconds since 1970): a context named for another cluster now (its cluster or server changed) begins anew, and a head drops the samples it holds from before. |

#### KubeUse

One resource's totals (CPU in cores, memory in bytes): what the pods ask for, use and are held to, and what the nodes can give them.

| Field | Type | Required | What it is |
|---|---|---|---|
| `title` | string |  | Its name: CPU, Memory. |
| `requests` | number |  | What the pods ask for (their containers' requests). |
| `used` | number/null |  | What they use now (the metrics API's), or null without one. |
| `limits` | number/null |  | What they're held to (the limits their containers set), or null when none sets one. |
| `allocatable` | number/null |  | What the nodes can give pods, or null when the nodes can't be listed. |
| `cells` | array of string |  | requests, used, limits and allocatable as they're shown. |
| `shares` | array of string |  | requests, used and limits as shares of allocatable (28%), or empty. |

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
| `key` | string |  | Its key (Ed25519's public half, in base64): what signs the tunnels it opens. |
| `providers` | array of Provider |  | Its model providers. |
| `hops` | integer |  | Links away. |
| `through` | string/null |  | The member it's reached through, by name; null for one linked to here. |
| `commit` | string/null |  | The commit its code is (null: not a checkout, or it didn't say). |
| `made` | integer/null |  | When that commit was made, in seconds (null: not known). |
| `heads` | array of Head |  | The apps on its machine connected to it, by the code they run. |
| `behind` | integer/null |  | How many commits it lacks of the mesh's newest code (MeshView's newest), as this host's git counts them (null: it can't). |
| `deciders` | array of Decider |  | Its deciders, and whether each answered when it last looked. |

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
| `key` | string |  | Its key (Ed25519's public half, in base64): what signs the tunnels it opens. |
| `providers` | array of Provider |  | Its model providers. |
| `commit` | string/null |  | The commit its code is (null: not a checkout, or it didn't say). |
| `made` | integer/null |  | When that commit was made, in seconds (null: not known). |
| `heads` | array of Head |  | The apps on its machine connected to it, by the code they run. |
| `deciders` | array of Decider |  | Its deciders, and whether each answered when it last looked. |

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

#### RestartBlocker

One thing a pending restart waits for: a chat whose agent is running a command, a task's run, an agent run on this host for another host's chat (an Apple or pi model), or a terminal on this host that someone is attached to.

| Field | Type | Required | What it is |
|---|---|---|---|
| `kind` | `chat` \| `task` \| `agent` \| `terminal` | yes | A chat running a command, a task running, an agent run for a chat elsewhere, or an attached terminal. |
| `session` | string |  | The chat's id: a chat's, a task's Bot's, or the chat elsewhere an agent run is for. |
| `host` | string |  | An agent run's: the id of the host its chat is on. |
| `task` | string |  | A task run's: its task's id. |
| `run` | string |  | A task run's: its id. |
| `terminal` | string |  | A terminal's: its id (terminal's attach). |
| `name` | string | yes | The chat's title, the task's name, the agent's model, or the terminal's title. |
| `waiting` | boolean |  | A task run's: true while it waits for input (an ask of its Bot, asking); a restart would stop it. |
| `what` | string |  | What it runs: the chat's oldest command, the agent run's chat's host, or the terminal's program. |

#### RestartState

A restart of the host process it's keeping till nothing blocks it: what asked for it, whether a head's holding it, and what it waits for. Every head shows the same.

| Field | Type | Required | What it is |
|---|---|---|---|
| `by` | `head` \| `update` | yes | What asked for it: a head (Restart Daemon), or its own updater (a newer version). |
| `to` | string |  | The commit it'll restart onto, when its updater asked. |
| `paused` | boolean | yes | A head is showing what it waits for: it won't restart till that head's let go. |
| `blockers` | array of RestartBlocker | yes | What it waits for, oldest session first. |

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
| `bot` | string |  | A chat a task's run started (a subagent sender, its report of a turn): the session of the run's Bot, on its host. |
| `task` | string |  | A task's run's: the id of its task, on its Bot's Tasks page. |
| `task_name` | string |  | A chat a task's run started: its task's name, as the from line says it ("<task> run"). |
| `run` | string |  | A task's run's: the id of the run, on its Bot's Tasks page (its Bot: bot, else its session). |
| `over` | `finished` \| `stopped` \| `failed` |  | A chat a task's run started: how the turn it reports ended (finished; stopped: interrupted or stopped; failed: an error, or its agent ended before its turn did). |

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
| `created` | number/null |  | When it was made, epoch seconds: the earlier of when the host took it up and its transcript's first record. |
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
| `rotated` | number/null |  | A Bot's: when it was last rotated (its conversation archived as a past thread, a new one begun), epoch seconds; null if it never has. A head's copy of its transcript is let go of as it changes. |
| `autofix` | AutofixPull? |  | Its Auto-fix: the pull request its host follows for it; null while it's off. |
| `acting` | boolean |  | Whether its turn is at its browser's tools (Emission's), its own or a subagent's: from the start of a call of Emission's till another tool's call or text starts after it (in each agent's own messages; on while any agent is at one), the turn ends or its agent goes; only while it's working, not asking. |
| `context` | ContextUsage? |  | How much of its context window its agent last said it's used, kept while its agent's stopped; null till it's said. |
| `on_phone` | boolean |  | A Bot's browser is on a phone now. |
| `fork_from` | string/null |  | The session it was forked from. |
| `spawned_by` | string/null |  | The chat whose mesh tools started it (start's spawned_by), or the Bot whose task's run started it. |
| `task_asks` | array of string |  | A Bot's: the uuids of the messages its tasks' runs have sent it as asks, each run waiting for input (asking) till the turn that message began ends. Empty for every other. |
| `task_run` | TaskRunRef? |  | The task's run that started it (start's task_run), on its Bot's host. |
| `desktop` | boolean |  | Claude Desktop's: a chat in Claude Desktop's own list on its host's machine. Every app lists it in the Desktop group. |
| `taken_up` | boolean |  | false for a Claude Desktop chat its host only lists: nothing of it runs on the host till it's written in, which takes it up (as adopt does). Missing for every other. |
| `elsewhere` | boolean |  | Claude Desktop is writing it now (written in the last minute, not by the host's own agent): every app keeps it read-only while it is. |
| `start_key` | string/null |  | Its start's start_key, if it was given one. |
| `account` | Account? |  | The subscription account its agent runs under on its host, fixed as it's made (the one the host's new chats take for its provider then, accounts' defaults, or as it's first written in), or the one picked for it since (accounts' use), from the moment it's picked; null for the CLI's own sign-in, or an account since removed (account_gone). |
| `account_gone` | boolean |  | Its subscription account was removed from its host after it was picked for it: its agent can't start till another is picked. |
| `held` | array of HeldMessage |  | Messages held for it, oldest first. |
| `senders` | integer |  | Its sender store's revision (0 for none): what a head's copy is checked against. |
| `always_allow` | array of string |  | The tools allowed whenever it asks for them, sorted. |
| `asks` | array of Ask |  | What it's asking, not answered yet. |
| `ended` | TurnEnd? |  | Its last turn's end, as its host keeps it; null before one has ended (and always for a chat another chat's mesh tools, or a task's run, started). |
| `seen` | TurnEnd? |  | The last turn's end that's been seen: a head showed it on screen (seen), or its user wrote in it since. Ended and not seen: every head keeps its notification of it, and its dot, till it is; then every head takes them back. |

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
| `bot` | string |  | A chat a task's run started (a subagent sender, its report of a turn): the session of the run's Bot, on its host. |
| `task` | string |  | A task's run's: the id of its task, on its Bot's Tasks page. |
| `task_name` | string |  | A chat a task's run started: its task's name, as the from line says it ("<task> run"). |
| `run` | string |  | A task's run's: the id of the run, on its Bot's Tasks page (its Bot: bot, else its session). |
| `over` | `finished` \| `stopped` \| `failed` |  | A chat a task's run started: how the turn it reports ended (finished; stopped: interrupted or stopped; failed: an error, or its agent ended before its turn did). |
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

#### Task

One of a Bot's tasks (baudelib.tasks): a script its host runs, by hand or as a trigger fires, its form filled, its Bot asked when it can't decide or fails.

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id. |
| `name` | string | yes | Its name. |
| `runtime` | `python` \| `shell` \| `bot` | yes | How its script's run: Python 3, /bin/sh, or given to its Bot as a turn (a prompt). |
| `script` | string/null |  | Its script (task's answer, and a set's; tasks' listing leaves it out). |
| `version` | string/null |  | Its script's version: the first 12 hex of its SHA-256. |
| `form` | array/null of TaskField |  | Its form's fields. |
| `triggers` | array/null of TaskTrigger |  | What starts it. |
| `variables` | array/null of TaskVariable |  | Its variables, each bound once (bindings), its script reading them (baudetask.vars). |
| `bindings` | object/null |  | A variable's name: what it's bound to (one not here is unbound). |
| `warnings` | object/null |  | A bound variable's name: why what it's bound to isn't there now (a model not served, a host out of reach, a pod gone), as the host last looked (the host's to say: not set). |
| `notify_failure` | boolean/null |  | A run that fails is sent to its Bot, with its log. |
| `fallback` | boolean/null |  | Notify on failure under its old name, for older heads: read when notify_failure isn't given, and sent as notify_failure is. |
| `notify_exit` | boolean/null |  | A run that finishes, exit 0 and not stopped, is told to its Bot, with the end of its output; the Bot isn't asked to act. |
| `timeout_s` | integer/null |  | The longest a run may take; it's stopped after. |
| `paused` | boolean/null |  | Its triggers don't fire (it can still be run by hand). |
| `last` | TaskRun? |  | Its latest run. |
| `next` | number/null |  | When a schedule of its fires next. |

#### TaskBinding

What a task's variable is bound to: value, and what else its type has. Picked from what its host offers (task_choices), or typed in as any text.

| Field | Type | Required | What it is |
|---|---|---|---|
| `value` | string | yes | What it is: a model's value, a decider's value (what decide's model takes), a host's id or name, a context's, pod's, container's or database's name, or the text. |
| `label` | string/null |  | What it's shown as (a model's name, a host's), if not its value. |
| `effort` | string/null |  | A model's: its effort. |
| `host` | string/null |  | A model's: the member of the mesh that serves it, by name (its provider), if it isn't this host. |
| `model` | string/null |  | A decider's: the model it serves, as it said. |
| `context` | string/null |  | A pod's: its Kubernetes context. |
| `namespace` | string/null |  | A pod's: its namespace. |
| `container` | string/null |  | A database's: the container it runs in. |

#### TaskChoice

One thing a task's variable could be bound to, as its host offers it (task_choices): its own models, the mesh's hosts, its kubeconfig's contexts, the workloads running in its local clusters, its Docker containers, the databases running on it (what ⌘K lists), its deciders and the mesh's.

| Field | Type | Required | What it is |
|---|---|---|---|
| `binding` | TaskBinding | yes | What it's bound to, picked. |
| `detail` | string/null |  | Where it is, beside its name (a pod's context and namespace). |
| `efforts` | array/null of string |  | A model's: the efforts it takes. |

#### TaskField

A field of a task's form: filled by hand, by a trigger's payload, or by its Bot.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string | yes | Its name, as the script reads it (letters, digits, _). |
| `label` | string/null |  | What it's called on the form (its name, if none). |
| `type` | `text` \| `number` \| `bool` \| `choice` | yes | What it takes. |
| `choices` | array/null of string |  | A choice's choices. |
| `default` | any |  | Its value when nothing fills it. |
| `required` | boolean/null |  | It must be filled for a run to start. |

#### TaskLogLine

A line of a run's log.

| Field | Type | Required | What it is |
|---|---|---|---|
| `at` | number | yes | When. |
| `kind` | `out` \| `err` \| `say` \| `ask` \| `answer` \| `status` \| `agent` \| `warning` | yes | What it is: the script's output or errors, a line it said, an ask of its Bot, the Bot's answer, the run's state changing, or what a chat it started said as its first turn ended (told its Bot too), or a bound variable not there as the run started. |
| `text` | string | yes | What it says. |
| `values` | object/null |  | An answer's values, from a form. |
| `error` | boolean/null |  | An ask's: it was an error handed over. An agent's: the chat failed, or was stopped. |
| `session` | string |  | An agent's: the chat's id. |
| `over` | `finished` \| `stopped` \| `failed` |  | An agent's: how the chat's turn ended (Sender's over). |
| `variable` | string |  | A warning's: the variable's name. |

#### TaskRun

A task's run: what started it, what it was given, how it went (a run's record is its telemetry).

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id. |
| `task` | string | yes | The task's id. |
| `name` | string/null |  | The task's name as it ran. |
| `version` | string/null |  | The script's version it ran. |
| `trigger` | string/null |  | The trigger that fired it (its id), or null: run by hand. |
| `kind` | `manual` \| `schedule` \| `git_push` | yes | What started it. |
| `payload` | object/null |  | The trigger's event. |
| `inputs` | object/null |  | Its form as filled. |
| `status` | `running` \| `asking` \| `done` \| `failed` \| `stopped` \| `skipped` | yes | How it is, or ended. |
| `started` | number | yes | When it started. |
| `offset` | integer/null |  | How far ahead of UTC its host's clock was as it started (seconds): its times shown as they were there. |
| `ended` | number/null |  | When it ended. |
| `seconds` | number/null |  | How long it took. |
| `exit` | integer/null |  | Its script's exit code. |
| `error` | string/null |  | Why it failed or was skipped. |
| `skips` | integer/null |  | A skipped run's: how many firings it stands for (its trigger fired again while the same run went on: one record, not one each). |
| `asks` | integer/null |  | How many times it asked its Bot. |
| `handed` | boolean/null |  | Its failure was handed to its Bot. |
| `chats` | array/null of string |  | The chats it started on its Bot's host (start's task_run), by their ids, oldest first: they go on after it's over, their states as the sessions listing has them. |
| `command` | string/null |  | What it runs, as a command line: its interpreter, then its script's path on its host; null: a bot task's, given its Bot as a turn. |
| `pid` | integer/null |  | Its script's process id, as it runs. |
| `pgid` | integer/null |  | Its script's process group, as it runs: what's left of it killed as its host starts again, if its host was killed as it ran. |

#### TaskRunRef

The task's run a chat was started by (Session's task_run), on its Bot's host.

| Field | Type | Required | What it is |
|---|---|---|---|
| `bot` | string | yes | The Bot's session id. |
| `task` | string | yes | The task's id. |
| `run` | string | yes | The run's id. |
| `name` | string |  | The task's name as it ran. |

#### TaskTrigger

What starts a task (besides a run by hand): a schedule, or a git push to a branch of a repo on the host, seen by polling its head. Its events' payloads fill the task's form (fills).

| Field | Type | Required | What it is |
|---|---|---|---|
| `id` | string | yes | Its id. |
| `kind` | `schedule` \| `git_push` | yes | What it is. |
| `every` | integer/null |  | A schedule's: minutes between runs. |
| `at` | string/null |  | A schedule's: the host's local time it runs at, HH:MM. |
| `days` | array/null of integer |  | A schedule's at's days, 0 Monday to 6 Sunday. |
| `next` | number/null |  | A schedule's: when it fires next. |
| `repo` | string/null |  | A git push's: the repo's folder on the host. |
| `branch` | string/null |  | A git push's: the branch watched. |
| `poll_s` | integer/null |  | A git push's: seconds between looks at the branch. |
| `seen` | string/null |  | A git push's: the branch's head as last seen. |
| `fills` | object/null |  | A form field's name: the payload's key that fills it (a field not named takes the payload's key of its own name). |
| `fired` | number/null |  | When it last fired. |
| `error` | string/null |  | Why it couldn't look or fire, last time. |

#### TaskVariable

A variable a task declares: a name its script reads it by (baudetask.vars), and a type, bound once for the task (its bindings), by its Bot or a person, unlike its form's inputs, which each run fills.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string | yes | Its name, as its row and the script say it ("Translation Model"). |
| `type` | `model` \| `decider` \| `host` \| `browser_host` \| `terminal_host` \| `kube_context` \| `pod` \| `container` \| `database` \| `text` | yes | What it's bound to: a model (with its effort), a decider (decide's), a host of the mesh (one to browse on, one to run terminals on), a Kubernetes context, a pod, a container, a database, or text. What's offered as it's bound is the host's own of that type; any text can be given all the same. |
| `description` | string/null |  | What it's for, as its Bot said. |

#### TelemetryDay

A day's tokens, from the turns that ended in it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `day` | string | yes | The day, YYYY-MM-DD, the host's time. |
| `tokens` | integer | yes | Tokens read and written. |
| `turns` | integer | yes | Turns that ended. |

#### TelemetryEvent

Something done in a chat, as an app saw it happen: kept by its machine's host as it's told (telemetry), read back with telemetry_read. A chat is named by its host's id and its session's id, so what every host keeps can be put together.

| Field | Type | Required | What it is |
|---|---|---|---|
| `at` | number | yes | When, in seconds since the epoch (to the millisecond). |
| `app` | `host` \| `baude` \| `record` \| `phone` |  | What saw it: Baude, Record, the phone's app, or the chat's host itself (a turn's end, as it ran there: what tokens are added up from). |
| `kind` | `chat_opened` \| `chat_front` \| `chat_left` \| `turn_sent` \| `turn_ended` | yes | What happened: a chat opened in a window or screen; a chat came in front (its window or screen in front, showing it, with someone at the machine); it stopped being in front (or nobody's at the machine now); a turn sent in it; a turn of it ended. |
| `host` | string | yes | The id of the host the chat is on. |
| `session` | string | yes | The chat's session id. |
| `seconds` | number/null |  | A turn_ended's: how long the turn took. |
| `turn` | string/null |  | A turn_ended's: the id of the turn's result, so a turn two apps saw end is counted once. |
| `model` | string/null |  | A turn_ended's: the model it ran on. |
| `effort` | string/null |  | A turn_ended's: the effort it ran at. |
| `tokens_in` | integer/null |  | A turn_ended's: the tokens it read (input, cache reads and writes). |
| `tokens_out` | integer/null |  | A turn_ended's: the tokens it wrote. |

#### TelemetryFront

A chat in front on a host's machine: shown in one of its apps' windows or screens, that in front, with someone at the machine.

| Field | Type | Required | What it is |
|---|---|---|---|
| `host` | string | yes | The id of the host the chat is on. |
| `session` | string | yes | The chat's session id. |

#### TelemetryLine

A line of code line_profiler timed.

| Field | Type | Required | What it is |
|---|---|---|---|
| `file` | string | yes | Its file: under the checkout, relative to it. |
| `line` | integer | yes | Its line number. |
| `function` | string | yes | The function it's in. |
| `code` | string/null |  | The line's text, stripped. |
| `hits` | integer | yes | Times it ran. |
| `seconds` | number | yes | Time spent on it. |

#### TelemetryModel

A model's turns, or a model and effort's.

| Field | Type | Required | What it is |
|---|---|---|---|
| `model` | string | yes | The model. |
| `effort` | string/null |  | The effort (null: a model's turns at any effort). |
| `turns` | integer | yes | Turns that ended on it. |
| `tokens` | integer | yes | Their tokens, read and written. |

#### TelemetryProc

A process's use of its machine, in one sample.

| Field | Type | Required | What it is |
|---|---|---|---|
| `app` | `host` \| `baude` \| `record` \| `emission` \| `multiplex` \| `phone` | yes | Which: the host process, Baude, Record, Emission, Multiplex, or the phone's app (its host too). |
| `pid` | integer | yes | Its process id. |
| `cpu` | number/null |  | Its CPU since the sample before, in percent of one core (null in its first sample). |
| `rss` | integer/null |  | Its resident memory, in bytes. |

#### TelemetryProfile

What line_profiler timed in a process over a stretch (saved every 30 seconds while profiling, and as it's switched off).

| Field | Type | Required | What it is |
|---|---|---|---|
| `at` | number | yes | When the stretch ended, in seconds since the epoch. |
| `from` | number | yes | When it began. |
| `app` | `host` \| `baude` \| `record` | yes | The process profiled: the host's, Baude's or Record's. |
| `lines` | array of TelemetryLine | yes | Its lines that ran, the most time first (at most 200). |

#### TelemetryProfiling

What a process profiles while line profiling's on.

| Field | Type | Required | What it is |
|---|---|---|---|
| `app` | `host` \| `baude` \| `record` | yes | The process: the host's, Baude's or Record's. |
| `functions` | array of string | yes | What's profiled: functions (module.function, module.Class.method) and modules (every function in it); its defaults unless others are named. |
| `default` | boolean | yes | They're its defaults (lineprofile.DEFAULTS, Baude's own pick), not named by telemetry_set. |
| `error` | string/null |  | Why it isn't profiling what it's told to (a name not found; line_profiler not installed). |

#### TelemetrySample

A host's machine and its processes, sampled every few seconds while telemetry is on.

| Field | Type | Required | What it is |
|---|---|---|---|
| `at` | number | yes | When, in seconds since the epoch. |
| `load` | array/null of number |  | The machine's load average over 1, 5 and 15 minutes (null where it has none: a phone). |
| `memory_total` | integer/null |  | The machine's memory, in bytes. |
| `memory_used` | integer/null |  | Its memory in use, in bytes (macOS: Activity Monitor's Memory Used, app memory, wired and compressed; Linux and a phone: the total less what's available to start new work without swapping). |
| `swap_total` | integer/null |  | Its swap, in bytes (macOS grows it as it's needed; a phone's is zram); 0 or null where it has none. |
| `swap_used` | integer/null |  | Its swap in use, in bytes. |
| `memory_pressure` | string/null |  | How pressed for memory the system says it is: "normal", "warning" or "critical" (macOS's memory pressure level; a phone's low memory: critical, else normal); null where it has no level (Linux). |
| `memory_stall` | number/null |  | The share of the last 10 seconds, in percent, some task stalled waiting on memory (Linux's pressure stall information, some avg10); null where it has none. |
| `procs` | array of TelemetryProc | yes | The host process, each app on its machine that told it telemetry (Baude, Record, the phone's app), and each Emission and Multiplex running there (the host finds those itself). |

#### TelemetryState

A host's telemetry: on or off, line profiling on or off, and what each process on its machine profiles.

| Field | Type | Required | What it is |
|---|---|---|---|
| `on` | boolean | yes | Events kept and the machine sampled. |
| `line_profiling` | boolean | yes | Line profiling on: each process profiles its functions while telemetry's on too (off unless switched on). |
| `line_profiler` | boolean | yes | line_profiler is installed for the host's Python. |
| `profiling` | array of TelemetryProfiling | yes | Each process's: the host's, Baude's, Record's. |

#### TelemetryUsage

The turns that ended in a stretch (turn_ended events, each turn once), added up by the host.

| Field | Type | Required | What it is |
|---|---|---|---|
| `days` | array of TelemetryDay | yes | Each day with a turn, the earliest first. |
| `models` | array of TelemetryModel | yes | Each model, the most turns first. |
| `combos` | array of TelemetryModel | yes | Each model and effort together, the most turns first. |

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

#### Theme

A theme as its host has it (its themes folder's <name>/: theme.css and tokens.json), worked out by the host for every app alike: the desktop apps draw its CSS over theirs and its colours, the phone its colours.

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string | yes | Its name: its folder's. |
| `css` | string |  | Its theme.css (GTK CSS, the desktop apps': drawn over all of theirs), empty if it has none. |
| `light` | object |  | Each colour of schema/tokens' libadwaita and baude groups the theme makes other than Baude's own in light, by its token's path: #rrggbb, or #rrggbbaa where it's see-through. A colour made of another (baude.rule of libadwaita.borders) is here when that one's set. |
| `dark` | object |  | Each colour of schema/tokens' libadwaita and baude groups the theme makes other than Baude's own in dark, by its token's path: #rrggbb, or #rrggbbaa where it's see-through. A colour made of another (baude.rule of libadwaita.borders) is here when that one's set. |
| `error` | string |  | Why its files can't be used, in place of the rest. |

#### ThemeFiles

A theme's files as they're written: what's kept to put one back (an undo).

| Field | Type | Required | What it is |
|---|---|---|---|
| `css` | string/null |  | theme.css's text, or null with none. |
| `tokens` | object/null |  | tokens.json's: a resolver document (schema/tokens/baude.resolver.json's format, its sources written in it) whose sets go over Baude's tokens, then its theme modifier's light or dark context over Baude's light or dark; colours of the libadwaita and baude groups only. null with none. |

#### ToolNames

How a host's MCP tools are named, for its heads to show (mcp__<server>__<tool> as "Server / Tool"): what its MCP servers and their tools say they're called, and the names its claude says its servers are configured as.

| Field | Type | Required | What it is |
|---|---|---|---|
| `servers` | object |  | Each server's title, by its name as it's put in its tools' names (each character not a letter, digit, _ or - made _): mesh: Mesh, claude_ai_Slack: Slack. |
| `tools` | object |  | A tool's title, by its whole name, where its server gives one (mcp__emission__javascript_tool: Run JavaScript). |

#### TurnEnd

A turn's end, as its host keeps it: what every head's notification of it is known by, and what's seen of a chat is said in.

| Field | Type | Required | What it is |
|---|---|---|---|
| `uuid` | string | yes | The uuid of the message the turn ended with (its result's, as its agent said it; the host's own where it said none). |
| `size` | integer | yes | Its transcript's size as the turn ended: a head that shows that much of it shows the turn's end. |

#### Update

Its own updater has a newer version ready, and waits for those commands to end before it restarts onto it.

| Field | Type | Required | What it is |
|---|---|---|---|
| `to` | string |  | The commit it'll restart onto. |
| `waiting` | array of Running |  | The commands it waits for. |

#### UsageLimit

One of a subscription's limits, as Claude Code's get_usage says it, or Codex's App Server's rate limits (account/rateLimits/read).

| Field | Type | Required | What it is |
|---|---|---|---|
| `name` | string | yes | Which limit (Five hour, Seven day…). |
| `key` | string |  | The limit's own name in Claude Code's usage (five_hour, seven_day…); a Codex limit's, by its window's length (five_hour and seven_day as Claude's, else 2_hour, 3_day…). |
| `resets` | number |  | When it resets, in seconds since 1970 (left out where the usage doesn't say). |
| `percent` | number | yes | How much of it is used, in percent. |

#### UsageReading

A subscription's usage as its host last read it: its limits and when.

| Field | Type | Required | What it is |
|---|---|---|---|
| `usage` | array of UsageLimit | yes | The limits as that read said them. |
| `read` | number | yes | When it was read, in seconds since 1970. |

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
| `DRAFT_CHARS_MAX` | `200000` | A chat's draft, as draft_save sends it, at most, in characters: a longer one is refused, and stays the head's own. |
| `EPHEMERAL_S` | `1` | A side question whose head's gone (or detached): gone this soon, in seconds. |
| `FRAME_QUALITY` | `72` | A watched browser's frames' JPEG quality (emission_frame). |
| `GRACE_MAX_S` | `86400` | start's grace, at most, in seconds. |
| `HOST_COMMAND` | `~/.local/bin/baude-headless` | What ssh runs on a host to reach its host process, with connect after it: the command Baude installs on every machine (baudelib/bins.py), its ~ the host's home. |
| `IMAGE_BYTES_MAX` | `3750000` | An image attached to a message, at most, in bytes (5 MB once base64-encoded). |
| `IMAGE_EDGE_MAX` | `1568` | An image attached to a message: its longest side, in pixels, larger ones scaled down. |
| `INFER_S` | `600` | A provider has this long to answer infer, in seconds. |
| `OPEN_S` | `20` | A member of the mesh, reached through it: its tunnel has this long to open, in seconds. |
| `PING_S` | `5` | A connection's ends each say something at least this often (ping), in seconds. |
| `QUERY_ROWS_MAX` | `1000` | A database query's rows, at most (databases' query): the rest aren't read. |
| `SHOT_MAX` | `1024` | A browser screenshot's longest side, in pixels. |
| `SHOT_QUALITY` | `82` | A browser screenshot's JPEG quality. |
| `SIGNED_S` | `300` | A tunnel's open: signed this near now, in seconds, either way, or it's closed where it's opened to. |
| `SILENT_S` | `16` | Silent this long, in seconds, the other end's taken for gone. |
| `TERMINAL_KEEP_S` | `600` | A terminal (terminal op) no connection is attached to now: its program kept running this long to be attached to again, in seconds. |
| `TERMINAL_KEPT_BYTES` | `262144` | What a terminal (terminal op) wrote lately, kept to show again on attach: this many bytes at most. |
| `TTL` | `16` | A tunnel's passed through at most this many hosts (open's ttl). |
| `VERSION` | `34` | The wire's version, as a host's hello says it. |
| `ZOOM_MAX` | `1568` | A browser zoom's longest side, in pixels. |
| `ZOOM_QUALITY` | `90` | A browser zoom's JPEG quality. |
