"""hostkube — the clusters this host's kubeconfig reaches, read for a head elsewhere (the phone's Kubernetes page): the
desktop's own functions (kubedata), kubectl's answers as Freelens' pages and columns. Each kind's objects, an object's
YAML, what controls an object, a pod deleted once it's asked, the whole stack's CPU and memory (kube); a pod's log followed and a kind's list watched,
on a connection of its own (follow); the kubeconfig's contexts, sent again as tools add and delete clusters (follow's
contexts). Secrets' values are never read, and kubectl is only ever given the arguments these
build. No GTK."""
import codecs
import collections
import json
import os
import select
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:  # (as server.catalogue has it: the modules at the root)
    sys.path.insert(0, ROOT)

import kubedata as kd  # noqa: E402
import lookup  # noqa: E402
import lookupkube  # noqa: E402
from baudelib import deps, wording  # noqa: E402

PAGES = {p["resource"]: p for _title, pages in kd.KUBE_SECTIONS for p in pages}
_CUSTOM = {}  # (context, definition) -> the page its objects have
lookupkube.KUBE_FORGET.append(lambda key: [_CUSTOM.pop(k, None) for k in list(_CUSTOM) if k[0] == key[0]])
BATCH_S = 0.1   # a log's lines are sent this often at most
WATCH_S = 1.0   # a watch's changes are sent this often at most
HISTORY_S = 30 * 60  # Home's history goes back this far
IDLE_S = 24 * 60 * 60  # a context Home hasn't been read for in a day is no longer sampled, and its history let go
BACKOFF_S = 5 * 60  # a cluster that keeps failing to be read is tried again at most this far apart (twice as far each time)
# cluster (kube_key: its context's name and server) -> {"entry": its context's kubeconfig entry as last seen (_entry),
# "began": when this history began, "asked": when Home last read it, "next": when the next sample is due, "fails": how
# many reads in a row failed, "fault": the last one's error, "reading": the read under way ({"done": Event, then "read"
# or "error"}) or None, "read": (when, its last kd.kube_summary_read) or None, "samples": deque of
# (when, {namespace: (cpu requested, cpu used, memory requested, memory used)})}: from the first time Home is read for
# a cluster, a sample every KUBE_REFRESH_S (_sampling; further apart while its reads fail) whether Home is open or not,
# in this process's memory, till its context goes from the kubeconfig or its server changes (KUBE_FORGET, on a
# kubeconfig read whole: one caught half written never lets go of a history) or Home isn't read for it for IDLE_S
_HISTORY = {}
_HISTORY_LOCK = threading.Lock()
_SAMPLER = []  # (the thread taking them, while there's a context to sample)


def _forget_history(key):
    """A cluster's history let go of: its context went from the kubeconfig, or its server changed (another cluster)."""
    with _HISTORY_LOCK:
        _HISTORY.pop(tuple(key), None)


lookupkube.KUBE_FORGET.append(_forget_history)


def _home(path):
    home = os.path.expanduser("~").rstrip("/")
    return "~" + path[len(home):] if home and path.startswith(home + "/") else path


def _fault(said, context):
    return {**kd.kube_diagnose(said, kd.kube_signin(context)), "said": said}


def _page(msg):
    """The page a request is for: a kind the view lists, by its resource, or a custom resource by its definition."""
    key, crd, context = msg.get("page"), msg.get("crd"), msg.get("context")
    if key in PAGES:
        return PAGES[key]
    name = crd or key
    if not name:
        raise ValueError("a kind's page, or a definition")
    if (context, name) not in _CUSTOM:
        _CUSTOM[(context, name)] = kd.kube_custom_page(json.loads(
            kd.kube_get(context, "customresourcedefinitions", name, "-o", "json") or "{}"))
    return _CUSTOM[(context, name)]


def _page_json(page, served=True):
    return {"title": page["title"], "resource": page["resource"], "crd": page.get("crd"),
            "namespaced": bool(page["namespaced"]), "served": served, "optional": bool(page.get("known")),
            "source": page["source"]}


def _containers(pod, now):
    return [{"name": n, "look": look, "words": words, "init": init} for n, look, words, init in
            kd.kube_containers(pod, now)]


def _rows(page, items, ctx, columns):
    out = []
    for texts, keys, obj in kd.kube_rows(page, items, ctx, columns):
        raw, now = obj["raw"], obj["now"]
        kinds = [c["kind"] for c in columns]
        owner = kd.kube_controller(raw) or {}
        uid = kd.kube_uid(raw)
        out.append({
            "cells": texts,
            "keys": [list(k) if isinstance(k, tuple) else k for k in keys],
            "tones": [(kd.kube_cell_tone(c, t) or "") if c["kind"] == "status" else "" for c, t in zip(columns, texts)],
            "tips": [lookup.full_time(now - k) if kind == "age" and isinstance(k, (int, float)) else ""
                     for kind, k in zip(kinds, keys)],
            "name": obj["name"], "namespace": obj["namespace"],
            "uid": "/".join(uid) if isinstance(uid, tuple) else str(uid),
            "version": str(lookupkube._km(raw).get("resourceVersion") or ""),
            "owner_kind": owner.get("kind") or "", "owner_name": owner.get("name") or "",
            "containers": _containers(raw, now) if page["resource"] == "pods" else [],
            "default": (lookupkube._km(raw).get("annotations") or {}).get("kubectl.kubernetes.io/default-container")
            or ""})
    return out


def _metrics(page, ctx):
    return ctx["metrics"] is not None if page["resource"] == "pods" else \
        ctx["node_metrics"] is not None if page["resource"] == "nodes" else None


def _contexts(contexts=None):
    contexts = [c for c in (lookupkube.kube_contexts() if contexts is None else contexts) if not c.get("cluster_only")]
    where = ", ".join(_home(p) for p in lookupkube.kubeconfig_paths()) or wording.say("kube.no_kubeconfig")
    return {"contexts": [{"name": c["name"], "server": c.get("server") or "", "current": bool(c.get("current")),
                          "local": bool(lookupkube.kube_local(c.get("server")))} for c in contexts], "where": where}


def _failed(e, context):
    """A read that failed: the context gone from the kubeconfig (removed), or why the cluster can't be read (fault)."""
    if getattr(e, "removed", False):
        return {"removed": True}
    return {"fault": _fault(getattr(e, "said", str(e)), context)}


def _connect(context):
    try:
        resources = kd.kube_api_resources(context)
    except RuntimeError as e:
        return _failed(e, context)
    try:
        spaces = sorted(lookupkube._km(n).get("name", "") for n in json.loads(
            kd.kube_get(context, "namespaces", "-o", "json") or "{}").get("items") or [])
    except (RuntimeError, ValueError):
        spaces = []
    try:  # (names only: a big cluster's nodes, as JSON, list every image they hold)
        nodes = len(kd.kube_get(context, "nodes", "-o", "name").split())
    except RuntimeError:
        nodes = 0
    sections = [{"title": title, "icon": kd.KUBE_SECTION_ICONS.get(title, ""),
                 "pages": [_page_json(p, p["source"] == "summary" or not p.get("api") or p["api"] in resources or
                                       p["resource"] in resources)
                           for p in pages]} for title, pages in kd.KUBE_SECTIONS]
    return {"sections": sections, "namespaces": [s for s in spaces if s], "nodes": nodes}


def _list(msg):
    context, page = msg.get("context"), _page(msg)
    try:
        items = kd.kube_list(context, page)
    except (RuntimeError, ValueError) as e:
        return _failed(e, context)
    columns = kd.kube_page_columns(context, page, items=items)
    ctx = kd.kube_metrics_ctx(context, page)
    out = {"columns": [{"title": c["title"], "kind": c["kind"]} for c in columns],
           "rows": _rows(page, items, ctx, columns), "metrics": _metrics(page, ctx)}
    if msg.get("crd") and msg.get("page") not in PAGES:
        out["page"] = _page_json(page)
    return out


def _entry(c):
    """What a context's kubeconfig entry says of it, to tell when it's changed (a cluster that was failing to be read
    is then tried again at once)."""
    return tuple(c.get(k) for k in ("cluster", "user", "namespace", "server", "kubeconfig"))


def _renewed(h, c, now):
    """A history's backoff let go of if its context's kubeconfig entry c has changed since (under _HISTORY_LOCK)."""
    if c is not None and _entry(c) != h["entry"]:
        h["entry"] = _entry(c)
        h["fails"], h["fault"], h["next"] = 0, None, now


def _history(c, now):
    """A new history for context c (a kube_contexts entry), its first sample due now."""
    return {"entry": _entry(c), "began": now, "asked": now, "next": now, "fails": 0, "fault": None, "reading": None,
            "read": None, "samples": collections.deque()}


def _take(key, h):
    """Read a cluster's totals for its history h (kubectl: not under the lock) and keep what h needs of them; the read.
    One read at a time for a history: one asked for while another is under way waits for it and has what it had, so
    no sample is taken twice or kept out of order. A failed read is tried again twice as late each time in a row, up
    to BACKOFF_S (and raises)."""
    with _HISTORY_LOCK:
        run, mine = h["reading"], h["reading"] is None
        if mine:
            run = h["reading"] = {"done": threading.Event()}
    if mine:
        try:
            run["read"] = kd.kube_summary_read(key[0])
            kept = {ns: (t[0], t[1], t[3], t[4]) for ns, t in kd.kube_tally(run["read"])["namespaces"].items()}
        except Exception as e:  # noqa: BLE001 (handed on, as it is, to whoever asked)
            run["error"] = e
        now = time.time()
        with _HISTORY_LOCK:
            h["reading"] = None
            if _HISTORY.get(key) is h and "error" in run:  # (not one let go of while it was read)
                h["fails"] += 1
                h["fault"] = run["error"]
                h["next"] = now + min(BACKOFF_S, kd.KUBE_REFRESH_S * 2 ** (h["fails"] - 1))
            elif _HISTORY.get(key) is h:
                h["read"], h["next"], h["fails"], h["fault"] = (now, run["read"]), now + kd.KUBE_REFRESH_S, 0, None
                h["samples"].append((now, kept))
                while now - h["samples"][0][0] > HISTORY_S:
                    h["samples"].popleft()
        run["done"].set()
    else:
        run["done"].wait()
    if "error" in run:
        raise run["error"]
    return run["read"]


def _sample_one(key, h):
    """A sample taken for one history, on a thread of its own (a cluster that hangs holds up no other)."""
    try:
        _take(key, h)
    except (RuntimeError, ValueError, OSError):
        pass  # (a gap in its history; tried again as _take says)
    except Exception as e:  # noqa: BLE001
        print(f"kube-home-history: {key[0]}: {e!r}", file=sys.stderr, flush=True)


def _sampling():
    """A sample of each context Home has been read for every KUBE_REFRESH_S (further apart while it fails), till none
    is left: a context not read for IDLE_S (a day) is let go of, and one gone from the kubeconfig isn't read (and is let
    go of by KUBE_FORGET). One still being read isn't read again. (A sample that can't be read is a gap in its
    history.) Nothing that goes wrong ends it."""
    while True:
        time.sleep(1)
        try:
            if not _sample_due():
                return
        except Exception as e:  # noqa: BLE001 (tried again in a second)
            print(f"kube-home-history: {e!r}", file=sys.stderr, flush=True)


def _sample_due():
    """The samples that are due started, each on its own thread; False once there's no history left (and the sampler
    let go of)."""
    now = time.time()
    with _HISTORY_LOCK:
        for key in [k for k, h in _HISTORY.items() if now - h["asked"] > IDLE_S]:
            del _HISTORY[key]
        if not _HISTORY:
            _SAMPLER.clear()
            return False
        free = [(k, h) for k, h in _HISTORY.items() if h["reading"] is None]
    contexts = lookupkube.kube_contexts() if free else []
    for key, h in free:
        if lookupkube.kube_removed(key, contexts=contexts):  # (KUBE_FORGET lets go of its history)
            continue
        c = next((c for c in contexts if lookupkube.kube_key(c) == key and not c.get("cluster_only")), None)
        with _HISTORY_LOCK:
            _renewed(h, c, now)
            due = _HISTORY.get(key) is h and h["reading"] is None and now >= h["next"]
        if due:
            threading.Thread(target=_sample_one, args=(key, h), name="kube-home-sample", daemon=True).start()
    return True


def _sample(when, kept, namespaces):
    """A KubeSample: a history sample's totals in namespaces (None: every one)."""
    rows = kept.values() if namespaces is None else [kept[ns] for ns in namespaces if ns in kept]

    def add(i):
        vals = [r[i] for r in rows if r[i] is not None]
        return sum(vals) if vals else None
    return {"at": when, "cpu_requests": add(0) or 0, "cpu_used": add(1), "memory_requests": add(2) or 0,
            "memory_used": add(3)}


def _summary(msg):
    """Home: the stack's CPU and memory in the namespaces asked for (every one without), and how they went: the
    samples taken since the one asked for (every one without), as _sampling keeps them. A read under
    KUBE_REFRESH_S / 2 old is used again; while a cluster that failed waits to be tried again, its last fault is.
    fresh (Refresh): read now, whatever's kept or waited for."""
    context, namespaces, since = msg.get("context"), msg.get("namespaces"), msg.get("since")
    since = since if isinstance(since, (int, float)) and not isinstance(since, bool) else None
    fresh = msg.get("fresh") is True
    c = next((c for c in lookupkube.kube_contexts() if c["name"] == context and not c.get("cluster_only")), None)
    key, now = (lookupkube.kube_key(c) if c is not None else None), time.time()
    with _HISTORY_LOCK:
        h = _HISTORY.get(key) if key is not None else None  # (not in the kubeconfig: kubectl says so, unsampled)
        if h is None and key is not None:
            h = _HISTORY[key] = _history(c, now)
        elif h is not None:
            h["asked"] = now
            _renewed(h, c, now)
        last = h and h["read"]
        read = last[1] if last and not fresh and now - last[0] < kd.KUBE_REFRESH_S / 2 else None
        waiting = h["fault"] if h is not None and not fresh and h["fails"] and now < h["next"] else None
        if h is not None and not _SAMPLER:
            _SAMPLER.append(threading.Thread(target=_sampling, name="kube-home-history", daemon=True))
            _SAMPLER[0].start()
    try:
        if waiting is not None:
            raise waiting
        read = read or (_take(key, h) if h is not None else kd.kube_summary_read(context))
    except (RuntimeError, ValueError, OSError) as e:
        return _failed(e, context)
    with _HISTORY_LOCK:
        samples = list(h["samples"]) if h is not None and _HISTORY.get(key) is h else []
    history = [_sample(when, kept, namespaces) for when, kept in samples if since is None or when > since]
    return {"summary": {**kd.kube_summary(read, namespaces), "history": history, "every": kd.KUBE_REFRESH_S,
                        "minutes": HISTORY_S // 60, "began": h["began"] if h is not None else now}}


def _yaml(msg):
    context, page = msg.get("context"), _page(msg)
    namespace, name = msg.get("namespace") or "", msg.get("name")
    if page["source"] in ("secrets", "helm"):
        item = next((o for o in kd.kube_list(context, page)
                     if (lookupkube._km(o).get("name") or o.get("name"), lookupkube._km(o).get("namespace") or
                         o.get("namespace") or "") == (name, namespace)), {})
        if page["source"] == "secrets":
            return {"text": kd.kube_secret_yaml(namespace, name, item.get("type", ""), item.get("keys") or []),
                    "file": "secret.yaml", "refs": []}
        return {"text": json.dumps(item, indent=2), "file": "release.json", "refs": []}
    args = [page["resource"], name, "-o", "yaml"] + (["-n", namespace] if namespace else [])
    try:
        text = kd.kube_get(context, *args)
    except RuntimeError as e:
        if getattr(e, "removed", False):
            return {"removed": True}
        d = kd.kube_diagnose(getattr(e, "said", str(e)))
        return {"text": f"# {d['title']}: {d['why']}\n", "file": "object.yaml", "refs": []}
    return {"text": text, "file": "object.yaml",
            "refs": [{"line": line, "column": col, "length": n, "kind": k, "name": nm, "namespace": ns or ""}
                     for line, col, n, (k, nm, ns) in kd.kube_yaml_refs(text, namespace or None)]}


def _owner(msg):
    context, kind, name, namespace = msg.get("context"), msg.get("kind"), msg.get("name"), msg.get("namespace")
    for _ in range(3):  # (ReplicaSet → Deployment; Job → CronJob)
        if kind not in kd.KUBE_CLIMB:
            break
        try:
            up = kd.kube_controller(json.loads(kd.kube_get(context, kd.KUBE_CLIMB[kind], name, "-n", namespace,
                                                           "-o", "json")))
        except (RuntimeError, ValueError):
            break
        if not up or not up.get("kind") or not up.get("name"):
            break
        kind, name = up["kind"], up["name"]
    return {"kind": kind, "name": name, "resource": kd.KUBE_CLIMB.get(kind) or kd.kube_resource(kind)}


def _key(msg):
    """The context a request is for: its name, with the server the head knows it by when it says one (a context of
    that name with another server is another cluster, made again under the same name)."""
    server = msg.get("server")
    return (msg.get("context"), server) if isinstance(server, str) else msg.get("context")


def _server(msg):
    """The server the head knows a request's context by, if it says one (kubectl is pointed at it: kube_target)."""
    return msg.get("server") if isinstance(msg.get("server"), str) else None


ARGS = ("context", "namespace", "name", "pod", "container", "kind", "page", "crd")


def _unsafe(msg):
    """The first name in a request that kubectl would take for an option (one that starts with -), or None."""
    return next((k for k in ARGS if isinstance(msg.get(k), str) and msg[k].startswith("-")), None) or \
        ("namespaces" if msg.get("namespaces") is not None and (not isinstance(msg["namespaces"], list) or
                                                                not all(isinstance(n, str) for n in msg["namespaces"]))
         else None)


def answer(server, msg):
    """The reply's fields for a kube request."""
    what, context = msg.get("what"), msg.get("context")
    if (bad := _unsafe(msg)) is not None:
        return {"what": what, "error": f"{bad} can't start with -"}
    if (missing := deps.missing("kubectl")) is not None:  # (nothing kubectl reads is read: what the view shows instead)
        return {"what": what, "missing": missing}
    try:
        if what == "contexts":
            return {"what": what, **_contexts()}
        if not isinstance(context, str) or not context:
            return {"what": what, "error": "kube needs a context"}
        if lookupkube.kube_removed(_key(msg)):  # (deleted from the kubeconfig, or made again: kubectl isn't asked of it)
            return {"what": what, "removed": True}
        if what == "check":  # (still there: a shell may start)
            return {"what": what}
        if what == "connect":
            return {"what": what, **_connect(context)}
        if what == "list":
            return {"what": what, **_list(msg)}
        if what == "yaml":
            return {"what": what, **_yaml(msg)}
        if what == "owner":
            return {"what": what, **_owner(msg)}
        if what == "summary":
            return {"what": what, **_summary(msg)}
        if what == "delete":
            return {"what": what, "said": kd.kube_delete(context, msg.get("namespace"), msg.get("name"),
                                                          bool(msg.get("force")), _server(msg))}
    except (RuntimeError, ValueError, OSError) as e:
        if getattr(e, "removed", False):
            return {"what": what, "removed": True}
        return {"what": what, "error": str(e)}
    return {"what": what, "error": "kube takes contexts, check, connect, list, yaml, owner, summary or delete"}


# ---------------------------------------------------------------- following
def _start(conn, args, **kw):
    """kubectl with args, kept on the connection (stopped when it goes)."""
    proc = subprocess.Popen([lookupkube.kube_tool("kubectl"), *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, **kw)
    if getattr(conn, "kube_procs", None) is None:
        conn.kube_procs = []
    conn.kube_procs.append(proc)
    if getattr(conn, "gone", False):
        proc.terminate()
    return proc


def stop(conn):
    """A connection's kubectls ended (killed if one lingers)."""
    for proc in list(getattr(conn, "kube_procs", ())):
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            threading.Timer(3, lambda p=proc: p.poll() is None and p.kill()).start()


def _gone(conn, key, procs):
    """While a follow runs: its kubectls (procs, a list it keeps current) stopped once its context goes from the
    kubeconfig. A function that says whether it has (call it to let go of the watcher with stop=True)."""
    removed = threading.Event()

    def changed(contexts):  # (on the watcher's thread: looked up in its contexts, nothing waited on)
        if lookupkube.kube_removed(key, contexts=contexts):
            removed.set()
            for proc in list(procs):
                if proc.poll() is None:
                    try:
                        proc.terminate()
                    except OSError:
                        pass
    unwatch = lookupkube.kube_contexts_watch(changed)

    def gone(stop=False):
        if stop:
            unwatch()
        return removed.is_set()
    return gone


def _logs(conn, req, msg):
    previous = bool(msg.get("previous"))
    args = [*lookupkube.kube_target(msg["context"], _server(msg)), "logs", "--previous" if previous else "-f",
            f"--tail={kd.KUBE_LOG_TAIL}", "-n", msg["namespace"], msg["pod"], "-c", msg["container"]]
    procs = []
    gone = _gone(conn, _key(msg), procs)
    try:
        for again in (True, False):
            try:
                proc = _start(conn, args, stderr=subprocess.STDOUT)
            except OSError as e:
                conn.send({"op": "kube_end", "req": req, "code": 1, "said": str(e)})
                return
            procs[:] = [proc]
            code, held = _log_lines(conn, req, proc)
            if again and code and held is not None and lookupkube.kube_config_broken(held) and not gone():
                time.sleep(0.3)  # (a kubeconfig caught half written: kubectl run once more, what it said unsent)
                continue
            if held:
                conn.send({"op": "kube_text", "req": req, "text": held})
            break
        if gone() or code and lookupkube.kube_removed(_key(msg)):
            conn.send({"op": "kube_end", "req": req, "code": code or 1, "removed": True})
        else:
            conn.send({"op": "kube_end", "req": req, "code": code})
    finally:
        gone(stop=True)


def _log_lines(conn, req, proc):
    """A log's lines sent as kubectl writes them, BATCH_S's worth at a time, till it ends: its exit code, and what it
    wrote if it ended within its first second (held back, unsent: the caller sends it, or runs it again); None if
    that was sent."""
    pending, lock, done, began = [], threading.Lock(), threading.Event(), time.monotonic()

    def read():
        decode = codecs.getincrementaldecoder("utf-8")(errors="replace").decode
        fd = proc.stdout.fileno()
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                break
            with lock:
                pending.append(decode(chunk))
        done.set()
    threading.Thread(target=read, daemon=True, name="kube-logs-read").start()
    held = ""
    while True:
        finished = done.wait(BATCH_S)
        with lock:
            text, pending[:] = "".join(pending), []
        if held is not None:
            held += text
            if finished:
                break
            if time.monotonic() - began < 1:
                continue
            text, held = held, None
        if text:
            conn.send({"op": "kube_text", "req": req, "text": text})
        if finished:
            break
    proc.stdout.close()
    return proc.wait(), held


def _apply(objects, events):
    """A watch's events put in objects, by uid, as the desktop does (one the same version is no change): whether any was."""
    changed = False
    for event in events:
        o, kind = event.get("object") or {}, event.get("type")
        if kind not in ("ADDED", "MODIFIED", "DELETED") or not isinstance(o, dict):
            continue
        key = kd.kube_uid(o)
        have = objects.get(key)
        if kind == "DELETED":
            changed |= objects.pop(key, None) is not None
        elif have is None or (lookupkube._km(o).get("resourceVersion") != lookupkube._km(have).get("resourceVersion")
                              if lookupkube._km(o).get("resourceVersion") else o != have):
            objects[key], changed = o, True
    return changed


def _watch(conn, req, msg):
    procs = []
    gone = _gone(conn, _key(msg), procs)
    try:
        _watching(conn, req, msg, gone, procs)
    finally:
        gone(stop=True)


def _watching(conn, req, msg, gone, procs):
    """A kind's list watched (kubectl get --watch), its rows sent as they change; till the connection goes, or the
    context goes from the kubeconfig (kube_end, removed)."""
    context, page = msg["context"], _page(msg)
    if page["source"] != "kubectl":
        conn.send({"op": "kube_end", "req": req, "code": 1, "said": "that kind isn't watched"})
        return

    def removed(code=1):
        if gone() or code and lookupkube.kube_removed(_key(msg)):
            conn.send({"op": "kube_end", "req": req, "code": code or 1, "removed": True})
            return True
        return False
    state = {"ctx": kd.kube_metrics_ctx(context, page), "metrics_at": time.monotonic()}
    objects, fails, broken = {}, 0, 0

    def send(note=""):
        columns = kd.kube_page_columns(context, page, items=list(objects.values()))
        ctx = dict(state["ctx"], now=time.time())
        conn.send({"op": "kube_rows", "req": req, "rows": _rows(page, list(objects.values()), ctx, columns),
                   "metrics": _metrics(page, ctx), "note": note})

    def refresh():
        state["ctx"], state["metrics_at"] = kd.kube_metrics_ctx(context, page), time.monotonic()
        state["fresh"] = True
    args = ["--context", context, "get", page["resource"]] + (["--all-namespaces"] if page["namespaced"] else []) + \
        ["--watch", "--output-watch-events", "-o", "json"]
    while not getattr(conn, "gone", False):
        seen, said, code = 0, "", 0
        if (fault := lookupkube.kube_fault("kubectl")) is not None:
            code, said = 1, fault
        else:
            try:
                proc = _start(conn, args, stderr=subprocess.PIPE)
                procs[:] = [proc]
            except OSError as e:
                code, said, proc = 1, str(e), None
            if proc is not None:
                decode, parse = codecs.getincrementaldecoder("utf-8")(errors="replace").decode, json.JSONDecoder().raw_decode
                fd, buf, changed, sent = proc.stdout.fileno(), "", False, time.monotonic()
                while True:
                    ready = select.select([fd], [], [], 0.25)[0]
                    if ready:
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
                        seen += len(events)
                        changed |= _apply(objects, events)
                    if state.pop("fresh", False):
                        changed = True
                    elif time.monotonic() - state["metrics_at"] >= kd.KUBE_REFRESH_S:
                        state["metrics_at"] = time.monotonic()
                        threading.Thread(target=refresh, daemon=True, name="kube-metrics").start()
                    if changed and time.monotonic() - sent >= WATCH_S:
                        send()
                        changed, sent = False, time.monotonic()
                    if getattr(conn, "gone", False):
                        break
                if changed:
                    send()
                proc.stdout.close()
                said = (proc.stderr.read() or b"").decode(errors="replace").strip()
                proc.stderr.close()
                code = proc.wait()
        if getattr(conn, "gone", False) or removed(code):
            return
        if code and not seen and broken < lookupkube.KUBE_BROKEN_TRIES and lookupkube.kube_config_broken(said):
            broken += 1  # (a kubeconfig caught half written: watched again at once, twice at most)
            time.sleep(0.3)
            continue
        if seen:
            fails = broken = 0
        if code and not seen:
            fails += 1
            if fails >= 3:  # (it can't be watched: read every few seconds, the rows sent with why)
                why = kd.kube_diagnose(said)["title"].lower() if said else wording.say("kube.kubectl_exited", code=code)
                note = wording.say("kube.cant_watch", kind=page["title"].lower(), why=why, seconds=kd.KUBE_REFRESH_S)
                while not getattr(conn, "gone", False):
                    try:
                        objects.clear()
                        objects.update({kd.kube_uid(o): o for o in kd.kube_list(context, page)})
                        state["ctx"] = kd.kube_metrics_ctx(context, page)
                        send(note)
                    except (RuntimeError, ValueError):
                        pass
                    if removed(0):
                        return
                    for _ in range(kd.KUBE_REFRESH_S * 4):
                        if getattr(conn, "gone", False):
                            return
                        time.sleep(0.25)
                return
        time.sleep(5 if code and not seen else 1)


def _follow_contexts(conn, req):
    """The kubeconfig's contexts (kube_contexts), sent as the follow starts and again each time they change (tools
    adding and deleting clusters as they go), till the connection goes."""
    changed = threading.Event()
    unwatch = lookupkube.kube_contexts_watch(lambda _contexts: changed.set())
    try:
        conn.send({"op": "kube_contexts", "req": req, **_contexts()})
        while not getattr(conn, "gone", False):
            if changed.wait(1):
                changed.clear()
                conn.send({"op": "kube_contexts", "req": req, **_contexts()})
    finally:
        unwatch()


def follow(server, conn, msg):
    """A kube_follow request, run on a thread of its own: the answers carry msg's req, till kubectl ends or the
    connection goes (stop)."""
    req = msg.get("req")
    if (bad := _unsafe(msg)) is not None:
        conn.send({"op": "kube_end", "req": req, "code": 1, "said": f"{bad} can't start with -"})
        return
    try:
        if msg.get("what") == "contexts":
            _follow_contexts(conn, req)
        elif msg.get("what") in ("logs", "watch") and lookupkube.kube_removed(_key(msg)):
            conn.send({"op": "kube_end", "req": req, "code": 1, "removed": True})
        elif msg.get("what") == "logs":
            _logs(conn, req, msg)
        elif msg.get("what") == "watch":
            _watch(conn, req, msg)
        else:
            conn.send({"op": "error", "req": req, "error": "kube_follow takes contexts, logs or watch"})
            return
    except (KeyError, ValueError, OSError, RuntimeError) as e:
        conn.send({"op": "kube_end", "req": req, "code": 1, "said": str(e)})
