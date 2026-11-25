"""hostaccounts — this host's Claude and Codex subscription accounts (accounts), changed for a head anywhere in the
mesh (Settings → Subscriptions in Baude, Record and the phone, and a chat's Sign in card): listed (list), added
(add), renamed (rename), removed (remove), a Claude account's token replaced (token), each provider's default picked
(select), the subscription's usage read (usage), and Codex's device-code sign-in run here (sign_in: each line it
writes carried back as it's written, then the end; the connection's going stops it). Every answer but a sign-in's
lines carries the accounts and defaults as they are after it. No GTK."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading

from baudelib import accounts, models, wording

ESCAPES = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def state():
    return {"accounts": accounts.listing(), "defaults": accounts.defaults()}


def answer(server, conn, msg, reply):
    """An accounts request: answered here, or (usage, sign_in) on a thread of its own."""
    what, provider, profile = msg.get("what"), msg.get("provider"), msg.get("id") or ""

    def done(**extra):
        reply("accounts", what=what, **state(), **extra)

    try:
        if what == "list":
            return done()
        if what == "add":
            if provider == "claude":
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
            accounts.remove(profile)
            return done()
        if what == "select":
            accounts.select(provider, profile)
            server.default_changed(provider, profile)
            return done()
        if what == "usage":
            threading.Thread(target=_usage, args=(profile, done), daemon=True, name="usage").start()
            return None
        if what == "sign_in":
            return _sign_in(conn, msg, reply, done)
    except (ValueError, OSError) as e:
        return done(error=str(e))
    return done(error="accounts takes list, add, rename, token, remove, select, usage or sign_in")


def _usage(profile, done):
    try:
        limits = accounts.plan_usage(profile)
    except (OSError, subprocess.SubprocessError):
        return done(error=wording.say("subscriptions.usage_failed"))
    if limits is None:
        return done(usage=None)
    done(usage=[{"name": name, "percent": round(float(percent), 1)} for name, percent in limits])


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
    """The connection's gone: its Codex sign-in with it."""
    proc = getattr(conn, "accounts_login", None)
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass
