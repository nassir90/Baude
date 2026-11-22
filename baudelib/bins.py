"""Baude's commands in ~/.local/bin: baude, record and emission (the apps), baude-headless (the host process: what ssh
runs on a host, wire.HOST_COMMAND) and baude-oneshot. Each is a small script that runs this checkout's entry point
with the Python that wrote it. install writes them all (each app's --install, `baude_headless.py install`); refresh
writes again the ones that have fallen behind as Baude, Record and the host process start, so they follow the checkout
as it updates or moves. No GTK: a host may not have it."""
import os
import re
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))  # (the checkout they run)
# name -> (its module, beside ROOT; started by importing it, so Python uses its cached bytecode, as
# startup.launch_command does, rather than running the file)
NAMES = {"baude": ("baude", True), "record": ("record", False), "emission": ("emission", False),
         "baude-headless": ("baude_headless", False), "baude-oneshot": ("baude_oneshot", False)}
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


def _write(name, root):
    p = path(name)
    tmp = f"{p}.{os.getpid()}.new"
    with open(tmp, "w") as f:
        f.write(script(name, root))
    os.chmod(tmp, 0o755)
    os.replace(tmp, p)  # (a link there is replaced, not what it points at)
    return p


def install(root=ROOT):
    """Every command written for root, whatever was there: the paths."""
    os.makedirs(folder(), exist_ok=True)
    return [_write(name, root) for name in NAMES]


def refresh(root=ROOT):
    """The commands that have fallen behind root written again: missing, root's but written otherwise (an update, a
    new Python), or another checkout's that's gone (root moved there). Another checkout's that's still there, and a
    file that isn't one of these, are left as they are; a git worktree (a copy to work in, not where Baude's run from)
    writes none. The paths written; OSError if one can't be."""
    if os.path.isfile(os.path.join(root, ".git")):
        return []
    stale = []
    for name in NAMES:
        try:
            with open(path(name)) as f:
                text = f.read(4096)
        except FileNotFoundError:
            stale.append(name)
            continue
        except (OSError, UnicodeDecodeError):
            continue
        if text == script(name, root):
            continue
        there = checkout(text)
        if there is not None and (os.path.realpath(there) == os.path.realpath(root) or
                                  not os.path.isdir(there)):
            stale.append(name)
    if stale:
        os.makedirs(folder(), exist_ok=True)
    return [_write(name, root) for name in stale]


def refreshed(say=lambda text: print(text, file=sys.stderr)):
    """refresh, as an app or the host process starts: what it wrote (or why it couldn't) said with say."""
    try:
        for p in refresh():
            say(f"wrote {p}")
    except OSError as e:
        say(f"couldn't write the commands in {folder()}: {e}")
