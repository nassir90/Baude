"""Baude's commands in ~/.local/bin: baude, record, emission and multiplex (the apps), baude-headless (the host process: what ssh
runs on a host, wire.HOST_COMMAND), baude-oneshot and baude-mcp (the mesh and the notebooks as an MCP server). Each is
a small script that runs this checkout's entry point with the Python that wrote it. install writes them all (each
app's --install, `baude_headless.py install`); refresh writes again the ones that have fallen behind as Baude, Record
and the host process start (the host process: its own three), so they follow the checkout as it updates or moves.
No GTK: a host may not have it."""
import os
import re
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))  # (the checkout they run)
# name -> (its module, beside ROOT; started by importing it, so Python uses its cached bytecode, as
# startup.launch_command does, rather than running the file)
NAMES = {"baude": ("baude", True), "record": ("record", False), "emission": ("emission", False),
         "multiplex": ("multiplex", False),
         "baude-headless": ("baude_headless", False), "baude-oneshot": ("baude_oneshot", False),
         "baude-mcp": ("baude_mcp", False)}
HOST_NAMES = ("baude-headless", "baude-oneshot", "baude-mcp")
_ROOT_LINE = re.compile(r"^root=(.+)$", re.M)


def folder():
    """Where they go: ~/.local/bin ($BAUDE_BINS instead: the tests' and rigs')."""
    return os.environ.get("BAUDE_BINS") or os.path.join(os.path.expanduser("~"), ".local", "bin")


def path(name):
    return os.path.join(folder(), name)


def script(name, root=ROOT, python=None):
    """What the command name says, run from root with python (this one's by default)."""
    module, boot = NAMES[name]
    python = shlex.quote(python or sys.executable)
    if boot:  # (PYTHONDONTWRITEBYTECODE, inherited from some hosts, would leave the cache stale forever)
        code = (f"import sys; sys.dont_write_bytecode = False; sys.path.insert(0, sys.argv.pop(1)); "
                f"import {module}; sys.exit({module}.main())")
        run = f'exec {python} -c {shlex.quote(code)} "$root" "$@"'
    else:
        run = f'exec {python} "$root/{module}.py" "$@"'
    return f"#!/bin/sh\nroot={shlex.quote(root)}\n{run}\n"


def checkout(text):
    """The checkout a command of these runs (its root line), or None: not one of these."""
    m = _ROOT_LINE.search(text or "")
    try:
        return shlex.split(m[1])[0] if m else None
    except (ValueError, IndexError):
        return None


def named_python(text):
    """The Python a command of these runs (its exec line), or None."""
    m = re.search(r"^exec (.+)$", text or "", re.M)
    try:
        return shlex.split(m[1])[0] if m else None
    except (ValueError, IndexError):
        return None


def _read(name):
    """(its text, whether it's one of these) for the command name, or (None, True) if it isn't there; (None, False) if
    it can't be read."""
    try:
        with open(path(name)) as f:
            text = f.read(4096)
    except FileNotFoundError:
        return None, True
    except (OSError, UnicodeDecodeError):
        return None, False
    return text, text.startswith("#!/bin/sh\nroot=") and checkout(text) is not None


def _worktree(root):
    """A git worktree (a copy to work in, not where Baude's run from) writes to ~/.local/bin none, unless BAUDE_BINS
    names another folder."""
    return os.path.isfile(os.path.join(root, ".git")) and not os.environ.get("BAUDE_BINS")


def _write(name, root, python=None):
    p = path(name)
    tmp = f"{p}.{os.getpid()}.new"
    with open(tmp, "w") as f:
        f.write(script(name, root, python))
    os.chmod(tmp, 0o755)
    os.replace(tmp, p)  # (a link there is replaced, not what it points at)
    return p


def install(root=ROOT, names=NAMES, say=lambda text: print(text, file=sys.stderr)):
    """The commands written for root: the paths. A file there that isn't one of these is left as it is, and a git
    worktree writes none; each said with say."""
    if _worktree(root):
        say(f"not writing the commands in {folder()}: {root} is a git worktree (BAUDE_BINS names another folder)")
        return []
    os.makedirs(folder(), exist_ok=True)
    written = []
    for name in names:
        text, ours = _read(name)
        if not ours:
            say(f"left {path(name)}: Baude didn't write it")
            continue
        written.append(_write(name, root))
    return written


def refresh(root=ROOT, names=NAMES):
    """The commands that have fallen behind root written again: missing, or one of these whose checkout is gone (root
    moved there) or whose Python is, or which this version writes otherwise (with the same Python). Another checkout's
    that's still there, a Python that's still there, and a file that isn't one of these are left as they are; a git
    worktree writes none. The paths written; OSError if one can't be."""
    if _worktree(root):
        return []
    stale = []
    for name in names:
        text, ours = _read(name)
        if text is None:
            if ours:
                stale.append((name, None))
            continue
        if not ours:
            continue
        there, python = checkout(text), named_python(text)
        if not os.path.isdir(there) or not python or not os.path.exists(python):
            stale.append((name, None))
        elif os.path.realpath(there) == os.path.realpath(root) and text != script(name, root, python):
            stale.append((name, python))
    if stale:
        os.makedirs(folder(), exist_ok=True)
    return [_write(name, root, python) for name, python in stale]


def refreshed(say=lambda text: print(text, file=sys.stderr), names=NAMES):
    """refresh, as an app or the host process starts (the host process, names HOST_NAMES, leaves the apps' commands:
    its Python may not be theirs): what it wrote (or why it couldn't) said with say."""
    try:
        for p in refresh(names=names):
            say(f"wrote {p}")
    except OSError as e:
        say(f"couldn't write the commands in {folder()}: {e}")
