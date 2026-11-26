"""hostaccounts — this host's Claude and Codex subscription accounts (accounts), changed for a head anywhere in the
mesh (Settings → Subscriptions in Baude, Record and the phone, and a chat's Sign in card): listed (list), added
(add: a Claude token, or an existing signed-in Claude folder or CODEX_HOME), renamed (rename), removed (remove), a
token account's token replaced (token), each provider's default picked (select), a subscription's usage read
(usage), and a sign-in run here (sign_in): `claude auth login` with a Claude account's own folder, or Codex's
device-code login with its CODEX_HOME, each line it writes carried back as it's written (the page to open among
them), a code pasted on the head passed to it (code), then the end; the connection's going stops it. Every answer
but a sign-in's lines carries the accounts and defaults as they are after it, and whether this host has claude
(claude_cli: without it, a Claude account is a token). No GTK."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import uuid

from baudelib import accounts, models, wording
from baudelib.claude import claude_env, claude_exe
from baudelib.hostlogin import ESCAPES, NO_BROWSER, auth_status, signed_in_as

PROMPT = "Paste code here if prompted > "  # (claude auth login's, with no line break after it)


def claude_cli(server):
    """claude to run here, or None."""
    exe = getattr(server, "exe", None) or claude_exe()
    return shutil.which(exe) if exe else None


def state(server=None):
    return {"accounts": accounts.listing(), "defaults": accounts.defaults(), "claude_cli": bool(claude_cli(server))}


def answer(server, conn, msg, reply):
    """An accounts request: answered here, or (usage, sign_in) on a thread of its own."""
    what, provider, profile = msg.get("what"), msg.get("provider"), msg.get("id") or ""

    def done(**extra):
        reply("accounts", what=what, **state(server), **extra)

    try:
        if what == "list":
            return done()
        if what == "code":  # (a sign-in's, on this connection: no answer of its own)
            return _code(conn, msg.get("code"))
        if what == "add":
            if provider == "claude" and msg.get("folder"):
                accounts.add("claude", msg.get("name"), _signed_in_folder(server, msg["folder"]), kind="folder")
            elif provider == "claude":
                accounts.add("claude", msg.get("name"), accounts.check_token(msg.get("token")))
            elif msg.get("home"):
                accounts.add("codex", msg.get("name"), _signed_in_home(msg["home"]))
            else:
                raise ValueError(wording.say("subscriptions.not_chatgpt"))
            return done()
        if what == "rename":
            accounts.rename(profile, msg.get("name"))
            return done()
        if what == "token":
            accounts.replace_token(profile, msg.get("token"))
            return done()
        if what == "remove":
            accounts.remove(profile, claude_cli(server))
            return done()
        if what == "select":
            accounts.select(provider, profile)
            server.default_changed(provider, profile)
            return done()
        if what == "use":
            server.use_account(msg.get("session"), profile)
            return done()
        if what == "usage":
            threading.Thread(target=_usage, args=(server, profile, done), daemon=True, name="usage").start()
            return None
        if what == "sign_in":
            if provider == "claude" or (accounts.account(profile) or {}).get("provider") == "claude":
                return _claude_sign_in(server, conn, msg, reply, done)
            return _sign_in(conn, msg, reply, done)
    except (ValueError, OSError) as e:
        return done(error=str(e))
    return done(error="accounts takes list, add, rename, token, remove, select, use, usage, sign_in or code")


def _usage(server, profile, done):
    try:
        limits = accounts.plan_usage(profile, claude_cli(server))
    except (OSError, subprocess.SubprocessError):
        return done(error=wording.say("subscriptions.usage_failed"))
    if limits is None:
        return done(usage=None)
    done(usage=[{"name": name.replace("_", " ").capitalize(), "key": name, "percent": round(float(percent), 1),
                 **({"resets": resets} if resets is not None else {})} for name, percent, resets in limits])


def _signed_in_home(location):
    """An existing CODEX_HOME here, signed in with ChatGPT and keeping its credentials in a file: its real path."""
    directory = os.path.realpath(os.path.expanduser(location.strip()))
    try:
        with open(os.path.join(directory, "auth.json"), encoding="utf-8") as f:
            auth = json.load(f)
    except (OSError, ValueError):
        auth = None
    if (not isinstance(auth, dict) or auth.get("auth_mode") not in (None, "chatgpt")
            or not isinstance(auth.get("tokens"), dict) or not auth["tokens"].get("access_token")):
        raise ValueError(wording.say("subscriptions.not_chatgpt"))
    return directory


def _signed_in_folder(server, location):
    """An existing Claude config folder here that's signed in (claude auth status says so, run with it): its real
    path, given links to what it shares with the host's own (accounts.share) where it has none of its own."""
    folder = os.path.realpath(os.path.expanduser(location.strip()))
    exe = claude_cli(server)
    if not exe:
        raise ValueError(wording.say("subscriptions.no_claude"))
    if not os.path.isdir(folder) or not auth_status(exe, accounts.folder_env(claude_env(), folder)).get("loggedIn"):
        raise ValueError(wording.say("subscriptions.not_signed_in_folder"))
    accounts.share(folder)
    return folder


