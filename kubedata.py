"""The Kubernetes view's data: kubectl's answers as Freelens' pages and columns, with no GTK, for the view and for a host process asked by a head elsewhere."""
import json
import os
import re
import time

import lookup  # noqa: E402,F401
from lookup import full_time  # noqa: E402,F401
from lookupkube import (  # noqa: E402,F401
    _km, _ks, kube_fault, kube_fmt_age, kube_get, kube_run, kube_seconds, kube_tool,
)
from baudelib import wording  # noqa: E402


KUBE_REFRESH_S = 10  # the pods' metrics (and the list shown) are read again this often while the view is open


KUBE_LOG_TAIL = 500  # a log starts with this many of its last lines


KUBE_LOG_LINES = 20000  # and keeps this many as it's followed (the oldest go)


_KUBE_KLOG = re.compile(r"^[EWIF]\d{4} [\d:.]+\s+\d+ [\w.]+:\d+\] ")  # (client-go's log lines: E1028 09:57:43… x.go:381])


def kube_diagnose(said, signin=None):
    """What a kubectl failure means, from all it wrote: {"title", "why" (its words that say why), "fix" (a command to
    run in a terminal, or None)}. signin: how the context signs in (kube_signin), for the command. The credential
    plugin's own message comes before kubectl's last line, which only says the plugin failed (client-go passes the
    plugin's stderr through)."""
    lines = []
    for line in (said or "").splitlines():
        line = _KUBE_KLOG.sub("", line).strip()
        if line and line not in lines:
            lines.append(line)
    text = "\n".join(lines)
    signin = signin or {}
    profile = signin.get("profile")
    if m := re.search(r"The config profile \(([^)]+)\) could not be found", text):
        return {"title": wording.say("kube.aws_profile_missing", profile=m[1]), "why": m[0],
                "fix": f"aws configure sso --profile {m[1]}"}
    login = f"aws sso login --profile {profile}" if profile else "aws sso login"

    def first(pattern, last=False):  # (the plugin's line; with last, kubectl's own, which ends what it wrote)
        hits = [l.removeprefix("aws: [ERROR]: ").removeprefix("Unable to connect to the server: ")
                for l in lines if re.search(pattern, l, re.I)]
        return (hits[-1] if last else hits[0]) if hits else None
    if why := first(r"Token has expired and refresh failed|SSO session .*(expired|invalid)|Error loading SSO Token|"
                    r"UnauthorizedSSOTokenError|sso.*expired"):
        return {"title": wording.say("kube.aws_expired"), "why": why, "fix": login}
    if why := first(r"Unable to locate credentials|ExpiredToken|security token included in the request is (expired|"
                    r"invalid)"):
        return {"title": wording.say("kube.aws_no_credentials"), "why": why, "fix": login}
    if why := first(r"Reauthentication|gcloud auth login|problem refreshing your current auth tokens"):
        return {"title": wording.say("kube.gcloud_expired"), "why": why.split(" (err: ")[-1],
                "fix": "gcloud auth login"}
    if why := first(r"AADSTS\d+|az login|kubelogin"):
        return {"title": wording.say("kube.azure_expired"), "why": why[why.find("AADSTS"):] if "AADSTS" in why else why,
                "fix": "az login"}
    if m := re.search(r"exec: executable (\S+) not found", text):
        return {"title": wording.say("kube.not_installed", program=m[1]),
                "why": wording.say("kube.not_on_path", program=m[1], path=os.environ.get('PATH', '')), "fix": None}
    if why := first(r"cannot support interactive mode|standard input is not a terminal"):
        return {"title": wording.say("kube.needs_terminal"), "why": why,
                "fix": f"kubectl --context {signin['context']} get namespaces" if signin.get("context") else None}
    if why := first(r"must be logged in to the server|provide credentials|\(Unauthorized\)", last=True):
        return {"title": wording.say("kube.credentials_refused"), "why": why,
                "fix": login if signin.get("command") == "aws" else None}
    if why := first(r"\(Forbidden\)|is forbidden:", last=True):
        return {"title": wording.say("kube.not_allowed"), "why": why.removeprefix("Error from server (Forbidden): "),
                "fix": None}
    if why := first(r"x509:|certificate", last=True):
        return {"title": wording.say("kube.untrusted_certificate"), "why": why, "fix": None}
    if why := first(r"was refused|connection refused", last=True):
        here = re.search(r"(127\.0\.0\.1|localhost|\[::1\]|host\.docker\.internal)[:/]", why)  # (a cluster on this machine)
        return {"title": wording.say("kube.not_running") if here else wording.say("kube.unreachable"), "why": why,
                "fix": None}
    if why := first(r"no such host|i/o timeout|Unable to connect to the server|timed out|network is unreachable",
                    last=True):
        return {"title": wording.say("kube.unreachable"), "why": why, "fix": None}
    if m := re.search(r"exec: executable (\S+) failed with exit code \d+", text):  # (a plugin that said nothing known)
        return {"title": wording.say("kube.signin_failed", program=m[1]),
                "why": next((l for l in lines if "exec:" not in l), lines[-1]), "fix": None}
    return {"title": wording.say("kube.kubectl_failed"), "why": lines[-1] if lines else "", "fix": None}


def kube_signin(context):
    """How a context signs in, from the kubeconfig alone (no cluster asked): {"context", "command" (its credential
    plugin: aws, gke-gcloud-auth-plugin, kubelogin), "profile" (AWS_PROFILE, or --profile)}."""
    try:
        cfg = json.loads(kube_run("kubectl", ["config", "view", "--minify", "--context", context, "-o", "json"],
                                  timeout=10) or "{}")
    except (RuntimeError, ValueError):
        return {"context": context}
    ex = ((cfg.get("users") or [{}])[0].get("user") or {}).get("exec") or {}
    args, env = ex.get("args") or [], {e.get("name"): e.get("value") for e in ex.get("env") or []}
    profile = env.get("AWS_PROFILE") or (args[args.index("--profile") + 1] if "--profile" in args[:-1] else None)
    return {"context": context, "command": os.path.basename(ex.get("command") or "") or None, "profile": profile}


def kube_api_resources(context):
    """The resources the cluster serves, as kubectl names them (pods, deployments.apps, …)."""
    return set(kube_run("kubectl", ["--context", context, "api-resources", "-o", "name"]).split())


def kube_delete_pod(context, namespace, name, force=False):
    """Delete a pod, once you've confirmed it (worker thread): what kubectl said. Without force it doesn't wait for
    the pod to go (its grace period, 30 s by default): the list shows it Terminating meanwhile. With force, the API
    forgets it at once, whether or not its containers have stopped."""
    args = ["--context", context, "delete", "pod", name, "-n", namespace]
    args += ["--grace-period=0", "--force"] if force else ["--wait=false"]
    return kube_run("kubectl", args, timeout=60).strip()


def kube_delete(context, namespace, name, force=False):
    """Delete a pod (worker thread), once you've confirmed it: what to say of it: kubectl's text, else that it's gone,
    or why it failed."""
    try:
        return kube_delete_pod(context, namespace, name, force) or wording.say("kube.deleted", name=name)
    except RuntimeError as e:
        return wording.say("kube.delete_failed", name=name, why=e)


def kube_secret_yaml(namespace, name, kind, keys):
    """What a Secret shows: only its keys' names (its values are never read)."""
    return (f"# Secret {namespace}/{name} ({kind})\n# " + wording.say("kube.secret_values_hidden") + "\nkeys:\n" +
            "".join(f"  - {k}\n" for k in keys))


def kube_cell_tone(col, text):
    """A status cell's tone (ok, warn, bad, or None) from its text: the first of several, by the column's own reading or
    the common one."""
    return (col["tone"] or kube_status_tone)(text.split(",")[0].strip())


def kube_uid(o):
    """What an object is known by across a watch's events: its uid, or its namespace and name without one."""
    m = _km(o)
    return m.get("uid") or (m.get("namespace") or "", m.get("name") or "")


