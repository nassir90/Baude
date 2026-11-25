"""Claude and Codex subscription accounts kept on this host (Settings → Subscriptions, through the accounts op:
hostaccounts). A session's metadata carries only an account's id; the credential stays here, in an owner-only file
(0600) in an owner-only folder (0700), as Claude Code keeps its own sign-in in a file on Linux. A Claude account is a
token from claude setup-token, given to its claude on a pipe (CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR), which claude
reads once as it starts, so it's in no environment its tools inherit; a Codex account is a CODEX_HOME of its own,
with file-backed credentials Codex manages."""
import os
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from baudelib import wording
from baudelib.selfupdate import settings_path

PROVIDERS = ("claude", "codex")
_CLAUDE_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN",
                "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_USE_FOUNDRY")
_CODEX_KEYS = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_BASE_URL")
_cache = {}  # (what listing needs, read again when the file changes: its mtime → (accounts by id, sessions' accounts))


def root():
    return Path(settings_path()).parent / "accounts"


def _path():
    return root() / "profiles.sqlite"


@contextmanager
def database():
    folder = root()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)  # (one made before, or by hand: owner-only too)
    path = _path()
    os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
    os.chmod(path, 0o600)
    db = sqlite3.connect(path)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS profiles "
                       "(id TEXT PRIMARY KEY, provider TEXT, name TEXT, credential TEXT, UNIQUE(provider, name))")
            db.execute("CREATE TABLE IF NOT EXISTS defaults (provider TEXT PRIMARY KEY, profile TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS sessions (session TEXT PRIMARY KEY, profile TEXT)")
            yield db
    finally:
        db.close()


def _kind(provider):
    return "token" if provider == "claude" else "profile"


def _known():
    """(accounts by id: {id, provider, name, kind}, sessions' accounts by session id), from the file as it is now."""
    try:
        mtime = _path().stat().st_mtime_ns
    except OSError:
        return {}, {}
    if _cache.get("mtime") != mtime:
        with database() as db:
            accounts = {i: {"id": i, "provider": p, "name": n, "kind": _kind(p)}
                        for i, p, n in db.execute("SELECT id,provider,name FROM profiles")}
            sessions = dict(db.execute("SELECT session,profile FROM sessions"))
        _cache.update(mtime=mtime, accounts=accounts, sessions=sessions)
    return _cache["accounts"], _cache["sessions"]


def listing():
    """Every account here, by provider then name: [{id, provider, name, kind}] (Account's fields)."""
    return sorted(_known()[0].values(), key=lambda a: (PROVIDERS.index(a["provider"]), a["name"].lower()))


def account(profile):
    """An account by its id ({id, provider, name, kind}), or None ("": the CLI's own sign-in, or one removed)."""
    return _known()[0].get(profile) if profile else None


def defaults():
    """Each provider's default account's id ("" for the CLI's own sign-in)."""
    return {p: selected(p) for p in PROVIDERS}


def selected(provider):
    if not _path().exists():
        return ""
    with database() as db:
        row = db.execute("SELECT profile FROM defaults WHERE provider=?", (provider,)).fetchone()
    return row[0] if row else ""


def select(provider, profile):
    with database() as db:
        if profile and not db.execute("SELECT 1 FROM profiles WHERE provider=? AND id=?", (provider, profile)).fetchone():
            raise ValueError(wording.say("subscriptions.gone"))
        db.execute("INSERT OR REPLACE INTO defaults VALUES (?,?)", (provider, profile))


def check_token(token):
    """A Claude setup token as pasted, trimmed; ValueError for anything else."""
    token = (token or "").strip()
    if not token.startswith("sk-ant-oat") or any(c.isspace() for c in token):
        raise ValueError(wording.say("subscriptions.enter_token"))
    return token


def add(provider, name, credential):
    name = (name or "").strip()
    if provider not in PROVIDERS or not name:
        raise ValueError(wording.say("subscriptions.enter_name"))
    if not credential:
        raise ValueError(wording.say("subscriptions.enter_token"))
    profile = uuid.uuid4().hex
    with database() as db:
        try:
            db.execute("INSERT INTO profiles VALUES (?,?,?,?)", (profile, provider, name, credential))
        except sqlite3.IntegrityError:
            raise ValueError(wording.say("subscriptions.name_in_use")) from None
    return profile


def rename(profile, name):
    name = (name or "").strip()
    if not name:
        raise ValueError(wording.say("subscriptions.enter_name"))
    with database() as db:
        try:
            if not db.execute("UPDATE profiles SET name=? WHERE id=?", (name, profile)).rowcount:
                raise ValueError(wording.say("subscriptions.gone"))
        except sqlite3.IntegrityError:
            raise ValueError(wording.say("subscriptions.name_in_use")) from None


