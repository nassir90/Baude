"""A chat's folder that's gone: a .claude/worktrees/<name> worktree (Claude Code's and Claude Desktop's) made again from
its branch, as Claude Desktop does when it resumes a session whose worktree it removed when it was archived; else what
to do instead, said in a line (Gone). No GTK: the host process (baudelib.server) and Baude both use it."""
import json
import os

from baudelib.selfupdate import git

MARK = os.sep + os.path.join(".claude", "worktrees") + os.sep  # (where Claude Code and Claude Desktop make theirs)
TAIL = 4 << 20  # (how much of a transcript's end is read for the branch it was on)


class Gone(OSError):
    """A chat's folder that isn't there and couldn't be made again: str() is what to tell the user, as it is (why, and
    what to do instead)."""

    def __init__(self, why, instead):
        super().__init__(f"{why}: {instead}")
        self.why = why


def said(path):
    home = os.path.expanduser("~").rstrip(os.sep)
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def split(path):
    """(its repository's folder, its name) for a path in a .claude/worktrees/<name> worktree, or None."""
    repo, mark, rest = path.partition(MARK)
    name = rest.split(os.sep)[0]
    return (repo, name) if mark and repo and name else None


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
        cwd, branch = rec.get("cwd") if isinstance(rec, dict) else None, (rec or {}).get("gitBranch")
        if isinstance(cwd, str) and (cwd == top or cwd.startswith(top + os.sep)) and branch and branch != "HEAD":
            return branch
    return None


def candidates(repo, top, name, transcript):
    """The branches the worktree could have had, likeliest first: its transcript's; git's own record of it, if it's
    still there (removed by hand, not pruned); the names Claude Desktop and Claude Code give theirs."""
    out = [transcript_branch(transcript, top)]
    entry = None
    for line in (git(repo, "worktree", "list", "--porcelain") or "").splitlines():
        if line.startswith("worktree "):
            entry = os.path.realpath(line[9:])
        elif line.startswith("branch refs/heads/") and entry == top:
            out.append(line[18:])
    out += [f"claude/{name}", f"worktree-{name}", name]
    return list(dict.fromkeys(b for b in out if b))


def checked_out(repo, branch):
    """The checkout of repo that has branch checked out, or None."""
    for block in (git(repo, "worktree", "list", "--porcelain") or "").split("\n\n"):
        lines = block.splitlines()
        if lines and lines[0].startswith("worktree ") and f"branch refs/heads/{branch}" in lines:
            return lines[0][9:]
    return None


def restore(path, transcript=None):
    """Make the folder path is in again if it's a worktree that's gone (path itself or a folder in it): its branch
    checked out there again. The branch it checked out, or None if path is there already. Gone if it can't be."""
    path = os.path.realpath(os.path.expanduser(path))
    if os.path.isdir(path):
        return None
    parts = split(path)
    if parts is None:
        raise Gone(f"{said(path)} no longer exists", "/cd to another folder to carry on")
    repo, name = parts
    top = repo + MARK + name
    if not os.path.isdir(repo):
        raise Gone(f"{said(repo)} no longer exists, and worktree {name} with it", "/cd to another folder to carry on")
    if git(repo, "rev-parse", "--git-dir") is None:
        raise Gone(f"worktree {name} is gone, and {said(repo)} isn't a git repository to make it again from",
                   f"/cd {said(repo)} to carry on there")
    git(repo, "worktree", "prune")  # (git's record of one removed by hand: in the way of adding it again)
    for branch in candidates(repo, top, name, transcript):
        if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") is None:
            continue
        if git(repo, "worktree", "add", top, branch, timeout=120) is not None:
            if not os.path.isdir(path):  # (a folder in it the branch hasn't: the worktree's own, then)
                raise Gone(f"worktree {name} is back on {branch}, without {said(path)}", f"/cd {said(top)} to carry on")
            return branch
        if (there := checked_out(repo, branch)) is not None:
            raise Gone(f"worktree {name} is gone, and its branch {branch} is checked out in {said(there)}",
                       f"/cd {said(there)} to carry on there")
        raise Gone(f"worktree {name} is gone, and git couldn't check out its branch {branch} again",
                   f"/cd {said(repo)} to carry on in the project folder")
    raise Gone(f"worktree {name} is gone, and so is its branch", f"/cd {said(repo)} to carry on in the project folder")
