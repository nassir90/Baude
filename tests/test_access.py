#!/usr/bin/env python3
"""Access over the wire, fuzzed: two host processes linked as a mesh (on Unix sockets of the run's own, their chats
the fake claude's), the one asked of reached from the other through its relay, as any member's request reaches a
host. Each round sets random rules (and a chat's own), then sends every op a rule guards, and a chat's, each built
from the schema; whatever a rule refuses must come back access_denied, whatever it lets through must not, and both
hosts must still answer after. The same ops from a head on the host's own socket are never refused. No GTK.

    python3 tests/test_access.py [--rounds N] [--seed S]
"""
import argparse
import os
import random
import shlex
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = os.path.realpath(tempfile.mkdtemp(prefix="baude-access-"))
for var, sub in (("HOME", "home"), ("XDG_DATA_HOME", "xdg-data"), ("XDG_CONFIG_HOME", "xdg-config"),
                 ("XDG_CACHE_HOME", "xdg-cache"), ("XDG_STATE_HOME", "xdg-state"), ("CLAUDE_CONFIG_DIR", "claude"),
                 ("BAUDE_LAUNCHERS", "Applications"), ("BAUDE_BINS", "bin")):
    os.environ[var] = os.path.join(TMP, sub)
    os.makedirs(os.environ[var], exist_ok=True)
os.environ["KUBECONFIG"] = os.path.join(TMP, "no-kubeconfig")
os.environ["DOCKER_HOST"] = "unix://" + os.path.join(TMP, "no-docker.sock")
os.environ["BAUDE_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="bda-", dir="/tmp")
os.environ.update(BAUDE_SSH="false", BAUDE_MENU_BAR="0", BAUDE_UPDATE="0", BAUDE_GH="", FAKE_CLAUDE_SESSION_IDS="1",
                  FAKE_CLAUDE_TRANSCRIPTS="1")
os.environ["PATH"] = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                                     if not os.path.exists(os.path.join(p, "claude")))
FAKE = os.path.join(TMP, "fake-claude")
with open(FAKE, "w") as f:
    f.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(os.path.join(HERE, "fake_claude.py"))} "$@"\n')
os.chmod(FAKE, 0o755)
os.environ["BAUDE_CLAUDE"] = FAKE

from baudelib import access, wire  # noqa: E402
from baudelib.server import Server  # noqa: E402

# (ops that'd start something of the machine's own if they were let through: a shell, a browser, a claude; sent
# only where they're refused)
STARTS = {"terminal", "terminal_in", "multiplex", "emission", "emission_watch", "start", "agent", "kube_follow", "infer",
          "files", "emission_serve", "input", "delete", "stop", "archive", "unarchive", "set_cwd", "run_routine",
          "autofix", "held", "memory", "routines", "rename", "bot", "always_allow", "waits_on", "attach",
          "emission_watch_say", "emission_answer", "terminal_resize", "terminal_close", "detach"}
CHAT_OPS = sorted(op for op, names in wire.OPS.items() for n in names if wire.KIND[n] == "request" and
                  "session" in wire.DEFS[n].get("required", ()) and op not in access.OPS)


def made(schema, rng, sid):
    """A value as schema has it (its required fields only, for an object), session ids sid."""
    if "$ref" in schema:
        return made(wire.DEFS[schema["$ref"].removesuffix(".json")], rng, sid)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return rng.choice(schema["enum"])
    if "anyOf" in schema:
        return made(schema["anyOf"][0], rng, sid)
    kinds = schema.get("type", "object")
    kind = kinds[0] if isinstance(kinds, list) else kinds
    if kind == "object":
        return {k: made(schema["properties"][k], rng, sid) for k in schema.get("required", ())
                if k in schema.get("properties", {})}
    return {"string": "x", "integer": 1, "number": 1.0, "boolean": False, "array": [], "null": None}[kind]


def request(op, rng, sid):
    """A request of op the schema takes, its session sid."""
    name = next(n for n in wire.OPS[op] if wire.KIND[n] == "request")
    msg = made(wire.DEFS[name], rng, sid)
    if "session" in wire.DEFS[name]["properties"]:
        msg["session"] = sid
    if op == "start":
        msg.update(fork_from=sid) if rng.random() < 0.5 else None
    return msg


