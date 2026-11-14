"""Inference providers: where the models besides Claude's run, and what each has. Claude isn't one: claude runs
Claude's models on the machine it's on, wherever its models are served.

On this machine (found): Apple's on-device model (apple-claude; on macOS), MLX (pi-claude's own server, on the models
pi-claude/models.json names that are downloaded), Ollama (its server: settings.json's ollama_url, else OLLAMA_HOST,
else 127.0.0.1:11434), and the OpenAI-compatible servers settings.json lists as endpoints (LM Studio, llama.cpp's
llama-server, vLLM, ...: [{"id", "name", "url" (its /v1), "key"?: "$NAME" for an environment variable's, or the key,
"window"?: its context window}]). Ollama's models and the endpoints' run in pi-claude, as MLX's do: pi speaks their API
(openai-completions). Another host's in the mesh are what baudelib.mesh carries: one that serves HTTP is an endpoint
here too (mesh_endpoints: its host process's proxy on this machine), run as this machine's are.

A model's value says its provider: "apple", an MLX one's own (mlx-qwen3-4b), "ollama/<name>", and
"openai/<endpoint id>/<model id>"; Claude's are the rest (provider_of).

What's found is kept MAX_AGE seconds and looked for again on a thread of its own (found), so a window asking never
waits on a server that doesn't answer; listen(fn) hears when it changes."""
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from baudelib import models as modelslib
from baudelib.selfupdate import settings_path

OLLAMA_PORT = 11434
MAX_AGE = 15  # (seconds what's found is kept before it's looked for again)
TIMEOUT = 2.0  # (a server's answer to a look: a server on this machine answers at once, or isn't there)
WAIT = 4.0  # (what found(wait=True) waits for a look, the first time)
DEFAULT_WINDOW = 32768  # (an OpenAI-compatible server's context window, till it or settings.json say)
# Ollama's own context window where nothing sets one (OLLAMA_CONTEXT_LENGTH, the Ollama app's slider, a model's
# num_ctx): 4k on a machine with less than 24 GB for models. A loaded model's (its /api/ps) is what it has
OLLAMA_WINDOW = 4096
NAMES = {"apple": "Apple", "mlx": "MLX", "ollama": "Ollama"}
ORDER = ("apple", "mlx", "ollama")  # (then the endpoints, as settings.json lists them)
EMBED_RE = re.compile(r"embed|rerank|whisper|tts", re.I)  # (an endpoint's models that aren't for talking to)
ENDPOINT_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,30}")


def provider_of(value):
    """A model's provider, by its value: "apple", "mlx", "ollama", "openai/<id>"; "agent/<member>/<its there>" for one
    run on another host as an agent; None for Claude's."""
    if not isinstance(value, str) or not value:
        return None
    if (agent := modelslib.proxied(value)) is not None:
        return f"{modelslib.AGENT}{agent[0]}/{'apple' if agent[1] == modelslib.APPLE_MODEL['value'] else 'mlx'}"
    if value == modelslib.APPLE_MODEL["value"]:
        return "apple"
    if any(m["value"] == value for m in modelslib.mlx_models()):
        return "mlx"
    if value.startswith("ollama/"):
        return "ollama"
    if value.startswith("openai/") and value.count("/") >= 2:
        return "openai/" + value.split("/")[1]
    return None


def provider_name(pid, settings=None):
    """A provider as it's shown: Ollama, MLX, Apple, an endpoint's name (another host's in the mesh: studio / Ollama)."""
    if pid in NAMES:
        return NAMES[pid]
    if isinstance(pid, str) and pid.startswith(modelslib.AGENT):  # (another host's, run there: studio / Apple)
        member, _, inner = pid[len(modelslib.AGENT):].partition("/")
        host = next((m["host"] for m in mesh_agents() if m["provider"] == pid), member[:8])
        return f"{host} / {NAMES.get(inner, inner)}"
    if isinstance(pid, str) and pid.startswith("openai/"):
        ep = next((e for e in endpoints(settings) + mesh_endpoints() if e["id"] == pid[len("openai/"):]), None)
        return ep["name"] if ep else pid[len("openai/"):]
    return str(pid)


def model_name(value):
    """A model's name, known or not, the same whatever runs it and wherever: "qwen3:4b" for "ollama/qwen3:4b". Its
    provider and host are the provider picker's to say."""
    for p in found():
        for m in p["models"]:
            if m["value"] == value:
                return m["displayName"]
    if (agent := modelslib.proxied(value)) is not None:  # (another host's, run there)
        there = next((m for m in mesh_agents() if m["value"] == value), None)
        return there["displayName"] if there else model_name(agent[1])
    pid = provider_of(value)
    if pid == "ollama":
        return value[len("ollama/"):]
    if pid and pid.startswith("openai/"):
        return value.split("/", 2)[2]
    return value