def kube_cpu(q):
    """A CPU quantity in cores: 250m → 0.25, 2902430n → 0.0029, 2 → 2.0 (None if it isn't one)."""
    m = re.fullmatch(r"([\d.]+)([numk]?)", str(q or "").strip())
    if not m:
        return None
    return float(m.group(1)) * {"n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3}[m.group(2)]


def kube_bytes(q):
    """A memory quantity in bytes: 57340Ki, 1Gi, 500M, 1e3 (None if it isn't one)."""
    m = re.fullmatch(r"([\d.]+(?:e\d+)?)([KMGTPE]i?|k)?", str(q or "").strip())
    if not m:
        return None
    unit = m.group(2) or ""
    power = "KMGTPE".index(unit[0].upper()) + 1 if unit else 0
    return float(m.group(1)) * ((1024 if unit.endswith("i") else 1000) ** power)


def kube_fmt_bytes(n):
    if n is None:
        return "N/A"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def kube_pod_status(pod):
    """A pod's status as Freelens says it (Evicted, Terminating, Finalizing, else its phase), or, while a container
    waits for a reason (CrashLoopBackOff, ImagePullBackOff…), that reason, as kubectl does."""
    st, meta = pod.get("status") or {}, pod.get("metadata") or {}
    if st.get("reason") == "Evicted":
        return "Evicted"
    containers = st.get("containerStatuses") or []
    if meta.get("deletionTimestamp"):
        if any({"running", "waiting"} & set(c.get("state") or {}) for c in containers):  # ({"running": {}} too)
            return "Terminating"
        if meta.get("finalizers"):
            return "Finalizing"
    if st.get("phase") in ("Pending", "Running", None):
        for c in (st.get("initContainerStatuses") or []) + containers:
            reason = ((c.get("state") or {}).get("waiting") or {}).get("reason")
            if reason and reason != "PodInitializing":
                return reason
    return st.get("phase") or "Waiting"


# A ready container whose last run ended (lastState.terminated.finishedAt) less than an hour ago is green ringed with
# orange: it's up again, but it fell over lately. An hour is long enough to notice, short enough to mean "lately".
KUBE_RESTARTED_RECENTLY_S = 3600


def kube_container_look(cs, now):
    """How a container's square looks, from its status (None: none yet), and that in words: "ok" (running and
    ready), "starting" (waiting, starting, or running but not ready yet), "restarted" (ready, but restarted in the
    past KUBE_RESTARTED_RECENTLY_S), "bad" (crashed or failed: CrashLoopBackOff, an image that won't pull, exited
    with an error) or "done" (exited 0: an init container, a job's)."""
    if not cs:
        return "starting", wording.say("kube.not_started")
    state = cs.get("state") or {}
    restarts = cs.get("restartCount") or 0

    def times(look):
        return wording.say("kube.with_restarts", look=look, n=restarts) if restarts else look
    if "running" in state:
        if not cs.get("ready"):
            return "starting", times(wording.say("kube.running_not_ready"))
        ended = kube_seconds(_ks(cs, "lastState", "terminated", "finishedAt"), now)
        if ended is not None and ended < KUBE_RESTARTED_RECENTLY_S:
            return "restarted", times(wording.say("kube.running_restarted", ago=kube_fmt_age(ended),
                                                  at=full_time(now - ended)))
        return "ok", times(wording.say("kube.running_ready"))
    if "terminated" in state:
        t = state["terminated"]
        if not t.get("exitCode") and t.get("reason") in (None, "Completed"):
            return "done", wording.say("kube.completed")
        reason = t.get('reason') or 'Error'
        return "bad", times(wording.say("kube.terminated_code", reason=reason, code=t['exitCode']) if t.get("exitCode")
                            else wording.say("kube.terminated", reason=reason))
    reason = (state.get("waiting") or {}).get("reason") or ""
    bad = kube_status_tone(reason) == "bad" or reason.startswith("Invalid")  # (InvalidImageName too)
    return ("bad" if bad else "starting"), \
        times(wording.say("kube.waiting_for", reason=reason) if reason else wording.say("kube.waiting"))


def kube_containers(pod, now):
    """A pod's containers, its init containers first, as Freelens shows them: [(name, look, words, init)] (looks:
    kube_container_look). Named by the spec, so one with no status yet is there too."""
    st, spec, out = pod.get("status") or {}, pod.get("spec") or {}, []
    for init, specs, statuses in ((True, spec.get("initContainers"), st.get("initContainerStatuses")),
                                  (False, spec.get("containers"), st.get("containerStatuses"))):
        by = {c.get("name"): c for c in statuses or []}
        for name in [c.get("name") for c in specs or []] or list(by):
            out.append((name, *kube_container_look(by.get(name), now), init))
    return out


def kube_status_tone(text):
    """How a status reads: "ok", "warn" or "bad" (its colour in the table), or None."""
    t = (text or "").lower()
    if t in ("running", "succeeded", "active", "bound", "available", "complete", "ready", "deployed", "true",
             "normal", "superseded"):
        return "ok"
    if t in ("pending", "terminating", "finalizing", "waiting", "containercreating", "released", "suspended",
             "pending-install", "pending-upgrade", "uninstalling") or t.startswith("pending"):
        return "warn"
    if t in ("failed", "evicted", "error", "notready", "lost", "warning", "unknown") or "backoff" in t or \
            "err" in t or "oom" in t:
        return "bad"
    return None


def _kcount(v):
    return "" if v is None else str(v)


def kube_col(title, value, key=None, kind=None, tone=None):
    """A column: its title, its text for an object (value(o, ctx)), what it sorts by (key(o, ctx); the text, by
    default), and what it is: "name", "namespace" (a click filters to it), "owner" (Kind/name: a click goes to it),
    "status" (coloured: tone(text), kube_status_tone by default), "age", "containers" (a pod's, a square each) or
    None."""
    return {"title": title, "value": value, "key": key, "kind": kind, "tone": tone}


def _age_col(title="Age", path=("metadata", "creationTimestamp")):
    return kube_col(title, lambda o, c: kube_fmt_age(kube_seconds(_ks(o, *path), c["now"])),
                    lambda o, c: kube_seconds(_ks(o, *path), c["now"]), "age")


K_NAME = kube_col("Name", lambda o, c: _km(o).get("name") or o.get("name", ""), kind="name")  # (helm's: top-level)


K_NS = kube_col("Namespace", lambda o, c: _km(o).get("namespace") or o.get("namespace", ""), kind="namespace")


K_AGE = _age_col()


def _ncol(title, path):
    return kube_col(title, lambda o, c: _kcount(_ks(o, *path)), lambda o, c: _ks(o, *path, default=-1))


def _pod_ready(o, c):
    cs = _ks(o, "status", "containerStatuses", default=[])
    total = len(_ks(o, "spec", "containers", default=[]) or cs)
    return f"{sum(1 for x in cs if x.get('ready'))}/{total}"


def _pod_usage(o, c, which):
    m = c.get("metrics")
    if m is None:
        return None
    u = m.get((_km(o).get("namespace"), _km(o).get("name")))
    return u[which] if u else None


# kinds a click on Controlled By looks past, to what controls them (by the name kubectl gets them by)
# The fields of an object's YAML that name another object, as the API's schema defines them: (the path's end, the key)
# -> its kind; "*" takes the kind from the mapping's own kind: key (an owner reference, a scale target, a role ref,
# an event's object). Clicked in the Kubernetes view's YAML, the object it names is shown.
KUBE_REF_FIELDS = {
    (("metadata",), "namespace"): "Namespace",
    (("ownerReferences",), "name"): "*", (("scaleTargetRef",), "name"): "*", (("roleRef",), "name"): "*",
    (("subjects",), "name"): "*", (("involvedObject",), "name"): "*", (("regarding",), "name"): "*",
    (("targetRef",), "name"): "*", (("related",), "name"): "*",
    (("spec",), "nodeName"): "Node", (("spec",), "serviceAccountName"): "ServiceAccount",
    (("spec",), "priorityClassName"): "PriorityClass", (("spec",), "runtimeClassName"): "RuntimeClass",
    (("spec",), "volumeName"): "PersistentVolume", (("spec",), "storageClassName"): "StorageClass",
    (("spec",), "ingressClassName"): "IngressClass", (("spec",), "serviceName"): "Service",
    (("configMap",), "name"): "ConfigMap", (("configMapRef",), "name"): "ConfigMap",
    (("configMapKeyRef",), "name"): "ConfigMap", (("secret",), "secretName"): "Secret", (("secret",), "name"): "Secret",
    (("secretRef",), "name"): "Secret", (("secretKeyRef",), "name"): "Secret",
    (("imagePullSecrets",), "name"): "Secret", (("tls",), "secretName"): "Secret",
    (("persistentVolumeClaim",), "claimName"): "PersistentVolumeClaim", (("claimRef",), "name"): "*",
    (("backend", "service"), "name"): "Service", (("defaultBackend", "service"), "name"): "Service",
}


KUBE_REF_SKIP = {"User", "Group"}  # (an RBAC subject that's no object the API has)


def kube_yaml_refs(text, namespace=None):
    """The names of other objects in an object's YAML as kubectl writes it (block style, a list's dashes under its key):
    [(line, column, length, (kind, name, namespace))], 0-based, by KUBE_REF_FIELDS. A reference in the object's own
    namespace unless it says another (an event's object, a subject); a cluster's object (a Node) in none."""
    out, frames, skip = [], [], None  # (frames: the mappings open: indent, path, {key: (line, column, value)})

    def close(f):
        for key, (line, col, value) in f["keys"].items():
            kind = next((k for (end, k_), k in KUBE_REF_FIELDS.items()
                         if k_ == key and tuple(f["path"][-len(end):]) == end), False)
            if kind == "*":  # (a KEDA scaled object's target that names no kind is a Deployment, as KEDA reads it)
                kind = (f["keys"].get("kind") or (0, 0, ""))[2] or \
                    ("Deployment" if f["path"][-1:] == ["scaleTargetRef"] else None)
            if not kind or kind in KUBE_REF_SKIP or not value:
                continue
            ns = (f["keys"].get("namespace") or (0, 0, ""))[2] or \
                (None if kind in KUBE_CLUSTER_KINDS or kind == "Namespace" else namespace)
            out.append((line, col, len(value), (kind, value, ns)))
    for no, raw in enumerate(text.split("\n")):
        s = raw.lstrip(" ")
        n = len(raw) - len(s)
        if skip is not None:
            if not s or n > skip:  # (a block scalar's lines, or a long string's)
                continue
            skip = None
        if not s or s.startswith("#"):
            continue
        dash = False
        while s == "-" or s.startswith("- "):
            dash, n, s = True, n + 2, s[2:]
        while frames and (frames[-1]["indent"] > n or dash and frames[-1]["indent"] == n):
            close(frames.pop())
        m = re.match(r"([\w./-]+|\"[^\"]*\"):(?: (.*))?$", s)
        if not m:
            continue
        if not frames or frames[-1]["indent"] < n:
            up = frames[-1] if frames else None
            frames.append({"indent": n, "path": up["path"] + [up["last"]] if up else [], "keys": {}, "last": None})
        f, key, value = frames[-1], m[1].strip('"'), (m[2] or "").strip()
        col = len(raw) - len(s) + (m.start(2) if m[2] is not None else len(s))
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value, col = value[1:-1], col + 1
        f["keys"][key], f["last"] = (no, col, value), key
        if value:
            skip = n  # (what's deeper is this value's: a block scalar, a long string folded)
    while frames:
        close(frames.pop())
    return sorted(out)


KUBE_CLUSTER_KINDS = {"Node", "Namespace", "PersistentVolume", "StorageClass", "PriorityClass", "RuntimeClass",
                      "IngressClass", "ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition"}


def kube_resource(kind):
    """The resource kubectl calls a kind by: Ingress → ingresses, NetworkPolicy → networkpolicies."""
    k = kind.lower()
    return k if k.endswith("endpoints") else k + "es" if k.endswith(("s", "x", "ch", "sh")) else \
        k[:-1] + "ies" if k.endswith("y") and k[-2:-1] not in "aeiou" else k + "s"


KUBE_CLIMB = {"ReplicaSet": "replicasets", "Job": "jobs"}


def kube_controller(o):
    """The ownerReference that controls o (the first one, if none says it does), or None."""
    refs = _km(o).get("ownerReferences") or []
    return next((r for r in refs if r.get("controller")), refs[0] if refs else None)


def _owner(o, c):
    ref = kube_controller(o)
    return f"{ref.get('kind')}/{ref.get('name')}" if ref else ""


def _true_conditions(o, c):
    return ", ".join(x.get("type", "") for x in _ks(o, "status", "conditions", default=[]) if x.get("status") == "True")


def _job_status(o, c):
    for x in _ks(o, "status", "conditions", default=[]):
        if x.get("status") == "True" and x.get("type") in ("Complete", "Failed", "Suspended"):
            return x["type"]
    return "Running" if _ks(o, "status", "active") else "Pending"


def _job_duration(o, c):
    start = kube_seconds(_ks(o, "status", "startTime"), c["now"])
    end = kube_seconds(_ks(o, "status", "completionTime"), c["now"])
    return None if start is None else start - (end or 0)


def _svc_ports(o, c):
    out = []
    for p in _ks(o, "spec", "ports", default=[]):
        port = f"{p.get('port')}" + (f":{p['nodePort']}" if p.get("nodePort") else "")
        out.append(f"{port}/{p.get('protocol', 'TCP')}")
    return ", ".join(out)


def _lb(o, c):
    ing = _ks(o, "status", "loadBalancer", "ingress", default=[]) or []
    return ", ".join(x.get("ip") or x.get("hostname") or "" for x in ing) or \
        ", ".join(_ks(o, "spec", "externalIPs", default=[]) or [])


def _svc_status(o, c):
    return "Pending" if _ks(o, "spec", "type") == "LoadBalancer" and not _lb(o, c) else "Active"


def _ing_rules(o, c):
    out = []
    for r in _ks(o, "spec", "rules", default=[]):
        for p in _ks(r, "http", "paths", default=[]):
            svc = _ks(p, "backend", "service", default={})
            port = _ks(svc, "port", "number") or _ks(svc, "port", "name") or ""
            out.append(f"{r.get('host', '*')}{p.get('path', '/')} → {svc.get('name', '')}:{port}")
    return ", ".join(out)


def _endpoints(o, c):
    out = []
    for sub in o.get("subsets") or []:
        ports = [str(p.get("port")) for p in sub.get("ports") or []] or [""]
        for a in sub.get("addresses") or []:
            out += [f"{a.get('ip')}:{p}" if p else a.get("ip", "") for p in ports]
    return wording.say("kube.list_and_more", list=", ".join(out[:6]), n=len(out) - 6) if len(out) > 6 else \
        ", ".join(out)


def _slice_endpoints(o, c):
    ips = [ip for e in o.get("endpoints") or [] for ip in e.get("addresses") or []]
    return wording.say("kube.list_and_more", list=", ".join(ips[:6]), n=len(ips) - 6) if len(ips) > 6 else \
        ", ".join(ips)


def _subjects(o, c):
    return ", ".join(f"{s.get('kind')}/{s.get('name')}" for s in o.get("subjects") or [])


def _keys(field):
    return lambda o, c: ", ".join(sorted(list((o.get(field) or {}).keys()) +
                                         (list((o.get("binaryData") or {}).keys()) if field == "data" else [])))


def _node_roles(o, c):
    labels = _km(o).get("labels") or {}
    return ", ".join(sorted(k.split("/", 1)[1] for k in labels if k.startswith("node-role.kubernetes.io/"))) or "<none>"


def _node_ip(o, c):
    return next((a.get("address") for a in _ks(o, "status", "addresses", default=[]) if a.get("type") == "InternalIP"), "")


def _node_ready(o, c):
    ready = next((x for x in _ks(o, "status", "conditions", default=[]) if x.get("type") == "Ready"), None)
    state = "Ready" if ready and ready.get("status") == "True" else "NotReady"
    return state + (", SchedulingDisabled" if _ks(o, "spec", "unschedulable") else "")


def _node_usage(o, c, which):
    m = c.get("node_metrics")
    if m is None:
        return None
    u = m.get(_km(o).get("name"))
    return u[which] if u else None


def _cpu_col(fn):
    return kube_col("CPU", lambda o, c: "N/A" if fn(o, c, 0) is None else f"{fn(o, c, 0):.3f}",
                    lambda o, c: -1 if fn(o, c, 0) is None else fn(o, c, 0))


def _mem_col(fn):
    return kube_col("Memory", lambda o, c: kube_fmt_bytes(fn(o, c, 1)),
                    lambda o, c: -1 if fn(o, c, 1) is None else fn(o, c, 1))


def _status_col(fn, title="Status"):
    return kube_col(title, fn, kind="status")


# ---- a custom resource's columns: the printer columns its definition declares (additionalPrinterColumns), which
# kubectl get prints. Each is a JSONPath into the object, read here in the subset definitions use (no eval)
_KUBE_JSONPATHS = {}


def kube_jsonpath(path):
    """A printer column's JSONPath as steps for kube_jsonpath_find, or None if it isn't of the subset read here: .a.b,
    .a[0].b, .a[-1], .a[1:3], .a[*].b, .a['b.c'], ..a, and filters: [?(@.type=="Ready")], with != too, or [?(@.a)]
    (it has one)."""
    if path in _KUBE_JSONPATHS:
        return _KUBE_JSONPATHS[path]
    s = path.strip()
    s = s[1:-1].strip() if s.startswith("{") and s.endswith("}") else s
    s = s[1:] if s.startswith("$") else s
    s = s if not s or s[0] in ".[" else "." + s  # (status.phase: as .status.phase)
    steps, i = [], 0
    try:
        while i < len(s):
            if s.startswith("..", i):
                name, i = _kube_jp_name(s, i + 2)
                steps.append(("descend", name))
            elif s[i] == ".":
                name, i = _kube_jp_name(s, i + 1)
                steps.append(("all",) if name == "*" else ("key", name))
            elif s[i] == "[":
                end = _kube_jp_close(s, i)
                steps.append(_kube_jp_bracket(s[i + 1:end].strip()))
                i = end + 1
            else:
                raise ValueError(s[i:])
    except (ValueError, IndexError):
        steps = None
    _KUBE_JSONPATHS[path] = steps
    return steps


def _kube_jp_name(s, i):
    """A key's name in a JSONPath from i, to the next . or [ (a \\. is a dot in it: app\\.kubernetes\\.io/name)."""
    out = []
    while i < len(s) and s[i] not in ".[":
        if s[i] == "\\" and i + 1 < len(s):
            i += 1
        out.append(s[i])
        i += 1
    if not out:
        raise ValueError("no name")
    return "".join(out), i


def _kube_jp_close(s, i):
    """Where the [ at i closes (past quotes, and brackets within a filter)."""
    depth, quote = 0, None
    for j in range(i, len(s)):
        if quote:
            quote = None if s[j] == quote else quote
        elif s[j] in "'\"":
            quote = s[j]
        elif s[j] == "[":
            depth += 1
        elif s[j] == "]":
            depth -= 1
            if not depth:
                return j
    raise ValueError("no ]")


def _kube_jp_literal(text):
    t = text.strip()
    if len(t) > 1 and t[0] == t[-1] and t[0] in "'\"" and t[0] not in t[1:-1]:  # (not "a" && @.b=="c": not read here)
        return t[1:-1]
    if t in ("true", "false"):
        return t == "true"
    return float(t) if "." in t else int(t)  # (else ValueError: not a JSONPath read here)


def _kube_jp_bracket(inner):
    if inner == "*":
        return ("all",)
    if m := re.fullmatch(r"\?\(\s*@([^=!<>]*?)\s*(?:(==|!=|<=|>=|<|>)(.+))?\)", inner, re.S):
        sub = kube_jsonpath(m[1]) if m[1] else []
        if sub is None:
            raise ValueError(inner)
        return ("filter", sub, m[2], _kube_jp_literal(m[3]) if m[2] else None)
    if len(inner) > 1 and inner[0] == inner[-1] and inner[0] in "'\"":
        return ("key", inner[1:-1])
    if re.fullmatch(r"-?\d+", inner):
        return ("index", int(inner))
    if m := re.fullmatch(r"(-?\d*):(-?\d*)(?::(-?\d+))?", inner):
        return ("slice", *(int(x) if x else None for x in m.groups()))
    raise ValueError(inner)


_KUBE_JP_OPS = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, "<": lambda a, b: a < b,
                 ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b}


