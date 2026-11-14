"""lookup — what Baude's ⌘K searches, as a module of its own: the names a folder has, and finding them.

A folder's names (Names) are its repository's files and branches, the Kubernetes objects its manifests and charts
define, your kubeconfig's contexts, the workloads running in your clusters and the tables of the databases running
here (the catalogue), and the names Baude's Signal has looked up there (its glossary, <home>/.baude/signal.db).
SearchSpace lists them as ⌘K does (items), with the pull requests and issues of the GitHub repositories in the folder
(GITHUB), ranks them as you type (ref_palette_rank) and finds them in a text (mentions).

baude.py's Signal is a Names, with lookups and a worker thread on top; its ⌘K is a SearchSpace of it. record.py uses
a Folder: the catalogue and the glossary without Signal, for @ in a Record's diary and for what its questions say a
name is. Nothing here needs GTK: it reads files and runs git, kubectl, docker (and psql or arangosh in a container),
and gh, to read.
"""
import collections
import hashlib
import itertools
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse

from fixes import gh_path  # (the gh CLI, or None: BAUDE_GH, for tests)


REF_KINDS = {"file": ("File", "Files"), "commit": ("Commit", "Commits"), "branch": ("Branch", "Branches"),
             "pr": ("Pull request", "Pull requests"), "env": ("Env var", "Env vars"),
             "constant": ("Constant", "Constants"), "symbol": ("Code symbol", "Code symbols"),
             "config": ("Config key", "Config keys"), "service": ("Service", "Services"),
             "k8s": ("Kubernetes object", "Kubernetes objects"), "package": ("Package", "Packages"),
             "cli": ("Command", "Commands"), "external": ("External tool", "External tools"),
             "tool": ("Tool", "Tools"), "subagent": ("Subagent type", "Subagent types"), "skill": ("Skill", "Skills"),
             "route": ("HTTP route", "HTTP routes")}


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
    what, where = sources[0] if sources else ("Name", "")
    data, locs = {"catalogue": True}, []
    if entry.get("db"):
        live, n = entry["db"][0], len(entry["db"])
        table = live["table"]
        data["db"] = live
        data["note"] = (f"{'Collection' if live['engine'] == 'ArangoDB' else 'Table'} {table} in {live['database']}"
                        if table else f"Database {live['database']}") + f" ({live['engine']}, {live['container']})" + \
            (f", and {n - 1} more" if n > 1 else "")
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
        (f", and {len(sources) - 1} more" if len(sources) > 1 and not locs else "")
    return Ref(surface, kind, "ambiguous" if len(locs) > 1 else "resolved", data=data, locations=locs,
               reason=f"{len(locs)} places the catalogue has" if len(locs) > 1 else "")


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


def ref_yaml_path(lines, i):
    """The dotted key path of the YAML key on line i (0-based), from the keys above it by indentation."""
    m = re.match(r"(\s*)-?\s*([\w.-]+)\s*:", lines[i])
    if not m:
        return []
    path, indent = [m.group(2)], len(m.group(1))
    for j in range(i - 1, -1, -1):
        s = lines[j]
        ind = len(s) - len(s.lstrip())
        if s.strip() and not s.lstrip().startswith(("#", "{{")) and ind < indent:  # (a Helm template's {{- if }})
            if k := re.match(r"\s*-?\s*([\w.-]+)\s*:", s):
                path.insert(0, k.group(1))
            indent = ind
            if ind == 0:
                break
    return path


def git_exe():
    """The git to run. On macOS, /usr/bin/git is a stand-in that asks xcrun where the developer tools' git is on every
    run, which doubles what a git command costs (15 ms rather than 7): that git is asked for once and run directly."""
    if not hasattr(git_exe, "path"):
        git_exe.path = shutil.which("git") or "git"
        if sys.platform == "darwin" and git_exe.path == "/usr/bin/git":
            try:
                found = subprocess.run(["xcrun", "--find", "git"], capture_output=True, text=True, timeout=10,
                                       stdin=subprocess.DEVNULL).stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                found = ""
            if found and os.access(found, os.X_OK):
                git_exe.path = found
    return git_exe.path


_GIT_WAITS = threading.local()  # (what a thread waits for before each git it runs: git_wait)


def git_wait(wait):
    """Before each git this thread runs, wait() (Signal's worker: till the chats are still)."""
    _GIT_WAITS.wait = wait