def entries():
    """Every model this machine knows, by value: found's, then other hosts' in the mesh (mesh_endpoints', as they're
    run here through its proxy, and mesh_agents')."""
    out = {}
    for p in found():
        for m in p["models"]:
            out.setdefault(m["value"], m)
    for e in mesh_endpoints():
        for m in e["models"]:
            out.setdefault(f"openai/{e['id']}/{m['id']}", m | {"value": f"openai/{e['id']}/{m['id']}"})
    for m in mesh_agents():
        out.setdefault(m["value"], m)
    return out


def kind(m):
    """What makes a model the same one on every host that has it: what it's served as (its repo, its name to its
    server; else its name; Apple's, with no runtime, by its name), its window and its version, where they're said."""
    served = (None if not has_runtime(m.get("provider") or provider_of(m["value"])) else
              m.get("resolvedModel") or m.get("id")) or m.get("displayName") or m["value"]
    return served, m.get("window"), m.get("version")


def alike(models, first=()):
    """Models as one of each kind (each host's Apple model, say): [(the one listed, every one of its kind)], in
    models' order. The one listed is the first of first (values) that's of its kind, else its kind's first."""
    kinds = {}
    for m in models:
        kinds.setdefault(kind(m), []).append(m)
    return [(next((m for v in first for m in ms if m["value"] == v), ms[0]), ms) for ms in kinds.values()]


def told_apart(m, listed):
    """A model's name, with its window and version after it where another of listed (one of each kind) has its name:
    Apple Foundation Model (32k, v3), qwen3:8b (16k). Just its name where none does."""
    name = m.get("displayName") or m["value"]
    if sum((x.get("displayName") or x["value"]) == name for x in listed) < 2:
        return name
    tags = [f"{m['window'] // 1024}k" if isinstance(m.get("window"), int) else None,
            f"v{m['version']}" if m.get("version") else None]
    return f"{name} ({', '.join(t for t in tags if t)})" if any(tags) else name


def has_runtime(pid):
    """Is a provider one to pick, a runtime its models are served by (MLX's, Ollama, an endpoint)? Apple's on-device
    model has none, here or on another host (agent/<member>/apple): macOS runs it, as claude runs Claude's, so it's
    listed with Claude's."""
    return bool(pid) and pid != "apple" and not (pid.startswith(modelslib.AGENT) and pid.endswith("/apple"))


def where(pid):
    """(its host's name, None for this machine; its own name) of a provider: ("studio", "Ollama") for another host's in
    the mesh, (None, "MLX") for this machine's."""
    if isinstance(pid, str) and pid.startswith(modelslib.AGENT):  # (another host's, run there as an agent)
        member, _, inner = pid[len(modelslib.AGENT):].partition("/")
        host = next((m["host"] for m in mesh_agents() if m["provider"] == pid), member[:8])
        return host, NAMES.get(inner, inner)
    if isinstance(pid, str) and pid.startswith("openai/"):
        eid = pid[len("openai/"):]
        ep = next((e for e in mesh_endpoints() if e["id"] == eid), None)
        if ep is not None:
            return ep["host"], ep["provider"] or ep["host"]
        if m := re.fullmatch(r"mesh-([a-z0-9]+)-(.+)", eid):  # (a member gone from the mesh: by what its id says)
            return m[1], m[2]
    return None, provider_name(pid)


def elsewhere(p, member):
    """The models of another host's provider p (network().providers()'s; member: that host's id) as a chat on this
    host runs them, without moving: through the mesh's proxy here, as an endpoint's (mesh.endpoint_id), where it
    serves an API; else run there, as an agent whose tools are here (agent/<member>/<its value>: agentproxy)."""
    from baudelib import mesh
    keep = ("id", "tools", "window", "version", "input", "thinking")
    if p.get("http"):
        pid = f"openai/{mesh.endpoint_id(member, p['id'])}"
        return pid, [{"value": f"{pid}/{m.get('id') or m['value']}", "provider": pid,
                      "displayName": m.get("displayName") or m["value"]} | {k: m[k] for k in keep if k in m}
                     for m in p.get("models") or []]
    pid = f"{modelslib.AGENT}{member}/{p['id']}"
    return pid, [{"value": f"{modelslib.AGENT}{member}/{m['value']}", "provider": pid,
                  "displayName": m.get("displayName") or m["value"]} | {k: m[k] for k in keep if k in m}
                 for m in p.get("models") or []]


def rank(pid):
    """Where a provider's listed: Apple's, MLX, Ollama, then the endpoints."""
    return (ORDER.index(pid), "") if pid in ORDER else (len(ORDER), str(pid))


# ---- settings.json's