def _kube_jp_test(x, step):
    """Whether an item passes a filter step: has the path (no operator), or compares to the value as asked (an item
    without it doesn't, whatever the operator, as in kubectl)."""
    _f, sub, op, value = step
    got = kube_jsonpath_find(x, sub) if sub else [x]
    if not op or not got:
        return bool(got)
    try:
        return _KUBE_JP_OPS[op](got[0], value)
    except TypeError:  # (a string against a number)
        return False


def _kube_jp_descend(n, name):
    if isinstance(n, dict):
        return ([n[name]] if name in n else []) + [x for v in n.values() for x in _kube_jp_descend(v, name)]
    return [x for v in n for x in _kube_jp_descend(v, name)] if isinstance(n, list) else []


def kube_jsonpath_find(o, steps):
    """The values at a JSONPath's steps (kube_jsonpath) in o: [] where it has none (a missing key: no value, as
    kubectl's printer columns allow)."""
    if not steps:
        return []
    nodes = [o]
    for step in steps:
        out = []
        for n in nodes:
            if step[0] == "key":
                if isinstance(n, dict) and step[1] in n:
                    out.append(n[step[1]])
            elif step[0] == "all":
                out += list(n.values()) if isinstance(n, dict) else n if isinstance(n, list) else []
            elif step[0] == "index":
                if isinstance(n, list) and -len(n) <= step[1] < len(n):
                    out.append(n[step[1]])
            elif step[0] == "slice":
                if isinstance(n, list):
                    out += n[step[1]:step[2]:step[3]]
            elif step[0] == "filter":
                if isinstance(n, list):
                    out += [x for x in n if _kube_jp_test(x, step)]
            elif step[0] == "descend":
                out += _kube_jp_descend(n, step[1])
        nodes = out
    return [n for n in nodes if n is not None]


