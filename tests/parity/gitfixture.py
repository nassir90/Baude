#!/usr/bin/env python3
"""The parity rig's git: the garden's repository (make, unmake), GitHub stood in for (stub_github), and the answers the
host gives about it, recorded for the phone's rig (fixtures/git-answers.json).

    python3 tests/parity/gitfixture.py            record fixtures/git-answers.json
    python3 tests/parity/gitfixture.py --shas     print the shas of the chat's commit and the one before it

Everything is offline and the same on every machine: the repository's names, emails, dates and files are fixed, so its
shas are."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FIX = os.path.join(HERE, "fixtures")
sys.path.insert(0, ROOT)

ADA = ("Ada Park", "ada@example.com")
SAM = ("Sam Ortiz", "sam@example.com")
ROWAN = ("Rowan Lee", "41000001+rowan-lee@users.noreply.github.com")
CHAT_BRANCH = "fix/upload-retry"

UPLOAD_1 = '''"""Uploads that pick up where a dropped connection left them."""


def upload(sock, parts):
    """Send each part."""
    for part in parts:
        sock.send(part)
'''
UPLOAD_9 = '''"""Uploads that pick up where a dropped connection left them."""


def upload(sock, parts):
    """Send each part; after a drop, back off before reconnecting."""
    for part in parts:
        try:
            sock.send(part)
        except OSError:
            sock.backoff()
            sock.reconnect()
'''
TEST_8 = '''from garden.upload import upload

REPEAT = 20
'''
TEST_9 = TEST_8 + '''
class Flaky:
    """A socket that drops once, then takes everything."""

    def __init__(self):
        self.sent, self.dropped = [], False

    def send(self, part):
        if not self.dropped:
            self.dropped = True
            raise OSError("connection dropped")
        self.sent.append(part)
'''
DOCS_1 = "# Uploads\n\nAn upload sends its parts in order.\n"
DOCS_5 = DOCS_1 + "\nRelease 0.3 keeps going after a drop.\n"
DOCS_7 = DOCS_5 + "\nThe release notes are tidier.\n"
DOCS_10 = DOCS_5 + "\nA drop is retried after a pause.\n"

# (subject, author, date, files {path: text}): commits 1 to 3
STEPS = [
    ("Start the garden", ADA, "2026-11-02T09:00:00", {"garden/upload.py": UPLOAD_1, "tests/server.py": "DROPS = 0\n",
                                                      "docs/uploads.md": DOCS_1}),
    ("Add a timer to the bench", SAM, "2026-11-03T14:00:00", {"tests/bench.py": "import time\n"}),
    ("Report how long a drop takes", SAM, "2026-11-03T16:30:00", {"tests/bench.py": "import time\n\nREPORT = True\n"}),
]


def _env(home, name, email, date):
    return {**os.environ, "HOME": home, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email, "GIT_AUTHOR_DATE": f"{date} +0000", "GIT_COMMITTER_DATE": f"{date} +0000"}


def _run(repo, env, *args):
    r = subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-C", repo, *args],
                       env=env, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def _build(repo, home):
    """The garden's repository in repo (an empty folder): commits 1 to 10 as the rig's plan has them."""
    os.makedirs(repo)
    run = lambda who, date, *args: _run(repo, _env(home, *who, date), *args)  # noqa: E731

    def commit(subject, who, date, files, *more):
        for path, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(repo, path)), exist_ok=True)
            with open(os.path.join(repo, path), "w") as f:
                f.write(text)
        run(who, date, "add", "-A")
        run(who, date, "commit", "-q", "-m", subject, *more)
        return run(who, date, "rev-parse", "HEAD")
    run(ADA, "2026-11-02T09:00:00", "init", "-q", "--template=")
    shas = {}
    shas[1] = commit(*STEPS[0])
    run(SAM, "2026-11-03T14:00:00", "checkout", "-q", "-b", "bench-timer")
    shas[2] = commit(*STEPS[1])
    shas[3] = commit(*STEPS[2])
    run(ADA, "2026-11-04T10:00:00", "checkout", "-q", "main")
    run(ADA, "2026-11-04T10:00:00", "merge", "-q", "--no-ff", "bench-timer", "-m",
        "Merge pull request #120 from garden-dev/bench-timer", "-m", "Time the bench's drops")
    shas[4] = run(ADA, "2026-11-04T10:00:00", "rev-parse", "HEAD")
    shas[5] = commit("Release 0.3", ADA, "2026-11-05T11:00:00", {"docs/uploads.md": DOCS_5})
    run(ADA, "2026-11-05T11:00:00", "tag", "v0.3.0")
    run(SAM, "2026-11-12T09:30:00", "checkout", "-q", "-b", "docs-notes")
    shas[6] = commit("Draft the uploads notes", SAM, "2026-11-12T09:30:00", {"docs/notes.md": "# Notes\n"})
    run(ADA, "2026-11-13T15:00:00", "checkout", "-q", "main")
    shas[7] = commit("Tidy the release notes", ROWAN, "2026-11-13T15:00:00", {"docs/uploads.md": DOCS_7})
    run(ADA, "2026-11-20T10:05:00", "checkout", "-q", "-b", CHAT_BRANCH, shas[5])
    shas[8] = commit("Run the upload test twenty times", ADA, "2026-11-20T10:05:00", {"tests/test_upload.py": TEST_8})
    shas[9] = commit("Back off before reconnecting the upload socket", ADA, "2026-11-20T10:20:00",
                     {"garden/upload.py": UPLOAD_9, "tests/test_upload.py": TEST_9})
    shas[10] = commit("Note the retry in the docs", ADA, "2026-11-20T10:40:00", {"docs/uploads.md": DOCS_10})
    date = "2026-11-20T10:40:00"
    run(ADA, date, "remote", "add", "origin", "https://github.com/garden-dev/garden.git")
    for ref, sha in (("refs/remotes/origin/main", shas[7]), (f"refs/remotes/origin/{CHAT_BRANCH}", shas[9]),
                     ("refs/remotes/origin/docs-notes", shas[6])):
        run(ADA, date, "update-ref", ref, sha)
    run(ADA, date, "branch", "-q", "-D", "docs-notes")
    run(ADA, date, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    for branch, remote_ref in (("main", "main"), (CHAT_BRANCH, CHAT_BRANCH)):
        run(ADA, date, "config", f"branch.{branch}.remote", "origin")
        run(ADA, date, "config", f"branch.{branch}.merge", f"refs/heads/{remote_ref}")
    return shas


def written_shas():
    """The short shas of commits 8 and 9 as fixtures/git.jsonl has them (None where it doesn't yet)."""
    try:
        with open(os.path.join(FIX, "git.jsonl")) as f:
            text = f.read()
    except OSError:
        return None, None
    made = re.search(rf"\[{re.escape(CHAT_BRANCH)} ([0-9a-f]{{7}})\]", text)
    pushed = re.search(r"([0-9a-f]{7})\.\.([0-9a-f]{7})", text)
    return (pushed[1] if pushed else None), (made[1] if made else None)


def make(folder, check=True):
    """The garden's repository's .git in folder (its files stay as they are: the working tree shows as changed, which
    nothing reads). Raises if commit 8's or 9's sha isn't the one fixtures/git.jsonl names."""
    tmp = tempfile.mkdtemp()
    try:
        shas = _build(os.path.join(tmp, "repo"), os.path.join(tmp, "home"))
        if check:
            for n, written in zip((8, 9), written_shas()):
                if written != shas[n][:7]:
                    raise RuntimeError(f"commit {n} is {shas[n][:7]}, fixtures/git.jsonl has {written}: update it")
        unmake(folder)
        shutil.move(os.path.join(tmp, "repo", ".git"), os.path.join(folder, ".git"))
        return shas
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def unmake(folder):
    shutil.rmtree(os.path.join(folder, ".git"), ignore_errors=True)


_ORIGINALS = []


def stub_github(on, folder=None):
    """GitHub's answers from fixtures/github.json for its repository: the pull requests (lookupgit.PULLS), gh signed in,
    and the calls gh would make for a branch's pull request and a commit's. With on false, what was there is back."""
    import gitread
    import lookupgit
    global _ORIGINALS
    mods = [m for m in (gitread, sys.modules.get("gitview")) if m is not None]
    if not on:
        for obj, name, had, value in reversed(_ORIGINALS):
            if had:
                setattr(obj, name, value)
            elif name in vars(obj):
                delattr(obj, name)
        _ORIGINALS = []
        return
    with open(os.path.join(FIX, "github.json")) as f:
        data = json.load(f)
    prs = {p["number"]: p for p in data["prs"]}

    def put(obj, name, value):
        _ORIGINALS.append((obj, name, name in vars(obj), vars(obj).get(name)))
        setattr(obj, name, value)

    def mine(repo):
        return dict(prs) if repo == data["repo"] else None

    def gh_json(*args, timeout=20):
        if args[:2] == ("pr", "list") and "--head" in args:
            head = args[args.index("--head") + 1]
            p = next((p for p in prs.values() if p["head"] == head), None)
            return [{"number": p["number"], "title": p["title"], "state": p["state"].upper(), "url": p["url"]}] \
                if p else []
        if args[0] == "api" and re.search(r"/commits/[0-9a-f]+/pulls$", args[1]):
            return [{"number": n, "title": prs[n]["title"], "state": "closed" if prs[n]["state"] == "merged" else
                     prs[n]["state"], "html_url": prs[n]["url"],
                     "merged_at": "2026-11-20T11:00:00Z" if prs[n]["state"] == "merged" else None}
                    for n in data["commit_pulls"]]
        if args[0] == "api" and (m := re.search(r"/commits/([0-9a-f]+)$", args[1])):
            email = (lookupgit._git(folder, "log", "-1", "--format=%ae", m[1]) or "").strip()
            return {"author": {"login": data["logins"][email]}} if email in data["logins"] else {"author": None}
        if args[:2] == ("api", "user"):
            return {"login": data["login"]}
        if args[0] == "api" and "commits?per_page=100" in args[1]:
            return []
        return None
    states = lambda repo, want=True: mine(repo)  # noqa: E731
    put(lookupgit.PULLS, "states", states)
    put(lookupgit.PULLS, "read", lambda repo, fresh=False: mine(repo))
    put(lookupgit.PULLS, "pull", lambda repo, number: (mine(repo) or {}).get(number))
    put(lookupgit.PULLS, "signed_out", lambda: False)
    for m in mods:
        put(m, "gh_state", lambda refresh=False: ("signed_in", data["login"]))
        put(m, "_gh_json", gh_json)


def _stub_server(transcript, session):
    class Session:
        def transcript(self):
            return transcript
    server = type("Server", (), {})()
    server.in_sessions = lambda path: True
    server.lock = threading.Lock()
    server.sessions = {session: Session()}
    return server


def main():
    if "--shas" in sys.argv:
        tmp = tempfile.mkdtemp()
        try:
            shas = _build(os.path.join(tmp, "repo"), os.path.join(tmp, "home"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        print(shas[8], shas[9])
        return 0
    os.environ["BAUDE_GH"] = ""
    home = tempfile.mkdtemp()
    os.environ.update(HOME=home, XDG_CACHE_HOME=os.path.join(home, ".cache"), GIT_CONFIG_GLOBAL=os.devnull,
                      GIT_CONFIG_NOSYSTEM="1")
    from baudelib import hostfiles
    with open(os.path.join(FIX, "sessions.json")) as f:
        session = json.load(f)["sessions"][0]["id"]
    try:
        folder = os.path.realpath(os.path.join(home, "garden"))
        os.makedirs(folder)
        shas = make(folder)
        with open(os.path.join(FIX, "git.jsonl")) as f:
            text = f.read().replace("@CWD@", folder)
        transcript = os.path.join(home, "chat.jsonl")
        with open(transcript, "w") as f:
            f.write(text)
        server = _stub_server(transcript, session)
        ask = lambda **kw: hostfiles.git(server, {"op": "git", "cwd": folder, **kw})  # noqa: E731
        answers = {
            "page": ask(what="page", session=session, follow=True),
            "page|every": ask(what="page", session=session, follow=True, every=True),
            f"page|{CHAT_BRANCH}": ask(what="page", session=session, follow=False, revs=[CHAT_BRANCH]),
            f"card|{shas[9]}": ask(what="card", sha=shas[9]),
        }
    finally:
        shutil.rmtree(home, ignore_errors=True)
    text = json.dumps({
        "description": "What the host answers about the garden's repository (gitfixture.make): the Git page's reads "
                       "(page: the branches the chat touched; page|every; page|<the branches picked>) and the chat's "
                       "commit's card. Recorded by tests/parity/gitfixture.py; GitHub's parts are fixtures/github.json's. "
                       "@HOME@ is the host's home.",
        "answers": answers}, indent=1, ensure_ascii=False, sort_keys=False)
    text = text.replace(json.dumps(folder)[1:-1], "@HOME@/garden")
    with open(os.path.join(FIX, "git-answers.json"), "w") as f:
        f.write(text + "\n")
    print("recorded", ", ".join(answers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