def settings():
    try:
        with open(settings_path()) as f:
            out = json.load(f)
        return out if isinstance(out, dict) else {}
    except (OSError, ValueError):
        return {}


def endpoints(st=None):
    """settings.json's endpoints, those with an id and a url: [{"id", "name", "url", "key"?, "window"?}]."""
    st = settings() if st is None else st
    out = []
    for e in st.get("endpoints") or []:
        if isinstance(e, dict) and isinstance(e.get("id"), str) and ENDPOINT_ID_RE.fullmatch(e["id"]) and \
                isinstance(e.get("url"), str) and e["url"].startswith(("http://", "https://")):
            out.append({"id": e["id"], "name": str(e.get("name") or e["id"]), "url": e["url"].rstrip("/"),
                        "key": e.get("key") if isinstance(e.get("key"), str) else None,
                        "window": e["window"] if isinstance(e.get("window"), int) and e["window"] > 0 else None})
    return out


def mesh_endpoints():
    """Other hosts' providers in the mesh that serve HTTP, as endpoints on this machine (baudelib.mesh.endpoints: its
    host process's proxy, which runs each request there): [{"id", "name" (studio / Ollama), "host" (studio),
    "provider" (Ollama), "url", "key", "window":
    None, "models": [{"id", "displayName", "window", "tools", "input"}]}]; [] with no mesh here."""
    try:
        from baudelib import mesh
        found = mesh.endpoints()
    except Exception:  # noqa: BLE001 (no mesh, or its file unreadable: none)
        return []
    out = []
    for e in found:
        if isinstance(e, dict) and isinstance(e.get("id"), str) and ENDPOINT_ID_RE.fullmatch(e["id"]) and \
                isinstance(e.get("url"), str) and e["url"].startswith(("http://", "https://")):
            provider, host = e.get("provider_name"), e.get("host_name")
            if not (isinstance(provider, str) and provider and isinstance(host, str) and host):  # (a host process
                # here that names them only as "Ollama on studio")
                provider, _, host = str(e.get("name") or e["id"]).rpartition(" on ")
            out.append({"id": e["id"], "name": f"{host} / {provider}" if provider else host, "host": host,
                        "provider": provider, "url": e["url"].rstrip("/"),
                        "key": e.get("key") if isinstance(e.get("key"), str) else None, "window": None,
                        "models": [m for m in e.get("models") or [] if isinstance(m, dict) and isinstance(m.get("id"), str)]})
    return out


def mesh_agents():
    """Other hosts' models in the mesh that serve no API (Apple's, MLX's), as this machine runs them: there, as agents
    whose tools are here (agentproxy): [{"value" (agent/<member>/<its value>), "displayName", "provider", "host"
    (its name), "providerName", "tools", "window", "input", "thinking"}]; [] with no mesh here."""
    try:
        from baudelib import mesh
        found = mesh.agents()
    except Exception:  # noqa: BLE001 (no mesh, or its file unreadable: none)
        return []
    out = []
    for a in found:
        pid, models = elsewhere({"id": str(a.get("provider") or ""), "models": [
            m for m in a.get("models") or [] if isinstance(m, dict) and isinstance(m.get("value"), str)]}, a["host"])
        host = str(a.get("host_name") or a["host"][:8])
        out += [m | {"host": host, "providerName": f"{host} / {a.get('provider_name') or a.get('provider')}"}
                for m in models]
    return out


def key_of(ep):
    """An endpoint's key: "$NAME" or "${NAME}", that environment variable's; else as it's written; None for none."""
    k = (ep or {}).get("key")
    if not k:
        return None
    m = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", k)
    return os.environ.get(m[1]) if m else k


def ollama_url(st=None):
    """Where Ollama is: settings.json's ollama_url, else OLLAMA_HOST, else 127.0.0.1:11434; read as Ollama reads
    OLLAMA_HOST: host, host:port, or a URL (its port 80 or 443 if it gives none, and its path kept). 0.0.0.0, where it
    listens on every address, is reached at 127.0.0.1."""
    st = settings() if st is None else st
    raw = (st.get("ollama_url") if isinstance(st.get("ollama_url"), str) else None) or os.environ.get("OLLAMA_HOST") or ""
    raw = raw.strip().rstrip("/")
    if not raw:
        return f"http://127.0.0.1:{OLLAMA_PORT}"
    u = urllib.parse.urlsplit(raw if "://" in raw else "http://" + raw)
    host = u.hostname or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    try:
        port = u.port or (OLLAMA_PORT if "://" not in raw else 443 if u.scheme == "https" else 80)
    except ValueError:
        port = OLLAMA_PORT
    return f"{u.scheme or 'http'}://{'[' + host + ']' if ':' in host else host}:{port}{u.path.rstrip('/')}"


# ---- talking to a server

_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # (this machine's: never through a proxy)


