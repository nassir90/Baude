"""baudetask.vars: a task's variables as its run's script reads them, each bound once for the task (by its Bot, or on
its page), typed by what it's bound to. Each is text (what it's bound to: a str, passed anywhere a name or a value
goes), with what else its type has as attributes, and warning: why it isn't there now as the run started (a model not
served, a host out of reach, a pod gone), else None. Nothing's put in its place: the script decides what to do.

    import baudetask as task

    model = task.vars.translation_model      # or task.vars["Translation Model"]; None if it's not bound
    if model is None or model.warning:
        model = task.vars.worker_agent_model
    task.agent("Translate this: ...", model=model)   # its value, and its effort
    pod = task.vars["Pod"]                   # Pod: its name, .context, .namespace

Outside a task (no BAUDE_TASK_VARS), there are none."""
import json
import os
import re


class Bound(str):
    """What a variable's bound to: its value, as text; type, warning, label, and what its type has."""
    type = "text"
    FIELDS = ()

    def __new__(cls, binding, kind=None, warning=None):
        self = super().__new__(cls, binding.get("value") or "")
        self.type = kind or cls.type
        self.label = binding.get("label") or str(self)
        self.warning = warning
        for f in cls.FIELDS:
            setattr(self, f, binding.get(f))
        return self

    def __repr__(self):
        more = "".join(f", {f}={getattr(self, f)!r}" for f in self.FIELDS if getattr(self, f) is not None)
        return f"{type(self).__name__}({str(self)!r}{more}" + (f", warning={self.warning!r})" if self.warning else ")")


class Model(Bound):
    """A model: its value (what agent(model=) and decide(model=) take), effort, host (the member of the mesh that
    serves it, if not this one)."""
    type, FIELDS = "model", ("effort", "host")


class Decider(Bound):
    """A decision model's endpoint: its URL, and model (what it's asked for there)."""
    type, FIELDS = "decider", ("model",)


class Host(Bound):
    """A host of the mesh (to browse on, to run terminals on): its id or name."""
    type = "host"


class KubeContext(Bound):
    type = "kube_context"


class Pod(Bound):
    type, FIELDS = "pod", ("context", "namespace")


class Container(Bound):
    type = "container"


class Database(Bound):
    type, FIELDS = "database", ("container",)


class Text(Bound):
    type = "text"


KINDS = {"model": Model, "decider": Decider, "host": Host, "browser_host": Host, "terminal_host": Host,
         "kube_context": KubeContext, "pod": Pod, "container": Container, "database": Database, "text": Text}


def key(name):
    """A variable's name as an attribute: "Translation Model" is translation_model."""
    return re.sub(r"\W+", "_", name.strip().lower()).strip("_") or "_"


class Vars:
    """The task's variables: vars["Translation Model"] or vars.translation_model (None: not bound; KeyError,
    AttributeError: the task has none of that name); iterated, their names; warnings: {name: why} for those that
    aren't there now."""

    def __init__(self, given):
        self._all, self._keys = {}, {}
        for name, v in (given or {}).items():
            if not isinstance(v, dict):
                continue
            b = v.get("binding")
            self._all[name] = KINDS.get(v.get("type"), Text)(b, v.get("type"), v.get("warning")) \
                if isinstance(b, dict) and b.get("value") else None
            self._keys[key(name)] = name

    def __getitem__(self, name):
        if name in self._all:
            return self._all[name]
        if name in self._keys:
            return self._all[self._keys[name]]
        raise KeyError(name)

    def __getattr__(self, attr):
        if attr.startswith("_"):
            raise AttributeError(attr)
        try:
            return self[attr]
        except KeyError:
            raise AttributeError(f"the task has no variable {attr!r}") from None

    def __contains__(self, name):
        return name in self._all or name in self._keys

    def __iter__(self):
        return iter(self._all)

    def __len__(self):
        return len(self._all)

    def get(self, name, default=None):
        try:
            got = self[name]
        except KeyError:
            return default
        return default if got is None else got

    @property
    def warnings(self):
        return {n: b.warning for n, b in self._all.items() if b is not None and b.warning}

    def __repr__(self):
        return f"Vars({self._all!r})"


def _read():
    try:
        got = json.loads(os.environ.get("BAUDE_TASK_VARS") or "{}")
    except ValueError:
        got = {}
    return Vars(got if isinstance(got, dict) else {})


vars = _read()  # noqa: A001 (baudetask.vars, as scripts read it)
