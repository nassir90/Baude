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
account's CODEX_HOME has settings of its own. A Claude account can also be a token (kind "token"): a long-lived one
from claude setup-token, or an Anthropic API key, kept in this file and given to its claude on a pipe
(CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR, or CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR for an API key), which claude reads
once as it starts, so it's in no environment its tools inherit. A Codex account is a CODEX_HOME of its own (kind
"profile"), with file-backed credentials Codex manages (an OpenAI API key pasted is written to its auth.json as
`codex login --with-api-key` writes it)."""
import glob
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

PROVIDERS = ("claude", "codex", "antigravity")
_CLAUDE_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN",
                "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
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
            db.execute("CREATE TABLE IF NOT EXISTS switches (key TEXT PRIMARY KEY, disabled INTEGER, auto INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS resumes (key TEXT PRIMARY KEY)")  # (Auto Resume on Reset's: on)
            db.execute("CREATE TABLE IF NOT EXISTS autooff (key TEXT PRIMARY KEY, until REAL)")  # (disabled by itself)
            db.execute("CREATE TABLE IF NOT EXISTS antigravity_usage (session TEXT, profile TEXT, "
                       "used INTEGER, size INTEGER, cost REAL, currency TEXT, seen REAL, "
                       "PRIMARY KEY (profile, session))")
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
            switches = {k: {"disabled": bool(d), "auto_disable": bool(a)}
                        for k, d, a in db.execute("SELECT key,disabled,auto FROM switches")}
            resumes = {k for (k,) in db.execute("SELECT key FROM resumes")}
            autooff = dict(db.execute("SELECT key,until FROM autooff"))
        _cache.update(mtime=mtime, sessions=sessions, switches=switches, resumes=resumes, autooff=autooff,
                      accounts={i: {"id": i, "provider": p, "name": n, "kind": k} for i, p, n, k, _c in rows},
                      folders={i: c for i, _p, _n, k, c in rows if k == "folder"})
    return _cache["accounts"], _cache["sessions"], _cache["folders"]


class Disabled(OSError):
    """A message for a chat refused: the subscription it runs on is disabled on its host."""


def switches():
    """Each subscription's switches, by usage key (usage_key: an account's id, "" Claude's CLI's own sign-in,
    CODEX_CLI Codex's): {disabled, auto_disable}, for those with either on."""
    _known()
    return {k: v for k, v in _cache.get("switches", {}).items() if v["disabled"] or v["auto_disable"]}


def disabled(key):
    """Whether a subscription (its usage key) is disabled: its chats' agents stopped, and nothing sent to them."""
    return switches().get(key, {}).get("disabled", False)


def auto_resume(key=None):
    """Whether a subscription (usage key) resumes its chats a usage limit stopped once the limit resets (Auto Resume
    on Reset); key None: the usage keys of every one that does."""
    _known()
    on = _cache.get("resumes", set())
    return set(on) if key is None else key in on


def set_auto_resume(key, on):
    """A subscription's (usage key's) Auto Resume on Reset turned on or off."""
    check_key(key)
    with database() as db:
        if on:
            db.execute("INSERT OR REPLACE INTO resumes VALUES (?)", (key,))
        else:
            db.execute("DELETE FROM resumes WHERE key=?", (key,))


def auto_off(key=None):
    """Whether a subscription (usage key) was disabled by itself as it was exhausted (not by hand), and not enabled
    since: {until} (seconds since 1970 it's to be enabled again, or None where that isn't known), else None; key None:
    every such one's, by usage key."""
    _known()
    marks = {k: {"until": u} for k, u in _cache.get("autooff", {}).items() if switches().get(k, {}).get("disabled")}
    return marks if key is None else marks.get(key)


def mark_auto_off(key, until):
    """A subscription (usage key) disabled by itself till when (seconds since 1970, or None: not known)."""
    with database() as db:
        db.execute("INSERT OR REPLACE INTO autooff VALUES (?,?)", (key, until))


def check_key(key):
    """A usage key that names a subscription here: the CLI's own sign-in's ("" or CODEX_CLI), or an account's id;
    ValueError for one that doesn't."""
    if key in ("", CODEX_CLI, ANTIGRAVITY_CLI) or account(key) is not None:
        return key
    raise ValueError(wording.say("subscriptions.gone"))


