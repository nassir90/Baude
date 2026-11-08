"""The Kubernetes view (KubeView: your clusters' objects, as Freelens lists them; KubeWatch keeps its lists live), with what only it needs. Baude and Record both use it; its window is a host (what it asks of one: input, to focus when it closes)."""
import argparse
import array
import base64
import glob
import hashlib
import html
import itertools
import json
import math
import os
import re
import shutil
import socket
import struct
import weakref
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import queue
import urllib.parse

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402,F401

import lookup  # noqa: E402,F401
from lookup import (  # noqa: E402,F401  (⌘K's engine: what a folder has)
    _km, _ks, kube_contexts, kube_fault, kube_fmt_age,
    kube_get, kube_local, kube_run, kube_seconds, kube_tool, kubeconfig_paths,
)
from widgets import (  # noqa: E402,F401
    Fade, RefreshButton, WorkerPool, ellipsize_dropdown, ensure_icons, find_label, full_time, inside, load_settings,
    paned_grip, pretty_path, save_settings, themed_icon, time_tip, toast, view_bar,
    _scales,
)
from codeview import (  # noqa: E402,F401
    CodeView,
)


# ---------------------------------------------------------- kubernetes --
# ☰ → Kubernetes…: a view of a cluster, laid out like Freelens: its sidebar's categories, each kind's list with
# Freelens' columns, pods with their CPU and memory from the metrics API (every 10 s, as Freelens reads them).
# kubectl does the talking, with your kubeconfig's own auth; it's asked to get things (and to list the API's
# resources), to follow a pod's logs, and to delete a pod only once you've confirmed it; helm only to list releases.
# A Secret's values are never read: only its keys' names.

KUBE_POOL = WorkerPool(3)


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
        return {"title": f"AWS profile {m[1]} isn't set up", "why": m[0], "fix": f"aws configure sso --profile {m[1]}"}
    login = f"aws sso login --profile {profile}" if profile else "aws sso login"

    def first(pattern, last=False):  # (the plugin's line; with last, kubectl's own, which ends what it wrote)
        hits = [l.removeprefix("aws: [ERROR]: ").removeprefix("Unable to connect to the server: ")
                for l in lines if re.search(pattern, l, re.I)]
        return (hits[-1] if last else hits[0]) if hits else None
    if why := first(r"Token has expired and refresh failed|SSO session .*(expired|invalid)|Error loading SSO Token|"
                    r"UnauthorizedSSOTokenError|sso.*expired"):
        return {"title": "AWS sign-in expired", "why": why, "fix": login}
    if why := first(r"Unable to locate credentials|ExpiredToken|security token included in the request is (expired|"
                    r"invalid)"):
        return {"title": "No valid AWS credentials", "why": why, "fix": login}
    if why := first(r"Reauthentication|gcloud auth login|problem refreshing your current auth tokens"):
        return {"title": "Google Cloud sign-in expired", "why": why.split(" (err: ")[-1], "fix": "gcloud auth login"}
    if why := first(r"AADSTS\d+|az login|kubelogin"):
        return {"title": "Azure sign-in expired", "why": why[why.find("AADSTS"):] if "AADSTS" in why else why,
                "fix": "az login"}
    if m := re.search(r"exec: executable (\S+) not found", text):
        return {"title": f"{m[1]} isn't installed",
                "why": f"kubectl signs in to this cluster with {m[1]}, which isn't on Baude's PATH "
                       f"({os.environ.get('PATH', '')})", "fix": None}
    if why := first(r"cannot support interactive mode|standard input is not a terminal"):
        return {"title": "Signing in needs a terminal", "why": why,
                "fix": f"kubectl --context {signin['context']} get namespaces" if signin.get("context") else None}
    if why := first(r"must be logged in to the server|provide credentials|\(Unauthorized\)", last=True):
        return {"title": "The cluster didn't accept your credentials", "why": why,
                "fix": login if signin.get("command") == "aws" else None}
    if why := first(r"\(Forbidden\)|is forbidden:", last=True):
        return {"title": "Not allowed", "why": why.removeprefix("Error from server (Forbidden): "), "fix": None}
    if why := first(r"x509:|certificate", last=True):
        return {"title": "The cluster's certificate isn't trusted", "why": why, "fix": None}
    if why := first(r"was refused|connection refused", last=True):
        here = re.search(r"(127\.0\.0\.1|localhost|\[::1\]|host\.docker\.internal)[:/]", why)  # (a cluster on this machine)
        return {"title": "The cluster isn't running" if here else "Can't reach the cluster", "why": why, "fix": None}
    if why := first(r"no such host|i/o timeout|Unable to connect to the server|timed out|network is unreachable",
                    last=True):
        return {"title": "Can't reach the cluster", "why": why, "fix": None}
    if m := re.search(r"exec: executable (\S+) failed with exit code \d+", text):  # (a plugin that said nothing known)
        return {"title": f"Signing in with {m[1]} failed", "why": next((l for l in lines if "exec:" not in l),
                                                                        lines[-1]), "fix": None}
    return {"title": "kubectl failed", "why": lines[-1] if lines else "", "fix": None}


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


