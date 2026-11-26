#!/usr/bin/env python3
"""Access over the wire, fuzzed: two host processes linked as a mesh (on Unix sockets of the run's own, their chats
the fake claude's), the one asked of reached from the other through its relay, as any member's request reaches a
host. Each round sets random rules, each split capability's read and write apart (and a chat's own, read and write
apart), then sends every op a rule guards, and a chat's, each built from the schema with the fields that pick its side
sometimes there: each is checked against its own side (access.OPS), a read let in by the read rule or the write one;
whatever a rule refuses must come back access_denied, whatever it lets through must not, and both hosts must still
answer after. The same ops from a head on the host's own socket are never refused. Then, each round:
  - opens forged on the way: no from, another member's from, a signature taken off: closed, or, from a member whose
    key isn't known, served as from one no rule chooses;
  - relays borrowed: a member's tunnel, or a chat a member started, asking to be passed on: refused;
  - ops whose chat or folder is optional (git, signal_names, read_file, catalogue, list_dir): a member's only where it
    may read; a chat moved to another subscription (accounts' use): only where it may write the chat and the
    subscriptions;
  - the host's subscriptions (accounts' list and usage by Subscriptions' read, use by its write) by Subscriptions
    alone, never Access Rules: some rounds let a member manage subscriptions while refusing it the rules, and its
    access_set is refused all the same;
  - the chat's Bash asked about: approved by the member only with the chat's write and Shell, and its turn's end
    (turn_ended) told it only if it may read the chat.
No GTK.

    python3 tests/test_access.py [--rounds N] [--seed S]
"""
import argparse
import json
import os
import random
import shlex
import sys
import tempfile
import threading
import time
import uuid

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
os.environ["BAUDE_DOCKER"] = os.path.join(HERE, "fake_docker.py")  # (docker's ops let through: the fake's machine, never one here)
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

from baudelib import access, mesh, wire  # noqa: E402
from baudelib.server import Server  # noqa: E402

# (ops that'd start something of the machine's own, or change the host itself, if they were let through: a shell, a
# browser, a claude, a link, the host stopped; sent only where they're refused)
STARTS = {"terminal", "terminal_in", "multiplex", "emission", "emission_watch", "start", "agent", "kube_follow", "docker_follow", "infer",
          "files", "emission_serve", "input", "delete", "stop", "archive", "unarchive", "set_cwd", "run_task", "task_stop",
          "autofix", "held", "memory", "tasks", "rename", "bot", "always_allow", "waits_on", "attach",
          "emission_watch_say", "emission_answer", "terminal_resize", "terminal_close", "detach", "quit", "link",
          "unlink", "deactivate", "activate", "accounts", "login", "login_code", "telemetry_set", "theme_save",
          "theme_remove", "feedback", "fixes"}
FOLDERS = ("read_file", "git", "signal_names", "catalogue")  # (a chat's folder's, the chat optional: tried apart)
STRANGER = "00000000-0000-4000-8000-000000000000"


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
    if rng.random() < 0.5:  # (the fields that make one of a chat's a change to it)
        msg.update({"attach": {"always_allow": ["Read"]}, "memory": {"text": "x"}, "autofix": {"on": True},
                    "tasks": {"delete": "x"}, "notes": {"action": "write"}}.get(op, {}))
    return msg


def letting(rules, member):
    """Whom each rule lets in (rules: by name, as the host has them): a read rule's let in by its write one too."""
    lets = {r: access.lets(rule, member) for r, rule in rules.items()}
    return {r: v or r.endswith("_read") and lets[r.removesuffix("_read") + "_write"] for r, v in lets.items()}


def expected(msg, lets, chat, session):
    """Would the rules let a member send msg (the host's: lets; the chat's, its own first: chat; session: the chat, as
    the host has it)?"""
    op = msg["op"]
    adds = bool(set(msg.get("always_allow") or ()) - session.always_allow)  # (an attach's: only tools not allowed yet)
    rule = ("manage_write" if adds else "manage_read") if op == "attach" else access.rule_of(msg)
    if op not in access.FOUND:
        return lets[rule] and (op != "start" or "fork_from" not in msg or chat["manage_read"])
    extra = {"always_allow": lets["shell"], "run_task": lets["shell"], "waits_on": False,  # (waits_on: its agent's only)
             "bot": msg.get("bot") is None or lets["shell"] and lets["browser_write"],
             "attach": not adds or lets["shell"]}
    return chat[rule] and extra.get(op, True)