def _kube_cell_text(v):
    """A value as kubectl prints one in a column (true, 3, 0.5, text), a list's items joined, else compact JSON."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v):
        return ", ".join(_kube_cell_text(x) for x in v if x is not None)
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"))
    return "" if v is None else str(v)


def kube_printer_cell(values, kind, now):
    """A printer column's cell: (its text, what it sorts by; None: by its text) from the values its JSONPath found, by
    the column's type: a date is an age (as K_AGE), an integer or number sorts as one, anything else is text, the
    values joined with commas (.spec.triggers[*].type: cpu, prometheus)."""
    if not values:
        return "", None
    v = values[0]
    if kind == "date":
        s = kube_seconds(v, now) if isinstance(v, str) else None
        return kube_fmt_age(s), s
    if kind in ("integer", "number") and isinstance(v, (int, float)) and not isinstance(v, bool):
        n = int(v) if kind == "integer" and float(v).is_integer() else v
        return _kube_cell_text(n), n
    return ", ".join(t for t in (_kube_cell_text(x) for x in values) if t), None


# How a condition's status reads in a column of it (.status.conditions[?(@.type=="Ready")].status): True is good and
# False nothing to see (Active), unless it's a condition that must hold (Ready: False is bad), a fault (Degraded: True
# is bad) or a hold (Paused, a scaler's Fallback: True is a warning); Unknown is undecided
_KUBE_MUST_HOLD = {"True": "ok", "False": "bad", "Unknown": "warn"}


KUBE_CONDITION_TONES = {
    **dict.fromkeys(("ready", "available", "synced", "healthy", "established", "reconciled", "accepted", "programmed",
                     "resolvedrefs", "valid"), _KUBE_MUST_HOLD),
    **dict.fromkeys(("degraded", "failed", "stalled"), {"True": "bad", "Unknown": "warn"}),
    **dict.fromkeys(("paused", "suspended", "fallback"), {"True": "warn"})}


_KUBE_CONDITION = re.compile(r"\.status\.conditions\[\?\(\s*@\.type\s*==\s*['\"]([\w.-]+)['\"]\s*\)\]\.status")


def kube_condition_tone(kind):
    """A column of a condition's status (of type kind) coloured: text → "ok", "warn", "bad" or None."""
    tones = KUBE_CONDITION_TONES.get(kind.lower(), {"True": "ok", "Unknown": "warn"})
    return tones.get


def kube_col_title(name):
    """A printer column's name in the view's words: ScaleTargetKind → Scale Target Kind, READY → Ready."""
    name = name.capitalize() if name.isupper() and len(name) > 3 else name
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)


def kube_printer_col(p):
    """A column from a printer column ({name, type, jsonPath}), its type as kubectl reads it (kube_printer_cell). A
    condition's status, or a column named Status, Phase, State or Health, is a status, coloured."""
    path, kind = (p.get("jsonPath") or "").strip(), p.get("type") or "string"
    steps = kube_jsonpath(path)
    tone = kube_condition_tone(m[1]) if (m := _KUBE_CONDITION.fullmatch(path)) else None
    status = tone is not None or kind == "string" and p.get("name", "").lower() in ("status", "phase", "state",
                                                                                     "health")
    return kube_col(kube_col_title(p.get("name") or path), lambda o, c: kube_printer_cell(
        kube_jsonpath_find(o, steps), kind, c["now"])[0],
        (lambda o, c: kube_printer_cell(kube_jsonpath_find(o, steps), kind, c["now"])[1])
        if kind in ("integer", "number", "date") else None,
        "status" if status else "age" if kind == "date" else None, tone)


_KUBE_PRINTED = {}  # (the columns made for a set of printer columns: the same ones each time, so no table is rebuilt)


def kube_printer_columns(namespaced, printer):
    """A custom resource's columns from its definition's printer columns, as kubectl get prints them by default (not
    -o wide's, of a priority above 0): Name, Namespace if it has them, its own, and Age (where it has an Age, else
    last)."""
    key = json.dumps([namespaced, printer], sort_keys=True)
    if key in _KUBE_PRINTED:
        return _KUBE_PRINTED[key]
    cols = [K_NAME, K_NS] if namespaced else [K_NAME]
    for p in printer:
        path = (p.get("jsonPath") or "").strip()
        if (p.get("priority") or 0) > 0 or path in (".metadata.name", ".metadata.namespace") or \
                path == ".metadata.creationTimestamp" and K_AGE in cols:
            continue
        cols.append(K_AGE if path == ".metadata.creationTimestamp" else kube_printer_col(p))
    if K_AGE not in cols:
        cols.append(K_AGE)
    return _KUBE_PRINTED.setdefault(key, cols)


def kube_crd_printer(crd, version=None):
    """The printer columns a definition declares for a version (the one kubectl got: the list's apiVersion's); else
    its storage version, if served."""
    served = [v for v in _ks(crd, "spec", "versions", default=[]) or [] if v.get("served", True)]
    version = next((v for v in served if version and v.get("name") == version), None) or \
        next((v for v in served if v.get("storage")), served[0] if served else {})
    cols = version.get("additionalPrinterColumns")
    return list(cols if cols is not None else _ks(crd, "spec", "additionalPrinterColumns", default=[]) or [])


def kube_crd_page(title, crd, namespaced, printer, known=True):
    """A custom resource's page: its definition's name (plural.group, what kubectl gets it by), whether its objects are
    in namespaces, and its printer columns, in the definition's own words (shown if the cluster's definition can't
    be read, or declares none: kube_page_columns). known: one the sidebar lists while the cluster serves it (KEDA's
    scaled objects), and not at all on a cluster without it."""
    page = kube_page(title, crd, namespaced, kube_printer_columns(namespaced, printer), crd)
    page.update(crd=crd, printer=printer, known=known)
    return page


def _kp(name, path, kind="string", priority=0):
    """A printer column, as a definition declares one."""
    return {"name": name, "type": kind, "jsonPath": path, "priority": priority}


