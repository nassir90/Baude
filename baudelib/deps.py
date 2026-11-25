"""deps — the programs and libraries Baude's features need that aren't Baude's own (VTE, kubectl, Docker, claude, …),
read from schema/deps as this is imported: whether each is here, and how it's installed on this platform. A feature
that lacks one says so with what this gives (missing, say), every app alike; a host's are its host's to say (the deps
op, and a reply's missing), so the phone and another machine's apps show what that host lacks. No GTK.

schema/deps has a file for each, named by its key: {"description", "name" (as it's shown: a program's own spelling),
"where" ("app": the app's own process needs it, "host": the host process does), "needed_by" (the features, as the
schema/copy keys that name them), "detect" ({"macos"|"linux": [way, …]}: any way that finds it finds it; a platform
not there doesn't need it), "install" ({"macos"|"debian"|"fedora"|"arch": the command}), "link" (its install page),
"no_display" (true: only a host with no display needs it)}.
A way is {"env": VAR} (the program VAR names, if it's set and runnable), {"which": name, "not": path?} (on PATH, but
not path: macOS's /usr/bin/git, a stub until the Command Line Tools are in), {"gi": namespace, "version": v} (a GObject
typelib), {"glob": pattern} (a runnable file there) or {"run": argv} (a command that succeeds).

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


def _found(way):
    if "env" in way:
        exe = os.environ.get(way["env"])
        return bool(exe) and shutil.which(exe) is not None
    if "which" in way:
        exe = shutil.which(way["which"])
        return exe is not None and exe != way.get("not")
    if "gi" in way:
        try:
            import gi
            return way["version"] in gi.Repository.get_default().enumerate_versions(way["gi"])
        except (ImportError, AttributeError):
            return False
    if "glob" in way:
        return any(os.access(p, os.X_OK) for p in glob.glob(os.path.expanduser(way["glob"])))
    if "run" in way:
        argv = tuple(way["run"])
        if argv in _RUN and time.monotonic() - _RUN[argv][0] < RUN_KEPT_S:
            return _RUN[argv][1]
        try:
            ok = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        _RUN[argv] = (time.monotonic(), ok)
        return ok
    return False


def has_display():
    return SYSTEM == "macos" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def needed(key):
    """Does anything here need it (is it a dependency on this platform at all, and, for one with "no_display": true,
    on this host: Xvfb is for a host with no display)?"""
    d = TABLE[key]
    return SYSTEM in d["detect"] and not (d.get("no_display") and has_display())


def present(key):
    if key in hidden():
        return False
    ways = TABLE[key]["detect"].get(SYSTEM)
    return ways is None or not needed(key) or any(_found(w) for w in ways)


def info(key, here=None):
    """What's said of it (the wire's Dep): {dep, name, where, present, fix (this platform's command, or None), link}.
    here: whether it's here, if the caller knows (its own import failed, say); else it's looked for."""
    d = TABLE[key]
    here = present(key) if here is None or key in hidden() else here
    return {"dep": key, "name": d["name"], "where": d["where"], "present": bool(here),
            "fix": d["install"].get(platform()), "link": d.get("link")}


def missing(key, here=None):
    """None if it's here; else what's said of it (info)."""
    got = info(key, here)
    return None if got["present"] else got


def report(where="host"):
    """Every dependency of this platform's that where (host, app) needs, as info gives each: a deps op's answer."""
    return [info(k) for k, d in TABLE.items() if d["where"] == where and needed(k)]


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
