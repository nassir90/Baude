"""emissioncore — what Emission's parts share, no page engine in it: its constants, the socket each profile's
requests come in on (ProfileSocket), what's typed in the address bar as a URL (address), the commit it started at
(STARTED), whether this is an agent's Emission (AGENT), what a page that couldn't be opened shows, where a download
goes, and the script every page of a Bot's is given for its console (CONSOLE_JS).
"""
import html
import ipaddress
import json
import os
import socket
import sys
import urllib.parse

import macos  # noqa: E402  (no GTK: before it's loaded)
from gi.repository import GLib  # noqa: E402
from baudelib import selfupdate  # noqa: E402
from baudelib.emissionlink import profile_socket, profiles  # noqa


APP_ID = "dev.baude.Emission"
HOME_PAGE = "https://duckduckgo.com/"
SEARCH = "https://duckduckgo.com/?q={}"
POLL_MS = 200  # (each page's title, address, progress, back and forward read this often, the same on both engines)
SCREEN = (3840, 2400)  # (the virtual display a Linux agent's Emission is on: resize's limits fit in it)
AGENT_IDLE_S = 30 * 60  # (an Emission started for a Bot: gone after this long without a request)


class ProfileSocket:
    """This process taking requests for the profiles it has (each one's lock held: no one else listens there): a
    line of JSON each, answered with one, maybe later (a screenshot, a script)."""
    open = {}

    @classmethod
    def listen(cls, pid, handle):
        """handle(pid, request, answer) run for each request to pid's profile; answer(dict) sends its answer."""
        if pid in cls.open:
            return
        path = profile_socket(pid)
        try:
            os.unlink(path)  # (a socket left by an Emission that's gone: its lock's ours now)
        except OSError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(8)
        srv.setblocking(False)

        def came(*_):
            try:
                conn, _addr = srv.accept()
            except OSError:
                return True
            conn.settimeout(10)
            try:
                raw = b""
                while not raw.endswith(b"\n") and len(raw) < 1 << 22:
                    part = conn.recv(1 << 16)
                    if not part:
                        break
                    raw += part
                msg = json.loads(raw or b"{}")
                if not isinstance(msg, dict):
                    raise ValueError("not a request")
            except (OSError, ValueError):
                conn.close()
                return True

            def answer(reply):
                try:
                    conn.settimeout(30)
                    conn.sendall((json.dumps(reply) + "\n").encode())
                except (OSError, TypeError, ValueError):
                    pass
                finally:
                    conn.close()
            answer = once(answer)
            try:
                if msg.get("op") == "watch":
                    handle(pid, msg, answer, conn)  # (its connection kept: frames go on it, acks come back)
                    return True
                handle(pid, msg, answer)
            except Exception as e:  # (said to whoever asked, and in the log)
                sys.excepthook(*sys.exc_info())
                answer({"error": f"{type(e).__name__}: {e}"})
            return True
        cls.open[pid] = (srv, GLib.io_add_watch(srv.fileno(), GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN, came))

    @classmethod
    def close(cls, pid):
        srv, source = cls.open.pop(pid, (None, None))
        if srv is not None:
            GLib.source_remove(source)
            srv.close()
            try:
                os.unlink(profile_socket(pid))
            except OSError:
                pass


# ------------------------------------------------------------------ addresses --

def address(text):
    """What's typed in the address bar as a URL: one as it is; a path as a file; a local host (localhost, *.localhost,
    *.local) or an IP address given http://, another host https://; anything else searched for."""
    text = text.strip()
    if not text:
        return HOME_PAGE
    if text.startswith(("/", "~")):
        return "file://" + urllib.parse.quote(os.path.expanduser(text))
    if "://" in text or text.startswith(("about:", "file:", "data:")):
        return text
    if " " not in text:
        try:
            host = urllib.parse.urlsplit("//" + text).hostname or ""
        except ValueError:
            host = ""
        try:
            ipaddress.ip_address(host)
            local = True
        except ValueError:
            local = host == "localhost" or host.endswith((".localhost", ".local"))
        if local:
            return "http://" + text
        if "." in host:
            return "https://" + text
    return SEARCH.format(urllib.parse.quote_plus(text))


STARTED = selfupdate.git(selfupdate.HERE, "rev-parse", "HEAD", timeout=5) or None  # (the commit its code is, as it
# started: said when it's asked what it is, info, as this machine's host process does to list it among its apps)
AGENT = {"on": False}  # (this process an agent's Emission: emission.py --agent)


def for_bots(pid):
    """Whether pid's profile is a Bot's (its pages watched for its tools: their console kept)."""
    return any(p["id"] == pid and p.get("bot") for p in profiles())


def failure_page(url, why):
    """What a page that couldn't be opened shows (the same on both engines)."""
    esc = html.escape
    return ("<!doctype html><meta charset=utf-8><title>Couldn't open this page</title><style>"
            "body{font:15px system-ui,sans-serif;margin:3em auto;max-width:36em;color:#333}"
            "@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#ddd}}"
            f"h1{{font-size:20px}}code{{word-break:break-all}}</style><h1>Couldn't open this page</h1><p>{esc(why)}"
            f"</p><p><code>{esc(url)}</code></p>")


def once(fn):
    """fn, run the first time only (a dialog's answer: what WebKit's waiting on is answered once)."""
    done = []

    def run(*a):
        if not done:
            done.append(True)
            fn(*a)
    return run


def downloads_folder():
    return GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~/Downloads")


def download_path(suggested):
    """Where a download goes: the Downloads folder, under the name the site gave (its last part only), made unique."""
    name = os.path.basename((suggested or "").replace("\\", "/")).strip().lstrip(".") or "download"
    folder = downloads_folder()
    os.makedirs(folder, exist_ok=True)
    stem, ext = os.path.splitext(name)
    path, n = os.path.join(folder, name), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{stem} ({n}){ext}"), n + 1
    return path


# ------------------------------------------------------------------- macOS --

def objc_sel(name):
    macos.objc_send(b"NSObject", b"class")  # (the runtime loaded)
    return macos.objc_send.rt.sel_registerName(name)


# ------------------------------------------------------------------ agents --

CONSOLE_JS = r"""
(() => {
  const K = Symbol.for('emission.console');
  if (window[K]) return;
  try { performance.setResourceTimingBufferSize(2000); } catch (e) {}
  const log = [];
  Object.defineProperty(window, K, {value: log});
  const put = (level, text) => { log.push({level, text: String(text).slice(0, 2000), at: Date.now()});
    if (log.length > 300) log.shift(); };
  const show = x => { try { return typeof x === 'string' ? x : JSON.stringify(x); } catch (e) { return String(x); } };
  for (const level of ['log', 'info', 'warn', 'error', 'debug']) {
    const was = console[level];
    console[level] = function (...a) { try { put(level, a.map(show).join(' ')); } catch (e) {} return was.apply(this, a); };
  }
  addEventListener('error', e => put('error', e.message + (e.filename ? ' (' + e.filename + ':' + e.lineno + ')' : '')));
  addEventListener('unhandledrejection', e => put('error', 'unhandled rejection: ' + show(e.reason)));
})();
"""