def chat_letting(own, host, member):
    """Whom a chat's rules let in (own: its own, host: the host's, by name): each side its own or the host's, a write
    only for one its read lets in too."""
    read = access.lets(own.get("manage_read", host["manage_read"]), member)
    return {"manage_read": read,
            "manage_write": read and access.lets(own.get("manage_write", host["manage_write"]), member)}


def random_rule(rng, member):
    """Everybody, nobody, or some of member and a stranger."""
    who = rng.choice(access.WHO)
    return {"who": who, **({"members": rng.sample([member, STRANGER], rng.randint(0, 2))} if who == "chosen" else {})}


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


def forged(how, path, to):
    """A member's tunnel to to, its open changed on the way (how): (channel, None), or (None, why it was closed)."""
    sent = mesh.Link.tsend

    def tsend(link, key, f):
        if f.get("op") == "open":
            f = dict(f)
            if how == "no from":
                f.pop("from", None)
            elif how == "another's from":
                f["from"] = STRANGER
            elif how in ("unsigned", "unsigned stranger"):
                f.pop("signed", None)
                f.pop("sig", None)
                if how == "unsigned stranger":
                    f["from"] = STRANGER
            elif how == "replayed":
                tsend.kept = tsend.kept or dict(f)
                f = {**f, "signed": tsend.kept["signed"], "sig": tsend.kept["sig"]}
        return sent(link, key, f)
    tsend.kept = getattr(forged, "kept", None)
    mesh.Link.tsend = tsend
    try:
        ch, _ = wire.reach({"via": "mesh", "host": to, "path": path}, timeout=10)
        return ch, None
    except OSError as e:
        return None, str(e)
    finally:
        mesh.Link.tsend = sent
        forged.kept = tsend.kept


