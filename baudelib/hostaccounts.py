"""hostaccounts — this host's Claude and Codex subscription accounts (accounts), changed for a head anywhere in the
mesh (Settings → Subscriptions in Baude, Record and the phone, and a chat's Sign in card): listed (list), added
(add: a Claude token or API key, a Codex API key, or an existing signed-in Claude folder or CODEX_HOME), renamed (rename), removed (remove), a
token account's token replaced (token), a subscription disabled or enabled again (disable: its chats' agents stopped,
nothing sent to them) or set to disable itself as it's exhausted (auto_disable) or to resume its chats a usage limit
stopped as it resets (auto_resume: hostresume), a chat moved onto another (use: once it's on it, kept as the one a new
chat on its provider's models takes, accounts.selected), a subscription's usage read (usage; a setup token's, which can't be asked, as its chats' turns last
said it, with when: as_of), and a sign-in run here (sign_in): `claude auth login` with a Claude account's own folder, each line it
writes carried back as it's written (the page to open among them), a code pasted on the head passed to it (code),
or Codex's login with its CODEX_HOME (the browser's, the address the head's browser ended on passed to it: callback;
or the device code's), said as its state (login: the page to open, and the device code's code to enter there, once
codex writes them), then the end; the connection's going stops it. Every answer
but a sign-in's lines and states carries the accounts and defaults as they are after it, and whether this host has claude
(claude_cli: without it, a Claude account is a token), what's missing of a provider's program (missing: Codex's, by
deps, which a Codex sign-in is checked against before it's run), and each subscription's last read usage with when
(last_usage: shown greyed on a client till its own read answers). No GTK."""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import uuid
from http.client import HTTPConnection, HTTPException, InvalidURL

from baudelib import accounts, deps, hostlogin, hostresume, wording
from baudelib.claude import claude_env, claude_exe
from baudelib.hostlogin import ESCAPES, NO_BROWSER, auth_status, signed_in_as

PROMPT = "Paste code here if prompted > "  # (claude auth login's, with no line break after it)
URL = re.compile(r"https://\S+")
# (codex login --device-auth's one-time code, on a line of its own under "Enter this one-time code": ABCD-12345)
DEVICE_CODE = re.compile(r"^[A-Z0-9]{3,}(?:-[A-Z0-9]{3,})+$")


def claude_cli(server):
    """claude to run here, or None."""
    exe = getattr(server, "exe", None) or claude_exe()
    return shutil.which(exe) if exe else None


