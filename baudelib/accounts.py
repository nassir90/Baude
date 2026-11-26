"""Claude and Codex subscription accounts kept on this host (Settings → Subscriptions, through the accounts op:
hostaccounts). A session's metadata carries only an account's id; what signs it in stays here, in an owner-only file
(0600) in an owner-only folder (0700).

A Claude account is, where the host has claude, a config folder of its own (kind "folder"): claude runs with
CLAUDE_CONFIG_DIR set to it and keeps the sign-in there itself (`claude auth login` run with that folder; on macOS in
the Keychain under a service name suffixed with a hash of the folder's path, so each folder's is its own). Baude keeps
only the folder's path. The folder's projects (and the user's commands, agents, skills, plugins, hooks, output styles,
CLAUDE.md) are links to the host's own Claude folder's, so its chats are where every other chat is, and set up the
same. Its settings.json and .claude.json are its own (claude won't write through a link): a folder made here starts
with a copy of the host's settings.json and the host's user MCP servers (~/.claude.json's mcpServers), as a Codex
account's CODEX_HOME has settings of its own. Where the host has no claude, a Claude account is a token from claude setup-token (kind "token"), given to
its claude on a pipe (CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR), which claude reads once as it starts, so it's in no
environment its tools inherit. A Codex account is a CODEX_HOME of its own (kind "profile"), with file-backed
credentials Codex manages."""
import os
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from baudelib import wording
from baudelib.selfupdate import settings_path

PROVIDERS = ("claude", "codex")
_CLAUDE_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN",
                "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_USE_FOUNDRY")
_FOLDER_KEYS = ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR")  # (another folder's, or its Keychain item's)
# (what a folder account's folder shares with the host's own Claude folder: its chats, and how the user set Claude up)
SHARED = ("projects", "CLAUDE.md", "commands", "agents", "skills", "plugins", "hooks", "output-styles")
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
            db.execute("CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, provider TEXT, name TEXT, "
                       "credential TEXT, kind TEXT, UNIQUE(provider, name))")
            db.execute("CREATE TABLE IF NOT EXISTS defaults (provider TEXT PRIMARY KEY, profile TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS sessions (session TEXT PRIMARY KEY, profile TEXT)")
            yield db
    finally:
        db.close()


def _known():
    """(accounts by id: {id, provider, name, kind}, sessions' accounts by session id, folder accounts' folders by id),
    from the file as it is now."""
    try:
        mtime = _path().stat().st_mtime_ns
    except OSError:
        return {}, {}, {}
    if _cache.get("mtime") != mtime:
        with database() as db:
            rows = list(db.execute("SELECT id,provider,name,kind,credential FROM profiles"))
            sessions = dict(db.execute("SELECT session,profile FROM sessions"))
        _cache.update(mtime=mtime, sessions=sessions,
                      accounts={i: {"id": i, "provider": p, "name": n, "kind": k} for i, p, n, k, _c in rows},
                      folders={i: c for i, _p, _n, k, c in rows if k == "folder"})
    return _cache["accounts"], _cache["sessions"], _cache["folders"]


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


def check_name(provider, name):
    """A new account's name, trimmed; ValueError if it's empty or taken."""
    name = (name or "").strip()
    if provider not in PROVIDERS or not name:
        raise ValueError(wording.say("subscriptions.enter_name"))
    if any(a["provider"] == provider and a["name"] == name for a in listing()):
        raise ValueError(wording.say("subscriptions.name_in_use"))
    return name


def add(provider, name, credential, kind=None, profile=None):
    """A new account (kind: "token" or "folder" for Claude, "profile" for Codex; profile: its id, if it's picked
    already, as a folder made for it is named by it): its id."""
    name = (name or "").strip()
    if provider not in PROVIDERS or not name:
        raise ValueError(wording.say("subscriptions.enter_name"))
    if not credential:
        raise ValueError(wording.say("subscriptions.enter_token"))
    kind = kind or ("token" if provider == "claude" else "profile")
    profile = profile or uuid.uuid4().hex
    with database() as db:
        try:
            db.execute("INSERT INTO profiles VALUES (?,?,?,?,?)", (profile, provider, name, credential, kind))
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
        if not db.execute("UPDATE profiles SET credential=? WHERE id=? AND provider='claude' AND kind='token'",
                          (token, profile)).rowcount:
            raise ValueError(wording.say("subscriptions.gone"))


def remove(profile, exe=None):
    """An account gone: no longer anyone's default, and a folder made here for it removed with it (a Claude one's
    sign-in first signed out of, with its folder, so its Keychain item goes too; exe: claude's). A folder that was the
    user's before it was added is left as it is. Chats made under it say so as they start (_credential's OSError)."""
    with database() as db:
        row = db.execute("SELECT provider,credential,kind FROM profiles WHERE id=?", (profile,)).fetchone()
        if row is None:
            raise ValueError(wording.say("subscriptions.gone"))
        db.execute("DELETE FROM profiles WHERE id=?", (profile,))
        db.execute("DELETE FROM defaults WHERE profile=?", (profile,))
    provider, folder, kind = row
    if kind == "token" or Path(folder).parent != root():
        return
    if provider == "claude":
        sign_out(folder, exe)
    shutil.rmtree(folder, ignore_errors=True)


