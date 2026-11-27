# Deciding in a task's script: `decide()`

A task's script can ask a Bot (`ask()`) or a decision model (`decide()`). A decision model answers set questions about some text or data. It returns a typed answer with a calibrated confidence, in well under a second, and doesn't hold a conversation. Baude talks to any server that speaks [Strands Decider](https://github.com/strands-labs/strands-decider)'s System One API (`POST /v1/systemone`, `GET /health`), such as `strands-decider serve`. The server can run on the task's host or on any other host in the mesh.

## Setting up a decider

Run a decider wherever you want it, for example on a Mac with MLX:

```sh
pip install "strands-decider[mlx]"
strands-decider serve StrandsAgents/strands-decider-E2B-gemma4-v2-2611 --device mlx --port 8000
```

Then, in **Settings → Dependencies → Deciders**, with that host picked beside New session, give it a name and its URL (`http://127.0.0.1:8000`) and press **Add Decider**. Each decider's row says whether it answers. **Test** looks again, and **Remove** forgets it. Each host keeps its own list. Other hosts in the mesh see a host's deciders, and whether each answers, by its name: `studio/triage` is the decider named Triage on studio.

Ollama's and MLX's chat models aren't deciders. Only a server that answers `/v1/systemone` is.

## Asking

```python
import baudetask as task

text = task.inputs["message"]

# One question: its answer, directly
team = task.decide(text, task.choice("Which team should handle this?", ["billing", "sales", "retail"]),
                   model="studio/triage")
team.choice        # "billing"
team.confidence    # 0.91
team.scores        # {"billing": 0.91, "sales": 0.06, "retail": 0.03}

# Several: a dict of answers, by name (the decider reads the state once for all of them)
got = task.decide(text, {
    "urgent": task.noul("Is this urgent?"),
    "mood": task.score("How frustrated is the customer?", ["calm", "frustrated", "depressed"]),
}, model="studio/triage")
got["urgent"].noul       # 0.86: the probability it's true
got["mood"].score        # 1.2: the expected level, 0 being "calm"
got["mood"].scores       # {"calm": 0.12, "frustrated": 0.55, "depressed": 0.33}
```

The questions:

| Shorthand | Asks | The answer's fields |
|---|---|---|
| `choice(instructions, options)` | Which one of `options` (a list of names, or `{name: what it means}`) | `.choice`, `.scores` (each option's probability), `.confidence` |
| `noul(instructions, true=None, false=None)` | Yes or no (`true`, `false`: what each means, if it helps) | `.noul` (the probability it's true), `.confidence` (the probability of the side it leans to) |
| `score(instructions, levels)` | Where it sits on `levels`, lowest first (2 to 10 of them) | `.score` (the expected level, 0 the lowest), `.scores` (each level's probability, by name), `.confidence` |

`state` can be text, or data (a dict or a list) that the decider reads as it reads text. `images=[…]` (base64) go with the state, for a decider that reads images.

`model` is a decider's name: the task's host's own (`"triage"`), or another host's (`"studio/triage"`). If you leave it out, the task's host's first decider is used. A task variable of the decider type gives you the name, so the Bot that runs the task picks the decider. `task.deciders()` lists every decider the task's host knows, its own and the mesh's, with `value` (what `model` takes), `name`, `host_name` and `reachable`. Use it to pick one while the script runs.

Every call goes through the task's host, with the run's token. A decider on another host is asked through the mesh, as that host's access rules let this host use its models (Settings → Access → Providers). The script never reaches a decider's URL itself.

## When it can't decide

If the decider isn't running, doesn't answer, or isn't there, `decide()` raises `task.DecisionUnavailable` saying why. Nothing else is asked in its place: Baude never switches to another model on its own. What happens next is the script's choice:

```python
try:
    urgent = task.decide(text, task.noul("Is this urgent?"), model=task.inputs["decider"])
    if urgent.confidence < 0.8:
        urgent = None
except task.DecisionUnavailable as e:
    task.say("no decision:", e)
    urgent = None
if urgent is None:
    urgent = task.ask(f"Is this urgent?\n\n{text}", form=[{"name": "urgent", "type": "bool"}])["urgent"]
else:
    urgent = urgent.noul >= 0.5
```

A question the decider won't take (a choice with one option, a score with eleven levels) raises `task.Refused` with what the decider said. `DecisionUnavailable` is a `Refused` too.

You get warned before a run, not after: the host lists each decider with whether it answered when it last looked, so a task variable bound to one that isn't answering is flagged when it's bound, and before the task runs.

Outside a task (no `BAUDE_TASK_RUN`), `decide()` prints the questions and returns stand-ins, as `ask()` does: each choice's first option, a score's lowest level, a noul of 0, all with confidence 0.
