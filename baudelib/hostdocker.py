"""hostdocker — Docker on this host, for every app's Docker view (Baude's, Record's, the phone's: each asks its host):
docker's contexts, and Colima's and Podman's sockets found besides (contexts); a category's objects, as the view's
table has them (list); an object inspected, as JSON or YAML (inspect); a container or a Compose project started,
stopped, restarted or removed, and an image, a volume or a network removed (start, stop, restart, remove: the apps ask
first before a remove); a container's or a Compose project's log followed, on a connection of its own (follow). The
docker CLI does it all, with --format json, and is only ever given the arguments these build. No GTK."""
import calendar
import codecs
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:  # (as hostkube has it: the modules at the root)
    sys.path.insert(0, ROOT)

import lookup  # noqa: E402
from lookupkube import kube_fmt_age  # noqa: E402
from baudelib import deps, wording  # noqa: E402

BATCH_S = 0.1  # (a log's lines are sent this often at most)
LOG_TAIL = 1000  # (a log's last lines, then followed)
TIMEOUT_S = 30
SHELL = ["sh", "-c", "command -v bash >/dev/null 2>&1 && exec bash || exec sh"]
PREDEFINED = {"bridge", "host", "none"}  # (networks docker makes itself: never removed)

# the sidebar: (title, icon, [(page title, key, what it lists in a count)])
SECTIONS = (
    ("Containers", "baude-kube-workloads-symbolic", (("Running", "running", "running containers"),
                                                     ("All", "containers", "containers"))),
    ("Images", "package-x-generic-symbolic", (("Images", "images", "images"),)),
    ("Volumes", "baude-kube-storage-symbolic", (("Volumes", "volumes", "volumes"),)),
    ("Networks", "baude-kube-network-symbolic", (("Networks", "networks", "networks"),)),
    ("Compose Projects", "view-grid-symbolic", (("Compose Projects", "compose", "Compose projects"),)),
)
PAGES = {key for _t, _i, pages in SECTIONS for _p, key, _w in pages}
ACTIONS = ("start", "stop", "restart", "remove")


def _docker():
    return deps.program("docker") or "docker"


def _run(args, timeout=TIMEOUT_S):
    """docker's output for args, or RuntimeError with what it said (its said: all of it)."""
    try:
        p = subprocess.run([_docker(), *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        err = RuntimeError(f"docker: {e}")
        err.said = str(e)
        raise err from e
    if p.returncode:
        said = (p.stderr or p.stdout or "").strip() or wording.say("docker.exited", code=p.returncode)
        err = RuntimeError(said)
        err.said = said
        raise err
    return p.stdout


def _objects(text):
    """docker's --format json: an object a line (or one array of them, as some versions write)."""
    text = (text or "").strip()
    if text.startswith("["):
        got = json.loads(text)
        return [o for o in got if isinstance(o, dict)]
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict):
                out.append(o)
    return out


# ---------------------------------------------------------------- contexts
def _colima_sockets():
    """[(context name, socket)]: Colima's running profiles' Docker sockets, as Colima names their contexts."""
    exe = shutil.which("colima")
    if exe is None:
        return []
    try:
        text = subprocess.run([exe, "list", "--json"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    home = os.environ.get("COLIMA_HOME") or os.path.expanduser("~/.colima")
    out = []
    for p in _objects(text):
        name = p.get("name") or "default"
        if str(p.get("status") or "").lower() != "running":
            continue
        out.append(("colima" if name == "default" else f"colima-{name}", os.path.join(home, name, "docker.sock")))
    return out


def _podman_socket():
    """Podman's Docker-compatible socket, if Podman's here and it's there: its machine's (macOS) or the user's."""
    exe = shutil.which("podman")
    if exe is None:
        return None
    if sys.platform == "darwin":
        try:
            path = subprocess.run([exe, "machine", "inspect", "--format", "{{.ConnectionInfo.PodmanSocket.Path}}"],
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return None
    else:
        path = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "podman", "podman.sock")
    return path if path and os.path.exists(path.splitlines()[0]) else None


CONTEXTS_TTL_S = 30
_contexts_cache = (0.0, [])


def contexts(fresh=False):
    """[{name, current, args}]: docker's contexts (args: --context), then Colima's and Podman's sockets that none of
    them reaches (args: --host). Kept for CONTEXTS_TTL_S seconds; fresh reads them again."""
    global _contexts_cache
    at, kept = _contexts_cache
    if not fresh and kept and time.monotonic() - at < CONTEXTS_TTL_S:
        return kept
    _contexts_cache = (time.monotonic(), out := _read_contexts())
    return out


def _read_contexts():
    try:
        listed = _objects(_run(["context", "ls", "--format", "json"], timeout=10))
    except RuntimeError:
        listed = []
    out = [{"name": c.get("Name") or "", "current": bool(c.get("Current")), "args": ["--context", c.get("Name") or ""],
            "endpoint": c.get("DockerEndpoint") or ""} for c in listed if c.get("Name")]
    if not out:  # (a docker too old for contexts: what DOCKER_HOST, or the default, reaches)
        out = [{"name": "default", "current": True, "args": [], "endpoint": ""}]
    have = {c["name"] for c in out} | {c["endpoint"] for c in out}
    found = _colima_sockets()
    if (podman := _podman_socket()) is not None:
        found.append(("podman", podman))
    for name, sock in found:
        if name not in have and "unix://" + sock not in have:
            out.append({"name": name, "current": False, "args": ["--host", "unix://" + sock], "endpoint": "unix://" + sock})
    return out


def _args(name):
    """The global arguments that reach context name, or ValueError."""
    c = next((c for c in contexts() if c["name"] == name), None)
    if c is None:
        raise ValueError(wording.say("docker.no_such_context", context=name))
    return c["args"]


def diagnose(said, context):
    """Why Docker can't be reached, from what docker said: {title, why, fix, said}."""
    low = said.lower()
    lines = [ln.strip() for ln in said.splitlines() if ln.strip()]
    why = next((ln for ln in lines if "cannot connect" in ln.lower() or "error" in ln.lower()), lines[-1] if lines else "")
    if "cannot connect to the docker daemon" in low or "is the docker daemon running" in low or \
            "connection refused" in low or "no such file or directory" in low and ".sock" in low:
        if context.startswith("colima"):
            fix = "colima start" + ("" if context == "colima" else f" --profile {context[len('colima-'):]}")
        elif context == "podman":
            fix = "podman machine start" if sys.platform == "darwin" else "systemctl --user start podman.socket"
        else:
            fix = "open -a Docker" if sys.platform == "darwin" else "sudo systemctl start docker"
        return {"title": wording.say("docker.not_running"), "why": why, "fix": fix, "said": said}
    if "permission denied" in low:
        return {"title": wording.say("docker.not_allowed"), "why": why,
                "fix": None if sys.platform == "darwin" else "sudo usermod -aG docker $USER", "said": said}
    return {"title": wording.say("docker.failed"), "why": why, "fix": None, "said": said}


def _contexts_reply():
    cs = contexts(fresh=True)
    return {"contexts": [{"name": c["name"], "current": c["current"]} for c in cs],
            "sections": [{"title": t, "icon": icon, "pages": [{"title": p, "key": k, "what": w} for p, k, w in pages]}
                         for t, icon, pages in SECTIONS]}


# ---------------------------------------------------------------- lists
def _when(text):
    """docker's CreatedAt ("2026-11-26 13:00:00 +0000 UTC", or RFC 3339) as seconds since the epoch, or None."""
    if not isinstance(text, str) or not text:
        return None
    m = re.match(r"(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)(?:\.\d+)?\s*(Z|[+-]\d\d:?\d\d)?", text)
    if not m:
        return None
    at = calendar.timegm(time.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S"))
    off = m.group(3)
    if off and off != "Z":
        sign = 1 if off[0] == "+" else -1
        digits = off[1:].replace(":", "")
        at -= sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    return at


def _bytes(text):
    """A size docker writes (187MB, 1.2GB, 0B) as bytes, for sorting."""
    m = re.match(r"\s*([\d.]+)\s*([kKMGTP]?i?B)?", text or "")
    if not m:
        return None
    return float(m.group(1)) * 1000 ** "BKMGTP".index((m.group(2) or "B")[0].upper())


def _home(path):
    home = os.path.expanduser("~").rstrip("/")
    return "~" + path[len(home):] if home and path.startswith(home + "/") else path


def _labels(text):
    """docker's labels ("a=b,c=d") as a dict."""
    if isinstance(text, dict):
        return text
    out = {}
    for part in (text or "").split(","):
        k, _, v = part.partition("=")
        if k:
            out[k.strip()] = v
    return out


def _tone(state, status):
    status = (status or "").lower()
    if state == "running":
        return "bad" if "unhealthy" in status else "warn" if "health: starting" in status else "ok"
    if state in ("restarting", "paused", "created", "removing"):
        return "warn"
    if state == "dead" or state == "exited" and not re.search(r"exited \(0\)", status):
        return "bad"
    return ""


def _age(at, now):
    return (kube_fmt_age(now - at) if at is not None else "", now - at if at is not None else None,
            lookup.full_time(at) if at is not None else "")


def _row(id_, name, cols, cells, actions=(), logs=False, shell=(), running=False):
    """A row: cells as (text, key, tone, tip) by column."""
    return {"id": id_, "name": name, "cells": [c[0] for c in cells], "keys": [c[1] for c in cells],
            "tones": [c[2] for c in cells], "tips": [c[3] for c in cells], "actions": list(actions), "logs": logs,
            "shell": list(shell), "running": running}


def _containers(args, everything, now):
    cols = [("Name", "name"), ("Image", None), ("Status", "status"), ("Ports", None), ("Project", None),
            ("Age", "age")]
    rows = []
    for c in _objects(_run([*args, "ps", "--no-trunc", "--format", "json", *(["--all"] if everything else [])])):
        cid, state, status = c.get("ID") or "", (c.get("State") or "").lower(), c.get("Status") or ""
        name = (c.get("Names") or cid[:12]).split(",")[0]
        age, key, tip = _age(_when(c.get("CreatedAt")), now)
        ports = ", ".join(p.strip() for p in (c.get("Ports") or "").split(",") if p.strip())
        project = _labels(c.get("Labels")).get("com.docker.compose.project", "")
        running = state == "running"
        actions = ("stop", "restart", "remove") if running or state == "restarting" else \
            ("start", "remove") if state in ("exited", "created", "dead") else ("stop", "remove")
        rows.append(_row(cid, name, cols, [
            (name, name, "", ""), (c.get("Image") or "", c.get("Image") or "", "", ""),
            (status, status, _tone(state, status), ""), (ports, ports, "", ""), (project, project, "", ""),
            (age, key, "", tip)], actions, logs=True,
            shell=[_docker(), *args, "exec", "-it", cid, *SHELL] if running else (), running=running))
    return cols, rows


def _images(args, now):
    cols = [("Repository", "name"), ("Tag", None), ("ID", None), ("Size", None), ("Age", "age")]
    rows = []
    for i in _objects(_run([*args, "image", "ls", "--format", "json"])):
        repo, tag, iid = i.get("Repository") or "<none>", i.get("Tag") or "<none>", i.get("ID") or ""
        name = f"{repo}:{tag}" if repo != "<none>" else iid
        age, key, tip = _age(_when(i.get("CreatedAt")), now)
        size = i.get("Size") or ""
        rows.append(_row(iid if repo == "<none>" or tag == "<none>" else name, name, cols, [
            (repo, repo, "", ""), (tag, tag, "", ""), (iid.removeprefix("sha256:")[:12], iid, "", ""),
            (size, _bytes(size), "", ""), (age, key, "", tip)], ("remove",)))
    return cols, rows


def _volumes(args):
    cols = [("Name", "name"), ("Driver", None), ("Project", None), ("Mountpoint", None)]
    rows = []
    for v in _objects(_run([*args, "volume", "ls", "--format", "json"])):
        name = v.get("Name") or ""
        project = _labels(v.get("Labels")).get("com.docker.compose.project", "")
        rows.append(_row(name, name, cols, [(name, name, "", ""), (v.get("Driver") or "", v.get("Driver") or "", "", ""),
                                            (project, project, "", ""),
                                            (v.get("Mountpoint") or "", v.get("Mountpoint") or "", "", "")], ("remove",)))
    return cols, rows


def _networks(args, now):
    cols = [("Name", "name"), ("Driver", None), ("Scope", None), ("ID", None), ("Age", "age")]
    rows = []
    for n in _objects(_run([*args, "network", "ls", "--no-trunc", "--format", "json"])):
        name, nid = n.get("Name") or "", n.get("ID") or ""
        age, key, tip = _age(_when(n.get("CreatedAt")), now)
        rows.append(_row(nid or name, name, cols, [
            (name, name, "", ""), (n.get("Driver") or "", n.get("Driver") or "", "", ""),
            (n.get("Scope") or "", n.get("Scope") or "", "", ""), (nid[:12], nid, "", ""), (age, key, "", tip)],
            () if name in PREDEFINED else ("remove",)))
    return cols, rows


def _compose(args):
    cols = [("Name", "name"), ("Status", "status"), ("Config Files", None)]
    rows = []
    for p in _objects(_run([*args, "compose", "ls", "--all", "--format", "json"])):
        name, status = p.get("Name") or "", p.get("Status") or ""
        running = "running" in status
        tone = "ok" if running and "(" in status and "," not in status else "warn" if running else ""
        files = ", ".join(_home(f.strip()) for f in (p.get("ConfigFiles") or "").split(",") if f.strip())
        rows.append(_row(name, name, cols, [(name, name, "", ""), (status, status, tone, ""), (files, files, "", "")],
                         ("stop", "restart", "remove") if running else ("start", "remove"), logs=True, running=running))
    return cols, rows


def _list(args, page):
    now = time.time()
    if page in ("running", "containers"):
        cols, rows = _containers(args, page == "containers", now)
    elif page == "images":
        cols, rows = _images(args, now)
    elif page == "volumes":
        cols, rows = _volumes(args)
    elif page == "networks":
        cols, rows = _networks(args, now)
    else:
        cols, rows = _compose(args)
    return {"columns": [{"title": t, "kind": k} for t, k in cols], "rows": rows}


# ---------------------------------------------------------------- inspecting
def _scalar(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    s = str(v)
    plain = re.fullmatch(r"[A-Za-z0-9_./@][A-Za-z0-9_./@:+ -]*", s) and not s.endswith(" ") and \
        s.lower() not in ("true", "false", "null", "yes", "no", "on", "off", "~") and \
        not re.fullmatch(r"[-+]?[\d.]+(e[-+]?\d+)?", s, re.I) and ": " not in s
    return s if plain else json.dumps(s, ensure_ascii=False)


def yaml(v, indent=0):
    """v (JSON's) as YAML, block style: what inspect's YAML is."""
    pad = "  " * indent
    if isinstance(v, dict):
        if not v:
            return pad + "{}\n"
        out = []
        for k, x in v.items():
            key = _scalar(str(k))
            if isinstance(x, (dict, list)) and x:
                out.append(f"{pad}{key}:\n{yaml(x, indent + 1)}")
            else:
                out.append(f"{pad}{key}: {'{}' if x == {} else '[]' if x == [] else _scalar(x)}\n")
        return "".join(out)
    if isinstance(v, list):
        if not v:
            return pad + "[]\n"
        out = []
        for x in v:
            if isinstance(x, (dict, list)) and x:
                inner = yaml(x, indent + 1)
                out.append(f"{pad}- {inner[len(pad) + 2:]}")
            else:
                out.append(f"{pad}- {'{}' if x == {} else '[]' if x == [] else _scalar(x)}\n")
        return "".join(out)
    return pad + _scalar(v) + "\n"


def _inspect(args, page, oid, fmt):
    if page == "compose":
        got = _objects(_run([*args, "compose", "-p", oid, "ps", "--all", "--format", "json"]))
    else:
        kind = {"running": "container", "containers": "container", "images": "image", "volumes": "volume",
                "networks": "network"}[page]
        got = json.loads(_run([*args, kind, "inspect", oid]) or "[]")
    if fmt == "yaml":
        return {"text": yaml(got), "file": "inspect.yaml"}
    return {"text": json.dumps(got, indent=2, ensure_ascii=False) + "\n", "file": "inspect.json"}


# ---------------------------------------------------------------- acting
def _act(args, page, what, oid):
    """A start, stop, restart or remove of the object: what docker said (a Compose project's remove: down)."""
    if page == "compose":
        verb = {"start": ["start"], "stop": ["stop"], "restart": ["restart"], "remove": ["down"]}[what]
        out = _run([*args, "compose", "-p", oid, *verb], timeout=300)
    elif page in ("running", "containers"):
        if what == "remove":
            _run([*args, "stop", oid], timeout=120)
            out = _run([*args, "rm", oid], timeout=120)
        else:
            out = _run([*args, what, oid], timeout=120)
    elif what == "remove":
        kind = {"images": "image", "volumes": "volume", "networks": "network"}[page]
        out = _run([*args, kind, "rm", oid], timeout=120)
    else:
        raise ValueError(wording.say("docker.cant_do", what=what))
    return out.strip() or wording.say("docker.done", what=what, name=oid[:12] if len(oid) == 64 else oid)


ARGS = ("context", "page", "id")


def _unsafe(msg):
    """The first name in a request that docker would take for an option (one that starts with -), or None."""
    return next((k for k in ARGS if isinstance(msg.get(k), str) and msg[k].startswith("-")), None)


def answer(server, msg):
    """The reply's fields for a docker request."""
    what, context, page, oid = msg.get("what"), msg.get("context"), msg.get("page"), msg.get("id")
    if (bad := _unsafe(msg)) is not None:
        return {"what": what, "error": f"{bad} can't start with -"}
    if (missing := deps.missing("docker")) is not None:  # (nothing docker reads is read: what the view shows instead)
        return {"what": what, "missing": missing}
    try:
        if what == "contexts":
            return {"what": what, **_contexts_reply()}
        if not isinstance(context, str) or not context:
            return {"what": what, "error": "docker needs a context"}
        if page not in PAGES:
            return {"what": what, "error": f"docker takes a page: {', '.join(sorted(PAGES))}"}
        args = _args(context)
        if what == "list":
            try:
                return {"what": what, **_list(args, page)}
            except RuntimeError as e:
                return {"what": what, "fault": diagnose(getattr(e, "said", str(e)), context)}
        if not isinstance(oid, str) or not oid:
            return {"what": what, "error": f"docker's {what} needs an id"}
        if what == "inspect":
            return {"what": what, **_inspect(args, page, oid, msg.get("format") or "json")}
        if what in ACTIONS:
            return {"what": what, "said": _act(args, page, what, oid)}
    except (RuntimeError, ValueError, KeyError, OSError) as e:
        return {"what": what, "error": str(e)}
    return {"what": what, "error": "docker takes contexts, list, inspect, start, stop, restart or remove"}


# ---------------------------------------------------------------- following
def stop(conn):
    """A connection's dockers ended (killed if one lingers)."""
    for proc in list(getattr(conn, "docker_procs", ())):
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            threading.Timer(3, lambda p=proc: p.poll() is None and p.kill()).start()


def follow(server, conn, msg):
    """A docker_follow request, run on a thread of its own: the answers carry msg's req, till docker ends or the
    connection goes (stop)."""
    req = msg.get("req")
    if (bad := _unsafe(msg)) is not None:
        conn.send({"op": "docker_end", "req": req, "code": 1, "said": f"{bad} can't start with -"})
        return
    if msg.get("what") != "logs":
        conn.send({"op": "error", "req": req, "error": "docker_follow takes logs"})
        return
    if (missing := deps.missing("docker")) is not None:
        conn.send({"op": "docker_end", "req": req, "code": 1, "said": deps.say(missing, "window.docker")})
        return
    page, oid = msg.get("page"), msg.get("id")
    try:
        args = _args(msg.get("context") or "")
        if not isinstance(oid, str) or not oid or page not in ("running", "containers", "compose"):
            raise ValueError("docker_follow's logs need a container's or a Compose project's page and id")
        argv = [_docker(), *args, *(["compose", "-p", oid, "logs", "--no-color"] if page == "compose" else ["logs"]),
                "-f", f"--tail={LOG_TAIL}", *([] if page == "compose" else [oid])]
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except (OSError, ValueError, RuntimeError) as e:
        conn.send({"op": "docker_end", "req": req, "code": 1, "said": str(e)})
        return
    if getattr(conn, "docker_procs", None) is None:
        conn.docker_procs = []
    conn.docker_procs.append(proc)
    if getattr(conn, "gone", False):
        proc.terminate()
    pending, lock, done = [], threading.Lock(), threading.Event()

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
    threading.Thread(target=read, daemon=True, name="docker-logs-read").start()
    while True:
        finished = done.wait(BATCH_S)
        with lock:
            text, pending[:] = "".join(pending), []
        if text:
            conn.send({"op": "docker_text", "req": req, "text": text})
        if finished:
            break
    proc.stdout.close()
    conn.send({"op": "docker_end", "req": req, "code": proc.wait()})