def _kp_condition(kind, field="status"):
    return f'.status.conditions[?(@.type=="{kind}")].{field}'


# what well-known definitions declare (or, for those that declare none, what's worth a column): KEDA's are the ones
# its 2.x releases have declared, its triggers read from each scaled object's spec, so they're right whatever KEDA it
# is (newer ones read them from its status); cert-manager's, Argo Rollouts', Istio's and the Gateway API's are theirs
KEDA_SCALED_OBJECT = [
    _kp("ScaleTargetKind", ".status.scaleTargetKind"), _kp("ScaleTargetName", ".spec.scaleTargetRef.name"),
    _kp("Min", ".spec.minReplicaCount", "integer"), _kp("Max", ".spec.maxReplicaCount", "integer"),
    _kp("Triggers", ".spec.triggers[*].type"), _kp("Authentication", ".spec.triggers[*].authenticationRef.name"),
    _kp("Ready", _kp_condition("Ready")), _kp("Active", _kp_condition("Active")),
    _kp("Fallback", _kp_condition("Fallback")), _kp("Paused", _kp_condition("Paused")),
    _kp("Age", ".metadata.creationTimestamp", "date")]


KEDA_SCALED_JOB = [
    _kp("Min", ".spec.minReplicaCount", "integer"), _kp("Max", ".spec.maxReplicaCount", "integer"),
    _kp("Triggers", ".spec.triggers[*].type"), _kp("Authentication", ".spec.triggers[*].authenticationRef.name"),
    _kp("Ready", _kp_condition("Ready")), _kp("Active", _kp_condition("Active")),
    _kp("Paused", _kp_condition("Paused")), _kp("Age", ".metadata.creationTimestamp", "date")]


KEDA_TRIGGER_AUTH = [
    _kp("PodIdentity", ".spec.podIdentity.provider"), _kp("Secret", ".spec.secretTargetRef[*].name"),
    _kp("Env", ".spec.env[*].name"), _kp("VaultAddress", ".spec.hashiCorpVault.address"),
    _kp("ScaledObjects", ".status.scaledobjects", priority=1), _kp("ScaledJobs", ".status.scaledjobs", priority=1)]


CERT_MANAGER_CERTIFICATE = [
    _kp("Ready", _kp_condition("Ready")), _kp("Secret", ".spec.secretName"),
    _kp("Issuer", ".spec.issuerRef.name", priority=1), _kp("Status", _kp_condition("Ready", "message"), priority=1),
    _kp("Age", ".metadata.creationTimestamp", "date")]


CERT_MANAGER_ISSUER = [
    _kp("Ready", _kp_condition("Ready")), _kp("Status", _kp_condition("Ready", "message"), priority=1),
    _kp("Age", ".metadata.creationTimestamp", "date")]


def kube_page(title, resource, namespaced, columns, api=None, source="kubectl"):
    """A kind the sidebar lists: what kubectl calls it (resource), whether its objects are in namespaces, its columns,
    the name `kubectl api-resources` gives it (api: to tell whether the cluster serves it), and where its list comes
    from (kubectl; "secrets": names and keys only; "helm": helm's releases)."""
    return {"title": title, "resource": resource, "namespaced": namespaced, "columns": columns,
            "api": api or resource, "source": source}


KUBE_PODS = kube_page("Pods", "pods", True, [
    K_NAME, K_NS,
    kube_col("Containers", _pod_ready, lambda o, c: tuple(int(x) for x in _pod_ready(o, c).split("/")),
             "containers"),  # (drawn as a square each; "1/2", ready of all, is what it's filtered and sorted by)
    _cpu_col(_pod_usage), _mem_col(_pod_usage),
    kube_col("Restarts", lambda o, c: str(sum(x.get("restartCount", 0) for x in _ks(o, "status", "containerStatuses",
                                                                                     default=[]))),
             lambda o, c: sum(x.get("restartCount", 0) for x in _ks(o, "status", "containerStatuses", default=[]))),
    kube_col("Controlled By", _owner, kind="owner"),
    kube_col("Node", lambda o, c: _ks(o, "spec", "nodeName", default="")),
    K_AGE, _status_col(lambda o, c: kube_pod_status(o))])


KUBE_HOME = kube_page("Home", "home", True, [], source="summary")  # (the whole stack's CPU and memory: kube_summary)


KUBE_NODES = kube_page("Nodes", "nodes", False, [
    K_NAME, _cpu_col(_node_usage), _mem_col(_node_usage), kube_col("Roles", _node_roles),
    kube_col("Version", lambda o, c: _ks(o, "status", "nodeInfo", "kubeletVersion", default="")),
    kube_col("Internal IP", _node_ip), K_AGE, _status_col(_node_ready, "Conditions")])


