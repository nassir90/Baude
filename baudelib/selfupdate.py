"""Kept up to date from git, with no GTK: what Baude's and Record's updater (update.py) and the host process's
(baude_headless.py serve) both use. A look at the checkout (look): fetched and fast-forwarded if it's behind its
upstream with nothing of its own (or, follow: only its HEAD as it is, where something else moves it), then the commit
checked out if it isn't the one that started, nothing's changed but what's committed, and the code imports from it. A
version that won't import is never restarted onto (BROKEN). Never on by default: an update runs whatever the upstream
has."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))  # (the checkout: Baude's, the host's)
CHECK_S = 30  # (how often git's asked)
BROKEN = set()  # (commits that wouldn't import: never restarted onto)
NO_GTK = ("baude_headless",)  # (what's imported without GTK, which a host may not have at all)
ALSO = {"baude_headless": ("baudelib.menubar",)}  # (what a module imports only as it starts: checked with it)
# (no prompt: no terminal, no password asked of a terminal or a window; ssh's own agent and keys still work)
GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "SSH_ASKPASS_REQUIRE": "never", "GCM_INTERACTIVE": "never"}


def git(root, *args, timeout=30):
    """git in root: what it printed, stripped, or None if it failed (or took longer than timeout)."""
    try:
        r = subprocess.run(["git", "-C", root, *args], env=GIT_ENV, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=timeout, start_new_session=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def allowed():
    """Not turned off for this process (BAUDE_UPDATE=0, off, no, false), whatever the setting says."""
    return os.environ.get("BAUDE_UPDATE", "1").lower() not in ("0", "off", "no", "false")


def settings_path():
    """Baude's settings on this machine, where widgets.load_settings finds them (without GTK)."""
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "baude", "settings.json")


def setting_on():
    """Settings → Auto restart on update, as Baude on this machine last saved it (no file, or no Baude: off)."""
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f).get("auto_update") is True
    except (OSError, ValueError, AttributeError):
        return False


def host_enabled(asked=False):
    """The host process's updater: not turned off (allowed), and asked for (--auto-update) or turned on in this
    machine's Baude settings."""
    return allowed() and (bool(asked) or setting_on())


def upstream(root=HERE):
    """What the checkout follows (origin/main), or None: not a checkout, or a branch that follows nothing."""
    return git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", timeout=5) or None


def look(started, module, root=HERE, follow=False):
    """One look (off the main loop: it fetches): the commit to restart onto, or None. Fast-forwarded first if it's
    behind its upstream and has nothing of its own (follow: never, nor fetched: only the HEAD something else moves);
    then the commit checked out, if it isn't started, nothing's changed but what's committed, and module (baude,
    record, baude_headless) imports from it."""
    if not follow:
        git(root, "fetch", "--quiet", timeout=60)  # (failing, offline or no credentials: what's here still counts)
    clean = git(root, "status", "--porcelain", "--untracked-files=no") == ""
    head = git(root, "rev-parse", "HEAD")
    up = None if follow else git(root, "rev-parse", "@{upstream}")
    if clean and head and up and up != head and git(root, "merge-base", "--is-ancestor", "HEAD", up) is not None:
        git(root, "merge", "--ff-only", "--quiet", up)
        head = git(root, "rev-parse", "HEAD")
        clean = git(root, "status", "--porcelain", "--untracked-files=no") == ""
    if not clean or not head or head == started or head in BROKEN:
        return None
    if importable(module, root):
        return head
    BROKEN.add(head)  # (not tried again: the next commit is)
    return None


def importable(module, root=HERE, gtk=None):
    """module imports from root in a Python of its own, its bytecode cached for the start (baude.launch_command's):
    without GTK started (no display opened: nothing shown or brought to the front), or, for what needs no GTK
    (gtk False; NO_GTK's by default: the host process), without GTK at all; with what it imports only as it starts
    (ALSO: the host's menu bar item)."""
    gtk = module not in NO_GTK if gtk is None else gtk
    names = ", ".join((module, *ALSO.get(module, ())))
    boot = ("import sys, gi; gi.disable_legacy_autoinit(); " if gtk else "import sys; ") + \
        f"sys.dont_write_bytecode = False; sys.path.insert(0, {root!r}); import {names}"
    try:
        r = subprocess.run([sys.executable, "-c", boot], env={**os.environ, "BAUDE_UPDATE": "0"}, cwd=root,
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=120, start_new_session=True)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if r.returncode != 0:
        print(f"{module}: not restarting onto a version that won't import:\n{r.stderr.decode(errors='replace')}",
              file=sys.stderr)
    return r.returncode == 0


def new_commits(old, new, root=HERE):
    """How many commits new has that old hadn't (0 if it can't say)."""
    n = git(root, "rev-list", "--count", f"{old}..{new}", timeout=5) if old and new else None
    return int(n) if n and n.isdigit() else 0