def set_switch(key, disabled=None, auto_disable=None):
    """A subscription's (usage key's) disabled or auto-disable switch turned on or off (None: as it is)."""
    check_key(key)
    was = switches().get(key, {"disabled": False, "auto_disable": False})
    now = (was["disabled"] if disabled is None else bool(disabled),
           was["auto_disable"] if auto_disable is None else bool(auto_disable))
    with database() as db:
        if any(now):
            db.execute("INSERT OR REPLACE INTO switches VALUES (?,?,?)", (key, int(now[0]), int(now[1])))
        else:
            db.execute("DELETE FROM switches WHERE key=?", (key,))
        if disabled is not None:  # (by hand, or enabled again: not disabled by itself)
            db.execute("DELETE FROM autooff WHERE key=?", (key,))


def exhausted(key, until=None):
    """A subscription (usage key) seen exhausted (its limit used up, or extra usage being billed): disabled, if
    its auto-disable switch is on and it isn't already, remembered as disabled by itself (auto_off) till until (seconds
    since 1970 its limit resets, with a margin: None where that isn't known). Whether it was disabled now."""
    if not _path().exists():
        return False
    mine = switches().get(key)
    if not mine or not mine["auto_disable"] or mine["disabled"]:
        return False
    set_switch(key, disabled=True)
    mark_auto_off(key, until)
    return True


WHOLE_WINDOWS = ("five_hour", "seven_day")  # (Claude's limits that are the whole subscription's: the others are a model's or an app's)


def limits_exhausted(limits, provider="claude"):
    """Whether a subscription's limits ([(key, percent, resets)]) say one that stops it is used up: Claude's whole
    subscription's (WHOLE_WINDOWS), not a model's; any of Codex's own windows."""
    return any(isinstance(p, (int, float)) and p >= 100 and "overage" not in str(k) and
               (provider != "claude" or k in WHOLE_WINDOWS) for k, p, *_ in limits or ())


def limits_reset(limits, provider="claude"):
    """When the used-up windows of a subscription's limits (limits_exhausted) are all reset (the latest of them:
    seconds since 1970), or None where none says."""
    times = [r for k, p, *rest in limits or () for r in rest[:1] if isinstance(r, (int, float)) and
             isinstance(p, (int, float)) and p >= 100 and "overage" not in str(k) and
             (provider != "claude" or k in WHOLE_WINDOWS)]
    return max(times) if times else None


def codex_exhausted(snapshot):
    """Whether a Codex rate-limit snapshot (its App Server's) says the subscription's used up: its default bucket (not
    another limit's, as a model's own) at 100%, or its rateLimitReachedType, or its spend control, said."""
    if not isinstance(snapshot, dict) or snapshot.get("limitId") not in (None, "codex"):
        return False
    credits = snapshot.get("credits")
    return bool(snapshot.get("rateLimitReachedType")) or snapshot.get("spendControlReached") is True or \
        isinstance(credits, dict) and credits.get("spendControlReached") is True or \
        limits_exhausted(codex_limits(snapshot), "codex")


def key_provider(key):
    """The provider a usage key's subscription is of."""
    return "codex" if key == CODEX_CLI else "antigravity" if key == ANTIGRAVITY_CLI else \
        (account(key) or {}).get("provider", "claude")


def listing():
    """Every account here, by provider then name: [{id, provider, name, kind}] (Account's fields)."""
    return sorted(_known()[0].values(), key=lambda a: (PROVIDERS.index(a["provider"]), a["name"].lower()))


def account(profile):
    """An account by its id ({id, provider, name, kind}), or None ("": the CLI's own sign-in, or one removed)."""
    return _known()[0].get(profile) if profile else None


def defaults():
    """Each provider's account a new chat takes (selected): its id ("" for the CLI's own sign-in)."""
    return {p: selected(p) for p in PROVIDERS}


def selected(provider):
    """The account a new chat on provider's models takes here: the one select last kept (a chat's provider picker's
    pick), else the CLI's own sign-in ("")."""
    if not _path().exists():
        return ""
    with database() as db:
        row = db.execute("SELECT profile FROM defaults WHERE provider=?", (provider,)).fetchone()
    return row[0] if row else ""


