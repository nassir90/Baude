"""The task-script SDK's generated half, and its reference: baudelib/script/baudetask_sdk/generated.py and
site/docs/tasks.md, made from schema/types (the wire's requests and answers, the ones the heads use) and the Bot's own
tools' definitions (baudelib.emissiontools, baudelib.multiplextools), so an op or a tool added there is in the SDK once
this is run again. The hand-written half (baudetask_sdk/client.py: the wire; baudetask_sdk/__init__.py: typed
objects to enumerate and pass back, hosts, models, chats, subagents) is documented from its own docstrings.

    python3 -m baudelib.tasksdk           # both written
    python3 -m baudelib.tasksdk --check   # exit 1 if either isn't as it would be written now
"""
import argparse
import inspect
import keyword
import os
import re
import sys
import textwrap

from baudelib import emissiontools, multiplextools, wire, wording

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATED = os.path.join(ROOT, "baudelib", "script", "baudetask_sdk", "generated.py")
DOCS = os.path.join(ROOT, "site", "docs", "tasks.md")

# Each area: the request whose `what` its methods are (one method a value), or the Bot's tools it drives.
WHAT_AREAS = {"Kube": "kube", "Docker": "docker", "Databases": "databases"}
TOOL_AREAS = {"Emission": ("emission", emissiontools), "Terminal": ("multiplex", multiplextools)}
TOOL_NAMES = {  # (a tool's method, where its own name reads badly in a script: the rest keep theirs)
    "tabs_context": "tabs", "tabs_create": "open", "tabs_close": "close", "get_page_text": "text",
    "javascript_tool": "javascript", "read_console_messages": "console", "read_network_requests": "network",
    "resize_window": "resize", "list_terminals": "list", "open_terminal": "open", "read_terminal": "read",
    "send_text": "type", "send_keys": "keys", "wait_for": "wait", "close_terminal": "close"}
SKIP_TOOLS = {"browser_batch"}  # (a script calls the tools in turn itself)
NOT_OPS = {"hello", "relay", "join", "ping"}  # (the connection's own: client.py's)
WIDTH = 116


def _py(name):
    """A field's name as a Python parameter: as it is, or with _ after it (as_, from_)."""
    return name + "_" if keyword.iskeyword(name) else name


def _kind(p):
    if "$ref" in p:
        return wire._name(p["$ref"])
    for s in p.get("anyOf") or ():
        if "$ref" in s:
            return wire._name(s["$ref"]) + " or None"
    if "const" in p:
        return repr(p["const"])
    if "enum" in p:
        return " | ".join(repr(v) for v in p["enum"])
    t = p.get("type", "any")
    t = " or ".join("None" if x == "null" else x for x in t) if isinstance(t, list) else t
    return t + (f" of {_kind(p['items'])}" if "items" in p else "")


def _one(text):
    return " ".join(str(text or "").split())


def _doc(lines, indent):
    """A docstring, its lines wrapped at WIDTH."""
    pad = " " * indent
    out = []
    for line in lines:
        if not line:
            out.append("")
            continue
        sub = "    " if line.startswith("    ") else ""
        out += textwrap.wrap(line.strip(), WIDTH - indent - len(sub), initial_indent=sub,
                             subsequent_indent=sub + ("    " if sub else "")) or [""]
    body = "\n".join((pad + x) if x else "" for x in out).replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'{pad}"""{body.lstrip()}\n{pad}"""' if len(out) > 1 else f'{pad}"""{body.strip()}"""'


def _refs(d):
    """{field: entity name} for an object's fields that are an entity or a list of them."""
    out = {}
    for f, p in (d.get("properties") or {}).items():
        if (r := wire._ref(p)) is not None:
            out[f] = r
        elif isinstance(p.get("items"), dict) and (r := wire._ref(p["items"])) is not None:
            out[f] = r
    return out


def _fields_doc(d, skip=("op", "req")):
    return [f"    {f} ({_kind(p)}): {_one(p.get('description'))}" for f, p in (d.get("properties") or {}).items()
            if f not in skip]