def state(server=None):
    missing = {"codex": dep} if (dep := deps.missing("codex")) is not None else {}
    if (dep := deps.missing("antigravity")) is not None:
        missing["antigravity"] = dep
    last = {profile: {"read": round(read), "usage": _limits(limits)} for profile, (read, limits) in accounts.last_usage().items()}
    switches = accounts.switches()
    return {"accounts": accounts.listing(), "defaults": accounts.defaults(), "claude_cli": bool(claude_cli(server)),
            "missing": missing, "last_usage": last, "codex_usage": True,
            "antigravity_usage": {key: found for key in [accounts.ANTIGRAVITY_CLI,
                *(a["id"] for a in accounts.listing() if a["provider"] == "antigravity")]
                if (found := accounts.antigravity_usage("" if key == accounts.ANTIGRAVITY_CLI else key))},
            "disabled": sorted(k for k, v in switches.items() if v["disabled"]),
            "auto_disable": sorted(k for k, v in switches.items() if v["auto_disable"]),
            "auto_resume": sorted(accounts.auto_resume())}


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
        if what == "callback":  # (a Codex browser sign-in's, on this connection: answered with the page, or why not)
            return _callback(conn, msg.get("url"), reply, msg.get("cb"))
        if what == "add":
            if provider == "claude" and msg.get("folder"):
                accounts.add("claude", msg.get("name"), _signed_in_folder(server, msg["folder"]), kind="folder")
            elif provider == "claude":
                accounts.add("claude", msg.get("name"), accounts.check_token(msg.get("token")))
            elif provider == "antigravity" and msg.get("home"):
                home = os.path.expanduser(msg["home"])
                if not os.path.isdir(home):
                    raise ValueError(wording.say("subscriptions.not_antigravity_home"))
                _antigravity_signed_in(server, accounts.add("antigravity", msg.get("name"), os.path.abspath(home)))
            elif msg.get("home"):
                accounts.add("codex", msg.get("name"), _signed_in_home(msg["home"]))
            elif "token" in msg:
                accounts.add_codex_key(msg.get("name"), msg.get("token"))
            else:
                raise ValueError(wording.say("subscriptions.not_chatgpt"))
            return done()
        if what == "rename":
            accounts.rename(profile, msg.get("name"))
            return done()
        if what == "token":  # (for a chat (session): its sign-in put right, on every head)
            accounts.replace_token(profile, msg.get("token"))
            hostlogin.ended(server, hostlogin.chat_of(server, conn, msg), ("token", conn), signed_in=True)
            return done()
        if what == "remove":
            accounts.remove(profile, claude_cli(server))
            return done()
        if what in ("disable", "auto_disable"):  # (a subscription's switch: id, "" with provider for the CLI's own)
            key = accounts.usage_key((accounts.account(profile) or {}).get("provider", provider or "claude"), profile)
            on = bool(msg.get("on"))
            accounts.set_switch(key, **{"disabled" if what == "disable" else "auto_disable": on})
            off = what == "disable" and on
            if what == "auto_disable" and on and (limits := _spent(server, key)) is not None:  # (exhausted already:
                reset = accounts.limits_reset(limits, accounts.key_provider(key))  # disabled now)
                accounts.exhausted(key, hostresume.after(reset) if reset is not None else None)
                off = True
            if off:
                getattr(server, "subscription_disabled", lambda _key: None)(key)
            else:
                getattr(server, "subscription_switched", lambda: None)()
            return done()
        if what == "auto_resume":  # (its chats a usage limit stopped carried on as it resets: hostresume)
            key = accounts.usage_key((accounts.account(profile) or {}).get("provider", provider or "claude"), profile)
            accounts.set_auto_resume(key, bool(msg.get("on")))
            getattr(server, "subscription_switched", lambda: None)()
            return done()
        if what == "use":
            server.use_account(msg.get("session"), profile)
            return done()
        if what == "usage":
            threading.Thread(target=_usage, args=(server, profile, provider or "claude", done), daemon=True,
                             name="usage").start()
            return None
        if what == "sign_in":
            if provider == "claude" or (accounts.account(profile) or {}).get("provider") == "claude":
                done = _for_chat(server, conn, msg, done)
                return _claude_sign_in(server, conn, msg, reply, done)
            if provider == "antigravity" or (accounts.account(profile) or {}).get("provider") == "antigravity":
                return _antigravity_sign_in(server, conn, msg, reply, done)
            return _sign_in(conn, msg, reply, done)
    except (ValueError, OSError) as e:
        return done(error=str(e))
    return done(error="accounts takes list, add, rename, token, remove, disable, auto_disable, auto_resume, use, usage, "
                      "sign_in, code or callback")


def _for_chat(server, conn, msg, done):
    """A Claude account's sign-in for a chat (session, if the asker may read it): the chat's sign-in moved as it starts
    and ends, as a login request's is (hostlogin); done, as it ends."""
    sid, key = hostlogin.chat_of(server, conn, msg), ("accounts", conn)
    if sid is None or ((proc := getattr(conn, "accounts_login", None)) is not None and proc.poll() is None):
        return done
    hostlogin.began(server, sid, key)

    def ended(**extra):
        proc = getattr(conn, "accounts_login", None)
        cancelled = getattr(conn, "gone", False) or proc is not None and proc.returncode in (-15, 130)
        hostlogin.ended(server, sid, key, who=extra.get("who"), signed_in=bool(extra.get("signed_in")),
                        why=None if cancelled else extra.get("error") or wording.say("subscriptions.claude_failed"))
        done(**extra)
    return ended


def _spent(server, key):
    """The limits ([(key, percent, resets)]) that say a subscription (usage key) is exhausted, as it was last read or as
    its chats' turns last said it, or None where they don't."""
    from baudelib.serverstate import state_home
    read = accounts.last_usage().get(key)
    seen = accounts.seen_limits(getattr(server, "state_dir", None) or state_home(), key)
    provider = accounts.key_provider(key)
    return next((limits for limits in (read[1] if read else None, seen[0] if seen else None)
                 if accounts.limits_exhausted(limits, provider)), None)