def sign_out(folder, exe=None, env=None):
    """claude auth logout run with a Claude folder (env: claude's, else claude_env's), so its Keychain item goes."""
    import subprocess
    from baudelib.claude import claude_env, claude_exe
    try:
        subprocess.run([exe or claude_exe(), "auth", "logout"], env=folder_env(env or claude_env(), folder),
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def claude_folder(profile):
    """A folder account's folder (signing in to it again writes there)."""
    with database() as db:
        row = db.execute("SELECT credential FROM profiles WHERE id=? AND provider='claude' AND kind='folder'",
                         (profile,)).fetchone()
    if row is None:
        raise ValueError(wording.say("subscriptions.gone"))
    return row[0]


def new_claude_folder(profile):
    """The folder a new folder account (by its id) signs in to: made here, owner-only, set up as folder_env does."""
    root().mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = root() / f"claude-{profile}"
    folder.mkdir(mode=0o700)
    share(str(folder))
    _own_settings(str(folder))
    return str(folder)


def _host_claude_json():
    """The host's own Claude Code config file (where its user MCP servers are)."""
    folder = os.environ.get("CLAUDE_CONFIG_DIR")
    return os.path.join(folder, ".claude.json") if folder else os.path.expanduser("~/.claude.json")


def _own_settings(folder):
    """A folder made here given settings of its own to start with: a copy of the host's settings.json, and a
    .claude.json with the host's user MCP servers. A settings.json that's a link is replaced by a copy."""
    import json
    from baudelib.transcripts import projects_dir
    mine, theirs = os.path.join(folder, "settings.json"), os.path.join(os.path.dirname(projects_dir()), "settings.json")
    if os.path.islink(mine):
        os.unlink(mine)
    if not os.path.lexists(mine) and os.path.isfile(theirs):
        try:
            shutil.copyfile(theirs, mine)
            os.chmod(mine, 0o600)
        except OSError:
            pass
    config = os.path.join(folder, ".claude.json")
    if os.path.lexists(config):
        return
    try:
        with open(_host_claude_json(), encoding="utf-8") as f:
            servers = json.load(f).get("mcpServers")
    except (OSError, ValueError, AttributeError):
        return
    if isinstance(servers, dict) and servers:
        try:
            with os.fdopen(os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
                json.dump({"mcpServers": servers}, f, indent=2)
        except OSError:
            pass


def share(folder):
    """A folder account's folder given links to what it shares with the host's own Claude folder (SHARED), where it
    has none of its own: its projects always (made if need be: where Baude finds chats), the rest where the host's has
    them."""
    from baudelib.transcripts import projects_dir
    home = os.path.dirname(projects_dir())
    for name in SHARED:
        mine, theirs = os.path.join(folder, name), os.path.join(home, name)
        if os.path.lexists(mine) or os.path.realpath(theirs) == os.path.realpath(mine):
            continue
        if name == "projects":
            os.makedirs(theirs, exist_ok=True)
        elif not os.path.lexists(theirs):
            continue
        try:
            os.symlink(theirs, mine)
        except OSError:
            pass
    if Path(folder).parent == root() and os.path.islink(os.path.join(folder, "settings.json")):
        _own_settings(folder)


def folder_env(env, folder):
    """env for a claude run with a folder account's folder: CLAUDE_CONFIG_DIR, and nothing that would sign it in
    otherwise (an API key, a token, another folder's Keychain item)."""
    env = {k: v for k, v in env.items() if k not in _CLAUDE_KEYS + _FOLDER_KEYS}
    env["CLAUDE_CONFIG_DIR"] = folder
    return env


def claude_folders():
    """Folder accounts' folders that keep chats of their own (a folder the user had before, with its own projects): its
    projects folders, where Baude finds chats too (transcripts.projects_dirs)."""
    return [p for f in _known()[2].values() if os.path.isdir(p := os.path.join(f, "projects"))
            and not os.path.islink(p)]


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
    """(env, fd) for a claude run under a Claude account (profile; "": the CLI's own sign-in, env as it is, fd None).
    A folder account's: its folder as CLAUDE_CONFIG_DIR (folder_env), fd None. A token account's: nothing in env that
    would sign it in otherwise, and its token on a pipe whose reading end, fd, the child is given (subprocess's
    pass_fds) and the caller closes once it's started. Read once, the pipe's empty: nothing the agent runs can read the
    token from it, nor from its environment."""
    if not profile:
        return env, None
    token = _credential("claude", profile)
    if (account(profile) or {}).get("kind") == "folder":
        share(token)
        return folder_env(env, token), None
    env = {k: v for k, v in env.items() if k not in _CLAUDE_KEYS}
    read, write = os.pipe()
    try:
        os.write(write, token.encode())  # (far less than a pipe holds: written whole without a reader)
    finally:
        os.close(write)
    env["CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR"] = str(read)
    return env, read


USAGE_CACHE_S = 25  # (how long a read of a subscription's usage is kept: clients asking every 30 s share one)
_usage_read = {}  # (profile → (when, limits))
_usage_locks = {}  # (profile → the lock one read of it runs under)
_usage_guard = threading.Lock()
_usage_one_at_a_time = threading.Lock()


def plan_usage(profile, exe=None):
    """The subscription's limits as _plan_usage reads them, but one read per profile at a time and one per
    USAGE_CACHE_S: whoever asks while it runs, or just after, gets its answer. The reads (each a claude process) run one
    after another across profiles."""
    with _usage_guard:
        lock = _usage_locks.setdefault(profile, threading.Lock())
    with lock:
        kept = _usage_read.get(profile)
        if kept and time.monotonic() - kept[0] < USAGE_CACHE_S:
            return kept[1]
        with _usage_one_at_a_time:
            limits = _plan_usage(profile, exe)
        _usage_read[profile] = (time.monotonic(), limits)
        return limits


def _reset_time(value):
    """When a limit resets, as Claude Code's get_usage says it (an ISO time or seconds since 1970): seconds since 1970,
    or None."""
    from datetime import datetime, timezone
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) / 1000 if value > 1e11 else float(value)
    if isinstance(value, str):
        try:
            when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).timestamp()
    return None


