"""lookup — what Baude's ⌘K searches, as a module of its own: the names a folder has, and finding them.

A folder's names (Names) are its repository's files and branches, the Kubernetes objects its manifests and charts
define, your kubeconfig's contexts, the workloads running in your clusters and the tables of the databases running
here (the catalogue), and the names Baude's Signal has looked up there (its glossary, <home>/.baude/signal.db).
SearchSpace lists them as ⌘K does (items), with the pull requests and issues of the GitHub repositories in the folder
(GITHUB), ranks them as you type (ref_palette_rank) and finds them in a text (mentions).

baude.py's Signal is a Names, with lookups and a worker thread on top; its ⌘K is a SearchSpace of it. record.py uses
a Folder: the catalogue and the glossary without Signal, for @ in a Record's diary and for what its questions say a
name is. Nothing here needs GTK: it reads files and runs git, kubectl, docker (and psql or arangosh in a container),
and gh, to read. Kubernetes and the databases are read in lookupkube.py, git and GitHub in lookupgit.py.
"""
import collections
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse

from baudelib import wording
from lookupkube import (
    _KUBE_ACTION, _KUBE_NAME, _kube_args, db_names, kube_contexts, kube_kustomization, kube_made_as,
    kube_manifest_objects, kube_name_pattern, kube_render, kube_running, kube_yaml_scalars, pg_servers_cached,
)
from lookupgit import GITHUB, GITHUB_NAME, _git, github_data, github_icon


REF_KINDS = {"file": (wording.say("lookup.file"), wording.say("lookup.files")),
             "commit": (wording.say("lookup.commit"), wording.say("lookup.commits")),
             "branch": (wording.say("lookup.branch"), wording.say("lookup.branches")),
             "pr": (wording.say("lookup.pull_request"), wording.say("lookup.pull_requests")),
             "env": (wording.say("lookup.env_var"), wording.say("lookup.env_vars")),
             "constant": (wording.say("lookup.constant"), wording.say("lookup.constants")),
             "symbol": (wording.say("lookup.code_symbol"), wording.say("lookup.code_symbols")),
             "config": (wording.say("lookup.config_key"), wording.say("lookup.config_keys")),
             "service": (wording.say("lookup.service"), wording.say("lookup.services")),
             "k8s": (wording.say("lookup.kubernetes_object"), wording.say("lookup.kubernetes_objects")),
             "package": (wording.say("lookup.package"), wording.say("lookup.packages")),
             "cli": (wording.say("common.command"), wording.say("lookup.commands")),
             "external": (wording.say("lookup.external_tool"), wording.say("lookup.external_tools")),
             "tool": (wording.say("lookup.tool"), wording.say("lookup.tools")),
             "subagent": (wording.say("lookup.subagent_type"), wording.say("lookup.subagent_types")),
             "skill": (wording.say("lookup.skill"), wording.say("lookup.skills")),
             "route": (wording.say("lookup.http_route"), wording.say("lookup.http_routes"))}


# The glossary: one a repository (its main checkout's .baude/signal.db, its worktrees' too: signal_place), each name in
# it a checkout's and a scope's (the package a chat's folder is in, where a language server would start: "" the
# repository's top), so `store.py` in a chat in one package and in a chat in another are each its own. A location on
# another branch than the checkout's (a name only a branch the chat names has) keeps it as its rev.
REF_SCHEMA_VERSION = 2
REF_SCHEMA = """
CREATE TABLE IF NOT EXISTS entity (id INTEGER PRIMARY KEY, checkout TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '', kind TEXT, canonical TEXT, status TEXT, reason TEXT, attempts INTEGER DEFAULT 0,
    gave_up_at REAL, fingerprint TEXT, data TEXT, updated_at REAL, UNIQUE (checkout, scope, kind, canonical));
CREATE TABLE IF NOT EXISTS alias (checkout TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '', surface TEXT,
    entity_id INTEGER, PRIMARY KEY (checkout, scope, surface));
CREATE TABLE IF NOT EXISTS location (id INTEGER PRIMARY KEY, entity_id INTEGER, role TEXT, path TEXT, line INTEGER,
    snippet TEXT, snippet_start INTEGER, blob TEXT, state TEXT, commit_sha TEXT, branch TEXT, resolver TEXT,
    note TEXT, resolved_at REAL, rev TEXT);
CREATE INDEX IF NOT EXISTS location_entity ON location (entity_id);
CREATE TABLE IF NOT EXISTS mention (entity_id INTEGER, session TEXT, message TEXT, surface TEXT, head TEXT,
    dirty INTEGER, written_at REAL, seen_at REAL, PRIMARY KEY (entity_id, session, message, surface));
CREATE TABLE IF NOT EXISTS decision (checkout TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '', message TEXT,
    surface TEXT, verdict TEXT, kind TEXT, path TEXT, line INTEGER, role TEXT, note TEXT, why TEXT, by TEXT, at REAL,
    PRIMARY KEY (checkout, scope, message, surface));
PRAGMA user_version = 2;
"""


REF_LOC_FIELDS = ("id", "role", "path", "line", "snippet", "snippet_start", "blob", "state", "commit_sha", "branch",
                  "resolver", "note", "resolved_at", "rev")


def ref_key(text):
    """The one spelling of a name as written: trimmed, an ellipsised hash without its dots."""
    t = re.sub(r"\s+", " ", text.strip())
    return re.sub(r"(?<=[0-9a-f]{7})(?:…|\.\.\.)$", "", t)


# The name catalogue: every name Signal can know without being told what it is (the repository's files and branches,
# the Kubernetes objects its manifests define, your kubeconfig's contexts, the workloads running in your local
# clusters, the tables and collections of the databases running here), each reply matched against it as it's read.
# So a name with no shape of its own (orders-api, invoice_line) is found where it's written. In prose only the
# distinctive ones count (catalogue_shaped); in backticks, any the catalogue has.
CATALOGUE_STOP = frozenset("""
    main master develop development dev staging stage production prod test tests testing default public private
    local remote origin upstream head data user users job jobs app apps api web www db docs doc src lib libs build dist
    bin config configs settings index vendor node_modules kube-system kube-public kube-node-lease
    read-only follow-up built-in real-time end-to-end e-mail re-run set-up sign-in sign-up log-in opt-in opt-out
    up-to-date top-level one-off well-known long-running self-hosted open-source third-party back-end front-end
    server-side client-side e.g i.e""".split())


CATALOGUE_KINDS = ("k8s", "symbol", "branch", "file")  # (a name in several sources is looked up as the first it is)


CATALOGUE_TTL = 120  # seconds before what's running (and what the repository has) is catalogued again


_CATALOGUE_TOKEN = re.compile(r"(?<![\w./@-])[\w@][\w.@/-]*\w")