def _exhausted(server, key, limits, provider):
    """A usage read's limits (limits) that say a subscription's used up: disabled if it auto-disables (its chats
    stopped)."""
    reset = accounts.limits_reset(limits, provider)
    if accounts.limits_exhausted(limits, provider) and \
            accounts.exhausted(key, hostresume.after(reset) if reset is not None else None):
        getattr(server, "subscription_disabled", lambda _key: None)(key)


def _usage(server, profile, provider, done):
    """A subscription's usage (profile; "": the CLI's own sign-in of provider, Claude's where none is said)."""
    if profile:
        provider = (accounts.account(profile) or {}).get("provider", provider)
    if provider == "antigravity":
        try:
            limits = accounts.plan_usage(profile, provider=provider)
        except (OSError, ValueError):
            limits = None
        return done(usage=_limits(limits) if limits else None, tracked=accounts.antigravity_usage(profile))
    if profile:
        if accounts.api_key(profile):
            return done(usage=None, api_key=True)
    try:
        limits = accounts.plan_usage(profile, claude_cli(server), provider)
    except accounts.SignedOut as e:
        return done(error=str(e))
    except (OSError, subprocess.SubprocessError):
        return done(error=wording.say("subscriptions.codex_usage_failed" if provider == "codex" else
                                      "subscriptions.usage_failed"))
    if limits is None and provider == "claude" and (accounts.account(profile) or {}).get("kind") == "token":
        # (a setup token's usage can't be asked: as its chats' turns last said it)
        from baudelib.serverstate import state_home
        seen = accounts.seen_limits(getattr(server, "state_dir", None) or state_home(), accounts.usage_key(provider, profile))
        if seen is None:
            return done(usage=None, after_turn=True)
        return done(usage=_limits(seen[0]), as_of=round(seen[1]))
    if limits is None:
        return done(usage=None)
    _exhausted(server, accounts.usage_key(provider, profile), limits, provider)
    done(usage=_limits(limits))


def _limits(limits):
    """A subscription's limits as plan_usage says them, as the wire's UsageLimits."""
    return [{"name": name.replace("_", " ").capitalize(), "key": name, "percent": round(float(percent), 1),
             **({"resets": resets} if resets is not None else {})} for name, percent, resets in limits]


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
        else:
            accounts.forget_usage(profile)
        done(signed_in=True, who=who)

    def discard():
        """A new account's folder that won't be kept: signed out of first if claude finished signing in to it (its
        Keychain item would be left otherwise), then removed."""
        if proc.returncode == 0:
            accounts.sign_out(folder, exe, env)
        shutil.rmtree(folder, ignore_errors=True)

    threading.Thread(target=read, daemon=True, name="claude-login").start()
    return None


def _antigravity_signed_in(server, profile):
    """Tell the server an Antigravity account is signed in (its models are looked for now); never fails the request."""
    try:
        getattr(server, "antigravity_signed_in", lambda _profile: None)(profile)
    except Exception as exc:
        server.log(f"Antigravity sign-in follow-up: {exc}")