def _class(name, d):
    lines = [_one(d.get("description")) or name]
    fields = _fields_doc(d)
    if fields:
        lines += ["", *fields]
    out = [f"class {name}(Obj):", _doc(lines, 4)]
    if refs := _refs(d):
        out += _packed("_refs = {", [f"{_q(k)}: {_q(v)}" for k, v in refs.items()], "}", 4)
    return out


_WHATS = re.compile(r"(?:\A|; )([a-z_]+(?:, [a-z_]+)*(?:,? and [a-z_]+)?(?: and the actions)?): ")


def _whats_of(desc, whats):
    """The values of what a field's description says it's for ("list, yaml: ...; apply: ..."): None for every one."""
    named = set()
    for m in _WHATS.finditer(desc or ""):
        if m.start() and not (desc or "")[:m.start()].strip():
            continue
        named |= {w for w in re.split(r", | and ", m[1].replace(" and the actions", "")) if w}
        if " and the actions" in m[1]:
            named |= {"start", "stop", "restart", "remove"}
    named &= set(whats)
    return named or None


def _q(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _packed(head, items, tail, indent):
    """head + items joined by ", " + tail, as lines of WIDTH at most: items carried on lines under head's opening."""
    line = " " * indent + head + ", ".join(items) + tail
    if len(line) <= WIDTH:
        return [line]
    out, cur, pad = [], " " * indent + head, " " * (indent + len(head))
    for i, item in enumerate(items):
        piece = item + (", " if i < len(items) - 1 else tail)
        if len(cur) + len(piece.rstrip()) > WIDTH and cur.strip() != head.strip():
            out.append(cur.rstrip())
            cur = pad
        cur += piece
    out.append(cur.rstrip())
    return out


_ANSWERS = re.compile(r"(?:^|; |: )([a-z_]+(?:(?:, | and )[a-z_]+)*) \{([^}]*)\}")


def _answers(reply, what):
    """What a reply has for one value of what ("logs {text}" in its description): its fields, or None."""
    for m in _ANSWERS.finditer(wire.DEFS[reply].get("description") or ""):
        if what in re.split(r", | and ", m[1]):
            return m[2]
    return None


def _params(props, skip):
    return [(f, p) for f, p in props.items() if f not in skip]


def _method(name, params, lines, call, args):
    """A method: its def (its fields keyword arguments, host last), its docstring, and its body (call(op's, {...})):
    one that takes a timeout waited for that long on its answer, and then some (client.waited)."""
    if any(f == "timeout" for f, _p in params):
        args = [*args, "waited(timeout)"]
    sig = _packed(f"def {name}(", ["self", "*", *[f"{_py(f)}=None" for f, _p in params], "host=None"], "):", 4)
    body = _packed(f"return self.{call}(", [args[0], "{" + ", ".join(args[1]) + "}", *args[2:]], ")", 8)
    if len(body) > 1:  # (the fields' dict on lines of its own)
        body = _packed(f"return self.{call}({args[0]}, {{", args[1], f"}}, {', '.join(args[2:])})", 8)
    return [*sig, _doc(lines, 8), *body, ""]


def _what_area(cls, op):
    req = next(n for n in wire.OPS[op] if wire.KIND[n] == "request")
    reply = next((n for n in wire.OPS[op] if wire.KIND[n] == "reply"), None)
    d = wire.DEFS[req]
    whats = d["properties"]["what"]["enum"]
    out = [f"class {cls}(Area):", _doc([_one(d.get("description")), "",
                                        f"Each method is one {op} request (its what), to host: this one if none's "
                                        f"given. Answers {reply or 'Obj'}."], 4), ""]
    for what in whats:
        params = [(f, p) for f, p in _params(d["properties"], ("op", "req", "what"))
                  if (w := _whats_of(p.get("description"), whats)) is None or what in w]
        has = _answers(reply, what) if reply else None
        lines = [f"{op}'s {what}: answers {reply or 'Obj'}" + (f" {{{has}}}" if has else "") + "."]
        if params:
            lines += ["", *[f"    {_py(f)} ({_kind(p)}): {_one(p.get('description'))}" for f, p in params]]
        out += _method(_py(what), params, lines, "_call",
                       [_q(op), [f'"what": {_q(what)}'] + [f"{_q(f)}: {_py(f)}" for f, _p in params], "host",
                        reply or "Obj"])
    return out


def _tool_area(cls, op, module):
    out = [f"class {cls}(Area):", _doc([_one((module.__doc__ or "").split("\n\n")[0]), "",
                                        f"Each method is one of the Bot's own {module.NAME} tools, as its {op} op takes "
                                        f"it, to host: this one if none's given."], 4), ""]
    names = getattr(module, "ARGS", {})
    for tool, (desc, props, required) in module.SCHEMAS.items():
        if tool in SKIP_TOOLS:
            continue
        params = [(names.get(f, f), p) for f, p in props.items() if f not in ("host", "action")]
        lines = [_one(desc) + f" (the {tool} tool)"]
        if params:
            lines += ["", *[f"    {_py(f)} ({_kind(p)}{', required' if f in required else ''}): "
                            f"{_one(p.get('description'))}" for f, p in params]]
        out += _method(TOOL_NAMES.get(tool, tool), params, lines, "_tool",
                       [_q(op), [f'"op": {_q(module.OPS[tool])}'] + [f"{_q(f)}: {_py(f)}" for f, _p in params],
                        "host"])
    return out


def _ops_area():
    out = ["class Ops(Area):", _doc(["Every request a host takes, as the schema has it: one method each, named as its "
                                     "op, its fields its keyword arguments, to host (this one if none's given). What "
                                     "a request answers with more than once (a terminal's output, a log followed) "
                                     "is a connection's own: these give its first answer only."], 4), ""]
    for name in sorted((n for n in wire.MESSAGES if wire.KIND[n] == "request"),
                       key=lambda n: wire.DEFS[n]["properties"]["op"]["const"]):
        d = wire.DEFS[name]
        op = d["properties"]["op"]["const"]
        if op in NOT_OPS:
            continue
        reply = next((n for n in wire.OPS[op] if wire.KIND[n] == "reply"), None)
        params = _params(d["properties"], ("op", "req"))
        lines = [f"{op} ({name}): {_one(d.get('description'))}"]
        if params:
            lines += ["", *_fields_doc(d)]
        out += _method(_py(op), params, lines, "_call",
                       [_q(op), [f"{_q(f)}: {_py(f)}" for f, _p in params], "host", reply or "Obj"])
    return out


def generated():
    """generated.py's text."""
    out = ['"""The task-script SDK\'s generated half: made by baudelib/tasksdk.py from schema/types and the Bot\'s own '
           'tools\'',
           "definitions (baudelib/emissiontools.py, baudelib/multiplextools.py). Don't change it here: change those, "
           "and run",
           '`python3 -m baudelib.tasksdk` again."""',
           "from .client import Area, Obj, waited", "", ""]
    entities = sorted(n for n in wire.DEFS if n not in wire.MESSAGES)
    replies = sorted(n for n in wire.MESSAGES if wire.KIND[n] == "reply")
    for name in entities + replies:
        out += _class(name, wire.DEFS[name]) + ["", ""]
    out += _packed("TYPES = {", [f"{_q(n)}: {n}" for n in entities + replies], "}", 0)
    out += ["Obj._types.update(TYPES)", "", ""]
    out += [f"CLI_DEFAULT = {_q(wording.say('subscriptions.cli_default'))}  "
            "# (a subscription that's its CLI's own sign-in, as every app names it: schema/copy)", "", ""]
    for cls, op in WHAT_AREAS.items():
        out += _what_area(cls, op) + [""]
    for cls, (op, module) in TOOL_AREAS.items():
        out += _tool_area(cls, op, module) + [""]
    out += _ops_area()
    return "\n".join(out).rstrip() + "\n"


# ---- its reference

def _cell(text):
    return _one(text).replace("|", "\\|")


def _table(params, required=()):
    if not params:
        return []
    return ["| Argument | Type | What it is |", "|---|---|---|",
            *[f"| `{_py(f)}` | {_cell(_kind(p))}{' (required)' if f in required else ''} | "
              f"{_cell(p.get('description'))} |" for f, p in params], ""]


def _sig(fn):
    try:
        return str(inspect.signature(fn)).replace("(self, ", "(").replace("(self)", "()")
    except (TypeError, ValueError):
        return "(...)"


INTRO = """# Task scripts: the baudetask SDK

Made by `python3 -m baudelib.tasksdk` from `schema/types` (the wire's requests and answers, the ones Baude, Record and \
the phone use) and the Bot's own tools' definitions (`baudelib/emissiontools.py`, `baudelib/multiplextools.py`). \
Run it again after changing either: it writes this page and `baudelib/script/baudetask_sdk/generated.py`.

A Bot's task is a script its host runs (see [docs/tasks-design.md](../../docs/tasks-design.md)). Its Python scripts \
`import baudetask`, and through it reach whatever the Bot itself can: Emission tabs, Multiplex terminals, \
Kubernetes and Docker objects, databases, every host of the mesh and its models, chats and subagents, beside \
asking the Bot (`ask`, `fallback`), asking a person (`inbox`) and starting agents (`agent`).

## How calls are judged

Every call takes `host=`: any host of the mesh, by its name or id (or a `Host` from `hosts()`); this one if none's \
given. Calls go over the run's own connection to its host, which names the run and its token: the host takes the \
connection for the run's Bot's and applies the access rules to every call as it does to the Bot's own tools (as the \
member that started the Bot, if one did), on this host and, through it, on every other one (where the call is from \
this host). A run can do nothing its Bot couldn't through the SDK. What's refused raises `baudetask.Refused`, saying \
which rule refused it.

Being judged as the run's Bot applies to the SDK's calls; it isn't a sandbox. A script is a process run as the host's \
user: it can read and write what that user can, and open the host's socket without the run's token, as the owner. \
So setting or running a task already needs Shell (see [docs/tasks-design.md](../../docs/tasks-design.md)).

Emission and terminal calls drive the Bot's own browser profile and terminals on that host: never Emission's own \
window, nor another Bot's profile.

## Enumerate, pick, pass back

List calls answer typed objects (a `dict` whose fields are attributes too) that carry where they're from, so a \
script can filter them and pass one back:

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

`inbox()` raises an ask of a person in the Inbox of every app that may manage the task's Bot (Baude's, the phone's), \
drawn as a message is: Markdown, its pictures between paragraphs or alone in a table's cells, its form as setting \
rows, and the buttons the script gives, sized to their labels. Pressing one answers it, with the form's values; the \
ask leaves every Inbox at once. The person may have a model answer it for them to confirm, send it to a Bot, or \
write to the task's Bot, which may answer it or put a new version of it. The host keeps it across restarts; one whose \
run ended unanswered stays, struck through, till it's removed.

```python
import baudetask as task


@task.main
def deploy(inputs):
    asked = task.inbox(
        "### Deploy 2.5 to production?\n\nStaging has run **2.5** since 08:10 with no errors.\n\n![home](home.png)",
        ["Approve", {"label": "Deny", "style": "red"}],
        pictures=["shots/home.png"], form=[{"name": "note", "type": "text", "label": "Note for the release"}],
        title="Deploy site: 2.5 to production?")
    got = asked.wait()                 # {button, label, values, by}: days, if it must (the run waits for input)
    if got["label"] == "Approve":
        task.say("deploying:", got["values"]["note"])
```

`asked.poll()` answers at once (None if nobody's answered yet), `asked.wait(timeout=60)` gives up after a minute \
(None), `asked.revise(body=..., buttons=...)` puts a new version (what's left out stays), `asked.withdraw()` takes it \
back. A withdrawn ask, or one whose run is stopped, raises `baudetask.Unanswered` from `wait` and `poll`. Button \
styles: `primary`, `secondary` (the default), or one of the accent palette's colours `blue`, `teal`, `green`, \
`yellow`, `orange`, `red`, `pink`, `purple`, `slate`; `{"label": ..., "picture": "a.png"}` draws the picture over its \
label.

## Terminals

```python
term = task.terminal.open(command="bash", title="build")
task.terminal.type(terminal=term.terminal, text="make test", enter=True)
done = task.terminal.wait(terminal=term.terminal, pattern=r"passed|failed", timeout=120)
task.say(done.screen)
task.terminal.close(terminal=term.terminal)

out = task.terminal.run(["uname", "-a"], host="lab")   # a command run in a terminal there: {output, status}
```
"""


def docs():
    """site/docs/tasks.md's text."""
    sys.path.insert(0, os.path.join(ROOT, "baudelib", "script"))
    import baudetask_sdk as sdk
    from baudetask_sdk import generated as gen
    out = [INTRO, "## Hosts, models, chats and subagents", ""]
    for name in sdk.TOP:
        fn = getattr(sdk, name)
        out += [f"### `baudetask.{name}{_sig(fn)}`", "", _one(inspect.getdoc(fn)), ""]
    for area, obj in sdk.AREAS.items():
        cls = type(obj)
        base = next((b for b in cls.__mro__ if b.__module__ == gen.__name__ and b.__name__ != "Area"), None)
        out += [f"## `baudetask.{area}`", "", _one((inspect.getdoc(base) or inspect.getdoc(cls) or "")), ""]
        own = [(n, f) for n, f in vars(cls).items() if callable(f) and not n.startswith("_")]
        if own:
            out += ["Besides the generated calls below:", ""]
            for n, f in own:
                out += [f"- `{area}.{n}{_sig(f)}`: {_one(inspect.getdoc(f))}"]
            out.append("")
        if base is None:
            continue
        if base.__name__ in WHAT_AREAS:
            op = WHAT_AREAS[base.__name__]
            d = wire.DEFS[next(n for n in wire.OPS[op] if wire.KIND[n] == "request")]
            whats = d["properties"]["what"]["enum"]
            for what in whats:
                if what in vars(cls) and what not in vars(base):
                    continue
                params = [(f, p) for f, p in _params(d["properties"], ("op", "req", "what"))
                          if (w := _whats_of(p.get("description"), whats)) is None or what in w]
                out += [f"### `{area}.{_py(what)}(...)`", "", f"{op}'s {what}.", "", *_table(params)]
        else:
            op, module = TOOL_AREAS[base.__name__]
            names = getattr(module, "ARGS", {})
            for tool, (desc, props, required) in module.SCHEMAS.items():
                if tool in SKIP_TOOLS:
                    continue
                params = [(names.get(f, f), p) for f, p in props.items() if f not in ("host", "action")]
                out += [f"### `{area}.{TOOL_NAMES.get(tool, tool)}(...)`", "", _cell(desc) + f" (the `{tool}` tool)",
                        "", *_table(params, {names.get(r, r) for r in required})]
    out += ["## `baudetask.ops`", "", _one(inspect.getdoc(gen.Ops)), "",
            "Their fields are the wire's, listed in [mcp.md](mcp.md#the-wire).", "",
            ", ".join(f"`{n}`" for n in sorted(vars(gen.Ops)) if not n.startswith("_")), ""]
    out += ["## Objects", "", "What the calls answer with: each a `dict`, its fields attributes too. Their fields are "
            "the wire's entities' and answers', listed in [mcp.md](mcp.md#entities).", "",
            ", ".join(f"`{n}`" for n in sorted(gen.TYPES)), ""]
    return "\n".join(out).rstrip() + "\n"


def main():
    p = argparse.ArgumentParser(prog="python3 -m baudelib.tasksdk", description=__doc__.split("\n\n")[0])
    p.add_argument("--check", action="store_true", help="exit 1 if either file isn't as it would be written")
    a = p.parse_args()
    code, stale = generated(), []
    for path, want in ((GENERATED, code), (DOCS, None)):
        if want is None:  # (the page documents the client as it's written now)
            want = docs()
        try:
            with open(path, encoding="utf-8") as f:
                have = f.read()
        except OSError:
            have = None
        if have != want:
            if a.check:
                stale.append(os.path.relpath(path, ROOT))
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(want)
                print(f"wrote {os.path.relpath(path, ROOT)}", file=sys.stderr)
    if stale:
        print(f"not as baudelib/tasksdk.py writes them: {', '.join(stale)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