KUBE_SECTIONS = [
    ("Home", [KUBE_HOME]),
    ("Nodes", [KUBE_NODES]),
    ("Workloads", [
        KUBE_PODS,
        kube_page("Deployments", "deployments", True, [
            K_NAME, K_NS,
            kube_col("Ready", lambda o, c: f"{_ks(o, 'status', 'readyReplicas', default=0)}/"
                                           f"{_ks(o, 'spec', 'replicas', default=0)}",
                     lambda o, c: _ks(o, "status", "readyReplicas", default=0)),
            _ncol("Desired", ("spec", "replicas")), _ncol("Updated", ("status", "updatedReplicas")),
            _ncol("Available", ("status", "availableReplicas")), K_AGE,
            _status_col(_true_conditions, "Conditions")], "deployments.apps"),
        kube_page("DaemonSets", "daemonsets", True, [
            K_NAME, K_NS, _ncol("Desired", ("status", "desiredNumberScheduled")),
            _ncol("Current", ("status", "currentNumberScheduled")), _ncol("Ready", ("status", "numberReady")),
            _ncol("Updated", ("status", "updatedNumberScheduled")), _ncol("Available", ("status", "numberAvailable")),
            kube_col("Node Selector", lambda o, c: ", ".join(
                f"{k}={v}" for k, v in (_ks(o, "spec", "template", "spec", "nodeSelector", default={}) or {}).items())),
            K_AGE], "daemonsets.apps"),
        kube_page("StatefulSets", "statefulsets", True, [
            K_NAME, K_NS,
            kube_col("Ready", lambda o, c: f"{_ks(o, 'status', 'readyReplicas', default=0)}/"
                                           f"{_ks(o, 'spec', 'replicas', default=0)}",
                     lambda o, c: _ks(o, "status", "readyReplicas", default=0)),
            _ncol("Desired", ("spec", "replicas")), K_AGE], "statefulsets.apps"),
        kube_page("ReplicaSets", "replicasets", True, [
            K_NAME, K_NS, _ncol("Desired", ("spec", "replicas")), _ncol("Current", ("status", "replicas")),
            _ncol("Ready", ("status", "readyReplicas")), K_AGE], "replicasets.apps"),
        kube_page("Replication Controllers", "replicationcontrollers", True, [
            K_NAME, K_NS, _ncol("Desired", ("spec", "replicas")), _ncol("Current", ("status", "replicas")),
            _ncol("Ready", ("status", "readyReplicas")), K_AGE]),
        kube_page("Jobs", "jobs", True, [
            K_NAME, K_NS, _status_col(_job_status), _ncol("Succeeded", ("status", "succeeded")),
            _ncol("Completions", ("spec", "completions")), _ncol("Parallelism", ("spec", "parallelism")),
            kube_col("Duration", lambda o, c: kube_fmt_age(_job_duration(o, c)), lambda o, c: _job_duration(o, c) or 0),
            K_AGE], "jobs.batch"),
        kube_page("CronJobs", "cronjobs", True, [
            K_NAME, K_NS, kube_col("Schedule", lambda o, c: _ks(o, "spec", "schedule", default="")),
            kube_col("Timezone", lambda o, c: _ks(o, "spec", "timeZone", default="")),
            kube_col("Suspended", lambda o, c: wording.say("common.yes") if _ks(o, "spec", "suspend") else
                     wording.say("common.no")),
            kube_col("Active", lambda o, c: str(len(_ks(o, "status", "active", default=[]) or [])),
                     lambda o, c: len(_ks(o, "status", "active", default=[]) or [])),
            _age_col("Last schedule", ("status", "lastScheduleTime")), K_AGE], "cronjobs.batch"),
        kube_crd_page("Rollouts", "rollouts.argoproj.io", True, [  # (Argo Rollouts')
            _kp("Desired", ".spec.replicas", "integer"), _kp("Current", ".status.replicas", "integer"),
            _kp("Up-to-date", ".status.updatedReplicas", "integer"),
            _kp("Available", ".status.availableReplicas", "integer"),
            _kp("Age", ".metadata.creationTimestamp", "date")])]),
    ("Config", [
        kube_page("ConfigMaps", "configmaps", True, [K_NAME, K_NS, kube_col("Keys", _keys("data")), K_AGE]),
        kube_page("Secrets", "secrets", True, [K_NAME, K_NS, kube_col("Keys", lambda o, c: ", ".join(o.get("keys", []))),
                                               kube_col("Type", lambda o, c: o.get("type", "")), K_AGE],
                  source="secrets"),
        kube_page("Resource Quotas", "resourcequotas", True, [K_NAME, K_NS, K_AGE]),
        kube_page("Limit Ranges", "limitranges", True, [K_NAME, K_NS, K_AGE]),
        kube_page("Horizontal Pod Autoscalers", "horizontalpodautoscalers", True, [
            K_NAME, K_NS, _ncol("Min Pods", ("spec", "minReplicas")), _ncol("Max Pods", ("spec", "maxReplicas")),
            _ncol("Replicas", ("status", "currentReplicas")), K_AGE], "horizontalpodautoscalers.autoscaling"),
        kube_page("Vertical Pod Autoscalers", "verticalpodautoscalers", True, [K_NAME, K_NS, K_AGE],
                  "verticalpodautoscalers.autoscaling.k8s.io"),
        kube_page("Pod Disruption Budgets", "poddisruptionbudgets", True, [
            K_NAME, K_NS, kube_col("Min Available", lambda o, c: str(_ks(o, "spec", "minAvailable", default=""))),
            kube_col("Max Unavailable", lambda o, c: str(_ks(o, "spec", "maxUnavailable", default=""))),
            _ncol("Current Healthy", ("status", "currentHealthy")), _ncol("Desired Healthy", ("status", "desiredHealthy")),
            K_AGE], "poddisruptionbudgets.policy"),
        kube_page("Priority Classes", "priorityclasses", False, [
            K_NAME, _ncol("Value", ("value",)), kube_col("Global Default", lambda o, c: str(bool(o.get("globalDefault")))),
            K_AGE], "priorityclasses.scheduling.k8s.io"),
        kube_page("Runtime Classes", "runtimeclasses", False, [
            K_NAME, kube_col("Handler", lambda o, c: o.get("handler", "")), K_AGE], "runtimeclasses.node.k8s.io"),
        kube_page("Leases", "leases", True, [
            K_NAME, K_NS, kube_col("Holder", lambda o, c: _ks(o, "spec", "holderIdentity", default="")), K_AGE],
            "leases.coordination.k8s.io"),
        kube_page("Mutating Webhook Configurations", "mutatingwebhookconfigurations", False, [
            K_NAME, kube_col("Webhooks", lambda o, c: str(len(o.get("webhooks") or [])),
                             lambda o, c: len(o.get("webhooks") or [])), K_AGE],
            "mutatingwebhookconfigurations.admissionregistration.k8s.io"),
        kube_page("Validating Webhook Configurations", "validatingwebhookconfigurations", False, [
            K_NAME, kube_col("Webhooks", lambda o, c: str(len(o.get("webhooks") or [])),
                             lambda o, c: len(o.get("webhooks") or [])), K_AGE],
            "validatingwebhookconfigurations.admissionregistration.k8s.io"),
        kube_page("Validating Admission Policies", "validatingadmissionpolicies", False, [K_NAME, K_AGE],
                  "validatingadmissionpolicies.admissionregistration.k8s.io"),
        kube_page("Validating Admission Policy Bindings", "validatingadmissionpolicybindings", False, [K_NAME, K_AGE],
                  "validatingadmissionpolicybindings.admissionregistration.k8s.io")]),
    # (KEDA's kinds, beside the pod autoscalers in Config: only on a cluster with KEDA, as the other known custom
    # resources below are only where they're installed)
    ("KEDA", [
        kube_crd_page("Scaled Objects", "scaledobjects.keda.sh", True, KEDA_SCALED_OBJECT),
        kube_crd_page("Scaled Jobs", "scaledjobs.keda.sh", True, KEDA_SCALED_JOB),
        kube_crd_page("Trigger Authentications", "triggerauthentications.keda.sh", True, KEDA_TRIGGER_AUTH),
        kube_crd_page("Cluster Trigger Authentications", "clustertriggerauthentications.keda.sh", False,
                      KEDA_TRIGGER_AUTH)]),
    ("Network", [
        kube_page("Services", "services", True, [
            K_NAME, K_NS, kube_col("Type", lambda o, c: _ks(o, "spec", "type", default="")),
            kube_col("Cluster IP", lambda o, c: _ks(o, "spec", "clusterIP", default="")),
            kube_col("External IP", _lb), kube_col("Ports", _svc_ports), K_AGE, _status_col(_svc_status)]),
        kube_page("Endpoints", "endpoints", True, [K_NAME, K_NS, kube_col("Endpoints", _endpoints), K_AGE]),
        kube_page("Endpoint Slices", "endpointslices", True, [
            K_NAME, K_NS, kube_col("Address Type", lambda o, c: o.get("addressType", "")),
            kube_col("Ports", lambda o, c: ", ".join(f"{p.get('port')}/{p.get('protocol', 'TCP')}"
                                                       for p in o.get("ports") or [])),
            kube_col("Endpoints", _slice_endpoints), K_AGE], "endpointslices.discovery.k8s.io"),
        kube_page("Ingresses", "ingresses", True, [
            K_NAME, K_NS, kube_col("LoadBalancers", _lb), kube_col("Rules", _ing_rules), K_AGE],
            "ingresses.networking.k8s.io"),
        kube_page("Ingress Classes", "ingressclasses", False, [
            K_NAME, kube_col("Controller", lambda o, c: _ks(o, "spec", "controller", default="")), K_AGE],
            "ingressclasses.networking.k8s.io"),
        kube_page("Network Policies", "networkpolicies", True, [
            K_NAME, K_NS, kube_col("Policy Types", lambda o, c: ", ".join(_ks(o, "spec", "policyTypes", default=[]))),
            K_AGE], "networkpolicies.networking.k8s.io"),
        kube_crd_page("HTTP Routes", "httproutes.gateway.networking.k8s.io", True, [  # (the Gateway API's)
            _kp("Hostnames", ".spec.hostnames"), _kp("Age", ".metadata.creationTimestamp", "date")]),
        kube_crd_page("Ingress Routes", "ingressroutes.traefik.io", True, [  # (Traefik's, which declares none)
            _kp("Entry Points", ".spec.entryPoints"), _kp("Match", ".spec.routes[*].match"),
            _kp("Services", ".spec.routes[*].services[*].name")]),
        kube_crd_page("Virtual Services", "virtualservices.networking.istio.io", True, [  # (Istio's)
            _kp("Gateways", ".spec.gateways"), _kp("Hosts", ".spec.hosts"),
            _kp("Age", ".metadata.creationTimestamp", "date")])]),
    ("Storage", [
        kube_page("Persistent Volume Claims", "persistentvolumeclaims", True, [
            K_NAME, K_NS, kube_col("Storage class", lambda o, c: _ks(o, "spec", "storageClassName", default="")),
            kube_col("Size", lambda o, c: _ks(o, "status", "capacity", "storage") or
                     _ks(o, "spec", "resources", "requests", "storage", default=""),
                     lambda o, c: kube_bytes(_ks(o, "status", "capacity", "storage") or
                                             _ks(o, "spec", "resources", "requests", "storage")) or 0),
            K_AGE, _status_col(lambda o, c: _ks(o, "status", "phase", default=""))]),
        kube_page("Persistent Volumes", "persistentvolumes", False, [
            K_NAME, kube_col("Storage Class", lambda o, c: _ks(o, "spec", "storageClassName", default="")),
            kube_col("Capacity", lambda o, c: _ks(o, "spec", "capacity", "storage", default=""),
                     lambda o, c: kube_bytes(_ks(o, "spec", "capacity", "storage")) or 0),
            kube_col("Claim", lambda o, c: "/".join(x for x in (_ks(o, "spec", "claimRef", "namespace"),
                                                                _ks(o, "spec", "claimRef", "name")) if x)),
            K_AGE, _status_col(lambda o, c: _ks(o, "status", "phase", default=""))]),
        kube_page("Storage Classes", "storageclasses", False, [
            K_NAME, kube_col("Provisioner", lambda o, c: o.get("provisioner", "")),
            kube_col("Reclaim Policy", lambda o, c: o.get("reclaimPolicy", "")),
            kube_col("Default", lambda o, c: wording.say("common.yes") if (_km(o).get("annotations") or {}).get(
                "storageclass.kubernetes.io/is-default-class") == "true" else ""), K_AGE],
            "storageclasses.storage.k8s.io")]),
    ("Namespaces", [kube_page("Namespaces", "namespaces", False, [
        K_NAME, kube_col("Labels", lambda o, c: ", ".join(f"{k}={v}" for k, v in (_km(o).get("labels") or {}).items()
                                                          if not k.startswith("kubernetes.io/metadata"))),
        K_AGE, _status_col(lambda o, c: _ks(o, "status", "phase", default=""))])]),
    ("Events", [kube_page("Events", "events", True, [
        _status_col(lambda o, c: o.get("type", ""), "Type"), kube_col("Message", lambda o, c: o.get("message", "")),
        K_NS, kube_col("Involved Object", lambda o, c: f"{_ks(o, 'involvedObject', 'kind', default='')}/"
                                                       f"{_ks(o, 'involvedObject', 'name', default='')}"),
        kube_col("Source", lambda o, c: _ks(o, "source", "component") or o.get("reportingComponent") or ""),
        _ncol("Count", ("count",)), K_AGE,
        kube_col("Last Seen", lambda o, c: kube_fmt_age(kube_seconds(o.get("lastTimestamp") or o.get("eventTime"),
                                                                     c["now"])),
                 lambda o, c: kube_seconds(o.get("lastTimestamp") or o.get("eventTime"), c["now"]), "age")])]),
    ("Helm", [kube_page("Releases", "releases", True, [
        K_NAME, K_NS, kube_col("Chart", lambda o, c: o.get("chart", "")),
        kube_col("Revision", lambda o, c: str(o.get("revision", "")), lambda o, c: int(o.get("revision") or 0)),
        kube_col("App Version", lambda o, c: o.get("app_version", "")),
        _status_col(lambda o, c: o.get("status", "")),
        kube_col("Updated", lambda o, c: str(o.get("updated", ""))[:19])], api="", source="helm")]),
    ("User Management", [
        kube_page("Service Accounts", "serviceaccounts", True, [K_NAME, K_NS, K_AGE]),
        kube_page("Cluster Roles", "clusterroles", False, [K_NAME, K_AGE], "clusterroles.rbac.authorization.k8s.io"),
        kube_page("Roles", "roles", True, [K_NAME, K_NS, K_AGE], "roles.rbac.authorization.k8s.io"),
        kube_page("Cluster Role Bindings", "clusterrolebindings", False, [
            K_NAME, kube_col("Bindings", _subjects), K_AGE], "clusterrolebindings.rbac.authorization.k8s.io"),
        kube_page("Role Bindings", "rolebindings", True, [K_NAME, K_NS, kube_col("Bindings", _subjects), K_AGE],
                  "rolebindings.rbac.authorization.k8s.io")]),
    ("Certificates", [  # (cert-manager's)
        kube_crd_page("Certificates", "certificates.cert-manager.io", True, CERT_MANAGER_CERTIFICATE),
        kube_crd_page("Issuers", "issuers.cert-manager.io", True, CERT_MANAGER_ISSUER),
        kube_crd_page("Cluster Issuers", "clusterissuers.cert-manager.io", False, CERT_MANAGER_ISSUER)]),
    ("Monitoring", [  # (the Prometheus Operator's, which declare none)
        kube_crd_page("Service Monitors", "servicemonitors.monitoring.coreos.com", True, [
            _kp("Ports", ".spec.endpoints[*].port"), _kp("Job Label", ".spec.jobLabel")]),
        kube_crd_page("Pod Monitors", "podmonitors.monitoring.coreos.com", True, [
            _kp("Ports", ".spec.podMetricsEndpoints[*].port"), _kp("Job Label", ".spec.jobLabel")]),
        kube_crd_page("Prometheus Rules", "prometheusrules.monitoring.coreos.com", True, [
            _kp("Groups", ".spec.groups[*].name")])]),
    ("Custom Resources", [kube_page("Definitions", "customresourcedefinitions", False, [
        kube_col("Resource", lambda o, c: _ks(o, "spec", "names", "kind", default=""), kind="name"),
        kube_col("Group", lambda o, c: _ks(o, "spec", "group", default="")),
        kube_col("Version", lambda o, c: next((v.get("name") for v in _ks(o, "spec", "versions", default=[])
                                               if v.get("storage")), "")),
        kube_col("Scope", lambda o, c: _ks(o, "spec", "scope", default="")), K_AGE],
        "customresourcedefinitions.apiextensions.k8s.io")]),
]


