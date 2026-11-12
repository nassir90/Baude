"""What runs a model. Claude's run in claude; Apple's on-device one in apple-claude (apple-claude/); those on this machine
in pi-claude (pi-claude/: pi on a model mlx serves). Each speaks claude's stream-json on stdin and stdout, and each is
started, given an id and carried on differently: PROGRAMS says how, for Baude and the headless server alike (launch)."""
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
# mlx_vlm.server serves, each tool call asked): those in pi-claude/models.json that are downloaded, after Apple's;
# Manual only, like Apple's, and no effort but a thinking level for those that think; one to another keeps the
# conversation (pi's session), to Claude's doesn't
PI_CLAUDE = os.path.join(REPO, "pi-claude")
try:
    with open(os.path.join(PI_CLAUDE, "models.json")) as f:
        LOCAL_MODELS = [{"value": m["value"], "resolvedModel": m["repo"], "displayName": m["displayName"]} |
                        ({"supportsEffort": True, "supportedEffortLevels": ["off", "low", "medium", "high"]}
                         if m.get("thinking") else {}) for m in json.load(f)]
except (OSError, ValueError, KeyError):
    LOCAL_MODELS = []


def local_models():
    """LOCAL_MODELS whose weights are in the Hugging Face cache, while pi (in ~/.local/share/baude/pi) is installed."""
    pi = os.environ.get("BAUDE_PI") or os.path.expanduser("~/.local/share/baude/pi/node_modules/.bin/pi")
    if not os.access(pi, os.X_OK):
        return []
    hub = os.environ.get("HF_HUB_CACHE") or os.path.join(  # (where huggingface_hub looks)
        os.environ.get("HF_HOME") or os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                                                  "huggingface"), "hub")
    return [m for m in LOCAL_MODELS
            if os.path.isdir(os.path.join(hub, "models--" + m["resolvedModel"].replace("/", "--")))]


def model_program(value):
    """What runs a model: "apple" (apple-claude), "pi" (pi-claude), or "claude"."""
    if value == APPLE_MODEL["value"]:
        return "apple"
    return "pi" if any(m["value"] == value for m in LOCAL_MODELS) else "claude"


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


def runnable_models():
    """The models besides Claude's this machine can run now: Apple's once apple-claude's built, and pi's that are here."""
    return ([APPLE_MODEL] if apple_exe() else []) + local_models()


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
    return {"program": p["name"], "exe": exe, "missing": bool(p["exe"]) and exe is None,
            "mode": mode if p["modes"] else "default",
            "effort": effort if p["effort"] else None, "resume": resume, "extra": extra}
