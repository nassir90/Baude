"""signaltools — Signal's reader's tools (SignalTools): the socket the relay (SIGNAL_READER_MCP) talks to and what
the worker answers, then the worker's own jobs: a ref re-anchored, checked for staleness, read through the language
servers, validated and anchored.
"""
import hashlib
import json
import os
import re
import socket
import tempfile
import threading
import time

from baudelib.claude import claude_version
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from lookup import REF_KINDS, Ref, catalogue_hits, ref_key
from lookupkube import _KUBE_ACTION, kube_contexts, pg_databases, pg_servers, pg_servers_cached
from lookupgit import _git

from gi.repository import GLib  # noqa: E402

import refkinds  # noqa: E402
from widgets import load_settings, pretty_path, save_settings  # noqa: E402,F401
from refkinds import KUBE_KINDS, ref_kind, ref_kube, ref_quiet  # noqa: E402,F401
from markdownview import resolve_path  # noqa: E402,F401
from schemaview import db_label, pg_schema  # noqa: E402,F401

from signalrefs import (  # noqa: E402
    REF_AGENT_KINDS, REF_DECISION_FIELDS, REF_RULE_KINDS, REF_SENSITIVE, REF_TOOL_KINDS, SKILLS, SUBAGENTS, TOOLS,
    TOOL_NAMES, blob_sha, db_named, local_definitions, mcp_servers, mcp_tool_schemas, ref_candidates, ref_codeish,
    ref_redact, ref_secretish, ref_surface_in, tool_fetch,
)
from signalcode import REF_LSP_PACKAGES, RefCode, lsp_dir, lsp_fetch, lsp_node  # noqa: E402
from signalreader import SIGNAL_READER_PART, SIGNAL_READER_TOOLS, ref_seen, signal_reader_render  # noqa: E402
from toolcards import _first_line  # noqa: E402


