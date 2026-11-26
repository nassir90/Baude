"""What runs a model. Claude's run in claude; Apple's on-device one in apple-claude (apple-claude/); those MLX, Ollama
or an OpenAI-compatible server serves on this machine in pi-claude (pi-claude/: pi on the server's API). Each speaks
claude's stream-json on stdin and stdout, and each is started, given an id and carried on differently: PROGRAMS says
how, for Baude and the headless server alike (launch). Where the models besides Claude's are, and which there are:
baudelib.providers."""
import contextlib
import glob
import json
import os
import re
import shutil
import time

from baudelib import icons, wording

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))  # (apple-claude/ and pi-claude/ are beside baudelib/)

# (beta) Apple's on-device model, through apple-claude (apple-claude/: a Swift stand-in for claude on FoundationModels,
# its one tool Exec, each command asked for): in the model picker after Claude's; Manual only, no effort
APPLE_MODEL = {"value": "apple", "resolvedModel": "apple-foundation-model", "displayName": "Apple Foundation Model"}


def apple_exe():
    """apple-claude: BAUDE_APPLE_CLAUDE, else as `swift build -c release` in apple-claude/ leaves it; None if not built."""
    exe = os.environ.get("BAUDE_APPLE_CLAUDE") or \
        os.path.join(REPO, "apple-claude", ".build", "release", "apple-claude")
    return exe if os.access(exe, os.X_OK) else None


# (beta) models on this Mac, through pi-claude (pi-claude/: pi, the coding agent, on a model mlx_lm.server or
# mlx_vlm.server serves, each tool call asked): those in pi-claude/models.json, and those settings.json adds
# (mlx_models: Meta's add_mlx_model), and any other found downloaded (mlx_found), after Apple's; Manual only, like Apple's, and no effort but a
# thinking level for those that think; one to another keeps the conversation (pi's session), to Claude's doesn't
PI_CLAUDE = os.path.join(REPO, "pi-claude")


EFFORT = {"supportsEffort": True, "supportedEffortLevels": ["off", "low", "medium", "high"]}  # (a model that thinks,
# run in pi-claude: its thinking levels)


def _entry(m):
    """An MLX model as models.json has it, as initialize lists it."""
    return {"value": m["value"], "resolvedModel": m["repo"], "displayName": m["displayName"]} | \
        (EFFORT if m.get("thinking") else {})


try:
    with open(os.path.join(PI_CLAUDE, "models.json")) as f:
        MLX_OWN = [m for m in json.load(f) if isinstance(m, dict) and m.get("value") and m.get("repo")]
except (OSError, ValueError):
    MLX_OWN = []
LOCAL_MODELS = [_entry(m) for m in MLX_OWN]  # (models.json's: mlx_models has settings.json's and those found too)
_ADDED = {"at": None, "list": []}


def mlx_added():
    """The MLX models settings.json adds (mlx_models), as models.json has them: read again only once it's changed."""
    from baudelib.selfupdate import settings_path
    try:
        st = os.stat(settings_path())
        at = (st.st_mtime_ns, st.st_size, st.st_ino)  # (a write and its undo can share a coarse timestamp)
    except OSError:
        at = None
    if at != _ADDED["at"]:
        try:
            with open(settings_path()) as f:
                found = json.load(f).get("mlx_models") or []
        except (OSError, ValueError, AttributeError):
            found = []
        own = {m["value"] for m in MLX_OWN}
        _ADDED.update(at=at, list=[m | {"displayName": m["displayName"].removesuffix(" (MLX)") or m["displayName"]}
                                   for m in found if isinstance(m, dict) and isinstance(m.get("value"), str) and
                                   isinstance(m.get("repo"), str) and m["value"] not in own and
                                   isinstance(m.get("displayName"), str)])  # (named as models.json's are: a
        # name ending in " (MLX)" without it, the provider picker's to say)
    return list(_ADDED["list"])


def mlx_list():
    """Every MLX model pi-claude knows, as models.json has them: its own, then settings.json's (mlx_added), then those
    found downloaded that neither names (mlx_found)."""
    named = MLX_OWN + [{"input": ["text"]} | m for m in mlx_added()]
    return named + mlx_found({m["repo"] for m in named}, {m["value"] for m in named})


def mlx_models():
    """mlx_list's models as initialize lists them."""
    return [_entry(m) for m in mlx_list()]


def hf_hub():
    """Where huggingface_hub keeps what it downloads (its HF_HUB_CACHE, HF_HOME, XDG_CACHE_HOME)."""
    return os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME") or os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                                                  "huggingface"), "hub")


_DIRS = {"at": None, "list": []}


def mlx_dirs():
    """The folders besides the Hugging Face cache where MLX models are looked for: LM Studio's (~/.lmstudio/models, its
    older ~/.cache/lm-studio/models, LMSTUDIO_MODELS_DIR), then settings.json's mlx_dirs (each a model's folder, or one
    holding them, two levels down at most)."""
    from baudelib.selfupdate import settings_path
    try:
        st = os.stat(settings_path())
        at = (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        at = None
    if at != _DIRS["at"]:
        try:
            with open(settings_path()) as f:
                extra = json.load(f).get("mlx_dirs") or []
        except (OSError, ValueError, AttributeError):
            extra = []
        _DIRS.update(at=at, list=[os.path.expanduser(d) for d in extra if isinstance(d, str) and d.strip()]
                     if isinstance(extra, list) else [])
    out = [os.environ.get("LMSTUDIO_MODELS_DIR") or "", os.path.expanduser("~/.lmstudio/models"),
           os.path.expanduser("~/.cache/lm-studio/models"), *_DIRS["list"]]
    seen, dirs = set(), []
    for d in out:
        if d and os.path.isdir(d) and os.path.realpath(d) not in seen:
            seen.add(os.path.realpath(d))
            dirs.append(d)
    return dirs


def talks(snap):
    """Whether a downloaded model is one to talk to: it has a chat template (a speech or embedding model hasn't)."""
    if glob.glob(os.path.join(snap, "chat_template.*")):
        return True
    try:
        with open(os.path.join(snap, "tokenizer_config.json")) as f:
            return bool(json.load(f).get("chat_template"))
    except (OSError, ValueError, AttributeError):
        return False


def mlx_folder(snap):
    """A model's folder as MLX loads it, if it's one to talk to with its weights there in full: {"size" (bytes),
    "vision"}, else None. Any safetensors model with a config and a chat template: mlx-vlm's server loads an MLX
    conversion and the original alike (GGUF, PyTorch's .bin: not MLX's)."""
    try:
        with open(os.path.join(snap, "config.json")) as f:
            config = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(config, dict) or not talks(snap):
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
    return {"size": sum(os.path.getsize(w) for w in weights), "vision": "vision_config" in config}


def hub_snapshot(path):
    """The snapshot of a repo's folder in the Hugging Face cache that refs/main names, else the newest; None if none."""
    try:
        with open(os.path.join(path, "refs", "main")) as f:
            snap = os.path.join(path, "snapshots", f.read().strip())
    except OSError:
        snaps = [p for p in glob.glob(os.path.join(path, "snapshots", "*")) if os.path.isdir(p)]
        snap = max(snaps, key=os.path.getmtime) if snaps else None
    return snap if snap and os.path.isdir(snap) else None


def scan_downloaded():
    """Every MLX model downloaded here: [{"repo" (its Hugging Face id, for one in the cache; its folder's path, for
    one elsewhere), "name", "size", "vision", "where" ("Hugging Face", "LM Studio", "folder")}]."""
    out, seen = [], set()

    def one(repo, snap, where):
        try:
            found = mlx_folder(snap)
        except OSError:  # (changing as it's looked at: left out this time)
            return
        real = os.path.realpath(snap)
        if found is not None and real not in seen:
            seen.add(real)
            out.append(found | {"repo": repo, "name": repo.rstrip("/").split("/")[-1], "where": where})

    def hub(root):
        for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            if d.startswith("models--") and "--" in d[len("models--"):]:
                if snap := hub_snapshot(os.path.join(root, d)):
                    one(d[len("models--"):].replace("--", "/", 1), snap, "Hugging Face")

    try:
        hub(hf_hub())
    except OSError:
        pass
    lm = {os.path.realpath(os.path.expanduser(d)) for d in ("~/.lmstudio/models", "~/.cache/lm-studio/models")}
    for root in mlx_dirs():
        where = "LM Studio" if os.path.realpath(root) in lm or "lmstudio" in root.lower().replace("-", "") else "folder"
        todo = [(root, 0)]
        while todo:
            d, depth = todo.pop(0)
            try:  # (one folder unreadable, or gone: the rest still looked in)
                names = sorted(os.listdir(d))
                if os.path.basename(d) == "hub" or any(n.startswith("models--") for n in names):
                    hub(d)  # (a Hugging Face cache, given as a folder)
                    continue
                if os.path.isfile(os.path.join(d, "config.json")):
                    one(d, d, where)
                    continue
                if depth < 2:
                    todo += [(os.path.join(d, n), depth + 1) for n in names
                             if not n.startswith(".") and os.path.isdir(os.path.join(d, n))]
            except OSError:
                continue
    return out


_FOUND = {"key": None, "at": 0.0, "list": []}
FOUND_AGE = 30  # (s a scan's kept, while none of the folders looked in changes)


def downloaded():
    """scan_downloaded's list, scanned again only once a folder it looks in has changed, or FOUND_AGE on."""
    key = []
    for d in [hf_hub(), *mlx_dirs()]:
        try:
            key.append((d, os.stat(d).st_mtime_ns))
        except OSError:
            key.append((d, None))
    if key != _FOUND["key"] or time.monotonic() - _FOUND["at"] > FOUND_AGE:
        try:
            _FOUND.update(list=scan_downloaded())
        except OSError:
            pass
        _FOUND.update(key=key, at=time.monotonic())
    return list(_FOUND["list"])


def mlx_found(named=(), taken=()):
    """The MLX models downloaded here that models.json and settings.json don't name (by repo: named), as models.json
    would have them: value mlx-<its name> (mlx-<its whole id>, where that's taken), named as its folder is."""
    out, taken = [], set(taken)
    slug = lambda s: "mlx-" + re.sub(r"[^a-z0-9.]+", "-", s.lower()).strip("-")  # noqa: E731
    # (the Hugging Face cache's first, whatever order they're scanned in: a name both have is its)
    for m in sorted(downloaded(), key=lambda m: m["where"] != "Hugging Face"):
        if m["repo"] in named:
            continue
        value = slug(m["name"])
        if value in taken:
            value = slug(f"{m['name']} {m['where']}")
        if value in taken:
            continue
        taken.add(value)
        out.append({"value": value, "repo": m["repo"], "displayName": m["name"],
                    "input": ["text", "image"] if m["vision"] else ["text"], "found": m["where"]})
    return out


def is_downloaded(repo):
    """Whether an MLX model's weights are here: its folder (a path), or its repo in the Hugging Face cache."""
    if os.path.isabs(os.path.expanduser(repo)):
        return os.path.isdir(os.path.expanduser(repo))
    return os.path.isdir(os.path.join(hf_hub(), "models--" + repo.replace("/", "--")))


def pi_path():
    return os.environ.get("BAUDE_PI") or os.path.expanduser("~/.local/share/baude/pi/node_modules/.bin/pi")


def mlx_server():
    """mlx-vlm's server (mlx_vlm.server), which pi-claude serves MLX models with; None if it isn't installed."""
    exe = shutil.which("mlx_vlm.server") or os.path.expanduser("~/.local/bin/mlx_vlm.server")
    return exe if os.access(exe, os.X_OK) else None


def mlx_missing():
    """What MLX's models need here and haven't, each with how it's put right: [] when they run."""
    out = []
    if not os.access(pi_path(), os.X_OK):
        out.append(wording.say("model_providers.pi_missing",
                               folder=os.path.dirname(os.path.dirname(os.path.dirname(pi_path())))))
    if not mlx_server():
        lm = shutil.which("mlx_lm.server") or os.access(os.path.expanduser("~/.local/bin/mlx_lm.server"), os.X_OK)
        out.append(wording.say("model_providers.mlx_vlm_missing_lm") if lm else
                   wording.say("model_providers.mlx_vlm_missing"))
    return out


def downloaded_models():
    """mlx_models whose weights are here (models.json's and settings.json's that are downloaded, and those found)."""
    return [m for m in mlx_models() if is_downloaded(m["resolvedModel"])]


def local_models():
    """downloaded_models, while they can run here (mlx_missing: pi, and mlx-vlm's server)."""
    return [] if mlx_missing() else downloaded_models()


SERVED = ("ollama/", "openai/")  # (the values of models Ollama or an OpenAI-compatible server serves: baudelib.providers)


AGENT = "agent/"  # (a model of another host's, run there as an agent of its: baudelib.agentproxy)


def proxied(value):
    """(the member of the mesh, its value there) of a model run on another host as an agent (agent/<member>/<value>),
    None for any other."""
    if not isinstance(value, str) or not value.startswith(AGENT):
        return None
    member, _, inner = value[len(AGENT):].partition("/")
    return (member, inner) if member and inner else None


def runs_on(value):
    """The member of the mesh a model's program runs on, as an agent of its (proxied); None for this machine."""
    return (proxied(value) or (None,))[0]


CODEX_MODES = [("default", wording.say("mode_effort.manual")), ("auto", wording.say("mode_effort.auto")),
               ("bypassPermissions", wording.say("mode_effort.codex_bypass"))]
CODEX_MODE_VALUES = frozenset(value for value, _label in CODEX_MODES)


def codex_mode(mode):
    return mode if isinstance(mode, str) and mode in CODEX_MODE_VALUES else "default"


CODEX_DEFAULT = {"value": "codex/default", "displayName": "Codex (default model)"}


def codex_choices(entries):
    """Adapt the visible Codex catalog to Baude's model picker."""
    choices = []
    seen = set()
    for entry in entries:
        model_id = entry.get("model") or entry.get("id")
        if not model_id or model_id == "default" or model_id in seen or entry.get("hidden"):
            continue
        seen.add(model_id)
        levels = [opt["reasoningEffort"] for opt in entry.get("supportedReasoningEfforts") or []
                  if opt.get("reasoningEffort")]
        name = entry.get("displayName") or model_id
        if name.upper().startswith("GPT-"):
            name = "GPT " + name[4:].replace("-", " ")
            prefix, sep, variant = name.rpartition(" ")
            if variant.lower() in ("sol", "astra", "terra", "luna"):
                name = prefix + sep + variant.title()
        choices.append({
            "value": f"codex/{model_id}", "resolvedModel": model_id,
            "displayName": name,
            "description": entry.get("description") or "",
            "supportsEffort": bool(levels), "supportedEffortLevels": levels,
            "defaultEffort": entry.get("defaultReasoningEffort"), "isDefault": bool(entry.get("isDefault")),
        })
    default = next((choice for choice in choices if choice["isDefault"]), None)
    alias = CODEX_DEFAULT if default is None else {**default, **CODEX_DEFAULT}
    return [alias, *choices]


def codex_effort(catalog, value, effort):
    """Use the selected model's own effort default when a prior setting does not apply."""
    choice = next((m for m in catalog if m["value"] == value), None)
    if choice is None or not choice.get("resolvedModel"):
        return effort
    levels = choice["supportedEffortLevels"]
    if effort in levels:
        return effort
    return choice.get("defaultEffort") if levels else None


def codex_exe():
    return shutil.which(os.environ.get("BAUDE_CODEX", "codex"))


def model_program(value):
    """The program a model runs on: Claude Code, Codex, apple-claude, or pi-claude."""
    if (agent := proxied(value)) is not None:  # (only a model with no API is run so: Apple's, else MLX's)
        return "apple" if agent[1] == APPLE_MODEL["value"] else "pi"
    if isinstance(value, str) and value.startswith("codex/"):
        return "codex"
    if value == APPLE_MODEL["value"]:
        return "apple"
    if isinstance(value, str) and value.startswith(SERVED):
        return "pi"
    # (an mlx- value not found now, its folder gone or not mounted: pi-claude's, to say it isn't downloaded)
    return "pi" if isinstance(value, str) and value.startswith("mlx-") or \
        any(m["value"] == value for m in mlx_models()) else "claude"


_LONG = re.compile(r"\[1m\]|\b1m\b")  # (a 1M-context variant: its own model, opus[1m] beside opus)
_HEAD = re.compile(r"[a-z]+ \d+(?:\.\d+)*(?: \(1m context\))?")  # (a description that starts with the name: "Opus 5.5 · …")
ALIASES = ("opus", "sonnet", "haiku", "fable")  # (claude's own aliases, each a word of its models' ids)


def _folded(name):
    """A Claude model's name as it's compared, without what says it's a 1M-context one: "Opus 5.5", "claude-opus-5-5"
    and "claude-opus-5-5-20261001" are "opus55". None for none."""
    if not isinstance(name, str) or not name.strip():
        return None
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", name.casefold()).strip()
    s = re.sub(r"^(?:[a-z]+\.)?(?:anthropic[./])?claude[-_ ]?", "", s)  # (us.anthropic.claude-…, claude-…)
    s = re.sub(r"[-_@ ]\d{8}$", "", re.sub(r"-v\d+(?::\d+)?$", "", s.strip()))  # (a snapshot's date; Bedrock's -v1:0)
    return re.sub(r"[^a-z0-9]", "", s) or None


def claude_names(m):
    """The names a model of Claude's goes by, as they're compared (_folded): its value (an alias, opus; or its id), what
    it resolves to, its name, and the name its description starts with ("Opus 5.5 · …"). A 1M-context one's (any of
    them says [1m] or 1M) all end "[1m]": opus[1m] is never opus."""
    head = m.get("description").split("·", 1)[0].strip().casefold() if isinstance(m.get("description"), str) else ""
    said = [x for x in (m.get("value"), m.get("resolvedModel"), m.get("displayName"),
                        head if _HEAD.fullmatch(head) else None) if isinstance(x, str)]
    long = "[1m]" if any(_LONG.search(x.casefold()) for x in said) else ""
    return {f + long for f in map(_folded, said) if f}


_NAMES = [(re.compile(m, re.IGNORECASE), a) for m, a in icons.model_names()]


def id_name(value):
    """A model's id as it reads as a name where no host lists it (schema/icons/models/families.json's names):
    claude-opus-4-7 is "Opus 4.7", gpt-5.4-codex "GPT 5.4 codex"; None where none says."""
    for pattern, said in _NAMES if isinstance(value, str) else ():
        if hit := pattern.fullmatch(value):
            return re.sub(r"\{(\d+)\}", lambda g: _part(hit.group(int(g.group(1)))), said)
    return None


def _part(text):
    text = re.sub(r"[-_]", " ", text or "")
    return text[:1].upper() + text[1:]


def claude_match(value, listed, fallback=False):
    """The row of listed (Claude's, as claude lists them: never two of them made one) a model said another way is:
    its own; else the first that goes by one of its names (claude_names: opus, "Opus 5.5 · …" in its description, for
    claude-opus-5-5); else, with fallback (a list that's only claude's aliases, no versions: none listed yet), the one
    alias that's a word of its id. None if none."""
    if not value:
        return None
    if hit := next((m for m in listed if m["value"] == value), None):
        return hit
    if model_program(value) != "claude":
        return None
    names = claude_names({"value": value})
    if hit := next((m for m in listed if names & claude_names(m)), None):
        return hit
    words = re.split(r"[^a-z0-9\[\]]+", value.casefold())
    if fallback and "[1m]" not in value and len(said := [a for a in ALIASES if a in words]) == 1:
        return next((m for m in listed if m["value"] == said[0]), None)
    return None


def pi_exe():
    return os.environ.get("BAUDE_PI_CLAUDE") or os.path.join(PI_CLAUDE, "pi-claude")


# how each program is run. exe: what finds it (None: ClaudeProc's own, claude_exe; it gives None when it isn't here). modes: whether it has claude's permission modes (else
# Manual only). effort: whether it takes --effort. first: how a new conversation is given its id (claude's --session-id;
# apple-claude and pi-claude take the id they report from --resume). resumes: whether a later run carries the
# conversation on (apple-claude remembers nothing). transcript: whether it keeps a Claude Code transcript of it (only
# claude: the others' are kept for them where they're needed, as the headless server does)
PROGRAMS = {
    "codex": {"exe": "codex_exe", "modes": True, "effort": True, "first": None, "resumes": True,
              "transcript": False},
    "claude": {"exe": None, "modes": True, "effort": True, "first": "--session-id", "resumes": True,
               "transcript": True},
    "apple": {"exe": "apple_exe", "modes": False, "effort": False, "first": "--resume", "resumes": False,
              "transcript": False},
    "pi": {"exe": "pi_exe", "modes": False, "effort": True, "first": "--resume", "resumes": True, "transcript": False},
}


def program(value):
    """PROGRAMS' entry for what runs a model, its name as "name"."""
    name = model_program(value)
    return {**PROGRAMS[name], "name": name}


def runnable_models(fresh=False, tools=False, wait=True):
    """The models besides Claude's this machine can run now, each with its provider (baudelib.providers): Apple's
    once apple-claude's built, MLX's that are downloaded, Ollama's, and the endpoints'; with pi installed for all but
    Apple's. As last found (fresh: looked for now); tools: only those that call tools; wait: for a look, the first
    time (providers.found's)."""
    from baudelib import providers
    found = providers.found(wait=wait, max_age=0 if fresh else providers.MAX_AGE)
    return [m for p in providers.usable(found, tools) for m in p["models"]]


def window_of(value):
    """A model's context window as its provider says it (Ollama's, an endpoint's, another host's in the mesh), None
    where it hasn't."""
    from baudelib import providers
    return next((m.get("window") for p in providers.found() for m in p["models"] if m["value"] == value), None) or \
        next((m.get("window") for e in providers.mesh_endpoints() for m in e["models"]
              if f"openai/{e['id']}/{m['id']}" == value), None) or \
        next((m.get("window") for m in providers.mesh_agents() if m["value"] == value), None)


# what each model's sessions last said its window was (get_context_usage's maxTokens), by its value (a Baude chat's on
# another host: "<value>@<host>", BaudeWindow.window_key; claude's own default: "default"), for its next session's
# before it says: claude's for a model depends on the account, and Apple's on the OS
SAID_WINDOWS = {"at": None, "windows": {}}


def said_windows_path():
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "baude", "windows.json")


