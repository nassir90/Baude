"""What Baude and a server say to each other: one JSON object a line (UTF-8, ending in a newline), each with an "op".
The same lines could go over stdin and stdout: Channel takes any pair of binary files, a socket's or a pipe's.

What may be said is schema/'s (JSON Schema, draft 2020-12), read as this is imported. schema/types has a file for each
message (its op, its fields, what each is) and each entity in them (a session, a host's info, the mesh's records), named
for it; a message's name says what it is too: Hello.request.json (a request, which a host takes, its req coming back on
the answer), HelloReply.reply.json, Sessions.event.json (a host's, unasked), Open.frame.json (on a mesh link). One names
another by its file's name ({"$ref": "Session.json"}). schema/variables has a file for each of the protocol's constants,
its version (VERSION, said in hello for a person to read) among them. The Android app's build makes Kotlin of the same
files. Its reference, as this module's help shows it below, is made from them (docs()); defs has a class for each of its
messages and entities (Hello, Session, ...): what one's built with that the schema doesn't have is never sent. Python's
own senders write dicts, as before: every message sent goes through the schema all the same (Channel.send). A field
given as None that its op doesn't let be null is left out; one with an op or a field the schema doesn't have isn't sent
(a request of this end's own is refused here, as if the other end had; an answer goes as that refusal; a listing or a
turn's end is only said on stderr); a message passed on (a tunnel's) goes as it came. A request a host takes is looked
at too (refusal): an op it doesn't take is answered "unsupported operation", fields that aren't as the schema has them
"invalid data type", each an error with a code of its own (UNSUPPORTED, INVALID) and what was wrong (detail), the same
from every host, the phone's too, and what a head shows. $BAUDE_WIRE_STRICT=1 checks the types of what's sent and read
as well, and says on stderr what doesn't fit.

A head's requests carry a "req" it picks, and the answer carries it back. Anything wrong is an error, with the
request's req. A session's worktree that's gone is made again from its branch as a message or a revert is sent to it;
those watching it are told so by its claude's message {type: "_note", text}. A message a session is sent is told the
others attached to it live as {type: "_said", uuid, message, sender?} as it's given its agent and kept (one sent at a
turn Claude Code doesn't take into it, once that turn's over): its claude never says one back. sender: who it's from,
as its sender store has it (shown too), if not its user.

Senders. A message passed to a session from another than its user (input's as, a held message sent, a
subagent's task) has a uuid its program keeps: Claude Code's as its transcript record's, pi's on its session entry
(pi-claude's /baude-uuid), the host's own transcript's for the others (Server.keep). Its host keeps who it's from,
by that uuid, beside the session (its state folder's senders/<session>.json), and heads draw a message as from
another only if it's there, never from its text: the tag said_by puts at the head of its text is for its agent.
The store goes as its session's transcript goes: copied to a fork, trashed with it, and what a revert drops (its
message and those after it) gone from it too.

A session listed says where its transcript is on its host's machine ("transcript": a path, or None), to every head:
one on the same machine reads it there rather than fetching a copy.

Where a server is: a host process (baude_headless.py) listens on a Unix socket only (socket_path(), in a folder that's
its user's alone: that folder is all the say there is in who may use it), with no port on the network. What's on the
same machine connects to the socket (connect_local); another machine reaches it over ssh (connect_ssh), as
`ssh <host> ~/.local/bin/baude-headless connect` (HOST_COMMAND, the command Baude installs on every machine:
baudelib/bins.py), which carries these lines between ssh's stdin and stdout and the socket (bridge), starting the host
process if it isn't running. So ssh says who may, and keeps what's said private: the keys in
authorized_keys and known_hosts, never a password asked for. A client that isn't Python asks `baude-headless
socket` where this user's socket is, rather than copying runtime_dir's rules: $BAUDE_RUNTIME_DIR, else
$XDG_RUNTIME_DIR/baude (which a session over ssh and one at the screen may have differently, or lose as a session
ends), else $XDG_STATE_HOME/baude/run (~/.local/state/baude/run), or /tmp/baude-<uid> where that's too long a path for
a socket; never $TMPDIR, which macOS gives ssh's sessions another of. Or it runs `baude-headless connect` and talks
to its stdin and stdout. reach(where)
connects to any of them:
    {"via": "local"}                                this machine's host process (socket_path())
    {"via": "unix", "path": P}                      one listening on another socket
    {"via": "ssh", "dest": D, "strict"?}            ssh D (an alias in ~/.ssh/config, a host, user@host), its
                                                    HOST_COMMAND; strict: its key must be known already
    {"via": "mesh", "host": ID, "path"?}            member ID of the mesh this machine's host process is in (or the
                                                    one listening at path), through it: relay
"""
import collections
import dataclasses
import json
import keyword
import os
import re
import select
import socket
import stat
import subprocess
import sys
import threading
import time

from baudelib import wording

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "schema")
STRICT = os.environ.get("BAUDE_WIRE_STRICT") == "1"
KINDS = ("request", "reply", "event", "frame")  # (what a message is, as its file's name says: a request a host takes)
_FILE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\.([a-z]+))?\.json")


class SchemaError(Exception):
    """A file in schema/types or schema/variables isn't as it must be, said with its name: JSON that can't be read, a
    key twice in one object, a $ref to no type, say."""


def _pairs(kv):
    """An object as json reads it: ValueError for a key that's there twice (not the last one taken)."""
    obj = {}
    for k, v in kv:
        if k in obj:
            raise ValueError(f"{k!r} is there twice in one object")
        obj[k] = v
    return obj


def _load(folder):
    """Each JSON file in schema/<folder>, by its name: {name: (its kind, as its file's name says it, or None; its JSON;
    its path)}. Others (a README, an editor's backup) are passed over; a folder in it isn't."""
    out, root = {}, os.path.join(SCHEMA_DIR, folder)
    for f in sorted(os.listdir(root)):
        path = f"schema/{folder}/{f}"
        if f.startswith("."):
            continue
        if os.path.isdir(os.path.join(root, f)):
            raise SchemaError(f"{path}: a folder, not a JSON file")
        if not f.endswith(".json"):
            continue
        if (m := _FILE.fullmatch(f)) is None:
            raise SchemaError(f"{path}: not named Name.json or Name.kind.json")
        if m[1] in out:
            raise SchemaError(f"{path}: {out[m[1]][2]} is {m[1]} too")
        try:
            with open(os.path.join(root, f), "rb") as fh:
                out[m[1]] = (m[2], json.load(fh, object_pairs_hook=_pairs), path)
        except ValueError as e:
            raise SchemaError(f"{path}: {e}") from None
    return out


def _refs(schema):
    """Each $ref in a schema, wherever it is in it."""
    if isinstance(schema, dict):
        if "$ref" in schema:
            yield schema["$ref"]
        for v in schema.values():
            yield from _refs(v)
    elif isinstance(schema, list):
        for v in schema:
            yield from _refs(v)


def _check(types, variables):
    """SchemaError, naming the file, for a type that isn't an object with an object for each of its properties, a kind
    that isn't one of KINDS, a message without an op const, an entity with an op, a $ref to no type, or a variable
    whose const isn't a number or a string."""
    for kind, d, path in types.values():
        props = d.get("properties", {}) if isinstance(d, dict) else None
        if not isinstance(props, dict) or not all(isinstance(p, dict) for p in props.values()):
            raise SchemaError(f"{path}: not a type (an object, each of its properties an object)")
        if kind is not None and kind not in KINDS:
            raise SchemaError(f"{path}: {kind} isn't one of {', '.join(KINDS)}")
        if kind is not None and not isinstance(props.get("op", {}).get("const"), str):
            raise SchemaError(f"{path}: a message with no op const")
        if kind is None and "op" in props:
            raise SchemaError(f"{path}: an op, but no kind in its name (Name.request.json, ...)")
        for ref in _refs(d):
            if not isinstance(ref, str) or not ref.endswith(".json") or _name(ref) not in types:
                raise SchemaError(f"{path}: no type {ref}")
    for kind, v, path in variables.values():
        const = v.get("const") if isinstance(v, dict) else None
        if kind is not None or not isinstance(const, (int, float, str)) or isinstance(const, bool):
            raise SchemaError(f"{path}: not a variable (Name.json, an object whose const is a number or a string)")


def _name(ref):
    """The type a $ref names (its file's name: Session.json)."""
    return ref.removesuffix(".json")


class _Constants(dict):
    """The variables' consts, by name: SchemaError, naming its file, for one that isn't there."""

    def __missing__(self, name):
        raise SchemaError(f"schema/variables/{name}.json: not there")


_types, _variables = _load("types"), _load("variables")
_check(_types, _variables)
DEFS = {name: d for name, (_kind, d, _path) in _types.items()}  # (each message and entity, by its name)
KIND = {name: kind for name, (kind, _d, _path) in _types.items() if kind}  # (each message's)
MESSAGES = tuple(KIND)
OPS = {}  # (an op: its messages, a request's and its answer's, say)
for _n in MESSAGES:
    OPS.setdefault(DEFS[_n]["properties"]["op"]["const"], []).append(_n)
VARIABLES = {name: v for name, (_kind, v, _path) in _variables.items()}  # (each constant: its const, what it is)
CONSTANTS = _Constants({name: v["const"] for name, v in VARIABLES.items()})
del _types, _variables, _n


def _ref(schema):
    """The type a field's schema is, if it's one (or one or null): its name."""
    if "$ref" in schema:
        return _name(schema["$ref"])
    for s in schema.get("anyOf") or ():
        if "$ref" in s:
            return _name(s["$ref"])
    return None


VERSION = CONSTANTS["VERSION"]  # (said in hello, for a person to read)
HEAD_COMMIT = None  # (the commit a head's code is, as it started: said in its hello, set by Baude and Record)
HEAD_CLIENT = "tool"  # (what a head is, said in its hello: Baude, Record say what they are; a script is a tool)
STATES = tuple(DEFS["Session"]["properties"]["state"]["enum"])
PING_S = CONSTANTS["PING_S"]      # a subscriber hears from the server at least this often
SILENT_S = CONSTANTS["SILENT_S"]  # and takes it for gone after this long without a word
OPEN_S = CONSTANTS["OPEN_S"]      # (a member of the mesh, reached through it: the tunnel's this long to open)
UNSUPPORTED = "unsupported_operation"  # (the schema's own refusals: their codes, and what each says)
INVALID = "invalid_data_type"
SAID = {UNSUPPORTED: "unsupported operation", INVALID: "invalid data type"}


def takes(info, op):
    """Does a host take op, as its hello says: no only for one that lists the ops it takes (a phone's) without it."""
    ops = (info or {}).get("ops")
    return not isinstance(ops, list) or op in ops


# ---- the classes
_UNSET = type("Unset", (), {"__repr__": lambda self: "UNSET", "__bool__": lambda self: False})()
UNSET = _UNSET  # (a field a class was built without: not sent)


def py_name(field):
    """A field's name in Python: as it is, or with _ after it if it's a keyword (as, from)."""
    return field + "_" if keyword.iskeyword(field) else field


def _to_wire(value):
    if isinstance(value, Def):
        return value.to_wire()
    if isinstance(value, list):
        return [_to_wire(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_wire(v) for k, v in value.items()}
    return value


class Def:
    """What each of defs' classes is: built with its fields (by keyword, Python's names: as_ for as), those left out
    not sent."""
    _def = None
    _fields = ()  # ((the wire's name, Python's), ...)

    def to_wire(self):
        """The message or entity as it goes: the schema's fields only, those that were given."""
        out = {"op": self._op} if self._op else {}
        for wire_name, name in self._fields:
            if (v := getattr(self, name)) is not _UNSET:
                out[wire_name] = _to_wire(v)
        return out

    @classmethod
    def from_wire(cls, obj):
        """One as it came (a dict): the fields the schema has, entities in them made objects too; others left out."""
        kw = {}
        for wire_name, name in cls._fields:
            if wire_name in obj:
                kw[name] = _from_wire(cls._def["properties"][wire_name], obj[wire_name])
        return cls(**kw)


def _from_wire(schema, value):
    if value is None:
        return None
    ref = _ref(schema)
    if ref is not None and isinstance(value, dict) and DEFS[ref].get("additionalProperties") is False:
        return getattr(defs, ref).from_wire(value)
    if schema.get("type") == "array" and isinstance(value, list) and "items" in schema:
        return [_from_wire(schema["items"], v) for v in value]
    return value


def _make_class(name, d):
    fields = [(f, py_name(f)) for f in d["properties"] if f != "op"]
    op = d["properties"]["op"]["const"] if name in MESSAGES else None
    cls = dataclasses.make_dataclass(name, [(py, object, dataclasses.field(default=_UNSET)) for _f, py in fields],
                                     bases=(Def,), namespace={"__doc__": d.get("description")})
    cls._def, cls._fields, cls._op = d, tuple(fields), op
    return cls


class _Defs:
    """Each def's class (defs.Session), made the first time it's asked for: all of them at once would add some 25 ms
    to every import."""

    _lock = threading.Lock()

    def __getattr__(self, name):
        if name not in DEFS:
            raise AttributeError(f"no def {name} in the schema")
        with self._lock:  # (two threads asking at once: one class, made once)
            if name not in self.__dict__:
                setattr(self, name, _make_class(name, DEFS[name]))
            return self.__dict__[name]

    def __dir__(self):
        return list(DEFS)


defs = _Defs()


# ---- what's sent, checked
def _names_checker(schema, where):
    """A function that says what of a value isn't the schema's (a field it doesn't have, in an entity of its own,
    in a list, a map or itself), or None for one with nothing to look at (an open object, a plain value)."""
    ref = _ref(schema)
    if ref is not None:
        d = DEFS[ref]
        if d.get("additionalProperties") is not False:
            return None
        return _entity_checker(ref, d)
    if schema.get("type") == "array" and "items" in schema or \
            isinstance(schema.get("type"), list) and "array" in schema["type"] and "items" in schema:
        inner = _names_checker(schema["items"], where)
        if inner is None:
            return None

        def items(value):
            if isinstance(value, list):
                for i, v in enumerate(value):
                    if (bad := inner(v)) is not None:
                        return f"[{i}]{bad}"
            return None
        return items
    if isinstance(schema.get("additionalProperties"), dict):
        inner = _names_checker(schema["additionalProperties"], where)
        if inner is None:
            return None

        def values(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    if (bad := inner(v)) is not None:
                        return f"[{k!r}]{bad}"
            return None
        return values
    return None


_entities = {}


def _entity_checker(name, d):
    if name in _entities:
        return _entities[name]
    allowed = frozenset(d["properties"])
    nested = []

    def check(value):
        if not isinstance(value, dict):
            return None
        if not value.keys() <= allowed:
            return f".{sorted(set(value) - allowed)[0]}: {name} has no such field"
        for f, c in nested:
            if (v := value.get(f)) is not None and (bad := c(v)) is not None:
                return f".{f}{bad}"
        return None
    _entities[name] = check
    for f, p in d["properties"].items():
        if (c := _names_checker(p, f)) is not None:
            nested.append((f, c))
    return check


def _op_checker(op, names):
    """What of a line with this op isn't the schema's: its fields those of any of its lines, each as that line has
    it (a field two of them have otherwise than each other isn't looked into)."""
    allowed = frozenset(f for n in names for f in DEFS[n]["properties"])
    nested = []
    for f in allowed:
        said = {json.dumps(DEFS[n]["properties"][f], sort_keys=True) for n in names if f in DEFS[n]["properties"]}
        if len(said) == 1 and (c := _names_checker(json.loads(said.pop()), f)) is not None:
            nested.append((f, c))
    nested = tuple(nested)

    def check(msg):
        if not msg.keys() <= allowed:
            return f"{op}.{sorted(set(msg) - allowed)[0]}: {op} has no such field"
        for f, c in nested:
            if (v := msg.get(f)) is not None and (bad := c(v)) is not None:
                return f"{op}.{f}{bad}"
        return None
    return check


_CHECKS = {op: _op_checker(op, names) for op, names in OPS.items()}
_said = set()
SAID_MOST = 1000


def check(msg):
    """What of a message isn't the schema's (its op, a field), or None: what send refuses."""
    if not isinstance(msg, dict):
        return "not an object"
    c = _CHECKS.get(msg.get("op"))
    if c is None:
        return f"no op {msg.get('op')!r} in the schema"
    return c(msg)


def problems(msg, kinds=None):
    """Everything about a message that isn't as the schema has it, types too ($BAUDE_WIRE_STRICT's): [what, ...];
    kinds: as one of its op's messages of those kinds only (KINDS: request, ...)."""
    if not isinstance(msg, dict) or msg.get("op") not in OPS:
        return [check(msg) or "not a message"]
    best = None
    for name in OPS[msg["op"]]:
        if kinds is not None and KIND[name] not in kinds:
            continue
        found = []
        _validate(DEFS[name], msg, msg["op"], found)
        if not found:
            return []
        if best is None or len(found) < len(best):
            best = found
    return best or []


TAKEN = frozenset(DEFS[n]["properties"]["op"]["const"] for n in MESSAGES if KIND[n] == "request")  # (a host's ops)


def refused(code, req=None, detail=None):
    """One of the schema's own refusals (UNSUPPORTED, INVALID), for request req: an error, the same from every host,
    and what was wrong (detail)."""
    return {"op": "error", "req": req, "error": SAID[code], "code": code, **({"detail": detail} if detail else {})}


def said(reply):
    """An error reply as a person reads it: its error, and its detail if it has one."""
    reply = reply or {}
    return ": ".join(str(x) for x in (reply.get("error"), reply.get("detail")) if x)


def _nullable(p):
    t = p.get("type")
    return "type" not in p and "anyOf" not in p and "$ref" not in p and "const" not in p and "enum" not in p or \
        isinstance(t, list) and "null" in t or any(x.get("type") == "null" for x in p.get("anyOf") or ())


NULLABLE = {op: frozenset(f for n in names for f, p in DEFS[n]["properties"].items() if _nullable(p))
            for op, names in OPS.items()}  # (an op's fields that may be null, in any of its lines)


def _cut(schema, value):
    """value with what the schema hasn't left out of it: its closed entities' fields, in lists and maps too."""
    if value is None:
        return value
    ref = _ref(schema)
    if ref is not None:
        d = DEFS[ref]
        if d.get("additionalProperties") is not False or not isinstance(value, dict):
            return value
        return {k: _cut(d["properties"][k], v) for k, v in value.items() if k in d["properties"]}
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [_cut(schema["items"], v) for v in value]
    if isinstance(value, dict) and isinstance(schema.get("additionalProperties"), dict):
        return {k: _cut(schema["additionalProperties"], v) for k, v in value.items()}
    return value


def cut(msg):
    """A message with what the schema hasn't left out (a field its op's lines don't have, or one of a closed
    entity's in it): what a host's broadcast goes as, rather than not at all."""
    names = OPS.get(msg.get("op")) if isinstance(msg, dict) else None
    if not names:
        return msg
    out = {}
    for k, v in msg.items():
        said = [DEFS[n]["properties"][k] for n in names if k in DEFS[n]["properties"]]
        if said:
            out[k] = _cut(said[0], v) if all(s == said[0] for s in said) else v
    return out


def _unnuller(schema):
    """A function that leaves out of a value the fields given as None that can't be null, in the closed entities
    in it (in lists and maps too), or None if there are none to look at."""
    ref = _ref(schema)
    if ref is not None:
        return _entity_unnuller(ref) if DEFS[ref].get("additionalProperties") is False else None
    if isinstance(schema.get("items"), dict) and (inner := _unnuller(schema["items"])) is not None:
        def items(v):
            if not isinstance(v, list):
                return v
            out = None  # (the same list, if nothing in it changed)
            for i, x in enumerate(v):
                if (y := inner(x)) is not x:
                    out = out or list(v)
                    out[i] = y
            return v if out is None else out
        return items
    if isinstance(schema.get("additionalProperties"), dict) and \
            (inner := _unnuller(schema["additionalProperties"])) is not None:
        def values(v):
            if not isinstance(v, dict):
                return v
            out = {k: inner(x) for k, x in v.items()}
            return v if all(out[k] is x for k, x in v.items()) else out
        return values
    return None


_unnullers = {}


def _entity_unnuller(name):
    if name in _unnullers:
        return _unnullers[name]
    props = DEFS[name]["properties"]
    keep = frozenset(f for f, p in props.items() if _nullable(p))
    strict = tuple(f for f in props if f not in keep)  # (those that can't be null)
    inner = []

    def unnull(v):
        if not isinstance(v, dict):
            return v
        if None in v.values() and any(v.get(k, 0) is None for k in strict):
            v = {k: x for k, x in v.items() if x is not None or k in keep}
        for f, u in inner:
            if (x := v.get(f)) is not None and (y := u(x)) is not x:
                v = {**v, f: y}
        return v
    _unnullers[name] = unnull
    inner += [(f, u) for f, p in props.items() if (u := _unnuller(p)) is not None]
    return unnull


def _op_unnuller(op, names):
    keep = NULLABLE[op]
    inner = []
    for f in {f for n in names for f in DEFS[n]["properties"]}:
        said = {json.dumps(DEFS[n]["properties"][f], sort_keys=True) for n in names if f in DEFS[n]["properties"]}
        if len(said) == 1 and (u := _unnuller(json.loads(said.pop()))) is not None:
            inner.append((f, u))

    def unnull(msg):
        if None in msg.values():
            msg = {k: v for k, v in msg.items() if v is not None or k in keep}
        for f, u in inner:
            if (x := msg.get(f)) is not None and (y := u(x)) is not x:
                msg = {**msg, f: y}
        return msg
    return unnull


_UNNULL = {op: _op_unnuller(op, names) for op, names in OPS.items()}


def unnulled(msg):
    """msg without the fields given as None that can't be null (its own, and its closed entities'): left out, as
    not given."""
    if isinstance(msg, dict) and (u := _UNNULL.get(msg.get("op"))) is not None:
        return u(msg)
    return msg


def refusal(msg, takes=TAKEN):
    """What a request a host takes (one of takes' ops, as a request's line has it) is answered with if it can't be:
    refused(UNSUPPORTED) for an op it doesn't take, refused(INVALID) for fields that aren't as the schema has them;
    None for one it takes as it is. What's wrong, said on stderr."""
    req = msg.get("req") if isinstance(msg, dict) else None
    if not isinstance(msg, dict) or msg.get("op") not in takes:
        why = f"no op {msg.get('op') if isinstance(msg, dict) else None!r} here"
        _say(f"refused: {why}")
        return refused(UNSUPPORTED, req, why)
    if bad := problems(msg, ("request",)):
        _say(f"refused: {bad[0]}")
        return refused(INVALID, req, bad[0])
    return None


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list,
          "null": type(None)}


def _validate(schema, value, where, out):
    if (ref := schema.get("$ref")) is not None:
        return _validate(DEFS[_name(ref)], value, where, out)
    if "anyOf" in schema:
        for s in schema["anyOf"]:
            tried = []
            _validate(s, value, where, tried)
            if not tried:
                return
        return out.append(f"{where}: {json.dumps(value)[:60]} is none of what it may be")
    if "const" in schema and value != schema["const"]:
        return out.append(f"{where}: not {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        return out.append(f"{where}: {value!r} isn't one of {schema['enum']}")
    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(isinstance(value, _TYPES[k]) and not (k in ("integer", "number") and isinstance(value, bool))
                   for k in kinds):
            return out.append(f"{where}: {type(value).__name__} where it's {'/'.join(kinds)}")
    if isinstance(value, dict) and "properties" in schema:
        for f in schema.get("required") or ():
            if f not in value:
                out.append(f"{where}.{f}: missing")
        for f, v in value.items():
            if f in schema["properties"]:
                _validate(schema["properties"][f], v, f"{where}.{f}", out)
            elif schema.get("additionalProperties") is False:
                out.append(f"{where}.{f}: no such field")
    elif isinstance(value, dict) and isinstance(schema.get("additionalProperties"), dict):
        for k, v in value.items():
            _validate(schema["additionalProperties"], v, f"{where}[{k!r}]", out)
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            _validate(schema["items"], v, f"{where}[{i}]", out)


def _say(what):
    """What's wrong, on stderr: each once (the first SAID_MOST of them; after that, all of them)."""
    if what not in _said:
        if len(_said) < SAID_MOST:
            _said.add(what)
        print(f"wire: {what}", file=sys.stderr, flush=True)


def docs():
    """The wire's reference, made from the schema: each op's lines and fields, the entities and the constants."""
    def kind(p):
        if (ref := _ref(p)) is not None:
            return ref + ("?" if "anyOf" in p else "")
        t = p.get("type", "any")
        t = "/".join(t) if isinstance(t, list) else t
        if "items" in p:
            t += f" of {kind(p['items'])}"
        if "enum" in p:
            t = " | ".join(map(str, p["enum"]))
        return t

    def fields(d, indent):
        out = []
        for f, p in d["properties"].items():
            if f in ("op", "req"):
                continue
            out.append(f"{indent}{f}: {kind(p)}. {p.get('description', '')}")
        return out

    lines = ["", "The wire's reference (schema/types):", ""]
    for name in MESSAGES:
        d = DEFS[name]
        lines.append(f"    {d['properties']['op']['const']} ({KIND[name]}): {d.get('description', '')}")
        lines += fields(d, "        ")
    lines += ["", "Entities:"]
    for name, d in DEFS.items():
        if name not in MESSAGES:
            lines.append(f"    {name}: {d.get('description', '')}")
            lines += fields(d, "        ")
    lines += ["", "Constants (schema/variables):"] + [f"    {k} = {v['const']}: {v.get('description', '')}"
                                                      for k, v in VARIABLES.items()]
    return "\n".join(lines) + "\n"


if __doc__ is not None:  # (none under python -OO)
    __doc__ += docs()


def encode(msg):
    return (json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


class Channel:
    """Messages over a pair of binary files (rfile, wfile): send from any thread, read in one."""

    def __init__(self, rfile, wfile, sock=None):
        self.rfile, self.wfile, self.sock = rfile, wfile, sock
        self.lock = threading.Lock()
        self.closed = False
        self.serving = False  # (a host's, to a head: what it sends are answers, never requests of its own)
        self.inbox = collections.deque()  # (answers made here, read before what comes: a request refused as it went)
        self.refused_here = None  # (or what takes such an answer at once, for a channel whose reader's elsewhere)

    @classmethod
    def over(cls, sock):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return cls(sock.makefile("rb"), sock.makefile("wb"), sock)

    def send(self, msg, passed=False):
        """False once the other end's gone. Fields given as None that its op doesn't let be null are left out
        (unnulled). One the schema hasn't (its op, a field: check) isn't sent: a request of this end's own is answered
        here, refused(INVALID), as if the other end had (refused_here takes it, else read gives it); an answer goes as
        that refusal, for its request; one with no request (a listing, a turn's end) goes without what the schema
        hasn't (cut), said loudly on stderr. One that can't be written as
        JSON goes as an error, for its request. passed: one passed on as it came (a tunnel's), not looked at."""
        if isinstance(msg, Def):
            msg = msg.to_wire()
        if not passed:
            msg = unnulled(msg)
            if (bad := check(msg)) is not None:
                req = msg.get("req") if isinstance(msg, dict) else None
                if req is None:  # (a broadcast: sent without what the schema hasn't, said loudly, not dropped whole)
                    trimmed = cut(msg)
                    if check(trimmed) is not None:
                        _say(f"not sent: {bad}")
                        return not self.closed
                    _say(f"LEFT OUT OF A {str(msg.get('op')).upper()} SENT (the schema hasn't it): {bad}")
                    msg = trimmed
                elif not self.serving and (not isinstance(msg, dict) or msg.get("op") in TAKEN or
                                           msg.get("op") not in OPS):
                    _say(f"not sent: {bad}")
                    if self.refused_here is not None:  # (its caller answered now, not when the reader next wakes)
                        self.refused_here(refused(INVALID, req, bad))
                    else:
                        self.inbox.append(refused(INVALID, req, bad))
                    return not self.closed
                else:
                    _say(f"not sent: {bad}")
                    msg = refused(INVALID, req, bad)
            elif STRICT:
                for p in problems(msg):
                    _say(f"sent: {p}")
        try:
            data = encode(msg)
        except (TypeError, ValueError) as e:
            data = encode({"op": "error", "req": msg.get("req"), "error": f"not sent: {type(e).__name__}: {e}"})
        with self.lock:
            if self.closed:
                return False
            try:
                self.wfile.write(data)
                self.wfile.flush()
                return True
            except (OSError, ValueError):
                self.closed = True
                return False

    def read(self):
        """The next message (an answer made here first: inbox), or None at the end (closed, or a line that isn't one)."""
        while True:
            if self.inbox:
                return self.inbox.popleft()
            try:
                line = self.rfile.readline()
            except (OSError, ValueError):
                return None
            if not line:
                return None
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                return None
            if STRICT and isinstance(msg, dict):
                for p in problems(msg):
                    _say(f"read: {p}")
            return msg if isinstance(msg, dict) else None

    def reply(self):
        """The next message that isn't a ping (the other end's, which may come first, whenever it's due), or None."""
        while (msg := self.read()) is not None and msg.get("op") == "ping":
            pass
        return msg

    def keep_alive(self):
        """A ping every PING_S till it's closed, so the other end, which takes SILENT_S of silence for gone, doesn't."""
        def run():
            while not self.closed and self.send({"op": "ping"}):
                self.pinged.wait(PING_S)
        self.pinged = threading.Event()
        threading.Thread(target=run, daemon=True, name="ping").start()

    def __iter__(self):
        while (msg := self.read()) is not None:
            yield msg

    def close(self):
        with self.lock:
            self.closed = True
        if hasattr(self, "pinged"):
            self.pinged.set()
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for f in (self.wfile, self.rfile, self.sock):
            try:
                f is not None and f.close()
            except OSError:
                pass
        if (proc := getattr(self, "proc", None)) is not None:  # (ssh's: it goes with it)
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()


HOST_COMMAND = CONSTANTS["HOST_COMMAND"]  # (what ssh runs on a host, connect after it: ~/.local/bin/baude-headless)
_DEST = re.compile(r"[A-Za-z0-9_.@%:\[\]+-]+")
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
SUN_PATH = 104  # (macOS's room for a socket's path, and its end: Linux has 108)


def is_id(value):
    """Is it an id a host may give (its own, a session's): a plain word, no path in it, as files here are named by
    them?"""
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9_.-]", "-", text or "")[:64] or "host"


def runtime_dir():
    """Where this machine's host process listens, and ssh keeps its connections to other machines' to share: its
    user's own folder ($BAUDE_RUNTIME_DIR; else $XDG_RUNTIME_DIR/baude; else ~/.local/state/baude/run, or baude-<uid>
    in /tmp if that's too long a path for a socket). Not $TMPDIR: macOS sets it for a session at the screen but not
    for one ssh starts, and both must find the same host process."""
    if os.environ.get("BAUDE_RUNTIME_DIR"):
        return os.environ["BAUDE_RUNTIME_DIR"]
    if os.environ.get("XDG_RUNTIME_DIR"):
        return os.path.join(os.environ["XDG_RUNTIME_DIR"], "baude")
    path = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "baude", "run")
    if len(os.path.join(path, "host.sock.lock").encode()) <= 100:
        return path
    return f"/tmp/baude-{os.getuid()}"


def socket_path():
    return os.path.join(runtime_dir(), "host.sock")


def own_dir(path):
    """A folder that's this user's alone (made, if it isn't there): not a link, theirs, no one else let in. OSError
    if it can't be (another user's, or something else there)."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    st = os.lstat(path)
    if not os.path.isdir(path) or os.path.islink(path) or st.st_uid != os.getuid():
        raise OSError(f"{path} isn't a folder of this user's own")
    if st.st_mode & 0o077:
        os.chmod(path, 0o700)
    return path


def _hello(ch, timeout, what, here=False):
    """(channel, the server's info) past hello, or OSError: what didn't answer. here: by its socket, on its machine."""
    ch.sock.settimeout(timeout)
    ch.send({"op": "hello", "client": HEAD_CLIENT, "version": VERSION, **({"commit": HEAD_COMMIT} if HEAD_COMMIT else {}),
             **({"here": True} if here else {})})
    reply = ch.reply()
    if not reply or reply.get("op") != "hello":
        ch.close()
        raise OSError(said(reply) or f"{what} isn't a Baude host")
    ch.sock.settimeout(None)
    return ch, reply.get("server") or {}


def own_socket(path):
    """OSError unless a socket's this user's own, in a folder no one else may write in: one another user left where
    this user's should be (the /tmp folder, say) is no host process of theirs, and isn't connected to."""
    folder, me = os.lstat(os.path.dirname(path) or "."), os.getuid()
    st = os.lstat(path)
    if not stat.S_ISDIR(folder.st_mode) or folder.st_uid != me or folder.st_mode & 0o022 or \
            not stat.S_ISSOCK(st.st_mode) or st.st_uid != me:
        raise OSError(f"{path} isn't this user's own: not connected to")


def connect_local(path=None, timeout=5.0):
    """A Channel to the host process listening on a Unix socket (this machine's, socket_path(), if none's given)."""
    path = path or socket_path()
    own_socket(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(path)
    except OSError:
        sock.close()
        raise
    return _hello(Channel.over(sock), timeout, path, here=True)


def is_dest(dest):
    """Can this be given to ssh as where to go: a name, user@host or an address, not an option?"""
    return isinstance(dest, str) and bool(_DEST.fullmatch(dest)) and not dest.startswith("-")


def ssh_args(dest, strict=False):
    """ssh's command line for a host's process: no password or passphrase ever asked (BatchMode: its keys and agent,
    as ~/.ssh/config has them), a connection to each host kept for a minute and shared (ControlMaster), so a
    session's own connection doesn't wait for another handshake. strict: the host's key must be in known_hosts
    already (as a host taken up once from what the network said was kept). It runs HOST_COMMAND there."""
    if not is_dest(dest):
        raise OSError(f"not somewhere ssh can go: {dest!r}")
    control = control_path(dest)
    share = ["-o", "ControlMaster=auto", "-o", f"ControlPath={control}", "-o", "ControlPersist=60"] if control else \
        ["-o", "ControlMaster=no", "-o", "ControlPath=none"]
    return [os.environ.get("BAUDE_SSH") or "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", *share,
            *(["-o", "StrictHostKeyChecking=yes"] if strict else []), "--", dest,
            f"{HOST_COMMAND} connect"]


def control_path(dest):
    """Where ssh keeps its shared connection to dest: short, as the socket ssh makes there first is its path and 17
    more (".", 16 random letters); in the runtime folder, else this user's own folder in /tmp; None if neither has
    room (each connection its own, then)."""
    import hashlib
    name = "c-" + hashlib.sha1(dest.encode()).hexdigest()[:12]
    for folder in (runtime_dir(), f"/tmp/baude-{os.getuid()}"):
        path = os.path.join(folder, name)
        if len(path.encode()) + 17 < SUN_PATH:
            try:
                own_dir(folder)
                return path
            except OSError:
                continue
    return None


def connect_ssh(dest, strict=False, timeout=25.0):
    """A Channel to another machine's host process, over ssh (ssh_args): its stdin and stdout one end of a socket
    pair, this the other, so a Channel takes it as it takes a socket. OSError, with what ssh said, if it can't."""
    mine, theirs = socket.socketpair()
    try:
        proc = subprocess.Popen(ssh_args(dest, strict), stdin=theirs, stdout=theirs, stderr=subprocess.PIPE,
                                start_new_session=True)
    except OSError:
        mine.close()
        raise
    finally:
        theirs.close()
    said = []

    def errors():  # (what ssh says goes wrong: the last of it, for the error)
        for line in proc.stderr:
            said[:] = (said + [line.decode("utf-8", "replace").strip()])[-3:]
    threading.Thread(target=errors, daemon=True, name="ssh-stderr").start()
    ch = Channel.over(mine)
    ch.proc = proc
    try:
        return _hello(ch, timeout, dest)
    except OSError as e:
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        if proc.returncode == 127 or any("No such file or directory" in s or "not found" in s for s in said):
            raise OSError(wording.say("hosts.no_commands", host=dest)) from None  # (ssh's own words say neither what nor what to do)
        raise OSError("; ".join(s for s in said if s) or str(e)) from None


def through(ch, host, timeout=5.0, as_=None, meta=False, agent=None):
    """(channel, info) past hello for member host of the mesh, on a channel to a host process in it: relay, then
    hello through the tunnel (as_: relay's as; meta: its as_meta; agent: its as_agent). OSError if there's no way to
    it."""
    ch.sock.settimeout(max(timeout, OPEN_S + 5))  # (the host process's own wait, OPEN_S, ends first, and says why)
    ch.send({"op": "relay", "to": host, "req": 0, **({"as": as_} if as_ else {}),
             **({"as_meta": True} if meta else {}), **({"as_agent": agent} if agent and not as_ and not meta else {})})
    reply = ch.reply()
    if not reply or reply.get("op") != "relayed":
        ch.close()
        raise OSError(said(reply) or "the host process here didn't answer")
    return _hello(ch, max(timeout, 10.0), host)


def connect_mesh(host, path=None, timeout=5.0, as_=None, meta=False, agent=None):
    """A Channel to member host of the mesh, through this machine's host process (socket_path(), or the one at
    path); as_, meta, agent: relay's as, as_meta, as_agent."""
    path = path or socket_path()
    own_socket(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(path)
    except OSError:
        sock.close()
        raise
    return through(Channel.over(sock), host, timeout, as_, meta, agent)


def reach(where, timeout=5.0, as_=None, meta=False, agent=None):
    """(channel, info) for a host process wherever it is (where: see this module's docstring); as_, meta, agent:
    through the mesh, relay's as, as_meta and as_agent (on this machine, a request's own says it)."""
    via = (where or {}).get("via")
    if via == "local":
        return connect_local(None, timeout)
    if via == "unix":
        return connect_local(where["path"], timeout)
    if via == "ssh":
        return connect_ssh(where["dest"], bool(where.get("strict")), max(timeout, 3.0))
    if via == "mesh":
        return connect_mesh(where["host"], where.get("path"), timeout, as_, meta, agent)
    raise OSError(f"nowhere to reach: {where!r}")


def place(where):
    """where with only what says where it is (Where's fields)."""
    return {k: v for k, v in (where or {}).items() if k in DEFS["Where"]["properties"]}


def is_where(where):
    """Is it somewhere reach can go?"""
    if not isinstance(where, dict):
        return False
    via = where.get("via")
    return via == "local" or via == "unix" and isinstance(where.get("path"), str) or \
        via == "ssh" and is_dest(where.get("dest")) or \
        via == "mesh" and is_id(where.get("host")) and isinstance(where.get("path") or "", str)


SENDER_KINDS = ("bot", "chat", "subagent", "host")  # (who a message to a session is from, if not its user: said_by)
_SENDER = re.compile(r'\A<baude-sender((?: [a-z_]+="[^"<>]*")*)>[^<]*</baude-sender>(?:\n\n|\n|\Z)')


def clean_sender(sender):
    """A sender as it's kept and said ({kind, name, host, host_name, session}: words, cut short), or None if it isn't
    one."""
    if not isinstance(sender, dict):
        return None
    out = {k: " ".join(str(sender[k]).split())[:120] for k in ("name", "host", "host_name", "session")
           if isinstance(sender.get(k), str) and sender[k].strip()}
    out["kind"] = sender.get("kind") if sender.get("kind") in SENDER_KINDS else "host"
    return out


def sender_name(sender):
    """Who a sender is, as a message from it's headed: "Ada, a Bot on lab-mac", "subagent “List” on C"."""
    name, where = sender.get("name") or "", sender.get("host_name")
    kind = sender.get("kind")
    who = {"bot": wording.say("senders.bot", name=name) if name else wording.say("senders.bot_unnamed"),
           "chat": wording.say("senders.chat", name=name) if name else wording.say("senders.chat_unnamed"),
           "subagent": wording.say("senders.subagent", name=name) if name else wording.say("senders.subagent_unnamed"),
           "host": name or wording.say("senders.host_unnamed")}.get(
        kind, name or wording.say("senders.another"))  # (a kind of a newer host's: by its name)
    return wording.say("senders.on", who=who, where=where) if where and kind != "host" else who


def said_by(sender, content):
    """A user message's content (text, or blocks) as from sender, not the session's user, for its agent: its first
    line says so, in a tag, then a blank line and what was said. (Heads draw who it's from from the host's sender
    store, never from this.)"""
    import html
    sender = clean_sender(sender)
    if sender is None:
        return content
    attrs = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in sender.items())
    head = (f"<baude-sender{attrs}>A message from {html.escape(sender_name(sender), quote=False)}, not from your user"
            f"</baude-sender>\n\n")
    if isinstance(content, str):
        return head + content
    blocks = list(content or [])
    at = next((i for i, b in enumerate(blocks) if isinstance(b, dict) and b.get("type") == "text"), None)
    if at is None:
        return [{"type": "text", "text": head.rstrip()}] + blocks
    blocks[at] = {**blocks[at], "text": head + (blocks[at].get("text") or "")}
    return blocks


def unsaid(text):
    """A message's text without the tag said_by put at its head (one the sender store says is from another, as it's
    drawn; what a mesh tool sent, as it finds it again)."""
    m = _SENDER.match(text or "")
    return text if m is None else text[m.end():]


def is_local(where):
    """Is it on this machine (reached by its socket, not over ssh or through the mesh)?"""
    return (where or {}).get("via") in ("local", "unix")


def said_where(where):
    """Where a host process is, as people say it: user@host, through the mesh, or local (this machine)."""
    if (where or {}).get("via") == "mesh":
        return wording.say("senders.through_mesh")
    return where.get("dest") if (where or {}).get("via") == "ssh" else wording.say("senders.local")


def bridge(sock, rfd=0, wfd=1):
    """What comes in on rfd to the socket, and what the socket says out on wfd, as it comes, till either end's done
    (`baude_headless.py connect`: ssh's stdin and stdout, and the host process's socket)."""
    sock.setblocking(True)
    open_in = True
    while True:
        ready, _, _ = select.select([sock] + ([rfd] if open_in else []), [], [])
        if sock in ready:
            data = sock.recv(65536)
            if not data:
                return
            while data:
                data = data[os.write(wfd, data):]
        if open_in and rfd in ready:
            data = os.read(rfd, 65536)
            if not data:  # (the other end's said all it will: the socket's told, and what's still to come comes)
                open_in = False
                try:
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    return
                continue
            sock.sendall(data)


def wait_for_socket(path, seconds):
    """Has a host process started listening at path within seconds?"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            ch, _info = connect_local(path, timeout=2)
            ch.close()
            return True
        except OSError:
            time.sleep(0.1)
    return False