def _plan_usage(profile, exe=None):
    """The subscription's limits, [(key, percent, resets)], as Claude Code's get_usage says them without sending a chat turn;
    None where it can't say: a setup token's account (it has the inference scope only, and the usage endpoint needs the
    profile scope) or a sign-in that isn't a claude.ai subscription's. A folder account's are read with its folder."""
    import json
    import subprocess
    import tempfile
    from baudelib.claude import claude_env, claude_exe

    if profile and (account(profile) or {}).get("kind") != "folder":
        return None
    env, _fd = claude_launch(claude_env(), profile)
    request = {"type": "control_request", "request_id": "usage",
               "request": {"subtype": "get_usage", "skip_behaviors": True}}
    with tempfile.TemporaryDirectory() as cwd:
        result = subprocess.run([exe or claude_exe(), "-p", "--input-format", "stream-json", "--output-format",
                                 "stream-json", "--verbose", "--no-session-persistence", "--strict-mcp-config",
                                 "--setting-sources", ""], cwd=cwd, env=env,
                                input=json.dumps(request) + "\n", capture_output=True, text=True, timeout=25)
    for line in result.stdout.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        reply = record.get("response") if isinstance(record, dict) else None
        if isinstance(reply, dict) and reply.get("request_id") == "usage":
            limits = (reply.get("response") or {}).get("rate_limits") or {}
            found = [(name, window["utilization"], _reset_time(window.get("resets_at"))) for name, window in limits.items()
                     if isinstance(window, dict) and isinstance(window.get("utilization"), (float, int))]
            return found or None
    return None


def claude_session_profile(session):
    """The Claude account a chat run by claude here (not by its host process: Meta's, a side question's) is under:
    the one it was first run under, else (a new one, or one from before accounts) the default for a new one; for an old
    one, the folder account whose own folder keeps its transcript, else the CLI's own sign-in."""
    if not session:
        return selected("claude")
    _accounts, sessions, folders = _known()
    if session in sessions:
        return sessions[session]
    for profile, folder in folders.items():
        projects = os.path.join(folder, "projects")
        if not os.path.islink(projects) and any(
                os.path.exists(os.path.join(projects, d, session + ".jsonl"))
                for d in (os.listdir(projects) if os.path.isdir(projects) else [])):
            return profile
    return ""


def use_claude_session(session, profile):
    """A chat run by claude here goes on under another account from now on (profile; "": the CLI's own sign-in)."""
    if session:
        with database() as db:
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?)", (session, profile))


def carry_codex_thread(thread, was, now):
    """A Codex chat's thread (its rollout file) copied from the CODEX_HOME of the account it ran under (was; "": the
    CLI's own) to that of the account it goes on under (now), where it isn't already, so Codex there resumes it."""
    def home(profile):
        return Path(codex_home(profile)) if profile else \
            Path(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"))
    if not thread or was == now:
        return
    source, target = home(was), home(now)
    for found in (source / "sessions").glob(f"**/rollout-*{thread}.jsonl"):
        to = target / found.relative_to(source)
        if not to.exists():
            to.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(found, to)


def remember_claude_session(session, profile):
    if profile and session:
        with database() as db:
            db.execute("INSERT OR IGNORE INTO sessions VALUES (?,?)", (session, profile))