class SignalTools:
    """Signal's reader's tools: a socket the relay (SIGNAL_READER_MCP) talks to; the worker answers. Then the
    worker's jobs: re-anchoring, stale refs, the language servers, validating and anchoring a ref."""

    # ---- the reader's tools: a socket the relay (SIGNAL_READER_MCP) talks to; the worker answers
    def tool_socket_dir(self):
        return os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()

    def tool_socket(self):
        """The socket the reader's tools come in on (started the first time it's asked for)."""
        if self._sock_path is None:
            path = os.path.join(self.tool_socket_dir(),
                                f"baude-signal-{os.getpid()}-{hashlib.sha1(self.dir.encode()).hexdigest()[:8]}.sock")
            try:
                os.unlink(path)
            except OSError:
                pass
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(path)
            os.chmod(path, 0o600)
            srv.listen(8)
            threading.Thread(target=self._serve, args=(srv,), daemon=True, name="baude-signal-tools").start()
            self._sock_path = path
        return self._sock_path

    def _serve(self, srv):
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve_one, args=(conn,), daemon=True).start()

    def _serve_one(self, conn):
        try:
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(1 << 16)
                if not chunk:
                    break
                data += chunk
            req = json.loads(data or b"{}")
            tool, args = req.get("tool"), req.get("args") or {}
            if tool == "_tools":
                res = {"tools": SIGNAL_READER_TOOLS}
            elif tool == "schema":
                res = self._tool_schema(args)  # (docker, not the glossary: here)
            else:
                box = {"event": threading.Event()}
                self.jobs.put(("tool", (tool, args, box), None))
                res = box["result"] if box["event"].wait(240) and "result" in box else \
                    {"error": "Baude is busy; try again in a moment"}
            conn.sendall((json.dumps(res, ensure_ascii=False, default=str) + "\n").encode())
        except (OSError, ValueError) as e:
            try:
                conn.sendall((json.dumps({"error": str(e)}) + "\n").encode())
            except OSError:
                pass
        finally:
            conn.close()

    def _job_tool(self, arg, _meta):
        tool, args, box = arg
        try:
            fn = {"known": self._tool_known, "lookup": self._tool_lookup, "decide": self._tool_decide,
                  "kube": self._tool_kube}.get(tool)
            box["result"] = fn(args) if fn else {"error": f"no tool {tool}"}
        except Exception as e:  # noqa: BLE001 (the reader is told; the worker goes on)
            box["result"] = {"error": f"{type(e).__name__}: {e}"}
        box["event"].set()

    @staticmethod
    def _places(ref, n=5):
        return [f"{l['path']}" + (f":{l['line']}" if l.get("line") else "") + f" ({l['role']}"
                + (f", {l['note']}" if l.get("note") else "") + ")" for l in ref.locations[:n]]

    def _tool_known(self, args):
        ai = self.agent
        reply = args.get("reply")
        number = {h: i + 1 for i, h in enumerate(ai.replies)} if ai is not None else {}
        out = []
        for name in [str(n) for n in (args.get("names") or [])][:60]:
            key = ref_key(name.strip("`"))
            item = {"name": name}
            ref = self._load(key)
            if ref is not None:
                item.update(kind=ref.kind, status=ref.status, places=self._places(ref), why=ref.reason or None,
                            note=ref.data.get("note"))
            decs = self._open().execute("SELECT message, verdict, path, line, role, note, why FROM decision WHERE "
                                        "checkout = ? AND scope = ? AND surface = ? ORDER BY at DESC LIMIT 4",
                                        (self.checkout, self.scope, key)).fetchall()
            if decs:
                item["decided"] = [{"reply": number.get(m), "verdict": v,
                                    **({"place": p + (f":{n}" if n else "") + (f" ({r})" if r else "")} if p else {}),
                                    **({"why": w} if w else {}), **({"note": nt} if nt else {})}
                                   for m, v, p, n, r, nt, w in decs]
            if ai is not None and ai.full and isinstance(reply, int):
                seen = ref_seen(ai.evidence, key, reply)
                if seen:
                    item["seen"] = [p + (f":{n}" if n else "") for p, n in seen[:5]]
            out.append(item)
        return {"names": out}

    def _tool_lookup(self, args):
        name = ref_key(str(args.get("name") or "").strip("`"))
        if not name:
            return {"error": "no name"}
        kind = REF_AGENT_KINDS.get(str(args.get("kind") or "").lower()) or ref_kind(name) or "symbol"
        if kind not in REF_RULE_KINDS:
            kind = "symbol"
        self._looked.add(name)
        ref = self._resolve(name, kind, self._repo_state(), {})
        return {"name": name, "kind": ref.kind, "status": ref.status, "places": self._places(ref, 12),
                "why": ref.reason or None}

    def _tool_kube(self, args):
        """The reader's view of the repository's Kubernetes config: the objects with a name like name (part of one; a
        pod's name finds its workload), of a kind, in a namespace, and the kubeconfig's contexts (names only); with
        none of those, an overview: the charts and their releases, the kinds defined, the contexts."""
        st = self._repo_state()
        idx = self._kube_index(st)
        want = ref_key(str(args.get("name") or "")).strip("`").lower()
        kind = str(args.get("kind") or "").strip()
        kind = KUBE_KINDS.get(kind.lower(), kind) if kind else None
        ns = str(args.get("namespace") or "").strip() or None

        def row(o, name, how=""):
            r = {"kind": o["kind"], "name": name, "place": f"{o['path']}:{o['line']}"}
            if o["namespace"] and not _KUBE_ACTION.search(o["namespace"]):
                r["namespace"] = o["namespace"]
            for k in ("chart", "release", "overlay"):
                if o.get(k):
                    r[k] = o[k]
            if how:
                r["how"] = how
            return r

        def ctx(c):
            return {k: c[k] for k in ("name", "cluster", "namespace", "user", "current") if c.get(k)}
        if not (want or kind or ns):
            kinds = {}
            for o in idx["objects"]:
                if o["names"]:
                    kinds[o["kind"]] = kinds.get(o["kind"], 0) + 1
            return {"charts": [{"chart": r or ".", "name": c["name"], "values": c["values_files"],
                                "releases": [x["release"] + (f" (namespace {x['namespace']})" if x["namespace"] else "")
                                             for x in c["releases"]]} for r, c in idx["charts"].items()],
                    "kinds": kinds, "contexts": [ctx(c) for c in kube_contexts()][:20],
                    "note": "give a name (or part of one), a kind or a namespace for the objects themselves"}
        rows, rendered = [], {(o["path"], o["line"]) for o in idx["objects"] if o["names"]}
        for o in idx["objects"]:
            if (kind and o["kind"] != kind) or (ns and o["namespace"] and o["namespace"] != ns) or \
                    (not o["names"] and (o["path"], o["line"]) in rendered):  # (a template a release renders: as rendered)
                continue
            names = [n for n, _how in o["names"]] or [o["raw"]]
            if not want or any(want in n.lower() for n in names):
                rows.append(row(o, next((n for n in names if want in n.lower()), names[0]),
                                "" if o["names"] else "a templated name no release renders"))
        if want and not rows:  # (a pod's name, or a name a template matches by its literal parts)
            rows = [row(o, n, how) for o, n, how in self._kube_find(want, kind, ns, idx)]
        contexts = [ctx(c) for c in kube_contexts() if not want or want in c["name"].lower()] if not kind else []
        out = {"objects": rows[:40], "contexts": contexts[:20]}
        if len(rows) > 40:
            out["more"] = len(rows) - 40
        if not rows and not contexts:
            out["why"] = "no object or context like that in this repository's manifests, charts, overlays or kubeconfig"
        return out

    def _kube_object_at(self, path, line, st):
        """Whether a Kubernetes object the repository defines has its name on that line (a place kube gave)."""
        return bool(line) and any(o["path"] == path and o["line"] == line for o in self._kube_index(st)["objects"])

    def _tool_schema(self, args):
        table, column = (args.get("table") or "").strip().lower(), (args.get("column") or "").strip().lower()
        if not table and not column:
            return {"error": "give a table or a column"}
        try:
            servers = pg_servers()
        except RuntimeError as e:
            return {"error": str(e)}
        found = []
        for s in servers:
            for db in pg_databases(s):
                key = (s["container"], db)
                if key not in self._schemas:
                    try:
                        self._schemas[key] = pg_schema(s, db)
                    except (RuntimeError, ValueError):
                        self._schemas[key] = {"tables": {}}
                for (ns, name), t in self._schemas[key]["tables"].items():
                    cols = [c["name"] for c in t["columns"]]
                    if (table and (name.lower() == table or table in name.lower())) or \
                            (column and any(c.lower() == column for c in cols)):
                        found.append({"server": s["container"], "database": db, "schema": ns, "table": name,
                                      "columns": [f"{c['name']} {c['type']}" for c in t["columns"]][:80]})
        return {"tables": found[:12]} if found else {"tables": [], "why": "no reachable database has it"}

    def _snap(self, full, line, surface):
        """The line nearest to line in full that has the name (its last part) as a word; None if none does."""
        last = re.split(r"[.:/]", re.sub(r"\(.*\)$", "", surface).strip("`/"))[-1] or surface
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                lines = f.read(self.BLOB_LIMIT).splitlines()
        except OSError:
            return None
        pat = re.compile(r"(?<![\w$])" + re.escape(last) + r"(?![\w$])")
        hits = [i + 1 for i, l in enumerate(lines) if pat.search(l)]
        if not hits:
            return None
        return min(hits, key=lambda n: abs(n - (line or 1)))

    def _searched(self, surface):
        """Whether the name was searched for: a lookup, the reader's own Grep, or the rules looked and gave up."""
        ai = self.agent
        word = re.split(r"[.:/ ]", surface)[-1].lower() or surface.lower()
        if surface in self._looked or (ai is not None and any(word in p.lower() for p in ai.searches)):
            return True
        old = self._load(surface)
        return old is not None and old.status == "not_found"

    def _tool_decide(self, args):
        """The reader's decisions, checked one by one; kept for their reply."""
        ai = self.agent
        if ai is None:
            return {"error": "the reader is off"}
        st, kept, rejected, refs, rows = self._repo_state(), 0, [], [], []
        for i, d in enumerate(args.get("decisions") or []):
            if not isinstance(d, dict):
                continue
            try:
                n = int(d.get("reply"))
            except (TypeError, ValueError):
                rejected.append({"i": i, "why": "reply must be a number"})
                continue
            name = str(d.get("name") or "").strip().strip("`")
            if not 1 <= n <= len(ai.texts):
                rejected.append({"i": i, "name": name, "why": f"there is no reply {n}"})
                continue
            surface = ref_surface_in(ref_key(name), [ai.texts[n - 1]])
            if not surface or ref_secretish(surface):
                rejected.append({"i": i, "name": name, "why": f"reply {n} doesn't write {name!r}"})
                continue
            verdict, note = d.get("verdict"), _first_line(str(d.get("note") or ""), 160)
            kind = REF_AGENT_KINDS.get(str(d.get("kind") or "").lower())
            old = self._load(surface)
            if old is not None and old.status == "resolved" and (old.kind in ("commit", "branch", "pr") or
                                                                 old.data.get("context")):
                kept += 1  # (a git ref or a kubeconfig context Baude resolved itself: its link stands)
                continue
            if verdict == "link" and kind in ("commit", "branch", "pr"):  # (one the rules missed: git settles it)
                ref = self._resolve(surface, kind, st, {})
                if ref.status != "resolved":
                    rejected.append({"i": i, "name": name, "why": f"git has no {kind} {surface!r}"})
                    continue
                ref.attempted = True
                self._save(ref, st["fp"])
                refs.append(ref)
                kept += 1
                continue
            row = {"message": ai.replies[n - 1], "surface": surface, "verdict": verdict,
                   "kind": REF_AGENT_KINDS.get(str(d.get("kind") or "").lower(), str(d.get("kind") or "")[:20] or None),
                   "path": None, "line": None, "role": None, "note": note, "why": None, "by": "reader",
                   "at": time.time()}
            if verdict == "link":
                rel = self._relpath(str(d.get("path") or ""))
                full = os.path.join(self.root, rel) if rel else None
                if not full or not os.path.exists(full):
                    rejected.append({"i": i, "name": name, "why": f"{d.get('path')!r} isn't in this checkout ("
                                     f"{self.root}); not_in_code with why elsewhere, if it's somewhere else"})
                    continue
                line = None
                if os.path.isfile(full):
                    try:
                        line = int(d.get("line")) if d.get("line") else None
                    except (TypeError, ValueError):
                        line = None
                    if (d.get("role") or "") not in ("file", "folder") and not self._kube_object_at(rel, line, st):
                        line = self._snap(full, line, surface)  # (a Kubernetes object's place, as kube gave it, stands)
                        if line is None:
                            rejected.append({"i": i, "name": name, "why": f"{rel} doesn't have {surface!r}: find "
                                             "the file that does"})
                            continue
                row.update(path=rel, line=line, role=(d.get("role") or ("folder" if os.path.isdir(full) else
                                                                         "definition"))[:20])
                refs.append(self._reader_entity(surface, row, st))
            elif verdict == "not_in_code":
                why = d.get("why") if d.get("why") in ("proposed", "external", "elsewhere", "not_found") else "not_found"
                if why != "external" and not self._searched(surface):
                    rejected.append({"i": i, "name": name, "why": f"search for {surface!r} first (lookup, or Grep): "
                                     "it may be in the code"})
                    continue
                row["why"] = why
                refs.append(self._absent_entity(surface, row, old, st))
            elif verdict == "plain":
                if ref_codeish(surface) and not refkinds.ref_cloud_provider(surface):  # (Fly.io, DigitalOcean: plain)
                    rejected.append({"i": i, "name": name, "why": f"{surface!r} is written as code, not English: "
                                     "link it, or not_in_code with why proposed, external, elsewhere or not_found"})
                    continue
            else:
                rejected.append({"i": i, "name": name, "why": "verdict is link, plain or not_in_code"})
                continue
            self._store_decision(row)
            rows.append(row)
            kept += 1
        self._open().commit()
        self._post([r for r in refs if r is not None])
        if rows:
            GLib.idle_add(self._apply_decisions, rows)
            v = __import__("collections").Counter(r["verdict"] for r in rows)
            self._tell(entry=f"{ai.model.capitalize()} decided {kept}: {v.get('link', 0)} links, {v.get('plain', 0)} "
                             f"plain words, {v.get('not_in_code', 0)} not in the code" +
                             (f"; {len(rejected)} rejected" if rejected else ""))
            GLib.idle_add(self.reader_said, "glossary", {"rows": rows, "rejected": len(rejected)})
        answer = {"kept": kept, "rejected": rejected}
        if (left := self._undecided(ai)):
            answer["undecided"] = left
        return answer

    def _undecided(self, ai, limit=40):
        """The names in the part being read that have no decision yet (the reply's own, or one carried from an earlier
        reply): its backticked names and the rules' (routes in code blocks), but for those the rules placed in one
        file, the git refs Baude resolved, Claude Code's tools, and paths that already link."""
        part = ai.part
        if not part:
            return []
        db, left = self._open(), []
        decided = {}
        for m, surface in db.execute("SELECT message, surface FROM decision WHERE by = 'reader' AND checkout = ? AND "
                                     "scope = ?", (self.checkout, self.scope)):
            decided.setdefault(surface, set()).add(m)
        for n in range(part["a"], min(part["b"], len(ai.texts)) + 1):
            text, before = ai.texts[n - 1], set(ai.replies[:n])
            names = dict(ref_candidates(text))
            for s, c in catalogue_hits(text, self.catalogue).items():  # (the names known to be there, too)
                names.setdefault(s, c)
            for raw in re.findall(r"`([^`\n]{2,80})`", re.sub(r"(?ms)^\s*```.*?(?:^\s*```[^\n]*$|\Z)", "", text)):
                key = ref_key(raw)
                if key not in names and not resolve_path(raw) and not ref_secretish(key) and \
                        re.search(r"[A-Za-z]", key) and not re.fullmatch(r"\d[\d.,:%\sA-Za-z]*", key):
                    names[key] = (ref_kind(key) or "name", False)
            for surface, (kind, _strong) in names.items():
                if kind in REF_TOOL_KINDS or decided.get(surface, set()) & before or \
                        refkinds.ref_cloud_provider(surface):
                    continue
                ref = self._load(surface)
                if ref is not None and ref.status == "resolved" and (ref.kind in ("commit", "branch", "pr") or
                                                                     ref.data.get("context") or
                                                                     len({l["path"] for l in ref.locations}) == 1):
                    continue
                left.append(f"reply {n}: {surface}")
                if len(left) >= limit:
                    return left + ["(and more)"]
        return left

    def _absent_entity(self, surface, row, old, st):
        """The glossary's entry for a name the reader says isn't in the code: an outside tool (external), or not found
        with its reason; made only where the rules have nothing better (what it means in a reply is the decision)."""
        if old is not None and old.status not in ("pending", "not_found"):
            return None
        if row["why"] == "external":
            ref = Ref(surface, "external", "resolved", surface, data={"by": "reader", "note": row["note"] or
                                                                      wording.say("references.external")})
        else:
            why = {"proposed": "proposed in the conversation, not written yet", "elsewhere": "somewhere else (another "
                   "checkout, branch or machine)"}.get(row["why"], "not in this folder's code")
            ref = Ref(surface, row["kind"] if row["kind"] in REF_KINDS else (old.kind if old else ref_kind(surface) or
                                                                            "symbol"), "not_found",
                      reason=why + (f": {row['note']}" if row["note"] else ""), data={"by": "reader"})
        ref.attempted = True
        self._save(ref, st["fp"])
        return ref

    def _relpath(self, path):
        """A path the reader gave, relative to the checkout (absolute ones from another checkout of it too)."""
        path = path.strip().strip("`")
        if not path:
            return None
        if os.path.isabs(path):
            for top in (self.root, *self.other_checkouts()):
                if path.startswith(top.rstrip("/") + os.sep) or path == top:
                    return os.path.relpath(path, top)
            return None
        return os.path.normpath(path)

    def _reader_entity(self, surface, row, st):
        """The glossary's entry for a name the reader linked: made if the rules had none, its place added if new."""
        loc = {"role": row["role"], "path": row["path"], "line": row["line"], "resolver": "reader",
               "note": row["note"] or ""}
        self._anchor(loc, st)
        old = self._load(surface)
        if old is None or old.status in ("not_found", "pending"):
            ref = Ref(surface, row["kind"] if row["kind"] in REF_KINDS else (old.kind if old else "symbol"), "resolved",
                      f"{row['path']}::{surface}", data={"by": "reader", "note": row["note"]}, locations=[loc])
        elif any(l["path"] == loc["path"] for l in old.locations):
            return None
        else:
            ref = old
            ref.locations.append(loc)
        ref.attempted = True
        self._save(ref, st["fp"])
        return ref

    def _store_decision(self, row):
        """A decision for one reply; the reader's are kept over the evidence's (worker)."""
        db = self._open()
        old = db.execute("SELECT by FROM decision WHERE checkout = ? AND scope = ? AND message = ? AND surface = ?",
                         (self.checkout, self.scope, row["message"], row["surface"])).fetchone()
        if old and old[0] == "reader" and row["by"] != "reader":
            return False
        db.execute(f"INSERT OR REPLACE INTO decision (checkout, scope, {', '.join(REF_DECISION_FIELDS)}) VALUES "
                   f"(?, ?, {', '.join('?' * len(REF_DECISION_FIELDS))})",
                   (self.checkout, self.scope, *(row[f] for f in REF_DECISION_FIELDS)))
        return True

    def _job_decisions_load(self):
        rows = [dict(zip(REF_DECISION_FIELDS, r)) for r in
                self._open().execute(f"SELECT {', '.join(REF_DECISION_FIELDS)} FROM decision WHERE checkout = ? AND "
                                     f"scope = ?", (self.checkout, self.scope))]
        if rows:
            GLib.idle_add(self._apply_decisions, rows)

    def _job_evidence(self, arg, _meta):
        """What Claude read or grepped before a reply, as decisions where it's clear: a name shaped like code (not a
        plain word) that it saw in exactly one file is linked there, for that reply. The reader may decide otherwise."""
        if not load_settings().get("signal_evidence", True):
            return
        evidence, rows = arg["evidence"], []
        if not evidence:
            return
        db = self._open()
        for n, (text, message) in enumerate(arg["replies"], arg.get("first", 1)):  # (those it may have changed for)
            for surface, (kind, strong) in ref_candidates(text).items():
                if re.fullmatch(r"[A-Za-z][a-z]+", surface) or kind in ("commit", "branch", "pr", "tool"):
                    continue
                seen = ref_seen(evidence, surface, n)
                if not seen or db.execute("SELECT 1 FROM decision WHERE checkout = ? AND scope = ? AND message = ? AND "
                                          "surface = ?", (self.checkout, self.scope, message, surface)).fetchone():
                    continue
                ref = self._load(surface)
                if ref is not None and ref.status == "resolved" and len({l["path"] for l in ref.locations}) == 1:
                    continue  # (the rules know it)
                if ref is not None and ref.status == "ambiguous":  # one of the rules' matches: the one Claude saw
                    paths = {l["path"] for l in ref.locations}
                    seen = [(p, l) for p, l in seen if p in paths]
                if len({p for p, _l in seen}) != 1:
                    continue
                path, line = next(((p, l) for p, l in seen if l), seen[0])
                if not os.path.exists(os.path.join(self.root, path)):
                    continue
                row = {"message": message, "surface": surface, "verdict": "link", "kind": kind, "path": path,
                       "line": line, "role": "seen", "note": "where Claude looked", "why": None, "by": "session",
                       "at": time.time()}
                if self._store_decision(row):
                    rows.append(row)
        db.commit()
        if rows:
            GLib.idle_add(self._apply_decisions, rows)
            self._tell(entry=f"From what Claude read and grepped: {len(rows)} names placed in their replies")

    def _job_reader_part(self, arg, _meta):
        """A part, as the reader gets it: what happened, then the names the rules found in its replies with their
        answers (and, reading in full, the places Claude saw that mention them)."""
        ai = self.agent
        if ai is None:
            return
        lines = []
        for ev in arg["events"]:
            if ev[0] != "reply":
                continue
            cands = list(ref_candidates(ev[1]).items())[:30]
            if not cands:
                continue
            bits = []
            for surface, (kind, _strong) in cands:
                ref = self._load(surface)
                if kind == "k8s" and ref is not None and ref.status == "not_found" and ref_quiet(surface):
                    continue  # (a kebab-case name that's no object here: as likely a package's, so it tells nothing)
                if ref is None or ref.status == "pending":
                    bits.append(f"`{surface}` (not looked up)")
                elif ref.status == "not_found":
                    bits.append(f"`{surface}`: not found ({_first_line(ref.reason, 60)})")
                else:
                    first = ref.locations[0] if ref.locations else None
                    where = (first["path"] + (f":{first['line']}" if first.get("line") else "")) if first else ""
                    many = f"{len(ref.locations)} matches, first " if ref.status == "ambiguous" else ""
                    bits.append(f"`{surface}`: {many}{where}".rstrip(": "))
                if arg["full"]:
                    seen = ref_seen(ai.evidence, surface, ev[2])
                    if seen:
                        bits[-1] += " — Claude saw it at " + ", ".join(p + (f":{n}" if n else "") for p, n in seen[:2])
            lines.append(f"- reply {ev[2]}: " + "; ".join(bits))
        names = ("The backticked names Baude's rules found (and the routes in code blocks), with their answers "
                 "(decide what each means there; the names written in prose are yours to add):\n" + "\n".join(lines) +
                 "\n") if lines else \
            "Baude's rules found no backticked names here: the names are yours to find.\n"
        if arg.get("glossary") and arg["a"] > 1:  # (read on its own: the glossary stands in for the replies before)
            names += "\n" + self._earlier_decisions(arg, ai) + "\n"
        text = SIGNAL_READER_PART.format(k=arg["k"], a=arg["a"], b=arg["b"],
                                      events=signal_reader_render(arg["events"], arg["full"]), names=names)
        GLib.idle_add(ai.send, arg["k"], arg["a"], arg["b"], text)

    def _earlier_decisions(self, arg, ai, limit=80):
        """The glossary as a part read on its own needs it: the reader's latest decision before the part for each
        name its replies write."""
        texts = "\n".join(ev[1] for ev in arg["events"] if ev[0] == "reply")
        number = {h: i + 1 for i, h in enumerate(ai.replies)}
        latest = {}
        for m, surface, verdict, path, line, role, why, note in self._open().execute(
                "SELECT message, surface, verdict, path, line, role, why, note FROM decision WHERE by = 'reader' AND "
                "checkout = ? AND scope = ?", (self.checkout, self.scope)):
            n = number.get(m)
            if n is None or n >= arg["a"] or (surface in latest and latest[surface][0] > n) or surface not in texts:
                continue
            latest[surface] = (n, verdict, path, line, role, why, note)
        bits = []
        for surface, (n, verdict, path, line, role, why, note) in sorted(latest.items(), key=lambda x: -x[1][0]):
            if not re.search(r"(?<![\w`-])" + re.escape(surface) + r"(?![\w`-])", texts):
                continue
            what = (f"{path}" + (f":{line}" if line else "") + (f" ({role})" if role else "")) if verdict == "link" else \
                "plain English" if verdict == "plain" else f"not in the code ({why})"
            bits.append(f"`{surface}` (reply {n}): {what}" + (f" — {_first_line(note, 60)}" if note else ""))
            if len(bits) >= limit:
                break
        if not bits:
            return "Decided in earlier replies, for names in this part: none."
        return "Decided in earlier replies, for names in this part:\n" + "\n".join(f"- {b}" for b in bits)

    def _job_tools(self, _arg, _meta):
        """The cached tool schemas, this folder's subagent and skill definitions, and (with your OK) the tools of
        the MCP servers configured for it."""
        if self.halted:
            return
        if not TOOLS.loaded:
            TOOLS.load()
        agents, skills = local_definitions(self.root)
        GLib.idle_add(self._locals_ready, agents, skills)
        servers = {n: c for n, c in mcp_servers(self.root).items() if c.get("command")}
        if not servers:
            return
        on = load_settings().get("signal_mcp_schemas")
        if on is None and "mcp" not in self.tools_said:
            self.tools_said.add("mcp")
            GLib.idle_add(self._ask, "mcp", servers)
        elif on:
            for name, conf in servers.items():
                self._tell(activity=f"Reading the tools of the MCP server {name}…")
                tools = mcp_tool_schemas(conf, self.root)
                TOOLS.add_mcp(name, tools)
                self._tell(entry=f"MCP server {name}: {len(tools)} tools" if tools else
                           f"MCP server {name}: no tools listed (it didn't start, or isn't stdio)")
            GLib.idle_add(self._tools_ready)

    def _locals_ready(self, agents, skills):
        SUBAGENTS.clear()
        SUBAGENTS.update(agents)
        SKILLS.clear()
        SKILLS.update(skills)
        self.rescan_tools()
        return False

    def _want_tools(self):
        """A tool was named and its schema isn't here: fetch it if you said yes, ask you if you haven't said.
        Not when this Claude Code's schemas are here already (a tool they don't have isn't in Anthropic's types)."""
        if TOOLS.loaded and TOOLS.cli == claude_version():
            return
        on = load_settings().get("signal_tool_schemas")
        if on and "fetch" not in self.tools_said:
            self.tools_said.add("fetch")
            self._tell(activity="Fetching the tool schemas from Anthropic…")
            ok, what = tool_fetch(claude_version())
            self._tell(entry=f"Tool schemas: {what}")
            if ok and TOOLS.load():
                GLib.idle_add(self._tools_ready)
        elif on is None and "asked" not in self.tools_said:
            self.tools_said.add("asked")
            GLib.idle_add(self._ask, "tools", claude_version())

    def _job_tools_fetch(self, _arg, _meta):
        self._want_tools()

    def _tools_ready(self):
        """Schemas arrived: the tools named so far are looked up again, and earlier replies looked at for the
        tools they name (main loop)."""
        self.refresh([s for s, r in self.refs.items() if r.kind == "tool"])
        self.rescan_tools()
        return False

    def _ask(self, topic, detail):
        for fn in list(self.question_listeners)[:1]:  # (one window asks)
            fn(topic, detail)
        return False

    def set_tool_schemas(self, on):
        """Your answer about fetching tool schemas (the question, or the Signal page's switch)."""
        save_settings(signal_tool_schemas=on)
        if on:
            self.tools_said.discard("fetch")
            self.jobs.put(("tools_fetch", None, None))
        self.status_changed()

    def set_mcp_schemas(self, on):
        save_settings(signal_mcp_schemas=on)
        if on:
            self.jobs.put(("tools", None, None))

    def _tool(self, surface, fetch=True):
        """A tool by name ("Agent", "the Agent tool", mcp__server__tool): its schema card, from the catalogue
        (Anthropic's SDK types and docs, or an MCP server's tools/list), else what its calls show."""
        name = re.sub(r"\s+tools?$", "", re.sub(r"^(the|a|an)\s+", "", surface.strip("`").strip(), flags=re.I))
        info = TOOLS.get(name)
        if info is None and not TOOLS.loaded:  # (the cache first)
            TOOLS.load()
            info = TOOLS.get(name)
        if info is None and fetch and not name.startswith("mcp__"):
            self._want_tools()
            info = TOOLS.get(name)
        if info is None and name not in TOOL_NAMES:
            return Ref(surface, "tool", "not_found", reason=f"no tool named {name} in this Claude Code")
        data = {**(info or {}), "tool": name, "phrase": surface != name, "cli": TOOLS.cli, "sdk": TOOLS.sdk}
        if info is None:
            data["source"] = "calls"
            if name.startswith("mcp__"):
                data["server"] = name.split("__")[1]
        return Ref(surface, "tool", "resolved", name, data=data)

    def _definition(self, surface, kind, table):
        """A subagent type or a skill: its definition file (a built-in subagent type has none)."""
        name = surface.strip("`").strip()
        info = table.get(name)
        if info is None:
            return Ref(surface, kind, "not_found", reason=f"no {REF_KINDS[kind][0].lower()} named {name} here")
        locs = []
        if info.get("path"):
            path = info["path"]
            rel = os.path.relpath(path, self.root) if path.startswith(self.root + os.sep) else path
            locs.append({"role": "definition", "path": rel, "line": 1, "resolver": "definitions"})
        return Ref(surface, kind, "resolved", name, data={**info, "phrase": False}, locations=locs)

    def _job_reanchor(self, arg, _meta):
        surface, index, line = arg
        ref, st = self._load(surface), self._repo_state()
        if ref is None or index >= len(ref.locations):
            return
        ref.locations[index]["line"] = line
        self._anchor(ref.locations[index], st)
        if ref.status == "stale":
            ref.status = "resolved"
        self._save(ref, st["fp"])
        self._open().commit()
        self._post([ref])

    def _job_stale(self, surface, _meta):
        ref = self._load(surface)
        if ref is not None and ref.status != "stale":
            ref.status = "stale"
            self._save(ref, ref.fingerprint)
            self._open().commit()
            self._post([ref])

    def _job_lsp(self, on, _meta):
        if not on:
            if self.code is not None:
                self.code.stop()
            self._tell(entry="Language servers off: code names are found by search")
        elif self._code() is not None:  # there already (else _code fetches them, then looks again)
            GLib.idle_add(self._lsp_ready)

    def _code(self):
        """The language servers, if Signal may use them and they're installed (worker thread); else None, and
        the first time one is wanted, the window asks once whether to fetch them (or, if you said yes, they're
        fetched). Without them, code names are found by search."""
        on = load_settings().get("signal_lsp")
        if on is False:
            return None
        if self.code is None:
            self.code = RefCode(self)
        if self.code.ready():
            return self.code
        if self.halted:  # (looking up here alone: what's installed only, nothing fetched or asked)
            return None
        if not lsp_node():
            if "no node" not in self.lsp_said:
                self.lsp_said.add("no node")
                from baudelib import deps
                self._tell(entry=deps.say(deps.info("node", here=False), "side_panel.signal"))
        elif on is None and "asked" not in self.lsp_said:
            self.lsp_said.add("asked")
            GLib.idle_add(self._ask_lsp)
        elif on and "fetch" not in self.lsp_said:
            self.lsp_said.add("fetch")
            threading.Thread(target=self._fetch_lsp, daemon=True, name="baude-lsp-fetch").start()
        return None

    def _fetch_lsp(self):
        self._tell(entry="Fetching the language servers: " + ", ".join(f"{n} {v}" for n, v in REF_LSP_PACKAGES)
                         + f" into {pretty_path(lsp_dir())}")
        t0 = time.monotonic()
        ok, what = lsp_fetch()
        self._tell(entry=f"Language servers: {what} ({time.monotonic() - t0:.0f} s)")
        if ok:
            if self.code is not None:
                self.code.checked = 0
            GLib.idle_add(self._lsp_ready)

    def _validate(self, ref, st, meta):
        """A known name, mentioned again: move its locations with the code, or mark it stale."""
        if ref.kind == "branch":  # a branch moves on: look it up again when the repository has
            return ref if ref.fingerprint == st["fp"] else self._resolve(ref.surface, "branch", st, meta)
        if ref.kind not in ("file", "env", "constant", "route", "k8s", "config"):
            return ref
        lost = False
        for loc in ref.locations:
            _full, line, how = self.locate(loc)
            if how == "moved":
                loc["line"] = line
                self._anchor(loc, st)
            lost = lost or how == "lost"
        if not lost:
            if ref.status == "stale":
                ref.status = "resolved" if len(ref.locations) == 1 or ref.kind != "file" else "ambiguous"
            return ref
        cited = ref.kind == "file" and any(loc.get("line") for loc in ref.locations)
        if not cited:  # found again by name: the cheap tiers
            again = self._resolve(ref.surface, ref_kind(ref.surface) or ref.kind, st, meta)
            if again.status in ("resolved", "ambiguous"):
                return again
        ref.status = "stale"  # a cited line that's gone: keep it, so it can be opened as it was
        return ref

    def _resolve(self, surface, kind, st, meta, keep=True):
        """What a name points at (kind: what it looks like). keep False (looked up while Signal isn't running):
        nothing copied into .baude, and a tool's schema only if it's been fetched before."""
        if kind == "commit":
            ref = self._commit(surface)
        elif kind == "branch":
            ref = self._branch(surface) or self._file(surface, st, meta)
        elif kind == "pr":
            ref = self._pr(surface)
        elif kind == "env":
            ref = self._name(surface, st)
        elif kind == "route":
            ref = self._route(surface, st)
        elif kind == "cli":
            ref = self._cli(surface, st, meta)
        elif kind == "symbol":
            ref = self._symbol(surface, st, meta)
        elif kind == "k8s":
            ref = self._kube(surface, st, meta)
            if ref.status == "not_found" and "/" in surface and (ref_kube(surface) or ("",))[0] == "object":
                other = self._branch(surface) or self._file(surface, st, meta)  # (svc/app is a folder too)
                ref = other if other.status != "not_found" else ref
        elif kind == "config":
            ref = self._kube(surface, st, meta) if ref_kube(surface) else self._config(surface, surface, st) or \
                Ref(surface, "config", "not_found", reason="no YAML key or Helm helper has this name")
        elif kind == "tool":
            ref = self._tool(surface, fetch=keep)
        elif kind in ("subagent", "skill"):
            ref = self._definition(surface, kind, SUBAGENTS if kind == "subagent" else SKILLS)
        else:
            ref = self._file(surface, st, meta)
        if ref.status == "not_found" and kind in ("symbol", "env") and re.fullmatch(r"[A-Za-z_]\w{2,}", surface):
            ref = self._live_db_name(surface) or ref  # (built at run time: f"e_{entity}", say)
        for loc in ref.locations:
            self._anchor(loc, st, keep)
        ref.attempted = True
        return ref

    def _live_db_name(self, surface):
        """A Ref for a name no code defines that a running database has: a table or collection (or an entity's
        e_<surface> / a relation's r_<surface>), or a database. Clicked, it opens the schema diagram there."""
        try:
            hits = db_named(surface)
        except Exception:  # noqa: BLE001 (Docker or kubectl gone: nothing to say)
            return None
        if not hits:
            return None
        container, db, table = hits[0]
        servers = {s["container"]: s for s in pg_servers_cached()}
        engine = "ArangoDB" if (servers.get(container) or {}).get("engine") == "arango" else "Postgres"
        label = db_label(db)
        what = (f"{'Collection' if engine == 'ArangoDB' else 'Table'} {table} in {label}" if table else
                f"Database {label}") + f" ({engine}, {container})" + \
            (wording.say("lookup.and_more", n=len(hits) - 1) if len(hits) > 1 else "")
        live = {"table": table, "database": db, "container": container, "engine": engine}
        return Ref(surface, "symbol", "resolved", table or db, data={"db": live, "note": what})

    def _anchor(self, loc, st, keep=True):
        """Record how a location's file looked when it was read: its hash, git state and the lines around it.
        A version that isn't committed is kept in .baude/blobs (unless the file may hold secrets, or keep is False:
        looked up while Signal isn't running). One on another branch (its rev) as that branch has it."""
        if loc.get("rev"):
            data = _git(self.top, "show", f"{loc['rev']}:{loc['path']}", raw=True) if self.top else None
            loc.update(state="branch", commit_sha=(_git(self.top, "rev-parse", loc["rev"]) or "").strip() or None,
                       branch=loc["rev"], resolved_at=time.time(), blob=blob_sha(data) if data else None,
                       snippet=None, snippet_start=None)
            if data and loc.get("line") and not REF_SENSITIVE.search(loc["path"]):
                lines = data.decode("utf-8", "replace").splitlines()
                start = max(1, loc["line"] - 3)
                loc["snippet"], loc["snippet_start"] = "\n".join(lines[start - 1:loc["line"] + 3]), start
            return
        full = os.path.join(self.root, loc["path"])
        xy = st["status"].get(loc["path"])
        state = ("committed" if xy is None else "untracked" if xy == "??" else
                 "unstaged" if xy[1] != " " else "staged") if self.top else "no git"
        loc.update(state=state, commit_sha=st["head"], branch=st["branch"], resolved_at=time.time(),
                   blob=None, snippet=None, snippet_start=None)
        if os.path.isdir(full):
            return
        try:
            with open(full, "rb") as f:
                data = f.read(self.BLOB_LIMIT + 1)
        except OSError:
            return
        if len(data) > self.BLOB_LIMIT:
            return
        loc["blob"] = blob_sha(data)
        sensitive = bool(REF_SENSITIVE.search(loc["path"]))
        if loc.get("line"):
            lines = data.decode("utf-8", "replace").splitlines()
            start = max(1, loc["line"] - 3)
            loc["snippet"] = "\n".join(ref_redact(s) if sensitive else s for s in lines[start - 1:loc["line"] + 3])
            loc["snippet_start"] = start
        if state != "committed" and not sensitive and keep:
            path = os.path.join(self.dir, "blobs", loc["blob"])
            if not os.path.exists(path):
                import zlib
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path + ".tmp", "wb") as f:
                    f.write(zlib.compress(data))
                os.replace(path + ".tmp", path)