# each category's icon, as Freelens' sidebar has them (KUBE_ICONS)
KUBE_SECTION_ICONS = {"Home": "baude-kube-home-symbolic", "Nodes": "baude-kube-nodes-symbolic", "Workloads": "baude-kube-workloads-symbolic",
                      "Config": "baude-kube-config-symbolic", "Network": "baude-kube-network-symbolic",
                      "Storage": "baude-kube-storage-symbolic", "Namespaces": "baude-kube-namespaces-symbolic",
                      "Events": "baude-kube-events-symbolic", "Helm": "baude-kube-helm-symbolic",
                      "User Management": "baude-kube-users-symbolic", "Custom Resources": "baude-kube-crds-symbolic",
                      "KEDA": "baude-kube-scaling-symbolic", "Certificates": "baude-kube-certificates-symbolic",
                      "Monitoring": "baude-kube-monitoring-symbolic"}


# a Secret's list: names, types, creation times and its keys' names; never its values
KUBE_SECRETS_TEMPLATE = ('{{range .items}}{{.metadata.namespace}}{{"\\t"}}{{.metadata.name}}{{"\\t"}}{{.type}}'
                         '{{"\\t"}}{{.metadata.creationTimestamp}}{{"\\t"}}{{range $k, $v := .data}}{{$k}},{{end}}'
                         '{{"\\n"}}{{end}}')


def kube_custom_page(crd):
    """The page for a custom resource's objects (from its definition): name, namespace (if it has them), the printer
    columns it declares, age."""
    names, group = _ks(crd, "spec", "names", default={}), _ks(crd, "spec", "group", default="")
    return kube_crd_page(names.get("kind") or names.get("plural", "?"), f"{names.get('plural')}.{group}",
                         _ks(crd, "spec", "scope") == "Namespaced", kube_crd_printer(crd), known=False)


_KUBE_PRINTERS = {}  # (context, definition) -> (when, its printer columns, or None: it couldn't be read)


def kube_page_columns(context, page, max_age=60, items=()):
    """A page's columns on a context (worker thread). A custom resource's are its definition's printer columns as this
    cluster has them (another KEDA prints others) for the version its objects came as (items'), read once a minute at
    most; those it's known by if its definition declares none, or can't be read (a role that may list scaled objects
    but not definitions, say)."""
    if not page.get("crd"):
        return page["columns"]
    crd = kube_page_crd(context, page, max_age)
    version = next((i["apiVersion"].rsplit("/", 1)[-1] for i in items if isinstance(i, dict) and i.get("apiVersion")),
                   None)
    printer = kube_crd_printer(crd, version) if crd is not None else None
    return kube_printer_columns(page["namespaced"], printer or page["printer"])


def kube_page_crd(context, page, max_age=60):
    """A custom resource's definition on a context, its versions' printer columns only (worker thread; once a minute
    at most), or None if it can't be read."""
    key = (context, page["crd"])
    when, crd = _KUBE_PRINTERS.get(key, (-max_age, None))
    if time.monotonic() - when >= max_age:
        try:
            full = json.loads(kube_get(context, "customresourcedefinitions", page["crd"], "-o", "json") or "{}")
            crd = {"spec": {"versions": [{k: v[k] for k in ("name", "served", "storage", "additionalPrinterColumns")
                                          if k in v} for v in _ks(full, "spec", "versions", default=[]) or []],
                            **({"additionalPrinterColumns": full["spec"]["additionalPrinterColumns"]}
                               if "additionalPrinterColumns" in (full.get("spec") or {}) else {})}}
        except (RuntimeError, ValueError, AttributeError, TypeError):
            crd = None
        _KUBE_PRINTERS[key] = (time.monotonic(), crd)
    return crd


def kube_list(context, page):
    """A page's objects in a context (worker thread): [object]; a Secret is {metadata, type, keys} only."""
    if page["source"] == "helm":
        return json.loads(kube_run("helm", ["list", "--all-namespaces", "--output", "json",  # (helm 4: every status)
                                            "--kube-context", context]) or "[]")
    if page["source"] == "secrets":
        out = []
        for line in kube_get(context, "secrets", "--all-namespaces", "-o", f"go-template={KUBE_SECRETS_TEMPLATE}"
                             ).splitlines():
            parts = line.split("\t")
            if len(parts) == 5:
                ns, name, kind, created, keys = parts
                out.append({"metadata": {"namespace": ns, "name": name, "creationTimestamp": created},
                            "type": kind, "keys": [k for k in keys.split(",") if k]})
        return out
    args = [page["resource"], "-o", "json"] + (["--all-namespaces"] if page["namespaced"] else [])
    return json.loads(kube_get(context, *args) or "{}").get("items") or []


def kube_metrics_ctx(context, page):
    """What a page's rows are made with (worker thread): now, and the pods' or nodes' CPU and memory if it shows them."""
    ctx = {"now": time.time(), "metrics": None, "node_metrics": None}
    if page is KUBE_PODS:
        ctx["metrics"] = kube_pod_metrics(context)
    elif page["resource"] == "nodes":
        ctx["node_metrics"] = kube_node_metrics(context)
    return ctx


def kube_pod_metrics(context):
    """{(namespace, pod): (cores, bytes)} from the metrics API, the pod's containers summed (as Freelens does); None
    when the cluster has no metrics API."""
    try:
        raw = json.loads(kube_get(context, "--raw", "/apis/metrics.k8s.io/v1beta1/pods") or "{}")
    except (RuntimeError, ValueError):
        return None
    out = {}
    for p in raw.get("items") or []:
        cpu = sum(kube_cpu(_ks(c, "usage", "cpu")) or 0 for c in p.get("containers") or [])
        mem = sum(kube_bytes(_ks(c, "usage", "memory")) or 0 for c in p.get("containers") or [])
        out[(_km(p).get("namespace"), _km(p).get("name"))] = (cpu, mem)
    return out


