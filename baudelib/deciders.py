"""Deciders: the decision models a host knows, as it knows its chat models' servers (baudelib.providers). A decider is
a server that answers Strands Decider's System One API (`strands-decider serve <model> --port 8000`, on this machine or
any it reaches): POST /v1/systemone {state, questions: {name: {type: choice | noul | score, instructions, criteria}},
images?} -> {model, answers: {name: {type, choice | noul | score, probabilities, legend, confidence}}, usage,
latency_ms}, and GET /health. Ollama's and MLX's chat models aren't deciders: only a server that answers that is.

Kept in deciders.json in the host's state folder, [{id, name, url}], added, removed and tested from Settings →
Dependencies on every app (the deciders op). Whether each answers is looked at again every MAX_AGE seconds as it's
asked for, and said with it: to heads (deciders), and to the mesh in this host's record (published: no URL), so a
task's variable bound to one that isn't answering is warned of before it runs. A task's script asks one with decide()
(baudetask), through its host: one of this host's is asked here, another member's there, through the mesh, as that
member's access rules let it (providers). Nothing here picks another decider when one can't answer: the script is
told (Unavailable) and its own code decides what to do."""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from baudelib import wire, wording

FILE = "deciders.json"
MAX = 32  # (a member's deciders taken from its record)
MAX_AGE = 15  # (seconds whether one answers is kept before it's looked at again)
TIMEOUT = 2.0  # (a look at /health)
DECIDE_S = wire.CONSTANTS["INFER_S"]  # (a decision: its server's first may load the model)
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,30}")
ANSWER_KEYS = ("type", "choice", "noul", "score", "probabilities", "legend", "confidence")


class Unavailable(Exception):
    """The decider can't be asked now (not running, not answering, not there): why."""


def slug(name):
    """An id for a decider named name: its words in lower case, joined by -."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:31] or "decider"


def clean_url(url):
    """A decider's base URL as given (http(s)://host:port, a path kept), its /v1/systemone or a trailing / taken off;
    None if it isn't one."""
    if not isinstance(url, str):
        return None
    url = url.strip().rstrip("/")
    url = re.sub(r"/v1(/systemone)?$", "", url)
    u = urllib.parse.urlsplit(url)
    return url if u.scheme in ("http", "https") and u.hostname else None


def _direct(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    local = host in ("127.0.0.1", "localhost", "::1") or host.endswith(".localhost")
    return urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()


def fetch(url, body=None, timeout=TIMEOUT):
    """The server's JSON answer (body: POSTed as JSON). OSError if it doesn't answer, said as providers says it
    ("isn't running at …"); ValueError (with what it said) for a 4xx, a question it won't take; OSError for a 5xx."""
    from baudelib.providers import unreachable
    base = re.sub(r"/(health|v1/systemone)$", "", url)  # (said as it's set)
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with _direct(url).open(req, timeout=timeout) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read() or b"{}").get("detail")
        except (ValueError, AttributeError, OSError):
            said = None
        text = said if isinstance(said, str) else json.dumps(said) if said else e.reason
        if 400 <= e.code < 500 and e.code not in (404, 408, 429):
            raise ValueError(f"{e.code}: {text}") from None
        raise OSError(unreachable(base, f"{e.code}: {text}")) from None
    except (urllib.error.URLError, OSError) as e:
        raise OSError(unreachable(base, getattr(e, "reason", e))) from None
    try:
        return json.loads(data)
    except ValueError:
        raise OSError(wording.say("deciders.not_decider", url=base)) from None


def look(entry, timeout=TIMEOUT):
    """Whether entry's server answers as a decider now: {reachable, model, error}."""
    try:
        health = fetch(entry["url"] + "/health", timeout=timeout)
    except (OSError, ValueError) as e:
        return {"reachable": False, "model": None, "error": str(e)}
    if not isinstance(health, dict) or health.get("status") not in ("ok", None):
        return {"reachable": False, "model": None, "error": wording.say("deciders.not_decider", url=entry["url"])}
    model = health.get("model") or health.get("checkpoint")
    return {"reachable": True, "model": str(model)[:200] if model else None, "error": None}


def clean_answers(answers):
    """A server's answers, by question, with what the wire has of each (Decision.json)."""
    out = {}
    for name, a in (answers or {}).items() if isinstance(answers, dict) else ():
        if isinstance(name, str) and isinstance(a, dict):
            one = {k: a[k] for k in ANSWER_KEYS if k in a}
            for k in ("probabilities", "legend"):
                if not isinstance(one.get(k), dict):
                    one.pop(k, None)
            out[name] = one
    return out