def catalogue_ref(surface, entry):
    """A link for a name the catalogue has, made from what it says (nothing looked up: Signal isn't running): a running
    database's table (its diagram, clicked; in several databases, picked from them), a file or a manifest's object (its
    file; in several, listed), a running workload (where it runs), a branch, a context. Marked "catalogue": once Signal
    runs, it's looked up as any name. A pull request or an issue, from what GitHub said of it."""
    if (listed := entry.get("github")) is not None:
        return Ref(surface, "pr", "resolved", surface, data={"catalogue": True, **github_data(listed)})
    sources = entry.get("sources") or []
    kind = {"k8s": "k8s", "branch": "branch", "file": "file"}.get(entry["kind"], "symbol")
    what, where = sources[0] if sources else (wording.say("common.name"), "")
    data, locs = {"catalogue": True}, []
    if entry.get("db"):
        live, n = entry["db"][0], len(entry["db"])
        table = live["table"]
        data["db"] = live
        database, engine, container = live["database"], live["engine"], live["container"]
        data["note"] = (wording.say("lookup.database_in", database=database, engine=engine, container=container)
                        if not table else
                        wording.say("lookup.collection_in", table=table, database=database, engine=engine,
                                    container=container) if engine == "ArangoDB" else
                        wording.say("lookup.table_in", table=table, database=database, engine=engine,
                                    container=container)) + \
            (wording.say("lookup.and_more", n=n - 1) if n > 1 else "")
        return Ref(surface, "symbol", "resolved", table or live["database"], data=data)
    for w, at in sources:
        if kind == "file" and at and not re.fullmatch(r"\d+ files", at):
            locs.append({"role": "file", "path": at, "line": None, "note": ""})
        elif kind == "k8s" and at.endswith((".yaml", ".yml", ".json", ".tpl")):
            locs.append({"role": "definition", "path": at, "line": None, "note": w})
    running = [(w, at) for w, at in sources if w.endswith(" running")]
    if kind == "k8s" and running:  # (the popover says where it runs, as a lookup's)
        data.update(kind=running[0][0].removesuffix(" running"), name=surface)
    if kind == "branch":
        data["ref"] = surface
    data["note"] = what + (f" ({where})" if where and not locs else "") + \
        (wording.say("lookup.and_more", n=len(sources) - 1) if len(sources) > 1 and not locs else "")
    return Ref(surface, kind, "ambiguous" if len(locs) > 1 else "resolved", data=data, locations=locs,
               reason=wording.say("lookup.catalogue_places", n=len(locs)) if len(locs) > 1 else "")


def catalogue_shaped(name):
    """Whether a name is distinctive enough to link wherever it's written, outside backticks: four characters at
    least, a - _ . / or a digit in it (not just a number, a version or a date), and not a common word or a name every
    project or cluster has."""
    return len(name) >= 4 and bool(re.search(r"[-_./\d]", name)) and name.lower() not in CATALOGUE_STOP and \
        not name.startswith("_") and not re.fullmatch(r"v?[\d.:/-]+\w?", name)


def catalogue_hits(text, catalogue, hints=None):
    """The names in a reply that the catalogue has: {surface: (kind, True)}, code blocks left out. In its prose, the
    distinctive ones; in backticks, any. A kubeconfig context's kind goes into hints ("context")."""
    if not catalogue or not text:
        return {}
    found = {}

    def add(name):
        entry = catalogue.get(name)
        if entry is not None and name not in found:
            found[name] = (entry["kind"], True)
            if entry["hint"] and hints is not None:
                hints.setdefault(name, entry["hint"])
    for part in re.split(r"(`[^`\n]+`)", re.sub(r"(?ms)^\s*```.*?(?:^\s*```[^\n]*$|\Z)", "", text)):
        if part.startswith("`") and part.endswith("`") and len(part) > 2:
            add(ref_key(part[1:-1]))
        else:
            for m in _CATALOGUE_TOKEN.finditer(part):
                if catalogue_shaped(m[0]):
                    add(m[0])
    return found


def ref_date(iso):
    """An ISO time as local '17 Oct 2026, 14:03'."""
    if not iso:
        return ""
    import datetime
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%-d %b %Y, %H:%M")
    except ValueError:
        return iso


class Ref:
    """One name as written in replies (its surface) and what Signal found for it."""

    def __init__(self, surface, kind, status="pending", canonical=None, reason="", data=None, locations=None):
        self.surface, self.kind, self.status = surface, kind, status
        self.canonical, self.reason = canonical or surface, reason
        self.data, self.locations = data or {}, locations or []
        self.id = self.fingerprint = self.written_at = None
        self.attempted = False  # looked up (not just loaded) this time

    @property
    def after_fact(self):
        """A cited line looked up well after the message was written: it's the line now, which may not
        be the one meant."""
        times = [loc.get("resolved_at") for loc in self.locations if loc.get("line") and loc.get("resolved_at")]
        return bool(self.written_at and times and min(times) - self.written_at > 300)

    def counts(self):
        roles = {}
        for loc in self.locations:
            roles[loc["role"]] = roles.get(loc["role"], 0) + 1
        return roles

    def summary(self):
        """One line on what it is: a row's subtitle, a link's tooltip."""
        d = self.data
        if self.status == "pending":
            return wording.say("lookup.looking_it_up")
        if self.status == "not_found":
            return f"Not found: {self.reason}"
        if self.status == "stale":
            return wording.say("lookup.stale")
        if d.get("plain"):
            return wording.say("lookup.plain")
        if self.status == "ambiguous":
            if d.get("pick"):
                return wording.say("lookup.matches_pick", n=len(self.locations), pick=d["pick"], why=self.reason)
            return wording.say("lookup.matches", n=len(self.locations), why=self.reason)
        if self.kind == "commit":
            return f"{d.get('subject', '')} ({ref_date(d.get('date'))}, +{d.get('added', 0)} −{d.get('removed', 0)})"
        if self.kind == "branch":
            return f"{d.get('subject', '')} ({ref_date(d.get('date'))})"
        if self.kind == "pr":
            state = d.get("state") or wording.say("lookup.on_github")
            return f"#{d.get('number')} {d.get('title') or ''} ({state})".replace("  ", " ")
        if self.kind == "env":
            c = self.counts()
            parts = [f"{c[r]} {r}" for r in ("secret source", "set", "read") if c.get(r)]
            return wording.say("lookup.env_var_counts", counts=", ".join(parts)) if parts else \
                wording.say("lookup.env_var_mentioned")
        if self.kind == "tool":
            n = len(d.get("input") or [])
            name = d.get("tool") or self.canonical
            return (wording.say("lookup.tool_inputs", name=name, n=n) if d.get("input") else
                    wording.say("lookup.tool_named", name=name)) + \
                (f" · {d['description'][:90]}" if d.get("description") else "")
        if self.kind in ("subagent", "skill"):
            return f"{REF_KINDS[self.kind][0]} {self.canonical}" + (f" · {d['description'][:90]}" if d.get("description") else "")
        if self.kind in ("service", "k8s", "package", "cli", "external") or (self.data.get("note") and not self.locations):
            loc = self.locations[0] if self.locations else None
            path = loc and (os.path.basename(loc["path"]) if os.path.isabs(loc["path"]) else loc["path"])
            where = (f" ({path}" + (f":{loc['line']}" if loc.get("line") else "") + ")") if loc else ""
            return (self.data.get("note") or REF_KINDS.get(self.kind, (wording.say("common.name"),))[0]) + where
        if self.kind in ("constant", "route", "symbol", "config") and self.locations:
            loc = self.locations[0]
            where = loc["path"] + (f":{loc['line']}" if loc.get("line") else "") + \
                (f" ({loc['note']})" if loc.get("note") else "")
            if loc.get("rev"):
                return wording.say("lookup.located_on", role=loc["role"].capitalize(), where=where, rev=loc["rev"])
            return wording.say("lookup.located", role=loc["role"].capitalize(), where=where)
        return self.canonical


# What makes a folder a package a language server starts in (pyright's, typescript-language-server's): a chat's folder
# in one is that package's scope in the glossary.
SCOPE_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "package.json", "tsconfig.json")