def _antigravity_sign_in(server, conn, msg, reply, done):
    """Sign in through agy's official code flow, in a directory owned by this account."""
    if (running := getattr(conn, "accounts_login", None)) is not None and running.poll() is None:
        return done(error="this connection runs a sign-in already")
    profile = msg.get("id") or ""
    fresh = not profile
    name = accounts.check_name("antigravity", msg.get("name")) if fresh else ""
    deps.need("antigravity", "programs.antigravity")
    accounts.root().mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = tempfile.mkdtemp(prefix="antigravity-", dir=accounts.root()) if fresh else \
        accounts.antigravity_home(profile)
    env = {k: v for k, v in (os.environ | NO_BROWSER).items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    try:
        proc = subprocess.Popen([deps.program("antigravity"), "acp", "--gemini-dir", directory,
                                 "--app-data-dir", "antigravity-acp", "--login"],
                                env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, errors="replace")
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
                line = ESCAPES.sub("", line).strip()
                if line:
                    lines.append(line)
                    reply("accounts", what="sign_in", line=line)
        ok = proc.wait() == 0
        who = profile
        if ok and fresh:
            try:
                who = accounts.add("antigravity", name, directory)
            except ValueError as exc:
                shutil.rmtree(directory, ignore_errors=True)
                return done(error=str(exc))
        elif not ok and fresh:
            shutil.rmtree(directory, ignore_errors=True)
        if not ok:
            reason = next((line for line in reversed(lines) if line.lower().startswith("error")), None) or \
                wording.say("subscriptions.antigravity_failed")
            return done(error=reason)
        _antigravity_signed_in(server, who)
        done(signed_in=True, who=name or (accounts.account(who) or {}).get("name"))

    threading.Thread(target=read, daemon=True, name="antigravity-login").start()
    return None


def _sign_in(conn, msg, reply, done):
    """codex login, into a new profile folder (a new account, named name) or an account's own (id: signed in to again),
    one a connection: the browser's (method browser, the default: codex's own server on this machine's 127.0.0.1 takes
    the page's redirect, at port, and the head that isn't on this machine passes it the address its browser ended on,
    callback), else the device code's (method device: --device-auth), which it falls back to by itself where the
    browser's can't listen (its ports taken). What it says comes as the sign-in's state (login, a ChatLogin):
    signing_in at once, then with the page to open (url: the browser's, with port and opened where codex opened it in
    this machine's browser itself; the device code's, with the code to enter there) once codex writes it; its end says
    signed_in (who: the account's email, as its sign-in says it) or failed (reason: what codex said last)."""
    if (proc := getattr(conn, "accounts_login", None)) is not None and proc.poll() is None:
        return done(error="this connection runs a sign-in already")
    profile = msg.get("id") or ""
    fresh = not profile
    if fresh and not (msg.get("name") or "").strip():
        raise ValueError(wording.say("subscriptions.enter_name"))
    deps.need("codex", "programs.codex")
    accounts.root().mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = tempfile.mkdtemp(prefix="codex-", dir=accounts.root()) if fresh else accounts.codex_home(profile)
    if fresh:
        try:
            with open(os.path.join(directory, "config.toml"), "x") as config:
                config.write('cli_auth_credentials_store = "file"\n')
        except OSError:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def run(method):
        # (BROWSER: codex opens the page with it on Linux, and `true` does nothing; on macOS it opens it itself)
        proc = subprocess.Popen([deps.program("codex"), "-c", 'cli_auth_credentials_store="file"', "login",
                                 *(["--device-auth"] if method == "device" else [])],
                                env={**os.environ, **NO_BROWSER, "CODEX_HOME": directory}, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        conn.accounts_login, conn.accounts_port = proc, None
        if getattr(conn, "gone", False):
            proc.terminate()
        reply("accounts", what="sign_in", login={"state": "signing_in", "method": method})
        threading.Thread(target=read, args=(proc, method), daemon=True, name="codex-login").start()

    def read(proc, method):
        lines, page, code, port, after_code, told = [], None, None, None, False, None
        with proc.stdout:
            for line in proc.stdout:
                if not (line := ESCAPES.sub("", line).strip()):
                    continue
                lines.append(line)
                if page is None and (m := URL.search(line)):
                    page = m.group(0).rstrip(".,)")
                    port = callback_port(page) if method == "browser" else None
                elif method == "device" and code is None and (DEVICE_CODE.match(line) or after_code and " " not in line):
                    code = line
                after_code = code is None and "code" in line.lower()
                if page and (page, code) != told:  # (the page as soon as it's said, then with its code)
                    told, conn.accounts_port = (page, code), port
                    reply("accounts", what="sign_in", login={
                        "state": "signing_in", "method": method, "url": page, **({"code": code} if code else {}),
                        **({"port": port, "opened": sys.platform == "darwin"} if port else {})})
        ok = proc.wait() == 0 and os.path.isfile(os.path.join(directory, "auth.json"))
        conn.accounts_port = None
        if not ok and method == "browser" and any("already in use" in x for x in lines) and \
                not getattr(conn, "gone", False) and proc.returncode not in (-15, 130):
            try:
                return run("device")  # (its ports taken: the device code's way instead)
            except OSError as e:
                lines.append(str(e))
        if ok and fresh:
            try:
                accounts.add("codex", msg.get("name"), directory)
            except ValueError as e:
                shutil.rmtree(directory, ignore_errors=True)
                return done(error=str(e), login={"state": "failed", "method": method, "reason": str(e)})
        elif not ok and fresh:
            shutil.rmtree(directory, ignore_errors=True)
        elif ok:
            accounts.forget_usage(profile)  # (signed in again, perhaps as someone else: its usage read afresh)
        if not ok:
            why = next((x for x in reversed(lines) if x.lower().startswith("error")), None) or \
                (lines[-1] if lines and lines[-1] not in (page, code) else wording.say("subscriptions.codex_failed"))
            return done(error=why, login={"state": "failed", "method": method, "reason": why})
        who = codex_who(directory)
        done(signed_in=True, who=who, login={"state": "signed_in", "method": method, "who": who})

    try:
        run("device" if msg.get("method") == "device" else "browser")
    except OSError:
        if fresh:
            shutil.rmtree(directory, ignore_errors=True)
        raise
    return None


def callback_port(page):
    """The port of the 127.0.0.1 address a browser sign-in's page sends the browser back to (its redirect_uri), or
    None."""
    try:
        back = urllib.parse.urlsplit(urllib.parse.parse_qs(urllib.parse.urlsplit(page).query)["redirect_uri"][0])
        return back.port if back.hostname in ("127.0.0.1", "localhost") else None
    except (KeyError, IndexError, ValueError):
        return None


def _callback(conn, address, reply, cb=None):
    """The address a browser ended on after a sign-in's page (pasted on the head, or the request its browser made to
    the head's own 127.0.0.1), sent on to the browser sign-in this connection runs: requested of codex's server here,
    at its port, as the browser would have (only its path and query: never anywhere else), and what it answered
    (page: the page codex shows the browser, its own redirects back to itself followed) carried back."""
    proc, port = getattr(conn, "accounts_login", None), getattr(conn, "accounts_port", None)
    tag = {"cb": cb} if cb is not None else {}
    try:
        sent = urllib.parse.urlsplit((address or "").strip())
    except ValueError:
        sent = None
    if proc is None or proc.poll() is not None or not port:
        return reply("accounts", what="callback", **tag, error=wording.say("subscriptions.no_sign_in"))
    if sent is None or not sent.path.endswith("/auth/callback") or not sent.query:
        return reply("accounts", what="callback", **tag, error=wording.say("subscriptions.not_callback"))

    def ask():
        path = f"/auth/callback?{sent.query}"
        try:
            for _ in range(3):
                http = HTTPConnection("127.0.0.1", port, timeout=120)
                try:
                    http.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
                    r = http.getresponse()
                    body, kind, location = r.read(1 << 20), r.getheader("Content-Type") or "", r.getheader("Location")
                finally:
                    http.close()
                back = urllib.parse.urlsplit(location or "")
                if r.status in (301, 302, 303, 307) and location and (not back.netloc or back.hostname in (
                        "127.0.0.1", "localhost") and back.port == port):
                    path = back.path + (f"?{back.query}" if back.query else "")  # (its own success page)
                    continue
                return reply("accounts", what="callback", **tag, page={
                    "status": r.status, "type": kind, "body": body.decode("utf-8", "replace"),
                    **({"location": location} if location else {})})
        except (InvalidURL, ValueError):  # (a query it can't send: a page that says so)
            return reply("accounts", what="callback", **tag, page={
                "status": 400, "type": "text/plain; charset=utf-8", "body": wording.say("subscriptions.not_callback")})
        except (OSError, HTTPException) as e:
            return reply("accounts", what="callback", **tag, error=str(e) or wording.say("host_status.no_answer"))
        reply("accounts", what="callback", **tag, error=wording.say("host_status.no_answer"))

    threading.Thread(target=ask, daemon=True, name="codex-callback").start()
    return None


def codex_who(directory):
    """Whose a CODEX_HOME's ChatGPT sign-in is: the email its auth.json's id token names, or None."""
    try:
        with open(os.path.join(directory, "auth.json"), encoding="utf-8") as f:
            token = (json.load(f).get("tokens") or {}).get("id_token") or ""
        body = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (OSError, ValueError, IndexError, AttributeError, TypeError):
        return None
    email = claims.get("email") if isinstance(claims, dict) else None
    return email if isinstance(email, str) and email else None


def stop(conn):
    """The connection's gone: its sign-in with it."""
    proc = getattr(conn, "accounts_login", None)
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass
