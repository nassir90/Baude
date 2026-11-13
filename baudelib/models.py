"""What runs a model. Claude's run in claude; Apple's on-device one in apple-claude (apple-claude/); those MLX, Ollama
or an OpenAI-compatible server serves on this machine in pi-claude (pi-claude/: pi on the server's API). Each speaks
claude's stream-json on stdin and stdout, and each is started, given an id and carried on differently: PROGRAMS says
how, for Baude and the headless server alike (launch). Where the models besides Claude's are, and which there are:
baudelib.providers."""
import json
import os

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
# (mlx_models: Meta's add_mlx_model), that are downloaded, after Apple's; Manual only, like Apple's, and no effort but a
# thinking level for those that think; one to another keeps the conversation (pi's session), to Claude's doesn't
PI_CLAUDE = os.path.join(REPO, "pi-claude")


def _entry(m):
    """An MLX model as models.json has it, as initialize lists it."""
    return {"value": m["value"], "resolvedModel": m["repo"], "displayName": m["displayName"]} | \
        ({"supportsEffort": True, "supportedEffortLevels": ["off", "low", "medium", "high"]} if m.get("thinking") else {})


try:
    with open(os.path.join(PI_CLAUDE, "models.json")) as f:
        MLX_OWN = [m for m in json.load(f) if isinstance(m, dict) and m.get("value") and m.get("repo")]
except (OSError, ValueError):
    MLX_OWN = []
LOCAL_MODELS = [_entry(m) for m in MLX_OWN]  # (models.json's: mlx_models has those settings.json adds too)
_ADDED = {"at": None, "list": []}


def mlx_added():
    """The MLX models settings.json adds (mlx_models), as models.json has them: read again only once it's changed."""
    from baudelib.selfupdate import settings_path
    try:
        at = os.stat(settings_path()).st_mtime_ns
    except OSError:
        at = None
    if at != _ADDED["at"]:
        try:
            with open(settings_path()) as f:
                found = json.load(f).get("mlx_models") or []
        except (OSError, ValueError, AttributeError):
            found = []
        own = {m["value"] for m in MLX_OWN}
        _ADDED.update(at=at, list=[m for m in found if isinstance(m, dict) and isinstance(m.get("value"), str) and
                                   isinstance(m.get("repo"), str) and m["value"] not in own and
                                   isinstance(m.get("displayName"), str)])
    return list(_ADDED["list"])


def mlx_list():
    """Every MLX model pi-claude knows, as models.json has them: its own, then settings.json's (mlx_added)."""
    return MLX_OWN + [{"input": ["text"]} | m for m in mlx_added()]


def mlx_models():
    """mlx_list's models as initialize lists them."""
    return LOCAL_MODELS + [_entry(m) for m in mlx_added()]


def hf_hub():
    """Where huggingface_hub keeps what it downloads (its HF_HUB_CACHE, HF_HOME, XDG_CACHE_HOME)."""
    return os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME") or os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                                                  "huggingface"), "hub")


def local_models():
    """mlx_models whose weights are in the Hugging Face cache, while pi (in ~/.local/share/baude/pi) is installed."""
    pi = os.environ.get("BAUDE_PI") or os.path.expanduser("~/.local/share/baude/pi/node_modules/.bin/pi")
    if not os.access(pi, os.X_OK):
        return []
    hub = hf_hub()
    return [m for m in mlx_models()
            if os.path.isdir(os.path.join(hub, "models--" + m["resolvedModel"].replace("/", "--")))]


SERVED = ("ollama/", "openai/")  # (the values of models Ollama or an OpenAI-compatible server serves: baudelib.providers)


def model_program(value):
    """What runs a model: "apple" (apple-claude), "pi" (pi-claude: MLX's, Ollama's, an endpoint's), or "claude"."""
    if value == APPLE_MODEL["value"]:
        return "apple"
    if isinstance(value, str) and value.startswith(SERVED):
        return "pi"
    return "pi" if any(m["value"] == value for m in mlx_models()) else "claude"


def pi_exe():
    return os.environ.get("BAUDE_PI_CLAUDE") or os.path.join(PI_CLAUDE, "pi-claude")


# how each program is run. exe: what finds it (None: ClaudeProc's own, claude_exe; it gives None when it isn't here). modes: whether it has claude's permission modes (else
# Manual only). effort: whether it takes --effort. first: how a new conversation is given its id (claude's --session-id;
# apple-claude and pi-claude take the id they report from --resume). resumes: whether a later run carries the
# conversation on (apple-claude remembers nothing). transcript: whether it keeps a Claude Code transcript of it (only
# claude: the others' are kept for them where they're needed, as the headless server does)
PROGRAMS = {
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
              if f"openai/{e['id']}/{m['id']}" == value), None)


def launch(model, mode, effort, session_id=None, resumed=False):
    """How to start what runs a model, for a conversation of session_id (None: a new one it names itself) that it has
    (resumed) or hasn't had yet: {program, exe, missing (what runs it isn't here), mode, effort, resume, extra}, for
    ClaudeProc."""
    p = program(model)
    exe = globals()[p["exe"]]() if p["exe"] else None
    resume, extra = None, ()
    if session_id and (resumed and p["resumes"] or p["first"] == "--resume"):
        resume = session_id
    elif session_id:
        extra = (p["first"], session_id)
    if p["name"] == "claude" and os.environ.get("BAUDE_CHROME", "1") != "0":  # (Claude in Chrome, as claude --chrome:
        from baudelib.claude import claude_exe, has_flag  # browser use, in the browser you're signed in to; off with
        if has_flag(claude_exe(), "--chrome"):             # BAUDE_CHROME=0, and for a claude too old to know it)
            extra = (*extra, "--chrome")
    return {"program": p["name"], "exe": exe, "missing": bool(p["exe"]) and exe is None,
            "mode": mode if p["modes"] else "default",
            "effort": effort if p["effort"] else None, "resume": resume, "extra": extra}
