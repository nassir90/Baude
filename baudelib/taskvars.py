"""A task's variables (docs/tasks-design.md): names a task declares, each with a type (a model and its effort, a
decider, a host of the mesh, a Kubernetes context, pod, container or database, or text), bound once
for the task, by its Bot (set_task) or a person (its page), unlike its form's inputs, which each run fills. What's
offered as one's bound is what this host has of that type, from the lists ⌘K lists (its models, the mesh's hosts, its
kubeconfig's contexts, the workloads in its local clusters, its Docker containers, the databases running here, its deciders); any
text is taken all the same, for a flaky one. One bound to what isn't there now (a model not served, a host out of
reach, a pod gone) is warned of: on the task's page, and in a run's log as it starts. The run starts all the same, its
script told each one's warning (baudetask.vars) and left to decide what to do: nothing's put in its place. No GTK."""
import os
import re
import sys
import threading
import time

from baudelib import wire, wording

TYPES = tuple(wire.DEFS["TaskVariable"]["properties"]["type"]["enum"])
FIELDS = tuple(k for k in wire.DEFS["TaskBinding"]["properties"])
VARIABLES_MAX = 30
NAME_MAX, DESCRIPTION_MAX, VALUE_MAX = 60, 300, 500
LISTS_S = 15  # (what's here of each type, looked at again after this long, at most: its choices and warnings)
CHECK_S = 60  # (every bound variable of every task looked at again this often, for its warning)
CHOICES_MAX = 400
HOSTS = ("host", "browser_host", "terminal_host")
KUBE = ("kube_context", "pod")
_NAME = re.compile(r"[^\x00-\x1f\x7f]{1,%d}" % NAME_MAX)


def key(name):
    """A variable's name as a script reads it as an attribute: "Translation Model" is translation_model."""
    return re.sub(r"\W+", "_", name.strip().lower()).strip("_") or "_"


def clean_variable(v):
    if not isinstance(v, dict) or not isinstance(v.get("name"), str) or not _NAME.fullmatch(" ".join(v["name"].split())):
        raise ValueError(wording.say("tasks.variable_needs_name"))
    out = {"name": " ".join(v["name"].split()), "type": v.get("type") if v.get("type") in TYPES else "text"}
    if isinstance(v.get("description"), str) and v["description"].strip():
        out["description"] = " ".join(v["description"].split())[:DESCRIPTION_MAX]
    return out


def clean_variables(given):
    out = [clean_variable(v) for v in (given or [])[:VARIABLES_MAX]]
    if len({key(v["name"]) for v in out}) != len(out):
        raise ValueError(wording.say("tasks.variables_one_name"))
    return out


def clean_binding(b):
    """A binding as it's given (TaskBinding, or just its value as text) as it's kept, or None: nothing bound."""
    if isinstance(b, str):
        b = {"value": b}
    if not isinstance(b, dict) or not isinstance(b.get("value"), str) or not b["value"].strip():
        return None
    return {k: b[k].strip()[:VALUE_MAX] for k in FIELDS if isinstance(b.get(k), str) and b[k].strip()}


def clean_bindings(given, variables):
    """{name: binding} for a task's variables (those named, that are bound), as given."""
    names = {v["name"] for v in variables}
    out = {}
    for name, b in (given or {}).items() if isinstance(given, dict) else ():
        if name in names and (b := clean_binding(b)) is not None:
            out[name] = b
    return out