class Place(collections.namedtuple("Place", "store checkout scope top")):
    """Where a folder's names are kept (signal_place); key: the glossary and the names in it that are its own."""
    __slots__ = ()

    @property
    def key(self):
        return self.store, self.checkout, self.scope


_PLACES = {}


def signal_place(folder):
    """Where a folder's names are kept, and which of them are its own: store, the repository's .baude (its main
    checkout's: one glossary for the repository and its worktrees), else, outside git, the folder's; checkout, "" for
    the main checkout, else the worktree's top; scope, the package the folder is in (the nearest folder above it in its
    checkout with a SCOPE_MARKERS file, where a language server would start), relative to the checkout's top ("": none
    but the repository); top, the checkout's top (None outside git)."""
    folder = os.path.realpath(folder)
    if folder in _PLACES:
        return _PLACES[folder]
    top = (_git(folder, "rev-parse", "--show-toplevel") or "").strip()
    if not top:
        place = Place(os.path.join(folder, ".baude"), "", "", None)
    else:
        top = os.path.realpath(top)
        common = (_git(folder, "rev-parse", "--git-common-dir") or ".git").strip()
        common = os.path.realpath(os.path.join(folder, common))
        main = os.path.dirname(common) if os.path.basename(common) == ".git" else top
        scope, d = "", folder
        while d.startswith(top + os.sep):
            if any(os.path.exists(os.path.join(d, m)) for m in SCOPE_MARKERS):
                scope = os.path.relpath(d, top)
                break
            d = os.path.dirname(d)
        place = Place(os.path.join(main, ".baude"), "" if main == top else top, scope, top)
    _PLACES[folder] = place
    return place


def signal_store(folder, home=None):
    """Where a Signal's glossary was kept before there was one a repository (signal_place): the .baude of home (the
    folder the chat started in, folder by default), or for another checkout of it (a worktree the chat moved into),
    a folder of its own in there. Folded into the repository's when that Signal is next opened (signal_merge)."""
    folder = os.path.realpath(folder)
    home = os.path.realpath(home or folder)
    if home == folder:
        return os.path.join(home, ".baude")
    tag = hashlib.sha1(folder.encode()).hexdigest()[:6]  # (.baude/checkouts/widget-oct19-other-changes-1a2b3c)
    return os.path.join(home, ".baude", "checkouts", f"{os.path.basename(folder)}-{tag}")


def signal_glossary_exists(folder, home=None):
    """Whether the repository (or folder) has a glossary, or the folder one from before (signal_store)."""
    return any(os.path.exists(os.path.join(d, n)) for d in (signal_place(folder).store, signal_store(folder, home))
               for n in ("signal.db", "referencer.db"))


