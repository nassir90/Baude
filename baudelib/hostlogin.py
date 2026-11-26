"""hostlogin — claude auth login run on this machine: for a head elsewhere (a chat whose claude here can't sign in,
signed in again from that chat, on the phone or another machine's Baude), on a connection of its own (login): each
line it writes carried back as it's written (login_said), a code pasted there passed to it (login_code), and who's
signed in once it's done (login_end); the connection's going stops it. Also what's said of a turn's error that signing
in again would fix (needs_login, login_error), the same in every app: Baude's chats and Meta, Record's chats and Meta,
and the phone's (LocalCommands.kt); only a Claude Code session's (Codex's and pi's errors are theirs to say).

A chat's sign-in (ChatLogin: needed, signing_in, signed_in, failed) is its host's to keep (Session.login): a turn that
fails for want of it makes it needed (asked), a login request for the chat (its session) moves it as it goes, and each
change is told every head attached to it (login_state), as attach's answer tells one attaching. Every head draws the
chat's sign-in card from it alone, so each shows the same card, live or opened later: a sign-in cancelled goes back to
needed, with Claude's own reason, not to a state of the head's own.
"""
import json
import re
import subprocess
import threading

from baudelib import deps
from baudelib.claude import claude_env, claude_exe

# A turn's error that signing in again would fix: claude's own words for it ("Invalid API key · Please run /login",
# "OAuth token has expired", "Failed to authenticate: this login reached its time limit and cannot be renewed. Run
# `claude auth login` to sign in again…", "Not logged in"), or the API's (authentication_error, a 401). The phone's
# LocalCommands.kt has it as it is here.
NEEDS_LOGIN = re.compile(
    r"Please run /login|\brun /login\b|claude (?:auth )?login|claude setup-token|Invalid API key|"
    r"OAuth token (?:has )?(?:expired|revoked)|\b(?:not|n't) (?:logged|signed) in|authentication_(?:error|failed)|"
    r"Failed to authenticate|API Error: 401\b|"
    r"\b(?:login|sign-in|session|token|credentials?)\b[^.\n]{0,40}\b(?:expired|time limit|cannot be renewed|revoked)|"
    r"\b(?:log|sign) in again", re.I)
# (a sign-in that was there and ran out, as claude says it: not one there's never been)
EXPIRED = re.compile(r"\b(?:expired|time limit|cannot be renewed|can't be renewed)\b", re.I)
ESCAPES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]")  # (a link's, a colour's)
# (claude auth login run by a host opens no browser on the host's screen: claude runs BROWSER with the page's address,
# and `true` does nothing; the address is in what it writes, for the head to open)
NO_BROWSER = {"BROWSER": "true"}


def needs_login(text):
    """Whether a turn's error is one signing in again would fix."""
    return bool(text) and bool(NEEDS_LOGIN.search(text))


def login_error(msg):
    """The error of a message of claude's (a turn's result, or the message it writes in place of a reply) that
    signing in again would fix, or None. A result's only if it's an error; a reply's only if it's one claude wrote
    (its API error, a <synthetic> one), said to be the sign-in's (error: authentication_failed) or saying so."""
    if msg.get("type") == "result":
        text = msg.get("result") if isinstance(msg.get("result"), str) else ""
        return text if msg.get("is_error") and needs_login(text) else None
    if msg.get("type") != "assistant" or msg.get("parent_tool_use_id"):
        return None
    m = msg.get("message") or {}
    if not (msg.get("isApiErrorMessage") or msg.get("is_api_error_message") or m.get("model") == "<synthetic>" or
            msg.get("error") == "authentication_failed"):
        return None
    text = "".join(c.get("text", "") for c in m.get("content") or [] if isinstance(c, dict) and
                   c.get("type") == "text").strip()
    return text or msg.get("error") if msg.get("error") == "authentication_failed" or needs_login(text) else None


def reason(text):
    """A sign-in error, as its card says why: its first sentence (what claude says to do about it is the card's)."""
    text = ESCAPES.sub("", text or "").strip()
    return re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0][:200]


def asked(error):
    """A chat's sign-in (ChatLogin) once a turn's failed for want of it (error: claude's words): needed, whether what
    was there ran out (expired), and claude's own first sentence for why."""
    return {"state": "needed", "expired": bool(EXPIRED.search(error or "")), "reason": reason(error) or None,
            "who": None}


def heard(server, s, msg):
    """(lock held) What a chat's claude said, as it moves the chat's sign-in: a reply or a result saying it failed
    for want of signing in makes it needed (not while a sign-in for it runs); a turn that ends otherwise ends it."""
    if msg.get("type") not in ("assistant", "result") or msg.get("parent_tool_use_id"):
        return
    error = login_error(msg)
    if error is not None:
        if (s.login or {}).get("state") != "signing_in":
            tell(server, s, asked(error))
    elif msg.get("type") == "result" and s.login is not None and s.login.get("state") != "signing_in":
        tell(server, s, None)


