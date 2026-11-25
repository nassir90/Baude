"""deps — the programs and libraries Baude's features need that aren't Baude's own (VTE, kubectl, Docker, claude, …),
read from schema/deps as this is imported: whether each is here, and how it's installed on this platform. A feature
that lacks one says so with what this gives (missing, say), every app alike; a host's are its host's to say (the deps
op, and a reply's missing), so the phone and another machine's apps show what that host lacks. No GTK.

schema/deps has a file for each, named by its key: {"description", "name" (as it's shown: a program's own spelling),
"where" ("app": the app's own process needs it, "host": the host process does), "needed_by" (the features, as the
schema/copy keys that name them), "group" (the copy key of what Settings → Dependencies lists it under: GROUPS),
"detect" ({"macos"|"linux": [way, …]}: any way that finds it finds it; a platform not there doesn't need it),
"install" ({"macos"|"debian"|"fedora"|"arch": the command; a check's: the one that readies it}), "link" (its install
page; a check's: how it's set up), "no_display" (true: only a host with no display needs it), "of" (a check: the key
of the program it's a check of, which it's left out without, and listed after), "ready" (the copy key said of it when
it's there: deps.installed if none; given n, the count its probe found, where it takes one), "installed" and "start"
(ways and commands: one installed and not running, Ollama, is given its start command in place of its install's)}.
A way is {"env": VAR, "decides": true?} (the program VAR names, if it's set and runnable; decides: VAR set, even
empty, is the only answer, as BAUDE_GH is gh's), {"which": name, "not": path?} (on PATH, but not path: macOS's
/usr/bin/git, a stub until the Command Line Tools are in), {"gi": namespace, "version": v} (a GObject typelib),
{"glob": pattern} (a runnable file there), {"run": argv} (a command that succeeds) or {"probe": name} (PROBES': a
count, None for not ready).

Fault injection: BAUDE_HIDE_DEPS=vte,kubectl,… makes those missing, wherever they're looked for, whatever's here."""
import glob
import json
import os
import shutil
import subprocess
import sys
import time

from baudelib import wording

FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "schema", "deps")
SYSTEM = "macos" if sys.platform == "darwin" else "linux"
RUN_KEPT_S = 30  # (a run way's answer is kept this long: macOS's git is looked for on every Git read)
_RUN = {}  # (its argv: (when, whether it succeeded))
DISTROS = {"debian": "debian", "ubuntu": "debian", "linuxmint": "debian", "pop": "debian", "fedora": "fedora",
           "rhel": "fedora", "centos": "fedora", "arch": "arch", "manjaro": "arch", "endeavouros": "arch"}


def _load():
    table = {}
    for name in sorted(os.listdir(FOLDER)):
        if name.endswith(".json"):
            with open(os.path.join(FOLDER, name)) as f:
                table[name[:-5]] = json.load(f)
    return table


TABLE = _load()


def platform():
    """Which install command is this machine's: macos, debian, fedora or arch (by /etc/os-release); None for
    another Linux, which is given the install page instead."""
    if SYSTEM == "macos":
        return "macos"
    try:
        with open("/etc/os-release") as f:
            fields = dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
    except OSError:
        return None
    for name in (fields.get("ID", "") + " " + fields.get("ID_LIKE", "")).replace('"', "").split():
        if name in DISTROS:
            return DISTROS[name]
    return None


def hidden():
    return {n.strip() for n in os.environ.get("BAUDE_HIDE_DEPS", "").split(",") if n.strip()}


def _path(way):
    """The program a way finds (env, which, glob), or None."""
    if "env" in way:
        exe = os.environ.get(way["env"])
        return shutil.which(exe) if exe else None
    if "which" in way:
        exe = shutil.which(way["which"])
        return exe if exe != way.get("not") else None
    if "glob" in way:
        return next((p for p in sorted(glob.glob(os.path.expanduser(way["glob"]))) if os.access(p, os.X_OK)), None)
    return None


def _ran(argv):
    """Did argv succeed (its answer kept RUN_KEPT_S)?"""
    argv = tuple(argv)
    if argv in _RUN and time.monotonic() - _RUN[argv][0] < RUN_KEPT_S:
        return _RUN[argv][1]
    try:
        ok = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    _RUN[argv] = (time.monotonic(), ok)
    return ok


def _found(way):
    """Does a way find it: True, False, or a probe's count (0 is found: Ollama running with no models)."""
    if "env" in way or "which" in way or "glob" in way:
        return _path(way) is not None
    if "probe" in way:
        return PROBES[way["probe"]]()
    if "gi" in way:
        try:
            import gi
            return way["version"] in gi.Repository.get_default().enumerate_versions(way["gi"])
        except (ImportError, AttributeError):
            return False
    if "run" in way:
        return _ran(way["run"])
    return False


def _finds(ways):
    """What the first way that finds it gives (True, or a probe's count), None if none does."""
    for way in ways or []:
        if way.get("decides") and way["env"] in os.environ:
            return True if _found(way) else None
        got = _found(way)
        if got is not None and got is not False:
            return got
    return None


def program(key):
    """Where a program's found (its first env, which or glob way that finds it, as present finds it), or None."""
    for way in TABLE[key]["detect"].get(SYSTEM) or []:
        if way.get("decides") and way["env"] in os.environ:
            return _path(way)
        if (path := _path(way)) is not None:
            return path
    return None


