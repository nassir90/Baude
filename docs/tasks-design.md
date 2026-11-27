# Tasks

A Bot's Routines become **Tasks**: scripts a Bot's host keeps and runs, started by **triggers** (a schedule, a git
push, by hand, later a web page or an Emission page), with **inputs** that a trigger's event, a person or the Bot fills,
and the Bot behind them as the fallback for errors and the handler for steps a script can't decide. Everything goes
over the wire, so a head, the phone, a Bot's MCP tools and a plain script all drive tasks the same way.

## The Task

A task belongs to one Bot and is kept on that Bot's host, in the Bot's folder (`<state>/bots/<bot>/tasks/`):

| Field | What it is |
|---|---|
| `id`, `name` | |
| `runtime` | `python` (the host's Python 3), `shell` (`/bin/sh`), or `bot`: the script is a prompt the Bot is given as a turn (what a Routine was) |
| `script` | its text; kept content-addressed as `scripts/<version>.<ext>`, every version kept |
| `version` | the first 12 hex of the script's SHA-256; every run records the version it ran |
| `form` | its inputs, each `{name, label?, type: text, number, bool or choice, choices?, default?, required?}` |
| `triggers` | below |
| `variables` | typed variables, each `{name, type, description?}`, bound once for the task (below) |
| `bindings` | `{name: {value, label?, effort?, host?, model?, context?, namespace?, container?}}`: what each variable's bound to |
| `warnings` | (the host's to say) `{name: why}` for each bound variable that isn't there now |
| `notify_failure` | true: a failed run (non-zero exit, timeout) is sent to the Bot with its log |
| `notify_exit` | true: a run that exits 0 is told to the Bot, for its information, with the end of its output (default false) |
| `timeout_s` | the most a run may take (default an hour), not counting time it waits for input; it's stopped after |
| `paused` | its triggers don't fire; it can still be run by hand |

A script runs with the Bot's folder as its working directory (its worktree made again from its branch first, if it's
gone, as for a message), in a process group of its own, and gets its run in its environment:
`BAUDE_SOCKET`, `BAUDE_TASK_RUN`, `BAUDE_TASK_TOKEN`, `BAUDE_TASK_INPUTS` (the inputs as filled, JSON),
`BAUDE_TASK_PAYLOAD` (the trigger's event, JSON) and `BAUDE_IN_<FIELD>` for each input, for shell scripts.
`baudelib/script/` is on its `PYTHONPATH`, so a Python script does `import baudetask`. `BAUDE_TASK_VARS` has its
variables, `{name: {type, binding, warning?}}`.

## Variables

A task declares variables, each a name ("Translation Model"), a type and an optional description: `model` (a model's
value, its effort, the host serving it), `decider` (a decision model's endpoint: its URL and model), `host`,
`browser_host`, `terminal_host` (a host of the mesh), `kube_context`, `pod`, `container`, `database`, `text`. Unlike the
form's inputs, which each run fills, a variable is bound once for the task: by its Bot (`set_task`'s `bindings`) or by
a person on the task's page (`task_bind`, needing manage write on the Bot's chat, as its other changes do).

Binding offers what the task's host has of that type (`task_choices`), from the lists ⌘K lists: its models (as its
hello lists them, and other hosts' run here), the mesh's hosts, its kubeconfig's contexts, the workloads in its local
clusters, its Docker containers, the databases running on it, and its providers' endpoints for a decider. Any text is
taken instead, for a flaky entity. Clusters' choices need Kubernetes read, Docker's Docker read and databases'
Databases, as the catalogue's do.

The host looks at every bound variable every minute, at once as one's bound, and as a run starts: a model it can't run
(not served, no account), a decider's endpoint not reachable, a host not in the mesh or out of reach, a context gone,
a pod or container not running, a database gone. Each is a warning on the variable's row and its task's row, and the
first lines of the run's log (kind `warning`). The run starts all the same: nothing is put in its place, and the script
decides what to do with `baudetask.vars`:

```python
import baudetask as task
model = task.vars.translation_model          # or task.vars["Translation Model"]: a Model (a str: its value), or None
if model is None or model.warning:
    model = task.vars.worker_agent_model
task.agent("Translate this: ...", model=model)  # its value and its effort
```

Each variable is a `str` of its value with its type's fields as attributes (`.effort`, `.host`, `.context`,
`.namespace`, `.container`, `.model`), `.label` and `.warning`.

## Triggers

A trigger is a small program that turns the world into events. Each event has a **payload**, an object, and fires one
run. The payload fills the inputs: an input takes `payload[fills[field]]` if the trigger maps it, else `payload[field]`
if the payload has a key of that name, else its default.