def local_url(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1") or host.endswith(".localhost")


def fetch(url, body=None, timeout=TIMEOUT, key=None, method=None):
    """A server's JSON answer to url (body: JSON POSTed; method: another verb), or OSError (ValueError: not JSON).
    An HTTP error's raised as OSError saying what the server said."""
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with (_DIRECT if local_url(url) else urllib.request.build_opener()).open(req, timeout=timeout) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read() or b"{}").get("error")
        except (ValueError, AttributeError, OSError):
            said = None
        said = said.get("message") if isinstance(said, dict) else said
        raise OSError(f"{e.code}: {said or e.reason}") from None
    except urllib.error.URLError as e:
        raise OSError(str(e.reason)) from None
    return json.loads(data) if data.strip() else None


def unreachable(url, e):
    """Why a server didn't answer, said after its name: "isn't running at http://…"."""
    text = str(e)
    if "refused" in text.lower():
        return f"isn't running at {url}"
    if "timed out" in text.lower():
        return f"isn't answering at {url}"
    return f"at {url} said: {text}"


# ---- Ollama

_SHOWN = {}  # (Ollama's /api/show of a model, by its digest: what it can do, its window)


def ollama_show(url, name, digest=None, timeout=TIMEOUT):
    """Ollama's /api/show for a model (kept by digest), {} if it didn't say."""
    if digest and digest in _SHOWN:
        return _SHOWN[digest]
    try:
        shown = fetch(url + "/api/show", {"model": name}, timeout=timeout) or {}
    except (OSError, ValueError):
        return {}
    if digest:
        _SHOWN[digest] = shown
    return shown


def ollama_loaded(url, timeout=TIMEOUT):
    """The models Ollama has loaded now (/api/ps): {name: its context window}."""
    try:
        ps = fetch(url + "/api/ps", timeout=timeout) or {}
    except (OSError, ValueError):
        return {}
    return {m.get("name") or m.get("model"): m.get("context_length") for m in ps.get("models") or []
            if isinstance(m, dict)}


def ollama_window(shown, loaded=None):
    """A model's context window in Ollama: the one it's loaded with; else its own num_ctx; else
    OLLAMA_CONTEXT_LENGTH where it's set here; else Ollama's least (OLLAMA_WINDOW): no more than it was trained on."""
    trained = next((v for k, v in (shown.get("model_info") or {}).items()
                    if k.endswith(".context_length") and isinstance(v, int)), None)
    if isinstance(loaded, int) and loaded > 0:
        return loaded
    m = re.search(r"^num_ctx\s+(\d+)", shown.get("parameters") or "", re.M)
    env = os.environ.get("OLLAMA_CONTEXT_LENGTH", "")
    window = int(m[1]) if m else int(env) if env.isdigit() and int(env) > 0 else OLLAMA_WINDOW
    return min(window, trained) if trained else window


def ollama_entry(name, shown, loaded=None, size=None):
    """An Ollama model as a model entry (initialize's, with its provider, whether it calls tools, what it sees and its
    window); None for one that only embeds."""
    caps = shown.get("capabilities") or ["completion"]
    if "completion" not in caps:
        return None
    entry = {"value": f"ollama/{name}", "resolvedModel": name, "displayName": name,
             "provider": "ollama", "providerName": "Ollama", "tools": "tools" in caps,
             "input": ["text", "image"] if "vision" in caps else ["text"], "window": ollama_window(shown, loaded)}
    if "thinking" in caps:
        entry |= {"thinking": True, "supportsEffort": True, "supportedEffortLevels": ["off", "low", "medium", "high"]}
    if size:
        entry["size"] = size
    return entry


def ollama(st=None, timeout=TIMEOUT):
    """Ollama on this machine: {"id": "ollama", "name", "url", "version", "models", "error"} (error: why it isn't
    there, None if it is)."""
    url = ollama_url(st)
    out = {"id": "ollama", "name": "Ollama", "url": url, "version": None, "models": [], "error": None}
    try:
        out["version"] = (fetch(url + "/api/version", timeout=timeout) or {}).get("version")
        tags = fetch(url + "/api/tags", timeout=timeout) or {}
    except (OSError, ValueError) as e:
        out["error"] = unreachable(url, e)
        return out
    loaded = ollama_loaded(url, timeout)
    for m in tags.get("models") or []:
        name = isinstance(m, dict) and (m.get("name") or m.get("model"))
        if not name:
            continue
        entry = ollama_entry(name, ollama_show(url, name, m.get("digest"), timeout), loaded.get(name), m.get("size"))
        if entry is not None:
            out["models"].append(entry)
    return out


# ---- OpenAI-compatible servers

def endpoint_window(listed, ep, props=None):
    """A model's window at an endpoint: settings.json's for it; else what the server says of the model (vLLM's
    max_model_len, LM Studio's loaded_context_length, ...), or of itself (llama-server's /props n_ctx); else
    DEFAULT_WINDOW."""
    if ep.get("window"):
        return ep["window"]
    for k in ("loaded_context_length", "max_model_len", "context_length", "context_window", "max_context_length"):
        if isinstance(listed.get(k), int) and listed[k] > 0:
            return listed[k]
    n = ((props or {}).get("default_generation_settings") or {}).get("n_ctx")
    return n if isinstance(n, int) and n > 0 else DEFAULT_WINDOW


def endpoint(ep, timeout=TIMEOUT):
    """An OpenAI-compatible server settings.json lists: {"id": "openai/<id>", "name", "url", "models", "error"}."""
    pid = f"openai/{ep['id']}"
    out = {"id": pid, "name": ep["name"], "url": ep["url"], "models": [], "error": None}
    try:
        listed = fetch(ep["url"] + "/models", key=key_of(ep), timeout=timeout) or {}
    except (OSError, ValueError) as e:
        out["error"] = unreachable(ep["url"], e)
        return out
    if not isinstance(listed, dict) or not isinstance(listed.get("data"), list):
        out["error"] = f"at {ep['url']} didn't list models as OpenAI's API does"
        return out
    props = None
    try:  # (llama-server says its window at /props, beside /v1)
        props = fetch(re.sub(r"/v1$", "", ep["url"]) + "/props", timeout=timeout) if ep["url"].endswith("/v1") \
            and not ep.get("window") else None
    except (OSError, ValueError):
        pass
    for m in listed.get("data") or []:
        mid = isinstance(m, dict) and m.get("id")
        if not isinstance(mid, str) or not mid or EMBED_RE.search(mid):
            continue
        out["models"].append({"value": f"openai/{ep['id']}/{mid}", "resolvedModel": mid,
                              "displayName": mid, "provider": pid, "providerName": ep["name"],
                              "tools": True, "input": ["text"], "window": endpoint_window(m, ep, props)})
    return out


# ---- this machine's providers

def pi_installed():
    pi = os.environ.get("BAUDE_PI") or os.path.expanduser("~/.local/share/baude/pi/node_modules/.bin/pi")
    return os.access(pi, os.X_OK)


def look_here():
    """The providers found on disk, at once: Apple's (macOS) and MLX's."""
    out = []
    if sys.platform == "darwin":
        out.append({"id": "apple", "name": "Apple", "models": [modelslib.APPLE_MODEL | {"provider": "apple",
                                                                                         "providerName": "Apple"}],
                    "error": None if modelslib.apple_exe() else "apple-claude isn't built"})
    mlx = [m | {"provider": "mlx", "providerName": "MLX"} for m in modelslib.local_models()]
    out.append({"id": "mlx", "name": "MLX", "models": mlx, "error": None if pi_installed() else "pi isn't installed"})
    return out


def look():
    """This machine's providers, looked for now: [{"id", "name", "models", "error", ...}], in ORDER, the endpoints
    last. One with models and no error can run them (pi-claude needs pi, for all but Apple's)."""
    st, pi = settings(), pi_installed()
    out = look_here()
    found, threads = {}, []
    eps = endpoints(st)

    def one(key, fn, *args):
        found[key] = fn(*args)
    for key, fn, args in [("ollama", ollama, (st,)), *((e["id"], endpoint, (e,)) for e in eps)]:
        t = threading.Thread(target=one, args=(key, fn, *args), daemon=True)
        t.start()
        threads.append(t)
    deadline = time.monotonic() + TIMEOUT * 3 + 1
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    for key in ["ollama", *(e["id"] for e in eps)]:
        p = found.get(key)
        if p is None:
            continue
        if not p["error"] and not pi and p["models"]:
            p["error"] = "pi isn't installed"
        out.append(p)
    return out


_STATE = {"at": None, "providers": None, "looking": None, "started": 0.0}
_LOCK = threading.Lock()
_FIRST = threading.Event()  # (set once there's anything found: the quick part, look_here's, at once)
_LISTENERS = []


def listen(fn):
    """fn() whenever what's found changes (on the thread that looked)."""
    _LISTENERS.append(fn)


def unlisten(fn):
    if fn in _LISTENERS:
        _LISTENERS.remove(fn)


def _publish(now):
    with _LOCK:
        was, _STATE["providers"] = _STATE["providers"], now
    _FIRST.set()
    if _shape(now) != _shape(was):
        for fn in list(_LISTENERS):
            try:
                fn()
            except Exception:  # noqa: BLE001 (one listener's failure isn't another's)
                pass


def _look():
    if _STATE["providers"] is None:  # (the first look: what's on disk said at once, the servers' after)
        try:
            _publish(look_here())
        except Exception:  # noqa: BLE001 (the whole look says it)
            pass
    try:
        now = look()
    except Exception:  # noqa: BLE001 (a look that fails is tried again next time)
        now = None
    if now is not None:
        _publish(now)
    with _LOCK:
        _STATE["at"], _STATE["looking"] = time.monotonic(), None
    _FIRST.set()


def _shape(providers):
    """What a picker shows of providers (not a loaded model's window, which comes and goes with Ollama's keep-alive)."""
    return [(p["id"], p["error"], [(m["value"], m.get("tools")) for m in p["models"]]) for p in providers or []]


def found(wait=False, max_age=MAX_AGE):
    """This machine's providers as last found (look), looked for again on a thread once they're max_age old (0: now).
    wait: for what's found, at most WAIT seconds (or as many as it says) from when the look began: the first time,
    for what's on disk (Apple's, MLX's: at once; the servers' come after, listen); asked to be now (max_age 0), for
    the whole look. Every caller waits for the same look till the same moment."""
    with _LOCK:
        stale = _STATE["at"] is None or max_age == 0 or time.monotonic() - _STATE["at"] >= max_age
        if stale and _STATE["looking"] is None:
            _STATE["started"] = time.monotonic()
            t = _STATE["looking"] = threading.Thread(target=_look, daemon=True, name="providers")
            t.start()
        looking, have, started = _STATE["looking"], _STATE["providers"], _STATE["started"]
    if wait and looking is not None:
        left = max(0.0, started + (WAIT if wait is True else wait) - time.monotonic())
        if max_age == 0:
            looking.join(left)
        elif have is None:
            _FIRST.wait(left)
    return list(_STATE["providers"] or [])


def usable(providers=None, tools=False):
    """The providers that run models now: with models and no error (tools: those models only that call tools)."""
    out = []
    for p in found() if providers is None else providers:
        models = [m for m in p["models"] if not tools or m.get("tools", True)]
        if models and not p["error"]:
            out.append({**p, "models": models})
    return out


def local_providers():
    """This machine's providers that run models now, as another host is told of them (the mesh: plain JSON, no
    secrets): [{"id", "kind" (apple, mlx, ollama, openai), "name", "base_url" (its OpenAI-compatible API; None for one a
    program runs: Apple's, MLX's), "models": [{"value", "displayName", "id" (its name to its server), "tools",
    "window", "input", "thinking", "version" (where it's said)}]}]."""
    out = []
    for p in usable(found(wait=True)):
        kind = "openai" if p["id"].startswith("openai/") else p["id"]
        base = p["url"] + "/v1" if kind == "ollama" else p.get("url") if kind == "openai" else None
        out.append({"id": p["id"], "kind": kind, "name": p["name"], "base_url": base, "models": [
            {"value": m["value"], "displayName": m["displayName"], "id": m.get("resolvedModel") or m["value"],
             "tools": m.get("tools", True), "window": m.get("window"), "input": m.get("input") or ["text"],
             "thinking": bool(m.get("thinking") or m.get("supportsEffort"))} |
            ({"version": m["version"]} if isinstance(m.get("version"), str) else {}) for m in p["models"]]})
    return out


def endpoint_key(pid):
    """The key an endpoint of this machine's needs (local_providers' id: openai/<id>), for what forwards to it here;
    None for one that needs none. It never leaves this machine."""
    if not isinstance(pid, str) or not pid.startswith("openai/"):
        return None
    return key_of(next((e for e in endpoints() if e["id"] == pid[len("openai/"):]), None))


def group(models):
    """Models (initialize's, or a host's hello's) by provider: [(provider id, its name, [models])], Claude's and
    Apple's left out (has_runtime), in ORDER."""
    out = {}
    for m in models or []:
        if not isinstance(m, dict) or not m.get("value"):
            continue
        pid = m.get("provider") or provider_of(m["value"])
        if not has_runtime(pid):
            continue
        name = m.get("providerName") or provider_name(pid)
        out.setdefault(pid, (name, []))[1].append(m)
    return [(pid, name, ms) for pid, (name, ms) in sorted(out.items(), key=lambda kv: rank(kv[0]))]


# ---- setting models up (Meta's tools): Ollama's install and its models' health, MLX models downloaded, apple-claude

def ollama_install():
    """Where Ollama's installed here: {"exe": its command, "app": its app (macOS)}, None for either that isn't."""
    exe = shutil.which("ollama") or next((p for p in ("/usr/local/bin/ollama", "/opt/homebrew/bin/ollama")
                                          if os.access(p, os.X_OK)), None)
    app = next((p for p in ("/Applications/Ollama.app", os.path.expanduser("~/Applications/Ollama.app"))
                if os.path.isdir(p)), None) if sys.platform == "darwin" else None
    return {"exe": exe, "app": app}


CHECK_TIMEOUT = 300  # (s a model has to load and answer, checked)


def ollama_check(name, timeout=CHECK_TIMEOUT):
    """Whether one of Ollama's models works: (verdict, Ollama's words): "works"; "unhealthy" (it won't load or answer);
    "unsure" (Ollama didn't say what it is, or there wasn't the memory for it now: no fault of the model's). One that
    talks gives a one-token reply, one that embeds an embedding; one that wasn't loaded is unloaded again after."""
    url = ollama_url()
    was = name in ollama_loaded(url)
    shown = ollama_show(url, name)
    if not shown:
        return "unsure", "Ollama didn't say what it is"
    keep = {} if was else {"keep_alive": 0}
    talk = ("/api/generate", {"model": name, "prompt": "Say OK.", "stream": False, "options": {"num_predict": 1}})
    embed = ("/api/embed", {"model": name, "input": "OK"})
    tries = [talk, embed] if "completion" in (shown.get("capabilities") or ["completion"]) else [embed]
    error = None
    for path, body in tries:
        try:
            fetch(url + path, body | keep, timeout=timeout)
            return "works", None
        except (OSError, ValueError) as e:
            error = str(e)
            if "does not support generate" not in error:
                break
    if re.search(r"memory|out of memory|insufficient", error or "", re.I):
        return "unsure", error
    return "unhealthy", error


def ollama_delete(name):
    """One of Ollama's models removed (its weights too, where no other model shares them); OSError if it wasn't."""
    fetch(ollama_url() + "/api/delete", {"model": name}, method="DELETE")


def talks(snap):
    """Whether a downloaded model is one to talk to: it has a chat template (a speech or embedding model hasn't)."""
    if glob.glob(os.path.join(snap, "chat_template.*")):
        return True
    try:
        with open(os.path.join(snap, "tokenizer_config.json")) as f:
            return bool(json.load(f).get("chat_template"))
    except (OSError, ValueError, AttributeError):
        return False


def mlx_downloaded():
    """MLX models to talk to in the Hugging Face cache (modelslib.hf_hub), their weights there in full: [{"repo", "size"
    (bytes), "vision", "added" (pi-claude knows it: mlx_list)}]. An MLX model's config says how it's quantized, or its
    repo's name says MLX; one to talk to has a chat template (talks). Its snapshot is the one refs/main names, else the
    newest."""
    hub, out = modelslib.hf_hub(), []
    known = {m["repo"] for m in modelslib.mlx_list()}
    for d in sorted(os.listdir(hub)) if os.path.isdir(hub) else []:
        if not d.startswith("models--") or "--" not in d[len("models--"):]:
            continue
        try:
            found = one_downloaded(os.path.join(hub, d), d[len("models--"):].replace("--", "/", 1), known)
        except (OSError, ValueError):  # (changing as it's looked at: left out this time)
            continue
        if found is not None:
            out.append(found)
    return out


def one_downloaded(path, repo, known):
    """mlx_downloaded's entry for one repo's folder in the cache, or None."""
    try:
        with open(os.path.join(path, "refs", "main")) as f:
            snap = os.path.join(path, "snapshots", f.read().strip())
    except OSError:
        snaps = [p for p in glob.glob(os.path.join(path, "snapshots", "*")) if os.path.isdir(p)]
        snap = max(snaps, key=os.path.getmtime) if snaps else None
    if snap is None or not os.path.isdir(snap):
        return None
    with open(os.path.join(snap, "config.json")) as f:
        config = json.load(f)
    if not isinstance(config, dict) or not ("quantization" in config or "mlx" in repo.lower()) or not talks(snap):
        return None
    weights = [w for w in glob.glob(os.path.join(snap, "*.safetensors")) if os.path.exists(w)]
    try:
        with open(os.path.join(snap, "model.safetensors.index.json")) as f:
            shards = set((json.load(f).get("weight_map") or {}).values())
    except (OSError, ValueError, AttributeError):
        shards = set()
    have = {os.path.basename(w) for w in weights}
    if not have or not shards <= have:  # (none, or some still downloading)
        return None
    return {"repo": repo, "size": sum(os.path.getsize(w) for w in weights), "vision": "vision_config" in config,
            "added": repo in known}


APPLE_NEEDS = (26, 4)  # (the macOS apple-claude/Package.swift says it needs: FoundationModels' tools)
_BUILD = {"state": None, "lines": [], "started": None}  # (apple-claude's build, apple_build's: building, built, failed)
_BUILD_LOCK = threading.Lock()