def said_windows():
    """{model value: tokens}, as said_windows_path has them (read again once it's changed)."""
    path = said_windows_path()
    try:
        st = os.stat(path)
        at = (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        at = None
    if at != SAID_WINDOWS["at"]:
        try:
            with open(path) as f:
                got = json.load(f)
        except (OSError, ValueError):
            got = {}
        SAID_WINDOWS["windows"] = {k: v for k, v in got.items() if isinstance(v, int) and v > 0} \
            if isinstance(got, dict) else {}
        SAID_WINDOWS["at"] = at
    return SAID_WINDOWS["windows"]


def said_window(value):
    """The window a session of a model last said it had; None if none's said."""
    return said_windows().get(value) if isinstance(value, str) else None


def remember_window(value, tokens):
    """A session of a model's said its window (tokens): kept, for its next one's before it says (said_window)."""
    if not isinstance(value, str) or not value or not isinstance(tokens, int) or tokens <= 0 \
            or said_window(value) == tokens:
        return
    path = said_windows_path()
    part = f"{path}.{os.getpid()}"  # (written whole, then put in its place: Baude and Record both keep it)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(part, "w") as f:
            json.dump(said_windows() | {value: tokens}, f)
        os.replace(part, path)
    except (OSError, TypeError, ValueError):
        with contextlib.suppress(OSError):
            os.remove(part)  # (none left half written)


def launch(model, mode, effort, session_id=None, resumed=False):
    """How to start what runs a model, for a conversation of session_id (None: a new one it names itself) that it has
    (resumed) or hasn't had yet: {program, exe, missing (what runs it isn't here), mode, effort, resume, extra}, for
    ClaudeProc."""
    p = program(model)
    if proxied(model) is not None:  # (run on its host, as an agent of its: its program's there, not here)
        p = {**p, "exe": None}
    exe = globals()[p["exe"]]() if p["exe"] else None
    resume, extra = None, ()
    if session_id and (resumed and p["resumes"] or p["first"] == "--resume"):
        resume = session_id
    elif session_id and p["first"]:
        extra = (p["first"], session_id)
    if p["name"] == "claude" and os.environ.get("BAUDE_CHROME", "1") != "0":  # (Claude in Chrome, as claude --chrome:
        from baudelib.claude import claude_exe, has_flag  # browser use, in the browser you're signed in to; off with
        if has_flag(claude_exe(), "--chrome"):             # BAUDE_CHROME=0, and for a claude too old to know it)
            extra = (*extra, "--chrome")
    if p["name"] == "codex":
        mode = codex_mode(mode)
    return {"program": p["name"], "exe": exe, "missing": bool(p["exe"]) and exe is None,
            "mode": mode if p["modes"] else "default",
            "effort": effort if p["effort"] else None, "resume": resume, "extra": extra}
