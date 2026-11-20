"""A chat's folder that's gone: a .claude/worktrees/<name> worktree (Claude Code's and Claude Desktop's) made again from
its branch, as Claude Desktop does when it resumes a session whose worktree it removed when it was archived; else what
to do instead, said in a line (Gone). git runs here: off the GTK main loop, and outside the host process's lock. No
GTK: the host process (baudelib.server) and Baude both use it."""
import json
import os
import threading

from baudelib import wording
from baudelib.selfupdate import git

MARK = os.sep + os.path.join(".claude", "worktrees") + os.sep  # (where Claude Code and Claude Desktop make theirs)
TAIL = 4 << 20  # (how much of a transcript's end is read for the branch it was on)
_LOCK = threading.Lock()  # (one made at a time: a fork and a message racing on the same one find it made)


def said(path):
    home = os.path.expanduser("~").rstrip(os.sep)
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


class Gone(OSError):
    """A chat's folder that isn't there and couldn't be made again. str(): what to tell the user (why, and where to
    /cd to carry on); why, and folder (where to carry on, None if there's nowhere to say), for a head that words the
    rest itself (one whose chat is on another machine's host, where /cd isn't there)."""

    def __init__(self, why, folder=None, in_project=False):
        super().__init__(
            wording.say("worktree.carry_on_in_project", why=why, folder=said(folder)) if folder and in_project else
            wording.say("worktree.carry_on", why=why, folder=said(folder)) if folder else
            wording.say("worktree.carry_on_elsewhere", why=why))
        self.why, self.folder = why, folder


def split(path):
    """(its repository's folder, its name) for a path in a .claude/worktrees/<name> worktree, or None."""
    repo, mark, rest = path.partition(MARK)
    name = rest.split(os.sep)[0]
    return (repo, name) if mark and repo and name else None


def restorable(path):
    """Could restore make path again (no git run: a stat or two)? A worktree's, its repository there."""
    parts = split(os.path.realpath(os.path.expanduser(path)))
    return parts is not None and os.path.isdir(parts[0])


def missing(path):
    """The Gone for a folder that's not there (nothing tried)."""
    path = os.path.realpath(os.path.expanduser(path))
    parts = split(path)
    if parts is None:
        return Gone(wording.say("worktree.no_longer_exists", path=said(path)))
    return Gone(wording.say("worktree.gone", name=parts[1]), parts[0], in_project=True)


def transcript_branch(transcript, top):
    """The branch the chat was on in the worktree at top, as its transcript last says (Claude Code's gitBranch)."""
    try:
        with open(transcript, "rb") as f:
            f.seek(max(0, os.path.getsize(transcript) - TAIL))
            lines = f.read().splitlines()
    except (OSError, TypeError):
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        cwd, branch = rec.get("cwd"), rec.get("gitBranch")
        if isinstance(cwd, str) and (cwd == top or cwd.startswith(top + os.sep)) and branch and branch != "HEAD":
            return branch
    return None


def checkouts(repo):
    """{checkout's folder: its branch (None if detached)}, as git lists them (one removed by hand too, till pruned)."""
    out = {}
    for block in (git(repo, "worktree", "list", "--porcelain") or "").split("\n\n"):
        lines = block.splitlines()
        if lines and lines[0].startswith("worktree "):
            out[os.path.realpath(lines[0][9:])] = next((x[18:] for x in lines if x.startswith("branch refs/heads/")),
                                                       None)
    return out


def restore(path, transcript=None):
    """Make the folder path is in again if it's a worktree that's gone (path itself or a folder in it): its branch
    checked out there again. The branch it checked out, or None if path is there already. Gone if it can't be."""
    path = os.path.realpath(os.path.expanduser(path))
    if os.path.isdir(path):
        return None
    parts = split(path)
    if parts is None:
        raise missing(path)
    repo, name = parts
    top = repo + MARK + name
    if not os.path.isdir(repo):
        raise Gone(wording.say("worktree.repo_gone", repo=said(repo), name=name))
    with _LOCK:
        if os.path.isdir(path):
            return None
        if git(repo, "rev-parse", "--git-dir") is None:
            raise Gone(wording.say("worktree.not_a_repo", name=name, repo=said(repo)), repo)
        trees = checkouts(repo)
        stale = top in trees  # (git's record of it, its folder removed by hand: taken over, -f, not pruned)
        for branch in dict.fromkeys(b for b in (transcript_branch(transcript, top), trees.get(top), f"claude/{name}",
                                                f"worktree-{name}", name) if b):
            if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") is None:
                continue
            if (there := next((t for t, b in trees.items() if b == branch and t != top), None)) is not None:
                raise Gone(wording.say("worktree.branch_elsewhere", name=name, branch=branch, path=said(there)), there)
            made = git(repo, "worktree", "add", *(["-f"] if stale else []), top, branch, timeout=120) is not None
            if made or os.path.isdir(top):  # (made by something else meanwhile: there all the same)
                if not os.path.isdir(path):  # (a folder in it the branch hasn't: the worktree's own, then)
                    raise Gone(wording.say("worktree.back_without", name=name, branch=branch, path=said(path)), top)
                return branch
            raise Gone(wording.say("worktree.checkout_failed", name=name, branch=branch), repo, in_project=True)
    raise Gone(wording.say("worktree.branch_gone", name=name), repo, in_project=True)
