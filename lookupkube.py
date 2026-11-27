"""lookupkube — the Kubernetes and database names in lookup's catalogue, and reading them.

What a folder's manifests and Helm charts define (kube_manifest_objects, kube_render), your kubeconfig's contexts
and the clusters k3d, kind and minikube made (kube_contexts), the workloads running in your clusters (kube_running),
and the Postgres and ArangoDB databases running here, in Docker or a cluster, with their tables (db_index). Nothing
here needs GTK: it reads files and runs kubectl, helm and docker (and psql or arangosh in a container).
"""
import itertools
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.parse

from baudelib import wording


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


def _kubeconfig_named():
    env = os.environ.get("KUBECONFIG")
    return [os.path.expanduser(p) for p in env.split(os.pathsep) if p] if env else [os.path.expanduser("~/.kube/config")]


def kubeconfig_paths():
    """The kubeconfig files kubectl would read: $KUBECONFIG's, else ~/.kube/config (the ones that exist)."""
    return [os.path.realpath(p) for p in _kubeconfig_named() if os.path.isfile(p)]


def kubeconfig_stamp():
    """What tells whether a kubeconfig file changed, with a stat each and nothing read: every file kubectl would read
    (one that isn't there yet too), its inode, size and modification time. A tool that writes a new file and renames
    it over the old one changes its inode; one that writes in place, its size or time."""
    out = []
    for p in _kubeconfig_named():
        try:
            st = os.stat(p)
            out.append((p, st.st_ino, st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((p, None))
    return tuple(out)


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
    if not text.strip():
        out["partial"] = True  # (emptied to be written again, or left empty: _kube_entries tells)
    elif not text.endswith("\n") and not text.rstrip().endswith("}"):
        out["unended"] = True  # (cut off as it's written, or written without a last newline: _kube_entries tells)
    if text.lstrip().startswith("{"):  # (JSON is YAML too)
        try:
            doc = json.loads(text)
        except ValueError:
            return {**out, "partial": True}
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
_KUBE_GOOD = {}  # path -> its entries as last read whole (kept for one caught half written)
_KUBE_CONTEXTS_LOCK = threading.Lock()  # (held to look at or keep a read: never while a file's read or waited on)
KUBE_FORGET = []  # fn(key): what's kept of a cluster (its kube_key) dropped, as its context goes from the kubeconfig or
# its server changes


def kube_key(c):
    """A context as a cluster: its name and its cluster's server. One deleted and made again under the same name is
    another cluster (its server's port, at least, is new), and nothing kept of the old one is its."""
    return c.get("name"), c.get("server") or ""


def _kube_changing(path):
    """Whether a kubeconfig file was written to in the last second (a tool may still be writing it)."""
    try:
        return time.time() - os.stat(path).st_mtime < 1.0
    except OSError:
        return True


def _kube_entries(path, wait):
    """kubeconfig_entries(path), and whether it was read whole. One caught half written (emptied, or without its last
    newline, while it's still changing: one that has stopped changing is taken as it is) is taken again for a moment
    if wait. Still half written, or not waited for, the last whole read of it."""
    e = None
    for i in range(10 if wait else 1):
        if i:
            time.sleep(0.05)
        e = kubeconfig_entries(path)
        if (e.get("partial") or e.get("unended")) and _kube_changing(path):
            continue
        with _KUBE_CONTEXTS_LOCK:
            _KUBE_GOOD[path] = e
        return e, True
    with _KUBE_CONTEXTS_LOCK:
        good = _KUBE_GOOD.get(path)
    return (good if good is not None else e), False


def kube_contexts(wait=True):
    """Every kubeconfig context kubectl would see, with its cluster's server: [{name, cluster, server, namespace, user,
    kubeconfig, line, current}]; the clusters no context names are listed as contexts of their own name too. Read again
    when a file changes (kubeconfig_stamp), and again if one changed while it was read. wait=False on the main thread:
    a file caught half written isn't waited on (its last whole read is used, and the read isn't kept)."""
    for _ in range(5):
        key = kubeconfig_stamp()
        with _KUBE_CONTEXTS_LOCK:
            if key in _KUBE_CONTEXTS:
                return _KUBE_CONTEXTS[key]
        out, current, whole = [], None, True
        for p in kubeconfig_paths():
            e, read = _kube_entries(p, wait)
            whole = whole and read
            current = current or e["current"]  # (the first file that sets it wins, as in kubectl)
            servers = {c["name"]: c.get("server") for c in e["clusters"]}
            for c in e["contexts"]:
                out.append({**c, "server": servers.get(c.get("cluster")), "kubeconfig": p})
            named = {c.get("cluster") for c in e["contexts"]}
            out += [{"name": c["name"], "cluster": c["name"], "server": c.get("server"), "kubeconfig": p,
                     "line": c.get("line"), "cluster_only": True} for c in e["clusters"] if c["name"] not in named]
        if kubeconfig_stamp() == key:
            break
    for c in out:
        c["current"] = c["name"] == current
    before = None
    with _KUBE_CONTEXTS_LOCK:
        if whole and kubeconfig_stamp() == key:
            before = next(iter(_KUBE_CONTEXTS.values()), None)
            _KUBE_CONTEXTS.clear()
            _KUBE_CONTEXTS[key] = out
    if before is not None:
        _kube_forget(before, out)
    return out


def kube_contexts_kept():
    """kube_contexts' list if the files haven't changed since it was read (a stat each, nothing read); else None."""
    key = kubeconfig_stamp()
    with _KUBE_CONTEXTS_LOCK:
        return _KUBE_CONTEXTS.get(key)


def _kube_forget(before, now):
    """What's kept of each context that's gone, or whose server changed, let go of (KUBE_FORGET's, and the local
    clusters' workloads and databases, read again next time they're asked for)."""
    keys = {kube_key(c) for c in now}
    gone = [c for c in before if kube_key(c) not in keys]
    for c in gone:
        _KUBE_RUNNING.pop(c["name"], None)
        for fn in list(KUBE_FORGET):
            try:
                fn(kube_key(c))
            except Exception:  # noqa: BLE001 (one cache's trouble isn't the others')
                pass
    if any(kube_local(c.get("server")) for c in gone) or \
            {kube_key(c) for c in now if kube_local(c.get("server"))} - {kube_key(c) for c in before}:
        for k in [k for k in PG_LISTS if k == ("servers",) or k == ("index",) or k[0] == "databases"]:
            PG_LISTS.pop(k, None)


class _KubeWatcher:
    """The kubeconfig files looked at every POLL_S (a stat each: kubeconfig_stamp), on one thread for every watcher,
    while there's one. A change is read once the files have stopped changing for SETTLE_S (SETTLE_MAX_S at most: a
    file rewritten without a pause is read then anyway), so a file being written is never taken for its half; then each
    watcher's fn(contexts) is called (on that thread) if the contexts differ from those it was last told of (or had
    when it began: kube_contexts_kept's, else it's told of them as they are): one added or gone, a server, the
    current one. The thread ends once no one watches, mid-change too."""
    POLL_S = 1.0
    SETTLE_S = 0.3
    SETTLE_MAX_S = 2.5

    def __init__(self):
        self.fns, self.lock, self.thread = {}, threading.Lock(), None  # (fn -> the contexts it knows of)
        self.wake = threading.Event()

    def add(self, fn):
        kept = kube_contexts_kept()  # (nothing read here: add is called on the main thread)
        with self.lock:
            self.fns[fn] = self.said(kept) if kept is not None else None
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True, name="kubeconfig-watch")
                self.thread.start()
        self.wake.set()

        def remove():
            with self.lock:
                self.fns.pop(fn, None)
        return remove

    @staticmethod
    def said(contexts):
        return [(c["name"], c.get("server"), bool(c.get("current")), bool(c.get("cluster_only"))) for c in contexts]

    def _watched(self):
        """Whether anyone still watches; if not, the thread is let go of (it returns)."""
        with self.lock:
            if not self.fns:
                self.thread = None
                return False
            return True

    def _run(self):
        stamp = None
        while self._watched():
            now = kubeconfig_stamp()
            if stamp is not None and now != stamp:
                settled = time.monotonic() + self.SETTLE_MAX_S
                while time.monotonic() < settled:  # (still being written: read once it's done)
                    time.sleep(self.SETTLE_S)
                    if not self._watched():
                        return
                    if (again := kubeconfig_stamp()) == now:
                        break
                    now = again
            with self.lock:
                new = any(known is None for known in self.fns.values())
            if now != stamp or new:
                contexts = kube_contexts()
                stamp = now if kube_contexts_kept() is not None else None  # (a file still half written: read again)
                said = self.said(contexts)
                with self.lock:
                    told = [fn for fn, known in self.fns.items() if known != said]
                    for fn in told:
                        self.fns[fn] = said
                for fn in told:
                    try:
                        fn(contexts)
                    except Exception:  # noqa: BLE001 (a watcher's trouble isn't the others')
                        pass
            self.wake.wait(self.POLL_S)
            self.wake.clear()


_WATCHER = _KubeWatcher()


def kube_contexts_watch(fn):
    """fn(contexts) whenever the kubeconfig's contexts change (kube_contexts' list; on a thread of the watcher's), as
    external tools add and delete clusters; a function that stops it."""
    return _WATCHER.add(fn)


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


def _docker(*args, stdin=None, timeout=30):
    """docker's output, or raise RuntimeError with what it said."""
    from baudelib import deps
    if "docker" in deps.hidden():  # (fault injection: as if it weren't there)
        raise RuntimeError("docker: not found")
    try:
        p = subprocess.run([deps.program("docker") or "docker", *args], input=stdin, capture_output=True, text=True,
                           timeout=timeout)
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
            return {"state": wording.say("lookup.kube_suspended"), "ok": False}
        now = time.time()
        last = kube_seconds(_ks(o, "status", "lastScheduleTime"), now)
        return {"state": wording.say("lookup.kube_last_ran", ago=kube_fmt_age(last)), "ok": True, "at": now - last} \
            if last is not None else {"state": wording.say("lookup.kube_not_run_yet"), "ok": True}
    if o.get("kind") == "DaemonSet":
        want, ready = _ks(o, "status", "desiredNumberScheduled", default=0), _ks(o, "status", "numberReady", default=0)
    else:
        want, ready = _ks(o, "spec", "replicas", default=1), _ks(o, "status", "readyReplicas", default=0)
    if not want:
        return {"state": wording.say("lookup.kube_scaled_to_0"), "ok": False}
    return {"state": wording.say("lookup.kube_ready", ready=ready or 0, want=want), "ok": (ready or 0) >= want}


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
    """psql's answer (-At) to sql, one statement, run in the server's container (docker exec, or kubectl exec for a
    pod's) as its own user: in a read-only transaction, rolled back. Each -c goes to the server as it is (no psql
    meta-commands, variables or script), so sql is the one statement there is if it has no ; in it."""
    psql = ["psql", "-X", "-q", "-U", server["user"], "-d", database, "-At", "-v", "ON_ERROR_STOP=1",
            "-c", "begin transaction read only", "-c", sql, "-c", "rollback"]
    if server.get("exec"):
        cmd = server["exec"] + ["env", "PGOPTIONS=-c default_transaction_read_only=on"] + psql
        try:
            p = subprocess.run(cmd, input="", capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(wording.say("kube.kubectl_error", error=e)) from e
        if p.returncode:
            lines = (p.stderr or p.stdout).strip().splitlines()
            raise RuntimeError(lines[-1] if lines else wording.say("kube.kubectl_exited", code=p.returncode))
        return p.stdout
    return _docker("exec", "-e", "PGOPTIONS=-c default_transaction_read_only=on", server["container"], *psql)


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
    from baudelib import deps
    prefix = server.get("exec") or [deps.program("docker") or "docker", "exec", "-i", server["container"]]
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
    credential plugin failed; why it did (aws, gcloud, kubelogin's own message) is in the lines before (kube_diagnose).
    removed: the context it was run in is no longer in the kubeconfig (its cluster was deleted meanwhile)."""

    def __init__(self, last, said="", removed=False):
        super().__init__(last)
        self.said, self.removed = said or last, removed


_KUBE_BROKEN = re.compile(r"error loading config file", re.I)  # (client-go's words for a kubeconfig it can't read: a
# server's own bad answer, an HTML login page, an undecodable resource, says otherwise)
KUBE_BROKEN_TRIES = 2  # a kubeconfig caught half written: what failed is run again this many times at most


_KUBE_NO_CONTEXT = re.compile(r"context was not found for specified context|context \"[^\"]*\" does not exist|"
                              r"no context exists with the name", re.I)


def kube_config_broken(said):
    """Whether kubectl failed because it read a kubeconfig file half written (a tool rewriting it meanwhile)."""
    return bool(_KUBE_BROKEN.search(said or ""))


def kube_removed(context, settle=True, contexts=None):
    """Whether a context is no longer in the kubeconfig (a context name, or a kube_key: then a context of that name
    with another server is a cluster made again, and the one asked for is gone too). In contexts if given (a watcher's,
    read once the files settled), else as the files are now; then, with settle, one gone is looked at again after
    SETTLE_S, in case a file was being written, so it's called off the main thread."""
    name, server = context if isinstance(context, tuple) else (context, None)

    def gone(cs):
        return not any(c["name"] == name and (server is None or (c.get("server") or "") == server)
                       for c in cs if not c.get("cluster_only"))
    if not gone(contexts if contexts is not None else kube_contexts()):
        return False
    if contexts is not None or not settle:
        return True
    time.sleep(_KubeWatcher.SETTLE_S)
    return gone(kube_contexts())


def kube_target(context, server=None):
    """kubectl's arguments for a context's cluster: the context, and with the server the view knew it by, that server
    (so kubectl talks only to the cluster it was shown, not to one made again under the context's name)."""
    return ["--context", context] + ([f"--server={server}"] if server else [])


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


def kube_run(tool, args, timeout=30, input=None):
    """A kubectl or helm command's output (input: its stdin, else none); KubeError with what it wrote if it fails
    (removed, if its context has gone from the kubeconfig). One that read a kubeconfig half written (a tool rewriting
    it) is run once more."""
    if (fault := kube_fault(tool)) is not None and "config" not in args[:1]:
        raise KubeError(fault.splitlines()[-1], fault)
    context = next((args[i + 1] for i in range(len(args) - 1) if args[i] in ("--context", "--kube-context")), None)
    for again in (True, False):
        try:
            p = subprocess.run([kube_tool(tool), *args], capture_output=True, text=True, timeout=timeout,
                               **({"input": input} if input is not None else {"stdin": subprocess.DEVNULL}))
        except (OSError, subprocess.TimeoutExpired) as e:
            raise KubeError(f"{tool}: {e}") from e
        said = (p.stderr or p.stdout).strip() if p.returncode else ""
        removed = p.returncode and context is not None and _KUBE_NO_CONTEXT.search(said) and kube_removed(context)
        if not (again and p.returncode and not removed and (kube_config_broken(said) or _KUBE_NO_CONTEXT.search(said))):
            break
        time.sleep(0.3)  # (its context not there yet as kubectl read it: the file was being written)
    if p.returncode:
        raise KubeError(said.splitlines()[-1] if said else f"{tool} exited with {p.returncode}", said,
                        removed=bool(removed or context is not None and kube_removed(context)))
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