def tell(server, s, login):
    """(lock held) A chat's sign-in now: kept (Session.login) and told every head attached to it, if it changed."""
    if login == s.login:
        return
    s.login = login
    for conn in list(s.viewers):
        conn.send({"op": "login_state", "session": s.id, "login": login})


def _moved(server, sid, then):
    """A chat's sign-in moved by its sign-in (then(before): what it is now), under the host's lock."""
    if not sid:
        return
    with server.lock:
        s = server.sessions.get(sid)
        if s is not None:
            tell(server, s, then(s.login))


def auth_status(exe=None, env=None):
    """claude auth status --json: {loggedIn, email, orgName, subscriptionType, authMethod…}, or {} (it failed)."""
    try:
        out = subprocess.run([exe or claude_exe(), "auth", "status", "--json"], capture_output=True, text=True,
                             timeout=30, stdin=subprocess.DEVNULL, env=env or claude_env()).stdout
        return json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def signed_in_as(status):
    """Who auth_status says is signed in, in a few words, or None."""
    if not status.get("loggedIn"):
        return None
    who = status.get("email") or status.get("authMethod") or "your account"
    extra = " · ".join(x for x in (status.get("orgName"), status.get("subscriptionType")) if x)
    return f"{who} ({extra})" if extra else who


def start(server, conn, msg):
    """A login request: claude auth login started (one a connection), read on a thread of its own."""
    req = msg.get("req")
    if (proc := getattr(conn, "login", None)) is not None and proc.poll() is None:
        return conn.send({"op": "error", "req": req, "error": "this connection runs a sign-in already"})
    exe, env = server.exe or claude_exe(), claude_env() | (server.env or {}) | NO_BROWSER
    sid = msg.get("session") if isinstance(msg.get("session"), str) else None
    if (dep := deps.missing("claude")) is not None:  # (no claude here: what to install, as Dependencies says it)
        why = deps.say(dep, "report.claude")
        _moved(server, sid, lambda was: failed(was, why))
        return conn.send({"op": "login_end", "req": req, "code": None, "why": why})
    try:
        proc = subprocess.Popen([exe, "auth", "login", *(msg.get("args") or [])], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    except OSError as e:
        _moved(server, sid, lambda was: failed(was, f"couldn't start claude: {e}"))
        return conn.send({"op": "login_end", "req": req, "code": None, "why": f"couldn't start claude: {e}"})
    conn.login = proc
    if getattr(conn, "gone", False):
        proc.terminate()
    server.log("claude auth login started for a head elsewhere")
    before = {}  # (the chat's sign-in as it started: what a cancel puts back)

    def starting(was):
        before["was"] = was
        return {**(was or {"expired": False, "reason": None}), "state": "signing_in", "who": None}
    _moved(server, sid, starting)
    threading.Thread(target=_read, args=(server, conn, req, proc, exe, env, sid, before), daemon=True,
                     name="login").start()
    return None


def failed(was, why):
    """A chat's sign-in that didn't finish (why), or, cancelled (why None), what it was before it started."""
    return {"state": "failed", "expired": False, "reason": why, "who": None} if why else was


def _read(server, conn, req, proc, exe, env, sid=None, before=None):
    said = []
    for line in proc.stdout:
        conn.send({"op": "login_said", "req": req, "line": line.rstrip("\n")})
        if (text := ESCAPES.sub("", line).replace("Paste code here if prompted > ", "").strip()) and \
                not text.startswith("Opening browser") and "https://" not in text:
            said.append(text)
    code = proc.wait()
    who = signed_in_as(auth_status(exe, env)) if code == 0 else None
    conn.send({"op": "login_end", "req": req, "code": code, "who": who})
    if code == 0:
        _moved(server, sid, lambda _was: {"state": "signed_in", "expired": False, "reason": None, "who": who})
        return
    cancelled = code in (-15, 130) or getattr(conn, "gone", False)
    why = None if cancelled else next((x for x in reversed(said) if x.lower().startswith(("login failed", "error"))),
                                      said[-1] if said else f"claude auth login ended ({code})")
    _moved(server, sid, lambda _was: failed((before or {}).get("was"), why))


def code(conn, msg):
    """A login_code: the pasted code passed to the connection's claude auth login, if it's still running."""
    proc = getattr(conn, "login", None)
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.stdin.write(msg["code"].strip() + "\n")
        proc.stdin.flush()
    except OSError:
        pass


def stop(conn):
    """The connection's gone: its claude auth login with it."""
    proc = getattr(conn, "login", None)
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass
