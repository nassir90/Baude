"""A model's files on this machine, and those files served to another host in the mesh (mesh.Mesh.files), read-only.

Ollama keeps a model as a manifest (manifests/<registry>/<namespace>/<model>/<tag>) naming content-addressed blobs
(blobs/sha256-<hex>), which is a registry's layout already: served as a registry's /v2/ API, another host's own Ollama
pulls the model from here (ollama pull --insecure), checking every blob's digest as it does any pull. An MLX model is a
Hugging Face cache snapshot: its files listed, then each fetched (mlx_fetch, on the other host).

Paths served (serve):
  /v2/<namespace>/<model>/manifests/<tag>       an Ollama model's manifest (registry.ollama.ai's)
  /v2/<namespace>/<model>/blobs/sha256:<hex>    one of its blobs (a Range of it, if asked; first, sent on to
                                                itself, as a registry sends a pull to its CDN)
  /where/ollama/<name>                          where an Ollama model's files are here (JSON: ollama_where)
  /where/mlx/<org>/<repo>                       where an MLX model's are (JSON: mlx_where)
  /mlx/<org>/<repo>/<file>                      one of an MLX model's files
Nothing else: no path outside these folders, no other file."""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

REGISTRY = "registry.ollama.ai"
CHUNK = 1 << 20
MANIFEST_TYPE = "application/vnd.docker.distribution.manifest.v2+json"
PART = r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}"
NAME_RE = re.compile(rf"(?:({PART})/)?({PART})(?::({PART}))?")  # ([namespace/]model[:tag], as Ollama names them)
DIGEST_RE = re.compile(r"sha256[:-]([0-9a-f]{64})")
REPO_RE = re.compile(rf"({PART})/({PART})")  # (a Hugging Face repo id: org/name)
FILE_RE = re.compile(r"[A-Za-z0-9_.-][A-Za-z0-9_. -]{0,199}(?:/[A-Za-z0-9_.-][A-Za-z0-9_. -]{0,199}){0,3}")


def ollama_dirs():
    """Where Ollama may keep its models here, in the order looked in: OLLAMA_MODELS, settings.json's ollama_models,
    ~/.ollama/models (the app's, and ollama serve's), Linux's service's (/usr/share/ollama/..., /var/lib/ollama)."""
    out = [os.environ.get("OLLAMA_MODELS")]
    try:
        from baudelib import providers
        st = providers.settings().get("ollama_models")
        out.append(st if isinstance(st, str) else None)
    except Exception:  # noqa: BLE001 (no settings: the usual places)
        pass
    out += ["~/.ollama/models", "/usr/share/ollama/.ollama/models", "/var/lib/ollama/.ollama/models",
            "/var/lib/ollama/models"]
    return list(dict.fromkeys(os.path.expanduser(d) for d in out if d))


def parse_name(name):
    """(namespace, model, tag) of an Ollama model's name: qwen3:4b is (library, qwen3, 4b); None if it isn't one."""
    m = NAME_RE.fullmatch(str(name or "").strip())
    return (m[1] or "library", m[2], m[3] or "latest") if m else None


def _manifest(ns, model, tag):
    """(its models folder, its manifest's path) for an Ollama model of registry.ollama.ai's here; None if it isn't."""
    for d in ollama_dirs():
        path = os.path.join(d, "manifests", REGISTRY, ns, model, tag)
        if os.path.isfile(path):
            return d, path
    return None