def _code(conn, code):
    """A code pasted on the head, passed to the connection's sign-in if it's still running."""
    proc = getattr(conn, "accounts_login", None)
    if proc is None or proc.poll() is not None or proc.stdin is None or not (code or "").strip():
        return None
    try:
        proc.stdin.write(code.strip() + "\n")
        proc.stdin.flush()
    except OSError:
        pass
    return None


def _claude_sign_in(server, conn, msg, reply, done):
    """claude auth login run here with a Claude account's own folder: a new one's (named name, made now, and the
    account added once it's signed in) or an account's (id: signed in to again). Its lines carried back as it writes
    them (the page to open among them), a pasted code passed to it (code); one a connection."""
    if (proc := getattr(conn, "accounts_login", None)) is not None and proc.poll() is None:
        return done(error="this connection runs a sign-in already")
    exe = claude_cli(server)
    if not exe:
        raise ValueError(wording.say("subscriptions.no_claude"))
    profile = msg.get("id") or ""
    fresh = not profile
    name = accounts.check_name("claude", msg.get("name")) if fresh else ""
    if fresh:
        profile = uuid.uuid4().hex
        folder = accounts.new_claude_folder(profile)
    else:
        folder = accounts.claude_folder(profile)
        accounts.share(folder)
    env = accounts.folder_env(claude_env() | (getattr(server, "env", None) or {}), folder) | NO_BROWSER
    try:
        proc = subprocess.Popen([exe, "auth", "login"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1, errors="replace")
    except OSError:
        if fresh:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    conn.accounts_login = proc
    if getattr(conn, "gone", False):
        proc.terminate()

    def read():
        lines = []
        with proc.stdout:
            for line in proc.stdout:
                line = ESCAPES.sub("", line).replace(PROMPT, "").strip()
                if line:
                    lines.append(line)
                    reply("accounts", what="sign_in", line=line)
        code = proc.wait()
        who = signed_in_as(auth_status(exe, env)) if code == 0 else None
        if who is None:
            if fresh:
                discard()
            failed = next((x for x in reversed(lines) if x.lower().startswith(("login failed", "error"))), None)
            return done(error=failed or (lines[-1] if lines and code not in (-15, 130) else
                                         wording.say("subscriptions.claude_failed")))
        if fresh:
            try:
                accounts.add("claude", name, folder, kind="folder", profile=profile)
            except ValueError as e:
                discard()
                return done(error=str(e))
        done(signed_in=True, who=who)

    def discard():
        """A new account's folder that won't be kept: signed out of first if claude finished signing in to it (its
        Keychain item would be left otherwise), then removed."""
        if proc.returncode == 0:
            accounts.sign_out(folder, exe, env)
        shutil.rmtree(folder, ignore_errors=True)

    threading.Thread(target=read, daemon=True, name="claude-login").start()
    return None


def _sign_in(conn, msg, reply, done):
    """codex login --device-auth, into a new profile folder (a new account, named name) or an account's own (id: signed
    in to again), one a connection."""
    if (proc := getattr(conn, "accounts_login", None)) is not None and proc.poll() is None:
        return done(error="this connection runs a sign-in already")
    profile = msg.get("id") or ""
    fresh = not profile
    if fresh and not (msg.get("name") or "").strip():
        raise ValueError(wording.say("subscriptions.enter_name"))
    accounts.root().mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = tempfile.mkdtemp(prefix="codex-", dir=accounts.root()) if fresh else accounts.codex_home(profile)
    try:
        if fresh:
            with open(os.path.join(directory, "config.toml"), "x") as config:
                config.write('cli_auth_credentials_store = "file"\n')
        proc = subprocess.Popen([models.codex_exe() or "codex", "-c", 'cli_auth_credentials_store="file"', "login",
                                 "--device-auth"], env={**os.environ, "CODEX_HOME": directory},
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                errors="replace")
    except OSError:
        if fresh:
            shutil.rmtree(directory, ignore_errors=True)
        raise
    conn.accounts_login = proc
    if getattr(conn, "gone", False):
        proc.terminate()

    def read():
        lines = []
        with proc.stdout:
            for line in proc.stdout:
                if line := ESCAPES.sub("", line).strip():
                    lines.append(line)
                    reply("accounts", what="sign_in", line=line)
        ok = proc.wait() == 0 and os.path.isfile(os.path.join(directory, "auth.json"))
        if ok and fresh:
            try:
                accounts.add("codex", msg.get("name"), directory)
            except ValueError as e:
                shutil.rmtree(directory, ignore_errors=True)
                return done(error=str(e))
        elif not ok and fresh:
            shutil.rmtree(directory, ignore_errors=True)
        if not ok:
            return done(error=(lines[-1] if lines else wording.say("subscriptions.codex_failed")))
        done(signed_in=True)

    threading.Thread(target=read, daemon=True, name="codex-login").start()
    return None


def stop(conn):
    """The connection's gone: its sign-in with it."""
    proc = getattr(conn, "accounts_login", None)
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass
