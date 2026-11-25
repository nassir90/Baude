"""Subscription profiles owned by this host. Session metadata carries only the profile ID."""
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from baudelib.selfupdate import settings_path


def root():
    return Path(settings_path()).parent / "accounts"


@contextmanager
def database():
    folder = root()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / "profiles.sqlite"
    os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
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


def profiles(provider):
    if not (root() / "profiles.sqlite").exists():
        return []
    with database() as db:
        return db.execute("SELECT id,name FROM profiles WHERE provider=? ORDER BY name", (provider,)).fetchall()


def selected(provider):
    if not (root() / "profiles.sqlite").exists():
        return ""
    with database() as db:
        row = db.execute("SELECT profile FROM defaults WHERE provider=?", (provider,)).fetchone()
    return row[0] if row else ""


def select(provider, profile):
    with database() as db:
        if profile and not db.execute("SELECT 1 FROM profiles WHERE provider=? AND id=?", (provider, profile)).fetchone():
            raise ValueError("Account no longer available")
        db.execute("INSERT OR REPLACE INTO defaults VALUES (?,?)", (provider, profile))


def add(provider, name, credential):
    name = name.strip()
    if not name or not credential or provider not in ("claude", "codex"):
        raise ValueError("Enter an account name and credential")
    profile = uuid.uuid4().hex
    with database() as db:
        try:
            db.execute("INSERT INTO profiles VALUES (?,?,?,?)", (profile, provider, name, credential))
        except sqlite3.IntegrityError:
            raise ValueError("That account name is already in use") from None
    return profile


def environment(provider, env, profile=None):
    profile = selected(provider) if profile is None else profile
    if not profile:
        return env
    with database() as db:
        row = db.execute("SELECT credential FROM profiles WHERE provider=? AND id=?", (provider, profile)).fetchone()
    if not row:
        raise OSError("The subscription profile for this session is unavailable")
    env = env.copy()
    if provider == "codex":
        if not Path(row[0], "auth.json").is_file():
            raise OSError("Sign in to this Codex profile again")
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_BASE_URL"):
            env.pop(key, None)
        env["CODEX_HOME"] = row[0]
    else:
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
            env.pop(key, None)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = row[0]
    return env


def plan_usage(profile):
    """Ask Claude Code for its selected subscription's usage without sending a chat turn."""
    import json
    import subprocess
    import tempfile
    from baudelib.claude import claude_env, claude_exe

    request = {"type": "control_request", "request_id": "usage",
               "request": {"subtype": "get_usage", "skip_behaviors": True}}
    with tempfile.TemporaryDirectory() as cwd:
        result = subprocess.run([claude_exe(), "-p", "--input-format", "stream-json", "--output-format",
                                 "stream-json", "--verbose", "--no-session-persistence", "--strict-mcp-config",
                                 "--setting-sources", ""], cwd=cwd, env=environment("claude", claude_env(), profile),
                                input=json.dumps(request) + "\n",
                                capture_output=True, text=True, timeout=25)
    for line in result.stdout.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        reply = record.get("response") if isinstance(record, dict) else None
        if isinstance(reply, dict) and reply.get("request_id") == "usage":
            limits = (reply.get("response") or {}).get("rate_limits") or {}
            return [(name.replace("_", " ").capitalize(), window["utilization"]) for name, window in limits.items()
                    if isinstance(window, dict) and isinstance(window.get("utilization"), (float, int))]
    return []


def claude_session_profile(session):
    if not session:
        return selected("claude")
    if not (root() / "profiles.sqlite").exists():
        return ""
    with database() as db:
        row = db.execute("SELECT profile FROM sessions WHERE session=?", (session,)).fetchone()
    return row[0] if row else ""


def remember_claude_session(session, profile):
    if profile and session:
        with database() as db:
            db.execute("INSERT OR IGNORE INTO sessions VALUES (?,?)", (session, profile))