def ollama_where(name):
    """Where an Ollama model's files are: {"name", "dir" (Ollama's models folder), "manifest" (its path), "blobs":
    [{"digest", "path", "size"}], "size"}; ValueError if it isn't here."""
    parsed = parse_name(name)
    found = parsed and _manifest(*parsed)
    if not found:
        raise ValueError(f"no Ollama model {name} here (looked in {', '.join(ollama_dirs())})")
    d, path = found
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    blobs = []
    for layer in [manifest.get("config") or {}, *(manifest.get("layers") or [])]:
        m = DIGEST_RE.fullmatch(str(layer.get("digest") or ""))
        if m:
            p = os.path.join(d, "blobs", f"sha256-{m[1]}")
            blobs.append({"digest": f"sha256:{m[1]}", "path": p, "size": os.path.getsize(p) if os.path.exists(p)
                          else None})
    ns, model, tag = parsed
    return {"name": f"{model}:{tag}" if ns == "library" else f"{ns}/{model}:{tag}", "dir": d, "manifest": path,
            "blobs": blobs, "size": sum(b["size"] or 0 for b in blobs)}


def mlx_where(repo):
    """Where an MLX model of the Hugging Face cache is: {"repo", "dir" (its snapshot), "revision", "files": [{"name",
    "size"}], "size"}; ValueError if it isn't here."""
    from baudelib import models
    if not REPO_RE.fullmatch(str(repo or "")):
        raise ValueError(f"{repo!r} isn't a Hugging Face repo (org/name)")
    snap = models.hub_snapshot(os.path.join(models.hf_hub(), "models--" + repo.replace("/", "--")))
    if not snap:
        raise ValueError(f"no MLX model {repo} in the Hugging Face cache here ({models.hf_hub()})")
    files = []
    for root, dirs, names in os.walk(snap):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for n in sorted(names):
            rel = os.path.relpath(os.path.join(root, n), snap)
            if not n.startswith(".") and FILE_RE.fullmatch(rel) and os.path.isfile(os.path.join(root, n)):
                files.append({"name": rel, "size": os.path.getsize(os.path.join(root, n))})
    return {"repo": repo, "dir": snap, "revision": os.path.basename(snap), "files": files,
            "size": sum(f["size"] for f in files)}


def _file(path, rng=None, kind="application/octet-stream"):
    """(status, headers, chunks) of a file, or of a Range of it (bytes=a-b, a-)."""
    size = os.path.getsize(path)
    start, end = 0, size - 1
    m = re.fullmatch(r"bytes=(\d+)-(\d*)", rng or "")
    if m:
        start, end = int(m[1]), min(int(m[2]), size - 1) if m[2] else size - 1
        if start > end:
            return 416, {"Content-Range": f"bytes */{size}"}, iter(())

    def chunks():
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0 and (b := f.read(min(CHUNK, left))):
                left -= len(b)
                yield b
    heads = {"Content-Type": kind, "Content-Length": str(end - start + 1), "Accept-Ranges": "bytes"}
    if m:
        heads["Content-Range"] = f"bytes {start}-{end}/{size}"
    return (206 if m else 200), heads, chunks()


def _json(status, value):
    body = json.dumps(value).encode()
    return status, {"Content-Type": "application/json", "Content-Length": str(len(body))}, iter((body,))


def _error(status, text):
    return _json(status, {"errors": [{"code": "NOT_FOUND" if status == 404 else "UNSUPPORTED", "message": text}],
                          "error": text})


