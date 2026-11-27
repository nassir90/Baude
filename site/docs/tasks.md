# Task scripts: the baudetask SDK

Made by `python3 -m baudelib.tasksdk` from `schema/types` (the wire's requests and answers, the ones Baude, Record and the phone use) and the Bot's own tools' definitions (`baudelib/emissiontools.py`, `baudelib/multiplextools.py`). Run it again after changing either: it writes this page and `baudelib/script/baudetask_sdk/generated.py`.

A Bot's task is a script its host runs (see [docs/tasks-design.md](../../docs/tasks-design.md)). Its Python scripts `import baudetask`, and through it reach whatever the Bot itself can: Emission tabs, Multiplex terminals, Kubernetes and Docker objects, databases, every host of the mesh and its models, chats and subagents, beside asking the Bot (`ask`, `fallback`), asking a person (`inbox`) and starting agents (`agent`).

## How calls are judged

Every call takes `host=`: any host of the mesh, by its name or id (or a `Host` from `hosts()`); this one if none's given. Calls go over the run's own connection to its host, which names the run and its token: the host takes the connection for the run's Bot's and applies the access rules to every call as it does to the Bot's own tools (as the member that started the Bot, if one did), on this host and, through it, on every other one (where the call is from this host). A run can do nothing its Bot couldn't through the SDK. What's refused raises `baudetask.Refused`, saying which rule refused it.

Being judged as the run's Bot applies to the SDK's calls; it isn't a sandbox. A script is a process run as the host's user: it can read and write what that user can, and open the host's socket without the run's token, as the owner. So setting or running a task already needs Shell (see [docs/tasks-design.md](../../docs/tasks-design.md)).

Emission and terminal calls drive the Bot's own browser profile and terminals on that host: never Emission's own window, nor another Bot's profile.

## Enumerate, pick, pass back

List calls answer typed objects (a `dict` whose fields are attributes too) that carry where they're from, so a script can filter them and pass one back:

```python
import baudetask as task

pod = next(p for p in task.kube.pods(namespace="shop", selector="app=web") if p.status == "Running")
task.say(task.kube.logs(pod, tail=20))

gpu = next(h for h in task.hosts() if h.reachable and any("70b" in m.value for m in h.models))
model = next(m for m in task.models(host=gpu) if m.tools)
chat = task.subagents.start("Summarise the release notes", host=gpu, model=model)

web = next(c for c in task.docker.containers(host="build-box") if c.name.startswith("shop-api"))
task.docker.restart(web)
```

## An Emission test, as a task script

Open a page in the Bot's browser, check it, and ask the Bot when it's not as expected:

```python
import baudetask as task


@task.main
def check(inputs):
    url = inputs.get("url") or "http://127.0.0.1:8000/"
    tab = task.emission.open(url=url)
    try:
        text = task.emission.text(tab=tab.tab).text or ""
        if "Welcome" not in text:
            said = task.ask(f"{url} doesn't say Welcome. Is that expected?",
                            form=[{"name": "expected", "type": "bool"}])
            if not said["expected"]:
                raise AssertionError(f"{url}: no Welcome")
        found = task.emission.find(query="Sign in", tab=tab.tab)
        if found.matches:
            task.emission.click(ref=found.matches[0]["ref"], tab=tab.tab)
    finally:
        task.emission.close(tab=tab.tab)
```

## Asking a person: the Inbox

`inbox()` raises an ask of a person in the Inbox of every app that may manage the task's Bot (Baude's, the phone's), drawn as a message is: Markdown, its pictures between paragraphs or alone in a table's cells, its form as setting rows, and the buttons the script gives, sized to their labels. Pressing one answers it, with the form's values; the ask leaves every Inbox at once. The person may have a model answer it for them to confirm, send it to a Bot, or write to the task's Bot, which may answer it or put a new version of it. The host keeps it across restarts; one whose run ended unanswered stays, struck through, till it's removed.

```python
import baudetask as task


@task.main
def deploy(inputs):
    asked = task.inbox(
        "### Deploy 2.5 to production?

Staging has run **2.5** since 08:10 with no errors.

![home](home.png)",
        ["Approve", {"label": "Deny", "style": "red"}],
        pictures=["shots/home.png"], form=[{"name": "note", "type": "text", "label": "Note for the release"}],
        title="Deploy site: 2.5 to production?")
    got = asked.wait()                 # {button, label, values, by}: days, if it must (the run waits for input)
    if got["label"] == "Approve":
        task.say("deploying:", got["values"]["note"])
```

`asked.poll()` answers at once (None if nobody's answered yet), `asked.wait(timeout=60)` gives up after a minute (None), `asked.revise(body=..., buttons=...)` puts a new version (what's left out stays), `asked.withdraw()` takes it back. A withdrawn ask, or one whose run is stopped, raises `baudetask.Unanswered` from `wait` and `poll`. Button styles: `primary`, `secondary` (the default), or one of the accent palette's colours `blue`, `teal`, `green`, `yellow`, `orange`, `red`, `pink`, `purple`, `slate`; `{"label": ..., "picture": "a.png"}` draws the picture over its label.

## Terminals

```python
term = task.terminal.open(command="bash", title="build")
task.terminal.type(terminal=term.terminal, text="make test", enter=True)
done = task.terminal.wait(terminal=term.terminal, pattern=r"passed|failed", timeout=120)
task.say(done.screen)
task.terminal.close(terminal=term.terminal)

out = task.terminal.run(["uname", "-a"], host="lab")   # a command run in a terminal there: {output, status}
```

## Hosts, models, chats and subagents

### `baudetask.hosts()`

The mesh's hosts, this one first, each a Host with its models; those out of reach have reachable False and error saying why.

### `baudetask.models(host=None)`

The models host can run (this one's if none's given), each a Model: pass one as a subagent's model.

### `baudetask.subscriptions(host=None)`

The Claude and Codex subscriptions host's chats run on (this one's if none's given), each a Subscription: its CLI's own sign-in for each provider whose program the host has, then its accounts. Pass one as account= to subagents.start or agent; a model in none's models runs on no subscription (Ollama's, Apple's, an endpoint's).

## `baudetask.emission`

A Bot's browser tools: an MCP server on stdin and stdout (as baudelib.meshtools is), run on the Bot's host. Each call drives the Bot's own Emission profile on a host of the mesh (this one if none's named): through that host's process (wire's emission op), which starts Emission there for the Bot when it isn't running. A Bot only ever reaches its own profile; the sites it's signed in to are the ones its owner signed in to there, or copied in. Each is called by the agent as mcp__emission__<tool>. Each method is one of the Bot's own emission tools, as its emission op takes it, to host: this one if none's given.

Besides the generated calls below:

- `emission.sites(*, host=None)`: The sites the Bot's own profile on host is signed in to (its cookies' sites).
- `emission.profile(*, host=None)`: The Bot's own Emission profile on host (each host has its own; it's made as it's first used): its name and the sites it's signed in to.
- `emission.select(tab, *, host=None)`: Tab brought to the front of the Bot's browser.

### `emission.tabs(...)`

Your browser's tabs (started if need be) and recent downloads; sites: the sites it's signed in to. A tab's id is the tabId the tools take (none: the front tab). Each host has its own browser and sign-ins. (the `tabs_context` tool)

| Argument | Type | What it is |
|---|---|---|
| `sites` | boolean |  |

### `emission.open(...)`

Open a tab (on url: an address or a search) and wait for it to load. (the `tabs_create` tool)

| Argument | Type | What it is |
|---|---|---|
| `url` | string |  |

### `emission.navigate(...)`

Go to url (an address, a search, or back, forward, reload) and wait for it to load. (the `navigate` tool)

| Argument | Type | What it is |
|---|---|---|
| `url` | string (required) |  |
| `tab` | string | tabs_context's |

### `emission.close(...)`

Close a tab. (the `tabs_close` tool)

| Argument | Type | What it is |
|---|---|---|
| `tab` | string (required) | tabs_context's |

### `emission.screenshot(...)`

A picture of the page, for what text can't show: layout, images, a canvas, how it looks. read_page, find and get_page_text read pages for less. Its pixels are click, hover, drag, scroll and zoom's x, y. (the `screenshot` tool)

| Argument | Type | What it is |
|---|---|---|
| `tab` | string | tabs_context's |

### `emission.zoom(...)`

The page as it is now, at full detail, in region [x0, y0, x1, y1] of the last screenshot's pixels (which x, y stay in). (the `zoom` tool)

| Argument | Type | What it is |
|---|---|---|
| `region` | array of number (required) |  |
| `tab` | string | tabs_context's |

### `emission.read_page(...)`

How to read a page: its elements on screen and a screen above and below (links, buttons, fields, headings; filter all: the rest, text too), each with its role, name and ref; refs stay the same between reads. ref_id: only that element's part. (the `read_page` tool)

| Argument | Type | What it is |
|---|---|---|
| `tab` | string | tabs_context's |
| `filter` | 'interactive' \| 'all' |  |
| `ref` | string |  |
| `max_chars` | number |  |

### `emission.find(...)`

Elements anywhere on the page matching words (role, name, placeholder, id, link), with refs. (the `find` tool)

| Argument | Type | What it is |
|---|---|---|
| `query` | string (required) |  |
| `tab` | string | tabs_context's |

### `emission.text(...)`

The page's text (its main part's, if it has one). (the `get_page_text` tool)

| Argument | Type | What it is |
|---|---|---|
| `tab` | string | tabs_context's |
| `max_chars` | number |  |

### `emission.click(...)`

Click ref's element (its middle) or x, y, as a person would (x, y: the last screenshot's pixels, CSS ones before a tab's first); count 2: a double click. (the `click` tool)

| Argument | Type | What it is |
|---|---|---|
| `ref` | string | ref_12: from read_page, find or scroll |
| `x` | number |  |
| `y` | number |  |
| `tab` | string | tabs_context's |
| `button` | 'left' \| 'right' |  |
| `count` | number |  |
| `modifiers` | array of 'shift' \| 'ctrl' \| 'alt' \| 'cmd' |  |