def kube_node_metrics(context):
    try:
        raw = json.loads(kube_get(context, "--raw", "/apis/metrics.k8s.io/v1beta1/nodes") or "{}")
    except (RuntimeError, ValueError):
        return None
    return {_km(n).get("name"): (kube_cpu(_ks(n, "usage", "cpu")) or 0, kube_bytes(_ks(n, "usage", "memory")) or 0)
            for n in raw.get("items") or []}


def kube_rows(page, items, ctx, columns=None):
    """Each object as a row: (texts, sort keys, {name, namespace}); a column that fails on an odd object is blank.
    columns: the page's on this cluster (kube_page_columns), if not its own."""
    rows = []
    for o in items:
        texts, keys = [], []
        for col in page["columns"] if columns is None else columns:
            try:
                text = col["value"](o, ctx)
                key = col["key"](o, ctx) if col["key"] else str(text or "").lower()
            except (AttributeError, TypeError, ValueError, KeyError):
                text, key = "", ""
            texts.append("" if text is None else str(text))
            keys.append(key)
        rows.append((texts, keys, {"name": _km(o).get("name") or o.get("name", ""),
                                   "namespace": _km(o).get("namespace") or o.get("namespace") or "", "raw": o,
                                   "now": ctx["now"]}))
    return rows


# ---- the whole stack's CPU and memory (Home): what its pods ask for and are held to, what they use, what the nodes give
def _asks(container):
    """A container's requests and limits: (cpu request, cpu limit, memory request, memory limit), None where unset."""
    r = container.get("resources") or {}
    req, lim = r.get("requests") or {}, r.get("limits") or {}
    return (kube_cpu(req.get("cpu")) if req.get("cpu") is not None else None,
            kube_cpu(lim.get("cpu")) if lim.get("cpu") is not None else None,
            kube_bytes(req.get("memory")) if req.get("memory") is not None else None,
            kube_bytes(lim.get("memory")) if lim.get("memory") is not None else None)


def kube_pod_asks(pod):
    """What a pod asks of its node, as the scheduler reckons it: its containers' (and sidecars', init containers that
    keep running) summed, or its largest init container's if that's more, plus its overhead. (cpu request, cpu limit,
    memory request, memory limit): a limit is the sum of those set, as kubectl describe node adds them, and None if no
    container sets one."""
    spec = pod.get("spec") or {}
    init = spec.get("initContainers") or []
    running = list(spec.get("containers") or []) + [c for c in init if c.get("restartPolicy") == "Always"]
    once = [c for c in init if c.get("restartPolicy") != "Always"]
    overhead = _asks({"resources": {"requests": spec.get("overhead") or {}}})
    out = []
    for i in range(4):
        values = [a[i] for a in map(_asks, running)]
        firsts = [a[i] for a in map(_asks, once)]
        if all(v is None for v in values + firsts):
            out.append(None)
            continue
        total = max(sum(v or 0 for v in values), max((v or 0 for v in firsts), default=0))
        out.append(total + (overhead[i] or 0 if i in (0, 2) else 0))
    return tuple(out)


KUBE_SUMMARY_NODES = ("custom-columns=NAME:.metadata.name,CPU:.status.allocatable.cpu,"
                      "MEMORY:.status.allocatable.memory")


def kube_summary_read(context):
    """What Home is made of, read (worker thread): the pods that hold on to their node's CPU and memory (not those
    that finished: Succeeded, Failed), each [namespace, name, node, (asks)]; the nodes, each [name, allocatable cores,
    bytes] (None if they can't be listed); the pods' use from the metrics API ({(namespace, pod): (cores, bytes)}, or
    None without one). Raises RuntimeError if the pods can't be listed."""
    items = json.loads(kube_get(context, "pods", "-o", "json", "--all-namespaces") or "{}").get("items") or []
    pods = [[_km(o).get("namespace") or "", _km(o).get("name") or "", _ks(o, "spec", "nodeName", default="") or "",
             kube_pod_asks(o)] for o in items if _ks(o, "status", "phase") not in ("Succeeded", "Failed")]
    try:  # (names and allocatable only: a big cluster's nodes, as JSON, list every image they hold)
        nodes = []
        for line in kube_get(context, "nodes", "-o", KUBE_SUMMARY_NODES, "--no-headers").splitlines():
            parts = line.split()
            if len(parts) == 3:
                nodes.append([parts[0], kube_cpu(parts[1]), kube_bytes(parts[2])])
    except RuntimeError:
        nodes = None
    return {"pods": pods, "nodes": nodes, "metrics": kube_pod_metrics(context)}


def kube_fmt_cores(n):
    return "N/A" if n is None else f"{n:.2f}"


def _share(n, of):
    return f"{round(100 * n / of)}%" if n is not None and of else ""


def kube_use(title, requests, used, limits, allocatable, memory):
    """One resource's totals as a reply carries them (KubeUse): its numbers, their texts, each as a share of what's
    allocatable."""
    fmt = kube_fmt_bytes if memory else kube_fmt_cores
    alloc = None if allocatable is None else kube_fmt_bytes(allocatable) if memory else \
        wording.say("kube.cores", n=round(allocatable, 2) if allocatable % 1 else int(allocatable))
    return {"title": title, "requests": requests, "used": used, "limits": limits, "allocatable": allocatable,
            "cells": [fmt(requests), fmt(used), "–" if limits is None else fmt(limits), alloc or ""],
            "shares": [_share(requests, allocatable), _share(used, allocatable), _share(limits, allocatable)]}


def kube_tally(read, namespaces=None):
    """read's pods (kube_summary_read) in namespaces (None: every one) added up by namespace and by node:
    {"total": [cpu request, cpu used, cpu limit, memory request, memory used, memory limit, pods], "namespaces":
    {namespace: the same}, "nodes": {node: the same}}. A limit stays None while no pod sets one; used, while there are
    no metrics."""
    metrics = read["metrics"]

    def add(into, ns, name, asks):
        use = metrics.get((ns, name)) if metrics is not None else None
        values = (asks[0], use and use[0], asks[1], asks[2], use and use[1], asks[3])
        for i, v in enumerate(values):
            if v is not None:
                into[i] = (into[i] or 0) + v
            elif i in (1, 4) and metrics is not None and into[i] is None:
                into[i] = 0
        into[6] += 1
    def blank():
        return [0, None if metrics is None else 0, None, 0, None if metrics is None else 0, None, 0]
    total, by_ns, by_node = blank(), {}, {}
    for ns, name, node, asks in read["pods"]:
        if namespaces is not None and ns not in namespaces:
            continue
        add(total, ns, name, asks)
        add(by_ns.setdefault(ns, blank()), ns, name, asks)
        add(by_node.setdefault(node, blank()), ns, name, asks)
    return {"total": total, "namespaces": by_ns, "nodes": by_node}


KUBE_SUMMARY_COLUMNS = ["Pods", "CPU requested", "CPU used", "CPU limits", "Memory requested", "Memory used",
                        "Memory limits"]


def kube_summary(read, namespaces=None):
    """Home's numbers (KubeSummary, its history aside): the pods in namespaces (None: every one), their CPU and memory
    asked for, used and held to, against what every node can give them; and the same by namespace and by node (a
    node's against its own)."""
    tally, nodes = kube_tally(read, namespaces), read["nodes"]
    alloc_cpu = None if not nodes else sum(n[1] or 0 for n in nodes)
    alloc_mem = None if not nodes else sum(n[2] or 0 for n in nodes)

    def cells(t):
        return [str(t[6]), kube_fmt_cores(t[0]), kube_fmt_cores(t[1]), "–" if t[2] is None else kube_fmt_cores(t[2]),
                kube_fmt_bytes(t[3]), kube_fmt_bytes(t[4]), "–" if t[5] is None else kube_fmt_bytes(t[5])]
    t = tally["total"]
    empty = [0, None if read["metrics"] is None else 0, None, 0, None if read["metrics"] is None else 0, None, 0]
    return {
        "cpu": kube_use("CPU", t[0], t[1], t[2], alloc_cpu, False),
        "memory": kube_use("Memory", t[3], t[4], t[5], alloc_mem, True),
        "metrics": read["metrics"] is not None, "pods": t[6],
        "namespace_columns": ["Namespace"] + KUBE_SUMMARY_COLUMNS,
        "namespaces": [{"name": ns, "cells": [ns] + cells(v)} for ns, v in sorted(tally["namespaces"].items())],
        "node_columns": ["Node"] + KUBE_SUMMARY_COLUMNS[:4] + ["CPU allocatable"] + KUBE_SUMMARY_COLUMNS[4:] +
        ["Memory allocatable"],
        "nodes": [{"name": name, "cells": [name] + (c := cells(tally["nodes"].get(name) or empty))[:4] +
                   [kube_fmt_cores(cpu)] + c[4:] + [kube_fmt_bytes(mem)]} for name, cpu, mem in nodes or []]}