def replace_token(profile, token):
    """A Claude account's token replaced (signed in to it again)."""
    token = check_token(token)
    with database() as db:
        if not db.execute("UPDATE profiles SET credential=? WHERE id=? AND provider='claude'", (token, profile)).rowcount:
            raise ValueError(wording.say("subscriptions.gone"))


def remove(profile):
    """An account gone: no longer anyone's default, and a Codex profile folder made here removed with it. Chats made
    under it say so as they start (environment's OSError)."""
    with database() as db:
        row = db.execute("SELECT provider,credential FROM profiles WHERE id=?", (profile,)).fetchone()
        if row is None:
            raise ValueError(wording.say("subscriptions.gone"))
        db.execute("DELETE FROM profiles WHERE id=?", (profile,))
        db.execute("DELETE FROM defaults WHERE profile=?", (profile,))
    if row[0] == "codex" and Path(row[1]).parent == root():
        shutil.rmtree(row[1], ignore_errors=True)


def codex_home(profile):
    """A Codex account's CODEX_HOME (signing in to it again writes there)."""
    with database() as db:
        row = db.execute("SELECT credential FROM profiles WHERE id=? AND provider='codex'", (profile,)).fetchone()
    if row is None:
        raise ValueError(wording.say("subscriptions.gone"))
    return row[0]


def _credential(provider, profile):
    with database() as db:
        row = db.execute("SELECT credential FROM profiles WHERE provider=? AND id=?", (provider, profile)).fetchone()
    if not row:
        raise OSError(wording.say("subscriptions.session_account_gone"))
    return row[0]


def environment(provider, env, profile=None):
    """env for a Codex run under an account (profile; None: the default here): its CODEX_HOME, and nothing that would
    sign it in otherwise."""
    profile = selected(provider) if profile is None else profile
    if not profile:
        return env
    home = _credential(provider, profile)
    if not Path(home, "auth.json").is_file():
        raise OSError(wording.say("subscriptions.codex_signed_out"))
    env = {k: v for k, v in env.items() if k not in _CODEX_KEYS}
    env["CODEX_HOME"] = home
    return env


def claude_launch(env, profile):
    """(env, fd) for a claude run under a Claude account (profile; "": the CLI's own sign-in, env as it is, fd None):
    nothing in env that would sign it in otherwise, and its token on a pipe whose reading end, fd, the child is given
    (subprocess's pass_fds) and the caller closes once it's started. Read once, the pipe's empty: nothing the agent
    runs can read the token from it, nor from its environment."""
    if not profile:
        return env, None
    token = _credential("claude", profile)
    env = {k: v for k, v in env.items() if k not in _CLAUDE_KEYS}
    read, write = os.pipe()
    try:
        os.write(write, token.encode())  # (far less than a pipe holds: written whole without a reader)
    finally:
        os.close(write)
    env["CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR"] = str(read)
    return env, read


def plan_usage(profile):
    """The subscription's limits, [(name, percent)], as Claude Code's get_usage says them without sending a chat turn;
    None where it can't say: a setup token's account (it has the inference scope only, and the usage endpoint needs the
    profile scope) or a CLI sign-in that isn't a claude.ai subscription's."""
    import json
    import subprocess
    import tempfile
    from baudelib.claude import claude_env, claude_exe

    if profile:
        return None
    request = {"type": "control_request", "request_id": "usage",
               "request": {"subtype": "get_usage", "skip_behaviors": True}}
    with tempfile.TemporaryDirectory() as cwd:
        result = subprocess.run([claude_exe(), "-p", "--input-format", "stream-json", "--output-format",
                                 "stream-json", "--verbose", "--no-session-persistence", "--strict-mcp-config",
                                 "--setting-sources", ""], cwd=cwd, env=claude_env(),
                                input=json.dumps(request) + "\n", capture_output=True, text=True, timeout=25)
    for line in result.stdout.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        reply = record.get("response") if isinstance(record, dict) else None
        if isinstance(reply, dict) and reply.get("request_id") == "usage":
            limits = (reply.get("response") or {}).get("rate_limits") or {}
            found = [(name.replace("_", " ").capitalize(), window["utilization"]) for name, window in limits.items()
                     if isinstance(window, dict) and isinstance(window.get("utilization"), (float, int))]
            return found or None
    return None


def claude_session_profile(session):
    """The Claude account a chat run by claude here (not by its host process: Meta's, a side question's) is under:
    the one it was first run under, else (a new one, or one from before accounts) the default for a new one and the
    CLI's own sign-in for an old one."""
    if not session:
        return selected("claude")
    return _known()[1].get(session, "")


def remember_claude_session(session, profile):
    if profile and session:
        with database() as db:
            db.execute("INSERT OR IGNORE INTO sessions VALUES (?,?)", (session, profile))
