#!/usr/bin/env python3
"""Score a Signal replay (tests/ref_replay.py) against a reference list of the entities in that session.

    tests/ref_score.py RESULT.json REFERENCE.json [REFERENCE.json...] [--native MESSAGES.json FOLDER]

A reference list is a JSON list of entities a reader may want to jump to, each {"surface": the text as written,
"class": file|function|class|field|constant|table|route|env_var|commit|config|service|package|cli|external|...,
"exists": yes|proposed|elsewhere|no|external, "path": where it really is (repository-relative), "line": ...}.
docs/signal-next.md says how the lists for two real sessions were made; they stay outside this
repository, since they describe another one. With --native (the replies' text as a JSON list of strings, and the
session's folder), names Baude's own file linker links count as linked too, and so does a name the reference lists
that Baude links as part of a longer backticked span in the same reply (`member_id` in
`{installation_id, member_id, name}`): that span's link is scored for it, and the table's last line counts these.

For each reference entity: was it looked up (or skipped as an unknown kind, or missed), linked (resolved or
several matches), linked to the right place (one of its locations is the reference path, or under it for a
folder / service), and is the first location (what a click opens) the right one? Entities not in the
repository (proposed, elsewhere, gone) are right when they end up "not found"; external tools are right when
they're marked external. SCORE_VERBOSE=1 lists the misses.
"""
import json
import re
import sys
from collections import Counter, defaultdict

GROUPS = {"file": "files", "commit": "git refs", "branch": "git refs", "pr": "git refs", "env_var": "env vars",
          "constant": "constants", "function": "functions", "class": "classes", "field": "fields",
          "table": "tables / migrations", "migration": "tables / migrations", "route": "routes",
          "config": "config / k8s", "k8s": "config / k8s", "service": "services", "package": "packages",
          "test": "tests", "cli": "commands", "external": "external", "other": "other"}


def norm(s):
    s = s.strip().strip("`").strip()
    s = re.sub(r"[.,;:]+$", "", s)
    s = re.sub(r"^(the|a|an) ", "", s, flags=re.I)
    s = re.sub(r"\(\)$", "", s)
    return s.rstrip("/") if len(s) > 1 else s


def path_ok(gold, locs):
    g = (gold or "").strip("/")
    if not g:
        return False
    for l in locs:
        p = (l.get("path") or "").strip("/")
        if p == g or p.startswith(g + "/") or g.startswith(p + "/") and p.count("/") >= 2:
            return True
    return False


def main():
    result = json.load(open(sys.argv[1]))
    gold = []
    args = sys.argv[2:sys.argv.index("--native")] if "--native" in sys.argv else sys.argv[2:]
    for path in args:
        gold += json.load(open(path))
    seen, items = set(), []
    for g in gold:  # one per surface
        key = norm(g.get("surface", ""))
        if key and key not in seen:
            seen.add(key)
            items.append(g)
    rows = {norm(r["surface"]): r for r in result["rows"]}
    # names Baude's own file linker already links, outside Signal: [text](existing path), and backticked
    # or bare paths that exist relative to the folder (messages file and folder given with --native)
    messages = None
    if "--native" in sys.argv:
        import os
        i = sys.argv.index("--native")
        messages, root = json.load(open(sys.argv[i + 1])), sys.argv[i + 2]
        for text in messages:
            for label, target in re.findall(r"\[([^\]]+)\]\(([^)\s]+)\)", text):
                path = re.sub(r":\d+(:\d+)?$", "", target)
                if "://" not in target and os.path.exists(os.path.join(root, path)):
                    rows.setdefault(norm(label), {"surface": label, "kind": "file", "status": "resolved",
                                                  "locations": [{"path": path}], "by": "native"})
            for span in re.findall(r"`([^`\n]+)`", text):
                path = re.sub(r":\d+(:\d+)?$", "", span.strip())
                if path and "/" in path and os.path.exists(os.path.join(root, path)):
                    rows.setdefault(norm(span), {"surface": span, "kind": "file", "status": "resolved",
                                                 "locations": [{"path": path.rstrip("/")}], "by": "native"})
    skipped = {norm(s) for s in result.get("skipped", {})}

    def in_span(g, key):
        """The row of a longer backticked span of code in the entity's own reply that has it as a name of its own
        and is linked ({installation_id, member_id, name} for member_id): the link a reader clicks on it. A path
        or a URL that has the word in it (services/beacon-runner, ?installation_id=) doesn't count."""
        m = g.get("message")
        if messages is None or not isinstance(m, int) or not 0 <= m < len(messages):
            return None
        word = re.compile(rf"(?:^|(?<=[\s{{(\[,])){re.escape(key)}(?=$|[\s,}})\]?:=.])")
        for span in re.findall(r"`([^`\n]+)`", messages[m]):
            r = rows.get(norm(span))
            if norm(span) != key and word.search(span) and r is not None and r["kind"] == "symbol" \
                    and r["status"] in ("resolved", "ambiguous"):
                return r
        return None
    via_span = []
    table = defaultdict(Counter)
    misses = defaultdict(list)
    for g in items:
        key, group = norm(g["surface"]), GROUPS.get(g.get("class"), "other")
        r = rows.get(key)
        if r is None and (r := in_span(g, key)) is not None:
            via_span.append(g["surface"])
        exists = g.get("exists")
        t = table[group]
        t["n"] += 1
        state = ("missed" if r is None and key not in skipped else "skipped" if r is None else
                 {"resolved": "linked", "ambiguous": "several"}.get(r["status"], r["status"]))
        t["st_" + state] += 1
        if exists == "yes":
            t["in_repo"] += 1
            if state in ("linked", "several"):
                t["linked"] += 1
                if path_ok(g.get("path"), r["locations"][:1]) or (g.get("class") in ("commit", "branch") and state == "linked"):
                    t["top"] += 1  # the first place offered is the right one
                if path_ok(g.get("path"), r["locations"]) or (g.get("class") in ("commit", "branch") and state == "linked"):
                    t["right"] += 1
                else:
                    t["wrong"] += 1
                    misses[group].append(f"WRONG {g['surface']!r} -> {[l['path'] for l in r['locations'][:2]]} "
                                         f"(gold {g.get('path')})")
            else:
                misses[group].append(f"{state.upper()} {g['surface']!r} (gold {g.get('path')})")
        elif exists in ("proposed", "elsewhere", "no"):
            t["absent"] += 1
            if r is not None and r["status"] == "not_found":
                t["absent_ok"] += 1
        elif exists == "external":
            t["external"] += 1
            if r is not None and r["kind"] == "external":
                t["external_ok"] += 1
    pct = lambda a, b: f"{100 * a / b:5.1f}%" if b else "    –"  # noqa: E731
    print(f"{'class':22} {'n':>4} {'in repo':>7} {'looked up':>9} {'linked':>7} {'right':>7} {'1st ok':>7} {'wrong':>6}"
          f" {'absent ok':>9} {'external':>8}")
    total = Counter()
    order = sorted(table, key=lambda k: -table[k]["n"])
    for group in order + ["ALL"]:
        t = table[group] if group != "ALL" else total
        if group != "ALL":
            total.update(t)
        looked = t["n"] - t["st_missed"] - t["st_skipped"]
        print(f"{group:22} {t['n']:4} {t['in_repo']:7} {pct(looked, t['n']):>9} {pct(t['linked'], t['in_repo']):>7}"
              f" {pct(t['right'], t['in_repo']):>7} {pct(t['top'], t['in_repo']):>7} {t['wrong']:6} {pct(t['absent_ok'], t['absent']):>9}"
              f" {pct(t['external_ok'], t['external']):>8}")
    if via_span:
        print(f"\nscored through the longer span they're written in: {len(via_span)} ({', '.join(via_span)})")
    extra = [r for k, r in rows.items() if k not in seen and r["status"] in ("resolved", "ambiguous")]
    print(f"\nlinked names not in the reference list: {len(extra)}"
          + (f" (e.g. {', '.join(r['surface'] for r in extra[:8])})" if extra else ""))
    if __import__("os").environ.get("SCORE_VERBOSE"):
        for group in order:
            print(f"\n## {group}")
            for m in misses[group][:25]:
                print("  ", m)


if __name__ == "__main__":
    main()
