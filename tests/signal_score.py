#!/usr/bin/env python3
"""Score a replay (tests/ref_replay.py) per mention: for each entry of a reference list, what its own reply shows.

    tests/signal_score.py RESULT.json MESSAGES.json REFERENCE.json [REFERENCE.json...]

MESSAGES.json is the replies' text as a JSON list (the reference entries' "message" indexes into it). A reference
entry is {"surface", "message", "class", "exists": yes|proposed|elsewhere|no|external, "path", "line"}; see
tests/ref_score.py and docs/signal-next.md. The replay's glossary store is read for each entry the way
Baude draws it in that reply: the reply's own decision first (the reader's, or the evidence's: what Claude read or
grepped), else the glossary's answer for the name. Baude's own file linker counts too: a backticked or [linked] path
that exists. An entry in the repository is right when what's drawn opens its place (the file, or under the folder);
wrong when it opens another; missed when nothing is drawn. Not in the repository (proposed, elsewhere, no): right
when it's shown as not found or not in the code. External: right when it's marked external. A kubeconfig context's
place is "kubeconfig:<its name>" (tests/kube_fixture.py). SCORE_VERBOSE=1 lists what went wrong.
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

GROUPS = {"file": "files", "commit": "git refs", "branch": "git refs", "pr": "git refs", "env_var": "env vars",
          "constant": "constants", "function": "functions", "class": "classes", "field": "fields",
          "table": "tables / migrations", "migration": "tables / migrations", "route": "routes",
          "config": "config / k8s", "k8s": "config / k8s", "service": "services", "package": "packages",
          "test": "tests", "cli": "commands", "external": "external", "other": "other"}
PLAIN = re.compile(r"[A-Za-z][a-z]{2,}")


def norm(s):
    s = s.strip().strip("`").strip()
    s = re.sub(r"[.,;:]+$", "", s)
    s = re.sub(r"^(the|a|an) ", "", s, flags=re.I)
    s = re.sub(r"\(\)$", "", s)
    return (s.rstrip("/") if len(s) > 1 else s).lower()


def path_ok(gold, path):
    g, p = (gold or "").strip("/"), (path or "").strip("/")
    return bool(g and p) and (p == g or p.startswith(g + "/") or g.startswith(p + "/") and p.count("/") >= 2)


def main():
    result = json.load(open(sys.argv[1]))
    messages = json.load(open(sys.argv[2]))
    gold = [g for path in sys.argv[3:] for g in json.load(open(path))]
    store = next((f"{result['store']}/{n}" for n in ("signal.db", "referencer.db")
                  if os.path.exists(f"{result['store']}/{n}")), f"{result['store']}/signal.db")
    db = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    reading = result.get("reading")
    has_decisions = bool(db.execute("SELECT name FROM sqlite_master WHERE name = 'decision'").fetchone())
    decisions = defaultdict(dict)  # message -> norm(surface) -> decision
    if has_decisions:
        for m, s, v, p, ln, role, why, by in db.execute("SELECT message, surface, verdict, path, line, role, why, by "
                                                        "FROM decision"):
            decisions[m][norm(s)] = {"verdict": v, "path": p, "line": ln, "role": role, "why": why, "by": by}
    entities = {}
    for surface, kind, status, data, eid in db.execute("SELECT a.surface, e.kind, e.status, e.data, e.id FROM alias a "
                                                       "JOIN entity e ON e.id = a.entity_id"):
        first = db.execute("SELECT path FROM location WHERE entity_id = ? ORDER BY id LIMIT 1", (eid,)).fetchone()
        entities.setdefault(norm(surface), {"surface": surface, "kind": kind, "status": status,
                                            "data": json.loads(data or "{}"), "path": first[0] if first else None})
    root = result.get("searched") or ""
    hashes = [hashlib.sha1(t.encode("utf-8", "replace")).hexdigest()[:16] for t in messages]

    def shown(g):
        """What the entry's reply draws for it: (state, path, by)."""
        m = g.get("message")
        if not isinstance(m, int) or not 0 <= m < len(messages):
            return ("missed", None, None)
        text, key = messages[m], norm(g["surface"])
        h = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]
        d = decisions.get(h, {}).get(key)
        e = entities.get(key)
        if e is not None and e["kind"] in ("commit", "branch", "pr") and e["status"] == "resolved":
            return ("linked", e["path"], "rules")  # (a git ref Baude resolved itself keeps its link)
        if d is None:  # (carried from the nearest earlier reply the reader decided it in, else a later one)
            near = sorted((i for i, mh in enumerate(hashes) if (x := decisions.get(mh, {}).get(key)) is not None
                           and x["by"] == "reader"), key=lambda i: (i > m, abs(i - m)))
            d = decisions[hashes[near[0]]][key] if near else None
        if d is not None:
            if d["verdict"] == "link":
                return ("linked", d["path"], d["by"])
            if d["verdict"] == "plain":
                return ("plain", None, d["by"])
            return ("external" if d["why"] == "external" else "absent", None, d["by"])
        for label, target in re.findall(r"\[([^\]]+)\]\(([^)\s]+)\)", text):  # Baude's own file links
            path = re.sub(r":\d+(:\d+)?$", "", target)
            if norm(label) == key and "://" not in target and os.path.exists(os.path.join(root, path)):
                return ("linked", path, "native")
        e = entities.get(key)
        if e is None:
            span = re.sub(r":\d+(:\d+)?$", "", g["surface"].strip("`"))
            if "/" in span and os.path.exists(os.path.join(root, span)):
                return ("linked", span.rstrip("/"), "native")
            return ("missed", None, None)
        if e["data"].get("plain") or e["data"].get("check") == "due" or \
                (reading and has_decisions and PLAIN.fullmatch(e["surface"])):
            return ("plain", None, "rules")
        if e["status"] in ("resolved", "ambiguous"):
            if e["data"].get("context"):  # (a kubeconfig context: no place in the repository, a card)
                return ("linked", f"kubeconfig:{e['data']['context']}", "rules")
            return ("external", None, "rules") if e["kind"] == "external" else ("linked", e["path"], "rules")
        if e["status"] == "not_found":
            return ("absent", None, "rules")
        return ("missed", None, None)

    table, misses, by = defaultdict(Counter), defaultdict(list), Counter()
    for g in gold:
        group, exists = GROUPS.get(g.get("class"), "other"), g.get("exists")
        state, path, who = shown(g)
        t = table[group]
        t["n"] += 1
        if exists == "yes":
            t["in_repo"] += 1
            if state == "linked":
                t["linked"] += 1
                if path_ok(g.get("path"), path) or (g.get("class") in ("commit", "branch", "pr") and not g.get("path")):
                    t["right"] += 1
                    by[who] += 1
                else:
                    t["wrong"] += 1
                    misses[group].append(f"WRONG  {g['surface']!r} (reply {g['message']}) -> {path} [{who}]"
                                         f" (reference {g.get('path')})")
            elif state == "external" and g.get("class") == "external":
                t["right"] += 1
            else:
                misses[group].append(f"{state.upper():6} {g['surface']!r} (reply {g['message']}) (reference "
                                     f"{g.get('path')})")
        elif exists in ("proposed", "elsewhere", "no"):
            t["absent"] += 1
            if state == "absent":
                t["absent_ok"] += 1
            elif state == "linked":
                t["wrong"] += 1
                misses[group].append(f"WRONG  {g['surface']!r} (reply {g['message']}) -> {path} [{who}] (reference: "
                                     f"{exists})")
        elif exists == "external":
            t["external"] += 1
            if state == "external":
                t["external_ok"] += 1
            elif state == "linked":
                t["wrong_ext"] += 1
    pct = lambda a, b: f"{100 * a / b:5.1f}%" if b else "    –"  # noqa: E731
    print(f"{result['store'].split('/')[-1]}: {result.get('model') or 'no reader'}"
          f"{' (' + result['context'] + ')' if result.get('context') else ''}, evidence "
          f"{'on' if result.get('evidence', True) else 'off'}; {result.get('seconds')} s, ${result.get('cost', 0):.2f}")
    print(f"{'class':22} {'n':>4} {'in repo':>7} {'linked':>7} {'right':>7} {'wrong':>6} {'absent ok':>9} {'external':>8}")
    total = Counter()
    order = sorted(table, key=lambda k: -table[k]["n"])
    for group in order + ["ALL"]:
        t = table[group] if group != "ALL" else total
        if group != "ALL":
            total.update(t)
        print(f"{group:22} {t['n']:4} {t['in_repo']:7} {pct(t['linked'], t['in_repo']):>7} "
              f"{pct(t['right'], t['in_repo']):>7} {t['wrong']:6} {pct(t['absent_ok'], t['absent']):>9} "
              f"{pct(t['external_ok'], t['external']):>8}")
    print("right, by who placed it: " + ", ".join(f"{k} {v}" for k, v in by.most_common()))
    if os.environ.get("SCORE_VERBOSE"):
        for group in order:
            print(f"\n## {group}")
            for m in misses[group][:40]:
                print("  ", m)
    if os.environ.get("SCORE_JSON"):
        json.dump({g: dict(t) for g, t in list(table.items()) + [("ALL", total)]} | {"by": dict(by)},
                  open(os.environ["SCORE_JSON"], "w"), indent=1)


if __name__ == "__main__":
    main()
