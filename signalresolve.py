"""signalresolve — how Signal settles a ref (SignalResolve): the resolvers, each returning a Ref whose locations are
anchored afterwards, spans of more than a name, and Kubernetes' manifests, charts, overlays and kubeconfig.
"""
import itertools
import json
import os
import re
import shutil
import time

from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)
from lookup import Ref, ref_key
from lookupkube import _KUBE_ACTION, kube_contexts, kube_made_as, kube_running, kubeconfig_paths, ref_yaml_path
from lookupgit import GITHUB, _git, github_data

from refkinds import _KUBE_PASCAL, _REF_METHODS, ref_commit_range, ref_kube, ref_pr_info, ref_span  # noqa: E402,F401
from gitview import _gh_json, github_repo  # noqa: E402,F401

from signalrefs import (
    KUBE_ORDER, KUBE_WORKLOADS, REF_CODE_PATHS, REF_SENSITIVE, _REF_TYPING, kube_pod_owners, ref_container,
    ref_def_role, ref_excerpt_re, ref_in_order, ref_is_test, ref_name_role, ref_shape_blocks,
)
from signalcode import ref_py_scope, ref_same_name


class SignalResolve:
    """Signal's resolvers (worker thread), spans of more than a name, and Kubernetes."""

    # ---- resolvers (worker thread): each returns a Ref; its locations are anchored afterwards
    def _file(self, surface, st, meta):
        m = re.fullmatch(r"(.*?)(?::(\d+)(?::\d+)?)?", surface)
        path, line = m.group(1), int(m.group(2)) if m.group(2) else None
        files, dirs, by_name = self._files(st)
        want = path[2:] if path.startswith("./") else path
        is_dir, want = want.endswith("/"), want.rstrip("/")
        expanded = os.path.expanduser(want)
        here = os.path.relpath(os.path.join(self.folder, want), self.root)
        if os.path.isabs(expanded):
            hits = [expanded]
        elif os.path.exists(os.path.join(self.root, want)):  # exactly that path: from the top, or the chat's folder
            hits = [os.path.normpath(want)]
        elif not here.startswith("..") and os.path.exists(os.path.join(self.root, here)):
            hits = [here]
        elif is_dir:
            hits = [d for d in dirs if d == want or d.endswith("/" + want)]
        elif "/" in want:
            hits = [f for f in files if f == want or f.endswith("/" + want)] or \
                   [d for d in dirs if d == want or d.endswith("/" + want)]
        else:  # a name: files with it, else folders with it (recipes.tsp/ is a folder)
            hits = list(by_name.get(want, [])) or [d for d in dirs if d.rsplit("/", 1)[-1] == want]
        if not hits and "/" in want and not want.startswith(".."):  # folders left out: applab-api/quick_edit.py
            parts = want.split("/")
            pool = dirs if is_dir else [f for f in files if f.rsplit("/", 1)[-1] == parts[-1]]
            hits = [f for f in pool if f.rsplit("/", 1)[-1] == parts[-1] and ref_in_order(parts[:-1], f.split("/")[:-1])]
        hits = sorted((h for h in hits if os.path.exists(os.path.join(self.root, h))), key=lambda h: (len(h), h))
        if len(hits) > 1:  # the one the session itself read or edited is the one it means
            touched = {os.path.relpath(p, self.root) for p in (meta or {}).get("touched", ())
                       if isinstance(p, str) and p.startswith(self.root + os.sep)}
            if len(mine := [h for h in hits if h in touched]) == 1:
                hits = mine
        if len(hits) > 1 and self.scope:  # else the one in the chat's package, if it has one (the package's first)
            mine = [h for h in hits if self._in_scope(h)]
            hits = mine if len(mine) == 1 else mine + [h for h in hits if h not in mine]
        if len(hits) > 1 and line:  # a cited line rules out the files too short to have it
            hits = [h for h in hits if self._line_count(h) >= line] or hits
        if not hits:
            where = os.path.basename(self.root) or self.root
            return Ref(surface, "file", "not_found", reason=f"no file or folder in {where} ends with {want} "
                                                           "(files git ignores, like build output, aren't searched)")
        locs = [{"role": "folder" if os.path.isdir(os.path.join(self.root, h)) else "file", "path": h,
                 "line": line, "resolver": "files"} for h in hits[:40]]
        if len(hits) == 1:
            return Ref(surface, "file", "resolved", hits[0] + (f":{line}" if line else ""), locations=locs)
        return Ref(surface, "file", "ambiguous", reason=f"{len(hits)} files end with {want}", locations=locs)

    def _line_count(self, path):
        try:
            with open(os.path.join(self.root, path), "rb") as f:
                return f.read(self.BLOB_LIMIT).count(b"\n") + 1
        except OSError:
            return 0

    def _commit(self, surface):
        """A commit (or a range of them: its last, and how many) in this repository, or in another the chat worked
        in (chat_repos)."""
        span = ref_commit_range(surface)
        tops = [t for t in (self.top, *self.chat_repos()) if t]
        if not tops:
            return Ref(surface, "commit", "not_found", reason="this folder isn't in a git repository")
        for top in tops:
            sha = (_git(top, "rev-parse", "--verify", "--quiet", f"{span[2] if span else surface}^{{commit}}")
                   or "").strip()
            base = (_git(top, "rev-parse", "--verify", "--quiet", f"{span[0]}^{{commit}}") or "").strip() \
                if span and sha else ""
            if sha and (base or not span):
                break
        else:
            return Ref(surface, "commit", "not_found",
                       reason="no commit with this hash in this repository or the others this chat worked in "
                              "(it may be another kind of hash)")
        github = self.github if top == self.top else github_repo(top)
        info = (_git(top, "show", "-s", "--format=%H%x00%an%x00%aI%x00%cI%x00%s%x00%P", sha) or "").rstrip("\n")
        full, author, adate, cdate, subject, parents = (info.split("\0") + [""] * 6)[:6]
        stat = _git(top, "diff", "--shortstat", base, sha) if base else \
            _git(top, "diff", "--shortstat", parents.split()[0], sha) if parents \
            else _git(top, "show", "--shortstat", "--format=", sha)
        num = lambda pat: int(m.group(1)) if (m := re.search(pat, stat or "")) else 0  # noqa: E731
        refs = (_git(top, "for-each-ref", "--contains", sha, "--format=%(refname)", "refs/heads", "refs/remotes")
                or "").split()
        branches = [r[len("refs/heads/"):] for r in refs if r.startswith("refs/heads/")]
        remotes = [r[len("refs/remotes/"):] for r in refs if r.startswith("refs/remotes/") and not r.endswith("/HEAD")]
        data = {"sha": full, "author": author, "date": adate, "committed": cdate, "subject": subject,
                "files": num(r"(\d+) files? changed"), "added": num(r"(\d+) insertions?"),
                "removed": num(r"(\d+) deletions?"), "branches": branches[:12], "remotes": remotes[:12],
                "pushed": bool(remotes)}
        if top != self.top:  # (another repository's: its own place, and its GitHub)
            data.update(repo=top, github=github)
        if span:
            data.update(range=base, count=int((_git(top, "rev-list", "--count", f"{base}..{sha}") or "0").strip()))
        if github and remotes:
            data["url"] = f"https://github.com/{github}/compare/{base}...{full}" if span else \
                f"https://github.com/{github}/commit/{full}"
        if not refs:  # on no branch: a rebase or squash replaced it. Its successor most likely kept the subject.
            data["rewritten"] = True
            for row in (_git(top, "log", "--all", "-F", f"--grep={subject}", "--format=%H%x00%s", "-n", "50")
                        or "").splitlines():
                c, _, s = row.partition("\0")
                if s == subject and c != full:
                    data["successor"] = c
                    break
        return Ref(surface, "commit", "resolved", f"{base}..{full}" if span else full, data=data)

    def _branch(self, surface):
        if not self.top:
            return None
        for ref in (f"refs/heads/{surface}", f"refs/remotes/{surface}"):
            if sha := (_git(self.top, "rev-parse", "--verify", "--quiet", ref) or "").strip():
                break
        else:
            return None
        remote = ref.startswith("refs/remotes/")
        name = surface.split("/", 1)[1] if remote else surface
        _sha, subject, date = ((_git(self.top, "log", "-1", "--format=%H%x00%s%x00%cI", ref) or "").strip().split("\0")
                               + ["", "", ""])[:3]
        upstream = "" if remote else (_git(self.top, "rev-parse", "--abbrev-ref", f"{surface}@{{upstream}}") or "").strip()
        base = upstream or (_git(self.top, "symbolic-ref", "--short", "refs/remotes/origin/HEAD") or "").strip()
        data = {"ref": ref, "name": name, "sha": sha, "subject": subject, "date": date, "upstream": upstream,
                "base": base, "pushed": remote or bool(_git(self.top, "rev-parse", "--verify", "--quiet",
                                                             f"refs/remotes/origin/{name}"))}
        if base and base != surface:
            counts = (_git(self.top, "rev-list", "--left-right", "--count", f"{base}...{ref}") or "").split()
            if len(counts) == 2:
                data["behind"], data["ahead"] = int(counts[0]), int(counts[1])
        if self.github and data["pushed"]:
            data["url"] = f"https://github.com/{self.github}/tree/{name}"
        return Ref(surface, "branch", "resolved", ref, data=data)

    def _pr(self, surface):
        if (self.catalogue.get(surface) or {}).get("kind") == "branch" and (ref := self._branch(surface)) is not None:
            return ref  # (a branch may be called feature/login#12)
        m = re.search(r"(?:([\w.-]+/[\w.-]+))?#(\d+)$", surface)
        repo = (m.group(1) if m else None) or self.github
        if not m or not repo:
            return Ref(surface, "pr", "not_found", reason="no GitHub repository to look it up in")
        n = int(m.group(2))
        data = {"number": n, "repo": repo, "url": f"https://github.com/{repo}/pull/{n}"}
        if (listed := GITHUB.known(repo, n)) is not None:  # (read already: what it is, and where it stands)
            data.update(github_data(listed))
        return Ref(surface, "pr", "resolved", f"{repo}#{n}", data=data)

    def _needs_github(self, ref):
        if ref.kind not in ("commit", "branch", "pr") or ref.status != "resolved" or not shutil.which("gh") or \
                ref.data.get("issue"):  # (an issue: no pull request's details to read)
            return False
        if ref.kind != "pr" and not ((ref.data.get("github") or self.github) and ref.data.get("pushed")):
            return False
        return time.time() - (ref.data.get("fetched_at") or 0) > self.GITHUB_TTL

    def _enrich(self, ref):
        """Pull requests from GitHub: a commit's, a branch's, or a PR's own details."""
        d = ref.data
        d["fetched_at"] = time.time()
        if ref.kind == "commit":
            prs = _gh_json("api", f"repos/{d.get('github') or self.github}/commits/{d['sha']}/pulls")
            if isinstance(prs, list):
                d["prs"] = [ref_pr_info(p) for p in prs[:10]]
        elif ref.kind == "branch":
            prs = _gh_json("pr", "list", "--repo", self.github, "--head", d["name"], "--state", "all", "--limit", "10",
                           "--json", "number,title,state,createdAt,mergedAt,closedAt,url")
            if isinstance(prs, list):
                d["prs"] = [{"number": p.get("number"), "title": p.get("title", ""), "state": p.get("state", "").lower(),
                             "url": p.get("url"), "created": p.get("createdAt"), "merged": p.get("mergedAt"),
                             "closed": p.get("closedAt")} for p in prs]
        else:
            p = _gh_json("api", f"repos/{d['repo']}/pulls/{d['number']}")
            if isinstance(p, dict) and p.get("number"):
                d.update(ref_pr_info(p), additions=p.get("additions"), deletions=p.get("deletions"),
                         changed_files=p.get("changed_files"), author=(p.get("user") or {}).get("login"),
                         head=(p.get("head") or {}).get("ref"), base=(p.get("base") or {}).get("ref"))
                return f"{ref.surface}: {d.get('title', '')} ({d.get('state', '')})"
            return f"no answer about {ref.surface}"
        if "prs" not in d:
            return f"no answer about {ref.surface} (offline, or gh isn't signed in)"
        return f"{len(d['prs'])} pull request{'s' * (len(d['prs']) != 1)} for {ref.surface}"

    def _env_prefixes(self, st):
        """pydantic settings classes' env_prefix: their fields are read from PREFIX_FIELD, a name no
        search for the env var itself finds."""
        def build():
            return [(path, m.group(1)) for path, _n, text in
                    self._grep(["-E", r"env_prefix[[:space:]]*=[[:space:]]*[\"'][A-Za-z0-9_]+[\"']"], ["*.py"])
                    if (m := re.search(r"env_prefix\s*=\s*[\"']([A-Za-z0-9_]+)[\"']", text))]
        return self._index("env_prefixes", st, build)

    def _name(self, name, st):
        """An UPPER_SNAKE name: an env var if something sets or reads it, else a constant if it's defined."""
        hits = [(p, n, ref_name_role(p, t, name), "") for p, n, t in self._grep(["-w", "-F", "-e", name])]
        for path, prefix in self._env_prefixes(st):
            if name.upper().startswith(prefix.upper()) and len(name) > len(prefix):
                field = name[len(prefix):].lower()
                try:
                    with open(os.path.join(self.root, path), encoding="utf-8", errors="replace") as f:
                        for n, text in enumerate(f, 1):
                            if re.match(rf"\s*{re.escape(field)}\s*[:=]", text):
                                hits.append((path, n, "read", f"settings field {field} (env_prefix {prefix})"))
                except OSError:
                    pass
        order = ["read", "set", "secret source", "defined", "used", "doc"]
        caps = {"secret source": 10, "set": 20, "read": 20, "defined": 10, "used": 10, "doc": 3}
        code = (".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs", ".go")
        locs, seen, taken = [], set(), {}
        for path, n, role, note in sorted(hits, key=lambda h: (order.index(h[2]), not h[0].endswith(code),
                                                                ref_is_test(h[0]), h[0], h[1])):
            if (path, n) in seen or taken.get(role, 0) >= caps[role]:
                continue
            seen.add((path, n))
            taken[role] = taken.get(role, 0) + 1
            locs.append({"role": role, "path": path, "line": n, "note": note, "resolver": "grep"})
        roles = {loc["role"] for loc in locs}
        if roles & {"secret source", "set", "read"}:
            return Ref(name, "env", "resolved", name, locations=locs)
        defs = [loc for loc in locs if loc["role"] == "defined"]
        if defs:
            canonical = f"{defs[0]['path']}::{name}" if len(defs) == 1 else name
            return Ref(name, "constant", "resolved", canonical, locations=locs)
        if locs:
            return Ref(name, "env", "not_found", locations=locs,
                       reason=f"only mentioned ({len(hits)} places): nothing sets, reads or defines it")
        return Ref(name, "env", "not_found", reason=f"not in any file in {os.path.basename(self.root) or self.root}")

    def _symbol(self, surface, st, meta):
        """A code name: its definitions (def, class, function, const, interface, a field, a table or column).
        module.name looks in that module first; obj.attr, with no module of that name, lists where it's used
        and the attr fields defined there. Which class obj is takes a language server (_symbol_lsp): its answer,
        when it has one, comes first. A span that's more than a name (ref_span) has resolvers of its own."""
        if span := ref_span(surface):
            return self._span(surface, span, st, meta)
        name = re.sub(r"\[[^\]]*\]", "", re.sub(r"\([^()]*\)$", "", surface))
        parts = name.split(".")
        last = parts[-1]
        words = self._grep(["-w", "-F", "-e", last], REF_CODE_PATHS, limit=30000)  # one fast search, then
        defs = [(p, line, role) for p, line, text in words if (role := self._def_role(p, line, text, last))]
        if surface.endswith(")"):  # a call: only what can be called
            defs = [d for d in defs if d[2] in ("function", "method", "class", "variable", "re-export")]
        defs = [d for d in defs if not ref_is_test(d[0])] or defs  # tests only when nothing else defines it
        stem = lambda p: os.path.splitext(os.path.basename(p))[0]  # noqa: E731
        touched = self._touched(meta)
        loc = lambda p, line, role, note="": {"role": role, "path": p, "line": line, "resolver": "grep",  # noqa: E731
                                              "note": note}
        def modules():  # a module or package of that name: export_openapi, orchard_relay.gen
            files, dirs, _by_name = self._files(st)
            code = tuple(p[1:] for p in REF_CODE_PATHS)
            found = [(f, "module") for f in files if f.endswith(code) and stem(f) == last] + \
                    [(d, "folder") for d in dirs if d.rsplit("/", 1)[-1] == last]
            return [loc(p, None, role) for p, role in found if ref_in_order(parts[:-1], p.split("/")[:-1])]
        def dotted():  # the patterns' answer for module.name and obj.attr
            mod = parts[-2]
            mine = [d for d in defs if stem(d[0]) == mod or os.path.basename(os.path.dirname(d[0])) == mod]
            if mine:
                return self._symbol_ref(surface, name, [loc(*d) for d in mine], touched)
            if mods := modules():
                return self._symbol_ref(surface, name, mods, touched)
            used = [(p, line) for p, line, _t in self._grep(["-F", "-e", name], REF_CODE_PATHS)]
            files = {p for p, _ in used}
            members = [d for d in defs if d[2] in ("field", "attribute", "method", "column")]
            if of := self._of_class(members, mod):  # sr.dlg.quick_edits: Dlg's, whatever sr is
                return self._symbol_ref(surface, name, [loc(p, line, role, f"{cls}.{last}") for p, line, role, cls in of]
                                        + [loc(p, line, "used") for p, line in used[:8]], touched)
            near = [d for d in members if d[0] in files] or \
                   [d for d in members if any(os.path.dirname(d[0]) == os.path.dirname(f) for f in files)]
            locs = [loc(p, line, "used") for p, line in used[:8]] + \
                   [loc(p, line, role, f"{last}") for p, line, role in (near or members)[:12]]
            if used:
                return Ref(surface, "symbol", "resolved", name, locations=locs)
            if members:
                return self._symbol_ref(surface, name, locs, touched)
            if conf := self._config(surface, name, st):
                return conf
            return Ref(surface, "symbol", "not_found",
                       reason=f"no module {mod} defines {last}, and {name} isn't used in any code file")
        if len(parts) >= 2:
            ref = dotted()
            if (code := self._code()) is None or (lsp := self._symbol_lsp(surface, name, parts, st, touched, code)) is None:
                return ref
            if lsp.kind == "symbol":  # the server's definition first, then what the patterns had (other matches)
                have = {(l["path"], l["line"]) for l in lsp.locations}
                lsp.locations += [l for l in ref.locations if (l["path"], l["line"]) not in have][:30 - len(lsp.locations)]
            return lsp
        ents = self._entity_fields(last) if "_" in last and not surface.endswith(")") else []
        if defs or ents:  # (a data model's fields and columns come first: the schema the code's models mirror)
            return self._symbol_ref(surface, name, [{**loc(p, line, "entity field", note), "resolver": "yaml"}
                                                    for p, line, note in ents] + [loc(*d) for d in defs], touched)
        if mods := modules():
            return self._symbol_ref(surface, name, mods, touched)
        if words and (code := self._code()) is not None and (ref := self._lsp_bare(surface, name, words, st, touched, code)):
            return ref
        if conf := self._config(surface, name, st):
            return conf
        used = words
        return Ref(surface, "symbol", "not_found", locations=[loc(p, line, "used") for p, line, _t in used[:8]],
                   reason=f"used in {len(used)} places but defined in no code file (a JSON field, a config key?)"
                   if used else "not in any code file")

    def _touched(self, meta):
        """The files the session's own tools read or wrote, relative to the repository."""
        return {os.path.relpath(t, self.root) for t in (meta or {}).get("touched", ())
                if isinstance(t, str) and t.startswith(self.root + os.sep)}

    # ---- spans that are more than a name (ref_span): each is one glossary entry, its words looked up together
    def _span(self, surface, span, st, meta):
        form, a, b = span
        touched = self._touched(meta)
        if form == "shape":
            return self._shape(surface, a, b, touched)
        if form == "pair":
            return self._pair(surface, a, b, touched)
        if form == "query":
            return self._query(surface, a, touched)
        return self._excerpt(surface, a, touched)

    def _span_ref(self, surface, locs, touched, missing):
        """The places found for a span, best first: one is found; several are, the one in a file the session
        touched first; none, it's not found for the reason given. Tests count only when nothing else has it."""
        locs = ([l for l in locs if not ref_is_test(l["path"])] or locs)[:30]
        if not locs:
            return Ref(surface, "symbol", "not_found", reason=missing)
        if len(locs) > 1 and len(mine := [l for l in locs if l["path"] in touched]) == 1:
            locs = mine + [l for l in locs if l is not mine[0]]
            return Ref(surface, "symbol", "resolved", f"{mine[0]['path']}::{surface}", locations=locs)
        if len(locs) == 1:
            return Ref(surface, "symbol", "resolved", f"{locs[0]['path']}::{surface}", locations=locs)
        return Ref(surface, "symbol", "ambiguous", surface, reason=f"{len(locs)} places", locations=locs)

    def _shape(self, surface, key, names, touched):
        """An object's shape, {installation_id, member_id, name}: the class, type, table or data-model entity that
        declares all those names, else an object or a parameter list that does. With a key, previous: {job_id,
        seq}: the key's definition, typed with such a class or near where code writes the key with that shape."""
        paths = REF_CODE_PATHS + ["*.yaml", "*.yml"]
        files = None
        for n in sorted(names, key=len, reverse=True):  # (the longest names are the rarest: the fewest files)
            got = self._grep_files(["-w", "-F", "-e", n], paths)
            files = got if files is None else files & got
            if not files:
                break
        tiers = {"class": 0, "type": 0, "table": 0, "entity": 0, "object": 1, "parameters": 2}
        found = []
        for path in sorted(files or ())[:3000]:
            lines = self._text(path).splitlines()
            for h, extra in ref_shape_blocks(lines, names):
                role, cname = ref_container(lines, h, path)
                found.append({"role": role, "path": path, "line": h + 1, "resolver": "shape", "name": cname,
                              "extra": extra, "note": f"{cname}: {', '.join(names)}" if cname else ", ".join(names)})
        # the declared types first, and among them the ones with the fewest other members: the shape as written
        found.sort(key=lambda l: (tiers[l["role"]], ref_is_test(l["path"]), l["extra"], l["path"] not in touched,
                                  l["path"], l["line"]))
        what = "{" + ", ".join(names) + "}"
        if key is None:
            top = [l for l in found if not ref_is_test(l["path"])] or found
            best = [l for l in top if (tiers[l["role"]], l["extra"]) == (tiers[top[0]["role"]], top[0]["extra"])]
            return self._span_ref(surface, [{k: v for k, v in l.items() if k not in ("name", "extra")} for l in best],
                                  touched, f"nothing declares all of {what}")
        last = key.split(".")[-1]
        opens = re.compile(rf"(?<![\w.]){re.escape(last)}[\"']?\??\s*[:=]\s*\{{")
        written = [l for l in found if l["role"] == "object" and opens.search(self._text(l["path"]).splitlines()[l["line"] - 1])]
        classes = {l["name"] for l in found if l["role"] in ("class", "type") and l["name"]}
        defs = [(p, n, role, t) for p, n, t in self._grep(["-w", "-F", "-e", last], paths, limit=30000)
                if (role := self._def_role(p, n, t, last)) in ("field", "column", "attribute")]
        typed = [d for d in defs if any(re.search(rf"\b{re.escape(c)}\b", d[3]) for c in classes)]
        here = {l["path"] for l in written}
        near = [d for d in defs if d[0] in here] or [d for d in defs if os.path.dirname(d[0]) in {os.path.dirname(p) for p in here}]
        pick = typed or near
        pick = [d for d in pick if any(re.search(rf"\b{re.escape(n)}\b", d[3]) for n in names)] or pick  # (its comment)
        locs = [{"role": role, "path": p, "line": n, "resolver": "shape", "note": f"{last}: {what}"}
                for p, n, role, _t in pick] + \
               [{"role": "used", "path": l["path"], "line": l["line"], "resolver": "shape", "note": l["note"]} for l in written]
        ref = self._span_ref(surface, locs, touched, f"nothing defines {key} as {what}")
        if ref.status == "ambiguous" and sum(l["role"] != "used" for l in ref.locations) == 1:
            ref.status, ref.canonical = "resolved", f"{ref.locations[0]['path']}::{surface}"  # (one definition, used here)
        return ref

    def _pair(self, surface, key, value, touched):
        """key: value, preview.reason: crashed or origin: quick_edit: the key's definitions that name the value, on
        their line (reason: Literal["crashed"]) or where the type they're declared with is (origin: Origin, and
        Origin = Literal[..., "quick_edit", ...]); in YAML, the key whose line or block has it (strategy: then
        type: Recreate). When none does, it isn't found: every definition of a plain word like origin isn't it."""
        last = key.split(".")[-1]
        has = re.compile(rf"(?<![\w-]){re.escape(value)}(?![\w-])")
        hits, types = [], {}
        for p, n, t in self._grep(["-w", "-F", "-e", last], REF_CODE_PATHS + ["*.yaml", "*.yml"], limit=30000):
            if p.endswith((".yaml", ".yml")):
                if m := re.match(rf"(\s*)-?\s*{re.escape(last)}\s*:(.*)", t):
                    lines = self._text(p).splitlines()
                    block = [m.group(2)] + list(itertools.takewhile(
                        lambda s: not s.strip() or len(s) - len(s.lstrip()) > len(m.group(1)), lines[n:n + 12]))
                    if any(has.search(s.split("#")[0]) for s in block):
                        hits.append({"role": "key", "path": p, "line": n, "resolver": "yaml", "note": f"{last}: {value}"})
                continue
            if self._def_role(p, n, t, last) not in ("field", "column", "attribute", "variable"):
                continue
            after = t.split(last, 1)[1]
            if has.search(after.split("#")[0].split("//")[0]):
                hits.append({"role": "field", "path": p, "line": n, "resolver": "grep", "note": f"{last}: {value}"})
            else:
                for typ in re.findall(r"\b([A-Z]\w+)\b", after.split("=")[0]):
                    if typ not in _REF_TYPING:
                        types.setdefault(typ, (p, n))
        for typ in list(types)[:8] if not hits else ():  # origin: Origin, where Origin names the value
            for p, n, t in self._grep(["-w", "-F", "-e", typ], REF_CODE_PATHS, limit=2000):
                if self._def_role(p, n, t, typ) in ("variable", "type", "class"):
                    lines = self._text(p).splitlines()
                    body = [t] + list(itertools.takewhile(lambda s: not s.strip() or s[:1].isspace(), lines[n:n + 40]))
                    if any(has.search(s) for s in body):
                        hits.append({"role": "type", "path": p, "line": n, "resolver": "grep",
                                     "note": f"{typ}, the type of {last}"})
        return self._span_ref(surface, hits, touched, f"no definition of {last} names {value}")

    def _query(self, surface, name, touched):
        """?name=value: where code reads that query parameter (searchParams.get("name"), request.args["name"],
        Query(alias="name"), a FastAPI parameter name: str = Query(...))."""
        q = re.escape(name)
        reads = re.compile(rf"(?:params|query|args|search\w*|URLSearchParams\([^)]*\))\.(?:get|getAll|has)\(\s*[\"']{q}[\"']"
                           rf"|(?:params|query|args|query_params)\s*\[\s*[\"']{q}[\"']|alias\s*=\s*[\"']{q}[\"']"
                           rf"|\b{q}\s*:[^=]*=\s*Query\(", re.I)
        locs = [{"role": "reads", "path": p, "line": n, "resolver": "grep", "note": f"?{name}="}
                for p, n, t in self._grep(["-F", "-e", name], REF_CODE_PATHS, limit=20000) if reads.search(t)]
        return self._span_ref(surface, locs, touched, f"no code reads the query parameter {name}")

    def _excerpt(self, surface, text, touched):
        """A piece of code, keep_dist=package == "orchard-ui": where it's written (any spacing, either quote, an
        ellipsis for anything). In more than five places it's too common to point anywhere."""
        frags = [f for f in re.split(r"\s+|\.\.\.|…|[\"'`]", text) if len(f) >= 3]
        if not frags:
            return Ref(surface, "symbol", "not_found", reason="too short to look for")
        pattern = ref_excerpt_re(text)
        paths = REF_CODE_PATHS + ["*.yaml", "*.yml", "*.toml", "*.json", "*.css", "*.scss", "*.html", "*.sh", "*.tsp"]
        hits = [{"role": "written", "path": p, "line": n, "resolver": "grep", "note": ""}
                for p, n, t in self._grep(["-F", "-e", max(frags, key=len)], paths, limit=5000)
                if not REF_SENSITIVE.search(p) and pattern.search(t)]
        hits = [h for h in hits if not ref_is_test(h["path"])] or hits
        if len(hits) > 5:
            return Ref(surface, "symbol", "not_found", reason=f"written in {len(hits)} places: too common to point at one")
        return self._span_ref(surface, hits, touched, "not written in any code file")

    def _config(self, surface, name, st=None):
        """A name that isn't code but configuration: a Helm helper ({{ define "app.previewUpstream" }}) or a
        YAML key, matched by its dotted path (livePreview.runner.previewPort), with the templates using it. A path a
        Helm chart's values files have from their top is that chart's value (_kube_values)."""
        parts, last = name.split("."), name.split(".")[-1]
        if st is not None and len(parts) > 1 and self._kube_index(st)["charts"]:
            ref = self._kube_values(surface, name, st, fallback=False)
            if ref is not None:
                return ref
        texts, locs = {}, []

        def lines(path):
            if path not in texts:
                try:
                    with open(os.path.join(self.root, path), encoding="utf-8", errors="replace") as f:
                        texts[path] = f.read().splitlines()
                except OSError:
                    texts[path] = []
            return texts[path]
        yaml = ["*.yaml", "*.yml", "*.tpl"]
        for path, n, text in self._grep(["-w", "-F", "-e", last], yaml, limit=20000):
            if re.search(rf"define\s+\"{re.escape(name)}\"", text):
                locs.append({"role": "helm helper", "path": path, "line": n, "resolver": "yaml", "note": ""})
            elif re.match(rf"\s*-?\s*{re.escape(last)}\s*:", text) and \
                    ref_in_order(parts[:-1], ref_yaml_path(lines(path), n - 1)[:-1]):
                key = ".".join(ref_yaml_path(lines(path), n - 1))
                locs.append({"role": "key", "path": path, "line": n, "resolver": "yaml", "note": key})
        if len(parts) > 1:  # where templates read it: .Values.<name>, include "<name>"
            for path, n, _t in self._grep(["-F", "-e", f".Values.{name}", "-e", f'include "{name}"'], yaml)[:10]:
                locs.append({"role": "used", "path": path, "line": n, "resolver": "yaml", "note": ""})
        defs = [l for l in locs if l["role"] != "used"]
        if not defs:
            return None
        locs = locs[:30]
        if len(defs) == 1:
            return Ref(surface, "config", "resolved", f"{defs[0]['path']}::{name}", locations=locs)
        return Ref(surface, "config", "ambiguous", name, reason=f"{len(defs)} keys named {last}", locations=locs)

    def _def_role(self, path, line, text, name):
        """ref_def_role, plus a table named on the line after create_table( / Table( (alembic's layout)."""
        if role := ref_def_role(path, text, name):
            return role
        if re.fullmatch(rf"\s*[\"']{re.escape(name)}[\"'],?\s*", text) and line > 1:
            try:
                with open(os.path.join(self.root, path), encoding="utf-8", errors="replace") as f:
                    before = f.read().splitlines()[line - 2]
            except (OSError, IndexError):
                return None
            if re.search(r"(create_table|\bTable)\(\s*$", before):
                return "table"
            if re.search(r"(Column|add_column\([^,]*,\s*\w*\.?Column)\(\s*$", before):
                return "column"
        return None

    def _in_scope(self, path):
        """Whether a path (relative to the checkout) is in this Signal's package (always, with none)."""
        return not self.scope or path == self.scope or path.startswith(self.scope + "/")

    def _symbol_ref(self, surface, name, locs, touched):
        """One definition: found. Several: the one in a file the session touched, else the one in the chat's package
        (this Signal's scope), else all of them, the package's first."""
        schema = ("entity field", "table", "column")  # (a data model's own definitions before the code's copies)
        locs = sorted(locs, key=lambda l: (l["role"] == "used", l["role"] not in schema, not self._in_scope(l["path"]),
                                           ref_is_test(l["path"]), l["path"], l["line"] or 0))[:30]
        defs = [l for l in locs if l["role"] != "used"]
        if len(defs) > 1 and len(mine := [l for l in defs if l["path"] in touched]) == 1:
            locs = mine + [l for l in locs if l is not mine[0]]
            defs = mine
        if len(defs) > 1 and self.scope and len(mine := [l for l in defs if self._in_scope(l["path"])]) == 1:
            locs = mine + [l for l in locs if l is not mine[0]]
            defs = mine
        if len(defs) == 1:
            return Ref(surface, "symbol", "resolved", f"{defs[0]['path']}::{name}", locations=locs)
        return Ref(surface, "symbol", "ambiguous", name, reason=f"{len(defs)} definitions of {name}", locations=locs)

    def _entity_fields(self, name):
        """A data model's fields named so, declared in YAML (a Widget's system.yaml: ontology, entities, each one's
        fields): [(path, line, "Entity.name")]."""
        out = []
        for path, n, text in self._grep(["-w", "-F", "-e", name], ["*.yaml", "*.yml"], limit=5000):
            if not re.match(rf"\s+{re.escape(name)}\s*:", text):
                continue
            lines = self._text(path).splitlines()
            keys = ref_yaml_path(lines, n - 1)
            if len(keys) >= 3 and keys[-2] == "fields" and "entities" in keys:
                indent = len(text) - len(text.lstrip())
                entity = next((m.group(1) for s in reversed(lines[:n - 1]) if len(s) - len(s.lstrip()) < indent
                               and (m := re.match(r"\s*-\s*name:\s*[\"']?([\w.-]+)", s))), None)
                out.append((path, n, f"{entity}.{name}" if entity else name))
        return out

    def _of_class(self, members, obj):
        """The members (path, line, role) that are in a class named like the object they're used on: in
        sr.dlg.quick_edits, Dlg's quick_edits, whatever sr is. Python only (its own parser): [(path, line, role, class)]."""
        out = []
        for path, line, role in members[:60]:
            if path.endswith((".py", ".pyi")) and (classes := ref_py_scope(self._text(path), line)[1]) \
                    and ref_same_name(obj, classes[-1]):
                out.append((path, line, role, classes[-1]))
        return out

    # ---- Kubernetes (see "signal: Kubernetes"): the repository's manifests, charts and overlays, and the kubeconfig
    def _kube(self, surface, st, meta):
        """A Kubernetes name (ref_kube); a word a reply calls a Deployment or a context ("the `builder` NodePool") is a
        name of that kind."""
        hint = ((meta or {}).get("hints") or {}).get(surface)
        return self._kube_ref(surface, ref_kube(surface) or ("name", ref_key(surface)), st, meta, hint)

    def _kube_ref(self, surface, how, st, meta, hint=None):
        """A Kubernetes name as ref_kube or ref_kube_command reads it (how): the objects the repository defines with that
        name (a pod's: its workload), the chart value or key path, the release, or the kubeconfig context. hint: the
        kind the reply gave it, or "context"."""
        what = how[0]
        if what == "values":
            return self._kube_values(surface, how[1], st)
        if what == "field":
            return self._kube_field(surface, how[1], st, meta) or (
                self._symbol(surface, st, meta) if not surface.startswith(".") else
                Ref(surface, "config", "not_found", reason=f"no manifest or values file has the key path {how[1]}"))
        if what == "release":
            return self._kube_release(surface, how[1], st)
        idx = self._kube_index(st)
        name, kind, hits = (how[2] if what in ("object", "selector") else how[1]), None, []
        if what == "selector":
            _w, key, value, ns = how
            hits = [(o, o["names"][0][0], f"labelled {key}={value}") for o in idx["objects"] if o["names"] and
                    o["labels"].get(key) == value and o["kind"] in KUBE_WORKLOADS + ("Service",) and
                    not (ns and o["namespace"] and o["namespace"] != ns)] or self._kube_find(value, None, ns, idx)
        elif what == "object":
            _w, kind, _n, ns = how
            hits = self._kube_find(name, kind, ns, idx) or \
                (self._kube_find(name, "Rollout", ns, idx) if kind == "Deployment" else [])  # (Argo's stand-in)
        elif what == "name" and hint != "context":
            kind = hint if hint and (hint in KUBE_ORDER or _KUBE_PASCAL.fullmatch(hint)) else None
            hits = (self._kube_find(name, kind, None, idx) if kind else []) or self._kube_find(name, None, None, idx)
        if not hits and kind == "Namespace":
            hits = self._kube_namespaces(name, idx)
        if hits:
            return self._kube_objects_ref(surface, hits, kind)
        if what in ("context", "name") and (found := [c for c in kube_contexts() if name in (c["name"], c.get("cluster"))
                                                      or (kube_made_as(c) or ("", ""))[1] == name]):
            found.sort(key=lambda c: name not in (c["name"], c.get("cluster")))  # (the exact name first)
            return self._kube_context_ref(surface, found)
        if what == "name" and hint != "context" and (live := kube_running([name])):  # (no manifest has it: it runs)
            r = live[0]
            note = f"{r['kind']} {name}, running in {r['context']} · {r['namespace']} ({r['state']})" + \
                (wording.say("lookup.and_more", n=len(live) - 1) if len(live) > 1 else "")
            return Ref(surface, "k8s", "resolved", f"{r['kind']}/{name}", data={
                "kind": r["kind"], "name": name, "namespace": r["namespace"], "live": True, "note": note})
        where = os.path.basename(self.root) or self.root
        if what == "context":
            reason = f"no context or cluster {name} in your kubeconfig" + ("" if kubeconfig_paths() else " (there is none)")
        else:
            reason = f"no Kubernetes object {name} in {where}'s manifests, Helm charts or kustomize overlays" + \
                (", nor a kubeconfig context" if what == "name" else "")
        return Ref(surface, "k8s", "not_found", reason=reason)

    def _kube_objects_ref(self, surface, hits, prefer=None):
        """The objects found for a name, its workload first (or the kind the reply gave it): one place (a chart, a
        kustomize base, a folder) defining them is found; several are several matches."""
        seen, mine = set(), []
        for h in hits:
            if (h[0]["path"], h[0]["line"]) not in seen:
                seen.add((h[0]["path"], h[0]["line"]))
                mine.append(h)
        rank = {k: i for i, k in enumerate(KUBE_ORDER)}
        mine.sort(key=lambda h: (h[0]["kind"] != prefer, rank.get(h[0]["kind"], len(rank)), h[0]["path"], h[0]["line"]))
        mine = mine[:30]

        def note(o, n, how):
            ns = o["namespace"] if o["namespace"] and not _KUBE_ACTION.search(o["namespace"]) else None
            return ", ".join(x for x in (n if n != surface else "", how, f"namespace {ns}" if ns else "") if x)
        locs = [{"role": o["kind"], "path": o["path"], "line": o["line"], "resolver": o["resolver"], "note": note(o, n, how)}
                for o, n, how in mine]
        first, name, how = mine[0]
        groups = sorted({o["group"] or "." for o, _n, _h in mine})
        ns = first["namespace"] if first["namespace"] and not _KUBE_ACTION.search(first["namespace"]) else None
        data = {"kind": first["kind"], "name": name, "namespace": ns, "chart": first["chart"], "release": first["release"],
                "overlay": first.get("overlay"), "how": how,
                "note": f"{first['kind']} {name}" + (f" ({how})" if how else "")}
        if len(groups) == 1:
            return Ref(surface, "k8s", "resolved", f"{first['kind']}/{name}", data=data, locations=locs)
        return Ref(surface, "k8s", "ambiguous", f"{first['kind']}/{name}", data=data, locations=locs,
                   reason=f"{len(groups)} places define it: {', '.join(groups[:4])}")

    @staticmethod
    def _kube_context_ref(surface, contexts):
        """A kubeconfig context (or a cluster no context names): its cluster, server, namespace and user's name. It has
        no location: the file holds credentials, so it isn't opened; its card says where it is."""
        c = next((c for c in contexts if not c.get("cluster_only")), contexts[0])
        what = "cluster" if c.get("cluster_only") else "context"
        made = kube_made_as(c) or ("", "")
        bits = [f"cluster {c['cluster']}" if c.get("cluster") and c["cluster"] != c["name"] else "",
                f"the {made[0]} cluster {made[1]}" if made[1] and made[1] not in (c["name"], c.get("cluster")) else "",
                f"namespace {c['namespace']}" if c.get("namespace") else "", "the current one" if c.get("current") else ""]
        note = f"Kubeconfig {what} {c['name']}" + (": " + ", ".join(b for b in bits if b) if any(bits) else "")
        data = {"context": c["name"], "cluster": c.get("cluster"), "server": c.get("server"),
                "namespace": c.get("namespace"), "user": c.get("user"), "kubeconfig": c["kubeconfig"],
                "line": c.get("line"), "current": bool(c.get("current")), "cluster_only": bool(c.get("cluster_only")),
                "note": note}
        return Ref(surface, "k8s", "resolved", f"{what} {c['name']}", data=data)

    def _kube_find(self, name, kind, ns, idx, pods=True):
        """The objects named name (of that kind, in namespace ns, when given): [(object, the name matched, how)]. Exact
        names first (as written, rendered for a release, as an overlay renames it; a generated ConfigMap's with its
        hash); else templated names whose literal parts match, the most literal first; else, for a pod, its workload."""
        exact, loose = [], []
        for o in idx["objects"]:
            if kind and o["kind"] != kind:
                continue
            if ns and o["namespace"] and not _KUBE_ACTION.search(o["namespace"]) and o["namespace"] != ns:
                continue
            hit = next(((n, how) for n, how in o["names"] if n == name), None) or \
                next(((n, (how + ", " if how else "") + "with the hash kustomize adds") for n, how in o["names"]
                      if o.get("generated") and re.fullmatch(re.escape(n) + r"-[a-z0-9]{10}", name)), None)
            if hit:
                exact.append((o, name, hit[1]))
            elif o["pattern"] and o["literal"] >= 4 and re.fullmatch(o["pattern"], name):
                loose.append((o, name, f"its template names it {o['raw']}"))
        if exact or loose:
            return exact or sorted(loose, key=lambda h: -h[0]["literal"])
        if pods and kind in (None, "Pod"):
            for owner_kind, owner in kube_pod_owners(name):
                kinds = {"CronJob": ("CronJob",), "Deployment": ("Deployment", "Rollout"),
                         "StatefulSet": ("StatefulSet",)}.get(owner_kind, ("Job", "DaemonSet", "ReplicaSet"))
                got = [(o, n, (how + "; " if how else "") + f"{name} is one of its pods")
                       for o, n, how in self._kube_find(owner, None, ns, idx, pods=False) if o["kind"] in kinds]
                if got:
                    return got
        return []

    def _kube_namespaces(self, name, idx):
        """Where the repository puts things in namespace name, when no Namespace object defines it: a kustomization's
        namespace:, a Helm release's -n, then the manifests that say so (the first in each file)."""
        out, files = [], set()
        place = lambda path, line, how: ({"kind": "Namespace", "path": path, "line": line, "resolver": "kube",  # noqa: E731
                                          "namespace": None, "group": f"namespace {name}", "chart": None,
                                          "release": None}, name, how)
        for d, k in sorted(idx["kustomizations"].items()):
            if k["namespace"] == name:
                out.append(place(k["path"], k.get("ns_line"), f"kustomize sets it for {d or '.'}"))
        for r in idx["releases"]:
            if r["namespace"] == name:
                out.append(place(r["path"], r["line"], f"helm -n, for the release {r['release']}"))
        for o in idx["objects"]:
            if o["namespace"] == name and o.get("ns_line") and o["path"] not in files and o["names"]:
                files.add(o["path"])
                out.append(place(o["path"], o["ns_line"], f"the namespace of {o['kind']} {o['names'][0][0]}"))
        return out

    def _kube_values(self, surface, key, st, fallback=True):
        """A Helm chart's value (.Values.image.tag, helm --set image.tag=...): where its values files set it (values.yaml
        first), then the templates that read it. Not in any: any YAML key of that path (_config), or None with no
        fallback."""
        idx = self._kube_index(st)
        parts, locs, charts = key.split("."), [], set()
        for root, c in idx["charts"].items():
            for vf in c["values_files"]:
                lines = self._text(vf).splitlines()
                for i, s in enumerate(lines):
                    if re.match(rf"\s*{re.escape(parts[-1])}\s*:", s) and ref_yaml_path(lines, i) == parts:
                        locs.append({"role": "value", "path": vf, "line": i + 1, "resolver": "helm",
                                     "note": f"the {c['name']} chart's {os.path.basename(vf)}"})
                        charts.add(root)
                        break
        if not locs:
            if not fallback:
                return None
            return self._config(surface, key) or Ref(surface, "config", "not_found",
                                                     reason=f"no Helm chart's values files have {key}")
        used = [(p, n) for p, n, t in self._grep(["-F", "-e", f".Values.{key}"], ["*.yaml", "*.yml", "*.tpl"], limit=200)
                if re.search(rf"\.Values\.{re.escape(key)}(?![\w.-])", t)]
        locs += [{"role": "used", "path": p, "line": n, "resolver": "helm", "note": f".Values.{key}"} for p, n in used[:12]]
        if len(charts) == 1:
            return Ref(surface, "config", "resolved", f"{next(iter(charts)) or '.'}::.Values.{key}", locations=locs)
        return Ref(surface, "config", "ambiguous", f".Values.{key}", reason=f"{len(charts)} charts have {key}",
                   locations=locs)

    def _kube_field(self, surface, path, st, meta):
        """A key path in manifests (spec.strategy.type, metadata.labels): the manifest, template and values lines whose
        key path ends with the most of it (a values file has a template's settings under keys of its own), the files
        the session touched first. None if no line has two of its keys in order (or its only one)."""
        parts, idx = path.split("."), self._kube_index(st)
        values = {vf for c in idx["charts"].values() for vf in c["values_files"]}
        touched, found = self._touched(meta), []
        key = re.compile(rf"\s*-?\s*{re.escape(parts[-1])}\s*:")
        for f in sorted({o["path"] for o in idx["objects"]} | values):
            lines = self._text(f).splitlines()
            for i, s in enumerate(lines):
                if key.match(s):
                    have = ref_yaml_path(lines, i)
                    k = next((k for k in range(len(parts), 0, -1) if ref_in_order(parts[-k:], have)), 0)
                    if k >= min(2, len(parts)):
                        found.append((k, f, i + 1, ".".join(have)))
        if not found:
            return None
        best = max(x[0] for x in found)
        top = sorted((x for x in found if x[0] == best), key=lambda x: (x[1] not in touched, x[1] in values, x[1], x[2]))
        locs = [{"role": "key", "path": f, "line": n, "resolver": "yaml", "note": have} for _k, f, n, have in top[:30]]
        if len(locs) == 1:
            return Ref(surface, "config", "resolved", f"{locs[0]['path']}::{path}", locations=locs)
        return Ref(surface, "config", "ambiguous", path, reason=f"{len(top)} manifests have {path}", locations=locs)

    def _kube_release(self, surface, release, st):
        """A Helm release: the lines that install it (helm upgrade --install), then its chart."""
        rs = [r for r in self._kube_index(st)["releases"] if r["release"] == release]
        if not rs:
            return Ref(surface, "k8s", "not_found",
                       reason=f"nothing in {os.path.basename(self.root) or self.root} installs a Helm release {release}")
        charts = list(dict.fromkeys(r["chart"] for r in rs))
        locs = [{"role": "release", "path": r["path"], "line": r["line"], "resolver": "helm",
                 "note": f"installs {release} from {r['chart'] or '.'}" + (f" in {r['namespace']}" if r["namespace"] else "")}
                for r in rs] + [{"role": "chart", "path": os.path.join(c, "Chart.yaml"), "line": 1, "resolver": "helm",
                                 "note": ""} for c in charts]
        data = {"release": release, "chart": charts[0], "namespace": rs[0]["namespace"],
                "note": f"Helm release {release} of {charts[0] or '.'}"}
        return Ref(surface, "k8s", "resolved" if len(charts) == 1 else "ambiguous", f"release {release}", data=data,
                   locations=locs[:30], reason="" if len(charts) == 1 else f"installed from {len(charts)} charts")

    def _build_routes(self, st):
        """FastAPI handlers with their full paths (decorator + APIRouter prefix + include_router prefix),
        OpenAPI snapshots' paths, and nginx locations."""
        texts = {}

        def lines(path):
            if path not in texts:
                try:
                    with open(os.path.join(self.root, path), encoding="utf-8", errors="replace") as f:
                        texts[path] = f.read().splitlines()
                except OSError:
                    texts[path] = []
            return texts[path]

        def statement(path, n):  # the line and its continuation lines, while brackets are open
            out, depth = [], 0
            for s in lines(path)[n - 1:n + 11]:
                out.append(s.strip())
                depth += s.count("(") + s.count("[") - s.count(")") - s.count("]")
                if depth <= 0:
                    break
            return " ".join(out)

        def enclosing_def(path, n):
            ls = lines(path)
            indent = len(ls[n - 1]) - len(ls[n - 1].lstrip()) if n <= len(ls) else 0
            for j in range(n - 2, -1, -1):
                s = ls[j]
                ind = len(s) - len(s.lstrip())
                if s.strip() and ind < indent:
                    if d := re.match(r"\s*(?:async\s+)?def\s+(\w+)", s):
                        return d.group(1)
                    if ind == 0:
                        return None
                    indent = ind
            return None

        prefix_re = re.compile(r"prefix\s*=\s*[rf]?[\"']([^\"']*)[\"']")
        routers = {}
        for path, n, _t in self._grep(["-E", r"[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=[[:space:]]*APIRouter\("], ["*.py"]):
            s = statement(path, n)
            if m := re.match(r"([A-Za-z_]\w*)\s*=\s*APIRouter\(", s):
                p = prefix_re.search(s)
                routers.setdefault(path, []).append({"line": n, "var": m.group(1), "prefix": p.group(1) if p else "",
                                                     "builder": enclosing_def(path, n)})
        def aliases(path):  # import aliases in a file: {alias: "module.name"}
            out = {}
            for m in re.finditer(r"from\s+([\w.]+)\s+import\s+\(?([^)\n]*(?:\n[^)\n]*)*?)\)?\s*$",
                                 "\n".join(lines(path)), re.M):
                for name, alias in re.findall(r"(\w+)\s+as\s+(\w+)", m.group(2)):
                    out[alias] = f"{m.group(1).split('.')[-1]}.{name}"
            return out
        includes = []
        for path, n, _t in self._grep(["-E", r"include_router\("], ["*.py"]):
            s = statement(path, n)
            if m := re.search(r"include_router\(\s*([\w.]+)", s):
                p = prefix_re.search(s)
                token = m.group(1)
                includes.append((aliases(path).get(token, token), p.group(1) if p else ""))
        handlers = []
        verbs = "get|post|put|patch|delete|head|options|api_route|websocket"
        for path, n, _t in self._grep(["-E", rf"@[A-Za-z_][A-Za-z0-9_]*\.({verbs})\("], ["*.py"]):
            s = statement(path, n)
            m = re.search(rf"@(\w+)\.({verbs})\(\s*(?:path\s*=\s*)?[rf]?[\"']([^\"']*)[\"']", s)
            if not m:
                continue
            var, verb, route = m.groups()
            if verb == "api_route":
                ms = re.search(r"methods\s*=\s*[\[(]([^\])]*)", s)
                methods = {x.upper() for x in re.findall(r"[\"'](\w+)[\"']", ms.group(1))} if ms else set()
            else:
                methods = {"WEBSOCKET" if verb == "websocket" else verb.upper()}
            mine = [r for r in routers.get(path, []) if r["var"] == var]
            router = ([r for r in mine if r["line"] < n] or mine or [None])[-1]
            own, builder = (router["prefix"], router["builder"]) if router else ("", None)
            stem = os.path.splitext(os.path.basename(path))[0]
            prefixes = set()
            for token, prefix in includes:
                last, mod = token.split(".")[-1], (token.split(".")[-2] if "." in token else None)
                if (builder and last == builder and mod in (None, stem)) or (not builder and (
                        token in (f"{stem}.{var}", f"{stem}_router", f"{stem}_{var}") or (var != "router" and token == var))):
                    prefixes.add(prefix)
            fn = next((d.group(1) for s2 in lines(path)[n:n + 15]
                       if (d := re.match(r"\s*(?:async\s+)?def\s+(\w+)", s2))), "")
            paths = {re.sub(r"/{2,}", "/", f"{inc}{own}{route}") or "/" for inc in (prefixes or {""})}
            handlers.append({"path": path, "line": n, "methods": methods, "paths": paths, "fn": fn})
        specs = []
        files = self._files(st)[0]
        for path in [f for f in files if "openapi" in os.path.basename(f).lower() and f.endswith(".json")][:100]:
            try:
                if os.path.getsize(os.path.join(self.root, path)) > 3_000_000:
                    continue
                doc = json.loads("\n".join(lines(path)))
            except (OSError, ValueError):
                continue
            paths = doc.get("paths") if isinstance(doc, dict) else None
            for route, ops in paths.items() if isinstance(paths, dict) else ():
                key = json.dumps(route) + ":"
                n = next((i for i, s in enumerate(lines(path), 1) if key in s), 1)
                methods = {k.upper() for k in (ops if isinstance(ops, dict) else {})
                           if k.upper() in _REF_METHODS.split("|")}
                specs.append({"path": path, "line": n, "route": route, "methods": methods})
        edges = []
        for path, n, text in self._grep(["-E", r"^[[:space:]]*location[[:space:]]+(=|\^~)?[[:space:]]*/"],
                                        ["*.conf", "*.conf.template", "*nginx*"]):
            if m := re.match(r"\s*location\s+(=|\^~)?\s*(/[^\s{]*)", text):
                edges.append({"path": path, "line": n, "prefix": m.group(2), "exact": m.group(1) == "="})
        return {"handlers": handlers, "specs": specs, "edges": edges}