class Deciders:
    """A host's deciders (state_dir's deciders.json) and whether each answers, as last looked at."""

    def __init__(self, state_dir):
        self.path = os.path.join(state_dir, FILE)
        self.lock = threading.Lock()
        self.status = {}  # (id -> {reachable, model, error, at})
        try:
            with open(self.path, encoding="utf-8") as f:
                kept = json.load(f)
        except (OSError, ValueError):
            kept = []
        self.kept = []
        for e in kept if isinstance(kept, list) else []:
            url = clean_url(e.get("url")) if isinstance(e, dict) else None
            if url and isinstance(e.get("id"), str) and ID_RE.fullmatch(e["id"]):
                self.kept.append({"id": e["id"], "name": str(e.get("name") or e["id"])[:64], "url": url})

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.kept, f, indent=1)
        os.replace(tmp, self.path)

    def entries(self):
        with self.lock:
            return [dict(e) for e in self.kept]

    def find(self, name):
        """One of this host's, by its id or its name (any case); None if there's none."""
        if not isinstance(name, str):
            return None
        low = name.strip().lower()
        with self.lock:
            return next((dict(e) for e in self.kept if e["id"] == low or e["name"].lower() == low), None)

    def add(self, name, url):
        """A decider kept (its id made from its name), looked at at once: its entry with what was found. ValueError
        for a name or URL that won't do."""
        name = (name or "").strip()[:64]
        base = clean_url(url)
        if not name:
            raise ValueError(wording.say("deciders.named"))
        if base is None:
            raise ValueError(wording.say("deciders.bad_url"))
        with self.lock:
            if any(e["name"].lower() == name.lower() for e in self.kept):
                raise ValueError(wording.say("deciders.taken", name=name))
            ids, sid, n = {e["id"] for e in self.kept}, slug(name), 2
            eid = sid
            while eid in ids:
                eid, n = f"{sid[:28]}-{n}", n + 1
            entry = {"id": eid, "name": name, "url": base}
            self.kept.append(entry)
            self.save()
        return self.test(eid)

    def remove(self, eid):
        with self.lock:
            was = len(self.kept)
            self.kept = [e for e in self.kept if e["id"] != eid]
            self.status.pop(eid, None)
            if len(self.kept) != was:
                self.save()
            return len(self.kept) != was

    def test(self, eid):
        """One of this host's looked at now: shown's, or None if there's none."""
        e = next((e for e in self.entries() if e["id"] == eid), None)
        if e is None:
            return None
        self._took(e["id"], look(e))
        return self.shown(e)

    def _took(self, eid, found):
        with self.lock:
            self.status[eid] = {**found, "at": time.monotonic()}

    def shown(self, e):
        """An entry as heads are told of it (Decider.json): this host's own, with its URL."""
        with self.lock:
            st = self.status.get(e["id"]) or {}
        return {"value": e["id"], "id": e["id"], "name": e["name"], "url": e["url"], "reachable": bool(st.get("reachable")),
                "model": st.get("model"), "error": st.get("error")}

    def listing(self, fresh=False):
        """Every one of this host's, looked at again if it's MAX_AGE old (fresh: now), each at once on a thread of its
        own: [Decider]."""
        now, entries = time.monotonic(), self.entries()
        with self.lock:
            stale = [e for e in entries if fresh or now - (self.status.get(e["id"]) or {}).get("at", -1e9) >= MAX_AGE]
        threads = [threading.Thread(target=lambda e=e: self._took(e["id"], look(e)), daemon=True, name="decider-look")
                   for e in stale]
        for t in threads:
            t.start()
        for t in threads:
            t.join(TIMEOUT * 2 + 1)
        return [self.shown(e) for e in entries]

    def published(self, listed=None):
        """This host's, as the mesh is told of them: no URL."""
        return [{"id": d["id"], "name": d["name"], "reachable": d["reachable"],
                 **{k: d[k] for k in ("model", "error") if d.get(k)}}
                for d in (self.listing() if listed is None else listed)]

    def decide(self, entry, ask):
        """ask (a decide request's state, questions, images) put to entry's server: {model, answers, latency_ms}.
        Unavailable if it can't be asked; ValueError for a question it won't take."""
        body = {"state": ask.get("state", ""), "questions": ask.get("questions") or {}}
        if ask.get("images"):
            body["images"] = ask["images"]
        started = time.monotonic()
        try:
            got = fetch(entry["url"] + "/v1/systemone", body, timeout=DECIDE_S)
        except OSError as e:
            self._took(entry["id"], {"reachable": False, "model": None, "error": str(e)})
            raise Unavailable(str(e)) from None
        if not isinstance(got, dict) or not isinstance(got.get("answers"), dict):
            raise Unavailable(wording.say("deciders.not_decider", url=entry["url"]))
        with self.lock:
            was = self.status.get(entry["id"]) or {}
            self.status[entry["id"]] = {"reachable": True, "model": got.get("model") or was.get("model"), "error": None,
                                        "at": time.monotonic()}
        latency = got.get("latency_ms")
        return {"model": str(got.get("model") or ""), "answers": clean_answers(got["answers"]),
                "latency_ms": float(latency) if isinstance(latency, (int, float)) else
                round((time.monotonic() - started) * 1000, 2)}


def from_record(raw):
    """A member's deciders as its record said them, cut to size."""
    out = []
    for d in raw[:MAX] if isinstance(raw, list) else ():
        if isinstance(d, dict) and isinstance(d.get("id"), str) and ID_RE.fullmatch(d["id"]):
            out.append({"id": d["id"], "name": str(d.get("name") or d["id"])[:64], "reachable": d.get("reachable") is True,
                        **({"model": d["model"][:200]} if isinstance(d.get("model"), str) else {}),
                        **({"error": d["error"][:300]} if isinstance(d.get("error"), str) else {})})
    return out