def heard(ch):
    """What comes on ch, as it comes, kept in a list (read on a thread of its own)."""
    out = []

    def run():
        while (m := ch.read()) is not None:
            out.append(m)
    threading.Thread(target=run, daemon=True).start()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)
    print(f"seed {seed}", flush=True)
    asked, other = host("asked"), host("other")
    other.mesh.link({"via": "unix", "path": asked.socket_path})
    wait(lambda: other.me["id"] in asked.mesh.hops and asked.me["id"] in other.mesh.hops, "the two linked")
    wait(lambda: asked.mesh.pins.get(other.me["id"]) == other.mesh.key, "other's key pinned")
    folder, mfolder = os.path.join(TMP, "c"), os.path.join(TMP, "m")
    for f in (folder, mfolder):
        os.makedirs(f, exist_ok=True)
    with open(os.path.join(folder, "f.txt"), "w") as f:
        f.write("x")
    chat = asked.start({"prompt": "Hello", "cwd": folder})
    wait(lambda: chat.asks, "the chat on the host asking")
    member, _ = wire.reach({"via": "mesh", "host": asked.me["id"], "path": other.socket_path}, timeout=10)
    watcher, _ = wire.reach({"via": "mesh", "host": asked.me["id"], "path": other.socket_path}, timeout=10)
    direct, _ = wire.reach({"via": "unix", "path": asked.socket_path}, timeout=10)
    req = 1
    assert ask(direct, {"op": "access_set", "capability": "spawn", "rule": {"who": "everybody"}}, req)["op"] == "access_set"
    req += 1  # (a rule kept before its capability was split: both sides', Access Rules' write's only)
    old = os.path.join(TMP, "servers", "old")
    os.makedirs(old, exist_ok=True)
    with open(os.path.join(old, access.FILE), "w") as f:
        json.dump({"rules": {"kube": {"who": "chosen", "members": [STRANGER]}, "access": {"who": "nobody"},
                             "shell": {"who": "nobody"}}}, f)
    kept = access.Access(type("S", (), {"state_dir": old})()).rules
    assert kept["kube_read"] == kept["kube_write"] == {"who": "chosen", "members": [STRANGER]}, kept
    assert kept["access_read"] == {"who": "everybody"} and kept["access_write"] == {"who": "nobody"}, kept
    assert kept["shell"] == {"who": "nobody"} and kept["manage_read"] == {"who": "everybody"}, kept
    req += 1  # (a chat a member starts: its own tools judged as that member)
    started = ask(member, {"op": "start", "prompt": "", "cwd": mfolder, "draft": True}, req)
    mchat = (started.get("session") or {}).get("id")
    assert mchat and asked.sessions[mchat].meta["started_by"] == {"id": other.me["id"], "verified": True}, started
    as_mchat, _ = wire.reach({"via": "unix", "path": asked.socket_path}, timeout=10, as_=mchat)
    req += 1
    assert ask(watcher, {"op": "subscribe"}, req)["op"] == "sessions"
    watched = heard(watcher)  # (a member's, subscribed: what it's told of the chat's turns)
    for name, rule in (("manage_write", {"who": "nobody"}), ("manage_read", {"who": "everybody"})):
        req += 1
        assert ask(direct, {"op": "access_set", "capability": name, "rule": rule}, req)["op"] == "access_set"
    for back in (False, True):  # (a chat's own read, no own write; the host's write widened after: the chat's write
        if back:                  # still within its read; and so with its own write set, then back to the host's)
            req += 1
            ask(direct, {"op": "access_set", "session": chat.id, "capability": "manage_write",
                         "rule": {"who": "chosen", "members": [other.me["id"]]}}, req)
        req += 1
        ask(direct, {"op": "access_set", "session": chat.id, "capability": "manage_read",
                     "rule": {"who": "chosen", "members": [STRANGER]}}, req)
        req += 1
        ask(direct, {"op": "access_set", "session": chat.id, "capability": "manage_write", "rule": None}, req)
        req += 1
        ask(direct, {"op": "access_set", "capability": "manage_write", "rule": {"who": "everybody"}}, req)
        for msg in ({"op": "transcript", "session": chat.id}, {"op": "rename", "session": chat.id, "title": "x"},
                    {"op": "attach", "session": chat.id}):
            req += 1
            assert ask(member, msg, req).get("code") == wire.DENIED, f"{msg['op']} let through past the chat's read"
        req += 1
        assert chat.id not in {x["id"] for x in ask(member, {"op": "subscribe"}, req)["sessions"]}
        req += 1
        ask(direct, {"op": "access_set", "capability": "manage_write", "rule": {"who": "nobody"}}, req)
    for name in access.rules_of("manage"):
        req += 1
        ask(direct, {"op": "access_set", "session": chat.id, "capability": name, "rule": None}, req)
    refused, let, forgeries = 0, 0, 0
    for n in range(args.rounds):
        rules = {}
        for name in rng.sample(access.RULES, len(access.RULES)):  # (in any order: write's let into read as it's set)
            rules[name] = random_rule(rng, other.me["id"])
        if rng.random() < 0.3:  # (subscriptions a member's, the rules not)
            rules["subscriptions_write"] = rng.choice([{"who": "everybody"},
                                                       {"who": "chosen", "members": [other.me["id"]]}])
            rules["access_write"] = rng.choice([{"who": "nobody"}, {"who": "chosen", "members": [STRANGER]}])
            rules["access_read"] = rules["access_write"]
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
        for name in access.rules_of("manage"):  # (the chat's own, each side apart: None, the host's)
            req += 1
            own = rng.choice([None, None, random_rule(rng, other.me["id"])])
            got = ask(direct, {"op": "access_set", "session": chat.id, "capability": name, "rule": own}, req)
            assert got["op"] == "access_set", got
        for name in rng.sample(access.rules_of("manage"), rng.randint(0, 2)):  # (the host's changed after the chat's)
            req += 1
            got = ask(direct, {"op": "access_set", "capability": name, "rule": random_rule(rng, other.me["id"])}, req)
            assert got["op"] == "access_set", got
        host_rules = {name: asked.access.rule(name) for name in access.RULES}
        lets = letting(host_rules, other.me["id"])
        own = access.own_rules(asked.sessions[chat.id].meta)
        chat_lets = chat_letting(own, host_rules, other.me["id"])
        chat_let = chat_lets["manage_read"]
        ops = [op for op in access.OPS if op not in FOLDERS]
        rng.shuffle(ops)
        for op in ops:
            msg = request(op, rng, chat.id)
            if wire.problems(msg, ("request",)):  # (one the schema won't take as made: the schema's refusal first)
                continue
            cap = access.rule_of(msg, chat)
            allowed = expected(msg, lets, chat_lets, chat)
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

        # (ops whose chat or folder is optional: a member's only where it may manage)
        optional = [({"op": "git", "what": "page", "session": chat.id, "cwd": folder}, chat_let),
                    ({"op": "signal_names", "session": chat.id, "cwd": folder}, chat_let),
                    ({"op": "read_file", "path": os.path.join(folder, "f.txt")}, chat_let),
                    ({"op": "catalogue", "cwd": folder}, chat_let),
                    ({"op": "read_file", "path": os.path.join(HERE, "test_access.py")}, False),
                    ({"op": "list_dir", "path": TMP}, lets["manage_read"]),
                    ({"op": "accounts", "what": "use", "session": chat.id, "id": ""},
                     lets["subscriptions_write"] and chat_lets["manage_write"]),
                    ({"op": "accounts", "what": "list"}, lets["subscriptions_read"]),
                    ({"op": "accounts", "what": "usage", "id": ""}, lets["subscriptions_read"]),
                    ({"op": "access_set", "capability": "subscriptions_read",
                      "rule": asked.access.rule("subscriptions_read")}, lets["access_write"]),
                    ({"op": "access"}, lets["access_read"])]
        for msg, allowed in rng.sample(optional, len(optional)):
            req += 1
            got = ask(member, msg, req)
            assert (got.get("code") != wire.DENIED) == allowed, f"round {n}: {msg} {allowed}: {got}"

        # (opens forged on the way: closed; one unsigned from a member with no key known: no rule chooses it)
        how = rng.choice(["no from", "another's from", "unsigned", "unsigned stranger", "replayed"])
        ch, why = forged(how, other.socket_path, asked.me["id"])
        forgeries += 1
        if how == "unsigned stranger":
            assert ch is not None, f"round {n}: {how}: {why}"
            req += 1
            listed = ask(ch, {"op": "subscribe"}, req)
            shown = chat.id in {x["id"] for x in listed["sessions"]}
            everybody = own.get("manage_read", host_rules["manage_read"])["who"] == "everybody"
            assert shown == everybody, f"round {n}: an unverified member's listing: {shown}, {own}"
            req += 1
            assert (ask(ch, {"op": "access_set", "capability": "shell", "rule": asked.access.rule("shell")}, req)
                    .get("code") == wire.DENIED) != (asked.access.rule("access_write")["who"] == "everybody")
            ch.close()
        elif how == "replayed" and forged.kept and ch is not None and not getattr(forged, "replay_tried", False):
            ch.close()  # (the first: the open kept; tried again next time it comes up)
            forged.replay_tried = True
        else:
            assert ch is None, f"round {n}: an open forged ({how}) was served"

        # (relays borrowed: a member's tunnel, or a chat a member started, passed on: refused)
        req += 1
        got = ask(member, {"op": "relay", "to": other.me["id"]}, req)
        assert got.get("code") == wire.DENIED, f"round {n}: a member's tunnel relayed: {got}"
        try:
            ch, _ = wire.reach({"via": "mesh", "host": other.me["id"], "path": asked.socket_path}, timeout=5,
                               as_=mchat)
            ch.close()
            raise AssertionError(f"round {n}: a member's chat relayed")
        except OSError:
            pass
        req += 1  # (its own tools, on the host's socket: judged as the member)
        listed = ask(as_mchat, {"op": "subscribe"}, req)
        assert (chat.id in {x["id"] for x in listed["sessions"]}) == chat_let, f"round {n}: a member's chat's listing"

        # (the chat's Bash: approved by the member only with Shell; its turn's end told only to those it's shown to)
        del watched[:]
        perm = next(iter(chat.asks))
        approve = {"op": "input", "session": chat.id, "msg": {"type": "control_response", "response": {
            "subtype": "success", "request_id": perm, "response": {"behavior": "allow", "updatedInput": {}}}}}
        req += 1
        got = ask(member, approve, req)
        assert (got.get("code") == wire.DENIED) != (chat_lets["manage_write"] and lets["shell"]), \
            f"round {n}: Bash approved: {got}"
        if got.get("code") == wire.DENIED:
            req += 1
            ask(direct, approve, req)
        wait(lambda: not chat.busy, "the turn's end")
        time.sleep(0.5)
        told = [m for m in watched if m.get("op") == "turn_ended" and m.get("session") == chat.id]
        assert bool(told) == chat_let, f"round {n}: turn_ended told: {bool(told)}, may manage: {chat_let}"
        req += 1
        ask(direct, {"op": "input", "session": chat.id, "msg": {"type": "user", "uuid": str(uuid.uuid4()), "message": {
            "role": "user", "content": "Again"}}}, req)
        wait(lambda: chat.asks, "the chat asking again")

        req += 1
        listed = ask(member, {"op": "subscribe"}, req)
        assert (chat.id in {x["id"] for x in listed["sessions"]}) == chat_let, f"round {n}: listing"
        for ch in (member, direct):
            req += 1
            assert ask(ch, {"op": "hello"}, req)["op"] == "hello"
    print(f"ok: {args.rounds} rounds, {refused} refused, {let} let through, {forgeries} opens forged", flush=True)
    for ch in (member, direct, watcher, as_mchat):
        ch.close()
    for s in (other, asked):
        s.shutdown()
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)
    shutil.rmtree(os.environ["BAUDE_RUNTIME_DIR"], ignore_errors=True)
    os._exit(0)


if __name__ == "__main__":
    main()