def select(provider, profile):
    """A chat's provider picker's pick (profile; "": the CLI's own sign-in) kept as the one a new chat on provider's
    models takes (selected)."""
    with database() as db:
        if profile and not db.execute("SELECT 1 FROM profiles WHERE provider=? AND id=?", (provider, profile)).fetchone():
            raise ValueError(wording.say("subscriptions.gone"))
        db.execute("INSERT OR REPLACE INTO defaults VALUES (?,?)", (provider, profile))


API_KEY = "sk-ant-api"  # (an Anthropic API key's start; a setup token's is sk-ant-oat)


def check_token(token):
    """A Claude setup token or Anthropic API key as pasted, trimmed; ValueError for anything else."""
    token = (token or "").strip()
    if not token.startswith(("sk-ant-oat", API_KEY)) or any(c.isspace() for c in token):
        raise ValueError(wording.say("subscriptions.enter_token"))
    return token


def check_openai_key(key):
    """An OpenAI API key as pasted, trimmed; ValueError for anything else."""
    key = (key or "").strip()
    if not key.startswith("sk-") or len(key) < 20 or any(c.isspace() for c in key):
        raise ValueError(wording.say("subscriptions.enter_openai_key"))
    return key


def add_codex_key(name, key):
    """A new Codex account signed in with an OpenAI API key: a CODEX_HOME of its own made here (owner-only), its
    auth.json (0600) as `codex login --with-api-key` writes it, credentials kept in a file. Its id."""
    import json
    import tempfile
    name, key = check_name("codex", name), check_openai_key(key)
    root().mkdir(mode=0o700, parents=True, exist_ok=True)
    home = tempfile.mkdtemp(prefix="codex-", dir=root())
    try:
        with open(os.path.join(home, "config.toml"), "x") as config:
            config.write('cli_auth_credentials_store = "file"\n')
        with os.fdopen(os.open(os.path.join(home, "auth.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w",
                       encoding="utf-8") as f:
            json.dump({"auth_mode": "apikey", "OPENAI_API_KEY": key}, f, indent=2)
        return add("codex", name, home)
    except BaseException:
        shutil.rmtree(home, ignore_errors=True)
        raise


def api_key(profile):
    """Whether an account is signed in with an API key (paid per use: no subscription limits): a Claude token
    account's Anthropic API key, or a Codex account whose auth.json holds an OpenAI API key."""
    import json
    a = account(profile)
    if a is None:
        return False
    try:
        credential = _credential(a["provider"], profile)
    except OSError:
        return False
    if a["provider"] == "claude":
        return a["kind"] == "token" and credential.startswith(API_KEY)
    try:
        with open(os.path.join(credential, "auth.json"), encoding="utf-8") as f:
            auth = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(auth, dict) and (auth.get("auth_mode") == "apikey" or
                                       bool(auth.get("OPENAI_API_KEY")) and not auth.get("tokens"))


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
        db.execute("DELETE FROM switches WHERE key=?", (profile,))
        db.execute("DELETE FROM resumes WHERE key=?", (profile,))
        db.execute("DELETE FROM autooff WHERE key=?", (profile,))
        db.execute("DELETE FROM antigravity_usage WHERE profile=?", (profile,))
    _drop_usage(profile, gone=True)
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


def antigravity_home(profile):
    """The isolated Gemini directory of an Antigravity account."""
    with database() as db:
        row = db.execute("SELECT credential FROM profiles WHERE id=? AND provider='antigravity'", (profile,)).fetchone()
    if row is None:
        raise ValueError(wording.say("subscriptions.gone"))
    return row[0]


def _credential(provider, profile):
    with database() as db:
        row = db.execute("SELECT credential FROM profiles WHERE provider=? AND id=?", (provider, profile)).fetchone()
    if not row:
        raise OSError(wording.say("subscriptions.session_account_gone"))
    return row[0]


class SignedOut(OSError):
    """A Codex account whose folder has no sign-in."""


def environment(provider, env, profile=None):
    """env for a Codex run under an account (profile; None: the default here): its CODEX_HOME, and nothing that would
    sign it in otherwise."""
    profile = selected(provider) if profile is None else profile
    if not profile:
        return env
    home = _credential(provider, profile)
    if not Path(home, "auth.json").is_file():
        raise SignedOut(wording.say("subscriptions.codex_signed_out"))
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
    env["CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR" if token.startswith(API_KEY) else
        "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR"] = str(read)
    return env, read


USAGE_CACHE_S = 25  # (how long a read of a subscription's usage is kept: clients asking every 30 s share one)
CODEX_CLI = "codex"  # (the usage key of Codex's CLI's own sign-in; Claude's is "", an account's its id)
ANTIGRAVITY_CLI = "antigravity"
_usage_read = {}  # (usage key → (when, limits, when in seconds since 1970))
_usage_locks = {}  # (usage key → the lock one read of it runs under)
_usage_guard = threading.Lock()
_usage_one_at_a_time = {"claude": threading.Lock(), "codex": threading.Lock(), "antigravity": threading.Lock()}


def usage_key(provider, profile):
    """What a subscription's usage is kept and said under: an account's id; the CLI's own sign-in's, "" for Claude's,
    CODEX_CLI for Codex's."""
    return profile or ("" if provider == "claude" else ANTIGRAVITY_CLI if provider == "antigravity" else CODEX_CLI)


def note_antigravity_usage(profile, session, used, size, cost=None, currency=None):
    """Keep the latest ACP context and cumulative cost of each native session, by account."""
    if not session or not isinstance(used, int) or not isinstance(size, int):
        return
    key = usage_key("antigravity", profile)
    with database() as db:
        db.execute("INSERT OR REPLACE INTO antigravity_usage VALUES (?,?,?,?,?,?,?)",
                   (session, key, used, size, cost if isinstance(cost, (int, float)) else None,
                    currency if isinstance(currency, str) else None, time.time()))


def antigravity_usage(profile):
    """The latest context and cumulative cost reported for an account's native sessions."""
    key = usage_key("antigravity", profile)
    with database() as db:
        rows = list(db.execute("SELECT used,size,cost,currency,seen FROM antigravity_usage WHERE profile=?",
                               (key,)))
    if not rows:
        return None
    latest = max(rows, key=lambda row: row[4])
    costs = [row[2] for row in rows if row[3] == "USD" and row[2] is not None]
    return {"context_used": latest[0], "context_size": latest[1], "cost_usd": sum(costs) if costs else None,
            "sessions": len(rows), "seen": latest[4]}


def plan_usage(profile, exe=None, provider="claude"):
    """The subscription's limits as its provider reads them, but one read per
    subscription at a time and one per USAGE_CACHE_S: whoever asks while it runs, or just after, gets its answer. The
    reads run one after another across one provider's subscriptions.
    provider: whose CLI's own sign-in profile "" is (an account's is its own)."""
    if profile:
        provider = (account(profile) or {}).get("provider", provider)
    key = usage_key(provider, profile)
    with _usage_guard:
        lock = _usage_locks.setdefault(key, threading.Lock())
    with lock:
        kept = _usage_read.get(key)
        if kept and time.monotonic() - kept[0] < USAGE_CACHE_S:
            return kept[1]
        with _usage_one_at_a_time[provider]:
            limits = _codex_usage(profile) if provider == "codex" else \
                _antigravity_limits(profile) if provider == "antigravity" else _plan_usage(profile, exe)
        with _usage_guard:
            if _usage_locks.get(key) is lock:  # (not an account removed while this read ran)
                _usage_read[key] = (time.monotonic(), limits, time.time())
        return limits


def forget_usage(profile):
    """The kept reading of a subscription's usage dropped (after a sign-in as someone else), once a read running now is
    done: the next ask reads afresh."""
    _drop_usage(profile)


def _drop_usage(profile, gone=False):
    with _usage_guard:
        lock = _usage_locks.get(profile)
    if lock is None:
        with _usage_guard:
            _usage_read.pop(profile, None)
        return
    with lock:
        with _usage_guard:
            _usage_read.pop(profile, None)
            if gone:
                _usage_locks.pop(profile, None)


def last_usage():
    """Each subscription's (usage key's) last read limits and when they were read (seconds since 1970): {profile: (read,
    limits)}, for those whose last read said them; a client shows them till its own read answers."""
    with _usage_guard:
        return {profile: (kept[2], kept[1]) for profile, kept in _usage_read.items() if kept[1]}


SEEN_FILE = "usage_seen.json"  # (in the host's state folder: each subscription's limits as its chats' turns said them)
_seen_guard = threading.Lock()


def note_limits(folder, key, info):
    """A claude chat's rate_limit_event (its rate_limit_info: the limits its last request's answer said, utilization a
    fraction and resetsAt in seconds since 1970) kept for usage key key in folder (the host's state folder), each
    window's (five_hour, seven_day…) latest with when it was seen, for seen_limits. Overage isn't kept. Whether it
    says the subscription's exhausted: the request refused as a whole (status rejected, but not at a model's own limit), or
    extra usage billed (overage). A window's own status or utilization doesn't say it."""
    import json
    if not isinstance(info, dict):
        return False
    kind = info.get("rateLimitType")
    spent = info.get("status") == "rejected" and not (isinstance(kind, str) and kind.startswith("seven_day_")) or \
        info.get("isUsingOverage") is True or info.get("overageInUse") is True
    found = {}
    windows = info.get("unifiedWindows")
    for name, window in [*(windows.items() if isinstance(windows, dict) else ()),
                         (info.get("rateLimitType"), info)]:
        if not isinstance(name, str) or "overage" in name or not isinstance(window, dict):
            continue
        used, resets = window.get("utilization"), window.get("resetsAt")
        if isinstance(used, (int, float)) and not isinstance(used, bool):
            found.setdefault(name, {"percent": round(float(used) * 100, 1),
                                    **({"resets": _reset_time(resets)} if _reset_time(resets) is not None else {})})
    if not found:
        return spent
    path = os.path.join(folder, SEEN_FILE)
    with _seen_guard:
        try:
            with open(path, encoding="utf-8") as f:
                kept = json.load(f)
        except (OSError, ValueError):
            kept = {}
        if not isinstance(kept, dict):
            kept = {}
        now = time.time()
        mine = kept.get(key) if isinstance(kept.get(key), dict) else {}
        mine.update({name: {**window, "seen": now} for name, window in found.items()})
        kept[key] = mine
        os.makedirs(folder, exist_ok=True)
        part = f"{path}.{os.getpid()}.part"
        with open(part, "w", encoding="utf-8") as f:
            json.dump(kept, f)
        os.replace(part, path)
    return spent


def seen_limits(folder, key):
    """A subscription's limits as its chats' turns last said them (note_limits), [(key, percent, resets)] in
    _plan_usage's shape, and when the latest of them was seen (seconds since 1970); None where none were. A window
    that has reset since reads 0, with no reset time."""
    import json
    try:
        with open(os.path.join(folder, SEEN_FILE), encoding="utf-8") as f:
            mine = json.load(f).get(key)
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(mine, dict) or not mine:
        return None
    now, limits = time.time(), []
    order = ("five_hour", "seven_day")
    for name in sorted(mine, key=lambda n: (order.index(n) if n in order else len(order), n)):
        window = mine[name]
        resets = window.get("resets")
        gone = resets is not None and resets <= now
        limits.append((name, 0.0 if gone else window["percent"], None if gone else resets))
    return limits, max(w["seen"] for w in mine.values())


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


CODEX_WINDOWS = {300: "five_hour", 10080: "seven_day"}  # (Codex's limit windows, in minutes, that Claude's limits share names with)


def _codex_window_key(minutes, fallback):
    """A Codex limit window's key: five_hour or seven_day as Claude's, else its length (2_hour, 3_day), else fallback."""
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
        return fallback
    if minutes in CODEX_WINDOWS:
        return CODEX_WINDOWS[minutes]
    if minutes % 1440 == 0:
        return f"{minutes // 1440}_day"
    if minutes % 60 == 0:
        return f"{minutes // 60}_hour"
    return f"{minutes}_minute"


def _codex_usage(profile):
    """A Codex subscription's limits from its ChatGPT usage endpoint, in [(key, percent, resets)] form.
    None for an API-key sign-in; an expired login may fall back to Codex's App Server for refresh."""
    from baudelib import codex, models

    exe = models.codex_exe()
    try:
        snapshot = codex.rate_limits(exe, profile=profile)
    except RuntimeError as e:
        raise OSError(str(e)) from None
    if snapshot is None:
        return None
    return codex_limits(snapshot) or None


def _antigravity_limits(profile):
    """A running Antigravity app's five-hour and weekly quota pools, if its local server exposes them."""
    from baudelib.antigravityquota import read
    return read(antigravity_home(profile) if profile else "")


def codex_limits(snapshot):
    """A Codex rate-limit snapshot's primary and secondary windows as [(key, percent, resets)]."""
    found = []
    for which in ("primary", "secondary"):
        window = snapshot.get(which)
        if isinstance(window, dict) and isinstance(window.get("usedPercent"), (int, float)) and \
                not isinstance(window.get("usedPercent"), bool):
            found.append((_codex_window_key(window.get("windowDurationMins"), which), window["usedPercent"],
                          _reset_time(window.get("resetsAt"))))
    return found


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


def _codex_home(profile):
    """The CODEX_HOME a Codex chat under profile runs with ("": the CLI's own), or None: an account since removed."""
    if not profile:
        return Path(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"))
    try:
        return Path(codex_home(profile))
    except ValueError:
        return None


def claude_projects(profile):
    """The projects folder a claude run under profile ("": the CLI's own sign-in) keeps its chats in, as a real path: a
    folder account's own, where it has one (claude_folders), else the host's (share links it there)."""
    from baudelib.transcripts import projects_dir
    if profile and (account(profile) or {}).get("kind") == "folder":
        mine = os.path.join(_credential("claude", profile), "projects")
        if os.path.lexists(mine):
            return os.path.realpath(mine)
    return os.path.realpath(projects_dir())


def _seen(paths):
    """What the files are now ((path, size, mtime) each): the same again once a copy's made if nothing's written them."""
    out = []
    for path in paths:
        try:
            st = os.stat(path)
            out.append((str(path), st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((str(path), None, None))
    return tuple(out)


def _carry(source, to):
    """source copied to `to`, unless what's there is as new (copy2 keeps the time it was written: a chat moved back to
    an account it ran under gets what it's said since), through a file beside it renamed over it, so a claude or Codex
    reading `to` never sees half of it."""
    source, to = Path(source), Path(to)
    if os.path.realpath(source) == os.path.realpath(to):
        return
    if to.exists() and to.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return
    to.parent.mkdir(parents=True, exist_ok=True)
    temp = to.with_name(f".{to.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temp)
        os.replace(temp, to)
    finally:
        if temp.exists():
            temp.unlink()


def carry_chat(program, thread, transcript, was, now):
    """A chat's own record carried from the account it ran under (was; "": the CLI's own) to the one it goes on under
    (now), so its program there resumes it: Codex's thread (its rollout files, by thread id) from was's CODEX_HOME to
    now's; claude's transcript (its path) into now's projects folder, where that isn't the one it's in. Returns (its
    transcript where now's claude finds it, or None; what the files copied were, _seen, to tell if they're written
    after). Nothing to carry from an account since removed. OSError if a copy fails."""
    if program == "codex":
        source, target = _codex_home(was), _codex_home(now)
        if not thread or was == now or source is None or target is None:
            return None, ()
        found = list((source / "sessions").glob(f"**/rollout-*{glob.escape(thread)}.jsonl"))
        seen = _seen(found)
        for path in found:
            _carry(path, target / path.relative_to(source))
        return None, seen
    if not transcript or not os.path.exists(transcript):
        return None, ()
    seen = _seen([transcript])
    project = os.path.basename(os.path.dirname(transcript))
    to = os.path.join(claude_projects(now), project, os.path.basename(transcript))
    _carry(transcript, to)
    return to, seen


def carry_seen(seen):
    """_seen again for the files carry_chat copied: equal to what it returned if none's been written since."""
    return _seen([p for p, _size, _mtime in seen])


def remember_claude_session(session, profile):
    if profile and session:
        with database() as db:
            db.execute("INSERT OR IGNORE INTO sessions VALUES (?,?)", (session, profile))