def apple_status():
    """Apple's on-device model's adapter (apple-claude) here: {"built", "exe", "source", "swift", "macos", "fits" (this
    macOS runs it), "build": its build's state, "log": the build's last lines}."""
    mac = platform.mac_ver()[0] if sys.platform == "darwin" else ""
    try:
        fits = tuple(int(x) for x in (mac.split(".") + ["0"])[:2]) >= APPLE_NEEDS
    except ValueError:
        fits = False
    return {"built": bool(modelslib.apple_exe()), "exe": modelslib.apple_exe(),
            "source": os.path.isfile(os.path.join(modelslib.REPO, "apple-claude", "Package.swift")),
            "swift": shutil.which("swift"), "macos": mac, "fits": fits, "build": _BUILD["state"],
            "log": list(_BUILD["lines"][-12:])}


def apple_build(done=None):
    """apple-claude built (swift build -c release, in apple-claude/), on a thread of its own: its state in
    apple_status, done(ok) when it's over. False if it can't be (not macOS, no swift, already building)."""
    st = apple_status()
    with _BUILD_LOCK:
        if sys.platform != "darwin" or not st["swift"] or not st["source"] or _BUILD["state"] == "building":
            return False
        _BUILD.update(state="building", lines=[], started=time.time())

    def run():
        ok = False
        try:
            p = subprocess.Popen([st["swift"], "build", "-c", "release"], cwd=os.path.join(modelslib.REPO, "apple-claude"),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 errors="replace")
            for line in p.stdout:
                _BUILD["lines"] = (_BUILD["lines"] + [line.rstrip()])[-200:]
            ok = p.wait() == 0 and bool(modelslib.apple_exe())
        except Exception as e:  # noqa: BLE001 (whatever went wrong: said, and the build's over)
            _BUILD["lines"] = (_BUILD["lines"] + [str(e)])[-200:]
        finally:
            _BUILD["state"] = "built" if ok else "failed"
        found(max_age=0)  # (Apple's model, there now: the pickers)
        if done is not None:
            done(ok)
    threading.Thread(target=run, daemon=True, name="apple-claude-build").start()
    return True


# ---- what pi-claude runs a model with

def resolve(value, timeout=TIMEOUT):
    """How pi-claude runs a model of Ollama's or an endpoint's (value): {"value", "repo" (the id its server knows it
    by), "displayName", "input", "thinking", "tools", "window", "provider", "kind" ("ollama", "openai"), "url" (its
    server), "baseUrl" (its /v1), "apiKey"}; "error" too where its server isn't there, or doesn't have it. None for
    any other model."""
    pid = provider_of(value)
    if pid == "ollama":
        name, url = value[len("ollama/"):], ollama_url()
        out = {"value": value, "repo": name, "displayName": name, "input": ["text"], "thinking": False,
               "tools": True, "window": OLLAMA_WINDOW, "provider": "ollama", "kind": "ollama", "url": url,
               "baseUrl": url + "/v1", "apiKey": "ollama", "error": None}
        try:
            shown = fetch(url + "/api/show", {"model": name}, timeout=timeout) or {}
        except (OSError, ValueError) as e:
            text = str(e)
            out["error"] = f"Ollama doesn't have {name}" if "not found" in text else f"Ollama {unreachable(url, e)}"
            return out
        entry = ollama_entry(name, shown, ollama_loaded(url, timeout).get(name))
        if entry is None:
            out["error"] = f"{name} only embeds: there's nothing to talk to"
            return out
        return out | {k: entry[k] for k in ("input", "tools", "window")} | {"thinking": bool(entry.get("thinking"))}
    if pid and pid.startswith("openai/"):
        _, eid, mid = value.split("/", 2)
        ep = next((e for e in endpoints() + mesh_endpoints() if e["id"] == eid), None)
        if ep is None:
            return {"value": value, "repo": mid, "displayName": mid, "input": ["text"], "thinking": False,
                    "tools": True, "window": DEFAULT_WINDOW, "provider": pid, "kind": "openai", "url": "",
                    "baseUrl": "", "apiKey": "none", "error": f"there's no endpoint {eid} in settings.json"}
        if "models" in ep:  # (another host's, in the mesh: what it said of the model, its server asked there)
            listed, error = next((m for m in ep["models"] if m["id"] == mid), {}), None
        else:
            there = endpoint(ep, timeout)
            listed = next((m for m in there["models"] if m["resolvedModel"] == mid), {})
            error = there["error"] and f"{ep['name']} {there['error']}"
        return {"value": value, "repo": mid, "displayName": mid,
                "input": listed.get("input") or ["text"], "thinking": False, "tools": listed.get("tools", True),
                "window": listed.get("window") or ep.get("window") or DEFAULT_WINDOW, "provider": pid, "kind": "openai",
                "url": ep["url"], "baseUrl": ep["url"], "apiKey": key_of(ep) or "none", "error": error}
    return None