class Lists:
    """What this host has of each type, each looked at once (as it's first asked for: kubectl and docker take a
    while) and kept for LISTS_S, for the choices offered and the warnings: one at a time per type."""

    def __init__(self, server):
        self.server, self.kept, self.lock, self.locks = server, {}, threading.Lock(), {}

    def get(self, kind):
        with self.lock:
            lock = self.locks.setdefault(kind, threading.Lock())
        with lock:
            at, got = self.kept.get(kind, (None, None))
            if at is None or time.monotonic() - at > LISTS_S:
                try:
                    got = getattr(self, f"_{kind}")()
                except Exception as e:  # noqa: BLE001 (kubectl or docker gone, or not there: nothing of it here)
                    got = {"error": f"{type(e).__name__}: {e}"}
                self.kept[kind] = (time.monotonic(), got)
            return got

    @staticmethod
    def _lookup():
        root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        import lookupkube
        return lookupkube

    def _models(self):
        """[{value, label, detail, efforts}]: what this host runs (as its hello lists them), then other hosts' run here."""
        from baudelib import providers
        listed = self.server.models()
        out, seen = [], set()
        for m in listed + [m for m in providers.mesh_agents()]:
            v = m.get("value")
            if not isinstance(v, str) or v in seen:
                continue
            seen.add(v)
            levels = m.get("supportedEffortLevels")
            out.append({"value": v, "label": m.get("displayName") or providers.model_name(v, listed),
                        "detail": m.get("providerName") or m.get("host") or None, "host": m.get("host") or None,
                        "efforts": [x for x in levels if isinstance(x, str)] if isinstance(levels, list) else None})
        return {"items": out}

    def _hosts(self):
        """[{value (id), label (name), detail, gone}]: this host, the mesh's members, and those reached lately but
        not now."""
        view = self.server.mesh.view()
        me = self.server.me
        out = [{"value": me["id"], "label": me["name"], "detail": wording.say("tasks.this_host")}]
        out += [{"value": m["id"], "label": m.get("name") or m["id"]} for m in view.get("members") or []
                if m.get("id") and m["id"] not in view.get("inactive", ())]
        out += [{"value": m["id"], "label": m.get("name") or m["id"], "gone": True} for m in view.get("gone") or []]
        return {"items": out}

    def _contexts(self):
        return {"items": [{"value": c["name"], "detail": c.get("server") or None}
                          for c in self._lookup().kube_contexts() if c.get("name")]}

    def _pods(self):
        """The workloads running in this host's local clusters (kube_running: ⌘K's), pods first."""
        running = self._lookup().kube_running(None)
        pods = sorted(running, key=lambda r: (r.get("kind") != "Pod", r.get("name") or ""))
        return {"items": [{"value": r["name"], "context": r.get("context"), "namespace": r.get("namespace"),
                           "detail": f"{r.get('kind')} · {r.get('context')} · {r.get('namespace')}"}
                          for r in pods if r.get("name")]}

    def _containers(self):
        """The containers Docker runs here (its current context's)."""
        from baudelib import hostdocker
        ctx = next((c for c in hostdocker.contexts() if c.get("current")), None)
        args = ctx["args"] if ctx else []
        out = []
        for line in hostdocker._run([*args, "ps", "--format", "{{.Names}}\t{{.Image}}"], timeout=10).splitlines():
            name, _, image = line.partition("\t")
            if name.strip():
                out.append({"value": name.split(",")[0].strip(), "detail": image.strip() or None})
        return {"items": out}

    def _databases(self):
        """The databases and their tables running here (db_names: ⌘K's)."""
        out = []
        for name, places in self._lookup().db_names().items():
            for container, db, table in places[:4]:
                out.append({"value": name, "container": container,
                            "detail": f"{db} ({container})" if table else container})
        return {"items": out}

    def _deciders(self):
        """The deciders this host knows (baudelib/deciders.py, as its deciders op lists them): its own, then the mesh's
        members', each by its value (what decide's model takes) and whether it answered when last looked at."""
        srv = self.server
        out = [{"value": d["value"], "label": d.get("name") or d["value"], "detail": d.get("model") or None,
                "gone": not d.get("reachable")} for d in srv.deciders.listing()]
        for _member, name, decs in srv.mesh.member_deciders():
            out += [{"value": f"{name}/{d['id']}", "label": f"{name}/{d.get('name') or d['id']}",
                     "detail": d.get("model") or name, "gone": not d.get("reachable")} for d in decs]
        return {"items": out}

LIST_OF = {"model": "models", "decider": "deciders", "host": "hosts", "browser_host": "hosts",
           "terminal_host": "hosts", "kube_context": "contexts", "pod": "pods", "container": "containers",
           "database": "databases"}
NEEDS = {"kube_context": "kube_read", "pod": "kube_read", "container": "docker_read", "database": "databases"}
# (what ⌘K's catalogue holds back of them, too, from one who may not read them)


def choices(lists, kind):
    """What a variable of kind could be bound to here: [TaskChoice], best first."""
    if kind not in LIST_OF:
        return []
    out = []
    for it in lists.get(LIST_OF[kind]).get("items") or []:
        b = {k: it[k] for k in FIELDS if isinstance(it.get(k), str) and it[k]}
        if kind in HOSTS and it.get("label"):  # (a host's kept by its name, as a person types it)
            b = {"value": it["label"], "label": it["label"]}
        detail = wording.say("tasks.unreachable" if kind == "decider" else "tasks.out_of_reach") \
            if it.get("gone") else it.get("detail")
        out.append({"binding": b, **({"detail": detail} if detail else {}),
                    **({"efforts": it["efforts"]} if it.get("efforts") else {})})
    return out[:CHOICES_MAX]


def warning(server, lists, kind, b):
    """Why what a variable of kind is bound to (b) isn't there now, or None (it is, or it's text, or what it's of
    can't be looked at here: nothing to say)."""
    if not b or kind == "text":
        return None
    value = b["value"]
    if kind == "model":
        try:
            ok = server.can_run(value)
        except Exception:  # noqa: BLE001 (a provider's look gone wrong: not said to be missing)
            ok = True
        return None if ok else wording.say("tasks.model_missing")
    got = lists.get(LIST_OF[kind])
    if got.get("error") is not None:
        return None
    items = got.get("items") or []
    if kind == "decider":  # (one of the host's deciders, by its value, that answered when last looked at)
        hit = next((it for it in items if it["value"] == value), None)
        return wording.say("tasks.no_decider") if hit is None else wording.say("tasks.unreachable") \
            if hit.get("gone") else None
    if kind in HOSTS:
        hit = next((it for it in items if value in (it["value"], it.get("label"))), None)
        return wording.say("tasks.no_host") if hit is None else wording.say("tasks.out_of_reach") \
            if hit.get("gone") else None
    if kind == "pod":
        look = lists._lookup()
        if b.get("context") and not any(c.get("name") == b["context"] and look.kube_local(c.get("server"))
                                        for c in look.kube_contexts()):
            return None  # (a remote cluster's pods aren't looked at here: nothing to say)
        hit = any(it["value"] == value and (not b.get("context") or it.get("context") == b["context"]) and
                  (not b.get("namespace") or it.get("namespace") == b["namespace"]) for it in items)
        return None if hit else wording.say("tasks.not_running")
    if kind == "container":
        return None if any(it["value"] == value for it in items) else wording.say("tasks.not_running")
    if kind == "kube_context":
        return None if any(it["value"] == value for it in items) else wording.say("tasks.no_context")
    if kind == "database":
        return None if any(it["value"] == value and (not b.get("container") or it.get("container") == b["container"])
                           for it in items) else wording.say("tasks.no_database")
    return None


def warnings(server, lists, task):
    """{name: warning} for task's bound variables that aren't there now."""
    out = {}
    for v in task.get("variables") or []:
        b = (task.get("bindings") or {}).get(v["name"])
        if b and (w := warning(server, lists, v["type"], b)):
            out[v["name"]] = w
    return out


def for_script(task, said):
    """What a run's script reads its variables from (BAUDE_TASK_VARS): {name: {type, binding, warning}}."""
    return {v["name"]: {"type": v["type"], "binding": (task.get("bindings") or {}).get(v["name"]),
                        **({"warning": said[v["name"]]} if v["name"] in said else {})}
            for v in task.get("variables") or []}