def serve(path, headers=None):
    """(status, headers, chunks) for a path another host asks of this machine's models (the module's list); 404 for
    anything else."""
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    parts = path.split("?", 1)[0].split("/")[1:]
    try:
        if parts[:1] == ["v2"] and len(parts) == 5 and re.fullmatch(PART, parts[1]) and re.fullmatch(PART, parts[2]):
            ns, model, kind, ref = parts[1:]
            if kind == "manifests" and re.fullmatch(PART, ref):
                found = _manifest(ns, model, ref)
                if not found:
                    return _error(404, f"no Ollama model {model}:{ref} here")
                return _file(found[1], kind=MANIFEST_TYPE)
            m = DIGEST_RE.fullmatch(ref)
            if kind == "blobs" and m:  # (only one a manifest here names: no other file of Ollama's folder)
                for d in ollama_dirs():
                    blob = os.path.join(d, "blobs", f"sha256-{m[1]}")
                    if os.path.isfile(blob) and _named(d, ns, model, f"sha256:{m[1]}"):
                        if "?" not in path and "range" not in headers:  # (Ollama's pull asks first where a blob
                            # is, as a registry sends it to a CDN: here, where it's asked)
                            return 307, {"Location": path + "?direct", "Content-Length": "0"}, iter(())
                        return _file(blob, headers.get("range"))
                return _error(404, f"no blob {ref} of {model} here")
        if parts[:2] == ["where", "ollama"] and len(parts) in (3, 4):
            return _json(200, ollama_where("/".join(parts[2:])))
        if parts[:2] == ["where", "mlx"] and len(parts) == 4:
            return _json(200, mlx_where(f"{parts[2]}/{parts[3]}"))
        if parts[:1] == ["mlx"] and len(parts) >= 4:
            where = mlx_where(f"{parts[1]}/{parts[2]}")
            rel = "/".join(parts[3:])
            if FILE_RE.fullmatch(rel) and ".." not in rel.split("/") and rel in {f["name"] for f in where["files"]}:
                return _file(os.path.join(where["dir"], rel), headers.get("range"))
            return _error(404, f"no file {rel} of {where['repo']} here")
    except ValueError as e:
        return _error(404, str(e))
    except OSError as e:
        return _error(500, str(e))
    return _error(404, "not a model's file: /v2/..., /where/..., /mlx/...")