### `emission.drag(...)`

Drag along path [[x, y], ...] (x, y: the last screenshot's pixels, CSS ones before a tab's first): select text, move a slider, drag into place. (the `drag` tool)

| Argument | Type | What it is |
|---|---|---|
| `path` | array of array of number (required) |  |
| `tab` | string | tabs_context's |

### `emission.hover(...)`

Move the mouse over ref's element or x, y (x, y: the last screenshot's pixels, CSS ones before a tab's first). (the `hover` tool)

| Argument | Type | What it is |
|---|---|---|
| `ref` | string | ref_12: from read_page, find or scroll |
| `x` | number |  |
| `y` | number |  |
| `tab` | string | tabs_context's |

### `emission.type(...)`

Type text, key by key, into the focused field (or ref's, focused first). (the `type` tool)

| Argument | Type | What it is |
|---|---|---|
| `text` | string (required) |  |
| `ref` | string | ref_12: from read_page, find or scroll |
| `tab` | string | tabs_context's |

### `emission.key(...)`

Press keys in turn, a space between: Enter, Tab, Escape, Backspace, Delete, Up, Down, Left, Right, Home, End, PageUp, PageDown, a character, or a combination like cmd+a. (the `key` tool)

| Argument | Type | What it is |
|---|---|---|
| `keys` | string (required) |  |
| `tab` | string | tabs_context's |

### `emission.scroll(...)`

Turn the mouse wheel over ref's element, x, y (x, y: the last screenshot's pixels, CSS ones before a tab's first) or the middle, by dy, dx CSS pixels (dy 600 if none; up is negative). Says what came into view, with refs. (the `scroll` tool)

| Argument | Type | What it is |
|---|---|---|
| `dy` | number |  |
| `dx` | number |  |
| `ref` | string | ref_12: from read_page, find or scroll |
| `x` | number |  |
| `y` | number |  |
| `tab` | string | tabs_context's |

### `emission.form_input(...)`

Set ref's field: a select's option (value or text), a checkbox or radio (true, false), or a value. (the `form_input` tool)

| Argument | Type | What it is |
|---|---|---|
| `ref` | string (required) | ref_12: from read_page, find or scroll |
| `value` | any (required) |  |
| `tab` | string | tabs_context's |

### `emission.javascript(...)`

Run text in the page as a REPL does: top-level await works, and the last expression's value comes back (no return needed). Its coordinates are CSS pixels, not the screenshot's. (the `javascript_tool` tool)

| Argument | Type | What it is |
|---|---|---|
| `text` | string (required) |  |
| `tab` | string | tabs_context's |

### `emission.console(...)`

The page's console messages and errors, newest last: pattern, a regex they match; onlyErrors; clear: forget them after. (the `read_console_messages` tool)

| Argument | Type | What it is |
|---|---|---|
| `tab` | string | tabs_context's |
| `pattern` | string |  |
| `onlyErrors` | boolean |  |
| `limit` | number |  |
| `clear` | boolean |  |

### `emission.network(...)`

What the page loaded: each URL, its kind, status, time and size; urlPattern: words in the URL; clear: forget them after. (the `read_network_requests` tool)

| Argument | Type | What it is |
|---|---|---|
| `tab` | string | tabs_context's |
| `urlPattern` | string |  |
| `limit` | number |  |
| `clear` | boolean |  |

### `emission.resize(...)`

Make the page's viewport width by height CSS pixels. (the `resize_window` tool)

| Argument | Type | What it is |
|---|---|---|
| `width` | number (required) |  |
| `height` | number (required) |  |
| `tab` | string | tabs_context's |

### `emission.prompts(...)`

What pages wait on you to answer: an alert, confirm or prompt, a site's sign-in box, files to upload. (the `prompts` tool)

### `emission.answer_prompt(...)`

Answer prompt id: accept (false: Cancel), text for a prompt, user and password for a sign-in, files (paths on that host) for an upload. (the `answer_prompt` tool)

| Argument | Type | What it is |
|---|---|---|
| `id` | string |  |
| `accept` | boolean |  |
| `text` | string |  |
| `user` | string |  |
| `password` | string |  |
| `files` | array of string |  |

## `baudetask.terminal`

A Bot's terminal tools: an MCP server on stdin and stdout (as baudelib.emissiontools is), run on the Bot's host. Each call reaches the terminals of a host of the mesh (this one if none's named) through that host's process (wire's multiplex op): Multiplex's own (multiplex.py, started there for open if it isn't running) and the shells Baude's and Record's Kubernetes view has open in pods. The terminal glows round its edges while the Bot's at it. Each is called by the agent as mcp__multiplex__<tool>. Each method is one of the Bot's own multiplex tools, as its multiplex op takes it, to host: this one if none's given.

Besides the generated calls below:

- `terminal.run(argv, *, cwd=None, timeout=60, host=None)`: argv run in a terminal of its own on host (a pseudo-terminal, as Multiplex's are), waited for: an Obj with output (what it wrote, its escapes taken out) and status (its exit status; None if it was still going after timeout seconds, when it's left to the host to end).

### `terminal.list(...)`

The terminals open on a host: Multiplex's tabs and the Kubernetes view's pod shells, the user's and yours, each with its id, title, folder, command and size. query: only those whose title, folder, command or text (screen and scrollback) has all its words, with the lines that do. (the `list_terminals` tool)

| Argument | Type | What it is |
|---|---|---|
| `query` | string |  |

### `terminal.open(...)`

A new tab in Multiplex (its window opened if it isn't): command run by your shell there, or the shell itself if none; in cwd. Answers once it's drawn its first output, with its screen. (the `open_terminal` tool)

| Argument | Type | What it is |
|---|---|---|
| `command` | string |  |
| `cwd` | string |  |
| `title` | string |  |

### `terminal.read(...)`

What's on a terminal's screen now and where its cursor is; lines_above: that many lines of scrollback above the screen too. (the `read_terminal` tool)

| Argument | Type | What it is |
|---|---|---|
| `terminal` | string (required) | list_terminals' id |
| `lines_above` | number |  |

### `terminal.type(...)`

Type text into a terminal, as if typed (a newline is Enter); enter: press Enter after it. Answers with its screen once output stops for a moment (wait_for for longer). (the `send_text` tool)

| Argument | Type | What it is |
|---|---|---|
| `terminal` | string (required) | list_terminals' id |
| `text` | string (required) |  |
| `enter` | boolean |  |

### `terminal.keys(...)`

Press keys in a terminal in turn, a space between: Enter, Tab, Escape, Backspace, Delete, Up, Down, Left, Right, Home, End, PageUp, PageDown, F1–F12, a character, or a combination like ctrl+c, ctrl+d, alt+b, shift+Tab. Answers with its screen once output stops for a moment. (the `send_keys` tool)

| Argument | Type | What it is |
|---|---|---|
| `terminal` | string (required) | list_terminals' id |
| `keys` | string (required) |  |

### `terminal.wait(...)`

Wait till pattern (a regex) shows on a terminal's screen, or its output's been quiet for quiet seconds (2 if no pattern), or its program ends, or timeout seconds (30; 120 at most) pass: which it was, and the screen. (the `wait_for` tool)

| Argument | Type | What it is |
|---|---|---|
| `terminal` | string (required) | list_terminals' id |
| `pattern` | string |  |
| `quiet` | number |  |
| `timeout` | number |  |

### `terminal.close(...)`

End a terminal's program (as closing its tab would) and close it. (the `close_terminal` tool)

| Argument | Type | What it is |
|---|---|---|
| `terminal` | string (required) | list_terminals' id |

## `baudetask.kube`

A cluster's objects as Freelens lists them, read through the host's kubeconfig and kubectl: its contexts (contexts), whether a context is still there as the head knows it (check: answered removed, or with nothing else; kubectl isn't run), one connected to (connect), a kind's objects (list), an object's YAML (yaml), what controls an object (owner), a pod deleted (delete), objects applied from YAML (apply), a pod's log as it is now (logs), a command run in a pod's container (exec: no terminal, its output when it ends), the whole stack's CPU and memory (summary, a read as list is). Secrets' values are never read. Each method is one kube request (its what), to host: this one if none's given. Answers KubeReply.

Besides the generated calls below:

- `kube.contexts(*, host=None, **_)`: The host's kubeconfig's contexts, each a KubeContext (name, server, current, local) with its host.
- `kube.objects(kind='pods', *, context=None, namespace=None, selector=None, crd=None, host=None)`: A kind's objects (kind: its resource, as the Kubernetes view's pages have them: pods, deployments, services, ...; crd: a custom resource's definition), each a KubeObject; namespace: only those in it; selector: only those whose labels match (app=web,tier!=cache). context: the kubeconfig's current one if none.
- `kube.pods(*, context=None, namespace=None, selector=None, host=None)`: The cluster's pods (objects("pods", ...)): pick one by its status, namespace or name and pass it on.
- `kube.get(obj=None, *, kind=None, name=None, namespace=None, context=None, host=None)`: An object's YAML (obj: a KubeObject; or kind, name and namespace).
- `kube.logs(pod=None, *, container=None, tail=None, previous=None, context=None, namespace=None, name=None, host=None, **_)`: A pod's log as it is now (pod: a KubeObject, or context, namespace and name): its last tail lines, its container's (the default one if none), its previous run's with previous.
- `kube.exec(pod=None, command=(), *, container=None, timeout=None, context=None, namespace=None, name=None, host=None, **_)`: A command (argv) run in a pod's container, no terminal: an Obj with text (what it wrote) and status.
- `kube.delete(obj=None, *, force=None, context=None, namespace=None, name=None, host=None, **_)`: A pod deleted (obj: a KubeObject, or context, namespace and name): what kubectl said.
- `kube.apply(manifest=None, *, context=None, namespace=None, host=None, **_)`: Objects applied from a manifest (YAML or JSON text): what kubectl said.

### `kube.contexts(...)`

kube's contexts.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The kubeconfig context. |
| `server` | string | The context's server as the head knows it (KubeContext's): a context of that name with another server is another cluster, and the request is answered removed. |

### `kube.check(...)`

kube's check.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The kubeconfig context. |
| `server` | string | The context's server as the head knows it (KubeContext's): a context of that name with another server is another cluster, and the request is answered removed. |

### `kube.connect(...)`

kube's connect.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The kubeconfig context. |
| `server` | string | The context's server as the head knows it (KubeContext's): a context of that name with another server is another cluster, and the request is answered removed. |

### `kube.list(...)`

kube's list.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The kubeconfig context. |
| `server` | string | The context's server as the head knows it (KubeContext's): a context of that name with another server is another cluster, and the request is answered removed. |
| `page` | string | list, yaml: the kind's page, by its resource. |
| `crd` | string | list, yaml: a custom resource's definition (plural.group) for a kind that isn't listed. |
| `selector` | string | list: only the objects whose labels match this selector (kubectl's -l: app=web,tier!=cache); a Secret's or a Helm release's page lists every one. |

### `kube.yaml(...)`

kube's yaml.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The kubeconfig context. |
| `server` | string | The context's server as the head knows it (KubeContext's): a context of that name with another server is another cluster, and the request is answered removed. |
| `page` | string | list, yaml: the kind's page, by its resource. |
| `crd` | string | list, yaml: a custom resource's definition (plural.group) for a kind that isn't listed. |
| `namespace` | string | yaml, owner, delete, logs, exec: the object's namespace; apply: where objects that name none go. |
| `name` | string | yaml, owner, delete, logs, exec: the object's name (logs, exec: the pod's). |

### `kube.owner(...)`

kube's owner.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The kubeconfig context. |
| `server` | string | The context's server as the head knows it (KubeContext's): a context of that name with another server is another cluster, and the request is answered removed. |
| `namespace` | string | yaml, owner, delete, logs, exec: the object's namespace; apply: where objects that name none go. |
| `name` | string | yaml, owner, delete, logs, exec: the object's name (logs, exec: the pod's). |
| `kind` | string | owner: the object's kind. |

### `kube.summary(...)`

kube's summary.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The kubeconfig context. |
| `server` | string | The context's server as the head knows it (KubeContext's): a context of that name with another server is another cluster, and the request is answered removed. |
| `namespaces` | array of string | summary: the namespaces it's the totals of (every one when it's left out; none when it's empty). |
| `since` | number | summary: the newest history sample the head has (its at): only later ones are sent (every one when it's left out). |
| `fresh` | boolean | summary: read the cluster now (Refresh), not a read the host kept or the fault it holds while it waits to try a failing cluster again. |

### `kube.delete(...)`

kube's delete.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The kubeconfig context. |
| `server` | string | The context's server as the head knows it (KubeContext's): a context of that name with another server is another cluster, and the request is answered removed. |
| `namespace` | string | yaml, owner, delete, logs, exec: the object's namespace; apply: where objects that name none go. |
| `name` | string | yaml, owner, delete, logs, exec: the object's name (logs, exec: the pod's). |
| `force` | boolean | delete: at once, not waiting for the pod to stop. |

### `kube.apply(...)`

kube's apply.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The kubeconfig context. |
| `server` | string | The context's server as the head knows it (KubeContext's): a context of that name with another server is another cluster, and the request is answered removed. |
| `namespace` | string | yaml, owner, delete, logs, exec: the object's namespace; apply: where objects that name none go. |
| `manifest` | string | apply: the objects, as YAML or JSON (kubectl apply -f -). |