| Kind | Config | Payload | In this PR |
|---|---|---|---|
| `manual` | none: every task can be run by hand, with its inputs filled in | the inputs as filled | yes |
| `schedule` | `every` minutes (5 to a month), or `at` HH:MM on `days` (host clock) | `{time}` | yes |
| `git_push` | `repo` (a folder on the host), `branch`, `poll_s` | `{repo, branch, before, after, commit, message, author}` | yes, by polling the branch head |
| `web` | `url`, `poll_s`, a parser script | what the parser prints | later |
| `emission` | a Bot's Emission profile, a page, `poll_s`, a decider script | what the decider prints | later |
| `script` | a program the host runs every `poll_s`, given its last state | `{"events": [...], "state": ...}` it prints | later |

`git_push` is built the way `script` triggers will be: a poll that's given its last state (the head it saw) and
returns events and its new state. A push seen while the host was off fires once when it's back. The first poll only
records the head. A schedule that comes due while a run of its task is going is recorded as a `skipped` run. A push
seen while a run is going isn't marked seen: once that run ends the branch is looked at again, and one run takes every
push since (its payload's `before` the head last run, `after` the head now).

## A run

`running` → (`asking` while it waits for input: an ask of its Bot, shown as "Waiting for input") → `done` | `failed` | `stopped` | `skipped`.

Each run is kept as `runs/<id>.json` with a log `runs/<id>.log` (JSON lines `{at, kind, text, values?}`, kinds `out`,
`err`, `say`, `ask`, `answer`, `status`, `agent`, `warning`). The latest 100 runs of each task are kept. A run records: task, version,
trigger (`{id, kind}`, `manual` for by hand), payload, inputs, started (and the host's UTC offset then, so its
time reads as it did there), ended, seconds, exit code, error, asks, `handed` (its failure given to the Bot), and its
script's `pid` and `pgid`.

Stopped, or out of time, a run's process group is sent SIGTERM and, 5 s on, SIGKILL, whether or not the script itself
has exited by then. A host killed as a run went (no chance to stop it) finds the run still `running` as it starts: the
run's process group, if one of its processes still runs that script, is killed, and the run is kept as `stopped`, so
two runs of one task never overlap.

## Asking the Bot, and the fallback

A script calls its Bot through the wire, mid-run:

```python
import baudetask as task

task.say("merging", task.inputs["branch"])
try:
    merge()
except Conflict as e:
    plan = task.ask(f"Resolve this conflict:\n{e}", form=[{"name": "keep", "type": "choice",
                                                          "choices": ["ours", "theirs", "both"]}])
    apply(plan["keep"])
```

- `ask(text, form=None)` sends `task_ask`. The host gives the Bot a turn of its own (from the task, by its name): it
  waits until the Bot is idle and then sends it, one ask of a Bot at a time, so the turn that ends next is that ask's,
  never a user's turn or one a stopped run's ask left going. The message ends by telling the Bot to answer with its
  `answer_task` tool (`{run, answer}`, `answer` an object of the form's fields, listed; `{run, text}` for an ask with
  no form). The run goes on as soon as the tool's called, not at the turn's end: the turn's text is only commentary,
  shown in the chat as any other. An answer that doesn't fit the form is refused to the tool with what was wrong, so
  the Bot can call it again. A turn that ends without the tool is reminded once; ending without it again, the ask
  fails (said as refused if the tool was called and refused). A Bot whose agent has no mesh tools (an Apple model) is
  asked for a JSON object of the fields in its reply instead, checked the same way, sent back once if it doesn't fit.
  The run is `asking` meanwhile ("Waiting for input"): it waits up to 7 days (`ASK_S`), its `timeout_s` clock
  stopped, and the script pings the host so the connection stays up however long the answer takes. The reminder is
  a message of its own, marked as waiting for the answer as the ask was.
- `fallback(error)` is an ask that says it's an error: the Bot gets the error and the log so far. The host refuses it
  for a task whose `notify_failure` is off.
- `@task.main` runs a function with the inputs; an exception's traceback is printed and the script exits 1, so the run
  fails and the host hands it over (below).
- Any runtime, Python or not: with `notify_failure` on, a run that exits non-zero or times out is sent to the Bot by the
  host (its exit, the task's id, its version and the log's last lines, and where the script is), and the Bot's answer
  goes in the log. The run is over first: kept as `failed` and no longer running before the Bot's turn is sent, so the
  Bot may run the task again in that turn.

## Self-healing (later; most of it falls out)

The Bot can already read and change its tasks with its MCP tools (`tasks`, `task`, `set_task`, `run_task`,
`task_run`). A failure handed to it says which task and version failed and how; the Bot fixes the script with
`set_task`, which makes a new version, and reruns it with `run_task`. A Bot's `run_task` of its own task starts the run
and returns at once (its `wait` defaults to false, and even waiting it returns as soon as the run asks the Bot), since
the Bot can only answer the run's asks once its turn is over. Missing: a trigger that fails to parse (a web
trigger) raising itself the same way, and the host rerunning the event that failed once the new version is saved.