def _named(d, ns, model, digest):
    """Whether a manifest of the model's (any tag of it) in Ollama's folder d names the blob."""
    root = os.path.join(d, "manifests", REGISTRY, ns, model)
    for tag in os.listdir(root) if os.path.isdir(root) else []:
        try:
            with open(os.path.join(root, tag), encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        if any(x.get("digest") == digest for x in [m.get("config") or {}, *(m.get("layers") or [])]):
            return True
    return False


# ---- fetched here, from another host in the mesh (its host process's Proxy: mesh.proxy)

def hosts():
    """The other hosts in the mesh that have models: {its name: its member id} (what this machine's host process
    keeps of them: mesh.endpoints, mesh.agents)."""
    from baudelib import mesh
    out = {}
    for e in mesh.endpoints() + mesh.agents():
        name = e.get("host_name") or str(e.get("host"))[:8]
        out.setdefault(str(name), e.get("host"))
    return out


def member_of(host):
    """A host's member id, by its name (or its id); ValueError naming those there are, if it isn't one."""
    found = hosts()
    if host in found.values():
        return host
    if host in found:
        return found[host]
    low = {k.lower(): v for k, v in found.items()}
    if str(host).lower() in low:
        return low[str(host).lower()]
    raise ValueError(f"no host {host!r} with models in the mesh" + (f": there's {', '.join(sorted(found))}"
                                                                     if found else " (none is linked)"))


def _proxy():
    from baudelib import mesh
    p = mesh.proxy()
    if p is None:
        raise ValueError("this machine's host process has no proxy to other hosts (none with models is linked)")
    return p


def _get(member, path, timeout=60):
    """A response from member's files (path), through the Proxy: urllib's response; ValueError with what it said."""
    p = _proxy()
    req = urllib.request.Request(f"{p['url'].removesuffix('/mesh')}/files/{member}{path}",
                                 headers={"Authorization": f"Bearer {p['key']}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        return opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read() or b"{}")
            said = said.get("error") if isinstance(said.get("error"), str) else (said.get("error") or {}).get("message")
        except (ValueError, AttributeError):
            said = None
        raise ValueError(said or f"HTTP {e.code}") from None
    except OSError as e:
        raise ValueError(str(e)) from None


def remote_where(member, model, kind=None):
    """Where a model's files are on another host: (kind, ollama_where's or mlx_where's there). kind: "ollama", "mlx",
    or None for whichever it is (Ollama's first)."""
    errors = []
    for k in [kind] if kind else ["ollama", "mlx"]:
        if k == "mlx" and not REPO_RE.fullmatch(model):
            errors.append(f"{model} isn't a Hugging Face repo (org/name)")
            continue
        try:
            with _get(member, f"/where/{k}/{model}") as r:
                return k, json.load(r)
        except ValueError as e:
            errors.append(str(e))
    raise ValueError("; ".join(errors))


def ollama_fetch(member, name, progress=None, stopped=lambda: False):
    """An Ollama model of another host's pulled into this machine's Ollama, by that Ollama (from the Proxy's /v2/,
    as a registry: its blobs checked as any pull's are), then named as it's named there. Its name here."""
    from baudelib import mesh, providers
    ns, model, tag = parse_name(name) or (None, None, None)
    if model is None:
        raise ValueError(f"{name!r} isn't an Ollama model's name")
    if ns != "library":
        raise ValueError(f"{name} isn't one of Ollama's library models: only those come through the mesh as yet "
                         "(model_files says where its files are, to copy by hand)")
    p, url = _proxy(), providers.ollama_url()
    here = f"{model}:{tag}"
    port = urllib.parse.urlsplit(p["url"]).port
    pulled = f"127.0.0.1:{port}/{mesh.namespace(p['key'], member)}/{model}:{tag}"
    req = urllib.request.Request(url + "/api/pull", json.dumps({"model": pulled, "insecure": True,
                                                               "stream": True}).encode(),
                                 {"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        resp = opener.open(req, timeout=600)
    except OSError as e:
        raise ValueError(f"Ollama {providers.unreachable(url, e)}") from None
    last = None
    with resp:
        for line in resp:
            if stopped():
                raise ValueError("stopped: Ollama's pull was let go of (what it had is kept for next time)")
            try:
                said = json.loads(line)
            except ValueError:
                continue
            if said.get("error"):
                raise ValueError(f"Ollama: {said['error']}")
            last = said.get("status")
            if progress and said.get("total"):
                progress(said.get("completed") or 0, said["total"], said.get("digest"))
    if last != "success":
        raise ValueError(f"Ollama's pull ended at {last!r}")
    providers.fetch(url + "/api/copy", {"source": pulled, "destination": here})
    providers.fetch(url + "/api/delete", {"model": pulled}, method="DELETE")  # (the name only: the copy keeps its blobs)
    return here


def mlx_fetch(member, repo, progress=None, stopped=lambda: False):
    """An MLX model of another host's Hugging Face cache copied into this one's, file by file (each written aside,
    then put in place; one there already at its size is kept). Its snapshot's folder here."""
    from baudelib import models
    _, where = remote_where(member, repo, "mlx")
    root = os.path.join(models.hf_hub(), "models--" + repo.replace("/", "--"))
    rev = where["revision"] if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", where.get("revision") or "") else "main"
    snap = os.path.join(root, "snapshots", rev)
    total, done = where["size"], 0
    for f in where["files"]:
        rel = f["name"]
        if not FILE_RE.fullmatch(rel) or ".." in rel.split("/"):
            raise ValueError(f"{rel!r} isn't a file to write here")
        dest = os.path.join(snap, rel)
        if os.path.isfile(dest) and os.path.getsize(dest) == f["size"]:
            done += f["size"]
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with _get(member, f"/mlx/{repo}/{rel}", timeout=600) as r, open(dest + ".part", "wb") as out:
            while b := r.read(CHUNK):
                if stopped():
                    raise ValueError(f"stopped at {rel} ({done / 1e9:.1f} of {total / 1e9:.1f} GB copied)")
                out.write(b)
                done += len(b)
                if progress:
                    progress(done, total, rel)
        if os.path.getsize(dest + ".part") != f["size"]:
            raise ValueError(f"{rel} came short ({os.path.getsize(dest + '.part'):,} of {f['size']:,} bytes)")
        os.replace(dest + ".part", dest)
    os.makedirs(os.path.join(root, "refs"), exist_ok=True)
    with open(os.path.join(root, "refs", "main"), "w") as f:
        f.write(rev)
    return snap