def _git(cwd, *args, ok=(0,), raw=False, timeout=20):
    """git's output (text, or bytes if raw), or None if it failed or git isn't there. It's started by posix_spawn, not
    forked: in cwd by -C, and no fds closed (Python's own are close-on-exec), as Python needs for that. A fork holds
    Python's lock 8 ms or more in a process Baude's size, and the window waited for each git a thread started."""
    if cwd is not None and not os.path.isdir(cwd):
        return None  # (as cwd= had it: no such folder)
    if (wait := getattr(_GIT_WAITS, "wait", None)) is not None:
        wait()
    try:
        p = subprocess.run([git_exe(), *(("-C", cwd) if cwd is not None else ()), *args], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=timeout, close_fds=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode not in ok:
        return None
    return p.stdout if raw else p.stdout.decode("utf-8", "replace")


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
            return "Looking it up…"
        if self.status == "not_found":
            return f"Not found: {self.reason}"
        if self.status == "stale":
            return "Stale: the code changed and this couldn't be found again"
        if d.get("plain"):
            return "Not a name here: plain English (Haiku), or none of the matches"
        if self.status == "ambiguous":
            pick = f", probably {d['pick']}" if d.get("pick") else ""
            return f"{len(self.locations)} matches{pick}: {self.reason}"
        if self.kind == "commit":
            return f"{d.get('subject', '')} ({ref_date(d.get('date'))}, +{d.get('added', 0)} −{d.get('removed', 0)})"
        if self.kind == "branch":
            return f"{d.get('subject', '')} ({ref_date(d.get('date'))})"
        if self.kind == "pr":
            return f"#{d.get('number')} {d.get('title') or ''} ({d.get('state') or 'on GitHub'})".replace("  ", " ")
        if self.kind == "env":
            c = self.counts()
            parts = [f"{c[r]} {r}" for r in ("secret source", "set", "read") if c.get(r)]
            return "Env var: " + (", ".join(parts) or "mentioned only")
        if self.kind == "tool":
            n = len(d.get("input") or [])
            return f"Tool {d.get('tool') or self.canonical}" + (f": {n} input{'s' * (n != 1)}" if d.get("input") else "") + \
                (f" · {d['description'][:90]}" if d.get("description") else "")
        if self.kind in ("subagent", "skill"):
            return f"{REF_KINDS[self.kind][0]} {self.canonical}" + (f" · {d['description'][:90]}" if d.get("description") else "")
        if self.kind in ("service", "k8s", "package", "cli", "external") or (self.data.get("note") and not self.locations):
            loc = self.locations[0] if self.locations else None
            path = loc and (os.path.basename(loc["path"]) if os.path.isabs(loc["path"]) else loc["path"])
            where = (f" ({path}" + (f":{loc['line']}" if loc.get("line") else "") + ")") if loc else ""
            return (self.data.get("note") or REF_KINDS.get(self.kind, ("Name",))[0]) + where
        if self.kind in ("constant", "route", "symbol", "config") and self.locations:
            loc = self.locations[0]
            return f"{loc['role'].capitalize()} in {loc['path']}" + (f":{loc['line']}" if loc.get("line") else "") + \
                (f" ({loc['note']})" if loc.get("note") else "") + (f", on {loc['rev']}" if loc.get("rev") else "")
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


_KUBE_NAME = r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?"  # (DNS-1123: what Kubernetes allows a name to be)


_KUBE_ACTION = re.compile(r"\{\{-?\s*(.*?)\s*-?\}\}")  # (a Go template action, in a Helm template)


_KUBE_VALUE_FLAGS = {"-n", "--namespace", "--context", "--cluster", "--user", "-c", "--container", "-l", "--selector",
                     "-f", "--filename", "-k", "--kustomize", "-o", "--output", "--kubeconfig", "--kube-context",
                     "--type", "--since", "--tail", "--timeout", "--field-selector", "-p", "--patch", "--replicas",
                     "--image", "-L", "--values", "--set", "--set-string", "--set-file", "--set-json", "--set-literal",
                     "--version", "--repo", "--description", "--post-renderer", "--revision", "--port", "--address"}


def _kube_args(words):
    """A kubectl or helm command's flags and its other words, after the program and up to a "--":
    ({flag: its first value, or "" for a switch}, [word])."""
    value_flags = _KUBE_VALUE_FLAGS - ({"-f"} if "logs" in words else set())  # (kubectl logs -f: follow)
    opts, pos, i = {}, [], 1
    while i < len(words) and words[i] != "--":
        w = words[i]
        if w.startswith("-") and len(w) > 1:
            flag, eq, val = w.partition("=")
            if not eq and flag in value_flags and i + 1 < len(words):
                val, i = words[i + 1], i + 1
            opts.setdefault(flag, val)
        else:
            pos.append(w)
        i += 1
    return opts, pos


def _kube_scalar(value):
    """A YAML value as written, without a trailing comment or the quotes around it."""
    value = re.sub(r"\s+#.*$", "", value.strip())
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value


def kube_manifest_objects(text):
    """The objects a YAML file (a manifest, or a Helm template) declares: [{kind, name, namespace, labels, line,
    ns_line}], the lines being metadata.name's and metadata.namespace's; labels are every labels: and matchLabels: key in the object. Read line by line, not
    parsed: a template isn't YAML until it's rendered."""
    out, doc, block = [], None, None  # (block: "metadata" or (indent, "labels") while in one)
    for n, line in enumerate(text.splitlines() + ["---"], 1):
        if re.match(r"---(\s|$)", line):
            if doc and doc.get("kind") and doc.get("name"):
                out.append({k: v for k, v in doc.items() if not k.startswith("_")})
            doc, block = {"labels": {}, "namespace": None, "_mind": None}, None
            continue
        if doc is None:
            doc = {"labels": {}, "namespace": None, "_mind": None}
        s = line.rstrip()
        if not s.strip() or s.lstrip().startswith(("#", "{{")):
            continue
        ind = len(s) - len(s.lstrip())
        if ind == 0:
            m = re.match(r"(kind|metadata)\s*:\s*(.*)", s)
            block = "metadata" if m and m.group(1) == "metadata" else None
            if m and m.group(1) == "kind" and not doc.get("kind"):
                doc["kind"] = _kube_scalar(m.group(2))
            continue
        if isinstance(block, tuple) and ind <= block[0]:
            block = "metadata" if block[2] else None
        m = re.match(r"(\s*)-?\s*([\w./-]+)\s*:\s*(.*)", s)
        if not m:
            continue
        key, value = m.group(2), m.group(3)
        if isinstance(block, tuple):
            if value and not value.startswith(("{{", "|", ">")):
                doc["labels"].setdefault(key, _kube_scalar(value))
            continue
        if key in ("labels", "matchLabels") and not value:
            block = (ind, "labels", block == "metadata")
            continue
        if block == "metadata":
            if doc["_mind"] is None:
                doc["_mind"] = ind
            if ind == doc["_mind"] and key == "name" and "name" not in doc and value:
                doc["name"], doc["line"] = _kube_scalar(value), n
            elif ind == doc["_mind"] and key == "namespace" and value:
                doc["namespace"], doc["ns_line"] = _kube_scalar(value), n
    return out


def kube_kustomization(text):
    """What a kustomization.yaml does to names: {"prefix", "suffix", "namespace", "resources" (bases and components
    too), "generated": [(kind, name, line)] (its ConfigMap and Secret generators'), "hash": whether generated names get
    kustomize's hash suffix}."""
    out = {"prefix": "", "suffix": "", "namespace": None, "resources": [], "generated": [], "hash": True}
    section, item = None, None
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace() and not line.startswith("-"):
            key, _, value = line.partition(":")
            section, item = key.strip(), None
            value = _kube_scalar(value)
            if section in ("namePrefix", "nameSuffix", "namespace") and value:
                out[{"namePrefix": "prefix", "nameSuffix": "suffix", "namespace": "namespace"}[section]] = value
                if section == "namespace":
                    out["ns_line"] = n
            continue
        s = line.strip()
        if section in ("resources", "bases", "components") and s.startswith("- "):
            out["resources"].append(_kube_scalar(s[2:]))
        elif section in ("configMapGenerator", "secretGenerator"):
            if s.startswith("-"):
                item = {}
                s = s[1:].strip()
            if item is not None and (m := re.match(r"name\s*:\s*(\S+)", s)) and "name" not in item:
                item["name"] = _kube_scalar(m.group(1))
                out["generated"].append(("ConfigMap" if section == "configMapGenerator" else "Secret", item["name"], n))
        elif section == "generatorOptions" and re.match(r"disableNameSuffixHash\s*:\s*true\b", s):
            out["hash"] = False
    return out


def kube_yaml_scalars(text):
    """A values file's scalars by their dotted key path: {"image.tag": "1.2", "nameOverride": ""}."""
    lines, out = text.splitlines(), {}
    for i, s in enumerate(lines):
        if (m := re.match(r"\s*([\w.-]+)\s*:\s*(\S.*)$", s)) and not s.lstrip().startswith(("#", "-")):
            value = _kube_scalar(m.group(2))
            if not value.startswith(("|", ">", "{", "[", "&", "*")):
                out[".".join(ref_yaml_path(lines, i))] = value
    return out


def kube_fullname(chart, release):
    """The name Helm's standard fullname helper gives a release of a chart: fullnameOverride, else the release name if
    it already has the chart's name (nameOverride, else the chart's) in it, else release-chart; at most 63 characters."""
    values = chart.get("values") or {}
    if values.get("fullnameOverride"):
        return values["fullnameOverride"][:63].rstrip("-")
    name = values.get("nameOverride") or chart["name"]
    return (release if name in release else f"{release}-{name}")[:63].rstrip("-")


def kube_render(raw, chart, release):
    """A Helm template's metadata.name as one release renders it, or None if it uses something Signal can't work out.
    Understood: include / template of a fullname or name helper (Helm's standard ones), .Release.Name, .Chart.Name,
    .Values.<path> (from the chart's values.yaml), printf "%s-x" with those, a quoted string, and the filters trunc,
    trimSuffix, lower, quote and default."""
    values = chart.get("values") or {}

    def term(t):
        t = t.strip()
        if t.startswith("(") and t.endswith(")"):
            return pipeline(t[1:-1])
        if m := re.fullmatch(r'"((?:[^"\\]|\\.)*)"', t):
            return m.group(1)
        if m := re.fullmatch(r'(?:include|template)\s+"([\w.-]+)"\s+[.$]', t):
            helper = m.group(1)
            return kube_fullname(chart, release) if helper.endswith(".fullname") else \
                (values.get("nameOverride") or chart["name"])[:63].rstrip("-") if helper.endswith(".name") else None
        if t in (".Release.Name", "$.Release.Name"):
            return release
        if t in (".Chart.Name", "$.Chart.Name"):
            return chart["name"]
        if m := re.fullmatch(r"\$?\.Values\.([\w.-]+)", t):
            return values.get(m.group(1)) or None
        if m := re.fullmatch(r'printf\s+"((?:[^"\\]|\\.)*)"\s+(.*)', t):
            args = [a for a in re.findall(r'\([^()]*\)|"(?:[^"\\]|\\.)*"|\S+', m.group(2))]
            got = [term(a) for a in args]
            if None in got or m.group(1).count("%s") != len(got) or re.search(r"%[^s]", m.group(1)):
                return None
            return m.group(1) % tuple(got)
        return None

    def pipeline(expr):
        stages = [s.strip() for s in expr.split("|")]
        v = term(stages[0])
        for s in stages[1:]:
            if v is None:
                return None
            if m := re.fullmatch(r"trunc\s+(\d+)", s):
                v = v[:int(m.group(1))]
            elif m := re.fullmatch(r'trimSuffix\s+"([^"]*)"', s):
                v = v[:-len(m.group(1))] if m.group(1) and v.endswith(m.group(1)) else v
            elif s == "lower":
                v = v.lower()
            elif m := re.fullmatch(r'default\s+"([^"]*)"', s):
                v = v or m.group(1)
            elif s != "quote":
                return None
        return v
    out, at = [], 0
    for m in _KUBE_ACTION.finditer(raw):
        out.append(raw[at:m.start()])
        if (v := pipeline(m.group(1))) is None:
            return None
        out.append(v)
        at = m.end()
    out.append(raw[at:])
    name = "".join(out).strip().strip("\"'")
    return name if re.fullmatch(_KUBE_NAME, name) else None


def kube_name_pattern(raw):
    """A templated name as a pattern (each template action stands for some name characters) and how many characters
    of it are literal: ({{ include "app.fullname" . }}-runner-ingress) -> (regex, 15)."""
    parts = _KUBE_ACTION.split(raw.strip().strip("\"'"))
    literal = "".join(parts[0::2])
    regex = "".join(re.escape(p) if i % 2 == 0 else r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?" for i, p in enumerate(parts))
    return regex, len(literal)


def kubeconfig_paths():
    """The kubeconfig files kubectl would read: $KUBECONFIG's, else ~/.kube/config (the ones that exist)."""
    env = os.environ.get("KUBECONFIG")
    paths = [p for p in env.split(os.pathsep) if p] if env else ["~/.kube/config"]
    return [os.path.realpath(os.path.expanduser(p)) for p in paths if os.path.isfile(os.path.expanduser(p))]


def kubeconfig_entries(path):
    """A kubeconfig's contexts, clusters and users by name, and its current context: {"contexts": [{name, cluster,
    namespace, user, line}], "clusters": [{name, server, line}], "users": [{name, line}], "current"}. Only those keys are
    read: certificates, keys, tokens and exec plugins never are (a server's user:password@ is dropped)."""
    out = {"contexts": [], "clusters": [], "users": [], "current": None}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(2 * 1024 * 1024)
    except OSError:
        return out
    keep = {"cluster", "namespace", "user", "server"}
    if text.lstrip().startswith("{"):  # (JSON is YAML too)
        try:
            doc = json.loads(text)
        except ValueError:
            return out
        for section, inner in (("contexts", "context"), ("clusters", "cluster"), ("users", None)):
            for e in doc.get(section) or []:
                if isinstance(e, dict) and isinstance(e.get("name"), str):
                    body = e.get(inner) if inner and isinstance(e.get(inner), dict) else {}
                    out[section].append({"name": e["name"], "line": None,
                                         **{k: v for k, v in body.items() if k in keep and isinstance(v, str)}})
        out["current"] = doc.get("current-context") if isinstance(doc.get("current-context"), str) else None
    else:
        section, item, item_indent = None, None, None
        lines, block = text.splitlines(), None
        for n, line in enumerate(lines, 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            ind = len(line) - len(line.lstrip())
            if block is not None and ind > block:
                continue  # (a block value's text: read with its key, if its key is one that's read)
            block = None
            if ind == 0 and not line.startswith("-"):
                key, _, value = line.partition(":")
                section = key.strip() if key.strip() in ("contexts", "clusters", "users") else None
                item = item_indent = None
                if key.strip() == "current-context":
                    out["current"] = _kube_scalar(value) or None
                continue
            if section is None:
                continue
            s, col = line.strip(), ind
            if s.startswith("-") and (item_indent is None or ind <= item_indent):  # (an entry of the section's list)
                item, item_indent = {"line": n}, ind
                out[section].append(item)
                s, col = s[1:].strip(), ind + 2
            m = re.match(r"([\w-]+)\s*:\s*(.*)$", s)
            if item is None or not m or not m.group(2):
                continue
            value = _kube_scalar(m.group(2))
            if m.group(2).startswith(("|", ">")):  # (name: >- and the name on the lines below, as some tools write it)
                block = col
                if not (m.group(1) == "name" or m.group(1) in keep and section != "users"):
                    continue  # (a certificate, a key: never read)
                value = " ".join(x.strip() for x in itertools.takewhile(
                    lambda x: not x.strip() or len(x) - len(x.lstrip()) > col, lines[n:]) if x.strip())
            if m.group(1) == "name" and col <= item_indent + 2 and "name" not in item:
                item["name"], item["line"] = value, n
            elif m.group(1) in keep and section != "users":
                item.setdefault(m.group(1), value)
    for c in out["clusters"]:
        if c.get("server"):
            c["server"] = re.sub(r"//[^/@\s]*@", "//", c["server"])
    for section in ("contexts", "clusters", "users"):
        out[section] = [e for e in out[section] if e.get("name")]
        if section == "users":
            out[section] = [{"name": e["name"], "line": e.get("line")} for e in out[section]]
    return out


KUBE_MADE_BY = (("k3d", "k3d.cluster", "6443/tcp"), ("kind", "io.x-k8s.kind.cluster", "6443/tcp"),
                ("minikube", "name.minikube.sigs.k8s.io", "8443/tcp"))


_KUBE_MADE = {}


def kube_made_clusters(max_age=30):
    """The clusters k3d, kind and minikube made on this machine, as the tools themselves record them: each labels a
    cluster's containers with its name (the labels `k3d cluster list` and `kind get clusters` read) and publishes its
    API server on a host port, the port a kubeconfig's server for it names. {port: (tool, name)}, stopped clusters
    too. Only those labels and the port bindings are read (k3d's other labels hold the cluster's join token)."""
    if time.monotonic() - _KUBE_MADE.get("at", -max_age) < max_age:
        return _KUBE_MADE["ports"]
    ports = {}
    fmt = "\t".join(f'{{{{index .Config.Labels "{label}"}}}}' for _t, label, _p in KUBE_MADE_BY) + \
        "\t{{json .HostConfig.PortBindings}}"
    try:
        ids = _docker("ps", "-aq", timeout=10).split()
        for line in (_docker("inspect", "--format", fmt, *ids, timeout=10) if ids else "").splitlines():
            *names, bindings = line.split("\t")
            for (tool, _label, port), name in zip(KUBE_MADE_BY, names):
                if name:
                    for b in (json.loads(bindings) or {}).get(port) or []:
                        if str(b.get("HostPort", "")).isdigit():
                            ports[int(b["HostPort"])] = (tool, name)
    except (RuntimeError, ValueError):
        pass  # (no Docker)
    _KUBE_MADE.update(at=time.monotonic(), ports=ports)
    return ports


def kube_made_as(c):
    """The name a kubeconfig context's cluster was made with, as the tool that made it records it: (tool, name), or
    None. A local cluster's is its containers' label (kube_made_clusters), found by its server's port; an EKS
    cluster's is in its ARN (arn:aws:eks:region:account:cluster/name, AWS's own id for it), when its server is that
    region's EKS endpoint. Never from how a context's name looks (k3d-dev): a context can be called anything."""
    server = c.get("server") or ""
    parts = urllib.parse.urlsplit(server)
    if kube_local(server):
        try:
            return kube_made_clusters().get(parts.port or 443)
        except ValueError:  # (a port that isn't one)
            return None
    for n in (c.get("cluster"), c.get("name")):
        m = re.fullmatch(r"arn:aws[\w-]*:eks:([\w-]+):\d{12}:cluster/([\w.-]+)", n or "")
        if m and (parts.hostname or "").endswith(f".{m.group(1)}.eks.amazonaws.com"):
            return "EKS", m.group(2)
    return None


_KUBE_CONTEXTS = {}


def kube_contexts():
    """Every kubeconfig context kubectl would see, with its cluster's server: [{name, cluster, server, namespace, user,
    kubeconfig, line, current}]; the clusters no context names are listed as contexts of their own name too. Read again
    when a file changes."""
    paths = kubeconfig_paths()
    key = tuple((p, os.stat(p).st_mtime_ns) for p in paths if os.path.exists(p))
    if key not in _KUBE_CONTEXTS:
        out, current = [], None
        for p in paths:
            e = kubeconfig_entries(p)
            current = current or e["current"]  # (the first file that sets it wins, as in kubectl)
            servers = {c["name"]: c.get("server") for c in e["clusters"]}
            for c in e["contexts"]:
                out.append({**c, "server": servers.get(c.get("cluster")), "kubeconfig": p})
            named = {c.get("cluster") for c in e["contexts"]}
            out += [{"name": c["name"], "cluster": c["name"], "server": c.get("server"), "kubeconfig": p,
                     "line": c.get("line"), "cluster_only": True} for c in e["clusters"] if c["name"] not in named]
        for c in out:
            c["current"] = c["name"] == current
        _KUBE_CONTEXTS.clear()
        _KUBE_CONTEXTS[key] = out
    return _KUBE_CONTEXTS[key]


REF_ICON_KINDS = {"commit": "git", "branch": "git", "pr": "git", "k8s": "k8s", "symbol": "code", "constant": "code",
                  "env": "env", "route": "route", "config": "config", "cli": "cli", "package": "package",
                  "service": "service", "tool": "claude", "subagent": "claude", "skill": "claude",
                  "external": "external"}


def github_icon(issue, state):
    """A pull request's logo (the branch's, green while it's open, purple once merged, grey if it was closed without
    being merged) or an issue's (a #, grey once closed): REF_ICON_SVG."""
    if issue:
        return "issue_closed" if state == "closed" else "issue"
    return {"merged": "pr_merged", "closed": "pr_closed"}.get(state, "pr_open")


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


def at_once(fn, items, workers=8):
    """[fn(item) for item in items], run side by side on threads, at most workers at a time: for calls that mostly
    wait on another program (a docker or kubectl exec, a quarter to a whole second each). An item whose call raised
    gives None."""
    items, out = list(items), []
    if len(items) <= 1:
        for item in items:
            try:
                out.append(fn(item))
            except Exception:  # noqa: BLE001  (as on a thread: that item's None)
                out.append(None)
        return out
    out, todo = [None] * len(items), queue.SimpleQueue()
    for i in range(len(items)):
        todo.put(i)

    def run():
        while True:
            try:
                i = todo.get_nowait()
            except queue.Empty:
                return
            try:
                out[i] = fn(items[i])
            except Exception:  # noqa: BLE001
                out[i] = None
    threads = [threading.Thread(target=run, daemon=True) for _ in range(min(workers, len(items)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


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


def ref_palette_rank(item, q):
    """How well a name matches what's typed (lower is better), or None. Each word typed must match (in any order:
    "relay job" finds relay_gen_job), the worst-matching word decides, then all of them together, then the typed words
    being in the name's order, then the shorter name. (A pull request or an issue goes by its number, its label: its
    repository's name is in every one of them, and any letters typed would find them all.)"""
    name = (item.get("label") or item["surface"]).lower()
    words = q.split()
    if not words:
        return (0, 0, 0, len(name))
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
    return (max(ranks), sum(ranks), 0 if in_order else 1, len(name))


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


def _docker(*args, stdin=None, timeout=30):
    """docker's output, or raise RuntimeError with what it said."""
    try:
        p = subprocess.run(["docker", *args], input=stdin, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"docker: {e}") from e
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1] if (p.stderr or p.stdout).strip()
                           else f"docker exited with {p.returncode}")
    return p.stdout


def pg_servers():
    """Postgres servers Baude can read: in running Docker containers (pg_docker_servers), and in pods of local
    Kubernetes clusters (pg_k8s_servers), both asked at once. An error only if there are none and Docker couldn't be
    asked."""
    errors = []

    def docker():
        try:
            return pg_docker_servers()
        except RuntimeError as e:
            errors.append(str(e))
            return []
    servers = [s for found in at_once(lambda ask: ask(), [docker, pg_k8s_servers]) for s in found or []]
    if not servers and errors:
        raise RuntimeError(errors[0])
    return servers


KUBE_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal", "host.k3d.internal",
                    "kubernetes.docker.internal", "host.minikube.internal"}


def kube_local(server):
    """Whether a cluster's API server is on this machine (k3d, kind, minikube, Docker Desktop…): a remote one is
    never searched for databases."""
    host = urllib.parse.urlsplit(server or "").hostname or ""
    return host in KUBE_LOCAL_HOSTS or host.endswith(".localhost") or host.startswith("127.")


KUBE_RUNS = {"deployments": ("Deployment", "/apis/apps/v1/deployments"),
             "statefulsets": ("StatefulSet", "/apis/apps/v1/statefulsets"),
             "daemonsets": ("DaemonSet", "/apis/apps/v1/daemonsets"),
             "cronjobs": ("CronJob", "/apis/batch/v1/cronjobs")}  # (what a name can be running as, and its list)


_KUBE_RUNNING = {}


def kube_running(names, max_age=15):
    """Where workloads called one of names (None: every one) run in your local clusters (a context whose API server is
    on this machine, never a remote one; only reads): [{"context", "namespace", "kind", "resource", "name", "state",
    "ok"}].
    A cluster's workloads are listed once per max_age seconds, whichever name is asked about: every kind of every
    cluster at once, from the API's own lists (kubectl get --raw: no discovery, half a second)."""
    contexts = [c["name"] for c in kube_contexts() if not c.get("cluster_only") and kube_local(c.get("server"))]
    stale = [c for c in contexts if time.monotonic() - _KUBE_RUNNING.get(c, (-max_age, None))[0] >= max_age]
    fetched = {c: [] for c in stale}

    def fetch(context, resource):
        kind, path = KUBE_RUNS[resource]
        try:
            items = json.loads(kube_get(context, "--raw", path, "--request-timeout=5s", timeout=10) or "{}") \
                .get("items") or []
        except (RuntimeError, ValueError):
            items = []  # (a cluster that isn't running)
        fetched[context] += [{**o, "kind": kind} for o in items]
    threads = [threading.Thread(target=fetch, args=(c, r), daemon=True) for c in stale for r in KUBE_RUNS]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    _KUBE_RUNNING.update({c: (time.monotonic(), items) for c, items in fetched.items()})
    out, order = [], list(KUBE_RUNS)
    for c in contexts:
        mine = [{"context": c, "namespace": _km(o).get("namespace", ""), "kind": o["kind"],
                 "resource": next(r for r, (k, _p) in KUBE_RUNS.items() if k == o["kind"]),
                 "name": _km(o)["name"], **kube_run_state(o)}
                for o in _KUBE_RUNNING.get(c, (0, []))[1] if names is None or _km(o).get("name") in names]
        out += sorted(mine, key=lambda r: (order.index(r["resource"]), r["namespace"], r["name"]))
    return out


def kube_run_state(o):
    """A workload's state in a few words, and whether it's all up: 1/1 ready, 0/2 ready, scaled to 0; a CronJob's
    last run, or suspended."""
    if o.get("kind") == "CronJob":
        if _ks(o, "spec", "suspend"):
            return {"state": "suspended", "ok": False}
        now = time.time()
        last = kube_seconds(_ks(o, "status", "lastScheduleTime"), now)
        return {"state": f"last ran {kube_fmt_age(last)} ago", "ok": True, "at": now - last} if last is not None else \
            {"state": "not run yet", "ok": True}
    if o.get("kind") == "DaemonSet":
        want, ready = _ks(o, "status", "desiredNumberScheduled", default=0), _ks(o, "status", "numberReady", default=0)
    else:
        want, ready = _ks(o, "spec", "replicas", default=1), _ks(o, "status", "readyReplicas", default=0)
    if not want:
        return {"state": "scaled to 0", "ok": False}
    return {"state": f"{ready or 0}/{want} ready", "ok": (ready or 0) >= want}


def pg_k8s_servers():
    """Postgres in running pods of local Kubernetes clusters (a context whose API server is on this machine; never a
    remote one): a container whose image is postgres, postgresql or postgis (CloudNativePG's instances too, not its
    operator). Read with `kubectl exec … psql`, as Docker's are with `docker exec`, as the container's
    POSTGRES_USER (when the pod spells it out), else postgres. Every cluster is asked at once (one that isn't running
    takes its whole timeout to say so)."""
    out = []
    local = [c for c in kube_contexts() if not c.get("cluster_only") and kube_local(c.get("server"))]

    def pods_of(c):
        try:
            return json.loads(kube_get(c["name"], "pods", "--all-namespaces", "-o", "json", "--request-timeout=5s",
                                       timeout=10) or "{}").get("items") or []
        except (RuntimeError, ValueError):
            return []  # (a cluster that isn't running)
    for c, pods in zip(local, at_once(pods_of, local)):
        for p in pods or []:
            if _ks(p, "status", "phase") != "Running":
                continue
            ns, pod = _km(p).get("namespace", ""), _km(p).get("name", "")
            for ctr in _ks(p, "spec", "containers", default=[]):
                repo, _, tag = ctr.get("image", "").rsplit("/", 1)[-1].split("@")[0].partition(":")
                prefix = [kube_tool("kubectl"), "--context", c["name"], "exec", "-i", "-n", ns, pod,
                          "-c", ctr.get("name", ""), "--"]
                if repo == "arangodb":
                    out.append({"container": f"{c['name']}/{ns}/{pod}", "engine": "arango", "version": tag,
                                "exec": prefix})
                    continue
                if repo not in ("postgres", "postgresql", "postgis"):
                    continue
                env = {e.get("name"): e.get("value") for e in ctr.get("env") or [] if isinstance(e.get("value"), str)}
                out.append({"container": f"{c['name']}/{ns}/{pod}", "user": env.get("POSTGRES_USER") or "postgres",
                            "version": (re.match(r"\d+", tag) or [""])[0],
                            "exec": [kube_tool("kubectl"), "--context", c["name"], "exec", "-i", "-n", ns, pod,
                                     "-c", ctr.get("name", ""), "--"]})
    return sorted(out, key=lambda s: s["container"])


def pg_docker_servers():
    """Database servers in running containers: Postgres ({"container", "user", "version"}: a container with
    PG_MAJOR or PGDATA set; its POSTGRES_USER, else postgres) and ArangoDB ({"container", "engine": "arango",
    "version"}: an arangodb image). Nothing else from their environments is kept."""
    ids = _docker("ps", "-q").split()
    if not ids:
        return []
    servers = []
    for line in _docker("inspect", "--format", "{{json .Name}} {{json .Config.Image}} {{json .Config.Env}}",
                        *ids).splitlines():
        try:
            name, image, env = line.split(" ", 2)
            name, image = json.loads(name), json.loads(image)
            env = dict(e.split("=", 1) for e in json.loads(env) or [] if "=" in e)
        except ValueError:
            continue
        repo, _, tag = image.rsplit("/", 1)[-1].split("@")[0].partition(":")
        if repo == "arangodb":
            servers.append({"container": name.lstrip("/"), "engine": "arango", "version": tag})
        elif "PG_MAJOR" in env or "PGDATA" in env:
            servers.append({"container": name.lstrip("/"), "user": env.get("POSTGRES_USER") or "postgres",
                            "version": env.get("PG_MAJOR", "")})
    return sorted(servers, key=lambda s: s["container"])


def pg_query(server, database, sql):
    """psql's answer (-At) to sql, run read-only in the server's container (docker exec, or kubectl exec for a pod's)
    as its own user."""
    psql = ["psql", "-X", "-q", "-U", server["user"], "-d", database, "-At", "-v", "ON_ERROR_STOP=1", "-f", "-"]
    if server.get("exec"):
        cmd = server["exec"] + ["env", "PGOPTIONS=-c default_transaction_read_only=on"] + psql
        try:
            p = subprocess.run(cmd, input=sql, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"kubectl: {e}") from e
        if p.returncode:
            lines = (p.stderr or p.stdout).strip().splitlines()
            raise RuntimeError(lines[-1] if lines else f"kubectl exited with {p.returncode}")
        return p.stdout
    return _docker("exec", "-i", "-e", "PGOPTIONS=-c default_transaction_read_only=on", server["container"],
                   *psql, stdin=sql)


def pg_databases(server):
    if server.get("engine") == "arango":
        return arango_databases(server)
    sql = "select datname from pg_database where not datistemplate and datallowconn order by 1"
    for db in ("postgres", "template1", server["user"]):
        try:
            return [d for d in pg_query(server, db, sql).split() if d]
        except RuntimeError:
            continue
    return []


PG_LISTS = {}  # ("servers",) or ("databases", container) -> (when, list): read again after half a minute


def pg_servers_cached(max_age=30):
    hit = PG_LISTS.get(("servers",))
    if hit is None or time.time() - hit[0] >= max_age:
        PG_LISTS[("servers",)] = hit = (time.time(), pg_servers())
    return hit[1]


def pg_databases_cached(server, max_age=30):
    key = ("databases", server["container"])
    hit = PG_LISTS.get(key)
    if hit is None or time.time() - hit[0] >= max_age:
        PG_LISTS[key] = hit = (time.time(), pg_databases(server))
    return hit[1]


def db_index(max_age=300):
    """Every table and collection (and database) in every database Baude can reach, by name: {name: [(container,
    database, table or None)]}, in the servers' and their databases' order. Names only: one light query per Postgres
    database and one call per ArangoDB server, all at once (one at a time, a click on a table's name waited seconds
    for a database it wasn't in). Kept for max_age seconds; one made at a time (whoever asks while it's being made
    gets that one). db_indexed: which databases it has (one that couldn't be read isn't)."""
    asked = time.time()
    hit = PG_LISTS.get(("index",))
    if hit is not None and asked - hit[0] < max_age:
        return hit[1]
    with _INDEXING:
        hit = PG_LISTS.get(("index",))
        if hit is not None and (hit[0] >= asked or time.time() - hit[0] < max_age):
            return hit[1]  # (made while this waited)
        return _db_index()


_INDEXING = threading.Lock()


def _db_index():
    try:
        servers = pg_servers_cached()
    except RuntimeError:
        servers = []
    sql = ("select table_name from information_schema.tables where table_schema not in "  # (and materialized views:
           "('pg_catalog', 'information_schema') union all select matviewname from pg_matviews "  # not in tables)
           "where schemaname not in ('pg_catalog', 'information_schema')")

    def tables(server):  # {database: [name]}
        if server.get("engine") == "arango":
            return arango_collection_names(server)
        dbs = pg_databases_cached(server)
        return {db: names.split() for db, names in zip(dbs, at_once(lambda db: pg_query(server, db, sql), dbs, 4))
                if names is not None}  # (four at a time per server: servers side by side too)
    out, indexed = {}, set()
    for s, found in zip(servers, at_once(tables, servers)):
        for db, names in (found or {}).items():
            indexed.add((s["container"], db))
            out.setdefault(db, []).append((s["container"], db, None))
            for n in names:
                out.setdefault(n, []).append((s["container"], db, n))
    PG_LISTS[("index",)] = (time.time(), out, indexed)
    return out


def db_names(max_age=300):
    """db_index without the postgres databases (a server's own, never an app's): Signal asks it about names the code
    doesn't define."""
    index = db_index(max_age)
    hit = PG_LISTS.get(("names",))
    if hit is not None and hit[1] is index:
        return hit[2]
    out = {}
    for name, places in index.items():
        if (kept := [p for p in places if p[1] != "postgres"]):
            out[name] = kept
    PG_LISTS[("names",)] = (time.time(), index, out)
    return out


ARANGO_SH = ('exec arangosh --quiet --console.history false --server.endpoint tcp://127.0.0.1:8529 '
             '--server.username "${ARANGO_USER:-root}" --server.password "$ARANGO_ROOT_PASSWORD" '
             '--javascript.execute-string "$1"')


def arango_eval(server, js, timeout=90):
    """What a read-only JavaScript program prints (JSON), run by arangosh in the server's container. Its @s are
    doubled: arangosh reads @NAME@ in an option's value as a variable, and @@ as an @."""
    prefix = server.get("exec") or ["docker", "exec", "-i", server["container"]]
    program = ("(function () {" + js + "\n})();").replace("@", "@@")
    try:
        p = subprocess.run(prefix + ["sh", "-c", ARANGO_SH, "sh", program], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"arangosh: {e}") from e
    text = p.stdout.strip()
    start = next((i for i, ch in enumerate(text) if ch in "[{"), None)
    if p.returncode or start is None:
        lines = [l for l in (p.stderr + "\n" + p.stdout).strip().splitlines() if l.strip()]
        raise RuntimeError(re.sub(r"^\S+ \[\d+\] \w+ \[\w+\] \{\w+\} ", "", lines[-1]) if lines else
                           f"arangosh exited with {p.returncode}")
    try:
        return json.loads(text[start:])
    except ValueError as e:
        raise RuntimeError(f"arangosh: {e}") from e


def arango_databases(server):
    return arango_eval(server, "print(JSON.stringify(db._databases()));")


def arango_collection_names(server):
    """Every database's collection names, in one call: {database: [name]} (no documents read)."""
    return arango_eval(server, "var out = {}; db._databases().forEach(n => { db._useDatabase(n); "
                               "out[n] = db._collections().map(c => c.name()).filter(c => !c.startsWith('_')); }); "
                               "print(JSON.stringify(out));")


def kube_tool(name):
    """kubectl or helm: $BAUDE_KUBECTL / $BAUDE_HELM, else the one on PATH."""
    return os.environ.get(f"BAUDE_{name.upper()}") or shutil.which(name) or name


class KubeError(RuntimeError):
    """kubectl or helm failed: str() is the last line it wrote, said all of it. The last line is often only that a
    credential plugin failed; why it did (aws, gcloud, kubelogin's own message) is in the lines before (kube_diagnose)."""

    def __init__(self, last, said=""):
        super().__init__(last)
        self.said = said or last


# Fault injection: BAUDE_KUBE_FAULT=<name> makes every kubectl call fail as it does then, with what kubectl (1.37)
# writes, the credential plugin's lines included: to see each failure in the Kubernetes view without breaking a
# real sign-in. kubectl retries discovery five times, and a plugin's stderr comes through each time.
_KUBE_AWS_FAIL = ('E1028 09:57:43.663194    9199 memcache.go:381] "Couldn\'t get current server API group list" '
                  'err="Get \\"https://ABCDEF12.gr7.eu-west-1.eks.amazonaws.com/api?timeout=32s\\": getting '
                  'credentials: exec: executable aws failed with exit code 255"')


_KUBE_AWS_END = "Unable to connect to the server: getting credentials: exec: executable aws failed with exit code 255"


KUBE_FAULTS = {
    "aws-sso-expired": ("aws: [ERROR]: Error when retrieving token from sso: Token has expired and refresh failed\n"
                        + _KUBE_AWS_FAIL + "\n") * 5 + _KUBE_AWS_END,
    "aws-sso-invalid": ("aws: [ERROR]: The SSO session associated with this profile has expired or is otherwise "
                        "invalid. To refresh this SSO session run aws sso login with the corresponding profile.\n"
                        + _KUBE_AWS_FAIL + "\n") * 5 + _KUBE_AWS_END,
    "aws-no-credentials": ('aws: [ERROR]: Unable to locate credentials. You can configure credentials by running '
                           '"aws configure".\n' + _KUBE_AWS_FAIL + "\n") * 5 + _KUBE_AWS_END,
    "aws-profile-missing": ("aws: [ERROR]: The config profile (AcmeCoPU) could not be found\n"
                            + _KUBE_AWS_FAIL + "\n") * 5 + _KUBE_AWS_END,
    "aws-not-installed": "Unable to connect to the server: getting credentials: exec: executable aws not found\n\n"
                         "It looks like you are trying to use a client-go credential plugin that is not installed.\n\n"
                         "To learn more about this feature, consult the documentation available at:\n"
                         "      https://kubernetes.io/docs/reference/access-authn-authz/authentication/"
                         "#client-go-credential-plugins",
    "gcloud-reauth": "F1028 09:57:43.663194    9199 cred.go:145] print credential failed with error: Failed to "
                     "retrieve access token:: failure while executing gcloud, with args [config config-helper "
                     "--format=json]: exit status 1 (err: ERROR: (gcloud.config.config-helper) There was a problem "
                     "refreshing your current auth tokens: Reauthentication failed. cannot prompt during "
                     "non-interactive execution.\nPlease run:\n\n  $ gcloud auth login\n\nto obtain new "
                     "credentials.\n)\nUnable to connect to the server: getting credentials: exec: executable "
                     "gke-gcloud-auth-plugin failed with exit code 1",
    "azure-expired": "Error: failed to get token: expected an empty error but received: AzureCLICredential: ERROR: "
                     "AADSTS700082: The refresh token has expired due to inactivity. Please run 'az login' to setup "
                     "account.\nUnable to connect to the server: getting credentials: exec: executable kubelogin "
                     "failed with exit code 1",
    "unauthorized": 'E1028 09:57:43.663194    9199 memcache.go:265] "Unhandled Error" err="couldn\'t get current '
                    'server API group list: the server has asked for the client to provide credentials"\n'
                    "error: You must be logged in to the server (the server has asked for the client to provide "
                    "credentials)",
    "forbidden": 'Error from server (Forbidden): pods is forbidden: User "arn:aws:sts::211326840684:assumed-role/'
                 'ReadOnly/naza" cannot list resource "pods" in API group "" at the cluster scope',
    "x509": "Unable to connect to the server: tls: failed to verify certificate: x509: certificate signed by unknown "
            "authority",
    "refused": "The connection to the server 127.0.0.1:6443 was refused - did you specify the right host or port?",
    "unreachable": "Unable to connect to the server: dial tcp: lookup ABCDEF12.gr7.eu-west-1.eks.amazonaws.com: "
                   "no such host",
    "timeout": "Unable to connect to the server: dial tcp 10.0.12.4:443: i/o timeout",
}


def kube_fault(tool):
    """The failure BAUDE_KUBE_FAULT injects into kubectl (what it writes), or None."""
    name = os.environ.get("BAUDE_KUBE_FAULT")
    return KUBE_FAULTS.get(name, f"error: unknown BAUDE_KUBE_FAULT {name!r}") if name and tool == "kubectl" else None


def kube_run(tool, args, timeout=30):
    """A kubectl or helm command's output; KubeError with what it wrote if it fails."""
    if (fault := kube_fault(tool)) is not None and "config" not in args[:1]:
        raise KubeError(fault.splitlines()[-1], fault)
    try:
        p = subprocess.run([kube_tool(tool), *args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise KubeError(f"{tool}: {e}") from e
    if p.returncode:
        said = (p.stderr or p.stdout).strip()
        raise KubeError(said.splitlines()[-1] if said else f"{tool} exited with {p.returncode}", said)
    return p.stdout


def kube_get(context, *args, timeout=30):
    """kubectl get …, in a context: the one kubectl verb the viewer uses for objects."""
    return kube_run("kubectl", ["--context", context, "get", *args], timeout)


def kube_seconds(ts, now):
    """Seconds since an RFC 3339 time (2026-10-22T09:00:00Z), or None."""
    if not ts:
        return None
    import calendar
    try:  # (Kubernetes writes UTC: 2026-10-22T09:00:00Z, an event's time with microseconds too)
        when = calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None
    return max(0.0, now - when)


def kube_fmt_age(s):
    """An age, closer the younger it is: 45s, 3m05s (under ten minutes), 42m, 5h07m (under a day), then 3d."""
    if s is None:
        return ""
    s = int(max(0, s))
    if s < 60:
        return f"{s}s"
    if s < 600:
        return f"{s // 60}m{s % 60:02d}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{s % 3600 // 60:02d}m"
    return f"{s // 86400}d"


def _km(o):
    return o.get("metadata") or {}


def _ks(o, *path, default=None):
    """o's value at path (keys), or default."""
    for k in path:
        o = o.get(k) if isinstance(o, dict) else None
        if o is None:
            return default
    return o


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

        def add(name, kind, what, where="", hint=None, db=None):
            if not name or len(name) < 2 or kind != "file" and (len(name) > 120 or "{" in name or name.startswith("_")):
                return  # (a file's name or path is one, whatever it's like)
            e = cat.setdefault(name, {"kind": kind, "hint": hint, "sources": []})
            if db is not None:  # (a running database's: where, as _live_db_name says it)
                e.setdefault("db", []).append(db)
            if CATALOGUE_KINDS.index(kind) < CATALOGUE_KINDS.index(e["kind"]):
                e["kind"], e["hint"] = kind, hint
            elif kind == e["kind"] and hint is None:
                e["hint"] = None  # (an object of that name comes before the context)
            if (what, where) not in e["sources"] and len(e["sources"]) < 8:
                e["sources"].append((what, where))
        _files, _dirs, by_name = self._files(st)
        for base, paths in by_name.items():
            named = "." in base.strip(".") and len(paths) <= 3  # (README.md, __init__.py: everyone's)
            if named:
                add(base, "file", "File", paths[0] if len(paths) == 1 else f"{len(paths)} files")
            if not named or len(paths) > 1:  # the rest by their paths: twelve compose.yaml, a Dockerfile
                for p in paths:
                    add(p, "file", "File", p)
        if self.top:
            for b in (_git(self.top, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes") or
                      "").split():
                if b != "HEAD" and not b.endswith("/HEAD"):
                    add(b, "branch", "Branch")
        for o in self._kube_index(st)["objects"]:
            for n, _how in o["names"]:
                if re.fullmatch(_KUBE_NAME, n):
                    add(n, "k8s", o["kind"], o["path"])
        for c in kube_contexts():
            add(c["name"], "k8s", "Kubeconfig cluster" if c.get("cluster_only") else "Kubeconfig context",
                hint="context")
            if made := kube_made_as(c):
                add(made[1], "k8s", f"{made[0]} cluster", c["name"], hint="context")
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
            self._tell(activity=f"Indexing {name.replace('_', ' ')}…")
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


# ---------------------------------------------------------------- GitHub's pull requests and issues
# With the gh CLI signed in, the pull requests and issues of the GitHub repositories in a folder are names too: its
# checkout's and those of the checkouts under it, the latest few and the open ones, in ⌘K and @, and # in Record's notes
# (items, SearchSpace.issues). GITHUB reads them on a thread of its own, keeps a copy of each on disk for the next start,
# and only ever asks with GET. Nothing at all happens without gh, signed out (gh api says so before it asks GitHub
# anything), or in a folder with no remote on GitHub.
#
# A look costs nothing while nothing's changed: asked again with the ETag of its last answer, GitHub answers 304, which
# doesn't count against the rate limit. So a repository in use is looked at about once a minute, while a window's shown
# (widgets.watch_github), with one request: the latest of everything, sorted by when it changed, which is what any
# change changes. Only when that's changed is the list of open ones asked for too: two requests a change. An answer with
# no ETag (no free lunch on offer) is asked again every 5 minutes.
#
# Only a folder attached to a notebook has the checkouts under it read too. A chat's folder, which no one attached, has
# the repository it's in: a vendored checkout under it isn't what the chat's about.

GITHUB_LOOK_S = 60  # a repository in use is looked at this often
GITHUB_PLAIN_S = 300  # (or, with no ETag to ask with, this often)
GITHUB_LOOKS_A_MINUTE = 6  # (each a gh run: many repositories in use are each looked at less often, this many in all)
GITHUB_REPOS = 24  # the most looked at, shared by the folders in use (GitHub._in_use)
GITHUB_RECENT, GITHUB_OPEN = 50, 100  # what's fetched of a repository: the latest of everything, the latest open
GITHUB_KEEP = 100  # (under this many requests left this hour, it waits for the next: they're for your other work too)
GITHUB_OUT_S, GITHUB_GONE_S = 600, 3600  # how long till it asks again: signed out; a repository GitHub won't show
GITHUB_TIMEOUT_S = 30
GITHUB_BACKOFF_S = 1800  # (offline, or GitHub in trouble: 2, 4, 8… minutes between tries, up to this)
GITHUB_WALK_DEPTH, GITHUB_WALK_DIRS = 3, 3000  # how far under a folder checkouts are looked for
GITHUB_SCAN_S = 300  # (and how often again)
GITHUB_SKIP = frozenset(("node_modules", "__pycache__", "venv", "vendor", "dist", "build", "target", "Library"))
_GITHUB_REMOTE = re.compile(r"(?:^|[/@])(?:www\.)?github\.com[:/]([\w-][\w.-]*/[\w-][\w.-]*?)(?:\.git)?/?$")
GITHUB_NAME = re.compile(r"(?<![\w./-])([\w.-]+/[\w.-]+)#(\d+)\b")  # (a pull request's or an issue's: owner/name#12)


def github_remotes(checkout):
    """The GitHub repositories (owner/name) a checkout came from, as configured (not as an insteadOf rewrites them):
    its origin's and its upstream's (a fork's issues are mostly its parent's), else the first it has."""
    named = {}
    for line in (_git(checkout, "config", "--get-regexp", r"^remote\..*\.url$", timeout=5) or "").splitlines():
        key, _, url = line.partition(" ")
        if (m := _GITHUB_REMOTE.search(url.strip())) and ".." not in m[1]:
            named.setdefault(key[len("remote."):-len(".url")], m[1])
    wanted = [named[n] for n in ("origin", "upstream") if n in named] or list(named.values())[:1]
    return list(dict.fromkeys(wanted))


def github_checkouts(folder, under=True):
    """A folder's git checkouts: the one it's in, then (under) those under it, the nearest first, not far
    (GITHUB_WALK_*). (top, [checkout])."""
    top = (_git(folder, "rev-parse", "--show-toplevel", timeout=5) or "").strip()
    start = os.path.realpath(top or folder)
    found, todo, seen = [start] * bool(top), collections.deque([(start, 0)] * under), 0
    while todo and seen < GITHUB_WALK_DIRS and len(found) < GITHUB_REPOS:
        at, depth = todo.popleft()
        seen += 1
        try:
            with os.scandir(at) as it:
                entries = list(it)
            if at != start and any(e.name == ".git" for e in entries):
                found.append(at)
            if depth < GITHUB_WALK_DEPTH:
                todo.extend((e.path, depth + 1) for e in entries if not e.name.startswith(".") and
                            e.name not in GITHUB_SKIP and e.is_dir(follow_symlinks=False))
        except OSError:
            continue
    return top or None, found


def _gh_get(path, etag=None):
    """GitHub's answer to a GET of path: (its status, or None if none came; {its headers, lower case}, its body, gh's
    exit code, or None if it didn't finish). gh api -i prints what GitHub answered whatever it was, but exits 1 on all
    but a 2xx, a 304 too; and 4, having asked no one, if it's signed out."""
    exe = gh_path()
    if not exe:
        return None, {}, b"", None
    args = [exe, "api", "--hostname", "github.com", "--method", "GET", "-i", "-H", "Accept: application/vnd.github+json"]
    args += ["-H", f"If-None-Match: {etag}"] * bool(etag)
    try:
        p = subprocess.run([*args, path], stdin=subprocess.DEVNULL, capture_output=True, timeout=GITHUB_TIMEOUT_S,
                           env={**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"})
    except (OSError, subprocess.TimeoutExpired):
        return None, {}, b"", None
    m = re.match(rb"HTTP/[\d.]+ (\d+)", p.stdout)
    if not m:
        return None, {}, b"", p.returncode
    head, *body = re.split(rb"\r?\n\r?\n", p.stdout, maxsplit=1)
    headers = {}
    for line in head.decode("utf-8", "replace").splitlines()[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return int(m[1]), headers, body[0] if body else b"", p.returncode


def github_kept(p):
    """What's kept of an issue or a pull request as GitHub's REST API says it: /issues lists both, a pull request saying
    so, and whether it's been merged, under pull_request; /pulls says that of it directly."""
    pr = "pull_request" in p or "merged_at" in p
    return {"number": p["number"], "title": re.sub(r"\s+", " ", p.get("title") or "").strip()[:300], "pr": pr,
            "state": "merged" if (p.get("pull_request") or p).get("merged_at") else
            "closed" if p.get("state") == "closed" else "open",
            "draft": bool(p.get("draft")), "updated": p.get("updated_at") or ""}


def github_data(listed):
    """What a link (a Ref's data) says of a pull request or an issue GITHUB lists."""
    return {"number": listed["number"], "repo": listed["repo"], "url": listed["url"], "title": listed["title"],
            "state": listed["state"], "issue": not listed["pr"]}


def github_url(surface):
    """The page of owner/name#12, a pull request or an issue (GitHub takes either to the right one), if owner/name is a
    repository that's been read: anything else might be spelt so (a branch called feature/login#12); else None."""
    m = GITHUB_NAME.fullmatch(surface)
    return f"https://github.com/{m[1]}/issues/{m[2]}" if m and GITHUB.has_read(m[1]) else None


def github_listing(repo, kept):
    """An item of lookup's (items) for a pull request or an issue kept (github_kept). Its name is owner/name#12: the
    same number in two repositories is two names."""
    number, pr, state = kept["number"], kept["pr"], kept["state"]
    what = {"merged": "Merged pull request", "closed": "Closed pull request"}.get(state) or \
        ("Draft pull request" if kept["draft"] else "Pull request") if pr else \
        "Closed issue" if state == "closed" else "Issue"
    url = f"https://github.com/{repo}/{'pull' if pr else 'issues'}/{number}"
    surface = f"{repo}#{number}"
    entry = {"kind": "pr" if pr else "issue", "hint": None, "sources": [(what, repo)],
             "github": {**kept, "repo": repo, "url": url}}
    return {"surface": surface, "canonical": surface, "faint": False, "icon": github_icon(not pr, state), "what": what,
            "summary": f"{kept['title']} · {repo}", "kind": entry["kind"], "path": None, "line": None, "entry": entry,
            "label": str(number)}


class GitHub:
    """The pull requests and issues of the GitHub repositories in the folders in use, read on a thread of its own (one
    for the app: GITHUB). look(folders), from the main loop, asks; what's read is swapped in whole, listed(folder) the
    same list till it changes (and generation goes up)."""

    NOTHING = ()

    def __init__(self):
        self.generation = 0
        self._todo, self._thread, self._one = queue.SimpleQueue(), None, threading.Lock()
        self.forget()

    def forget(self):
        """Nothing known, nothing in the way of the next look (in memory: the copies on disk stay)."""
        with self._one:
            self._lists, self._repos, self._folders = {}, {}, {}
            self._paused, self._fails, self._out = 0.0, 0, False
            self.generation += 1

    # ---- main loop (it never waits for any of this)
    def listed(self, folder):
        """The items (github_listing) of the repositories in a folder, as they were last read."""
        return self._lists.get(folder, self.NOTHING)

    def known(self, repo, number):
        """What's been read of repo's number (a listing's entry's "github"), or None."""
        for it in (self._repos.get(repo) or {}).get("listing", ()):
            if it["entry"]["github"]["number"] == number:
                return it["entry"]["github"]
        return None

    def own_repository(self, folder):
        """The repository a folder's checkout came from, as far as it's been looked at, or None: what a bare #12 in a
        chat in that folder can mean."""
        return next((k["own"] for k in list(self._folders.values()) if folder in k["keys"] and k["own"]), None)

    def has_read(self, repo):
        """Whether a repository's been read: here, or on another day (what's kept of it)."""
        return bool((self._repos.get(repo) or {}).get("etags")) or os.path.exists(self._path(repo))

    def look(self, attached=(), tops=()):
        """Look at the repositories in use, if it's time to: at once, not waiting for it. Nothing without gh. attached:
        the folders a notebook has (the repository each is in, and those of the checkouts under it). tops: a chat's
        folder, which no one attached (the repository it's in, alone)."""
        tops = [f for f in tops if f]
        if not (attached or tops) or gh_path() is None:
            return
        self._todo.put((list(attached), tops))
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="lookup-github")
            self._thread.start()

    # ---- its thread
    def _run(self):
        while True:
            attached, tops = self._todo.get()
            try:
                while True:
                    more = self._todo.get_nowait()
                    attached, tops = attached + more[0], tops + more[1]
            except queue.Empty:
                pass
            try:
                self.round(attached, tops)
            except Exception as e:  # noqa: BLE001 (whatever it was: asked again at the next look)
                print(f"lookup: reading GitHub failed: {e!r}", file=sys.stderr)

    def round(self, attached=(), tops=()):
        """A look at the repositories in use (look): each once it's due, while GitHub can be asked. What's kept on disk
        of one not seen before is listed at once."""
        if gh_path() is None:
            return
        with self._one:
            repos = self._in_use(attached, tops)
            for repo in repos:
                if repo not in self._repos:
                    self._repos[repo] = self._load(repo)
            self._publish()
            every = max(GITHUB_LOOK_S, 60 * len(repos) / GITHUB_LOOKS_A_MINUTE)
            for repo in repos:
                r, now = self._repos[repo], time.time()
                if now < self._paused:
                    break
                if now >= r["paused"] and now - r["looked"] >= (every if r["etags"].get("recent") else GITHUB_PLAIN_S):
                    self._read(repo, r)
                    self._publish()

    def _in_use(self, attached, tops):
        """The repositories in use, as many as are looked at, shared out so that a folder with many checkouts can't
        crowd out the others: each attached folder's own, then the chat's, then one each of the rest in turn."""
        tops = [self._in_folder(f, False)["repos"] for f in dict.fromkeys(tops) if f not in attached]
        attached = [self._in_folder(f, True)["repos"] for f in dict.fromkeys(attached)]
        order = [repos[0] for repos in attached + tops if repos]
        for turn in itertools.zip_longest(*(repos[1:] for repos in attached + tops)):
            order += [r for r in turn if r]
        return list(dict.fromkeys(order))[:GITHUB_REPOS]

    def _in_folder(self, folder, under):
        """The repositories of a folder, found again every so often (what's published of it, kept: nothing's changed
        unless they have). under: and those of the checkouts under it."""
        known = self._folders.get(folder)
        if known is None or known["under"] != under or time.time() - known["at"] > GITHUB_SCAN_S:
            top, checkouts = github_checkouts(folder, under)
            own = github_remotes(checkouts[0]) if top else []
            repos = [r for c in checkouts for r in github_remotes(c)]
            known = self._folders[folder] = {**(known or {}), "repos": list(dict.fromkeys(repos))[:GITHUB_REPOS],
                                             "under": under, "own": own[0] if own else None, "at": time.time(),
                                             "keys": {folder, top} - {None}}  # (top: a Names' root)
        return known

    def _read(self, repo, r):
        """One look: the latest of everything, and if that's changed (or wasn't read as far as that last time) the open
        ones too."""
        r["looked"] = time.time()
        recent = self._get(repo, r, "recent", f"state=all&sort=updated&direction=desc&per_page={GITHUB_RECENT}")
        if recent == "no issues":  # (a 410: issues switched off, its pull requests still there)
            r["pulls"], r["etags"] = True, {}
            recent = self._get(repo, r, "recent", f"state=all&sort=updated&direction=desc&per_page={GITHUB_RECENT}")
        if recent == "changed":
            r["open_due"] = True
        if recent in ("changed", "same") and r["open_due"] and \
                self._get(repo, r, "open", f"state=open&sort=updated&direction=desc&per_page={GITHUB_OPEN}") in \
                ("changed", "same"):
            r["open_due"] = False

    def _get(self, repo, r, which, query):
        """Ask for one list: "changed" (and it's taken in), "same", "gone" (GitHub won't show this repository), "no
        issues" (it says they're switched off) or "failed". What the answer says of GitHub as a whole goes for every
        repository: signed out, offline, the rate limit."""
        path = f"repos/{repo}/{'pulls' if r['pulls'] else 'issues'}?{query}"
        status, head, body, code = _gh_get(path, r["etags"].get(which))
        now = time.time()
        if code == 4 or status == 401:
            self._out, self._paused = True, now + GITHUB_OUT_S
            return "failed"
        if status is None or status >= 500:  # (offline, or GitHub in trouble)
            self._fails += 1
            self._paused = now + min(GITHUB_BACKOFF_S, GITHUB_LOOK_S * 2 ** self._fails)
            return "failed"
        self._fails, self._out = 0, False
        left = head.get("x-ratelimit-remaining", "")
        try:
            reset = float(head.get("x-ratelimit-reset") or 0)
            wait = float(head.get("retry-after") or 0)
        except ValueError:
            reset = wait = 0.0
        if status in (403, 429) and (wait or left == "0"):
            self._paused = max(now + wait, reset, now + GITHUB_LOOK_S)
            return "failed"
        if left.isdigit() and int(left) < GITHUB_KEEP:
            self._paused = max(self._paused, reset)
        if status == 304:
            return "same"
        if status == 410 and not r["pulls"]:
            return "no issues"
        if status != 200:  # (404: not there, or not yours to see; 403: not with this sign-in)
            r["paused"] = now + GITHUB_GONE_S
            if status == 404:
                r.update(listing=[], recent=[], open=[], etags={})
                try:
                    os.remove(self._path(repo))
                except OSError:
                    pass
            return "gone"
        try:
            kept = [github_kept(p) for p in json.loads(body)]
        except (ValueError, TypeError, KeyError, AttributeError):
            return "failed"
        same = kept == r[which]  # (a new ETag, as a reaction makes, and nothing here to show for it)
        r[which], r["etags"][which] = kept, head.get("etag") or ""
        if not same:
            r["listing"] = self._listing(repo, r)
        self._save(repo, r)
        return "same" if same else "changed"

    @staticmethod
    def _listing(repo, r):
        """What's listed of a repository: its two lists as one, the latest first."""
        latest = {}
        for it in sorted(r["recent"] + r["open"], key=lambda k: k["updated"]):
            latest[it["number"]] = it  # (in both: the one that says it changed last)
        return [github_listing(repo, it) for it in sorted(latest.values(), key=lambda k: k["updated"], reverse=True)]

    def _publish(self):
        """What's listed for each folder as things are: nothing while signed out. A folder's list is a new one only if
        it's changed."""
        changed = False
        for folder in self._folders.values():
            parts = [] if self._out else [self._repos[r]["listing"] for r in folder["repos"] if r in self._repos]
            if folder.get("parts") is None or len(parts) != len(folder["parts"]) or \
                    any(a is not b for a, b in zip(parts, folder["parts"])):
                folder["parts"] = parts
                made = tuple(it for part in parts for it in part)
                for key in folder["keys"]:
                    if made or key in self._lists:
                        self._lists[key] = made
                        changed = True
        if changed:
            self.generation += 1

    # ---- the copy on disk
    @staticmethod
    def _path(repo):
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        return os.path.join(base, "baude", "github", urllib.parse.quote(repo, safe="") + ".json")

    def _load(self, repo):
        r = {"listing": [], "recent": [], "open": [], "etags": {}, "looked": 0.0, "paused": 0.0, "open_due": False,
             "pulls": False}
        try:
            with open(self._path(repo), encoding="utf-8") as f:
                kept = json.load(f)
            listing = self._listing(repo, kept)
            r.update(recent=kept["recent"], open=kept["open"], etags=dict(kept["etags"]), listing=listing,
                     pulls=bool(kept.get("pulls")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass  # (none, or not what's written here)
        return r

    def _save(self, repo, r):
        path = self._path(repo)
        new = f"{path}.{os.getpid()}.new"  # (Baude and Record may both be at it)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(new, "w", encoding="utf-8") as f:
                json.dump({"recent": r["recent"], "open": r["open"], "etags": r["etags"], "pulls": r["pulls"]}, f)
            os.replace(new, path)
        except OSError:
            pass  # (only the next start's wait)


GITHUB = GitHub()


# ---------------------------------------------------------------- where each pull request stands
# The Git page's list of the pull requests a chat touches and its pills on the branches that have one, and the pills a
# reply's #12s and pull request links are drawn as, are each in the colour of where the pull request stands: a draft,
# open, changes asked for (a review's verdict, which holds it up), merged, or closed without being merged. GITHUB's
# lists don't say a review's verdict, nor the branch a pull request is from, so this asks gh pr list for them: the
# latest PULLS_LIMIT of a repository in one call, on a thread of its own, at most every PULLS_FRESH_S while something
# shows them (never one call a pull request), and keeps the answer on disk for the next start. Without gh, or signed
# out, it has nothing: no list, no pills.

PULLS_FRESH_S = 120  # a repository's pull requests are read again after this, if they're still shown
PULLS_LIMIT = 100  # the latest of them read (a number older than these: what GITHUB's lists say of it, if anything)
PULLS_FIELDS = "number,title,state,isDraft,reviewDecision,headRefName,isCrossRepository,url"


def pull_state(p):
    """Where a pull request stands, from gh pr list --json: "merged", "closed" (without being merged), "changes" (a
    review asked for changes: it can't go in till they're made, draft or not), "draft" or "open"."""
    state = (p.get("state") or "").upper()
    if state in ("MERGED", "CLOSED"):
        return state.lower()
    if (p.get("reviewDecision") or "").upper() == "CHANGES_REQUESTED":
        return "changes"
    return "draft" if p.get("isDraft") else "open"


def pull_info(p):
    """What's kept of a pull request gh pr list says: {number, title, state (pull_state), url, head}. head: the branch
    it's from, None if that's a fork's (another repository's branch of that name isn't this one's)."""
    return {"number": int(p["number"]), "title": re.sub(r"\s+", " ", p.get("title") or "").strip()[:300],
            "state": pull_state(p), "url": p.get("url") or "",
            "head": None if p.get("isCrossRepository") else (p.get("headRefName") or None)}


class PullStates:
    """Where the pull requests of each GitHub repository asked about stand (one for the app: PULLS). states(repo), from
    the main loop, is what was read last (or kept from the last start), asking for a read if one's due and never
    waiting for it; read(repo), on a worker thread, waits. generation goes up when what's known changes."""

    def __init__(self):
        self._cond, self._todo, self._thread = threading.Condition(), queue.SimpleQueue(), None
        self.forget()

    def forget(self):
        """Nothing known (in memory: the copies on disk stay)."""
        with self._cond:
            self._repos, self.generation = {}, getattr(self, "generation", 0) + 1

    def _entry(self, repo):  # (with _cond held)
        r = self._repos.get(repo)
        if r is None:
            r = self._repos[repo] = {"prs": None, "at": 0.0, "paused": 0.0, "out": False, "busy": False,
                                     "queued": False}
            try:
                with open(self._path(repo), encoding="utf-8") as f:
                    kept = json.load(f)
                r.update(prs={int(p["number"]): p for p in kept["prs"]}, at=float(kept["at"]),
                         out=bool(kept.get("out")))  # (signed out last time: nothing, till gh says otherwise)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass  # (none kept, or not what's written here)
        return r

    @staticmethod
    def _due(r, now):
        return now >= r["paused"] and now - r["at"] >= PULLS_FRESH_S

    # ---- main loop (it never waits for gh)
    def states(self, repo, want=True):
        """{number: pull_info} of a repository's pull requests as last read, or None: none read yet, no gh, or gh
        signed out. want: and a read asked for if one's due."""
        if not repo or gh_path() is None:
            return None
        with self._cond:
            r = self._entry(repo)
            prs = None if r["out"] else r["prs"]
            want = want and not (r["queued"] or r["busy"]) and self._due(r, time.time())
        if want:
            self.want(repo)
        return prs

    def pull(self, repo, number):
        """What's known of repo's pull request number: its pull_info; else, for one older than those read, what
        GITHUB's lists say (where it stands, not a review's verdict, nor its branch); else None. None too for an issue,
        and with nothing read of repo (states' None)."""
        prs = self.states(repo)
        if prs is None:
            return None
        if (p := prs.get(number)) is not None:
            return p
        k = GITHUB.known(repo, number)
        if k is None or not k.get("pr"):
            return None
        state = "draft" if k["state"] == "open" and k.get("draft") else k["state"]
        return {"number": number, "title": k.get("title", ""), "state": state,
                "url": f"https://github.com/{repo}/pull/{number}", "head": None}

    def signed_out(self):
        """Has gh said it's signed out (a read of any repository's)?"""
        with self._cond:
            return any(r["out"] for r in self._repos.values())

    def want(self, repo, fresh=False):
        """A read of repo's pull requests asked for, on this one's thread, if one's due (fresh: whether or not)."""
        if not repo or gh_path() is None:
            return
        with self._cond:
            r = self._entry(repo)
            if r["queued"] or r["busy"] or not (fresh or self._due(r, time.time())):
                return
            r["queued"] = True
        self._todo.put((repo, fresh))
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="lookup-pulls")
            self._thread.start()

    def look(self, repos):
        """The repositories shown, of those asked about before: each read again if it's due (a window's tick)."""
        for repo in repos:
            if repo and repo in self._repos:
                self.want(repo)

    # ---- worker threads
    def _run(self):
        while True:
            repo, fresh = self._todo.get()
            with self._cond:
                self._repos.get(repo, {})["queued"] = False
            try:
                self.read(repo, fresh)
            except Exception as e:  # noqa: BLE001 (whatever it was: asked again when it's next due)
                print(f"lookup: reading pull requests failed: {e!r}", file=sys.stderr)

    def read(self, repo, fresh=False):
        """(Not the main loop.) repo's pull requests, read now if it's due (fresh: whether or not), else as last read:
        states' answer. A read already under way is waited for, not made twice."""
        if not repo or gh_path() is None:
            return None
        with self._cond:
            r = self._entry(repo)
            while r["busy"]:
                self._cond.wait(GITHUB_TIMEOUT_S + 5)
            go = r["busy"] = fresh or self._due(r, time.time())  # (fresh: asked for, signed in again perhaps)
        if go:
            try:
                self._fetch(repo, r)
            finally:
                with self._cond:
                    r["busy"] = False
                    self._cond.notify_all()
        with self._cond:
            return None if r["out"] else r["prs"]

    def _fetch(self, repo, r):
        exe, code, out, err = gh_path(), None, "", ""
        try:
            p = subprocess.run([exe, "pr", "list", "--repo", repo, "--state", "all", "--limit", str(PULLS_LIMIT),
                                "--json", PULLS_FIELDS], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=GITHUB_TIMEOUT_S, errors="replace",
                               env={**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"})
            code, out, err = p.returncode, p.stdout, p.stderr
        except (OSError, subprocess.TimeoutExpired, TypeError, ValueError):
            pass  # (no answer: as offline)
        prs = None
        if code == 0:
            try:
                prs = {(i := pull_info(p))["number"]: i for p in json.loads(out)}
            except (ValueError, TypeError, KeyError, AttributeError):
                prs = None
        now = time.time()
        with self._cond:
            if prs is not None:
                changed = r["out"] or prs != r["prs"]
                r.update(prs=prs, at=now, paused=0.0, out=False)
            elif code == 4 or "gh auth login" in err:  # (signed out: gh asked no one)
                changed = not r["out"]
                r.update(out=True, at=now, paused=now + GITHUB_OUT_S)
                self._save(repo, now, {}, out=True)  # (what was read goes: the next start shows nothing either)
            else:  # (offline, GitHub in trouble, or a repository it won't show: what was read stays)
                changed = False
                r["paused"] = now + (GITHUB_GONE_S if "Could not resolve" in err else GITHUB_LOOK_S * 5)
            if changed:
                self.generation += 1
        if prs is not None:
            self._save(repo, now, prs)

    # ---- the copy on disk
    @staticmethod
    def _path(repo):
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        return os.path.join(base, "baude", "github", urllib.parse.quote(repo, safe="") + ".pulls.json")

    def _save(self, repo, at, prs, out=False):
        path = self._path(repo)
        new = f"{path}.{os.getpid()}.new"  # (Baude and Record may both be at it)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(new, "w", encoding="utf-8") as f:
                json.dump({"at": at, "prs": list(prs.values()), "out": out}, f)
            os.replace(new, path)
        except OSError:
            pass  # (only the next start's wait)


PULLS = PullStates()


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
        sources = e.get("sources") or [("Name", "")]
        what, where = sources[0]
        icon = {"k8s": "k8s", "branch": "git", "file": "file"}.get(e["kind"]) or \
            ("db" if what.startswith("Database") else "code")
        more = f" · {len(sources) - 1} more" if len(sources) > 1 else ""
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
    (mentions). The source is a Signal (Baude's ⌘K) or a Folder (Record's @, and what its questions say a name is).
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
        """Catalogue the names afresh if it's been a while; on_ready() once that's in. What waits for it (a GLib
        source, to remove if on_ready isn't wanted any more), or None: nothing to wait for."""
        if self.source is None:
            return None
        self.source.catalogue_soon()
        if not self.source.catalogue_busy:
            return None
        schedule = self.schedule
        if schedule is None:
            from gi.repository import GLib
            schedule = GLib.timeout_add

        def check():
            if self.source.catalogue_busy:
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