## Bot git repos (later)

Each Bot gets a git repo in its folder for its tools; a task's script is a file in it, and the version is the commit.
The host serves the repo to the Bot's working folder; a push to it is a `git_push` trigger on the Bot's own repo that
redeploys its tasks, so the Bot's fix-and-push is the deploy.

## Telemetry

The runs are the telemetry: when each trigger fired, how long each run took, failures, the version each ran, how
many asks. Read with `task` (a task's runs) and `task_run` (one run and its log). Later: the same as host telemetry
events (`task_run`) on Settings → Telemetry's timeline.

## The wire (schema/types)

| Request | Answer | Does |
|---|---|---|
| `tasks {session, set?, delete?}` | `tasks {session, tasks, clock}` | a Bot's tasks, without their scripts; one made or changed (`set`, a Task with its `script`), or deleted |
| `task {session, id, script?}` | `task {session, task, runs}` | one task, with its script if `script` is true (the Bot's `task` tool asks; the apps don't), and its latest runs |
| `run_task {session, id, inputs?}` | `task_started {session, run}` | run it now, by hand |
| `task_run {session, run}` | `task_run {session, run, log}` | one run and its log |
| `task_stop {session, run}` | `task_run` | a run stopped |
| `task_bind {session, id, name, binding?}` | `task` | one variable bound (no binding: unbound), its warning looked at now |
| `task_choices {session, type}` | `task_choices {session, type, choices}` | what a variable of that type could be bound to here |
| `task_report {run, token, say?}` | `task_reported` | a script's line in its run's log |
| `task_ask {run, token, text, form?, error?}` | `task_answer {text, values?}` | a script asks its Bot (above) |
| `answer_task {session, run, answer?, text?}` | `task_answered {session, run, task, values?, text?}` | the Bot (or someone who can manage it) answers the ask of its run that's waiting on it |

Entities: `Task`, `TaskField`, `TaskTrigger`, `TaskVariable`, `TaskBinding`, `TaskChoice`, `TaskRun`, `TaskLogLine`. `task_report` and `task_ask` are a running
script's: taken only on the host's own socket, with the run's token.

## Access

Making, changing or running a task runs code on the host, so `tasks` with `set` and `run_task` need **Shell**, on top of
managing the Bot's chat, and, as for anything that drives a Bot, Browser too (the rules from #460). Reading tasks and
runs is managing the chat. Chose Shell over a new Tasks capability because a task is a shell's worth of power: anyone
who may run a shell may already write and run any script, and anyone who may not shouldn't be able to through a task.
The Bot's own `set_task` and `run_task` tools aren't on its always-allowed list, so its permission mode asks about them
as it asks about Bash.

Answering an ask (`answer_task`) is managing the Bot's chat (Manage write): the host takes it only for an ask of that
run that's waiting on that Bot, so the Bot's own tool (on its host's socket) and anyone who may manage the Bot can
answer, and no one else.

## Routines

A Bot's routines move to tasks the first time its host reads its tasks: each becomes a `bot` task with the routine's
prompt as its script and its schedule as a `schedule` trigger (next and last kept). Each task takes its routine's id,
so a host killed between saving the tasks and dropping the routines moves them over again without making any twice.
The `routines` and `run_routine` ops, and the Routines page, are gone; the Tasks page takes their place.

## Screens

Side panel → Tasks (a Bot's chat), on Baude and the phone:

- **Tasks list**: each task's name, its last run's state, a switch (paused or not) and Run. The Bot makes tasks, as a
  person asks it to.
- **A task**: Run (its inputs first, if it has any), Change (its name, and whether failures go to the Bot), Delete
  (confirmed; its runs and the scripts only it ran go too); its triggers (+ adds one); its inputs (Change Inputs); its
  runs, newest first, with the version each ran. Triggers, inputs and runs are compact rows.
- **A run**: its state, trigger, version, time and length; its log, each ask and answer as a hand-off to the Bot.
- **Adding a trigger**: its kind, its settings, and which payload key fills each input.

No screen shows a task's script or lets a person edit it, nor its runtime: scripts grow into whole programs in the
Bot's repo, and they're the Bot's to write and change (`set_task`). To change a task's script, a person asks the Bot.

## In this PR

Tasks as scripts run by the host (python, shell, bot); manual, schedule and git push triggers; payloads filling
forms; `baudetask` (say, ask, fallback, main) over the wire; the host's fallback for
failed runs; runs and logs; the Bot's task tools; Routines moved over; the Tasks page on Baude and the phone; a test
that runs tasks through the wire.

## Later

Web, Emission and script triggers; self-healing's parse-error raising and automatic rerun; Bot git repos; host telemetry events; tasks that belong to no Bot.