### `kube.logs(...)`

kube's logs.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The kubeconfig context. |
| `server` | string | The context's server as the head knows it (KubeContext's): a context of that name with another server is another cluster, and the request is answered removed. |
| `namespace` | string | yaml, owner, delete, logs, exec: the object's namespace; apply: where objects that name none go. |
| `name` | string | yaml, owner, delete, logs, exec: the object's name (logs, exec: the pod's). |
| `container` | string | logs, exec: the container (the pod's default if none). |
| `previous` | boolean | logs: its previous run's. |
| `tail` | integer | logs: its last this many lines (the Kubernetes view's number if none). |

### `kube.exec(...)`

kube's exec.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The kubeconfig context. |
| `server` | string | The context's server as the head knows it (KubeContext's): a context of that name with another server is another cluster, and the request is answered removed. |
| `namespace` | string | yaml, owner, delete, logs, exec: the object's namespace; apply: where objects that name none go. |
| `name` | string | yaml, owner, delete, logs, exec: the object's name (logs, exec: the pod's). |
| `container` | string | logs, exec: the container (the pod's default if none). |
| `command` | array of string | exec: what's run, its argv (kubectl exec --, with no terminal and nothing on its stdin). |
| `timeout` | number | exec: seconds it may run (60 if none; 600 at most): then it's stopped. |

## `baudetask.docker`

Docker's containers, images, volumes, networks and Compose projects on the host, read and acted on through its docker CLI: its contexts and the sidebar's categories (contexts), a category's objects (list), an object inspected (inspect), a container's or Compose project's log as it is now (logs), a command run in a running container (exec: no terminal, its output when it ends), and a container or Compose project started, stopped, restarted or removed (start, stop, restart, remove; an image, a volume or a network removed). Each method is one docker request (its what), to host: this one if none's given. Answers DockerReply.

Besides the generated calls below:

- `docker.contexts(*, host=None, **_)`: The Docker contexts the host reaches, each a DockerContext (name, current) with its host.
- `docker.objects(page='containers', *, context=None, host=None)`: A page's objects (containers, running, images, volumes, networks, compose), each a DockerObject. context: docker's current one if none.
- `docker.ps(*, all=False, context=None, host=None)`: The containers (the running ones, unless all), each a DockerObject.
- `docker.containers(*, context=None, host=None)`: Every container, running or not, each a DockerObject.
- `docker.images(*, context=None, host=None)`: 
- `docker.inspect(obj=None, *, format=None, page=None, id=None, context=None, host=None, **_)`: What docker inspect says of an object (obj: a DockerObject; or page and id), as JSON or YAML text.
- `docker.logs(obj=None, *, tail=None, page=None, id=None, context=None, host=None, **_)`: A container's or Compose project's log as it is now: its last tail lines.
- `docker.exec(obj=None, command=(), *, timeout=None, page=None, id=None, context=None, host=None, **_)`: A command (argv) run in a running container, no terminal: an Obj with text (what it wrote) and status.
- `docker.start(obj=None, *, page=None, id=None, context=None, host=None, **_)`: A container or Compose project started: what docker said.
- `docker.stop(obj=None, *, page=None, id=None, context=None, host=None, **_)`: A container or Compose project stopped: what docker said.
- `docker.restart(obj=None, *, page=None, id=None, context=None, host=None, **_)`: A container or Compose project restarted: what docker said.
- `docker.remove(obj=None, *, page=None, id=None, context=None, host=None, **_)`: An object removed (a container stopped first; a Compose project taken down): what docker said.
- `docker.rm(obj=None, *, page=None, id=None, context=None, host=None, **_)`: An object removed (a container stopped first; a Compose project taken down): what docker said.

### `docker.contexts(...)`

docker's contexts.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The context, by its name, as contexts lists it. |

### `docker.list(...)`

docker's list.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The context, by its name, as contexts lists it. |
| `page` | string | list, inspect, logs, exec and the actions: the category's page, by its key. |

### `docker.inspect(...)`

docker's inspect.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The context, by its name, as contexts lists it. |
| `page` | string | list, inspect, logs, exec and the actions: the category's page, by its key. |
| `id` | string | inspect, logs, exec and the actions: the object, by its id (a Compose project's, its name). |
| `format` | 'json' \| 'yaml' | inspect: as JSON (the default) or YAML. |

### `docker.start(...)`

docker's start.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The context, by its name, as contexts lists it. |
| `page` | string | list, inspect, logs, exec and the actions: the category's page, by its key. |
| `id` | string | inspect, logs, exec and the actions: the object, by its id (a Compose project's, its name). |

### `docker.stop(...)`

docker's stop.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The context, by its name, as contexts lists it. |
| `page` | string | list, inspect, logs, exec and the actions: the category's page, by its key. |
| `id` | string | inspect, logs, exec and the actions: the object, by its id (a Compose project's, its name). |

### `docker.restart(...)`

docker's restart.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The context, by its name, as contexts lists it. |
| `page` | string | list, inspect, logs, exec and the actions: the category's page, by its key. |
| `id` | string | inspect, logs, exec and the actions: the object, by its id (a Compose project's, its name). |

### `docker.remove(...)`

docker's remove.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The context, by its name, as contexts lists it. |
| `page` | string | list, inspect, logs, exec and the actions: the category's page, by its key. |
| `id` | string | inspect, logs, exec and the actions: the object, by its id (a Compose project's, its name). |

### `docker.logs(...)`

docker's logs.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The context, by its name, as contexts lists it. |
| `page` | string | list, inspect, logs, exec and the actions: the category's page, by its key. |
| `id` | string | inspect, logs, exec and the actions: the object, by its id (a Compose project's, its name). |
| `tail` | integer | logs: its last this many lines (the Docker view's number if none). |

### `docker.exec(...)`

docker's exec.

| Argument | Type | What it is |
|---|---|---|
| `context` | string | The context, by its name, as contexts lists it. |
| `page` | string | list, inspect, logs, exec and the actions: the category's page, by its key. |
| `id` | string | inspect, logs, exec and the actions: the object, by its id (a Compose project's, its name). |
| `command` | array of string | exec: what's run, its argv (docker exec, with no terminal and nothing on its stdin). |
| `timeout` | number | exec: seconds it may run (60 if none; 600 at most): then it's stopped. |

## `baudetask.db`

The running databases, read-only: their servers and databases (servers), one database's schema (schema), a table's first rows (rows), a query's rows (query: Postgres's, in a read-only transaction). Secret-looking columns are masked here. Each method is one databases request (its what), to host: this one if none's given. Answers DatabasesReply.

Besides the generated calls below:

- `db.servers(*, host=None, **_)`: The database servers running on the host (in Docker, or a local cluster), each a DbServer with its databases (their names) and host.
- `db.connections(*, host=None, **_)`: The database servers running on the host (in Docker, or a local cluster), each a DbServer with its databases (their names) and host.
- `db.schema(server=None, database=None, *, host=None, **_)`: A database's tables (server: a DbServer, or its container; database: its first if none), each a DbTable with its columns.
- `db.rows(server=None, database=None, table=None, *, schema='public', host=None, **_)`: A table's first rows (table: a DbTable or its name), each {column: value}, secret-looking ones masked.
- `db.query(sql, server=None, database=None, *, host=None, **_)`: A query's rows (one statement that reads, run read-only), each {column: value}, secret-looking ones masked. server: a DbServer or its container; the host's first if none.

### `db.servers(...)`

databases's servers.

### `db.schema(...)`

databases's schema.

| Argument | Type | What it is |
|---|---|---|
| `container` | string | schema, rows, query: the server's container. |
| `database` | string | schema, rows, query: the database. |

### `db.rows(...)`

databases's rows.

| Argument | Type | What it is |
|---|---|---|
| `container` | string | schema, rows, query: the server's container. |
| `database` | string | schema, rows, query: the database. |
| `schema` | string | rows: the table's schema. |
| `table` | string | rows: the table. |

### `db.query(...)`

databases's query.

| Argument | Type | What it is |
|---|---|---|
| `container` | string | schema, rows, query: the server's container. |
| `database` | string | schema, rows, query: the database. |
| `sql` | string | query: one statement that reads (SELECT, WITH, VALUES, TABLE), run read-only; its rows come back as rows' do, secret-looking columns masked, QUERY_ROWS_MAX of them at most. |

## `baudetask.chats`

The type of the None singleton.

Besides the generated calls below:

- `chats.list(*, bots=False, host=None)`: A host's chats, newest first, each a Session (id, title, state, bot, ...) with its host; bots: Bots only.
- `chats.message(session, text, *, wait=True, timeout_s=600, host=None)`: text sent to a chat (session: a Session or its id), as from the task's Bot, and unless not wait its answer waited for: an Obj with session, text (what it said as its turn ended) and failed.

## `baudetask.subagents`

The type of the None singleton.

Besides the generated calls below:

- `subagents.start(prompt, *, host=None, model=None, cwd=None, title=None, mode='auto', wait=True, timeout_s=3600, account=None)`: A subagent started on host (this one if none's given) with model (a Model, or its value), given prompt: an Obj with session, host, text (what it said as its first turn ended; None if not wait) and failed. account: the subscription it runs on (a Subscription, or an id or name of one of host's, "" for its CLI's own sign-in), in place of the one it'd take. On this host it's baudetask.agent's: nested under the Bot, kept with the run.
- `subagents.list(*, host=None)`: The Bot's subagents on host (the chats it, or its tasks' runs, started there), each a Session.

## `baudetask.ops`

Every request a host takes, as the schema has it: one method each, named as its op, its fields its keyword arguments, to host (this one if none's given). What a request answers with more than once (a terminal's output, a log followed) is a connection's own: these give its first answer only.

Their fields are the wire's, listed in [mcp.md](mcp.md#the-wire).

`access`, `access_set`, `accounts`, `activate`, `agent`, `always_allow`, `answer_task`, `archive`, `attach`, `autofix`, `bot`, `bot_picture`, `browser_open`, `catalogue`, `chat_size`, `close`, `databases`, `deactivate`, `decide`, `deciders`, `delete`, `deps`, `detach`, `docker`, `docker_follow`, `draft_save`, `edit_adopt`, `edit_decide`, `edit_propose`, `edit_revise`, `edit_withdraw`, `edits`, `emission`, `emission_answer`, `emission_serve`, `emission_watch`, `emission_watch_say`, `feedback`, `files`, `fixes`, `git`, `held`, `in_`, `inbox`, `inbox_picture`, `inbox_press`, `inbox_raise`, `inbox_remove`, `inbox_revise`, `inbox_route`, `inbox_say`, `inbox_wait`, `inbox_withdraw`, `infer`, `input`, `kill`, `kube`, `kube_follow`, `link`, `list_dir`, `login`, `login_code`, `mcp`, `mcp_close`, `mcp_line`, `mcp_relay`, `memory`, `multiplex`, `notes`, `queue`, `quit`, `read_file`, `rename`, `restart`, `resume_cancel`, `rotate`, `run_task`, `seen`, `senders`, `set_cwd`, `signal_names`, `start`, `stop`, `subagents`, `subscribe`, `task`, `task_ask`, `task_bind`, `task_choices`, `task_output`, `task_report`, `task_run`, `task_stop`, `tasks`, `telemetry`, `telemetry_read`, `telemetry_set`, `terminal`, `terminal_close`, `terminal_in`, `terminal_resize`, `terminals`, `theme_remove`, `theme_save`, `themes`, `transcript`, `unarchive`, `unlink`, `waits_on`

## Objects

What the calls answer with: each a `dict`, its fields attributes too. Their fields are the wire's entities' and answers', listed in [mcp.md](mcp.md#entities).

`Accepted`, `AccessChat`, `AccessMember`, `AccessReply`, `AccessRule`, `AccessRules`, `AccessSetReply`, `AccessState`, `Account`, `AccountsReply`, `Activated`, `AgentStarted`, `Allowed`, `Archived`, `Ask`, `Attached`, `AutofixPull`, `AutofixReply`, `BotFields`, `BotListing`, `BotPictureReply`, `BotRef`, `BotSetReply`, `Branch`, `BrowserOpened`, `Busy`, `CallbackPage`, `CatalogueReply`, `ChatDraft`, `ChatLogin`, `ChatQueue`, `ChatSizeReply`, `Clock`, `Command`, `Commit`, `ContextCategory`, `ContextUsage`, `CwdSet`, `DatabasesReply`, `DbColumn`, `DbLink`, `DbServer`, `DbTable`, `Deactivated`, `DecideQuestion`, `Decided`, `Decider`, `DecidersReply`, `Decision`, `Deleted`, `Dep`, `DepsReply`, `Dir`, `DirEntry`, `DockerColumn`, `DockerContext`, `DockerEnd`, `DockerFault`, `DockerPage`, `DockerReply`, `DockerRow`, `DockerSection`, `DockerText`, `DraftSaved`, `Edit`, `EditReply`, `EditsReply`, `EmissionFrame`, `EmissionReply`, `EmissionServing`, `EmissionWatchEnd`, `EmissionWatching`, `Error`, `FeedbackFiled`, `FeedbackTarget`, `File`, `FixesReply`, `FoundLink`, `GitFound`, `GitMade`, `GitParent`, `GitReply`, `Gone`, `GoneFolder`, `Head`, `HeldMessage`, `HeldReply`, `HelloReply`, `HostHello`, `InboxAnswer`, `InboxAnswered`, `InboxAsk`, `InboxButton`, `InboxDone`, `InboxPicture`, `InboxPictureData`, `InboxPictureReply`, `InboxProposal`, `InboxRaised`, `InboxReply`, `InboxVersion`, `InferData`, `InferEnd`, `InferHead`, `Info`, `Joined`, `KubeColumn`, `KubeContainer`, `KubeContext`, `KubeContexts`, `KubeEnd`, `KubeFault`, `KubePage`, `KubeRef`, `KubeReply`, `KubeRow`, `KubeRows`, `KubeSample`, `KubeSection`, `KubeShare`, `KubeShareColumn`, `KubeSummary`, `KubeText`, `KubeUse`, `Linking`, `LoginEnd`, `LoginSaid`, `Member`, `MemberInfo`, `MemoryReply`, `MeshView`, `MultiplexReply`, `Notebook`, `NotesReply`, `OutLink`, `PageNow`, `PointerEvent`, `Provider`, `ProviderModel`, `Proxy`, `PullInfo`, `PullLink`, `QueueReply`, `QueuedMessage`, `Quitting`, `Record`, `Relayed`, `Relaying`, `Renamed`, `RestartBlocker`, `RestartPending`, `RestartState`, `ResumeCancelled`, `Rotated`, `Running`, `SeenSet`, `Sender`, `SendersReply`, `Session`, `SignalNamesReply`, `SignalRef`, `Started`, `Stopped`, `StoredSender`, `SubagentFile`, `SubagentsReply`, `Tab`, `Task`, `TaskAnswer`, `TaskAnswered`, `TaskBinding`, `TaskChoice`, `TaskChoicesReply`, `TaskField`, `TaskLogLine`, `TaskReply`, `TaskReported`, `TaskRun`, `TaskRunRef`, `TaskRunReply`, `TaskStarted`, `TaskTrigger`, `TaskVariable`, `TasksReply`, `TelemetryDay`, `TelemetryEvent`, `TelemetryFront`, `TelemetryKept`, `TelemetryLine`, `TelemetryModel`, `TelemetryProc`, `TelemetryProfile`, `TelemetryProfiling`, `TelemetryReadReply`, `TelemetrySample`, `TelemetrySetReply`, `TelemetryState`, `TelemetryUsage`, `TerminalEnd`, `TerminalInfo`, `TerminalOpened`, `TerminalOut`, `TerminalsReply`, `Theme`, `ThemeFiles`, `ThemeRemoved`, `ThemeSaved`, `ThemesReply`, `ToolNames`, `TranscriptReply`, `TurnEnd`, `Unarchived`, `Unlinked`, `Update`, `UsageLimit`, `UsageReading`, `WaitTarget`, `WaitsOnReply`, `WatchSay`, `WatchState`, `Where`