def _kube_contexts():
    import lookupkube  # (the host reads them itself: no kubectl run, nothing but names read)
    return len([c for c in lookupkube.kube_contexts() if not c.get("cluster_only")]) or None


def _gh_signed_in():
    exe = program("gh")
    return 1 if exe and _ran([exe, "auth", "token"]) else None  # (the token kept: GitHub isn't asked)


def _ollama():
    from baudelib import providers
    try:
        tags = providers.fetch(providers.ollama_url() + "/api/tags") or {}
    except (OSError, ValueError):
        return None
    return len([m for m in tags.get("models") or [] if isinstance(m, dict)])


def _mlx():
    from baudelib import models
    return len(models.downloaded_models()) if models.mlx_server() else None


PROBES = {"kube_contexts": _kube_contexts, "gh_signed_in": _gh_signed_in, "ollama": _ollama, "mlx": _mlx}


def has_display():
    return SYSTEM == "macos" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def needed(key):
    """Does anything here need it (is it a dependency on this platform at all, and, for one with "no_display": true,
    on this host: Xvfb is for a host with no display)?"""
    d = TABLE[key]
    return SYSTEM in d["detect"] and not (d.get("no_display") and has_display())


def _state(key):
    """(is it there, its probe's count or None)."""
    if key in hidden():
        return False, None
    ways = TABLE[key]["detect"].get(SYSTEM)
    if ways is None or not needed(key):
        return True, None
    got = _finds(ways)
    return got is not None, got if got is not True else None


def present(key):
    return _state(key)[0]


def info(key, here=None):
    """What's said of it (the wire's Dep): {dep, name, where, present, fix (this platform's command, or None), link,
    group, check, ready (what's said of it while it's there)}. here: whether it's here, if the caller knows (its own
    import failed, say); else it's looked for."""
    d = TABLE[key]
    there, n = _state(key) if here is None or key in hidden() else (here, None)
    fix = d["install"].get(platform())
    if not there and d.get("start") and _finds(d.get("installed", {}).get(SYSTEM)) is not None:
        fix = d["start"].get(platform())  # (installed, not running: started)
    ready = d.get("ready", "deps.installed")
    return {"dep": key, "name": d["name"], "where": d["where"], "present": bool(there), "fix": fix,
            "link": d.get("link"), "group": d.get("group"), "check": "of" in d,
            "ready": None if not there else wording.say(ready) if wording.has(ready) else wording.say(ready, n=n or 0)}


def missing(key, here=None):
    """None if it's here; else what's said of it (info)."""
    got = info(key, here)
    return None if got["present"] else got


# (Settings → Dependencies' groups, in its order: what needs them)
GROUPS = ("deps.providers", "deps.git_github", "window.kubernetes", "window.database_schemas", "deps.multiplex",
          "mesh_versions.emission", "side_panel.signal")


def report():
    """Every dependency of this platform's, the host's and its machine's apps' own, as info gives each: a deps op's
    answer, in Settings → Dependencies' order (by GROUPS, then by name, each check after its program). A check of a
    program that isn't here is left out (it's installed first)."""
    keys = [k for k in TABLE if needed(k) and "of" not in TABLE[k]]
    keys.sort(key=lambda k: (GROUPS.index(TABLE[k]["group"]), TABLE[k]["name"].lower()))
    out = []
    for k in keys:
        out.append(info(k))
        if out[-1]["present"]:
            out += [info(c) for c in sorted(TABLE, key=lambda c: TABLE[c]["name"].lower())
                    if TABLE[c].get("of") == k and needed(c)]
    return out


def feature_name(feature):
    """A feature's name as its copy key says it (window.kubernetes: Kubernetes; a menu item's … left off)."""
    return wording.say(feature).rstrip("…")


def title(dep, feature):
    """What a feature without dep shows as its heading: "Kubernetes needs kubectl" ("Git isn't installed", where the
    feature's name is in the dependency's: "Claude Code isn't installed"). dep: a Dep (info's)."""
    name = feature_name(feature)
    if name.lower() in dep["name"].lower():  # (Git is Git; Claude is in Claude Code)
        return wording.say("deps.not_installed", dep=dep["name"])
    return wording.say("deps.needs", feature=name, dep=dep["name"])


class Missing(OSError):
    """What a host can't do for want of a dependency: said as say says it, its Dep (dep) for the wire's error."""

    def __init__(self, dep, feature):
        super().__init__(say(dep, feature))
        self.dep = dep


def say(dep, feature):
    """The same in a line (a status line, a toast, stderr): what's missing and how it's installed."""
    name = feature_name(feature)
    if dep.get("fix") and name.lower() in dep["name"].lower():
        return wording.say("deps.not_installed_fix", dep=dep["name"], fix=dep["fix"])
    if dep.get("fix"):
        return wording.say("deps.needs_fix", feature=name, dep=dep["name"], fix=dep["fix"])
    if dep.get("link"):
        return wording.say("deps.needs_link", feature=name, dep=dep["name"], link=dep["link"])
    return wording.say("deps.needs", feature=name, dep=dep["name"])