def ask(ch, msg, req):
    ch.send({**msg, "req": req})
    end = time.monotonic() + 15
    while time.monotonic() < end:
        m = ch.reply()
        if m is None:
            raise AssertionError(f"{msg['op']}: the connection went")
        if m.get("req") == req:
            return m
    raise AssertionError(f"{msg['op']}: no answer")


def wait(cond, what, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def host(name):
    s = Server(state_dir=os.path.join(TMP, "servers", name), name=name, log=lambda _t: None,
               socket_path=os.path.join(os.environ["BAUDE_RUNTIME_DIR"], f"{name}.sock"))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)
    print(f"seed {seed}")
    asked, other = host("asked"), host("other")
    other.mesh.link({"via": "unix", "path": asked.socket_path})
    wait(lambda: other.me["id"] in asked.mesh.hops and asked.me["id"] in other.mesh.hops, "the two linked")
    chat = asked.start({"prompt": "Hello", "cwd": TMP})
    wait(lambda: chat.asks, "the chat on the host asking")
    member, _ = wire.reach({"via": "mesh", "host": asked.me["id"], "path": other.socket_path}, timeout=10)
    direct, _ = wire.reach({"via": "unix", "path": asked.socket_path}, timeout=10)
    stranger = "00000000-0000-4000-8000-000000000000"
    req, refused, let = 0, 0, 0
    for n in range(args.rounds):
        rules = {}
        for cap in access.CAPABILITIES:
            who = rng.choice(access.WHO)
            rules[cap] = {"who": who, **({"members": rng.sample([other.me["id"], stranger], rng.randint(0, 2))}
                                         if who == "chosen" else {})}
        if rng.random() < 0.5:  # (the rules set as a member would, where access lets it; else as a head here)
            for cap, rule in rules.items():
                req += 1
                got = ask(member, {"op": "access_set", "capability": cap, "rule": rule}, req)
                if got.get("code") == wire.DENIED:
                    req += 1
                    got = ask(direct, {"op": "access_set", "capability": cap, "rule": rule}, req)
                assert got.get("op") == "access_set", got
        else:
            for cap, rule in rules.items():
                req += 1
                assert ask(direct, {"op": "access_set", "capability": cap, "rule": rule}, req)["op"] == "access_set"
        own = rng.choice([None, {"who": "everybody"}, {"who": "nobody"}, {"who": "chosen", "members": [other.me["id"]]}])
        req += 1
        assert ask(direct, {"op": "access_set", "session": chat.id, "rule": own}, req)["op"] == "access_set"
        lets = {cap: access.lets(asked.access.rule(cap), other.me["id"]) for cap in access.CAPABILITIES}
        chat_let = access.lets(own, other.me["id"]) if own is not None else lets["manage"]
        ops = [(op, cap) for op, cap in access.OPS.items()] + [(op, "manage") for op in CHAT_OPS]
        rng.shuffle(ops)
        for op, cap in ops:
            msg = request(op, rng, chat.id)
            if wire.problems(msg, ("request",)):  # (one the schema won't take as made: the schema's refusal first)
                continue
            allowed = (lets[cap] and (op != "start" or "fork_from" not in msg or chat_let)) if cap != "manage" \
                else chat_let
            if allowed and op in STARTS:
                continue
            req += 1
            got = ask(member, msg, req)
            denied = got.get("code") == wire.DENIED
            assert denied != allowed, f"round {n}: {op} ({cap}) {'let through' if not allowed else 'refused'}: {got}"
            refused += denied
            let += not denied
            if not denied or op in STARTS:
                continue
            req += 1  # (the same from a head here: never refused)
            assert ask(direct, msg, req).get("code") != wire.DENIED, f"{op} refused a head here"
        req += 1
        listed = ask(member, {"op": "subscribe"}, req)
        assert (chat.id in {x["id"] for x in listed["sessions"]}) == chat_let, f"round {n}: listing"
        for ch in (member, direct):
            req += 1
            assert ask(ch, {"op": "hello"}, req)["op"] == "hello"
    print(f"ok: {args.rounds} rounds, {refused} refused, {let} let through")
    for ch in (member, direct):
        ch.close()
    for s in (other, asked):
        s.shutdown()
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)
    shutil.rmtree(os.environ["BAUDE_RUNTIME_DIR"], ignore_errors=True)
    os._exit(0)


if __name__ == "__main__":
    main()