def signal_upgrade(db):
    """A glossary from before scopes (user_version below REF_SCHEMA_VERSION: each name keyed by its spelling alone)
    made over: its names the main checkout's, scope "" (it was the folder's at the repository's top), a location
    with no rev. A copy of it is kept beside it first (signal.db.v1). Nothing to do for a new one, or one done."""
    if db.execute("PRAGMA user_version").fetchone()[0] >= REF_SCHEMA_VERSION:
        return
    tables = {n for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "alias" in tables and "scope" not in [c[1] for c in db.execute("PRAGMA table_info(alias)")]:
        path = db.execute("PRAGMA database_list").fetchone()[2]
        if path and not os.path.exists(path + ".v1"):
            shutil.copy2(path, path + ".v1")
        for t in ("entity", "alias", "decision"):
            if t in tables:
                db.execute(f"ALTER TABLE {t} RENAME TO {t}_v1")
        if "location" in tables and "rev" not in [c[1] for c in db.execute("PRAGMA table_info(location)")]:
            db.execute("ALTER TABLE location ADD COLUMN rev TEXT")
        db.executescript(REF_SCHEMA)
        cols = "kind, canonical, status, reason, attempts, gave_up_at, fingerprint, data, updated_at"
        if "entity" in tables:
            db.execute(f"INSERT INTO entity (id, {cols}) SELECT id, {cols} FROM entity_v1")
        if "alias" in tables:
            db.execute("INSERT INTO alias (surface, entity_id) SELECT surface, entity_id FROM alias_v1")
        dcols = "message, surface, verdict, kind, path, line, role, note, why, by, at"
        if "decision" in tables:
            db.execute(f"INSERT INTO decision ({dcols}) SELECT {dcols} FROM decision_v1")
        for t in ("entity", "alias", "decision"):
            db.execute(f"DROP TABLE IF EXISTS {t}_v1")
        db.commit()
    db.executescript(REF_SCHEMA)  # (a new one's tables; and the version)


def signal_merge(db, store, old, checkout, scope):
    """A glossary from before there was one a repository (old: the folder whose .baude had it, signal_store) folded
    into the repository's (db, in store), as checkout's and scope's names: a name both have keeps the newer, and the
    copies of files it kept go with it. old's glossary is then renamed signal.db.merged. False if there was none."""
    import sqlite3
    path = next((p for n in ("signal.db", "referencer.db") if os.path.exists(p := os.path.join(old, n))), None)
    if path is None or os.path.realpath(old) == os.path.realpath(store):
        return False
    src = sqlite3.connect(path)
    try:
        signal_upgrade(src)  # (the same shape: its own names "" and "")
        ids = {}
        cols = ("kind", "canonical", "status", "reason", "attempts", "gave_up_at", "fingerprint", "data", "updated_at")
        for row in src.execute(f"SELECT id, {', '.join(cols)} FROM entity WHERE checkout = '' AND scope = ''"):
            e = dict(zip(("id", *cols), row))
            have = db.execute("SELECT id, updated_at FROM entity WHERE checkout = ? AND scope = ? AND kind = ? AND "
                              "canonical = ?", (checkout, scope, e["kind"], e["canonical"])).fetchone()
            if have and (have[1] or 0) >= (e["updated_at"] or 0):
                ids[e["id"]] = have[0]
                continue
            if have:
                db.execute("DELETE FROM location WHERE entity_id = ?", (have[0],))
                db.execute(f"UPDATE entity SET {', '.join(c + ' = ?' for c in cols[2:])} WHERE id = ?",
                           (*(e[c] for c in cols[2:]), have[0]))
                eid = have[0]
            else:
                eid = db.execute(f"INSERT INTO entity (checkout, scope, {', '.join(cols)}) VALUES "
                                 f"(?, ?{', ?' * len(cols)})", (checkout, scope, *(e[c] for c in cols))).lastrowid
            ids[e["id"]] = eid
            fields = [f for f in REF_LOC_FIELDS if f != "id"]
            for loc in src.execute(f"SELECT {', '.join(fields)} FROM location WHERE entity_id = ? ORDER BY id",
                                   (e["id"],)):
                db.execute(f"INSERT INTO location (entity_id, {', '.join(fields)}) VALUES (?{', ?' * len(fields)})",
                           (eid, *loc))
        for surface, eid in src.execute("SELECT surface, entity_id FROM alias WHERE checkout = '' AND scope = ''"):
            if eid in ids:
                db.execute("INSERT OR IGNORE INTO alias (checkout, scope, surface, entity_id) VALUES (?, ?, ?, ?)",
                           (checkout, scope, surface, ids[eid]))
        for row in src.execute("SELECT entity_id, session, message, surface, head, dirty, written_at, seen_at "
                               "FROM mention"):
            if row[0] in ids:
                db.execute("INSERT OR IGNORE INTO mention VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (ids[row[0]], *row[1:]))
        dcols = "message, surface, verdict, kind, path, line, role, note, why, by, at"
        for row in src.execute(f"SELECT {dcols} FROM decision WHERE checkout = '' AND scope = ''"):
            db.execute(f"INSERT OR IGNORE INTO decision (checkout, scope, {dcols}) VALUES (?, ?{', ?' * 11})",
                       (checkout, scope, *row))
        db.commit()
    finally:
        src.close()
    blobs, into = os.path.join(old, "blobs"), os.path.join(store, "blobs")
    if os.path.isdir(blobs):
        os.makedirs(into, exist_ok=True)
        for name in os.listdir(blobs):
            if not os.path.exists(os.path.join(into, name)):
                shutil.copy2(os.path.join(blobs, name), os.path.join(into, name))
    os.replace(path, os.path.join(os.path.dirname(path), "signal.db.merged"))
    return True


REF_ICON_KINDS = {"commit": "git", "branch": "git", "pr": "git", "k8s": "k8s", "symbol": "code", "constant": "code",
                  "env": "env", "route": "route", "config": "config", "cli": "cli", "package": "package",
                  "service": "service", "tool": "claude", "subagent": "claude", "skill": "claude",
                  "external": "external"}


def ref_icon(kind, data=None, roles=()):
    """The logo a link to this kind of thing has before it (REF_ICON_SVG), or None: a running database's table or
    collection, or one the code defines, has the database's; a folder its own; a pull request where it stands."""
    live = (data or {}).get("db")
    if kind == "pr" and (data or {}).get("state"):
        return github_icon(data.get("issue"), data["state"].lower())
    if live or any(r in ("table", "column") for r in roles):
        return "arango" if (live or {}).get("engine") == "ArangoDB" else "db"
    if kind == "file":
        return "folder" if "folder" in roles else "file"
    return REF_ICON_KINDS.get(kind)


def ref_palette_word(name, canon, extra, w):
    """How well one typed word matches a name (lower is better), or None: the name starting with it, a word in it (after
    . / - _ : or a space, or a capital) starting with it, it anywhere in the name, the same in its canonical name, its
    letters in order in the name, it in the summary or kind."""
    if name.startswith(w):
        return 0
    if re.search(r"(?:^|[./_\-\s:])" + re.escape(w), name):
        return 1
    if w in name:
        return 2
    if canon and (canon.startswith(w) or w in canon):
        return 3
    it = iter(name)
    if all(ch in it for ch in w):
        return 4
    if w in extra:
        return 5
    return None


def item_when(item):
    """When an item was last touched, as epoch seconds, or None: a chat's last use (its "at"), a file's or a branch's
    last change (as the catalogue found it: its entry's "at"), a pull request's or issue's last update, a looked-up
    name's file's last change (stat once, kept on the item: those are few)."""
    if "_when" in item:
        return item["_when"]
    entry = item.get("entry") or {}
    when = next((t for t in (item.get("at"), entry.get("at")) if isinstance(t, (int, float))), None)
    listed = entry.get("github")
    if when is None and listed and listed.get("updated"):
        try:
            when = datetime.datetime.fromisoformat(listed["updated"].replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    if when is None and item.get("path") and not item.get("entry"):
        try:
            when = os.stat(item["path"]).st_mtime
        except (OSError, ValueError, TypeError):
            pass
    item["_when"] = when
    return when


RECENT_STEPS = (3600, 86400, 7 * 86400, 30 * 86400)  # (s: within the hour, the day, the week, the month)


def recency(item, now=None):
    """How lately an item was touched, in steps (RECENT_STEPS: 0 within the hour … 4 longer ago, or not known: a name
    with no time isn't put below an old one for it)."""
    when = item_when(item)
    if when is None:
        return len(RECENT_STEPS)
    age = (now or time.time()) - when
    return next((i for i, step in enumerate(RECENT_STEPS) if age < step), len(RECENT_STEPS))


def ref_palette_rank(item, q):
    """How well a name matches what's typed (lower is better), or None. Nothing typed: what was touched this month,
    the latest first (item_when), then the rest (older, or with no time) shorter name first. Typed: each word must
    match (in any order: "relay job" finds relay_gen_job), the worst-matching word decides, then all of them together,
    then the whole name being what's typed, then the typed words being in the name's order, then how lately it was
    touched (recency, in steps: a close match isn't passed by a looser one for being newer), then the shorter name. (A pull request or an issue goes by its number, its
    label: its repository's name is in every one of them, and any letters typed would find them all.)"""
    name = (item.get("label") or item["surface"]).lower()
    words = q.split()
    if not words:
        step = recency(item)
        return (0, 0, 0, step, -item_when(item) if step < len(RECENT_STEPS) else 0, len(name))
    # (a chat's summary says how long ago too, which isn't what it's searched by: its "where", its folder)
    canon, extra = item["canonical"].lower(), f"{item.get('where', item['summary'])} {item['what']}".lower()
    ranks = []
    for w in words:
        r = ref_palette_word(name, canon, extra, w)
        if r is None:
            return None
        ranks.append(r)
    at = [name.find(w) for w in words]
    in_order = all(x >= 0 for x in at) and at == sorted(at)
    return (max(ranks), sum(ranks), 0 if name == " ".join(words) else 1, 0 if in_order else 1, recency(item), len(name))


def find_spans(text, words, letters=False):
    """Where what was typed is in text (any case): [(start, end)] in characters, in order and merged. words: a string
    (found whole) or a list of words (each found wherever it is). letters: a word that isn't in text whole is found
    letter by letter, in order (as ⌘K matches)."""
    low, spans = text.lower(), []
    for w in ([words] if isinstance(words, str) else words):
        w = w.lower()
        if not w:
            continue
        start, found = 0, False
        while (i := low.find(w, start)) >= 0:
            spans.append((i, i + len(w)))
            start, found = i + len(w), True
        if not found and letters:
            at, pos = [], 0
            for ch in w:
                if (pos := low.find(ch, pos)) < 0:
                    break
                at.append((pos, pos + 1))
                pos += 1
            else:
                spans += at
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


class Names:
    """One folder's names: its indexes (files, the Kubernetes objects it defines, the text of files read; each kept
    for one state of the repository), the catalogue made from them and from what's running here, and the glossary
    Signal keeps in home's .baude. Its methods read files and run git, kubectl and docker: off the main loop."""

    BLOB_LIMIT = 4 * 1024 * 1024  # bigger files aren't anchored (or copied)

    def __init__(self, folder, home=None):
        self.folder, self.home = folder, home or folder
        # the repository's glossary, and which of its names are this one's: its checkout's, in its package (scope)
        self.place = signal_place(folder)
        self.dir, self.checkout, self.scope = self.place.key
        self.olds = {signal_store(folder, self.home)}  # (where its glossaries from before were: folded in, _open)
        self.db = None  # the glossary, once opened (_open)
        self.top = (_git(folder, "rev-parse", "--show-toplevel") or "").strip() or None
        self.root = self.top or folder  # where paths are relative to
        self._cache = {}  # indexes (files, routes, env prefixes) for one state of the repository
        self._texts = {}  # files read while resolving, by (path, mtime)

    def _tell(self, activity=False, entry=None, read=0):
        """What it's doing (a Signal shows it on its page): here, no one to tell."""

    def make_catalogue(self):
        """The name catalogue as things are now (_build_catalogue)."""
        return self._build_catalogue(self._repo_state())

    def glossary(self):
        """The names Signal has looked up here (Refs), read from its glossary without opening it for writing; []
        if there's none: this one's (its checkout's and scope's) first, then the rest of its checkout's, a spelling
        once. A glossary from before (signal_store) not yet folded in is read as it is."""
        import sqlite3
        path = os.path.join(self.dir, "signal.db")
        olds = [os.path.join(d, "signal.db") for d in self.olds if os.path.exists(os.path.join(d, "signal.db"))]
        out, seen = [], set()
        for p in [path] * os.path.exists(path) + olds:
            try:
                db = sqlite3.connect(f"file:{urllib.parse.quote(p)}?mode=ro", uri=True)
                try:
                    scoped = "scope" in [c[1] for c in db.execute("PRAGMA table_info(alias)")]
                    rows = db.execute("SELECT surface, scope FROM alias WHERE checkout = ? ORDER BY scope != ?, "
                                      "scope != ''", (self.checkout, self.scope)) if scoped else \
                        [(s, None) for (s,) in db.execute("SELECT surface FROM alias")]
                    for surface, scope in list(rows):
                        if surface not in seen and (r := self._load(surface, db, scope)) is not None:
                            seen.add(surface)
                            out.append(r)
                finally:
                    db.close()
            except sqlite3.Error:
                continue
        return out

    def _open(self):
        path = os.path.join(self.dir, "signal.db")
        old = os.path.join(self.dir, "referencer.db")  # (Signal was Referencer: its glossary goes on)
        if self.db is None and not os.path.exists(path) and os.path.exists(old):
            try:
                os.rename(old, path)
            except OSError:
                pass
        if self.db is not None and not os.path.exists(path):  # .baude was deleted: start a new one
            self.db.close()
            self.db = None
        if self.db is None:
            import sqlite3
            os.makedirs(self.dir, exist_ok=True)
            ignore = os.path.join(self.dir, ".gitignore")
            if not os.path.exists(ignore):  # the folder ignores itself: nothing in the repository changes
                with open(ignore, "w") as f:
                    f.write("# Baude Signal's glossary (see Baude's README)\n*\n")
            self.db = sqlite3.connect(path)
            signal_upgrade(self.db)
        for old in list(self.olds):  # (a glossary from before, the folder's: folded in, once)
            self.olds.discard(old)
            try:
                if signal_merge(self.db, self.dir, old, self.checkout, self.scope):
                    print(f"lookup: folded {old}'s glossary into {self.dir}'s", file=sys.stderr)
            except Exception as e:  # noqa: BLE001 (a broken one: left where it is)
                print(f"lookup: couldn't fold {old}'s glossary in: {e!r}", file=sys.stderr)
        return self.db

    def _build_catalogue(self, st):
        """The name catalogue: {name: {"kind" (what it's looked up as), "hint", "sources": [(what, where)]}}, from the
        repository's files (every one: by a name few of them share, else by its path) and branches, its Kubernetes
        objects, your kubeconfig's contexts (and the names their tools made their clusters with), the workloads running
        in your local clusters and the tables and collections of the databases running here. Only reads, as the lookups
        do."""
        cat = {}

        def add(name, kind, what, where="", hint=None, db=None, at=None):
            if not name or len(name) < 2 or kind != "file" and (len(name) > 120 or "{" in name or name.startswith("_")):
                return  # (a file's name or path is one, whatever it's like)
            e = cat.setdefault(name, {"kind": kind, "hint": hint, "sources": []})
            if db is not None:  # (a running database's: where, as _live_db_name says it)
                e.setdefault("db", []).append(db)
            if at is not None and at > (e.get("at") or 0):  # (when it was last changed: ⌘K's order, item_when)
                e["at"] = at
            if CATALOGUE_KINDS.index(kind) < CATALOGUE_KINDS.index(e["kind"]):
                e["kind"], e["hint"] = kind, hint
            elif kind == e["kind"] and hint is None:
                e["hint"] = None  # (an object of that name comes before the context)
            if (what, where) not in e["sources"] and len(e["sources"]) < 8:
                e["sources"].append((what, where))
        _files, _dirs, by_name = self._files(st)
        top = self.top or self.root

        def changed(path):
            try:
                return os.stat(os.path.join(top, path)).st_mtime
            except (OSError, ValueError):
                return None
        for base, paths in by_name.items():
            named = "." in base.strip(".") and len(paths) <= 3  # (README.md, __init__.py: everyone's)
            times = [changed(p) for p in paths]
            if named:
                add(base, "file", wording.say("lookup.file"), paths[0] if len(paths) == 1 else f"{len(paths)} files",
                    at=max((t for t in times if t is not None), default=None))
            if not named or len(paths) > 1:  # the rest by their paths: twelve compose.yaml, a Dockerfile
                for p, t in zip(paths, times):
                    add(p, "file", wording.say("lookup.file"), p, at=t)
        if self.top:
            for line in (_git(self.top, "for-each-ref", "--format=%(refname:short) %(committerdate:unix)", "refs/heads",
                              "refs/remotes") or "").splitlines():
                b, _, when = line.rpartition(" ")
                if b and b != "HEAD" and not b.endswith("/HEAD"):
                    add(b, "branch", wording.say("lookup.branch"), at=float(when) if when.isdigit() else None)
        for o in self._kube_index(st)["objects"]:
            for n, _how in o["names"]:
                if re.fullmatch(_KUBE_NAME, n):
                    add(n, "k8s", o["kind"], o["path"])
        for c in kube_contexts():
            add(c["name"], "k8s", wording.say("lookup.kubeconfig_cluster") if c.get("cluster_only") else
                wording.say("lookup.kubeconfig_context"),
                hint="context")
            if made := kube_made_as(c):
                add(made[1], "k8s", wording.say("lookup.made_cluster", tool=made[0]), c["name"], hint="context")
        try:
            for r in kube_running(None):
                add(r["name"], "k8s", f"{r['kind']} running", f"{r['context']} · {r['namespace']}")
        except Exception:  # noqa: BLE001 (kubectl gone: nothing running to say)
            pass
        try:
            engines = {s["container"]: "ArangoDB" if s.get("engine") == "arango" else "Postgres"
                       for s in pg_servers_cached()}
            for name, places in db_names().items():
                for container, db, table in places[:8]:
                    add(name, "symbol", "Database table" if table else "Database", f"{db} ({container})" if table
                        else container, db={"table": table, "database": db, "container": container,
                                            "engine": engines.get(container, "Postgres")})
        except Exception:  # noqa: BLE001 (Docker or kubectl gone)
            pass
        return cat

    def _load(self, surface, db=None, scope=None):
        """A name as the glossary has it, this one's (its checkout's, its scope's, or scope's); None if it hasn't.
        (scope None in a glossary from before scopes: by its spelling alone.)"""
        db = db or self._open()
        sql = "SELECT e.id, e.kind, e.canonical, e.status, e.reason, e.data, e.fingerprint " \
              "FROM alias a JOIN entity e ON e.id = a.entity_id WHERE a.surface = ?"
        scoped = db is self.db or scope is not None
        row = db.execute(sql + " AND a.checkout = ? AND a.scope = ?", (surface, self.checkout, self.scope if scope is
                                                                       None else scope)).fetchone() \
            if scoped else db.execute(sql, (surface,)).fetchone()
        if row is None:
            return None
        eid, kind, canonical, status, reason, data, fp = row
        cols = REF_LOC_FIELDS if scoped else REF_LOC_FIELDS[:-1]  # (no rev before scopes)
        locs = [dict(zip(cols, r)) for r in db.execute(
            f"SELECT {', '.join(cols)} FROM location WHERE entity_id = ? ORDER BY id", (eid,))]
        ref = Ref(surface, kind, status, canonical, reason or "", json.loads(data or "{}"), locs)
        ref.id, ref.fingerprint = eid, fp
        ref.written_at = db.execute("SELECT MIN(written_at) FROM mention WHERE entity_id = ?", (eid,)).fetchone()[0]
        return ref

    def _repo_state(self):
        """HEAD, the branch and every changed file, with a fingerprint of them (once per job)."""
        head = branch = raw = ""
        status = {}
        if self.top:
            head = (_git(self.top, "rev-parse", "HEAD") or "").strip()
            branch = (_git(self.top, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
            raw = _git(self.top, "status", "--porcelain=v1", "-z", "--untracked-files=all") or ""
            parts, i = raw.split("\0"), 0
            while i < len(parts):
                entry, i = parts[i], i + 1
                if len(entry) > 3:
                    status[entry[3:]] = entry[:2]
                    if entry[0] in "RC":
                        i += 1  # a rename's old path follows
        # a file edited again stays "modified": its mtime is what changes
        mtimes = "".join(f"{os.stat(os.path.join(self.top, p)).st_mtime_ns}" if os.path.exists(os.path.join(self.top, p))
                         else "-" for p in sorted(status)[:5000]) if self.top else ""
        fp = hashlib.sha1((head + raw + mtimes).encode()).hexdigest() if self.top else f"nogit-{int(time.time() // 60)}"
        return {"head": head, "branch": branch, "status": status, "fp": fp}

    def _index(self, name, st, build):
        key = (name, st["fp"])
        if key not in self._cache:
            self._cache = {k: v for k, v in self._cache.items() if k[1] == st["fp"]}
            self._tell(activity=wording.say("lookup.indexing", what=name.replace("_", " ")))
            t0 = time.monotonic()
            self._cache[key] = build()
            built = self._cache[key]
            size = len(built[0]) if name == "files" else sum(map(len, built.values())) if isinstance(built, dict) \
                else len(built)
            self._tell(entry=f"Indexed {name.replace('_', ' ')}: {size} in {time.monotonic() - t0:.1f} s")
        return self._cache[key]

    def _files(self, st):
        def build():
            if self.top:
                files = [f for f in (_git(self.top, "ls-files", "-z", "-co", "--exclude-standard") or "").split("\0") if f]
            else:
                files = []
                for d, subs, names in os.walk(self.root):
                    subs[:] = [s for s in subs if not s.startswith(".") and s not in ("node_modules", "__pycache__")]
                    files += [os.path.relpath(os.path.join(d, n), self.root) for n in names]
                    if len(files) > 50000:
                        break
            dirs, by_name = set(), {}
            for f in files:
                by_name.setdefault(os.path.basename(f), []).append(f)
                d = os.path.dirname(f)
                while d and d not in dirs:
                    dirs.add(d)
                    d = os.path.dirname(d)
            return files, dirs, by_name
        return self._index("files", st, build)

    def _grep(self, args, paths=(), limit=3000):
        """git grep over the folder's files (tracked and untracked, not ignored): [(path, line, text)]. An -E pattern
        spells a space [[:space:]]: Apple's git (macOS) doesn't know \\s."""
        if self.top:
            cmd = ["grep", "-n", "-I", "--untracked", *args, "--", *(paths or ["."]),
                   ":(exclude)*.lock", ":(exclude)*-lock.json", ":(exclude)*.min.js", ":(exclude).baude"]
        else:
            cmd = ["grep", "--no-index", "-n", "-I", *args, "--", *(paths or ["."])]
        hits = []
        for line in (_git(self.root, *cmd, ok=(0, 1), timeout=60) or "").splitlines()[:limit]:
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[1].isdigit():
                hits.append((parts[0], int(parts[1]), parts[2]))
        return hits

    def _grep_files(self, args, paths=()):
        """The files git grep finds a match in (tracked and untracked, not ignored)."""
        if self.top:
            cmd = ["grep", "-l", "-I", "--untracked", *args, "--", *(paths or ["."]), ":(exclude).baude"]
        else:
            cmd = ["grep", "--no-index", "-l", "-I", *args, "--", *(paths or ["."])]
        return set((_git(self.root, *cmd, ok=(0, 1), timeout=60) or "").splitlines())

    def _text(self, path):
        """A file's text (a path relative to the repository), kept while it doesn't change."""
        full = os.path.join(self.root, path)
        try:
            key = (path, os.stat(full).st_mtime_ns)
            if key not in self._texts:
                with open(full, encoding="utf-8", errors="replace") as f:
                    self._texts[key] = f.read(self.BLOB_LIMIT)
                while len(self._texts) > 50:
                    del self._texts[next(iter(self._texts))]
            return self._texts[key]
        except OSError:
            return ""

    def _kube_index(self, st):
        return self._index("kube", st, lambda: self._build_kube(st))

    def _build_kube(self, st):
        """Every Kubernetes object the repository defines: {"objects", "charts", "releases"}. An object: kind, names
        [(name, how)] (as written; rendered for each release of its chart; as an overlay renames it), pattern and
        literal (a templated name's, matched when no release renders it), raw (its name as written), namespace,
        labels, path and line, group (its chart, kustomize base or folder: one app's objects), chart, release,
        overlay, generated (a kustomize generator's, which gets a hash), resolver."""
        files, _dirs, by_name = self._files(st)
        charts = {}
        for f in by_name.get("Chart.yaml", []):
            root = os.path.dirname(f)
            name = re.search(r"(?m)^name:\s*[\"']?([\w.-]+)", self._text(f))
            vfiles = sorted((x for x in files if os.path.dirname(x) == root and
                             re.fullmatch(r"values[\w.-]*\.ya?ml", os.path.basename(x))),
                            key=lambda x: (os.path.basename(x) not in ("values.yaml", "values.yml"), x))
            default = [x for x in vfiles if os.path.basename(x) in ("values.yaml", "values.yml")]
            charts[root] = {"root": root, "name": name.group(1) if name else os.path.basename(root) or "chart",
                            "values": kube_yaml_scalars(self._text(default[0])) if default else {},
                            "values_files": vfiles, "releases": []}
        releases = self._kube_releases(charts)
        kz = {}
        for f in by_name.get("kustomization.yaml", []) + by_name.get("kustomization.yml", []) + \
                by_name.get("Kustomization", []):
            kz[os.path.dirname(f)] = {**kube_kustomization(self._text(f)), "path": f}

        def resource(d, r):
            return os.path.normpath(os.path.join(d, r)) if d else os.path.normpath(r)

        def base_of(path):  # the kustomize folder that lists this file, if one does
            d = os.path.dirname(path)
            return d if d in kz and any(resource(d, r) == path for r in kz[d]["resources"]) else None
        objects, by_file = [], {}
        for path in sorted(self._grep_files(["-e", "^kind:"], ["*.yaml", "*.yml"]))[:5000]:
            chart = next((c for r, c in charts.items() if path.startswith((r + "/" if r else "") + "templates/")), None)
            for o in kube_manifest_objects(self._text(path)):
                base = {"kind": o["kind"], "raw": o["name"], "names": [], "namespace": o["namespace"],
                        "ns_line": o.get("ns_line"), "labels": o["labels"], "path": path, "line": o["line"], "chart": chart["root"] if chart else None,
                        "group": chart["root"] if chart else base_of(path) or os.path.dirname(path), "release": None,
                        "pattern": None, "literal": 0, "resolver": "manifest"}
                if not _KUBE_ACTION.search(o["name"]):
                    made = [{**base, "names": [(o["name"], "")]}]
                    by_file.setdefault(path, []).extend(made)
                elif chart:
                    regex, literal = kube_name_pattern(o["name"])
                    templated_ns = not o["namespace"] or _KUBE_ACTION.search(o["namespace"])
                    made = [{**base, "names": [(n, f"release {r['release']}")], "release": r["release"], "resolver": "helm",
                             "namespace": r["namespace"] if templated_ns else o["namespace"]}
                            for r in chart["releases"] if (n := kube_render(o["name"], chart, r["release"]))]
                    made.append({**base, "pattern": regex, "literal": literal, "resolver": "helm"})
                else:
                    continue  # (a template outside a chart: nothing to render it with)
                objects += made

        def emitted(d, seen=()):  # the objects a kustomization makes, named as it names them
            k, out = kz[d], []
            for r in k["resources"]:
                p = resource(d, r)
                if p in kz and p not in seen and p != d:
                    out += emitted(p, seen + (d,))
                else:
                    out += [dict(o, names=list(o["names"])) for o in by_file.get(p, ())]
            out += [{"kind": kind, "raw": name, "names": [(name, f"generated by {k['path']}")], "namespace": None,
                     "labels": {}, "path": k["path"], "line": line, "chart": None, "group": d or ".", "release": None,
                     "pattern": None, "literal": 0, "resolver": "kustomize", "generated": k["hash"]}
                    for kind, name, line in k["generated"]]
            for o in out:
                if k["prefix"] or k["suffix"]:
                    how = ", ".join(x for x in (f"namePrefix {k['prefix']}" if k["prefix"] else "",
                                                f"nameSuffix {k['suffix']}" if k["suffix"] else "") if x)
                    o["names"] = [(k["prefix"] + n + k["suffix"], f"kustomize overlay {d or '.'} ({how})")
                                  for n, _how in o["names"]]
                    o["overlay"] = d or "."
                if k["namespace"]:  # (set here: the manifest's own namespace: line no longer says where)
                    o["namespace"], o["ns_line"] = k["namespace"], None
            return out
        made = set()
        for d in sorted(kz):
            for o in emitted(d):
                if (o.get("overlay") or o["resolver"] == "kustomize") and \
                        (key := (o["kind"], o["names"][0][0], o["path"], o["line"])) not in made:
                    made.add(key)
                    objects.append(o)
        return {"objects": objects, "charts": charts, "releases": releases, "kustomizations": kz}

    def _kube_releases(self, charts):
        """The Helm releases the repository installs: its helm upgrade --install and helm install lines (scripts,
        workflows, Makefiles, docs), each added to its chart's releases: [{release, chart, namespace, path, line}]."""
        out, seen = [], set()
        if not charts:
            return out
        for path, n, text in self._grep(["-F", "-e", "helm upgrade", "-e", "helm install"], limit=2000):
            lines, cmd, j = self._text(path).splitlines(), text[text.find("helm "):], n
            while cmd.rstrip().endswith("\\") and j < min(len(lines), n + 12):  # (continued on the next lines)
                cmd, j = cmd.rstrip()[:-1] + " " + lines[j].strip(), j + 1
            words = [w.strip("\"'") for w in re.split(r"\s(?:&&|\|\||\||#)\s|[;`]", cmd)[0].split()]
            opts, pos = _kube_args(words)
            if len(pos) < 3 or pos[0] not in ("upgrade", "install") or re.search(r"[$<{*]", pos[1] + pos[2]) or \
                    not re.fullmatch(_KUBE_NAME, pos[1]):
                continue
            release, arg = pos[1], pos[2].rstrip("/") or "."
            near = {os.path.normpath(os.path.join(os.path.dirname(path), arg)), os.path.normpath(arg)}
            near |= {"" for x in near if x == "."}
            root = next((r for r in charts if r in near), None) or \
                next((r for r in charts if r and r.endswith("/" + arg.lstrip("./"))), None)
            if root is None or (release, root) in seen:
                continue
            seen.add((release, root))
            ns = opts.get("-n") or opts.get("--namespace")
            r = {"release": release, "chart": root, "namespace": ns if ns and re.fullmatch(_KUBE_NAME, ns) else None,
                 "path": path, "line": n}
            charts[root]["releases"].append(r)
            out.append(r)
        return out


# ---------------------------------------------------------------- what ⌘K lists, and searching it

def items(source):
    """What ⌘K lists: every name the glossary has (looked up from replies) and every one the name catalogue has that
    it doesn't (the repository's files and branches, its Kubernetes objects, your contexts, the workloads running in
    your clusters, the tables of the databases running here), and the pull requests and issues of the GitHub
    repositories in its folder (GITHUB). source: a Signal or a Folder (refs, catalogue, root).
    [{surface, canonical, icon, what, summary, faint, kind, path, line, entry}]: kind as Signal looks it up, path
    absolute (or None), line (or None), entry the catalogue's (None for a looked-up name; GitHub's say so, "github").
    (And GitHub's a label: what a list shows of the name, the logo before it.)"""
    if source is None:
        return []
    root = getattr(source, "root", None) or ""
    out, listed = [], set()
    github = GITHUB.listed(root)
    on_github = {it["surface"] for it in github}
    for ref in list(source.refs.values()):
        if ref.status in ("pending", "not_found"):  # (a name looked for and not there is nothing to open)
            continue
        if ref.kind == "pr" and ref.canonical in on_github:  # (listed below, as it stands now)
            continue
        listed.add(ref.surface)
        at = next((l for l in ref.locations if l.get("path")), None)
        out.append({"surface": ref.surface, "canonical": ref.canonical or "", "faint": False,
                    "icon": ref_icon(ref.kind, ref.data, [l["role"] for l in ref.locations]),
                    "what": REF_KINDS.get(ref.kind, (ref.kind,))[0], "summary": ref.summary(), "kind": ref.kind,
                    "path": os.path.join(root, at["path"]) if at else None, "line": at.get("line") if at else None,
                    "entry": None})
    for name, e in list(source.catalogue.items()):
        if name in listed:
            continue
        sources = e.get("sources") or [(wording.say("common.name"), "")]
        what, where = sources[0]
        icon = {"k8s": "k8s", "branch": "git", "file": "file"}.get(e["kind"]) or \
            ("db" if what.startswith("Database") else "code")
        more = wording.say("lookup.more", n=len(sources) - 1) if len(sources) > 1 else ""
        # (a file's path, or the manifest a Kubernetes object is in; not where a workload runs)
        path = where if e["kind"] == "file" and where and not re.fullmatch(r"\d+ files", where) or \
            e["kind"] == "k8s" and "/" in where and not what.endswith(" running") else None
        out.append({"surface": name, "canonical": "", "faint": False, "icon": icon, "what": what,
                    "summary": (where or what) + more, "kind": e["kind"], "line": None,
                    "path": os.path.join(root, path) if path else None, "entry": e})
    out += map(dict, github)  # (a copy each: what uses them adds to them, as Baude's open)
    return out


class SearchSpace:
    """What ⌘K searches: a source's items (items), ranked as you type (ref_palette_rank), and found in a text
    (mentions). The source is a Signal (Baude's own chat's names, in @ and ⌘K) or a Folder (Record's @ and ⌘K, and what
    its questions say a name is).
    schedule(ms, fn) waits for a catalogue being made (GLib.timeout_add, unless given)."""

    def __init__(self, source, schedule=None):
        self.source, self.schedule, self._items, self._by = source, schedule, None, {}
        self._made = (None, None, None)  # (the source's catalogue and refs, and GitHub's list, the items were made from)

    @property
    def root(self):
        return getattr(self.source, "root", None)

    @property
    def busy(self):
        """The catalogue being made: what's listed isn't all there is yet."""
        return self.source is not None and self.source.catalogue_busy

    def refresh(self, on_ready=None):
        """Catalogue the names afresh if it's been a while; on_ready() once that's in, or once some of it is (a source
        whose catalogue comes in parts: Baude's HostFolder; and Record's Spaces, a folder at a time), asked again then
        for the rest while it's busy. What waits for it (a GLib source, to remove if on_ready
        isn't wanted any more), or None: nothing to wait for."""
        if self.source is None:
            return None
        self.source.catalogue_soon()
        if not self.source.catalogue_busy:
            return None
        schedule = self.schedule
        if schedule is None:
            from gi.repository import GLib
            schedule = GLib.timeout_add

        made = getattr(self.source, "catalogue", None)

        def check():
            if self.source.catalogue_busy and getattr(self.source, "catalogue", None) is made:
                return True
            self._items = None
            if on_ready is not None:
                on_ready()
            return False
        return schedule(400, check)

    def make_items(self):
        return items(self.source)

    def items(self):
        """The items (made once, and again once the source's catalogue or glossary or GitHub's list is a new one: one
        asked for before the catalogue was in isn't kept past it)."""
        made = (getattr(self.source, "catalogue", None), getattr(self.source, "refs", None), GITHUB.listed(self.root))
        if self._items is None or any(x is not y for x, y in zip(made, self._made)):
            self._items, self._made = self.make_items(), made
            self._by = {it["surface"]: it for it in self._items}
        return self._items

    def by_surface(self):
        self.items()
        return self._by

    def search(self, q, limit=None):
        """The items matching what's typed, best first (a place's older version last, and those later says), at most
        limit; and how many matched."""
        q = q.strip().lower()
        ranked = sorted(((rank, it) for it in self.items() if (rank := ref_palette_rank(it, q)) is not None),
                        key=lambda x: (x[1]["faint"], self.later(x[1]), x[0], x[1]["surface"].lower()))
        return [it for _r, it in ranked[:limit]], len(ranked)

    def later(self, item):
        """Is an item listed after the rest, however well it matches (Baude's: another host's)?"""
        return False

    def issues(self, q=""):
        """What # lists, and how many: the pull requests and issues there are, issues first, the open ones before the
        rest, the latest first. Digits typed find the numbers starting with them, that number first (GitHub's pull
        requests and issues are numbered as one: whichever it is; in two repositories, the first's). Anything else is
        words of a title, each starting a word in it, and nothing else: not its repository or its kind, as what follows
        a # is as likely a hashtag."""
        q, found = q.strip().lower(), []
        words, items = [re.compile(r"(?<!\w)" + re.escape(w)) for w in q.split()], self.items()
        exact = next((it for it in items if it.get("label") == q), None)
        for it in items:
            if (listed := (it.get("entry") or {}).get("github")) is None:
                continue
            if q.isdigit():
                rank = 0 if it is exact else 1 if it["label"] == q else 2 if it["label"].startswith(q) else None
            else:
                rank = 0 if all(w.search(listed["title"].lower()) for w in words) else None
            if rank is not None:
                found.append(((rank, listed["pr"], listed["state"] != "open"), listed["updated"], it))
        found.sort(key=lambda x: x[1], reverse=True)
        found.sort(key=lambda x: x[0])  # (stable: the latest first among equals)
        return [it for _r, _at, it in found], len(found)

    def find(self, surface):
        """The item called exactly surface (or as a backticked name is keyed), or None."""
        by = self.by_surface()
        return by.get(surface) or by.get(ref_key(surface))

    def mentions(self, text):
        """The items a text names, in the order it names them: in backticks, any listed; in its prose, the
        distinctive ones (catalogue_shaped) and the CamelCase names Signal looked up; code blocks left out, as
        catalogue_hits has it."""
        if not text:
            return []
        by, found = self.by_surface(), {}
        for part in re.split(r"(`[^`\n]+`)", re.sub(r"(?ms)^\s*```.*?(?:^\s*```[^\n]*$|\Z)", "", text)):
            if part.startswith("`") and part.endswith("`") and len(part) > 2:
                name = next((n for n in (part[1:-1], ref_key(part[1:-1])) if n in by), None)
                names = [name] if name else []
            else:
                # (and a name Signal looked up that's written as code is, RelayRunner: a quote from shown text has
                # lost its backticks)
                names = [m[0] for m in sorted((*GITHUB_NAME.finditer(part), *_CATALOGUE_TOKEN.finditer(part)),
                                              key=lambda m: m.start()) if m[0] in by and (
                    catalogue_shaped(m[0]) or by[m[0]]["entry"] is None and re.search(r"[a-z][A-Z]", m[0]))]
            for n in names:
                found.setdefault(n, by[n])
        return list(found.values())


class Folder(Names):
    """A folder's names for a search without Signal (Record's): Signal's glossary read (never written), and the
    catalogue made in a thread of its own when asked (catalogue_soon), each swapped in whole once made. What
    SearchSpace reads of a Signal: refs, catalogue, catalogue_busy, catalogue_soon(), root."""

    _instances = {}

    @classmethod
    def for_folder(cls, folder, home=None):
        """The Folder for folder (one per folder: its catalogue is made once and kept); its glossary is the
        repository's, as Signal keeps it (signal_place), its package's names first (glossary)."""
        folder = os.path.realpath(folder)
        home = os.path.realpath(home or folder)
        if (folder, home) not in cls._instances:
            cls._instances[(folder, home)] = cls(folder, home)
        return cls._instances[(folder, home)]

    def __init__(self, folder, home=None):
        super().__init__(folder, home)
        self.refs, self.catalogue, self.catalogue_at, self.catalogue_busy = {}, {}, 0.0, False

    def catalogue_soon(self):
        if not self.catalogue_busy and time.time() - self.catalogue_at > CATALOGUE_TTL:
            self.catalogue_busy = True
            threading.Thread(target=self._make, daemon=True, name="lookup-catalogue").start()

    def _make(self):
        refs = catalogue = None
        try:
            refs = {r.surface: r for r in self.glossary()}
            catalogue = self.make_catalogue()
        except Exception as e:  # noqa: BLE001 (git, kubectl or docker gone wrong: tried again later)
            print(f"lookup: cataloguing {self.folder} failed: {e!r}", file=sys.stderr)
        finally:
            if refs is not None:
                self.refs = refs
            if catalogue is not None:
                self.catalogue = catalogue
            self.catalogue_at, self.catalogue_busy = time.time(), False


def full_time(at):
    """A time in full, local: 00:00 Mon 4th May 2020. What every relative time says under the pointer (time_tip)."""
    t = time.localtime(at)
    return time.strftime("%H:%M %a ", t) + wording.say("time.day_of_month", day=t.tm_mday) + " " + \
        time.strftime("%B %Y", t)