class KubeLogTail:
    """kubectl logs (args), followed: what it writes read on a thread of its own and handed to on_text(tail, text) on
    the main thread in batches, however fast lines come; on_end(tail, exit code) once it exits by itself. stop()
    ends it: kubectl is terminated (killed if it lingers) and reaped, and nothing more is handed on."""
    BATCH_MS = 100

    def __init__(self, args, on_text, on_end):
        self.on_text, self.on_end, self.args = on_text, on_end, args
        self.pending, self.lock, self.scheduled, self.stopped = [], threading.Lock(), False, False
        self.proc = subprocess.Popen([kube_tool("kubectl"), *args], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        threading.Thread(target=self._read, daemon=True, name="baude-kube-logs").start()

    def _read(self):
        import codecs
        decode = codecs.getincrementaldecoder("utf-8")(errors="replace").decode
        fd = self.proc.stdout.fileno()
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                break
            with self.lock:
                self.pending.append(decode(chunk))
                if not self.scheduled:
                    self.scheduled = True
                    GLib.timeout_add(self.BATCH_MS, self._flush)
        self.proc.stdout.close()
        code = self.proc.wait()
        GLib.idle_add(self._ended, code)

    def _flush(self):
        with self.lock:
            text, self.pending, self.scheduled = "".join(self.pending), [], False
        if text and not self.stopped:
            self.on_text(self, text)
        return False

    def _ended(self, code):
        self._flush()  # (what came last, before saying it ended)
        if not self.stopped:
            self.stopped = True
            self.on_end(self, code)
        return False

    def stop(self):
        self.stopped = True
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass
            GLib.timeout_add(3000, lambda: (self.proc.poll() is None and self.proc.kill()) and False)


class KubeWatch:
    """A kind's objects, watched as Freelens watches them (its KubeObjectStore): kubectl lists them and then watches
    from that list's version (`get --watch --output-watch-events -o json`, so nothing falls between the two), and the
    events it writes are handed to on_events(watch, [event]) on the main thread a second's worth at a time, however
    fast they come (Freelens' eventsBuffer, flushed after 1000 ms). on_end(watch, exit code, all kubectl wrote) once
    it exits by itself (the API server ends a watch after a while: it's started again). stop() ends it."""
    BATCH_MS = 1000

    def __init__(self, args, on_events, on_end):
        self.on_events, self.on_end, self.args = on_events, on_end, args
        self.pending, self.lock, self.scheduled, self.stopped, self.seen = [], threading.Lock(), False, False, 0
        if (fault := kube_fault("kubectl")) is not None:  # (BAUDE_KUBE_FAULT: it fails as kubectl would)
            self.proc = None
            GLib.idle_add(self._ended, 1, fault)
            return
        self.proc = subprocess.Popen([kube_tool("kubectl"), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     stdin=subprocess.DEVNULL)
        threading.Thread(target=self._read, daemon=True, name="baude-kube-watch").start()

    def _read(self):
        import codecs
        decode, parse = codecs.getincrementaldecoder("utf-8")(errors="replace").decode, json.JSONDecoder().raw_decode
        fd, buf = self.proc.stdout.fileno(), ""
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                break
            buf += decode(chunk)
            events = []
            while True:  # (kubectl writes each event as indented JSON: one can straddle two reads)
                buf = buf.lstrip()
                try:
                    event, end = parse(buf)
                except ValueError:
                    break
                buf = buf[end:]
                if isinstance(event, dict) and event.get("type"):
                    events.append(event)
            if events:
                with self.lock:
                    self.pending += events
                    if not self.scheduled:
                        self.scheduled = True
                        GLib.timeout_add(self.BATCH_MS, self._flush)
        self.proc.stdout.close()
        said = (self.proc.stderr.read() or b"").decode(errors="replace").strip()
        self.proc.stderr.close()
        code = self.proc.wait()
        GLib.idle_add(self._ended, code, said)

    def _flush(self):
        with self.lock:
            events, self.pending, self.scheduled = self.pending, [], False
        if events and not self.stopped:
            self.seen += len(events)
            self.on_events(self, events)
        return False

    def _ended(self, code, said):
        self._flush()
        if not self.stopped:
            self.stopped = True
            self.on_end(self, code, said)
        return False

    def stop(self):
        self.stopped = True
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass
            GLib.timeout_add(3000, lambda: (self.proc.poll() is None and self.proc.kill()) and False)


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
        return "starting", "not started yet"
    state = cs.get("state") or {}
    restarts = cs.get("restartCount") or 0
    times = f" ({restarts} restart{'s' * (restarts != 1)})" if restarts else ""
    if "running" in state:
        if not cs.get("ready"):
            return "starting", "running, not ready yet" + times
        ended = kube_seconds(_ks(cs, "lastState", "terminated", "finishedAt"), now)
        if ended is not None and ended < KUBE_RESTARTED_RECENTLY_S:
            return "restarted", f"running, ready, restarted {kube_fmt_age(ended)} ago, at {full_time(now - ended)}" + times
        return "ok", "running, ready" + times
    if "terminated" in state:
        t = state["terminated"]
        if not t.get("exitCode") and t.get("reason") in (None, "Completed"):
            return "done", "completed"
        return "bad", f"terminated: {t.get('reason') or 'Error'}" + \
            (f" (exit code {t['exitCode']})" if t.get("exitCode") else "") + times
    reason = (state.get("waiting") or {}).get("reason") or ""
    bad = kube_status_tone(reason) == "bad" or reason.startswith("Invalid")  # (InvalidImageName too)
    return ("bad" if bad else "starting"), (f"waiting: {reason}" if reason else "waiting") + times


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
    return ", ".join(out[:6]) + (f" and {len(out) - 6} more" if len(out) > 6 else "")


def _slice_endpoints(o, c):
    ips = [ip for e in o.get("endpoints") or [] for ip in e.get("addresses") or []]
    return ", ".join(ips[:6]) + (f" and {len(ips) - 6} more" if len(ips) > 6 else "")


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


KUBE_NODES = kube_page("Nodes", "nodes", False, [
    K_NAME, _cpu_col(_node_usage), _mem_col(_node_usage), kube_col("Roles", _node_roles),
    kube_col("Version", lambda o, c: _ks(o, "status", "nodeInfo", "kubeletVersion", default="")),
    kube_col("Internal IP", _node_ip), K_AGE, _status_col(_node_ready, "Conditions")])


KUBE_SECTIONS = [
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
            kube_col("Suspended", lambda o, c: "Yes" if _ks(o, "spec", "suspend") else "No"),
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
            kube_col("Default", lambda o, c: "Yes" if (_km(o).get("annotations") or {}).get(
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
KUBE_SECTION_ICONS = {"Nodes": "baude-kube-nodes-symbolic", "Workloads": "baude-kube-workloads-symbolic",
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


class KubeRow(GObject.Object):
    """A row of the Kubernetes view's table: its cells' texts, what each sorts by, and which object it is."""
    __gtype_name__ = "BaudeKubeRow"

    def __init__(self, texts, keys, obj):
        super().__init__()
        self.texts, self.keys, self.obj = texts, keys, obj


_KUBE_ORDER = {-1: Gtk.Ordering.SMALLER, 0: Gtk.Ordering.EQUAL, 1: Gtk.Ordering.LARGER}


def kube_order(a, b):
    """Two sort keys compared (-1, 0, 1): numbers (and tuples of them) before text, nothing last."""
    def norm(k):
        if k is None or k == "":
            return (2, 0, "")
        if isinstance(k, (bool, int, float)) or isinstance(k, tuple):
            return (0, k, "")
        return (1, 0, str(k))
    x, y = norm(a), norm(b)
    try:
        return (x > y) - (x < y)
    except TypeError:  # (a tuple against a number: by their texts)
        return (str(x) > str(y)) - (str(x) < str(y))


class KubeSquares(Gtk.Widget):
    """A pod's containers in the Pods table, a small square each, as Freelens draws them: solid green, running and
    ready; solid orange, getting ready; green ringed with orange, ready but restarted lately; solid red, crashed or failed;
    grey, done (kube_container_look). Its tooltip names each container and its state."""
    SIZE, GAP = 10, 3
    # GNOME's palette (light, dark): fills, so the brighter shades, not the theme's text colours
    COLORS = {"ok": ("#2ec27e", "#33d17a"), "starting": ("#ff7800", "#ff8c26"), "restarted": ("#ff7800", "#ff8c26"),
              "bad": ("#e01b24", "#f0434a")}

    def __init__(self):
        super().__init__(halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
        self.looks = []

    def set_containers(self, containers):
        """containers: kube_containers' [(name, look, words, init)]."""
        self.looks = [look for _n, look, _w, _i in containers]
        self.set_tooltip_text("\n".join(f"{name}{' (init)' * init}: {words}" for name, _l, words, init in containers)
                              or None)
        self.queue_resize()

    def do_measure(self, orientation, _for_size):
        n = len(self.looks)
        size = n * self.SIZE + max(0, n - 1) * self.GAP if orientation == Gtk.Orientation.HORIZONTAL else self.SIZE
        return size, size, -1, -1

    def do_snapshot(self, snap):
        dark = Adw.StyleManager.get_default().get_dark()
        y = round((self.get_height() - self.SIZE) / 2)
        for i, look in enumerate(self.looks):
            color = Gdk.RGBA()
            if look == "done":  # (grey: the text's colour, faint)
                color = self.get_color()
                color.alpha *= 0.35
            else:
                color.parse(self.COLORS[look][dark])
            rect = Graphene.Rect().init(i * (self.SIZE + self.GAP), y, self.SIZE, self.SIZE)
            box = Gsk.RoundedRect()  # (kept in a variable: init_from_rect on a temporary returns freed memory)
            box.init_from_rect(rect, 2)
            if look == "restarted":  # (ready, so green; ringed with orange: it fell over lately)
                fill = Gdk.RGBA()
                fill.parse(self.COLORS["ok"][dark])
                snap.push_rounded_clip(box)
                snap.append_color(fill, rect)
                snap.pop()
                snap.append_border(box, [1] * 4, [color] * 4)  # (a whole pixel: crisp on any screen)
            else:
                snap.push_rounded_clip(box)
                snap.append_color(color, rect)
                snap.pop()


class KubeView(Gtk.Box):
    """☰ → Kubernetes…: a cluster's objects, as Freelens lays them out (see "kubernetes" above). The top bar: your
    kubeconfig's context, the namespaces shown (any set of them; a pod's namespace, clicked, shows only that one), a
    filter (Ctrl+F), Refresh. The sidebar: Freelens' categories (Nodes only when there's more than one). The table:
    the kind's objects with Freelens' columns; click a column to sort by it, again to reverse; a pod's row ends with
    its logs and a menu to delete it. Selecting a row shows its YAML (a Secret: only its keys' names); a pod's logs
    show under the table, followed. Esc closes the logs, then the YAML, then the view."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False, focusable=True)
        self.add_css_class("baude-frame")  # (the bar and the sidebar: widgets.CSS; the rest the inside, set into it)
        ensure_icons()
        self.win, self.contexts, self.context = win, [], None
        self.ns_shown, self.ns_by_context, self.ns_checks = [], {}, {}  # (none shown: all of them)
        self.page, self.table_page, self.resources, self.namespaces = KUBE_PODS, None, None, []
        self.generation, self.loading, self.timer, self.syncing = 0, False, None, False
        self.store = self.filter = self.selection = self.table = self.table_cols = None
        self.pending_pick = None  # (name, namespace) to select once the page is in (an owner clicked)
        # the page's objects by uid, as last read or watched; how its rows were made (now, metrics); the watch
        self.objects, self.ctx, self.watch, self.watch_fails = {}, {"now": 0, "metrics": None, "node_metrics": None}, None, 0
        self.metrics_note, self.node_count, self.note, self.dialog = "", 0, None, None
        self.found = weakref.WeakKeyDictionary()  # the table's labels and their text (find_label)
        self.log_tail = self.log_pod = None
        self.log_containers, self.log_following = [], True
        bar = view_bar()
        title = Gtk.Label(label="Kubernetes")
        title.add_css_class("heading")
        bar.append(title)
        self.context_dd = ellipsize_dropdown(Gtk.DropDown.new_from_strings([]), 32)
        self.context_dd.set_tooltip_text("Context (from your kubeconfig)")
        self.context_dd.connect("notify::selected", self.on_pick)
        bar.append(self.context_dd)
        # the namespaces shown: a check each, any set of them at once (Freelens' multi-select), or all of them
        self.ns_label = Gtk.Label(label="All namespaces", ellipsize=Pango.EllipsizeMode.END, max_width_chars=24)
        self.ns_all = Gtk.CheckButton(label="All namespaces")
        self.ns_all.connect("toggled", lambda b: self.syncing or self.show_namespaces([]))
        self.ns_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pick = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pick.append(self.ns_all)
        pick.append(Gtk.Separator())
        pick.append(Gtk.ScrolledWindow(child=self.ns_list, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                       propagate_natural_height=True, propagate_natural_width=True,
                                       max_content_height=420))
        self.ns_btn = Gtk.MenuButton(child=self.ns_label, always_show_arrow=True, popover=Gtk.Popover(child=pick),
                                     tooltip_text="Namespaces shown (any of them at once)")
        bar.append(self.ns_btn)
        self.search = Gtk.SearchEntry(placeholder_text="Filter", width_chars=22, tooltip_text="Filter (Ctrl+F)")
        self.search.connect("search-changed", lambda *_: self.refilter())
        self.search.connect("stop-search", lambda e: e.set_text("") if e.get_text() else self.escape())  # (Esc in it)
        bar.append(self.search)
        bar.append(Gtk.Box(hexpand=True))
        self.refresh_btn = RefreshButton(tooltip_text="Read it again")
        self.refresh_btn.connect("clicked", lambda *_: self.load())
        bar.append(self.refresh_btn)
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close (Esc)")
        close.add_css_class("circular")
        close.connect("clicked", lambda *_: self.close())
        bar.append(close)
        self.append(Gtk.WindowHandle(child=bar))  # (dragged, it moves the window; double-clicked, maximizes)
        # the sidebar: Freelens' categories (a category of one kind is just that kind: Nodes, Namespaces, Events)
        self.side = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.side.add_css_class("navigation-sidebar")
        self.side.add_css_class("kube-side")
        self.page_rows, self.head_rows = {}, {}
        # a category folds its kinds away (click its heading), as Freelens' do; which are folded is remembered
        self.folded = set(load_settings().get("kube_folded") or [])
        for section, pages in KUBE_SECTIONS:
            icon = KUBE_SECTION_ICONS.get(section)
            if not (len(pages) == 1 and pages[0]["title"] == section):
                head = Gtk.Box(spacing=8, margin_start=6, margin_end=4)  # (its gap above is the row's: kube-side-head-row)
                if icon:
                    head.append(Gtk.Image(icon_name=icon))
                head.append(Gtk.Label(label=section, xalign=0, hexpand=True))
                head.append(Gtk.Image(icon_name="pan-end-symbolic" if section in self.folded else "pan-down-symbolic"))
                head.add_css_class("kube-side-head")
                row = Gtk.ListBoxRow(child=head, activatable=True, selectable=False)
                row.add_css_class("kube-side-head-row")
                row.section = section
                self.head_rows[section] = row
                self.side.append(row)
                for page in pages:
                    self.add_page_row(page, section=section)
            else:  # (a category of one kind: its row has the category's icon)
                self.add_page_row(pages[0], nested=False, icon=icon)
        self.show_heads()
        self.update_nodes_row()
        self.side.connect("row-selected", self.on_side)
        self.side.connect("row-activated", lambda _b, row: hasattr(row, "section") and not hasattr(row, "page")
                          and self.fold(row.section, row.section not in self.folded))
        side_scroll = Gtk.ScrolledWindow(child=self.side, hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=230)
        # the table, its status line; the selected object's YAML beside it
        self.table_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.fade = Fade(self.table_scroll)  # (another context, kind or namespace: the table fades until it's in)
        self.status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.status.add_css_class("kube-status-line")
        table_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.table_stack = Gtk.Stack(vexpand=True, hexpand=True)  # (the table, or why it can't be read)
        self.table_stack.add_named(self.table_scroll, "table")
        self.table_stack.add_named(self.build_error(), "error")
        table_box.append(self.table_stack)
        table_box.append(self.status)
        self.details = CodeView()
        self.details_fade = Fade(self.details)  # (read again: the YAML shown goes grey until the new one's in)
        self.details_shown = None  # (the object whose YAML is shown, and its version)
        head = Gtk.Box(spacing=6, margin_start=10, margin_end=6, margin_top=6)
        self.details_title = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.details_title.add_css_class("heading")
        head.append(self.details_title)
        hide = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Hide")
        hide.add_css_class("flat")
        hide.connect("clicked", lambda *_: self.hide_details())
        head.append(hide)
        self.details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False, width_request=360)
        self.details_box.append(head)
        self.details_box.append(self.details)
        right = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=table_box,
                                     end_child=self.details_box, resize_start_child=True, resize_end_child=False,
                                     shrink_end_child=False))
        self.right = right
        # a pod's logs under the table and the YAML, the width of both, as Freelens docks them: a log's lines are long
        self.logs_box = self.build_logs()
        self.lower = inside(paned_grip(Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, start_child=right,
                                                 end_child=self.logs_box, resize_start_child=True,
                                                 resize_end_child=False, shrink_end_child=False)))
        body = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=side_scroll,
                                    end_child=self.lower, resize_start_child=False, shrink_start_child=False,
                                    vexpand=True))
        body.add_css_class("main-split")  # (its rule unseen: the inside's edge is the line; still dragged)
        self.append(body)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, _k, _s: (self.escape(), True)[1] if kv == Gdk.KEY_Escape else False)
        self.add_controller(keys)
        # a pod's row: its logs, deleting it (targets: "namespace/name"; a row's buttons hold no pod of their own)
        actions = Gio.SimpleActionGroup()
        for name, fn in (("logs", self.show_logs), ("delete", lambda key: self.ask_delete(key, False)),
                         ("force-delete", lambda key: self.ask_delete(key, True))):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", lambda _a, target, fn=fn: fn(target.get_string()))
            actions.add_action(act)
        self.insert_action_group("kube", actions)

    def build_error(self):
        """What's shown instead of the table when the cluster can't be read: what went wrong (kube_diagnose), the
        command that fixes it (a sign-in, say) to copy, Try again, and all kubectl wrote, folded away."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER,
                      halign=Gtk.Align.CENTER, margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
        box.add_css_class("kube-error")
        icon = Gtk.Image(icon_name="dialog-warning-symbolic", pixel_size=2 * _scales["icon_px"])
        icon.add_css_class("dim-label")
        box.append(icon)
        self.error_title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.error_title.add_css_class("title-3")
        box.append(self.error_title)
        self.error_why = Gtk.Label(wrap=True, selectable=True, justify=Gtk.Justification.CENTER, max_width_chars=72)
        self.error_why.add_css_class("dim-label")
        box.append(self.error_why)
        self.error_fix = Gtk.Label(selectable=True, xalign=0)
        self.error_fix.add_css_class("mono")
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy")
        copy.add_css_class("flat")
        copy.connect("clicked", lambda *_: (self.get_clipboard().set(self.error_fix.get_label()),
                                            toast(self.win, "Copied: run it in a terminal, then Try again")))
        self.error_fix_row = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        self.error_fix_row.add_css_class("kube-error-fix")
        self.error_fix_row.append(self.error_fix)
        self.error_fix_row.append(copy)
        box.append(self.error_fix_row)
        again = Gtk.Button(label="Try again", halign=Gtk.Align.CENTER)
        again.add_css_class("pill")
        again.connect("clicked", lambda *_: self.try_again())
        box.append(again)
        self.error_said = Gtk.Label(selectable=True, xalign=0, yalign=0, wrap=True,
                                    wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.error_said.add_css_class("mono")
        self.error_more = Gtk.Expander(label="kubectl's output", child=Gtk.ScrolledWindow(
            child=self.error_said, propagate_natural_height=True, max_content_height=220,
            hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(self.error_more)
        return Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER)

    def show_error(self, diag, said):
        """The cluster can't be read: why, instead of the table (and a word of it in the status line)."""
        self.error_title.set_label(diag["title"])
        self.error_why.set_label(diag["why"])
        self.error_why.set_visible(bool(diag["why"]))
        self.error_fix.set_label(diag["fix"] or "")
        self.error_fix_row.set_visible(bool(diag["fix"]))
        self.error_said.set_label(said or "")
        self.error_more.set_visible(bool(said) and said.strip() != diag["why"])
        self.table_stack.set_visible_child_name("error")
        self.say(f"{self.context}: {diag['title']}")

    def try_again(self):
        if self.resources is None:  # (it never connected: from the start)
            self.switch_context(self.context, self.ns_shown)
        else:
            self.load(switch=True)

    def build_logs(self):
        """The logs pane: the pod, its containers to pick from (init containers marked), the previous run's, follow,
        wrap, find, close; the log itself (CodeView: Ctrl+F finds in it)."""
        self.logs = CodeView()
        head = Gtk.Box(spacing=6, margin_start=10, margin_end=6, margin_top=4, margin_bottom=2)
        head.append(Gtk.Image.new_from_icon_name("baude-logs-symbolic"))  # (the row's button: these are its logs)
        self.logs_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.logs_title.add_css_class("heading")
        head.append(self.logs_title)
        self.logs_state = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.logs_state.add_css_class("dim-label")
        self.logs_state.add_css_class("caption")
        head.append(self.logs_state)
        self.logs_container = ellipsize_dropdown(Gtk.DropDown.new_from_strings([]), 28)
        self.logs_container.set_tooltip_text("Container")
        self.logs_container.connect("notify::selected", lambda *_: self.syncing or self.tail_logs())
        head.append(self.logs_container)
        self.logs_previous = Gtk.ToggleButton(label="Previous",
                                              tooltip_text="Its previous run's log (one that crashed, say), instead of "
                                                           "this one's")
        self.logs_previous.connect("toggled", lambda *_: self.syncing or self.tail_logs())
        self.logs_follow = Gtk.ToggleButton(icon_name="go-bottom-symbolic", active=True,
                                            tooltip_text="Follow: keep to the newest line (scrolling up stops it, "
                                                         "scrolling to the end again goes on)")
        self.logs_follow.connect("toggled", lambda b: self.syncing or self.follow_logs(b.get_active()))
        wrap = Gtk.ToggleButton(icon_name=themed_icon("text-wrap-symbolic", "format-justify-fill-symbolic"),
                                tooltip_text="Wrap long lines", active=bool(load_settings().get("kube_log_wrap")))
        wrap.connect("toggled", lambda b: (self.logs.set_wrap(b.get_active()),
                                           save_settings(kube_log_wrap=b.get_active())))
        self.logs.set_wrap(wrap.get_active())
        find = Gtk.Button(icon_name="edit-find-symbolic", tooltip_text="Find (Ctrl+F)")
        find.connect("clicked", lambda *_: self.logs.open_search())
        hide = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close the logs (stops following them)")
        hide.connect("clicked", lambda *_: self.hide_logs())
        for b in (self.logs_previous, self.logs_follow, wrap, find, hide):
            b.add_css_class("flat")
            head.append(b)
        # following stops when you scroll up (the wheel, the scrollbar, the keys), not whenever the view moves: a
        # text view moves itself as it measures lines it had guessed at, and that mustn't stop it
        adj = self.logs.scroll.get_vadjustment()
        adj.connect("changed", self._logs_moved)
        adj.connect("value-changed", self._logs_moved)
        self.log_wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        self.log_wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.log_wheel.connect("scroll", lambda _c, _dx, dy: (dy < 0 and self.follow_logs(False), False)[1])
        self.logs.scroll.add_controller(self.log_wheel)
        self.log_dragging = False
        drag = Gtk.GestureDrag(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", lambda *_: setattr(self, "log_dragging", True))
        drag.connect("drag-end", lambda *_: setattr(self, "log_dragging", False))
        self.logs.scroll.get_vscrollbar().add_controller(drag)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        up = {Gdk.KEY_Up, Gdk.KEY_Page_Up, Gdk.KEY_Home, Gdk.KEY_KP_Up, Gdk.KEY_KP_Page_Up, Gdk.KEY_KP_Home}
        keys.connect("key-pressed", lambda _c, kv, _k, _s: (kv in up and self.follow_logs(False), False)[1])
        self.logs.view.add_controller(keys)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False, height_request=140)  # (the pane's rule over it)
        box.append(head)
        box.append(self.logs)
        return box

    def add_page_row(self, page, nested=True, after=None, icon=None, section=None):
        label = Gtk.Label(label=page["title"], xalign=0, ellipsize=Pango.EllipsizeMode.END)
        # (a kind under a category lines up with the category's name, past its icon)
        child = Gtk.Box(spacing=8, margin_start=30 if nested else 6)
        if icon:
            child.append(Gtk.Image(icon_name=icon))
        child.append(label)
        row = Gtk.ListBoxRow(child=child)
        row.page = page
        row.section = section if section is not None else getattr(after, "section", None)
        row.served = not page.get("known")  # (a known custom resource's: not listed until the cluster serves it)
        if after is None:
            self.side.append(row)
        else:
            self.side.insert(row, after.get_index() + 1)
        self.page_rows[id(page)] = row
        row.set_visible(row.served and row.section not in self.folded)
        return row

    def show_heads(self):
        """A category's heading only while the cluster serves one of its kinds (KEDA's, on a cluster with KEDA)."""
        for section, head in self.head_rows.items():
            head.set_visible(any(r.served for r in self.page_rows.values() if r.section == section))

    def fold(self, section, folded):
        """Fold a category's kinds away (folded) or show them again; remembered (settings: "kube_folded")."""
        (self.folded.add if folded else self.folded.discard)(section)
        save_settings(kube_folded=sorted(self.folded))
        self.head_rows[section].get_child().get_last_child().set_from_icon_name(
            "pan-end-symbolic" if folded else "pan-down-symbolic")
        for row in self.page_rows.values():
            if row.section == section:
                row.set_visible(not folded and row.served)

    # ---- opening and closing
    def open(self, context=None, page=None, search=None, namespace=None):
        """Show the view: on context (else the one used last, else your kubeconfig's current one), at page (Pods by
        default), filtered by search, in namespace (else the namespaces shown in that context last)."""
        self.contexts = [c for c in kube_contexts() if not c.get("cluster_only")]
        names = [c["name"] for c in self.contexts]
        self.set_visible(True)
        self.grab_focus()
        if not names:
            where = ", ".join(pretty_path(p) for p in kubeconfig_paths()) or "~/.kube/config (there is none)"
            self.say(f"No kubeconfig context to show: {where}.")
            return
        last = load_settings().get("kube_context")
        current = next((c["name"] for c in self.contexts if c.get("current")), None)
        want = context if context in names else last if last in names else current or names[0]
        self.syncing = True  # (no "(current)" after one: Freelens doesn't, and the one it opens on says enough)
        self.context_dd.set_model(Gtk.StringList.new(names))
        self.context_dd.set_selected(names.index(want))
        self.syncing = False
        if search is not None:
            self.search.set_text(search)
        if page is not None:
            self.page = page
        self.select_page_row(self.page)
        self.switch_context(want, namespaces=[namespace] if namespace else None)
        if self.timer is None:
            self.timer = GLib.timeout_add_seconds(KUBE_REFRESH_S, self.tick)

    def close(self):
        self.set_visible(False)
        self.hide_logs()
        self.stop_watch()
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        self.win.input.grab_focus()

    def escape(self):
        if self.logs_box.get_visible():
            self.hide_logs()
        elif self.details_box.get_visible():
            self.hide_details()
        else:
            self.close()

    def find(self):
        """Ctrl+F while the view is open: its filter, focused, its text selected to type over. The window's Ctrl+F asks
        here first, as the view covers the window (so not the find bar of a Code page under it); in the YAML, the
        YAML's own Ctrl+F finds in it. False if the view is closed."""
        if not self.get_visible():
            return False
        self.search.grab_focus()
        self.search.select_region(0, -1)
        return True

    def tick(self):  # (the pods' CPU and memory read again while the view is open; the list too, if it isn't watched)
        if self.get_visible() and not self.loading and self.context:
            if self.watch is not None:
                self.refresh_metrics()
            else:
                self.load(quiet=True)
        return True

    def say(self, text):
        self.status.set_label(text)

    # ---- picking a context, namespaces, a kind
    def on_pick(self, dd, _pspec):
        if self.syncing:
            return
        GLib.idle_add(lambda: self.picked(dd) and False)  # (not from inside the drop-down's own signal)

    def picked(self, dd):
        if dd is self.context_dd and self.contexts:
            self.switch_context(self.contexts[dd.get_selected()]["name"])

    def switch_context(self, name, namespaces=None):
        """Read a context's namespaces and the resources it serves, then the page; the table fades meanwhile (the
        other cluster's object isn't shown beside it). It shows namespaces, else those it showed last time."""
        self.context, self.resources, self.namespaces, self.note = name, None, [], None
        self.ns_shown = list(namespaces) if namespaces is not None else list(self.ns_by_context.get(name, []))
        self.ns_by_context[name] = list(self.ns_shown)
        self.fill_namespaces()
        save_settings(kube_context=name)
        self.say(f"Connecting to {name}…")
        self.hide_details()
        self.hide_logs()  # (another cluster's: not its pod's)
        self.stop_watch()  # (the other cluster's)
        self.fade.hold()
        self.loading = True  # (no refresh meanwhile: it would be read before the context's resources)
        gen = self.generation = self.generation + 1

        def work():
            try:
                resources = kube_api_resources(name)
            except RuntimeError as e:
                said = getattr(e, "said", str(e))
                GLib.idle_add(self._failed, gen, kube_diagnose(said, kube_signin(name)), said)
                return
            try:
                spaces = sorted(_km(n).get("name", "") for n in json.loads(kube_get(name, "namespaces", "-o", "json")
                                                                           or "{}").get("items") or [])
            except (RuntimeError, ValueError):
                spaces = []
            try:  # (names only: a big cluster's nodes, as JSON, list every image they hold)
                nodes = len(kube_get(name, "nodes", "-o", "name").split())
            except RuntimeError:  # (not allowed to list them: no Nodes page to show either)
                nodes = 0
            GLib.idle_add(self._connected, gen, name, resources, spaces, nodes)
        KUBE_POOL.submit(work)

    def _failed(self, gen, diag, said=""):
        if gen == self.generation:
            self.loading = False
            self.refresh_btn.set_loading(False)
            self.stop_watch()
            self.show_error(diag, said)
            if self.store is not None:
                self.store.remove_all()
            self.fade.done()
        return False

    def _connected(self, gen, name, resources, spaces, nodes):
        """A cluster's resources, namespaces and nodes, as switch_context read them. Kept whatever was picked while they
        came (a kind, a namespace: each a newer load), so long as it's still this cluster: else the sidebar listed the
        other cluster's custom resources."""
        if name != self.context:
            return False
        self.resources, self.namespaces, self.node_count = resources, [s for s in spaces if s], nodes
        self.fill_namespaces()
        for row in self.page_rows.values():
            api = row.page["api"]
            served = not api or api in resources or row.page["resource"] in resources
            if row.page.get("known"):  # (KEDA's, cert-manager's…: listed only on a cluster that has them)
                row.served = served
                row.set_visible(served and row.section not in self.folded)
            else:  # (kinds this cluster doesn't serve: dimmed)
                row.set_sensitive(served)
                row.set_tooltip_text(None if served else f"{self.context} doesn't serve {row.page['title']}")
        self.show_heads()
        unserved = not getattr(self.page_rows.get(id(self.page)), "served", True)
        if unserved:
            self.page = KUBE_PODS  # (scaled objects shown, on a cluster without KEDA: its pods instead)
            self.select_page_row(KUBE_PODS)
        self.update_nodes_row()
        if unserved or gen == self.generation:  # (a page picked meanwhile is being read already)
            self.load()
        return False

    def update_nodes_row(self):
        """Nodes, in the sidebar, and the Node column, only for a cluster of more than one node: a local cluster's
        one node is no news, and every pod would say the same. The page stays while it's the one shown (opened from
        a link, say)."""
        self.page_rows[id(KUBE_NODES)].set_visible(self.node_count > 1 or self.page is KUBE_NODES)
        for c in getattr(self, "columns", None) or []:
            if c.get_title() == "Node":
                c.set_visible(self.node_count > 1)

    def on_side(self, _box, row):
        if row is None or self.syncing or not hasattr(row, "page"):
            return
        if row.page is not self.page:
            self.page = row.page
            self.hide_details()
            self.update_nodes_row()
            if self.context:
                self.load(switch=True)

    def select_page_row(self, page):
        row = self.page_rows.get(id(page))
        self.update_nodes_row()
        if row is not None:
            self.syncing = True
            self.side.select_row(row)
            self.syncing = False

    def fill_namespaces(self):
        """The namespace list: the cluster's namespaces, and any shown that it didn't list (one opened from a link),
        each a check; those shown checked, and named on the button."""
        child = self.ns_list.get_first_child()
        while child is not None:
            self.ns_list.remove(child)
            child = self.ns_list.get_first_child()
        self.ns_checks = {}
        for ns in sorted(set(self.namespaces) | set(self.ns_shown)):
            check = self.ns_checks[ns] = Gtk.CheckButton(label=ns)
            check.ns = ns
            check.connect("toggled", lambda b: self.syncing or self.show_namespaces(
                [n for n in self.ns_shown if n != b.ns] + [b.ns] * b.get_active()))
            self.ns_list.append(check)
        self.sync_namespaces()

    def sync_namespaces(self):
        was, self.syncing = self.syncing, True
        self.ns_all.set_active(not self.ns_shown)
        for ns, check in self.ns_checks.items():
            check.set_active(ns in self.ns_shown)
        self.syncing = was
        self.ns_label.set_label(self.ns_summary())
        self.ns_btn.set_tooltip_text(", ".join(self.ns_shown) if len(self.ns_shown) > 2 else
                                     "Namespaces shown (any of them at once)")

    def ns_summary(self):
        """The namespaces shown, in a few words: All namespaces; shop; shop, jobs; 3 namespaces."""
        n = self.ns_shown
        return "All namespaces" if not n else ", ".join(n) if len(n) <= 2 else f"{len(n)} namespaces"

    def show_namespaces(self, names):
        """Show these namespaces' objects (in the order picked), or every namespace's ([]): picked from the list, a
        pod's namespace clicked. The list is every namespace's already: only the table fades, to be filtered."""
        self.ns_shown = list(dict.fromkeys(names))
        if self.context:
            self.ns_by_context[self.context] = list(self.ns_shown)
        if any(ns not in self.ns_checks for ns in self.ns_shown):
            self.fill_namespaces()
        else:
            self.sync_namespaces()
        self.fade.swap(self.refilter)

    def set_namespace(self, ns):
        """Show only ns's objects (a pod's namespace clicked, a namespace opened); None: every namespace's."""
        if self.page["namespaced"]:
            self.show_namespaces([ns] if ns else [])
            return
        self.ns_shown = [ns] if ns else []  # (from the Namespaces page: its pods)
        if self.context:
            self.ns_by_context[self.context] = list(self.ns_shown)
        self.fill_namespaces()
        self.page = KUBE_PODS
        self.select_page_row(KUBE_PODS)
        self.load(switch=True)

    def go_to_owner(self, kind, name, ns):
        """Show what controls an object (a pod's Controlled By, clicked): a ReplicaSet's Deployment, a Job's CronJob,
        when it has one; its page, with it selected (its YAML beside the table)."""
        context = self.context
        self.say(f"Finding {kind}/{name}…")

        def work():
            k, n = kind, name
            for _ in range(3):  # (ReplicaSet → Deployment; Job → CronJob)
                if k not in KUBE_CLIMB:
                    break
                try:
                    up = kube_controller(json.loads(kube_get(context, KUBE_CLIMB[k], n, "-n", ns, "-o", "json")))
                except (RuntimeError, ValueError):
                    break
                if not up or not up.get("kind") or not up.get("name"):
                    break
                k, n = up["kind"], up["name"]
            GLib.idle_add(self._go_to, context, k, n, ns)
        KUBE_POOL.submit(work)

    def _go_to(self, context, kind, name, ns):
        if context != self.context:
            return False
        resource = KUBE_CLIMB.get(kind) or kube_resource(kind)
        row = next((r for r in self.page_rows.values() if r.page["resource"].split(".")[0] == resource and r.served),
                   None)
        if row is None:
            self.update_status()
            self.set_note(f"{kind}/{name}: no page here for {kind}")
            return False
        if ns and row.page["namespaced"] and self.ns_shown and ns not in self.ns_shown:
            self.show_namespaces(self.ns_shown + [ns])
        self.search.set_text("")
        self.pending_pick = (name, ns if row.page["namespaced"] else "")
        self.page = row.page
        self.select_page_row(row.page)
        self.hide_details()
        self.load(switch=True)
        return False

    # ---- reading a page
    def load(self, quiet=False, switch=False):
        """Read the page's objects (and, for pods and nodes, their CPU and memory) in the background. switch: it's
        another kind, so the table fades until they're in (a refresh doesn't)."""
        if not self.context:
            return
        self.stop_watch()  # (read again: watched again from what's read)
        if switch:
            self.fade.hold()
        page, context = self.page, self.context
        gen = self.generation = self.generation + 1
        self.loading = True
        if not quiet:
            self.say(f"Reading {page['title'].lower()} on {context}…")
            self.refresh_btn.set_loading(True)

        def work():
            # (a custom resource's definition, read alongside the list: two kubectl round trips at once, not after)
            crd = threading.Thread(target=kube_page_crd, args=(context, page), daemon=True) if page.get("crd") else None
            if crd is not None:
                crd.start()
            try:
                items = kube_list(context, page)
            except (RuntimeError, ValueError) as e:
                said = getattr(e, "said", str(e))
                GLib.idle_add(self._failed, gen, kube_diagnose(said, kube_signin(context)), said)
                return
            if crd is not None:
                crd.join()
            columns = kube_page_columns(context, page, items=items)  # (its definition's, on this cluster)
            ctx = kube_metrics_ctx(context, page)
            GLib.idle_add(self._loaded, gen, page, columns, kube_rows(page, items, ctx, columns), ctx, items)
        KUBE_POOL.submit(work)

    # ---- kept up to date, as Freelens keeps its lists: one read, then the changes as they happen
    def watch_page(self, gen, page):
        """Watch the page's kind (not Secrets: their values would come with them; nor Helm's releases): each change
        lands in the table within a second of its happening. The list is read every KUBE_REFRESH_S only if the
        cluster can't be watched."""
        if page["source"] != "kubectl" or gen != self.generation or not self.get_visible():
            return
        args = ["--context", self.context, "get", page["resource"]] + (["--all-namespaces"] if page["namespaced"]
                                                                        else [])
        self.watch = KubeWatch(args + ["--watch", "--output-watch-events", "-o", "json"],
                               lambda w, events: self._watched(w, gen, page, events),
                               lambda w, code, said: self._watch_ended(w, gen, page, code, said))

    def stop_watch(self):
        if self.watch is not None:
            self.watch.stop()
            self.watch = None

    def _watched(self, w, gen, page, events):
        """Changes, a second's worth: each object put in, replaced or taken out by its uid; one that's the same version
        as the one shown is skipped (the watch starts by listing what's there), as Freelens' updateFromEventsBuffer."""
        if w is not self.watch or gen != self.generation:
            return
        changed = False
        for event in events:
            o, kind = event.get("object") or {}, event.get("type")
            if kind not in ("ADDED", "MODIFIED", "DELETED") or not isinstance(o, dict):
                continue
            key = kube_uid(o)
            have = self.objects.get(key)
            if kind == "DELETED":
                changed |= self.objects.pop(key, None) is not None
            elif have is None or (_km(o).get("resourceVersion") != _km(have).get("resourceVersion")
                                  if _km(o).get("resourceVersion") else o != have):
                self.objects[key], changed = o, True
        self.watch_fails = 0
        if changed:
            self.show_rows(page, kube_rows(page, list(self.objects.values()), dict(self.ctx, now=time.time()),
                                           self.table_cols))
            self.update_status()

    def _watch_ended(self, w, gen, page, code, said):
        """The watch ended (the API server ends them after a while; or it failed): watched again after a second, or
        after 5 s if it failed (Freelens' retry); after three failures in a row, read every KUBE_REFRESH_S instead."""
        if w is not self.watch:
            return
        self.watch = None
        if gen != self.generation or not self.get_visible():
            return
        if code and not w.seen:
            self.watch_fails += 1
            if self.watch_fails >= 3:
                why = kube_diagnose(said)["title"].lower() if said else f"kubectl exited with {code}"
                self.set_note(f"can't watch {page['title'].lower()} ({why}): "
                              f"read every {KUBE_REFRESH_S} s instead")
                return
        GLib.timeout_add(5000 if code and not w.seen else 1000,
                         lambda: self.watch is None and self.watch_page(gen, page) and False)

    def refresh_metrics(self):
        """While the list is watched: only the pods' or nodes' CPU and memory read again (and the ages moved on)."""
        page, context, gen = self.table_page, self.context, self.generation

        def work():
            ctx = kube_metrics_ctx(context, page)
            GLib.idle_add(lambda: gen == self.generation and self.watch is not None and
                          (setattr(self, "ctx", ctx), self.show_rows(page, kube_rows(page, list(self.objects.values()),
                                                                                       ctx, self.table_cols)),
                           self.update_status())
                          and False)
        KUBE_POOL.submit(work)

    def show_rows(self, page, rows):
        """The table's rows replaced, the object selected before selected again (or the one an owner link asked for)."""
        picked, owner = self.pending_pick or self.selected_obj(), self.pending_pick
        self.pending_pick = None
        self.store.splice(0, self.store.get_n_items(), [KubeRow(t, k, o) for t, k, o in rows])
        if picked is not None:  # (the same object, selected again after a refresh; or the owner clicked)
            for i in range(self.selection.get_n_items()):
                o = self.selection.get_item(i).obj
                if (o["name"], o["namespace"]) == picked:
                    self.selection.set_selected(i)
                    if owner:
                        self.table.scroll_to(i, None, Gtk.ListScrollFlags.NONE, None)
                    break
            else:
                if owner:
                    self.set_note(f"{owner[0]} isn't in {page['title'].lower()} on {self.context}")

    def _loaded(self, gen, page, columns, rows, ctx, items=()):
        if gen != self.generation:
            return False
        self.loading = False
        self.refresh_btn.set_loading(False)
        self.table_stack.set_visible_child_name("table")
        if self.table_page is not page or self.table_cols is not columns:  # (or another cluster's printer columns)
            self.build_table(page, columns)
        self.objects, self.ctx, self.watch_fails = {kube_uid(o): o for o in items}, ctx, 0
        self.show_rows(page, rows)
        self.watch_page(gen, page)
        if page is KUBE_PODS or page["resource"] == "nodes":
            got = ctx["metrics"] if page is KUBE_PODS else ctx["node_metrics"]
            self.metrics_note = f"CPU and memory from the metrics API, every {KUBE_REFRESH_S} s" if got is not None \
                else "no metrics API on this cluster (CPU and memory N/A)"
        else:
            self.metrics_note = ""
        self.update_status()
        self.fade.done()
        return False

    def build_table(self, page, cols):
        """The table for a page, with these columns (its own; a custom resource's, as the cluster declares them)."""
        self.store = Gio.ListStore(item_type=KubeRow)
        self.filter = Gtk.CustomFilter.new(self.row_visible, None)
        cv = Gtk.ColumnView(show_row_separators=False, show_column_separators=False)
        cv.add_css_class("data-table")
        cv.add_css_class("kube-table")
        columns = [self.make_column(i, col) for i, col in enumerate(cols)]
        for c in columns:
            cv.append_column(c)
        if page is KUBE_PODS:
            cv.append_column(self.make_pod_buttons())
            header = cv.get_first_child()  # (its heading has nothing to say or sort by: not a button to press)
            last = header.get_last_child() if header is not None and header.get_css_name() == "header" else None
            if last is not None:
                last.set_can_target(False)
        sorted_model = Gtk.SortListModel(model=Gtk.FilterListModel(model=self.store, filter=self.filter),
                                         sorter=cv.get_sorter())
        self.selection = Gtk.SingleSelection(model=sorted_model, autoselect=False, can_unselect=True)
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self.selection.connect("notify::selected-item", self.on_select)
        cv.set_model(self.selection)
        cv.connect("activate", self.on_activate)
        first = next((c for c, col in zip(columns, cols) if col["kind"] == "name"), columns[0])
        if page["resource"] == "events":  # (the latest first)
            first = next(c for c in columns if c.get_title() == "Last Seen")
        cv.sort_by_column(first, Gtk.SortType.ASCENDING)
        self.table, self.table_page, self.columns, self.table_cols = cv, page, columns, cols
        self.table_scroll.set_child(cv)
        self.update_nodes_row()

    def make_column(self, i, col):
        tones = ("kube-ok", "kube-warn", "kube-bad")
        f = Gtk.SignalListItemFactory()

        def setup(_f, item):
            if col["kind"] == "namespace":  # (a namespace is a link: only its objects)
                btn = Gtk.Button(halign=Gtk.Align.START, child=Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))
                btn.add_css_class("flat")
                btn.add_css_class("kube-link")
                btn.ns = None
                btn.connect("clicked", lambda b: b.ns and self.set_namespace(b.ns))
                item.set_child(btn)
            elif col["kind"] == "owner":  # (an owner is a link: it, or what controls it in turn)
                btn = Gtk.Button(halign=Gtk.Align.START, child=Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))
                btn.add_css_class("flat")
                btn.add_css_class("kube-link")
                btn.owner = btn.ns = None
                btn.connect("clicked", lambda b: b.owner and self.go_to_owner(*b.owner))
                item.set_child(btn)
            elif col["kind"] == "containers":
                item.set_child(KubeSquares())
            else:
                item.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                         max_width_chars=80 if col["kind"] == "name" else 48))

        def bind(_f, item):
            text, w = item.get_item().texts[i], item.get_child()
            if col["kind"] == "owner":
                obj = item.get_item().obj
                ref = kube_controller(obj["raw"]) or {}
                self.find_label(w.get_child(), text)
                w.owner = (ref["kind"], ref["name"], obj["namespace"]) if ref.get("kind") and ref.get("name") else None
                return
            if isinstance(w, Gtk.Button):
                self.find_label(w.get_child(), text)
                w.ns = text
                w.set_tooltip_text(f"Only {text}'s" if text else None)
                return
            if isinstance(w, KubeSquares):
                w.set_containers(kube_containers(item.get_item().obj["raw"], time.time()))
                return
            self.find_label(w, text)
            w.set_tooltip_text(text if len(text) > (30 if col["kind"] == "name" else 40) else None)
            if col["kind"] == "age":  # (what it sorts by: how old, when the row was made)
                age = item.get_item().keys[i]
                time_tip(w, item.get_item().obj["now"] - age if isinstance(age, (int, float)) else None)
            if col["kind"] == "status":
                for t in tones:
                    w.remove_css_class(t)
                if tone := (col["tone"] or kube_status_tone)(text.split(",")[0].strip()):
                    w.add_css_class(f"kube-{tone}")
        f.connect("setup", setup)
        f.connect("bind", bind)
        c = Gtk.ColumnViewColumn(title=col["title"], factory=f, resizable=True,
                                 expand=col["kind"] == "name" or col["title"] in ("Message", "Rules"))
        # (fixed: the cells that matter most aren't squeezed, and the table scrolls sideways. A name and a namespace
        # no wider than this: the Pods table's Restarts is still in sight beside the YAML in a laptop's window)
        if width := {"name": 220, "namespace": 120}.get(col["kind"]) or {"Message": 420, "Rules": 320,
                                                                        "Involved Object": 260}.get(col["title"]):
            c.set_fixed_width(width)
        c.set_sorter(Gtk.CustomSorter.new(lambda a, b, _d: _KUBE_ORDER[kube_order(a.keys[i], b.keys[i])], None))
        return c

    def make_pod_buttons(self):
        """The Pods table's last column, as Freelens ends a pod's row: its logs (four lines of text), and ⋮, a menu to
        delete it or force-delete it. Each is the view's action with the pod as its target ("namespace/name")."""
        f = Gtk.SignalListItemFactory()

        def setup(_f, item):
            box = Gtk.Box(spacing=2, halign=Gtk.Align.END)
            logs = Gtk.Button(icon_name="baude-logs-symbolic", tooltip_text="Logs", action_name="kube.logs")
            more = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="Delete…")
            for b in (logs, more):
                b.add_css_class("flat")
                b.add_css_class("kube-row-btn")
                box.append(b)
            item.set_child(box)

        def bind(_f, item):
            obj, box = item.get_item().obj, item.get_child()
            key = GLib.Variant("s", f"{obj['namespace']}/{obj['name']}")
            logs, more = box.get_first_child(), box.get_last_child()
            logs.set_action_target_value(key)
            menu = Gio.Menu()
            for label, action in (("Delete", "kube.delete"), ("Force delete", "kube.force-delete")):
                entry = Gio.MenuItem.new(label, None)
                entry.set_action_and_target_value(action, key)
                menu.append_item(entry)
            more.set_menu_model(menu)
        f.connect("setup", setup)
        f.connect("bind", bind)
        return Gtk.ColumnViewColumn(title="", factory=f, resizable=False)

    def row_visible(self, row, _data=None):
        if self.ns_shown and self.table_page is not None and self.table_page["namespaced"] and \
                row.obj["namespace"] not in self.ns_shown:
            return False
        text = self.search.get_text().strip().lower()
        return not text or any(text in t.lower() for t in row.texts)

    def refilter(self):
        if self.filter is not None:
            self.filter.changed(Gtk.FilterChange.DIFFERENT)
        q = self.search.get_text().strip()
        for label, text in list(self.found.items()):  # (the cells already shown: what's typed highlighted in them)
            find_label(label, text, q)
        self.update_status()

    def find_label(self, label, text):
        """A cell's text, with the filter's text highlighted in it; kept, to highlight again as the filter changes."""
        self.found[label] = text
        find_label(label, text, self.search.get_text().strip())

    def update_status(self):
        if self.selection is None:
            return
        n, total = self.selection.get_n_items(), self.store.get_n_items()
        what = self.table_page["title"].lower()
        bits = [self.note and self.note[1] > time.monotonic() and self.note[0],
                f"{n} {what}" + (f" of {total}" if n != total else "") +
                (f" in {self.ns_summary()}" if self.ns_shown and self.table_page["namespaced"] else ""),
                self.context, self.watch is not None and "live", self.metrics_note,
                "read-only but for the deletes you confirm"]
        self.say(" · ".join(b for b in bits if b))

    # ---- an object: its YAML; a namespace or a custom resource's definition, opened
    def selected_obj(self):
        item = self.selection.get_selected_item() if self.selection is not None else None
        return (item.obj["name"], item.obj["namespace"]) if item is not None else None

    def on_select(self, sel, _pspec):
        item = sel.get_selected_item()
        if item is None:
            return
        page, obj, context = self.table_page, item.obj, self.context
        kind = page["title"][:-1] if page["title"].endswith("s") else page["title"]
        self.details_title.set_label(f"{kind} {obj['name']}" + (f" · {obj['namespace']}" if obj["namespace"] else ""))
        self.details_box.set_visible(True)
        if self.right.get_position() < 200 or self.right.get_position() > self.right.get_width() - 200:
            # (the table keeps two thirds: the Pods table's Restarts stays in sight in a 1280-wide window)
            self.right.set_position(max(320, int(self.right.get_width() * 0.65)))
        if page["source"] in ("secrets", "helm"):
            self.details_shown = None  # (shown from what's listed: nothing read)
        if page["source"] == "secrets":  # (only its keys' names: its values are never read)
            keys = obj["raw"].get("keys") or []
            self.details.load(f"# Secret {obj['namespace']}/{obj['name']} ({obj['raw'].get('type', '')})\n"
                              f"# Its values aren't shown.\nkeys:\n" + "".join(f"  - {k}\n" for k in keys), "secret.yaml")
            return
        if page["source"] == "helm":
            self.details.load(json.dumps(obj["raw"], indent=2), "release.json")
            return
        shown = ((context, page["resource"], obj["name"], obj["namespace"]), _km(obj["raw"]).get("resourceVersion"))
        if shown == self.details_shown:  # (the table read again, this object no different: its YAML stays)
            return
        again = self.details_shown is not None and self.details_shown[0] == shown[0]
        self.details_shown = shown
        self.details_fade.hold()  # (what's shown goes grey until the new YAML is in: no flash of nothing between)
        args = [page["resource"], obj["name"], "-o", "yaml"] + (["-n", obj["namespace"]] if obj["namespace"] else [])

        def work():
            try:
                text = kube_get(context, *args)
            except RuntimeError as e:
                d = kube_diagnose(getattr(e, "said", str(e)))
                text = f"# {d['title']}: {d['why']}\n"
            GLib.idle_add(self._details_read, shown, text, again)
        KUBE_POOL.submit(work)

    def _details_read(self, shown, text, again):
        """An object's YAML is in: shown (where it was scrolled to, if it's the same object read again), with the
        names of other objects in it as links."""
        if shown != self.details_shown:
            return False
        adj = self.details.scroll.get_vadjustment()
        at = adj.get_value() if again else 0
        self.details.load(text, "object.yaml")
        ns = shown[0][3] or None
        self.details.set_links(kube_yaml_refs(text, ns), self.open_ref)
        if at:
            GLib.idle_add(lambda: adj.set_value(min(at, adj.get_upper() - adj.get_page_size())) and False)
        self.details_fade.done()
        return False

    def open_ref(self, target):
        """A name in an object's YAML, clicked: that object (its page, it selected); a namespace, its objects."""
        kind, name, ns = target
        if kind == "Namespace":
            self.set_namespace(name)
        else:
            self._go_to(self.context, kind, name, ns)

    def hide_details(self):
        self.details_box.set_visible(False)
        self.details_shown = None
        if self.selection is not None:
            self.selection.set_selected(Gtk.INVALID_LIST_POSITION)

    def on_activate(self, _cv, position):
        row = self.selection.get_item(position)
        if row is None:
            return
        if self.table_page["resource"] == "namespaces":  # (a namespace: its pods)
            self.set_namespace(row.obj["name"])
        elif self.table_page["resource"] == "customresourcedefinitions":  # (a definition: its objects)
            page = kube_custom_page(row.obj["raw"])
            key = page["resource"]
            existing = next((r for r in self.page_rows.values() if r.page["resource"] == key), None)
            if existing is None:
                existing = self.add_page_row(page, after=self.page_rows[id(self.table_page)])
            self.page = existing.page
            self.select_page_row(existing.page)
            self.hide_details()
            self.load(switch=True)

    def pod_obj(self, key):
        """The pod "namespace/name" as the Pods table last listed it, or None."""
        ns, _, name = key.partition("/")
        if self.store is None or self.table_page is not KUBE_PODS:
            return None
        for i in range(self.store.get_n_items()):
            o = self.store.get_item(i).obj
            if (o["namespace"], o["name"]) == (ns, name):
                return o
        return None

    def set_note(self, text, seconds=30):
        """Say text first in the status line for a while, through the refreshes meanwhile (what a delete did)."""
        self.note = (text, time.monotonic() + seconds)
        if self.selection is not None:
            self.update_status()
        else:
            self.say(text)

    # ---- a pod's logs, followed
    def show_logs(self, key):
        """A pod's logs under the table: its default container's (the one its kubectl.kubernetes.io/default-container
        annotation names, else its first), from its last KUBE_LOG_TAIL lines on, followed. Another pod's, asked for,
        stop the one before's."""
        obj = self.pod_obj(key)
        if obj is None:
            return
        pod = obj["raw"]
        self.log_containers = [(name, init) for name, _l, _w, init in kube_containers(pod, time.time())]
        if not self.log_containers:
            self.set_note(f"{obj['name']} has no containers to show logs of")
            return
        names = [n for n, _i in self.log_containers]
        default = (_km(pod).get("annotations") or {}).get("kubectl.kubernetes.io/default-container")
        pick = names.index(default) if default in names else \
            next((i for i, (_n, init) in enumerate(self.log_containers) if not init), 0)
        self.log_pod = (self.context, obj["namespace"], obj["name"])
        self.logs_title.set_label(f"{obj['name']} · {obj['namespace']}")
        self.syncing = True
        self.logs_container.set_model(Gtk.StringList.new([n + " (init)" * init for n, init in self.log_containers]))
        self.logs_container.set_selected(pick)
        self.logs_previous.set_active(False)
        self.syncing = False
        if not self.logs_box.get_visible():
            self.logs_box.set_visible(True)
            self.lower.set_position(max(160, self.lower.get_height() // 2))
        self.tail_logs()

    def tail_logs(self):
        """The container picked, its log from the start: this run's followed (kubectl logs -f), or, with Previous,
        its last run's (which has ended). The one followed before is stopped first."""
        self.stop_logs()
        i = self.logs_container.get_selected()
        if self.log_pod is None or not 0 <= i < len(self.log_containers):
            return
        context, ns, pod = self.log_pod
        previous = self.logs_previous.get_active()
        args = ["--context", context, "logs", "--previous" if previous else "-f", f"--tail={KUBE_LOG_TAIL}",
                "-n", ns, pod, "-c", self.log_containers[i][0]]
        self.logs.load("", "pod.log")
        self.follow_logs(True)
        try:
            self.log_tail = KubeLogTail(args, self._log_text, self._log_end)
        except OSError as e:
            self.logs_state.set_label(f"kubectl: {e}")
            return
        self.logs_state.set_label("its previous run" if previous else "following")

    def stop_logs(self):
        if self.log_tail is not None:
            self.log_tail.stop()
            self.log_tail = None

    def hide_logs(self):
        """Close the logs: kubectl, following them, is stopped (never left running)."""
        self.stop_logs()
        self.log_pod = None
        self.logs_box.set_visible(False)

    def _log_text(self, tail, text):
        if tail is not self.log_tail:
            return
        b = self.logs.buffer
        before = b.get_line_count()
        b.insert(b.get_end_iter(), text.replace("\r\n", "\n"))
        if b.get_line_count() > KUBE_LOG_LINES * 1.1:  # (the oldest go, a tenth at a time)
            b.delete(b.get_start_iter(), b.get_iter_at_line(b.get_line_count() - KUBE_LOG_LINES)[1])
            if self.logs.search_bar.get_search_mode():  # (the matches' places moved)
                self.logs.find(self.logs.search_entry.get_text(), scroll=False)
        if len(str(b.get_line_count())) != len(str(before)):
            self.logs.gutter.queue_resize()  # (wider line numbers)

    def _log_end(self, tail, code):
        if tail is not self.log_tail:
            return
        self.log_tail = None
        if code:
            self.logs_state.set_label(f"kubectl exited with {code}")
        elif not self.logs_previous.get_active():
            self.logs_state.set_label("ended (its container stopped)")

    def follow_logs(self, on):
        """Keep to the newest line as lines come (on), or stay where you've scrolled to."""
        self.log_following = on
        was, self.syncing = self.syncing, True
        self.logs_follow.set_active(on)
        self.syncing = was
        if on:
            adj = self.logs.scroll.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())

    def _logs_moved(self, adj):
        """More lines, another size, the view moved: while following, back to the end (unless it's you dragging the
        scrollbar away from it); not following, the end reached again follows again."""
        end = adj.get_upper() - adj.get_page_size()
        if adj.get_value() >= end - 2:
            if not self.log_following:
                self.follow_logs(True)
        elif self.log_following:
            if self.log_dragging:
                self.follow_logs(False)
            else:
                adj.set_value(end)

    # ---- deleting a pod: the view's only writes, each confirmed first
    def ask_delete(self, key, force):
        """Ask before deleting a pod (force: at once, not waiting for it to stop), naming it, its namespace and its
        context, and saying plainly when that's a remote cluster (its API server isn't on this machine). Nothing runs
        unless you say Delete."""
        ns, _, name = key.partition("/")
        context = self.context
        server = next((c.get("server") for c in self.contexts if c["name"] == context), None)
        obj = self.pod_obj(key)
        owners = (_km(obj["raw"]).get("ownerReferences") or []) if obj else []
        body = [f"In namespace {ns}, on {context}."]
        if not kube_local(server):
            body.append(f"This is a remote cluster: {context}'s API server isn't on this machine.")
        if force:
            body.append("Force delete doesn't wait for the pod to stop: the cluster forgets it at once, while its "
                        "containers may go on running on their node for a while.")
        else:
            body.append("Its containers are stopped, given their grace period to finish.")
        body.append(f"It's controlled by {owners[0].get('kind')} {owners[0].get('name')}, which may start another "
                    "in its place." if owners else "No controller owns it: nothing will start it again.")
        dlg = Adw.AlertDialog(heading=f"{'Force delete' if force else 'Delete'} pod {name}?", body="\n\n".join(body),
                              default_response="cancel", close_response="cancel")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Force Delete" if force else "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "delete" and self.delete_pod(context, ns, name, force))
        self.dialog = dlg
        dlg.present(self.win)

    def delete_pod(self, context, ns, name, force):
        """Delete it (confirmed), off the main thread: what kubectl said goes in the status line, and the list is
        read again."""
        self.set_note(f"{'Force deleting' if force else 'Deleting'} pod {name}…")

        def work():
            try:
                said = kube_delete_pod(context, ns, name, force) or f'pod "{name}" deleted'
            except RuntimeError as e:
                said = f"couldn't delete pod {name}: {e}"
            GLib.idle_add(self._deleted, context, said)
        KUBE_POOL.submit(work)

    def _deleted(self, context, said):
        if context == self.context:
            self.set_note(said)
            self.load(quiet=True)
        return False
